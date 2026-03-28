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
use hmac::{Hmac, Mac};
use sha2::Sha384;
use dotenvy::dotenv;
use tracing::{info, warn, Level};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};
use memmap2::MmapMut;
use anyhow::{Context, Result};

use sniper_types::grid_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};

const BITFINEX_WS_URL: &str = "wss://api.bitfinex.com/ws/2";
const VERSION: &str = "1.0.0";

type HmacSha384 = Hmac<Sha384>;

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
        let client = reqwest::Client::builder().timeout(Duration::from_secs(10)).build().unwrap();

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
    let dir = std::path::Path::new(path).parent().unwrap();
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

async fn get_sig(sec: &str, payload: &str) -> String {
    let mut mac = HmacSha384::new_from_slice(sec.as_bytes()).expect("HMAC error");
    mac.update(payload.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

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

// ═══════════════════════════════════════════════════════════
// Fast Ticker Parser
// ═══════════════════════════════════════════════════════════
fn fast_parse_ticker(data: &[u8]) -> Option<(i64, i64, i64)> {
    if data.len() < 10 || data[0] != b'[' { return None; }
    let mut i = 1;
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() { return None; }
    let chan_id = std::str::from_utf8(&data[1..i]).ok()?.parse::<i64>().ok()?;
    if i + 2 < data.len() && data[i+1] == b'"' && data[i+2] == b'h' { return None; }
    while i < data.len() && data[i] != b'[' { i += 1; }
    if i >= data.len() { return None; }
    i += 1;
    let start = i;
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() || start >= i { return None; }
    let bid = (std::str::from_utf8(&data[start..i]).ok()?.parse::<f64>().ok()? * PRICE_SCALE_I as f64) as i64;
    i += 1;
    if i >= data.len() { return None; }
    while i < data.len() && data[i] != b',' { i += 1; }
    i += 1;
    if i >= data.len() { return None; }
    let start = i;
    while i < data.len() && data[i] != b',' && data[i] != b']' { i += 1; }
    if start >= i { return None; }
    let ask = (std::str::from_utf8(&data[start..i]).ok()?.parse::<f64>().ok()? * PRICE_SCALE_I as f64) as i64;
    Some((chan_id, bid, ask))
}

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

    let key = std::env::var("BITFINEX_API_KEY").context("Missing BITFINEX_API_KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing BITFINEX_API_SECRET")?;

    info!(event = "system_start", version = VERSION, bot = "grid", strategy = "multi_level_grid");
    notifier.send(format!("📐 Grid v{} (Dynamic Multi-Level) ONLINE", VERSION));

    loop {
        let ws_result = connect_async(BITFINEX_WS_URL).await;
        let (ws, _) = match ws_result {
            Ok(v) => v,
            Err(e) => { warn!(event = "ws_fail", error = %e); tokio::time::sleep(Duration::from_secs(60)).await; continue; }
        };

        let (mut write, mut read) = ws.split();

        // Auth
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis().to_string();
        let auth_payload = format!("AUTH{}", nonce);
        let sig = get_sig(&sec, &auth_payload).await;
        write.send(Message::Text(json!({"event":"auth","apiKey":key,"authSig":sig,"authPayload":auth_payload,"authNonce":nonce,"dms":4}).to_string().into())).await?;

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
                if let Some((chan, bid, ask)) = fast_parse_ticker(bytes) {
                    if ticker_chan == Some(chan) {
                        let mid = (bid + ask) / 2;
                        engine.best_bid.store(bid as u64, Ordering::Release);
                        engine.best_ask.store(ask as u64, Ordering::Release);
                        engine.mid_price.store(mid as u64, Ordering::Release);
                        engine.latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);

                        // Grid recalculation every 3 seconds
                        let paused = risk.global_paused.load(Ordering::Acquire) != 0;
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
                                        if let Some(p) = sniper_types::l2_command::calculate_warped_grid_level(
                                            grid_warp, lvl, true
                                        ) {
                                            if p > 0 {
                                                warp_buys.push(p as f64 / PRICE_SCALE_I as f64);
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
                    if v["event"] == "auth" && v["status"] == "OK" {
                        authed = true;
                        info!(event = "authenticated", bot = "grid");
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
