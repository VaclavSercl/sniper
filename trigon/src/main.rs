// 🔺 Trigon L0 Engine — Triangular Arbitrage Scanner
// Sniper Armada · Bot #4 · v1.0.0
//
// Monitors N triangles (A→B→C→A) for cross-pair price inefficiencies.
// When implied_rate > 1 + 3×fee → execute all 3 legs atomically.
//
// Architecture:
//   - L0 (this): Rust async, WebSocket, real-time triangle scanning
//   - L1: Python l1_shield.py (liquidity filter, execution risk)
//   - L2: Python sniper_orchestrator.py (triangle selection, fee optimization)
//
// mmap IPC:
//   - /dev/shm/beroun/trigon_engine.bin (written by L0, read by dashboard/brain)
//   - /dev/shm/beroun/trigon_risk.bin (written by L2, read by L0)

use std::time::{SystemTime, UNIX_EPOCH, Instant, Duration};
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::collections::HashMap;

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

use sniper_types::trigon_types::*;
use sniper_types::moonshot_types::{str_to_symbol_hash, symbol_hash_to_str};
use sniper_types::PRICE_SCALE_I;

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
        let log_dir = exe_dir.join("../../trigon/logs");
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
                    .json(&json!({"chat_id": chat_id, "text": format!("🔺 *TRIGON*\n`{}`", msg), "parse_mode": "Markdown"}))
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
// Triangle Calculator
// ═══════════════════════════════════════════════════════════
/// Calculate implied arbitrage rate for a triangle.
/// For legs with direction=0 (BUY): use ASK price (pay more)
/// For legs with direction=1 (SELL): use BID price (receive less)
/// Returns (implied_rate, profit_bps_after_fees)
fn calculate_triangle(
    bids: &[f64; 3],
    asks: &[f64; 3],
    directions: &[u32; 3],
    fee_bps: f64,
) -> (f64, f64) {
    let mut rate = 1.0;
    for i in 0..3 {
        if directions[i] == 0 {
            // BUY leg: we pay ASK (e.g., buy BTC with USD → use tBTCUSD ASK)
            if asks[i] > 0.0 { rate /= asks[i]; } else { return (0.0, -10000.0); }
        } else {
            // SELL leg: we receive BID (e.g., sell ETH for USD → use tETHUSD BID)
            if bids[i] > 0.0 { rate *= bids[i]; } else { return (0.0, -10000.0); }
        }
    }

    // Fee for each leg (3 trades)
    let fee_multiplier = (1.0 - fee_bps / 10000.0).powi(3);
    let rate_after_fees = rate * fee_multiplier;
    let profit_bps = (rate_after_fees - 1.0) * 10000.0;

    (rate, profit_bps)
}

// ═══════════════════════════════════════════════════════════
// Fast Ticker Parser (same proven pattern from Moonshot/Grid)
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
    // BID
    let start = i;
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() || start >= i { return None; }
    let bid = (std::str::from_utf8(&data[start..i]).ok()?.parse::<f64>().ok()? * PRICE_SCALE_I as f64) as i64;
    i += 1;
    if i >= data.len() { return None; }
    // Skip BID_SIZE
    while i < data.len() && data[i] != b',' { i += 1; }
    i += 1;
    if i >= data.len() { return None; }
    // ASK
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
    let engine_mmap = init_mmap_ptr::<TrigonEngineState>(TRIGON_ENGINE_PATH)?;
    let risk_mmap = init_mmap_ptr::<TrigonRiskState>(TRIGON_RISK_PATH)?;
    let fee_mmap = init_mmap_ptr::<sniper_types::fee_types::GlobalFeeState>(
        sniper_types::fee_types::FEE_STATE_PATH)?;
    let engine = unsafe { &*(engine_mmap.as_ptr() as *const TrigonEngineState) };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const TrigonRiskState) };
    let fee_state = unsafe { &*(fee_mmap.as_ptr() as *const sniper_types::fee_types::GlobalFeeState) };
    // L2CommandMatrix: manual init (bypass init_mmap_ptr which truncates to sizeof::<T>)
    let l2cmd_mmap = {
        let f = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false)
            .open(sniper_types::l2_command::L2_COMMAND_PATH)?;
        f.set_len(sniper_types::l2_command::L2_COMMAND_FILE_SIZE as u64)?;
        unsafe { MmapMut::map_mut(&f)? }
    };
    let l2cmd = unsafe { &*(l2cmd_mmap.as_ptr() as *const sniper_types::l2_command::L2CommandMatrix) };

    let key = std::env::var("BITFINEX_API_KEY").context("Missing BITFINEX_API_KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing BITFINEX_API_SECRET")?;

    info!(event = "system_start", version = VERSION, bot = "trigon", strategy = "triangular_arbitrage");

    // ═══ SINGLE-INSTANCE LOCK ═══
    use fs2::FileExt;
    let lock_file = std::fs::File::create("/tmp/trigon-core.lock")
        .context("Failed to create trigon lock file")?;
    if lock_file.try_lock_exclusive().is_err() {
        tracing::error!(event = "dual_instance_blocked",
            msg = "Another trigon-core is already running! Aborting.");
        std::process::exit(1);
    }
    let _lock_guard = lock_file;

    notifier.send(format!("🔺 Trigon v{} (Triangular Arbitrage) ONLINE", VERSION));

    // ═══ MAIN RECONNECT LOOP ═══
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

        // Collect all unique symbols from configured triangles
        let mut chan_to_symbol: HashMap<i64, u64> = HashMap::new();
        let mut symbol_bids: HashMap<u64, f64> = HashMap::new();
        let mut symbol_asks: HashMap<u64, f64> = HashMap::new();
        let mut subscribed_symbols: Vec<String> = Vec::new();

        for i in 0..TRIGON_MAX_TRIANGLES {
            for leg in 0..TRIGON_LEGS {
                let sym_hash = risk.triangles[i].leg_symbols[leg].load(Ordering::Acquire);
                if sym_hash != 0 {
                    let symbol = symbol_hash_to_str(sym_hash);
                    if !subscribed_symbols.contains(&symbol) {
                        write.send(Message::Text(
                            json!({"event":"subscribe","channel":"ticker","symbol":&symbol}).to_string().into()
                        )).await?;
                        subscribed_symbols.push(symbol.clone());
                        info!(event = "subscribing", symbol = symbol);
                    }
                }
            }
        }

        // If no triangles configured, subscribe to default set
        if subscribed_symbols.is_empty() {
            for &(a, b, c) in KNOWN_TRIANGLES {
                for sym in [a, b, c] {
                    if !subscribed_symbols.contains(&sym.to_string()) {
                        write.send(Message::Text(
                            json!({"event":"subscribe","channel":"ticker","symbol":sym}).to_string().into()
                        )).await?;
                        subscribed_symbols.push(sym.to_string());
                    }
                }
            }
            info!(event = "default_triangles", count = KNOWN_TRIANGLES.len());
        }

        let mut authed = false;
        let mut last_scan = Instant::now();
        let mut order_msg = String::with_capacity(2048);
        let mut last_exec_ms: [u64; TRIGON_MAX_TRIANGLES] = [0; TRIGON_MAX_TRIANGLES];

        // ═══ MESSAGE LOOP ═══
        while let Some(msg) = read.next().await {
            let loop_start = Instant::now();
            let msg = match msg { Ok(m) => m, Err(_) => break };

            let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
            engine.heartbeat_ms.store(now_ms, Ordering::Release);

            if let Message::Text(text) = msg {
                let bytes = text.as_bytes();

                // Fast path: ticker update
                if let Some((chan, bid, ask)) = fast_parse_ticker(bytes) {
                    if let Some(&sym_hash) = chan_to_symbol.get(&chan) {
                        let bid_f = bid as f64 / PRICE_SCALE_I as f64;
                        let ask_f = ask as f64 / PRICE_SCALE_I as f64;
                        symbol_bids.insert(sym_hash, bid_f);
                        symbol_asks.insert(sym_hash, ask_f);

                        // Update leg data in engine state
                        for t in 0..TRIGON_MAX_TRIANGLES {
                            for l in 0..TRIGON_LEGS {
                                let leg_hash = risk.triangles[t].leg_symbols[l].load(Ordering::Acquire);
                                if leg_hash == sym_hash {
                                    engine.triangles[t].legs[l].best_bid.store(bid as u64, Ordering::Release);
                                    engine.triangles[t].legs[l].best_ask.store(ask as u64, Ordering::Release);
                                    engine.triangles[t].legs[l].latency_ns.store(
                                        loop_start.elapsed().as_nanos() as u64, Ordering::Release
                                    );
                                }
                            }
                        }

                        // Triangle scan every 100ms
                        if authed && last_scan.elapsed().as_millis() >= 100 {
                            // Read real taker fee from shared GlobalFeeState
                            // All 3 legs are IOC → taker fee applies to each
                            // Scale: bps×100 → bps (e.g., 2000 → 20 bps)
                            let fee_bps = fee_state.taker_fee_bps.load(Ordering::Relaxed) as f64 / 100.0;
                            let paused = risk.global_paused.load(Ordering::Acquire) != 0;
                            let mut best_profit = -10000.0f64;

                            for t in 0..TRIGON_MAX_TRIANGLES {
                                let tr = &risk.triangles[t];
                                if tr.enabled.load(Ordering::Acquire) == 0 { continue; }

                                let mut bids = [0.0f64; 3];
                                let mut asks = [0.0f64; 3];
                                let mut dirs = [0u32; 3];
                                let mut all_valid = true;

                                for l in 0..TRIGON_LEGS {
                                    let h = tr.leg_symbols[l].load(Ordering::Acquire);
                                    dirs[l] = tr.leg_directions[l].load(Ordering::Acquire);
                                    if let (Some(&b), Some(&a)) = (symbol_bids.get(&h), symbol_asks.get(&h)) {
                                        bids[l] = b;
                                        asks[l] = a;
                                    } else {
                                        all_valid = false;
                                        break;
                                    }
                                }

                                if !all_valid { continue; }

                                let (rate, profit) = calculate_triangle(&bids, &asks, &dirs, fee_bps);
                                let et = &engine.triangles[t];
                                et.implied_rate.store((rate * PRICE_SCALE_I as f64) as i64, Ordering::Release);
                                et.profit_bps.store((profit * 100.0) as i64, Ordering::Release);
                                et.fee_cost_bps.store((fee_bps * 3.0 * 100.0) as u64, Ordering::Release);
                                et.last_calc_ns.store(now_ms * 1_000_000, Ordering::Release);

                                if profit > best_profit { best_profit = profit; }

                                // Execute if profitable and not paused
                                let min_profit = tr.min_profit_bps.load(Ordering::Acquire) as f64 / 100.0;
                                let cooldown = tr.cooldown_ms.load(Ordering::Acquire);
                                let max_usd = tr.max_order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;

                                // ═══ v14.2 LATENCY-AWARE PADDING (Issue #18) ═══
                                // L2 pre-computes p95 Tick-to-Trade → padding & killswitch
                                // L1: O(1) check via SeqLock-protected reads
                                let latency_pad = {
                                    let (v1, ok1) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
                                    let pad = l2cmd.latency_padding_bps.load(Ordering::Relaxed);
                                    let kill = l2cmd.latency_killswitch.load(Ordering::Relaxed);
                                    let (v2, ok2) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
                                    if v1 == v2 && ok1 && ok2 {
                                        if kill == 1 { continue; } // Killswitch: exchange overloaded
                                        pad.max(0) as f64
                                    } else { 0.0 }
                                };
                                let effective_min_profit = min_profit + latency_pad;

                                if !paused && profit > effective_min_profit
                                    && et.executing.load(Ordering::Acquire) == 0
                                    && (now_ms - last_exec_ms[t]) > cooldown
                                    && max_usd > 0.0
                                {
                                    et.executing.store(1, Ordering::Release);

                                    // Build ox_multi with 3 IOC legs + GID 4000
                                    // Each leg: symbol, direction (BUY=positive, SELL=negative), price
                                    order_msg.clear();
                                    order_msg.push_str("[0,\"ox_multi\",null,[");

                                    for l in 0..TRIGON_LEGS {
                                        let sym_hash = tr.leg_symbols[l].load(Ordering::Acquire);
                                        let dir = dirs[l];
                                        let sym = symbol_hash_to_str(sym_hash);
                                        let price = if dir == 0 { asks[l] } else { bids[l] };
                                        // Compute quantity: max_usd / price of this leg
                                        let qty = if price > 0.0 { max_usd / price } else { 0.0 };
                                        let signed_qty = if dir == 0 { qty } else { -qty };

                                        if l > 0 { order_msg.push(','); }
                                        order_msg.push_str("[\"on\",{\"gid\":4000,\"symbol\":\"");
                                        order_msg.push_str(&sym);
                                        order_msg.push_str("\",\"amount\":");
                                        // Use ryu for fast float formatting
                                        let mut buf = ryu::Buffer::new();
                                        order_msg.push_str(buf.format(signed_qty));
                                        order_msg.push_str(",\"price\":\"");
                                        let mut buf2 = ryu::Buffer::new();
                                        order_msg.push_str(buf2.format(price));
                                        order_msg.push_str("\",\"type\":\"EXCHANGE IOC\"}]");
                                    }
                                    order_msg.push_str("]]");

                                    match write.send(Message::Text(order_msg.clone().into())).await {
                                        Ok(_) => {
                                            et.executions.fetch_add(1, Ordering::Relaxed);
                                            last_exec_ms[t] = now_ms;
                                            info!(event = "arb_execute", triangle = t,
                                                profit_bps = format!("{:.2}", profit),
                                                rate = format!("{:.8}", rate),
                                                max_usd = format!("{:.2}", max_usd));
                                            notifier.send(format!(
                                                "💰 ARB EXEC! Tri#{} profit={:.2}bps rate={:.8} size=${:.2}",
                                                t, profit, rate, max_usd));
                                        }
                                        Err(e) => {
                                            warn!(event = "arb_send_fail", triangle = t, error = %e);
                                        }
                                    }
                                    et.executing.store(0, Ordering::Release);
                                }
                            }

                            engine.best_profit_bps.store((best_profit * 100.0) as i64, Ordering::Release);
                            engine.scan_latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);
                            last_scan = Instant::now();
                        }
                    }
                    continue;
                }

                // Slow path: events
                if bytes.first() == Some(&b'{') {
                    let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();
                    if v["event"] == "auth" && v["status"] == "OK" {
                        authed = true;
                        info!(event = "authenticated", bot = "trigon");
                    }
                    if v["event"] == "subscribed" && v["channel"] == "ticker" {
                        if let (Some(cid), Some(sym)) = (v["chanId"].as_i64(), v["symbol"].as_str()) {
                            let hash = str_to_symbol_hash(sym);
                            chan_to_symbol.insert(cid, hash);
                            info!(event = "subscribed", symbol = sym, chan_id = cid);
                        }
                    }
                }
            }
        }

        warn!(event = "ws_disconnected", bot = "trigon");
        notifier.send("⚠️ Trigon: WebSocket disconnected, reconnecting...".to_string());
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
