use std::time::{SystemTime, UNIX_EPOCH, Instant, Duration};
use std::fs::{OpenOptions};
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
use tracing::{info, Level};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};
use memmap2::MmapMut;
use anyhow::{Context, Result};

use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE_I};

const BITFINEX_AUTH_URL: &str = "wss://api.bitfinex.com/ws/2";
type HmacSha384 = Hmac<Sha384>;

// Background Notifier Task to keep Hot Path clean
struct AsyncNotifier {
    tx: mpsc::UnboundedSender<String>,
}

impl AsyncNotifier {
    fn new() -> Self {
        let (tx, mut rx) = mpsc::unbounded_channel::<String>();
        let token = std::env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default();
        let chat_id = std::env::var("TELEGRAM_CHAT_ID").unwrap_or_default();
        let client = reqwest::Client::builder().timeout(Duration::from_secs(10)).build().unwrap();
        let alerts_log = "/home/wwwenda/beroun-brain/short_term/alerts.log".to_string();

        tokio::spawn(async move {
            while let Some(msg) = rx.recv().await {
                info!(event = "async_alert", message = msg);
                if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&alerts_log) {
                    let _ = writeln!(file, "[{:?}] {}", SystemTime::now(), msg);
                }
                if token.is_empty() || chat_id.is_empty() { continue; }
                let url = format!("https://api.telegram.org/bot{}/sendMessage", token);
                let _ = client.post(url)
                    .json(&json!({"chat_id": chat_id, "text": format!("🐺 *BEROUN*\n`{}`", msg), "parse_mode": "Markdown"}))
                    .send().await;
            }
        });
        Self { tx }
    }

    fn send(&self, msg: String) {
        let _ = self.tx.send(msg);
    }
}

fn init_mmap_ptr<T: Default>(path: &str) -> Result<MmapMut> {
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

// ULTRA-FAST TICKER PARSER (Zero-allocation)
// Bitfinex ticker: [CHAN_ID, [BID, BID_SIZE, ASK, ASK_SIZE, ..., LAST_PRICE, ...]]
fn fast_parse_ticker(data: &[u8], target_chan: i64) -> Option<i64> {
    if data.len() < 10 || data[0] != b'[' { return None; }
    
    // Find first comma
    let mut i = 1;
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() { return None; }
    
    let chan_id = std::str::from_utf8(&data[1..i]).ok()?.parse::<i64>().ok()?;
    if chan_id != target_chan { return None; }
    
    // Check for heartbeat "hb"
    if i + 2 < data.len() && data[i+1] == b'"' && data[i+2] == b'h' { return None; }
    
    // Find nested array start '['
    while i < data.len() && data[i] != b'[' { i += 1; }
    if i >= data.len() { return None; }
    
    // Skip 6 commas to get to LAST_PRICE
    let mut commas = 0;
    while i < data.len() && commas < 6 {
        if data[i] == b',' { commas += 1; }
        i += 1;
    }
    if i >= data.len() { return None; }
    
    // Parse LAST_PRICE as float and convert to fixed-point
    let start = i;
    while i < data.len() && data[i] != b',' && data[i] != b']' { i += 1; }
    let price_str = std::str::from_utf8(&data[start..i]).ok()?;
    let p_f64 = price_str.parse::<f64>().ok()?;
    Some((p_f64 * PRICE_SCALE_I as f64) as i64)
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();
    tracing_subscriber::registry().with(fmt::layer().with_target(false).json()).with(EnvFilter::from_default_env().add_directive(Level::INFO.into())).init();

    let notifier = Arc::new(AsyncNotifier::new());
    let engine_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;
    let risk_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;
    
    let engine = unsafe { &*(engine_mmap.as_ptr() as *const EngineState) };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const RiskState) };

    let key = std::env::var("BITFINEX_API_KEY").context("Missing API KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing API SECRET")?;

    info!(event = "system_start", version = "5.1.0-ultra-low-latency");
    notifier.send("🐺 Beroun Sniper v5.1.0 ONLINE (Extreme Latency Edition)".to_string());

    loop {
        let ws_result = connect_async(BITFINEX_AUTH_URL).await;
        let (ws, _) = match ws_result {
            Ok(v) => v,
            Err(_) => { tokio::time::sleep(Duration::from_secs(60)).await; continue; }
        };

        let (mut write, mut read) = ws.split();
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis().to_string();
        let auth_payload = format!("AUTH{}", nonce);
        let sig = get_sig(&sec, &auth_payload).await;

        let auth_msg = json!({ "event": "auth", "apiKey": key, "authSig": sig, "authPayload": auth_payload, "authNonce": nonce, "dms": 4 });
        write.send(Message::Text(auth_msg.to_string().into())).await?;
        write.send(Message::Text(json!({"event": "subscribe", "channel": "ticker", "symbol": "tBTCUSD"}).to_string().into())).await?;

        let mut chan_id: Option<i64> = None;
        let mut last_upd = Instant::now();
        let mut last_p_i64 = 0i64;
        let mut authed = false;
        let mut order_msg = String::with_capacity(1024);

        while let Some(msg) = read.next().await {
            let start_process = Instant::now();
            let msg = match msg { Ok(m) => m, Err(_) => break };

            if let Message::Text(text) = msg {
                let bytes = text.as_bytes();
                
                // Fast path for Ticker
                if let Some(chan) = chan_id {
                    if let Some(p_i64) = fast_parse_ticker(bytes, chan) {
                        // ATOMIC UPDATE (Zero-copy, no casting UB)
                        engine.best_bid.store(p_i64 as u64, Ordering::Release);
                        engine.best_ask.store(p_i64 as u64, Ordering::Release);
                        engine.latency_ns.store(start_process.elapsed().as_nanos() as u64, Ordering::Release);

                        if authed && last_upd.elapsed().as_millis() > 50 && (p_i64 - last_p_i64).abs() > (0.5 * PRICE_SCALE_I as f64) as i64 {
                            if risk.paused.load(Ordering::Acquire) == 0 {
                                let order_usd = risk.order_usd.load(Ordering::Acquire) as i64;
                                let grid_step = risk.grid_step.load(Ordering::Acquire) as i64;
                                let bias = risk.bias_offset.load(Ordering::Acquire);

                                let buy_p = (p_i64 - grid_step + bias) as f64 / PRICE_SCALE_I as f64;
                                let sell_p = (p_i64 + grid_step + bias) as f64 / PRICE_SCALE_I as f64;
                                let btc_amount = (order_usd as f64 / p_i64 as f64).max(0.00015);

                                // Optimized Order Generation
                                order_msg.clear();
                                order_msg.push_str("[0,\"ox_multi\",null,[[\"oc_multi\",{\"symbol\":\"tBTCUSD\"}],");
                                order_msg.push_str("[\"on\",{\"symbol\":\"tBTCUSD\",\"amount\":");
                                order_msg.push_str(&format!("{:.5}", btc_amount));
                                order_msg.push_str(",\"price\":\"");
                                order_msg.push_str(&format!("{:.2}", buy_p));
                                order_msg.push_str("\",\"type\":\"EXCHANGE LIMIT\"}],[\"on\",{\"symbol\":\"tBTCUSD\",\"amount\":");
                                order_msg.push_str(&format!("{:.5}", -btc_amount));
                                order_msg.push_str(",\"price\":\"");
                                order_msg.push_str(&format!("{:.2}", sell_p));
                                order_msg.push_str("\",\"type\":\"EXCHANGE LIMIT\"}]]]");
                                
                                let _ = write.send(Message::Text(order_msg.clone().into())).await;
                                last_upd = Instant::now();
                                last_p_i64 = p_i64;
                            }
                        }
                        continue; // Exit ticker fast path
                    }
                }

                // Slow path for Auth/Events (uses standard JSON parser)
                if bytes[0] == b'{' {
                    let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();
                    if v["event"] == "auth" && v["status"] == "OK" { authed = true; }
                    if v["event"] == "subscribed" { chan_id = v["chanId"].as_i64(); }
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
