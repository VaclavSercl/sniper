use std::time::{SystemTime, UNIX_EPOCH, Instant, Duration};
use std::fs::{OpenOptions};
use std::io::Write;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tokio::sync::mpsc;
use futures_util::{StreamExt, SinkExt};
use serde_json::{json, Value};
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
        let alerts_log = "/home/wwwenda/hft-sniper/logs/alerts.log".to_string();

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

use std::fmt::Write as FmtWrite;
use simd_json::prelude::*;
use simd_json::BorrowedValue;
use crc32fast::Hasher;

// Helper to update the book
fn update_book(levels: &mut [beroun_types::OrderBookLevel], price: u64, amount: i64, count: u64) {
    if count > 0 {
        // Update or Insert
        let mut found = false;
        for lvl in levels.iter_mut() {
            if lvl.price.load(Ordering::Acquire) == price {
                lvl.amount.store(amount, Ordering::Release);
                lvl.count.store(count, Ordering::Release);
                found = true;
                break;
            }
        }
        if !found {
            // Find empty or replace based on price priority
            for lvl in levels.iter_mut() {
                if lvl.count.load(Ordering::Acquire) == 0 {
                    lvl.price.store(price, Ordering::Release);
                    lvl.amount.store(amount, Ordering::Release);
                    lvl.count.store(count, Ordering::Release);
                    break;
                }
            }
        }
    } else {
        // Delete
        for lvl in levels.iter_mut() {
            if lvl.price.load(Ordering::Acquire) == price {
                lvl.price.store(0, Ordering::Release);
                lvl.amount.store(0, Ordering::Release);
                lvl.count.store(0, Ordering::Release);
                break;
            }
        }
    }
    // Always sort after update: Bids DESC, Asks ASC (by price)
    // Note: This is not ideal for ultra-hot path, but for 25 levels it's okay.
    // In a real production HFT, we'd use a more efficient data structure.
}

fn sort_book(levels: &mut [beroun_types::OrderBookLevel], is_bid: bool) {
    levels.sort_by(|a, b| {
        let pa = a.price.load(Ordering::Acquire);
        let pb = b.price.load(Ordering::Acquire);
        
        // Push 0 price to the very end
        if pa == 0 && pb == 0 { return std::cmp::Ordering::Equal; }
        if pa == 0 { return std::cmp::Ordering::Greater; }
        if pb == 0 { return std::cmp::Ordering::Less; }
        
        if is_bid { pb.cmp(&pa) } else { pa.cmp(&pb) }
    });
}

fn format_bfx(val: f64) -> String {
    if val == val.trunc() {
        format!("{:.0}", val)
    } else {
        let s = format!("{:.12}", val);
        let s = s.trim_end_matches('0').trim_end_matches('.').to_string();
        s
    }
}

fn calculate_checksum(engine: &beroun_types::EngineState) -> i32 {
    let mut s = String::with_capacity(1024);
    let mut levels_found = 0;
    for i in 0..25 {
        let bid = &engine.bids[i];
        let ask = &engine.asks[i];
        
        let bp = bid.price.load(Ordering::Acquire);
        let ap = ask.price.load(Ordering::Acquire);

        if bid.count.load(Ordering::Acquire) > 0 && bp > 0 {
            levels_found += 1;
            let p = bp as f64 / beroun_types::PRICE_SCALE;
            let a = bid.amount.load(Ordering::Acquire) as f64 / beroun_types::PRICE_SCALE;
            let _ = write!(s, "{}:{}:", format_bfx(p), format_bfx(a));
        }
        if ask.count.load(Ordering::Acquire) > 0 && ap > 0 {
            levels_found += 1;
            let p = ap as f64 / beroun_types::PRICE_SCALE;
            let a = ask.amount.load(Ordering::Acquire) as f64 / beroun_types::PRICE_SCALE;
            let _ = write!(s, "{}:{}:", format_bfx(p), format_bfx(a));
        }
    }
    if levels_found == 0 {
        // info!(event = "checksum_debug", bids0_p = engine.bids[0].price.load(Ordering::Acquire), asks0_p = engine.asks[0].price.load(Ordering::Acquire));
    }
    if s.ends_with(':') { s.pop(); }
    let mut h = Hasher::new();
    h.update(s.as_bytes());
    h.finalize() as i32
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();
    tracing_subscriber::registry().with(fmt::layer().with_target(false).json()).with(EnvFilter::from_default_env().add_directive(Level::INFO.into())).init();

    let notifier = Arc::new(AsyncNotifier::new());
    let engine_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;
    let risk_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;
    
    let engine = unsafe { &mut *(engine_mmap.as_ptr() as *mut EngineState) };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const RiskState) };

    // Background Heartbeat Task
    let engine_heartbeat = engine_mmap.as_ptr() as usize;
    tokio::spawn(async move {
        let engine_ptr = unsafe { &*(engine_heartbeat as *const EngineState) };
        loop {
            if let Ok(now) = SystemTime::now().duration_since(UNIX_EPOCH) {
                engine_ptr.latency_ns.store(now.as_nanos() as u64, Ordering::Release);
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    });

    let key = std::env::var("BITFINEX_API_KEY").context("Missing API KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing API SECRET")?;

    info!(event = "system_start", version = "5.2.0-sovereign-hft");
    notifier.send("🐺 Beroun Sniper v5.2.0 Sovereign HFT ONLINE".to_string());

    loop {
        let ws_result = connect_async(BITFINEX_AUTH_URL).await;
        let (ws, _) = match ws_result {
            Ok(v) => v,
            Err(_) => { tokio::time::sleep(Duration::from_secs(10)).await; continue; }
        };

        let (mut write, mut read) = ws.split();
        
        // 0. Configuration: Enable OB_CHECKSUM (131072) and BULK_UPDATES (536870912)
        let conf_msg = json!({ "event": "conf", "flags": 131072 | 536870912 });
        write.send(Message::Text(conf_msg.to_string().into())).await?;
        info!(event = "bitfinex_conf_sent", flags = 131072 | 536870912);

        let nonce = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis().to_string();
        let auth_payload = format!("AUTH{}", nonce);
        let sig = get_sig(&sec, &auth_payload).await;

        let auth_msg = json!({ "event": "auth", "apiKey": key, "authSig": sig, "authPayload": auth_payload, "authNonce": nonce, "dms": 4 });
        write.send(Message::Text(auth_msg.to_string().into())).await?;
        
        // Subscribe to P0 Book (Top 25)
        write.send(Message::Text(json!({"event": "subscribe", "channel": "book", "symbol": "tBTCUSD", "prec": "P0", "freq": "F0", "len": "25"}).to_string().into())).await?;

        let mut chan_id: Option<i64> = None;
        let mut last_upd = Instant::now();
        let mut authed = false;

        while let Some(msg) = read.next().await {
            let msg = match msg { Ok(m) => m, Err(_) => break };
            
            if let Message::Text(text) = msg {
                let mut bytes = text.as_bytes().to_vec();
                let v = match simd_json::to_borrowed_value(&mut bytes) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                
                if let BorrowedValue::Array(arr) = v {
                    if arr[0].as_i64() == Some(0) {
                        let msg_type = arr[1].as_str().unwrap_or("");
                        
                        if msg_type == "te" {
                            if let Some(trade) = arr[2].as_array() {
                                if let (Some(amount), Some(price)) = (trade[4].as_f64(), trade[5].as_f64()) {
                                    engine.net_position.fetch_add((amount * beroun_types::PRICE_SCALE) as i64, Ordering::AcqRel);
                                }
                            }
                        }

                        if msg_type == "wu" || msg_type == "ws" {
                            let wallet_data: Vec<&BorrowedValue> = if msg_type == "wu" { 
                                vec![&arr[2]] 
                            } else { 
                                arr[2].as_array().map(|a: &Vec<BorrowedValue>| a.iter().collect()).unwrap_or_default()
                            };
                            for w in wallet_data {
                                if let (Some(w_type), Some(currency), Some(balance)) = (w[0].as_str(), w[1].as_str(), w[2].as_f64()) {
                                    if w_type == "exchange" {
                                        if currency == "BTC" {
                                            engine.wallet_btc.store((balance * beroun_types::PRICE_SCALE) as u64, Ordering::Release);
                                        } else if currency == "USD" || currency == "UST" {
                                            engine.wallet_usd.store((balance * beroun_types::PRICE_SCALE) as u64, Ordering::Release);
                                        }
                                    }
                                }
                            }
                        }
                    }

                    if arr[0].as_i64() == chan_id && chan_id.is_some() {
                        if arr[1].as_str() == Some("hb") { continue; }
                        
                        if arr[1].as_str() == Some("cs") {
                            let remote_cs = arr[2].as_i64().unwrap_or(0) as i32;
                            let local_cs = calculate_checksum(engine);
                            if remote_cs != local_cs {
                                info!(event = "checksum_mismatch", remote = remote_cs, local = local_cs);
                                // break; // Stay connected to debug
                            }
                            continue;
                        }

                        // Handle Book Update
                        if let Some(top_arr) = arr[1].as_array() {
                            // Snapshot or Bulk Update
                            for entry in top_arr {
                                if let Some(update) = entry.as_array() {
                                    if let (Some(price), Some(count), Some(amount)) = (update[0].as_f64(), update[1].as_i64(), update[2].as_f64()) {
                                        let p_u64 = (price * beroun_types::PRICE_SCALE) as u64;
                                        let a_i64 = (amount * beroun_types::PRICE_SCALE) as i64;
                                        let c_u64 = count as u64;
                                        if amount > 0.0 { update_book(&mut engine.bids, p_u64, a_i64, c_u64); }
                                        else { update_book(&mut engine.asks, p_u64, a_i64, c_u64); }
                                    }
                                }
                            }
                            sort_book(&mut engine.bids, true);
                            sort_book(&mut engine.asks, false);
                        } else if let (Some(price), Some(count), Some(amount)) = (arr[1].as_f64(), arr[2].as_i64(), arr[3].as_f64()) {
                            // Single Update
                            let p_u64 = (price * beroun_types::PRICE_SCALE) as u64;
                            let a_i64 = (amount * beroun_types::PRICE_SCALE) as i64;
                            let c_u64 = count as u64;
                            if amount > 0.0 {
                                update_book(&mut engine.bids, p_u64, a_i64, c_u64);
                                sort_book(&mut engine.bids, true);
                            } else {
                                update_book(&mut engine.asks, p_u64, a_i64, c_u64);
                                sort_book(&mut engine.asks, false);
                            }
                        }

                        // Update Best Bid/Ask in EngineState
                        let best_bid = engine.bids[0].price.load(Ordering::Acquire);
                        let best_ask = engine.asks[0].price.load(Ordering::Acquire);
                        engine.best_bid.store(best_bid, Ordering::Release);
                        engine.best_ask.store(best_ask, Ordering::Release);

                        // SNIPER LOGIC
                        if authed && best_bid > 0 && best_ask > 0 {
                            let now = Instant::now();
                            if now.duration_since(last_upd).as_millis() > 200 {
                                let is_paused = risk.paused.load(Ordering::Acquire) != 0;
                                if !is_paused {
                                    let mid_price = (best_bid + best_ask) / 2;
                                    let mid_f = mid_price as f64 / beroun_types::PRICE_SCALE;
                                    
                                    let order_usd = risk.order_usd.load(Ordering::Acquire) as i64;
                                    let grid_step = risk.grid_step.load(Ordering::Acquire) as i64;
                                    let bias = risk.bias_offset.load(Ordering::Acquire);

                                    let buy_p = (mid_price - (grid_step as u64) + (bias as u64)) as f64 / beroun_types::PRICE_SCALE;
                                    let sell_p = (mid_price + (grid_step as u64) + (bias as u64)) as f64 / beroun_types::PRICE_SCALE;
                                    let btc_amount = (order_usd as f64 / mid_f / beroun_types::PRICE_SCALE).max(0.00015);

                                    let order_msg = json!([0, "ox_multi", null, [
                                        ["oc_multi", { "all": 1 }],
                                        ["on", { "symbol": "tBTCUSD", "amount": format!("{:.5}", btc_amount), "price": format!("{:.2}", buy_p), "type": "EXCHANGE LIMIT", "flags": 4096 }],
                                        ["on", { "symbol": "tBTCUSD", "amount": format!("{:.5}", -btc_amount), "price": format!("{:.2}", sell_p), "type": "EXCHANGE LIMIT", "flags": 4096 }]
                                    ]]);
                                    
                                    let _ = write.send(Message::Text(order_msg.to_string().into())).await;
                                    last_upd = now;
                                    info!(event = "sniper_fire", bid = best_bid, ask = best_ask, buy = buy_p, sell = sell_p);
                                }
                            }
                        }
                    }
                } else if let BorrowedValue::Object(obj) = v {
                    if obj.get("event") == Some(&BorrowedValue::from("auth")) && obj.get("status") == Some(&BorrowedValue::from("OK")) { 
                        authed = true; 
                        info!(event = "bitfinex_auth_ok");
                    }
                    if obj.get("event") == Some(&BorrowedValue::from("subscribed")) { 
                        chan_id = obj.get("chanId").and_then(|id: &BorrowedValue| id.as_i64()); 
                        info!(event = "bitfinex_subscribed", chan_id = ?chan_id);
                    }
                    if obj.get("event") == Some(&BorrowedValue::from("error")) {
                        info!(event = "bitfinex_error", msg = ?obj.get("msg"), code = ?obj.get("code"));
                    }
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
