// 🌙 Moonshot L0 Engine — Multi-Symbol Flash Crash Catcher
// Sniper Armada · Bot #2 · v1.0.0
//
// Captures rapid price drops ("wicks") across up to 20 Bitfinex pairs.
// AI-driven pair selection via L2 Oracle (sniper_orchestrator.py).
// Ghost BUY orders are placed X% below market and dynamically repositioned.
//
// Architecture:
//   - L0 (this): Rust async, fastwebsockets, zero-alloc hot path
//   - L1: Python l1_shield.py (toxic flow filter, delta modifiers)
//   - L2: Python sniper_orchestrator.py (Gemini AI pair selection, 66min cycle)
//
// mmap IPC:
//   - /dev/shm/beroun/moonshot_engine.bin (written by L0, read by dashboard/brain)
//   - /dev/shm/beroun/moonshot_risk.bin (written by L2, read by L0)

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

use sniper_types::moonshot_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};

const BITFINEX_WS_URL: &str = "wss://api.bitfinex.com/ws/2";
const VERSION: &str = "1.0.0";

type HmacSha384 = Hmac<Sha384>;

// ═══════════════════════════════════════════════════════════
// Async Notifier — keeps hot path clean of I/O
// ═══════════════════════════════════════════════════════════
struct AsyncNotifier {
    tx: mpsc::UnboundedSender<String>,
}

impl AsyncNotifier {
    fn new() -> Self {
        let (tx, mut rx) = mpsc::unbounded_channel::<String>();
        let token = std::env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default();
        let chat_id = std::env::var("TELEGRAM_CHAT_ID").unwrap_or_default();
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .unwrap();

        // Resolve alerts log path relative to binary
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .unwrap_or_else(|| std::path::PathBuf::from("."));
        let log_dir = exe_dir.join("../../moonshot/logs");
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
                    .json(&json!({"chat_id": chat_id, "text": format!("🌙 *MOONSHOT*\n`{}`", msg), "parse_mode": "Markdown"}))
                    .send().await;
            }
        });
        Self { tx }
    }

    fn send(&self, msg: String) {
        let _ = self.tx.send(msg);
    }
}

// ═══════════════════════════════════════════════════════════
// mmap Initialization
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

// ═══════════════════════════════════════════════════════════
// HMAC Auth
// ═══════════════════════════════════════════════════════════
async fn get_sig(sec: &str, payload: &str) -> String {
    let mut mac = HmacSha384::new_from_slice(sec.as_bytes()).expect("HMAC error");
    mac.update(payload.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

// ═══════════════════════════════════════════════════════════
// ULTRA-FAST TICKER PARSER (Zero-allocation)
// Bitfinex ticker: [CHAN_ID, [BID, BID_SIZE, ASK, ASK_SIZE, ..., LAST_PRICE, ...]]
// ═══════════════════════════════════════════════════════════
fn fast_parse_ticker(data: &[u8]) -> Option<(i64, i64, i64)> {
    if data.len() < 10 || data[0] != b'[' { return None; }

    // Parse channel ID
    let mut i = 1;
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() { return None; }
    let chan_id = std::str::from_utf8(&data[1..i]).ok()?.parse::<i64>().ok()?;

    // Check for heartbeat "hb"
    if i + 2 < data.len() && data[i+1] == b'"' && data[i+2] == b'h' { return None; }

    // Find nested array start '['
    while i < data.len() && data[i] != b'[' { i += 1; }
    if i >= data.len() { return None; }
    i += 1; // skip '['

    // Parse BID (field 0)
    let start = i;
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() || start >= i { return None; }
    let bid_str = std::str::from_utf8(&data[start..i]).ok()?;
    let bid = (bid_str.parse::<f64>().ok()? * PRICE_SCALE_I as f64) as i64;
    i += 1; // skip ','
    if i >= data.len() { return None; }

    // Skip BID_SIZE (field 1)
    while i < data.len() && data[i] != b',' { i += 1; }
    i += 1;
    if i >= data.len() { return None; }

    // Parse ASK (field 2)
    let start = i;
    while i < data.len() && data[i] != b',' && data[i] != b']' { i += 1; }
    if start >= i { return None; }
    let ask_str = std::str::from_utf8(&data[start..i]).ok()?;
    let ask = (ask_str.parse::<f64>().ok()? * PRICE_SCALE_I as f64) as i64;

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
    let engine_mmap = init_mmap_ptr::<MoonshotEngineState>(MOONSHOT_ENGINE_PATH)?;
    let risk_mmap = init_mmap_ptr::<MoonshotRiskState>(MOONSHOT_RISK_PATH)?;

    let engine = unsafe { &*(engine_mmap.as_ptr() as *const MoonshotEngineState) };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const MoonshotRiskState) };

    // L2 Command Matrix — CAS Moonshot tripwire + trigger price
    let l2cmd_mmap = {
        let f = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false)
            .open(sniper_types::l2_command::L2_COMMAND_PATH)?;
        f.set_len(sniper_types::l2_command::L2_COMMAND_FILE_SIZE as u64)?;
        unsafe { MmapMut::map_mut(&f)? }
    };
    let l2cmd = unsafe { &*(l2cmd_mmap.as_ptr() as *const sniper_types::l2_command::L2CommandMatrix) };

    let key = std::env::var("BITFINEX_API_KEY").context("Missing BITFINEX_API_KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing BITFINEX_API_SECRET")?;

    info!(event = "system_start", version = VERSION, bot = "moonshot", strategy = "flash_crash_multi_symbol");

    // ═══ SINGLE-INSTANCE LOCK ═══
    use fs2::FileExt;
    let lock_file = std::fs::File::create("/tmp/moonshot-core.lock")
        .context("Failed to create moonshot lock file")?;
    if lock_file.try_lock_exclusive().is_err() {
        tracing::error!(event = "dual_instance_blocked",
            msg = "Another moonshot-core is already running! Aborting.");
        std::process::exit(1);
    }
    let _lock_guard = lock_file;

    notifier.send(format!("🌙 Moonshot v{} (Multi-Symbol AI) ONLINE", VERSION));

    // ═══ MAIN RECONNECT LOOP ═══
    loop {
        let ws_result = connect_async(BITFINEX_WS_URL).await;
        let (ws, _) = match ws_result {
            Ok(v) => v,
            Err(e) => {
                warn!(event = "ws_connect_fail", error = %e);
                tokio::time::sleep(Duration::from_secs(60)).await;
                continue;
            }
        };

        let (mut write, mut read) = ws.split();

        // ═══ AUTH ═══
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis().to_string();
        let auth_payload = format!("AUTH{}", nonce);
        let sig = get_sig(&sec, &auth_payload).await;
        let auth_msg = json!({
            "event": "auth",
            "apiKey": key,
            "authSig": sig,
            "authPayload": auth_payload,
            "authNonce": nonce,
            "dms": 4
        });
        write.send(Message::Text(auth_msg.to_string().into())).await?;

        // ═══ MULTI-SYMBOL SUBSCRIPTION ═══
        let mut chan_to_idx: HashMap<i64, usize> = HashMap::new();
        let mut idx_to_symbol: HashMap<usize, String> = HashMap::new();
        let mut last_order_ts = vec![Instant::now(); MOONSHOT_MAX_PAIRS];

        // Subscribe to all pairs configured by AI (from risk mmap)
        for i in 0..MOONSHOT_MAX_PAIRS {
            let symbol_hash = risk.pairs[i].symbol_hash.load(Ordering::Acquire);
            if symbol_hash != 0 {
                let symbol = symbol_hash_to_str(symbol_hash);
                write.send(Message::Text(
                    json!({"event": "subscribe", "channel": "ticker", "symbol": symbol}).to_string().into()
                )).await?;
                idx_to_symbol.insert(i, symbol);
            }
        }

        let mut authed = false;
        let mut order_msg = String::with_capacity(1024);

        // ═══ MESSAGE LOOP ═══
        while let Some(msg) = read.next().await {
            let loop_start = Instant::now();
            let msg = match msg { Ok(m) => m, Err(_) => break };

            // Update engine heartbeat
            let now_ms = SystemTime::now().duration_since(UNIX_EPOCH)
                .unwrap_or_default().as_millis() as u64;
            engine.heartbeat_ms.store(now_ms, Ordering::Release);

            if let Message::Text(text) = msg {
                let bytes = text.as_bytes();

                // ═══ FAST PATH: Ticker ═══
                if let Some((chan, bid, ask)) = fast_parse_ticker(bytes) {
                    if let Some(&idx) = chan_to_idx.get(&chan) {
                        let e = &engine.pairs[idx];
                        let r = &risk.pairs[idx];

                        // Write market data
                        e.best_bid.store(bid as u64, Ordering::Release);
                        e.best_ask.store(ask as u64, Ordering::Release);
                        e.last_trade.store(((bid + ask) / 2) as u64, Ordering::Release);
                        e.latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);

                        // ═══ MOONSHOT LOGIC ═══
                        let paused = risk.global_paused.load(Ordering::Acquire) != 0;
                        let min_interval_ms = 50;

                        // ═══ v14.2 CAS TRIPWIRE (Issue #18) ═══
                        // L2 pre-computes trigger_price (ema - 3.5σ) and arms weapon
                        // L1: O(1) price comparison + CAS atomic disarm = Single Bullet
                        if authed && !paused {
                            let mid_price = (bid + ask) / 2;
                            // Volume filter disabled here — L2 controls arming via leverage flush
                            // Pass avg_vol=0 to bypass anti-spoofing (L2 is the gatekeeper)
                            if let Some(trigger) = sniper_types::l2_command::moonshot_check_and_disarm(
                                l2cmd, mid_price as i64, 1, 0  // avg=0 bypasses volume check
                            ) {
                                // 🚀 MOONSHOT FIRED! CAS guarantees single execution
                                let order_usd = r.order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                                let mid_f64 = mid_price as f64 / PRICE_SCALE_I as f64;
                                if order_usd > 0.0 && mid_f64 > 0.0 {
                                    let coin_amount = (order_usd / mid_f64).max(0.00015);
                                    if let Some(symbol) = idx_to_symbol.get(&idx) {
                                        // IOC BUY at market — flash crash instant execution
                                        order_msg.clear();
                                        order_msg.push_str("[0,\"on\",null,{\"gid\":2001,\"symbol\":\"");
                                        order_msg.push_str(symbol);
                                        order_msg.push_str("\",\"amount\":");
                                        order_msg.push_str(&format!("{:.5}", coin_amount));
                                        order_msg.push_str(",\"price\":\"");
                                        order_msg.push_str(&format!("{:.4}", mid_f64));
                                        order_msg.push_str("\",\"type\":\"EXCHANGE IOC\"}]");

                                        let _ = write.send(Message::Text(order_msg.clone().into())).await;
                                        warn!(event = "moonshot_fired", symbol = %symbol,
                                              trigger = trigger as f64 / PRICE_SCALE_I as f64,
                                              price = mid_f64, amount = coin_amount);
                                        notifier.send(format!("🚀 MOONSHOT FIRED! {} @ ${:.2} (trigger ${:.2})",
                                            symbol, mid_f64, trigger as f64 / PRICE_SCALE_I as f64));
                                        last_order_ts[idx] = Instant::now();
                                    }
                                }
                            }
                        }

                        // ═══ GHOST ORDERS (existing passive logic) ═══
                        if authed && !paused && last_order_ts[idx].elapsed().as_millis() > min_interval_ms {
                            let mid_price = (bid + ask) / 2;
                            let mid_f64 = mid_price as f64 / PRICE_SCALE_I as f64;

                            let drop_pct = r.m_shot_price_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                            let tp_pct = r.tp_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                            let order_usd = r.order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;

                            if drop_pct > 0.0 && order_usd > 0.0 && mid_f64 > 0.0 {
                                let buy_p = mid_f64 * (1.0 - (drop_pct / 100.0));
                                let sell_p = buy_p * (1.0 + (tp_pct / 100.0));
                                let coin_amount = (order_usd / buy_p).max(0.00015);

                                let ask_f64 = ask as f64 / PRICE_SCALE_I as f64;
                                if buy_p >= ask_f64 { continue; }

                                if let Some(symbol) = idx_to_symbol.get(&idx) {
                                    order_msg.clear();
                                    order_msg.push_str("[0,\"ox_multi\",null,[[\"oc_multi\",{\"symbol\":\"");
                                    order_msg.push_str(symbol);
                                    order_msg.push_str("\"}],[\"on\",{\"gid\":2000,\"symbol\":\"");
                                    order_msg.push_str(symbol);
                                    order_msg.push_str("\",\"amount\":");
                                    order_msg.push_str(&format!("{:.5}", coin_amount));
                                    order_msg.push_str(",\"price\":\"");
                                    order_msg.push_str(&format!("{:.4}", buy_p));
                                    order_msg.push_str("\",\"type\":\"EXCHANGE LIMIT\"}],[\"on\",{\"gid\":2000,\"symbol\":\"");
                                    order_msg.push_str(symbol);
                                    order_msg.push_str("\",\"amount\":");
                                    order_msg.push_str(&format!("{:.5}", -coin_amount));
                                    order_msg.push_str(",\"price\":\"");
                                    order_msg.push_str(&format!("{:.4}", sell_p));
                                    order_msg.push_str("\",\"type\":\"EXCHANGE LIMIT\"}]]]");

                                    let _ = write.send(Message::Text(order_msg.clone().into())).await;
                                    e.buy_order_price.store((buy_p * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                                    last_order_ts[idx] = Instant::now();
                                }
                            }
                        }
                    }
                    continue; // Exit ticker fast path
                }

                // ═══ SLOW PATH: Auth/Events ═══
                if bytes.first() == Some(&b'{') {
                    let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();

                    if v["event"] == "auth" && v["status"] == "OK" {
                        authed = true;
                        info!(event = "authenticated", bot = "moonshot");
                    }

                    // Map chanId → internal pair index
                    if v["event"] == "subscribed" && v["channel"] == "ticker" {
                        if let (Some(chan_id), Some(symbol)) = (v["chanId"].as_i64(), v["symbol"].as_str()) {
                            for (&idx, sym) in idx_to_symbol.iter() {
                                if sym == symbol {
                                    chan_to_idx.insert(chan_id, idx);
                                    info!(event = "pair_subscribed", symbol = symbol, idx = idx, chan_id = chan_id);
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }

        warn!(event = "ws_disconnected", bot = "moonshot");
        notifier.send("⚠️ Moonshot: WebSocket disconnected, reconnecting...".to_string());
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
