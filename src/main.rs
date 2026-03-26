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
use std::sync::atomic::fence;
use simd_json::prelude::*;
use simd_json::BorrowedValue;
use crc32fast::Hasher;

/// Safe extraction of i64 from simd_json BorrowedValue.
/// Bitfinex may send COUNT as integer (5) or float (5.0).
/// simd_json's as_i64() returns None for floats, so we fallback.
#[inline(always)]
fn safe_as_i64(v: &BorrowedValue) -> Option<i64> {
    v.as_i64().or_else(|| v.as_f64().map(|f| f as i64))
}

/// Safe extraction of f64 from simd_json BorrowedValue.
/// CRITICAL: simd_json's as_f64() returns None for INTEGER values!
/// Bitfinex sends prices like 69516 (no decimal) which simd_json parses
/// as integer, making as_f64() return None. We must try as_i64/as_u64 first.
#[inline(always)]
fn safe_as_f64(v: &BorrowedValue) -> Option<f64> {
    v.as_f64()
        .or_else(|| v.as_i64().map(|i| i as f64))
        .or_else(|| v.as_u64().map(|u| u as f64))
}

/// CRITICAL: All book functions take raw pointers to avoid &mut aliasing.
/// The Rust optimizer with LTO=fat and opt-level=3 uses &mut exclusivity
/// to eliminate stores it considers "dead" — which breaks mmap-backed atomics.
///
/// Using *mut pointer + unsafe { &*ptr } for each access prevents this.
fn update_book(levels: *mut [beroun_types::OrderBookLevel; beroun_types::BOOK_LEVELS], price: u64, amount: i64, count: u64) {
    let levels = unsafe { &*levels };
    if count > 0 {
        // Update or Insert
        let mut found = false;
        for lvl in levels.iter() {
            if lvl.price.load(Ordering::SeqCst) == price {
                lvl.amount.store(amount, Ordering::SeqCst);
                lvl.count.store(count, Ordering::SeqCst);
                found = true;
                break;
            }
        }
        if !found {
            for lvl in levels.iter() {
                if lvl.count.load(Ordering::SeqCst) == 0 {
                    lvl.price.store(price, Ordering::SeqCst);
                    lvl.amount.store(amount, Ordering::SeqCst);
                    lvl.count.store(count, Ordering::SeqCst);
                    break;
                }
            }
        }
    } else {
        // Delete
        for lvl in levels.iter() {
            if lvl.price.load(Ordering::SeqCst) == price {
                lvl.price.store(0, Ordering::SeqCst);
                lvl.amount.store(0, Ordering::SeqCst);
                lvl.count.store(0, Ordering::SeqCst);
                break;
            }
        }
    }
}

fn sort_book(levels: *mut [beroun_types::OrderBookLevel; beroun_types::BOOK_LEVELS], is_bid: bool) {
    let levels = unsafe { &mut *levels };
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

fn calculate_checksum(engine: &beroun_types::EngineState, debug: bool) -> i32 {
    // Bitfinex P0 checksum: interleave bid[i] and ask[i] for i=0..25
    // Format: "bid0_price:bid0_amount:ask0_price:ask0_amount:bid1_price:..."
    // Bids sorted DESC by price, asks sorted ASC by price
    // Amounts: positive for bids, negative for asks (as stored)
    fence(Ordering::SeqCst); // Ensure all prior writes are visible
    let mut s = String::with_capacity(1024);
    let mut levels_found = 0;
    for i in 0..25 {
        let bid = &engine.bids[i];
        let ask = &engine.asks[i];
        
        let bp = bid.price.load(Ordering::SeqCst);
        let bc = bid.count.load(Ordering::SeqCst);
        let ap = ask.price.load(Ordering::SeqCst);
        let ac = ask.count.load(Ordering::SeqCst);

        if bc > 0 && bp > 0 {
            levels_found += 1;
            let p = bp as f64 / beroun_types::PRICE_SCALE;
            let a = bid.amount.load(Ordering::SeqCst) as f64 / beroun_types::PRICE_SCALE;
            if !s.is_empty() { s.push(':'); }
            let _ = write!(s, "{}:{}", format_bfx(p), format_bfx(a));
        }
        if ac > 0 && ap > 0 {
            levels_found += 1;
            let p = ap as f64 / beroun_types::PRICE_SCALE;
            let a = ask.amount.load(Ordering::SeqCst) as f64 / beroun_types::PRICE_SCALE;
            if !s.is_empty() { s.push(':'); }
            let _ = write!(s, "{}:{}", format_bfx(p), format_bfx(a));
        }
    }
    if debug || levels_found == 0 {
        let preview = if s.len() > 200 { &s[..200] } else { &s };
        info!(event = "checksum_debug", levels = levels_found,
              bids0_p = engine.bids[0].price.load(Ordering::SeqCst),
              bids0_c = engine.bids[0].count.load(Ordering::SeqCst),
              asks0_p = engine.asks[0].price.load(Ordering::SeqCst),
              asks0_c = engine.asks[0].count.load(Ordering::SeqCst),
              cs_str_preview = %preview);
    }
    let mut h = Hasher::new();
    h.update(s.as_bytes());
    h.finalize() as i32
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();
    tracing_subscriber::registry().with(fmt::layer().with_target(false).json()).with(EnvFilter::from_default_env().add_directive(Level::INFO.into())).init();

    let notifier = Arc::new(AsyncNotifier::new());
    let mut engine_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;
    let risk_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;
    
    // CRITICAL: Use raw pointer, NOT &mut reference!
    // &mut EngineState tells the optimizer it has exclusive access,
    // allowing it to eliminate atomic stores as "dead" writes.
    // Raw *mut preserves all stores through mmap.
    let engine_ptr: *mut EngineState = engine_mmap.as_mut_ptr() as *mut EngineState;
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const RiskState) };

    // Background Heartbeat Task
    let engine_heartbeat = engine_ptr as usize;
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
        let mut snapshot_loaded = false;
        let mut cs_debug_count: u32 = 0;

        while let Some(msg) = read.next().await {
            let msg = match msg { Ok(m) => m, Err(_) => break };
            
            if let Message::Text(text) = msg {
                let mut bytes = text.as_bytes().to_vec();
                let v = match simd_json::to_borrowed_value(&mut bytes) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                
                if let BorrowedValue::Array(arr) = v {
                    // Access engine via raw pointer for each operation
                    let engine = unsafe { &*engine_ptr };
                    
                    if arr[0].as_i64() == Some(0) {
                        let msg_type = arr[1].as_str().unwrap_or("");
                        
                        if msg_type == "te" {
                            if let Some(trade) = arr[2].as_array() {
                                if let (Some(amount), Some(_price)) = (safe_as_f64(&trade[4]), safe_as_f64(&trade[5])) {
                                    engine.net_position.fetch_add((amount * beroun_types::PRICE_SCALE) as i64, Ordering::SeqCst);
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
                                if let (Some(w_type), Some(currency), Some(balance)) = (w[0].as_str(), w[1].as_str(), safe_as_f64(&w[2])) {
                                    if w_type == "exchange" {
                                        if currency == "BTC" {
                                            engine.wallet_btc.store((balance * beroun_types::PRICE_SCALE) as u64, Ordering::SeqCst);
                                        } else if currency == "USD" || currency == "UST" {
                                            engine.wallet_usd.store((balance * beroun_types::PRICE_SCALE) as u64, Ordering::SeqCst);
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
                            let do_debug = cs_debug_count < 5;
                            let local_cs = calculate_checksum(unsafe { &*engine_ptr }, do_debug);
                            cs_debug_count += 1;
                            if remote_cs != local_cs {
                                info!(event = "checksum_mismatch", remote = remote_cs, local = local_cs);
                                // Reconnect on persistent mismatch (after initial debug)
                                if cs_debug_count > 10 { break; }
                            } else {
                                info!(event = "checksum_ok", cs = remote_cs);
                            }
                            continue;
                        }

                        // Handle Book Update (snapshot or bulk update: [[P,C,A],...] )
                        if let Some(top_arr) = arr[1].as_array() {
                            // Check if first element is an array → [[P,C,A],...] format
                            // vs [P,C,A] format (single update without BULK_UPDATES)
                            let is_nested = top_arr.first().map_or(false, |e| e.as_array().is_some());
                            
                            if is_nested {
                                // Snapshot or bulk update: [[P,C,A],[P,C,A],...]
                                let mut bid_count = 0u32;
                                let mut ask_count = 0u32;
                                for entry in top_arr {
                                    if let Some(update) = entry.as_array() {
                                        if let (Some(price), Some(count), Some(amount)) = (safe_as_f64(&update[0]), safe_as_i64(&update[1]), safe_as_f64(&update[2])) {
                                            let p_u64 = (price * beroun_types::PRICE_SCALE).round() as u64;
                                            let a_i64 = (amount * beroun_types::PRICE_SCALE).round() as i64;
                                            let c_u64 = count as u64;
                                            if amount > 0.0 {
                                                update_book(unsafe { &raw mut (*engine_ptr).bids }, p_u64, a_i64, c_u64);
                                                bid_count += 1;
                                            } else {
                                                update_book(unsafe { &raw mut (*engine_ptr).asks }, p_u64, a_i64, c_u64);
                                                ask_count += 1;
                                            }
                                        }
                                    }
                                }
                                fence(Ordering::SeqCst);
                                sort_book(unsafe { &raw mut (*engine_ptr).bids }, true);
                                sort_book(unsafe { &raw mut (*engine_ptr).asks }, false);

                                if !snapshot_loaded && (bid_count + ask_count) > 10 {
                                    snapshot_loaded = true;
                                    let eng = unsafe { &*engine_ptr };
                                    info!(event = "snapshot_loaded", bids = bid_count, asks = ask_count,
                                          best_bid_p = eng.bids[0].price.load(Ordering::SeqCst),
                                          best_ask_p = eng.asks[0].price.load(Ordering::SeqCst),
                                          best_bid_a = eng.bids[0].amount.load(Ordering::SeqCst),
                                          best_ask_a = eng.asks[0].amount.load(Ordering::SeqCst),
                                          best_bid_c = eng.bids[0].count.load(Ordering::SeqCst),
                                          best_ask_c = eng.asks[0].count.load(Ordering::SeqCst));
                                }
                            } else {
                                // Single update without BULK_UPDATES: [P,C,A]
                                if let (Some(price), Some(count), Some(amount)) = (safe_as_f64(&top_arr[0]), safe_as_i64(&top_arr[1]), safe_as_f64(&top_arr[2])) {
                                    let p_u64 = (price * beroun_types::PRICE_SCALE).round() as u64;
                                    let a_i64 = (amount * beroun_types::PRICE_SCALE).round() as i64;
                                    let c_u64 = count as u64;
                                    if amount > 0.0 {
                                        update_book(unsafe { &raw mut (*engine_ptr).bids }, p_u64, a_i64, c_u64);
                                        sort_book(unsafe { &raw mut (*engine_ptr).bids }, true);
                                    } else {
                                        update_book(unsafe { &raw mut (*engine_ptr).asks }, p_u64, a_i64, c_u64);
                                        sort_book(unsafe { &raw mut (*engine_ptr).asks }, false);
                                    }
                                }
                            }
                        }

                        // Update Best Bid/Ask in EngineState
                        let eng = unsafe { &*engine_ptr };
                        let best_bid = eng.bids[0].price.load(Ordering::SeqCst);
                        let best_ask = eng.asks[0].price.load(Ordering::SeqCst);
                        eng.best_bid.store(best_bid, Ordering::SeqCst);
                        eng.best_ask.store(best_ask, Ordering::SeqCst);

                        // SNIPER LOGIC
                        if authed && best_bid > 0 && best_ask > 0 {
                            let now = Instant::now();
                            if now.duration_since(last_upd).as_millis() > 200 {
                                let is_paused = risk.paused.load(Ordering::Acquire) != 0;
                                if !is_paused {
                                    let mid_price_i = ((best_bid as i64) + (best_ask as i64)) / 2;
                                    let mid_f = mid_price_i as f64 / beroun_types::PRICE_SCALE;
                                    
                                    let order_usd = risk.order_usd.load(Ordering::Acquire) as i64;
                                    let grid_step = risk.grid_step.load(Ordering::Acquire) as i64;
                                    let bias = risk.bias_offset.load(Ordering::Acquire);

                                    let buy_p = (mid_price_i - grid_step + bias).max(0) as f64 / beroun_types::PRICE_SCALE;
                                    let sell_p = (mid_price_i + grid_step + bias).max(0) as f64 / beroun_types::PRICE_SCALE;
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
