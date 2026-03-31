// 📐 Grid L0 Engine — Dynamic Multi-Level Grid Trading
// Sniper Armada · Bot #3 · v1.0.0
//
// Places N BUY levels below center + N SELL levels above center.
// Supports arithmetic (fixed USD spacing) and geometric (fixed % spacing).
// AI-driven parameter tuning via L2 Oracle (sniper_orchestrator.py).
//
// Strategy from HFT-Grid:
//   - 5 BUY + 5 SELL levels (configurable 1-10)
//   - When BUY fills → place corresponding SELL (grid_spacing higher)
//   - When SELL fills → place corresponding BUY (grid_spacing lower)
//   - Dynamic spacing via ATR/2 (configurable via AI)
//   - 3 consecutive losses → 24h halt
//
// Architecture:
//   - L0 (this): Rust async, WebSocket, grid management
//   - L1: Python l1_shield.py (volatility filter, ATR calc)
//   - L2: Python sniper_orchestrator.py (grid params, center price)

use std::time::{SystemTime, UNIX_EPOCH, Instant, Duration};
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use tokio::sync::mpsc;
use futures_util::{StreamExt, SinkExt};
use serde_json::json;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use dotenvy::dotenv;
use tracing::{info, warn, error, Level};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};
use memmap2::MmapMut;
use anyhow::{Context, Result};

use sniper_types::grid_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};
use sniper_types::exchange::bitfinex;

const VERSION: &str = "1.0.0";

// ═══════════════════════════════════════════════════════════
// Async Notifier
// ═══════════════════════════════════════════════════════════
struct AsyncNotifier {
    tx: mpsc::UnboundedSender<String>,
}

impl AsyncNotifier {
    fn new() -> Self {
        let (tx, mut rx) = mpsc::unbounded_channel::<String>();
        let token = std::env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default();
        let chat_id = std::env::var("TELEGRAM_CHAT_ID").unwrap_or_default();
        let client = reqwest::Client::builder().timeout(Duration::from_secs(10)).build().unwrap_or_default();

        let exe_dir = std::env::current_exe()
            .ok().and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .unwrap_or_else(|| std::path::PathBuf::from("."));
        let log_dir = exe_dir.join("../../grid/logs");
        let _ = std::fs::create_dir_all(&log_dir);
        let alerts_log = log_dir.join("alerts.log");

        tokio::spawn(async move {
            while let Some(msg) = rx.recv().await {
                info!(event = "async_alert", message = msg);
                if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&alerts_log) {
                    let _ = writeln!(file, "[{:?}] {}", SystemTime::now(), msg);
                }
                if token.is_empty() || chat_id.is_empty() { continue; }
                let url = format!("https://api.telegram.org/bot{}/sendMessage", token);
                let _ = client.post(url)
                    .json(&json!({"chat_id": chat_id, "text": format!("📐 *GRID*\n`{}`", msg), "parse_mode": "Markdown"}))
                    .send().await;
            }
        });
        Self { tx }
    }

    fn send(&self, msg: String) { let _ = self.tx.send(msg); }
}

// ═══════════════════════════════════════════════════════════
// mmap
// ═══════════════════════════════════════════════════════════
fn init_mmap_ptr<T: Default>(path: &str) -> Result<MmapMut> {
    let dir = std::path::Path::new(path).parent().unwrap_or_else(|| std::path::Path::new("/dev/shm/beroun"));
    std::fs::create_dir_all(dir)?;
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    let mut mmap = unsafe { MmapMut::map_mut(&file)? };
    if mmap.iter().all(|&b| b == 0) {
        let default_val = T::default();
        let ptr = &default_val as *const T as *const u8;
        let slice = unsafe { std::slice::from_raw_parts(ptr, std::mem::size_of::<T>()) };
        mmap.copy_from_slice(slice);
    }
    Ok(mmap)
}

// Auth → shared exchange module (Phase 5.2)

// ═══════════════════════════════════════════════════════════
// Grid Level Calculator
// ═══════════════════════════════════════════════════════════
fn calculate_grid_levels(
    center: f64,
    spacing: f64,
    num_buy: u32,
    num_sell: u32,
    mode: u32,       // 0 = arithmetic, 1 = geometric
    geo_pct: f64,     // geometric step %
) -> (Vec<f64>, Vec<f64>) {
    let mut buys = Vec::with_capacity(num_buy as usize);
    let mut sells = Vec::with_capacity(num_sell as usize);

    for i in 1..=(num_buy as usize) {
        let price = if mode == 0 {
            center - (i as f64 * spacing)        // Arithmetic
        } else {
            center * (1.0 - geo_pct / 100.0).powi(i as i32) // Geometric
        };
        if price > 0.0 { buys.push(price); }
    }

    for i in 1..=(num_sell as usize) {
        let price = if mode == 0 {
            center + (i as f64 * spacing)
        } else {
            center * (1.0 + geo_pct / 100.0).powi(i as i32)
        };
        sells.push(price);
    }

    (buys, sells)
}

// Ticker parser → shared exchange module (Phase 5.2)

// ═══════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════
#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();
    tracing_subscriber::registry()
        .with(fmt::layer().with_target(false).json())
        .with(EnvFilter::from_default_env().add_directive(Level::INFO.into()))
        .init();

    let notifier = Arc::new(AsyncNotifier::new());
    let engine_mmap = init_mmap_ptr::<GridEngineState>(GRID_ENGINE_PATH)?;
    let risk_mmap = init_mmap_ptr::<GridRiskState>(GRID_RISK_PATH)?;
    let engine = unsafe { &*(engine_mmap.as_ptr() as *const GridEngineState) };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const GridRiskState) };

    // L2 Grid Warp Matrix (Cache Line 3, offset 128 in l2_command.bin)
    let l2cmd_mmap = {
        let f = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false)
            .open(sniper_types::l2_command::L2_COMMAND_PATH)?;
        f.set_len(sniper_types::l2_command::L2_COMMAND_FILE_SIZE as u64)?;
        unsafe { MmapMut::map_mut(&f)? }
    };
    let grid_warp = unsafe {
        &*(l2cmd_mmap.as_ptr()
            .add(std::mem::size_of::<sniper_types::l2_command::L2CommandMatrix>())
            .add(std::mem::size_of::<sniper_types::l2_command::L2ASMatrix>())
            as *const sniper_types::l2_command::L2GridWarpMatrix)
    };
    // CL4: Global Risk (hedge shield check)
    let l2risk = unsafe {
        &*(l2cmd_mmap.as_ptr()
            .add(std::mem::size_of::<sniper_types::l2_command::L2CommandMatrix>())
            .add(std::mem::size_of::<sniper_types::l2_command::L2ASMatrix>())
            .add(std::mem::size_of::<sniper_types::l2_command::L2GridWarpMatrix>())
            as *const sniper_types::l2_command::L2GlobalRiskMatrix)
    };
    // CL5: Portfolio telemetry
    let l2portfolio = unsafe {
        &*(l2cmd_mmap.as_ptr()
            .add(std::mem::size_of::<sniper_types::l2_command::L2CommandMatrix>())
            .add(std::mem::size_of::<sniper_types::l2_command::L2ASMatrix>())
            .add(std::mem::size_of::<sniper_types::l2_command::L2GridWarpMatrix>())
            .add(std::mem::size_of::<sniper_types::l2_command::L2GlobalRiskMatrix>())
            as *const sniper_types::l2_command::L2PortfolioTelemetry)
    };

    let key = std::env::var("BITFINEX_API_KEY").context("Missing BITFINEX_API_KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing BITFINEX_API_SECRET")?;

    info!(event = "system_start", version = VERSION, bot = "grid", strategy = "multi_level_grid");

    // ═══ SINGLE-INSTANCE LOCK ═══
    use fs2::FileExt;
    let lock_file = std::fs::File::create("/tmp/grid-core.lock")
        .context("Failed to create grid lock file")?;
    if lock_file.try_lock_exclusive().is_err() {
        tracing::error!(event = "dual_instance_blocked",
            msg = "Another grid-core is already running! Aborting.");
        std::process::exit(1);
    }
    let _lock_guard = lock_file;

    notifier.send(format!("📐 Grid v{} (Dynamic Multi-Level) ONLINE", VERSION));

    loop {
        let ws_result = connect_async(bitfinex::WS_URL).await;
        let (ws, _) = match ws_result {
            Ok(v) => v,
            Err(e) => { warn!(event = "ws_fail", error = %e); tokio::time::sleep(Duration::from_secs(60)).await; continue; }
        };

        let (mut write, mut read) = ws.split();

        // Auth (shared exchange module)
        let auth_msg = sniper_types::exchange::bitfinex_auth_message(&key, &sec);
        write.send(Message::Text(auth_msg.into())).await?;

        // Subscribe to ticker
        let symbol = "tBTCUSD"; // Default, overridable via risk state
        write.send(Message::Text(json!({"event":"subscribe","channel":"ticker","symbol":symbol}).to_string().into())).await?;

        let mut authed = false;
        let mut ticker_chan: Option<i64> = None;
        let mut last_grid_calc = Instant::now();
        let mut order_msg = String::with_capacity(2048);

        while let Some(msg) = read.next().await {
            let loop_start = Instant::now();
            let msg = match msg { Ok(m) => m, Err(_) => break };

            let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
            engine.heartbeat_ms.store(now_ms, Ordering::Release);

            if let Message::Text(text) = msg {
                let bytes = text.as_bytes();

                // Fast path: ticker
                if let Some((chan, bid, ask)) = sniper_types::exchange::fast_parse_ticker(bytes) {
                    if ticker_chan == Some(chan) {
                        let mid = (bid + ask) / 2;
                        engine.best_bid.store(bid as u64, Ordering::Release);
                        engine.best_ask.store(ask as u64, Ordering::Release);
                        engine.mid_price.store(mid as u64, Ordering::Release);
                        engine.latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);

                        // Grid recalculation every 3 seconds
                        let paused = risk.global_paused.load(Ordering::Acquire) != 0;

                        // Phase 3: Report grid inventory to L2 portfolio aggregator
                        let grid_inv = engine.net_position.load(Ordering::Relaxed);
                        l2portfolio.grid_inventory.store(grid_inv, Ordering::Relaxed);

                        // Phase 3: Portfolio shield check (VPIN crisis → stop buying)
                        let hedge_active = !sniper_types::l2_command::should_grid_place_bid(l2risk);

                        if authed && !paused && last_grid_calc.elapsed().as_secs() >= 3 {
                            let spacing = risk.grid_spacing.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                            let num_buy = risk.num_buy_levels.load(Ordering::Acquire);
                            let num_sell = risk.num_sell_levels.load(Ordering::Acquire);
                            let mode = risk.grid_mode.load(Ordering::Acquire);
                            let geo_pct = risk.geometric_step_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                            let qty = risk.order_qty.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                            let center_override = risk.center_price_override.load(Ordering::Acquire) as f64 / PRICE_SCALE;

                            let mid_f64 = mid as f64 / PRICE_SCALE_I as f64;
                            let center = if center_override > 0.0 { center_override } else { mid_f64 };

                            if spacing > 0.0 && qty > 0.0 && center > 0.0 {
                                // ═══ v14.3 GAUSSIAN WARP GRID (Issue #18 Phase 2b) ═══
                                // If L2 has set a dynamic anchor, use warped quadratic levels
                                // Otherwise fall back to linear calculate_grid_levels
                                let anchor = grid_warp.grid_dynamic_anchor.load(Ordering::Relaxed);
                                let (buys, sells) = if anchor > 0 {
                                    // L2 controls grid topology
                                    let mut warp_buys = Vec::new();
                                    let mut warp_sells = Vec::new();
                                    for lvl in 1..=30i64 {
                                        // Phase 3: Skip ALL bids if hedge shield is active
                                        if !hedge_active {
                                            if let Some(p) = sniper_types::l2_command::calculate_warped_grid_level(
                                                grid_warp, lvl, true
                                            ) {
                                                if p > 0 {
                                                    warp_buys.push(p as f64 / PRICE_SCALE_I as f64);
                                                }
                                            }
                                        }
                                        if let Some(p) = sniper_types::l2_command::calculate_warped_grid_level(
                                            grid_warp, lvl, false
                                        ) {
                                            warp_sells.push(p as f64 / PRICE_SCALE_I as f64);
                                        }
                                    }
                                    (warp_buys, warp_sells)
                                } else {
                                    // Fallback: original linear grid
                                    calculate_grid_levels(center, spacing, num_buy, num_sell, mode, geo_pct)
                                };

                                // Write levels to mmap for dashboard
                                for (i, &price) in buys.iter().enumerate() {
                                    if i < GRID_MAX_LEVELS {
                                        engine.buy_levels[i].price.store((price * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                                        engine.buy_levels[i].quantity.store((qty * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                                    }
                                }
                                engine.active_buy_levels.store(buys.len() as u32, Ordering::Release);

                                for (i, &price) in sells.iter().enumerate() {
                                    if i < GRID_MAX_LEVELS {
                                        engine.sell_levels[i].price.store((price * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                                        engine.sell_levels[i].quantity.store((qty * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                                    }
                                }
                                engine.active_sell_levels.store(sells.len() as u32, Ordering::Release);

                                // Build multi-order: cancel all + place grid
                                order_msg.clear();
                                order_msg.push_str("[0,\"ox_multi\",null,[");
                                // Cancel all existing
                                order_msg.push_str("[\"oc_multi\",{\"symbol\":\"");
                                order_msg.push_str(symbol);
                                order_msg.push_str("\"}]");

                                // Place buy levels
                                for price in &buys {
                                    order_msg.push_str(",[\"on\",{\"gid\":3000,\"symbol\":\"");
                                    order_msg.push_str(symbol);
                                    order_msg.push_str("\",\"amount\":");
                                    order_msg.push_str(&format!("{:.5}", qty));
                                    order_msg.push_str(",\"price\":\"");
                                    order_msg.push_str(&format!("{:.1}", price));
                                    order_msg.push_str("\",\"type\":\"EXCHANGE LIMIT\"}]");
                                }

                                // Place sell levels
                                for price in &sells {
                                    order_msg.push_str(",[\"on\",{\"gid\":3000,\"symbol\":\"");
                                    order_msg.push_str(symbol);
                                    order_msg.push_str("\",\"amount\":");
                                    order_msg.push_str(&format!("{:.5}", -qty));
                                    order_msg.push_str(",\"price\":\"");
                                    order_msg.push_str(&format!("{:.1}", price));
                                    order_msg.push_str("\",\"type\":\"EXCHANGE LIMIT\"}]");
                                }

                                order_msg.push_str("]]");
                                let _ = write.send(Message::Text(order_msg.clone().into())).await;
                                last_grid_calc = Instant::now();
                            }
                        }
                    }
                    continue;
                }

                // Slow path: events
                if bytes.first() == Some(&b'{') {
                    let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();
                    if v["event"] == "auth" {
                        if v["status"] == "OK" {
                            authed = true;
                            info!(event = "authenticated", bot = "grid");
                        } else {
                            error!(event = "auth_failed", bot = "grid", status = %v["status"], msg = %v["msg"]);
                            notifier.send(format!("❌ AUTH FAILED: {}", v["msg"]));
                        }
                    }
                    if v["event"] == "subscribed" && v["channel"] == "ticker" {
                        if let Some(cid) = v["chanId"].as_i64() {
                            ticker_chan = Some(cid);
                            info!(event = "ticker_subscribed", chan_id = cid);
                        }
                    }
                }
            }
        }

        warn!(event = "ws_disconnected", bot = "grid");
        notifier.send("⚠️ Grid: WebSocket disconnected, reconnecting...".to_string());
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
