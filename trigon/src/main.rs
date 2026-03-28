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
    let engine = unsafe { &*(engine_mmap.as_ptr() as *const TrigonEngineState) };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const TrigonRiskState) };

    let key = std::env::var("BITFINEX_API_KEY").context("Missing BITFINEX_API_KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing BITFINEX_API_SECRET")?;

    info!(event = "system_start", version = VERSION, bot = "trigon", strategy = "triangular_arbitrage");
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
                            let fee_bps = risk.fee_bps.load(Ordering::Acquire) as f64;
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
                                if !paused && profit > min_profit && et.executing.load(Ordering::Acquire) == 0 {
                                    et.executing.store(1, Ordering::Release);
                                    // TODO: Implement atomic 3-leg execution via ox_multi
                                    info!(event = "arb_opportunity", triangle = t, profit_bps = format!("{:.2}", profit), rate = format!("{:.8}", rate));
                                    notifier.send(format!("💰 ARB! Triangle {} profit={:.2}bps rate={:.8}", t, profit, rate));
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
