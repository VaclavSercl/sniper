use std::time::{SystemTime, UNIX_EPOCH, Instant, Duration};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tokio::sync::mpsc;
use futures_util::{StreamExt, SinkExt};
use serde_json::json;
use tokio_tungstenite::tungstenite::protocol::Message;
use hmac::{Hmac, Mac};
use sha2::Sha384;
use dotenvy::dotenv;
use tracing::{info, Level};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};
use memmap2::MmapMut;
use anyhow::{Context, Result};
use std::collections::VecDeque;

use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH};

type HmacSha384 = Hmac<Sha384>;

// Background Notifier Task — BotEvent-based aggregation
pub enum BotEvent {
    Alert(String),
    Trade { amount: f64, price: f64 },
}

struct AsyncNotifier {
    tx: mpsc::UnboundedSender<BotEvent>,
}

impl AsyncNotifier {
    fn new() -> Self {
        let (tx, mut rx) = mpsc::unbounded_channel::<BotEvent>();
        let token = std::env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default();
        let chat_id = std::env::var("TELEGRAM_CHAT_ID").unwrap_or_default();
        let client = reqwest::Client::builder().timeout(Duration::from_secs(10)).build().unwrap();
        let alerts_log = "/home/wwwenda/hft-sniper/logs/alerts.log".to_string();

        tokio::spawn(async move {
            let mut report_interval = tokio::time::interval(Duration::from_secs(3600));
            let mut buys: u32 = 0;
            let mut sells: u32 = 0;
            let mut volume: f64 = 0.0;
            let mut last_price: f64 = 0.0;

            loop {
                tokio::select! {
                    _ = report_interval.tick() => {
                        if buys + sells > 0 {
                            let msg = format!(
                                "📊 *Hodinový Report*\n📈 Obchodů: `{}` ({} nákup / {} prodej)\n💰 Objem: `{:.5}` BTC\n💲 Posl. cena: `${:.2}`",
                                buys + sells, buys, sells, volume, last_price
                            );
                            if !token.is_empty() && !chat_id.is_empty() {
                                let url = format!("https://api.telegram.org/bot{}/sendMessage", token);
                                let _ = client.post(&url).json(&json!({"chat_id": &chat_id, "text": format!("🐺 {}", msg), "parse_mode": "Markdown"})).send().await;
                            }
                            buys = 0; sells = 0; volume = 0.0;
                        }
                        // v7.1: Hourly state.json snapshot for Oracle
                        tokio::task::spawn_blocking(|| {
                            let _ = std::process::Command::new("/home/wwwenda/hft-sniper/target/release/beroun-config")
                                .arg("export-json")
                                .stdout(std::fs::File::create("/home/wwwenda/hft-sniper/runtime/state.json").unwrap_or_else(|_| std::fs::File::create("/dev/null").unwrap()))
                                .spawn();
                        });
                    }
                    Some(event) = rx.recv() => {
                        match event {
                            BotEvent::Alert(msg) => {
                                info!(event = "async_alert", message = %msg);
                                let log_path = alerts_log.clone();
                                let log_msg = msg.clone();
                                tokio::task::spawn_blocking(move || {
                                    use std::fs::OpenOptions;
                                    use std::io::Write;
                                    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&log_path) {
                                        let _ = writeln!(f, "[{:?}] {}", SystemTime::now(), log_msg);
                                    }
                                });
                                if !token.is_empty() && !chat_id.is_empty() {
                                    let url = format!("https://api.telegram.org/bot{}/sendMessage", token);
                                    let _ = client.post(&url).json(&json!({"chat_id": &chat_id, "text": format!("🐺 {}", msg), "parse_mode": "Markdown"})).send().await;
                                }
                            },
                            BotEvent::Trade { amount, price } => {
                                if amount > 0.0 { buys += 1; } else { sells += 1; }
                                volume += amount.abs();
                                last_price = price;
                                info!(event = "trade_aggregated", amount = amount, price = price, buys = buys, sells = sells);
                            }
                        }
                    }
                }
            }
        });
        Self { tx }
    }

    fn alert(&self, msg: String) { let _ = self.tx.send(BotEvent::Alert(msg)); }
    fn trade(&self, amount: f64, price: f64) { let _ = self.tx.send(BotEvent::Trade { amount, price }); }
}

fn init_mmap_ptr<T: Default>(path: &str) -> Result<MmapMut> {
    let file = std::fs::OpenOptions::new().read(true).write(true).create(true).open(path)?;
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

use std::sync::atomic::fence;
use simd_json::prelude::*;
use simd_json::BorrowedValue;
use crc32fast::Hasher;

#[inline(always)]
fn safe_as_i64(v: &BorrowedValue) -> Option<i64> {
    v.as_i64().or_else(|| v.as_f64().map(|f| f as i64))
}

#[inline(always)]
fn safe_as_f64(v: &BorrowedValue) -> Option<f64> {
    v.as_f64()
        .or_else(|| v.as_i64().map(|i| i as f64))
        .or_else(|| v.as_u64().map(|u| u as f64))
}

fn update_book(levels: *mut [beroun_types::OrderBookLevel; beroun_types::BOOK_LEVELS], price: u64, amount: i64, count: u64) {
    let levels = unsafe { &*levels };
    if count > 0 {
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
    levels.sort_unstable_by(|a, b| {
        let pa = a.price.load(Ordering::Acquire);
        let pb = b.price.load(Ordering::Acquire);
        if pa == 0 && pb == 0 { return std::cmp::Ordering::Equal; }
        if pa == 0 { return std::cmp::Ordering::Greater; }
        if pb == 0 { return std::cmp::Ordering::Less; }
        if is_bid { pb.cmp(&pa) } else { pa.cmp(&pb) }
    });
}

#[inline]
fn write_bfx(w: &mut impl std::fmt::Write, val: f64) -> std::fmt::Result {
    if val == val.trunc() {
        write!(w, "{:.0}", val)
    } else {
        let mut buf = [0u8; 32];
        let n = {
            use std::io::Write;
            let mut cursor = std::io::Cursor::new(&mut buf[..]);
            write!(cursor, "{:.12}", val).unwrap();
            cursor.position() as usize
        };
        let s = unsafe { std::str::from_utf8_unchecked(&buf[..n]) };
        let trimmed = s.trim_end_matches('0').trim_end_matches('.');
        w.write_str(trimmed)
    }
}

fn calculate_checksum(engine: &beroun_types::EngineState, debug: bool) -> i32 {
    fence(Ordering::SeqCst);
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
            let _ = write_bfx(&mut s, p);
            s.push(':');
            let _ = write_bfx(&mut s, a);
        }
        if ac > 0 && ap > 0 {
            levels_found += 1;
            let p = ap as f64 / beroun_types::PRICE_SCALE;
            let a = ask.amount.load(Ordering::SeqCst) as f64 / beroun_types::PRICE_SCALE;
            if !s.is_empty() { s.push(':'); }
            let _ = write_bfx(&mut s, p);
            s.push(':');
            let _ = write_bfx(&mut s, a);
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

fn main() -> Result<()> {
    dotenv().ok();
    tracing_subscriber::registry().with(fmt::layer().with_target(false).json()).with(EnvFilter::from_default_env().add_directive(Level::INFO.into())).init();

    if let Some(core_ids) = core_affinity::get_core_ids() {
        if core_ids.len() > 1 {
            core_affinity::set_for_current(core_ids[1]);
            info!(event = "cpu_pinned", core = 1, total_cores = core_ids.len());
        }
    }

    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .context("Failed to build Tokio runtime")?;

    rt.block_on(async_main())
}

// ═══════════════════════════════════════════════════════════════════════
// ASYNC MAIN — Dual-WS architecture with Watchdog + Graceful Shutdown
// ═══════════════════════════════════════════════════════════════════════

async fn async_main() -> Result<()> {
    let notifier = Arc::new(AsyncNotifier::new());
    let mut engine_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;
    let risk_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;

    let engine_ptr: *mut EngineState = engine_mmap.as_mut_ptr() as *mut EngineState;
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const RiskState) };

    // Background Heartbeat (latency_ns in its own cache line)
    let engine_hb = engine_ptr as usize;
    tokio::spawn(async move {
        loop {
            if let Ok(now) = SystemTime::now().duration_since(UNIX_EPOCH) {
                unsafe { &*(engine_hb as *const EngineState) }
                    .latency_ns.store(now.as_nanos() as u64, Ordering::Release);
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    });

    // ═══ VOLATILITY ENGINE (Dynamic Grid Step) ═══
    let vol_engine_ptr = engine_ptr as usize;
    let vol_risk_ptr = risk as *const RiskState as usize;
    tokio::spawn(async move {
        let mut price_history: VecDeque<i64> = VecDeque::with_capacity(61);
        let base_grid: i64 = 200_000_000;    // $2 base spread
        let max_grid: i64 = 5_000_000_000;   // $50 max spread
        let vol_mult: f64 = 0.15;            // 15% of price range
        let default_grid: u64 = 300_000_000; // $3 fallback
        loop {
            tokio::time::sleep(Duration::from_millis(500)).await;
            let engine = unsafe { &*(vol_engine_ptr as *const EngineState) };
            let risk = unsafe { &*(vol_risk_ptr as *const RiskState) };
            if risk.paused.load(Ordering::Acquire) != 0 { continue; }
            let bb = engine.best_bid.load(Ordering::Acquire);
            let ba = engine.best_ask.load(Ordering::Acquire);
            if bb > 0 && ba > 0 {
                let mid = ((bb as i64) + (ba as i64)) / 2;
                price_history.push_back(mid);
                if price_history.len() > 60 { price_history.pop_front(); }
                if price_history.len() >= 10 {
                    let min_p = *price_history.iter().min().unwrap();
                    let max_p = *price_history.iter().max().unwrap();
                    let range = max_p - min_p;
                    let dynamic = (range as f64 * vol_mult) as i64;
                    let new_grid = (base_grid + dynamic).min(max_grid) as u64;
                    risk.grid_step.store(new_grid, Ordering::Release);
                    if range > 5_000_000_000 {
                        tracing::info!(event = "volatility_spike",
                            range_usd = range / beroun_types::PRICE_SCALE_I,
                            new_grid_usd = new_grid as i64 / beroun_types::PRICE_SCALE_I);
                    }
                } else {
                    risk.grid_step.store(default_grid, Ordering::Release);
                }
            }
        }
    });

    let key = std::env::var("BITFINEX_API_KEY").context("Missing API KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing API SECRET")?;

    info!(event = "system_start", version = "6.0.0-sovereign");
    notifier.alert("*Beroun Sniper v6.0 ONLINE*\n`Dual-WS + Watchdog + Vol Engine + Trade Aggregation`".to_string());

    // SIGTERM listener (systemd, Docker)
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .context("Failed to setup SIGTERM")?;

    // ═══ OUTER RECONNECT LOOP ═══
    loop {
        info!(event = "connecting_dual_ws");

        let ws_mdata = match connect_ws().await {
            Ok(v) => v,
            Err(e) => { info!(event = "mdata_connect_failed", error = %e); tokio::time::sleep(Duration::from_secs(5)).await; continue; }
        };
        info!(event = "mdata_ws_connected");

        let ws_exec = match connect_ws().await {
            Ok(v) => v,
            Err(e) => { info!(event = "exec_connect_failed", error = %e); tokio::time::sleep(Duration::from_secs(5)).await; continue; }
        };
        info!(event = "exec_ws_connected");

        let (mut mdata_write, mut mdata_read) = ws_mdata.split();
        let (exec_write, exec_read) = ws_exec.split();

        // Configure Market Data WS (public, no auth)
        let _ = mdata_write.send(Message::Text(json!({"event":"conf","flags":131072|536870912}).to_string().into())).await;
        let _ = mdata_write.send(Message::Text(json!({"event":"subscribe","channel":"book","symbol":"tBTCUSD","prec":"P0","freq":"F0","len":"25"}).to_string().into())).await;

        // Order channel: HFT loop → exec writer
        let (order_tx, mut order_rx) = tokio::sync::mpsc::unbounded_channel::<String>();
        // Error channel: any task → main loop (watchdog kill switch)
        let (err_tx, mut err_rx) = tokio::sync::mpsc::unbounded_channel::<&'static str>();

        // ── TASK 1: EXEC WRITER ──
        let mut exec_write = exec_write;
        let err_tx_w = err_tx.clone();
        let key_c = key.clone();
        let sec_c = sec.clone();
        let writer_handle = tokio::spawn(async move {
            let nonce = match SystemTime::now().duration_since(UNIX_EPOCH) {
                Ok(d) => d.as_millis().to_string(),
                Err(_) => { let _ = err_tx_w.send("writer_time_error"); return; }
            };
            let auth_payload = format!("AUTH{}", nonce);
            let sig = get_sig(&sec_c, &auth_payload).await;
            let _ = exec_write.send(Message::Text(json!({"event":"conf","flags":131072}).to_string().into())).await;
            let auth_msg = json!({"event":"auth","apiKey":key_c,"authSig":sig,"authPayload":auth_payload,"authNonce":nonce,"dms":4});
            if exec_write.send(Message::Text(auth_msg.to_string().into())).await.is_err() {
                let _ = err_tx_w.send("writer_auth_failed");
                return;
            }
            while let Some(order_msg) = order_rx.recv().await {
                if exec_write.send(Message::Text(order_msg.into())).await.is_err() {
                    let _ = err_tx_w.send("writer_socket_error");
                    break;
                }
            }
        });

        // ── TASK 2: EXEC READER (te, wu, n + 15s watchdog) ──
        let mut exec_read = exec_read;
        let err_tx_r = err_tx.clone();
        let exec_engine = engine_ptr as usize;
        let exec_notifier = notifier.clone();
        let reader_handle = tokio::spawn(async move {
            loop {
                match tokio::time::timeout(Duration::from_secs(15), exec_read.next()).await {
                    Ok(Some(Ok(msg))) if msg.is_text() => {
                        let mut bytes = msg.into_data().to_vec();
                        let v = match simd_json::to_borrowed_value(&mut bytes) {
                            Ok(v) => v,
                            Err(_) => continue,
                        };
                        if let BorrowedValue::Array(arr) = v {
                            let engine = unsafe { &*(exec_engine as *const EngineState) };
                            if arr[0].as_i64() == Some(0) {
                                let mt = arr[1].as_str().unwrap_or("");
                                if mt == "te" {
                                    if let Some(trade) = arr[2].as_array() {
                                        if let (Some(trade_amt), Some(trade_price)) = (safe_as_f64(&trade[4]), safe_as_f64(&trade[5])) {
                                            let scale = beroun_types::PRICE_SCALE;
                                            let old_pos_i = engine.net_position.load(Ordering::SeqCst);
                                            let old_pos = old_pos_i as f64 / scale;
                                            let old_aep_i = engine.average_entry_price.load(Ordering::SeqCst);
                                            let old_aep = old_aep_i as f64 / scale;

                                            // 1. REALIZED PnL (trade reduces/closes position)
                                            if (old_pos > 0.0 && trade_amt < 0.0) || (old_pos < 0.0 && trade_amt > 0.0) {
                                                let closed_amt = trade_amt.abs().min(old_pos.abs());
                                                let pnl_gain = if old_pos > 0.0 {
                                                    (trade_price - old_aep) * closed_amt
                                                } else {
                                                    (old_aep - trade_price) * closed_amt
                                                };
                                                engine.realized_pnl.fetch_add((pnl_gain * scale).round() as i64, Ordering::SeqCst);
                                                info!(event = "pnl_realized", gain = pnl_gain, closed = closed_amt, aep = old_aep);
                                            }

                                            // 2. AVERAGE ENTRY PRICE (WAP)
                                            let new_pos = old_pos + trade_amt;
                                            if new_pos.abs() > 1e-8 {
                                                if (old_pos >= 0.0 && trade_amt > 0.0) || (old_pos <= 0.0 && trade_amt < 0.0) {
                                                    // Enlarging position → weighted average
                                                    let new_aep = (old_pos.abs() * old_aep + trade_amt.abs() * trade_price) / new_pos.abs();
                                                    engine.average_entry_price.store((new_aep * scale).round() as i64, Ordering::SeqCst);
                                                } else if (old_pos > 0.0 && new_pos < 0.0) || (old_pos < 0.0 && new_pos > 0.0) {
                                                    // Position flipped → AEP = trade price
                                                    engine.average_entry_price.store((trade_price * scale).round() as i64, Ordering::SeqCst);
                                                }
                                                // Partial close: AEP stays the same (no update needed)
                                            } else {
                                                // Position == 0 → reset AEP
                                                engine.average_entry_price.store(0, Ordering::SeqCst);
                                            }

                                            // 3. Update net_position
                                            engine.net_position.store((new_pos * scale).round() as i64, Ordering::SeqCst);
                                            info!(event = "trade_executed", amount = trade_amt, price = trade_price,
                                                  new_pos = new_pos, aep = engine.average_entry_price.load(Ordering::SeqCst) as f64 / scale);
                                            exec_notifier.trade(trade_amt, trade_price);
                                        }
                                    }
                                }
                                if mt == "wu" || mt == "ws" {
                                    let wd: Vec<&BorrowedValue> = if mt == "wu" { vec![&arr[2]] }
                                    else { arr[2].as_array().map(|a: &Vec<BorrowedValue>| a.iter().collect()).unwrap_or_default() };
                                    for w in wd {
                                        if let (Some(wt), Some(cur), Some(bal)) = (w[0].as_str(), w[1].as_str(), safe_as_f64(&w[2])) {
                                            if wt == "exchange" {
                                                if cur == "BTC" { engine.wallet_btc.store((bal * beroun_types::PRICE_SCALE) as u64, Ordering::SeqCst); }
                                                else if cur == "USD" || cur == "UST" { engine.wallet_usd.store((bal * beroun_types::PRICE_SCALE) as u64, Ordering::SeqCst); }
                                            }
                                        }
                                    }
                                }
                                if mt == "n" {
                                    if let Some(n) = arr[2].as_array() {
                                        info!(event = "bitfinex_notification",
                                              ntype = n[1].as_str().unwrap_or("?"),
                                              status = n[6].as_str().unwrap_or("?"),
                                              text = n[7].as_str().unwrap_or(""));
                                    }
                                }
                                // --- ORDER SNAPSHOT (state recovery after connect) ---
                                if mt == "os" {
                                    if let Some(orders) = arr[2].as_array() {
                                        for order in orders {
                                            if let Some(o) = order.as_array() {
                                                if let (Some(id), Some(sym), Some(amt), Some(status)) = (
                                                    o[0].as_u64().or_else(|| o[0].as_f64().map(|f| f as u64)),
                                                    o[3].as_str(), safe_as_f64(&o[6]), o[13].as_str()
                                                ) {
                                                    if sym == "tBTCUSD" && (status.contains("ACTIVE") || status.contains("PARTIALLY")) {
                                                        if amt > 0.0 { engine.active_buy_id.store(id, Ordering::SeqCst); }
                                                        else { engine.active_sell_id.store(id, Ordering::SeqCst); }
                                                        info!(event = "order_recovered", id = id, side = if amt > 0.0 { "buy" } else { "sell" });
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                // --- ORDER LIFECYCLE (on=new, ou=update, oc=cancel) ---
                                if mt == "on" || mt == "ou" || mt == "oc" {
                                    if let Some(o) = arr[2].as_array() {
                                        if let (Some(id), Some(sym), Some(amt), Some(status)) = (
                                            o[0].as_u64().or_else(|| o[0].as_f64().map(|f| f as u64)),
                                            o[3].as_str(), safe_as_f64(&o[6]), o[13].as_str()
                                        ) {
                                            if sym == "tBTCUSD" {
                                                if status.contains("ACTIVE") || status.contains("PARTIALLY") {
                                                    if amt > 0.0 { engine.active_buy_id.store(id, Ordering::SeqCst); }
                                                    else { engine.active_sell_id.store(id, Ordering::SeqCst); }
                                                    info!(event = "order_tracked", mt = mt, id = id, side = if amt > 0.0 { "buy" } else { "sell" });
                                                } else if status.contains("CANCELED") || status.contains("EXECUTED") {
                                                    if amt > 0.0 {
                                                        let _ = engine.active_buy_id.compare_exchange(id, 0, Ordering::SeqCst, Ordering::SeqCst);
                                                    } else {
                                                        let _ = engine.active_sell_id.compare_exchange(id, 0, Ordering::SeqCst, Ordering::SeqCst);
                                                    }
                                                    info!(event = "order_cleared", mt = mt, id = id, status = status);
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        } else if let BorrowedValue::Object(obj) = v {
                            if obj.get("event") == Some(&BorrowedValue::from("auth")) && obj.get("status") == Some(&BorrowedValue::from("OK")) {
                                info!(event = "exec_auth_ok");
                            }
                        }
                    }
                    Ok(Some(Ok(_))) => {} // Ping/Pong
                    Ok(Some(Err(_))) | Ok(None) => { let _ = err_tx_r.send("exec_socket_closed"); break; }
                    Err(_) => { let _ = err_tx_r.send("exec_socket_timeout"); break; }
                }
            }
        });

        // ── TASK 3: MARKET DATA HFT LOOP (inline, highest priority) ──
        let mut chan_id: Option<i64> = None;
        let mut last_upd = Instant::now();
        let mut snapshot_loaded = false;
        let mut cs_debug_count: u32 = 0;
        let mut should_reconnect = false;
        let mut shutdown_reason: &str = "unknown";

        loop {
            if should_reconnect { break; }
            tokio::select! {
                biased;
                // ── GRACEFUL SHUTDOWN ──
                _ = tokio::signal::ctrl_c() => {
                    info!(event = "shutdown_initiated", signal = "SIGINT");
                    graceful_shutdown(&order_tx, &notifier, engine_ptr).await;
                    writer_handle.abort();
                    reader_handle.abort();
                    return Ok(());
                }
                _ = sigterm.recv() => {
                    info!(event = "shutdown_initiated", signal = "SIGTERM");
                    graceful_shutdown(&order_tx, &notifier, engine_ptr).await;
                    writer_handle.abort();
                    reader_handle.abort();
                    return Ok(());
                }
                // ── WATCHDOG: task failure ──
                Some(reason) = err_rx.recv() => {
                    shutdown_reason = reason;
                    info!(event = "watchdog_triggered", reason = reason);
                    should_reconnect = true;
                }
                // ── MARKET DATA with 15s watchdog ──
                res = tokio::time::timeout(Duration::from_secs(15), mdata_read.next()) => {
                    match res {
                        Ok(Some(Ok(msg))) if msg.is_text() => {
                            let mut bytes = msg.into_data().to_vec();
                            let v = match simd_json::to_borrowed_value(&mut bytes) {
                                Ok(v) => v,
                                Err(_) => continue,
                            };
                            if let BorrowedValue::Array(arr) = v {
                                if arr[0].as_i64() == chan_id && chan_id.is_some() {
                                    if arr[1].as_str() == Some("hb") { continue; }

                                    if arr[1].as_str() == Some("cs") {
                                        let remote_cs = arr[2].as_i64().unwrap_or(0) as i32;
                                        let do_debug = cs_debug_count < 5;
                                        let local_cs = calculate_checksum(unsafe { &*engine_ptr }, do_debug);
                                        cs_debug_count += 1;
                                        if remote_cs != local_cs {
                                            info!(event = "checksum_mismatch", remote = remote_cs, local = local_cs);
                                            if cs_debug_count > 10 { shutdown_reason = "checksum_persist"; should_reconnect = true; }
                                        } else {
                                            info!(event = "checksum_ok", cs = remote_cs);
                                        }
                                        continue;
                                    }

                                    // Book Update
                                    if let Some(top_arr) = arr[1].as_array() {
                                        let is_nested = top_arr.first().map_or(false, |e| e.as_array().is_some());
                                        if is_nested {
                                            let mut bc = 0u32;
                                            let mut ac = 0u32;
                                            for entry in top_arr {
                                                if let Some(u) = entry.as_array() {
                                                    if let (Some(price), Some(count), Some(amount)) = (safe_as_f64(&u[0]), safe_as_i64(&u[1]), safe_as_f64(&u[2])) {
                                                        let p = (price * beroun_types::PRICE_SCALE).round() as u64;
                                                        let a = (amount * beroun_types::PRICE_SCALE).round() as i64;
                                                        let c = count as u64;
                                                        if amount > 0.0 { update_book(unsafe { &raw mut (*engine_ptr).bids }, p, a, c); bc += 1; }
                                                        else { update_book(unsafe { &raw mut (*engine_ptr).asks }, p, a, c); ac += 1; }
                                                    }
                                                }
                                            }
                                            fence(Ordering::SeqCst);
                                            sort_book(unsafe { &raw mut (*engine_ptr).bids }, true);
                                            sort_book(unsafe { &raw mut (*engine_ptr).asks }, false);
                                            if !snapshot_loaded && (bc + ac) > 10 {
                                                snapshot_loaded = true;
                                                let eng = unsafe { &*engine_ptr };
                                                info!(event = "snapshot_loaded", bids = bc, asks = ac,
                                                      best_bid_p = eng.bids[0].price.load(Ordering::SeqCst),
                                                      best_ask_p = eng.asks[0].price.load(Ordering::SeqCst),
                                                      best_bid_a = eng.bids[0].amount.load(Ordering::SeqCst),
                                                      best_ask_a = eng.asks[0].amount.load(Ordering::SeqCst),
                                                      best_bid_c = eng.bids[0].count.load(Ordering::SeqCst),
                                                      best_ask_c = eng.asks[0].count.load(Ordering::SeqCst));
                                            }
                                        } else {
                                            if let (Some(price), Some(count), Some(amount)) = (safe_as_f64(&top_arr[0]), safe_as_i64(&top_arr[1]), safe_as_f64(&top_arr[2])) {
                                                let p = (price * beroun_types::PRICE_SCALE).round() as u64;
                                                let a = (amount * beroun_types::PRICE_SCALE).round() as i64;
                                                let c = count as u64;
                                                if amount > 0.0 { update_book(unsafe { &raw mut (*engine_ptr).bids }, p, a, c); sort_book(unsafe { &raw mut (*engine_ptr).bids }, true); }
                                                else { update_book(unsafe { &raw mut (*engine_ptr).asks }, p, a, c); sort_book(unsafe { &raw mut (*engine_ptr).asks }, false); }
                                            }
                                        }
                                    }

                                    // Update BBA
                                    let eng = unsafe { &*engine_ptr };
                                    let best_bid = eng.bids[0].price.load(Ordering::SeqCst);
                                    let best_ask = eng.asks[0].price.load(Ordering::SeqCst);
                                    eng.best_bid.store(best_bid, Ordering::SeqCst);
                                    eng.best_ask.store(best_ask, Ordering::SeqCst);

                                    // ── SNIPER INTEL v6.2 (OBI + Dynamic Sizing) ──
                                    const MIN_TICK: i64 = 100_000_000;
                                    if best_bid > 0 && best_ask > 0 {
                                        let now = Instant::now();
                                        if now.duration_since(last_upd).as_millis() > 3000 {
                                            if risk.paused.load(Ordering::Acquire) == 0 {
                                                // 1. MICRO-PRICE (volume-weighted mid from L1)
                                                let mid_i = ((best_bid as i64) + (best_ask as i64)) / 2;
                                                let bid_vol_0 = eng.bids[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                                                let ask_vol_0 = eng.asks[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                                                let total_vol_0 = bid_vol_0 + ask_vol_0;
                                                let micro_i = if total_vol_0 > 0.0 {
                                                    ((best_bid as f64 * ask_vol_0 + best_ask as f64 * bid_vol_0) / total_vol_0).round() as i64
                                                } else { mid_i };
                                                let micro_f = micro_i as f64 / beroun_types::PRICE_SCALE;

                                                // 2. L2 ORDER BOOK IMBALANCE (OBI — top 10 levels)
                                                let mut sum_bid_vol: f64 = 0.0;
                                                let mut sum_ask_vol: f64 = 0.0;
                                                for i in 0..10 {
                                                    sum_bid_vol += eng.bids[i].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                                                    sum_ask_vol += eng.asks[i].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                                                }
                                                let obi = if (sum_bid_vol + sum_ask_vol) > 0.0 {
                                                    (sum_bid_vol - sum_ask_vol) / (sum_bid_vol + sum_ask_vol)
                                                } else { 0.0 };

                                                // 3. DYNAMIC SIZING (signal convergence)
                                                let base_usd = risk.order_usd.load(Ordering::Acquire) as f64;
                                                let micro_bias = micro_i - mid_i;
                                                let final_order_usd = if (obi > 0.2 && micro_bias > 0) || (obi < -0.2 && micro_bias < 0) {
                                                    (base_usd * 1.5).clamp(base_usd * 0.5, base_usd * 2.0)
                                                } else if (obi > 0.1 && micro_bias < 0) || (obi < -0.1 && micro_bias > 0) {
                                                    (base_usd * 0.7).clamp(base_usd * 0.5, base_usd * 2.0)
                                                } else {
                                                    base_usd
                                                };

                                                // 4. INVENTORY SKEW
                                                let grid = risk.grid_step.load(Ordering::Acquire) as i64;
                                                let current_pos = eng.net_position.load(Ordering::Acquire);
                                                let max_pos = risk.max_inv_delta.load(Ordering::Acquire) as i64;
                                                let inv_skew = if max_pos > 0 {
                                                    let ratio = (current_pos as f64 / max_pos as f64).clamp(-1.0, 1.0);
                                                    (-ratio * grid as f64 * 2.0).round() as i64
                                                } else { 0 };

                                                // 5. FINAL PRICES
                                                let bias = risk.bias_offset.load(Ordering::Acquire);
                                                let final_bias = bias + inv_skew;
                                                let buy_i = (micro_i - grid + final_bias).max(0);
                                                let sell_i = (micro_i + grid + final_bias).max(0);
                                                let lb = eng.last_buy_price.load(Ordering::SeqCst);
                                                let ls = eng.last_sell_price.load(Ordering::SeqCst);
                                                let db = (buy_i - lb).abs();
                                                let ds = (sell_i - ls).abs();

                                                if db >= MIN_TICK || ds >= MIN_TICK || lb == 0 {
                                                    // 6. TARGETED CANCEL + NEW ORDERS
                                                    let buy_id = eng.active_buy_id.load(Ordering::SeqCst);
                                                    let sell_id = eng.active_sell_id.load(Ordering::SeqCst);
                                                    let oc_payload = match (buy_id > 0, sell_id > 0) {
                                                        (true, true) => format!(r#"["oc_multi",{{"id":[{},{}]}}],"#, buy_id, sell_id),
                                                        (true, false) => format!(r#"["oc_multi",{{"id":[{}]}}],"#, buy_id),
                                                        (false, true) => format!(r#"["oc_multi",{{"id":[{}]}}],"#, sell_id),
                                                        _ => String::new(),
                                                    };

                                                    let bp = buy_i as f64 / beroun_types::PRICE_SCALE;
                                                    let sp = sell_i as f64 / beroun_types::PRICE_SCALE;
                                                    let amt = (final_order_usd / micro_f / beroun_types::PRICE_SCALE).max(0.00015);
                                                    let msg = format!(
                                                        r#"[0,"ox_multi",null,[{}["on",{{"symbol":"tBTCUSD","amount":"{:.5}","price":"{:.2}","type":"EXCHANGE LIMIT","flags":4096}}],["on",{{"symbol":"tBTCUSD","amount":"{:.5}","price":"{:.2}","type":"EXCHANGE LIMIT","flags":4096}}]]]"#,
                                                        oc_payload, amt, bp, -amt, sp
                                                    );
                                                    let _ = order_tx.send(msg);

                                                    // 7. DASHBOARD METRICS
                                                    eng.last_buy_price.store(buy_i, Ordering::SeqCst);
                                                    eng.last_sell_price.store(sell_i, Ordering::SeqCst);
                                                    let t2t_us = now.elapsed().as_micros() as u64;
                                                    eng.t2t_micros.store(t2t_us, Ordering::SeqCst);
                                                    eng.micro_price.store(micro_i as u64, Ordering::SeqCst);
                                                    eng.current_skew.store(inv_skew, Ordering::SeqCst);
                                                    eng.l2_imbalance.store((obi * beroun_types::PRICE_SCALE) as i64, Ordering::SeqCst);
                                                    eng.current_order_usd.store(final_order_usd as u64, Ordering::SeqCst);
                                                    last_upd = now;
                                                    info!(event = "sniper_fire", bid = best_bid, ask = best_ask,
                                                          micro = micro_i, buy = bp, sell = sp,
                                                          obi = format!("{:.3}", obi),
                                                          order_usd = final_order_usd as f64 / beroun_types::PRICE_SCALE,
                                                          inv_skew = inv_skew, position = current_pos,
                                                          dynamic_grid = grid);
                                                }
                                            }
                                        }
                                    }
                                }
                            } else if let BorrowedValue::Object(obj) = v {
                                if obj.get("event") == Some(&BorrowedValue::from("subscribed")) {
                                    chan_id = obj.get("chanId").and_then(|id: &BorrowedValue| id.as_i64());
                                    info!(event = "mdata_subscribed", chan_id = ?chan_id);
                                }
                                if obj.get("event") == Some(&BorrowedValue::from("error")) {
                                    info!(event = "mdata_error", msg = ?obj.get("msg"), code = ?obj.get("code"));
                                }
                            }
                        }
                        Ok(Some(Ok(_))) => {} // Ping/Pong
                        Ok(Some(Err(e))) => { info!(event = "mdata_read_error", error = %e); shutdown_reason = "mdata_error"; should_reconnect = true; }
                        Ok(None) => { info!(event = "mdata_socket_closed"); shutdown_reason = "mdata_closed"; should_reconnect = true; }
                        Err(_) => { info!(event = "mdata_timeout_15s"); shutdown_reason = "mdata_timeout"; should_reconnect = true; }
                    }
                }
            }
        }

        // ═══ RECONNECT CLEANUP ═══
        info!(event = "reconnecting", reason = shutdown_reason);
        writer_handle.abort();
        reader_handle.abort();

        // Zero order book — sniper waits for fresh snapshot
        let eng = unsafe { &*engine_ptr };
        for i in 0..beroun_types::BOOK_LEVELS {
            eng.bids[i].count.store(0, Ordering::SeqCst);
            eng.bids[i].price.store(0, Ordering::SeqCst);
            eng.asks[i].count.store(0, Ordering::SeqCst);
            eng.asks[i].price.store(0, Ordering::SeqCst);
        }
        eng.best_bid.store(0, Ordering::SeqCst);
        eng.best_ask.store(0, Ordering::SeqCst);
        eng.last_buy_price.store(0, Ordering::SeqCst);
        eng.last_sell_price.store(0, Ordering::SeqCst);
        eng.active_buy_id.store(0, Ordering::SeqCst);
        eng.active_sell_id.store(0, Ordering::SeqCst);

        notifier.alert(format!("⚠️ *RECONNECT*\n`Reason: {}`", shutdown_reason));
        tokio::time::sleep(Duration::from_secs(3)).await;
    }
}

/// Graceful shutdown: cancel tracked orders, wait for TCP flush, exit.
async fn graceful_shutdown(
    order_tx: &tokio::sync::mpsc::UnboundedSender<String>,
    notifier: &AsyncNotifier,
    engine_ptr: *const EngineState,
) {
    notifier.alert("🛑 *Shutdown*: cancelling orders...".to_string());
    let eng = unsafe { &*engine_ptr };
    let buy_id = eng.active_buy_id.load(Ordering::SeqCst);
    let sell_id = eng.active_sell_id.load(Ordering::SeqCst);

    let cancel_msg = if buy_id > 0 || sell_id > 0 {
        let mut ids = Vec::new();
        if buy_id > 0 { ids.push(buy_id.to_string()); }
        if sell_id > 0 { ids.push(sell_id.to_string()); }
        info!(event = "shutdown_cancel_targeted", buy_id = buy_id, sell_id = sell_id);
        format!(r#"[0,"oc_multi",null,{{"id":[{}]}}]"#, ids.join(","))
    } else {
        // Fallback: no tracked IDs → cancel all as safety net
        info!(event = "shutdown_cancel_all_fallback");
        r#"[0,"oc_multi",null,{"all":1}]"#.to_string()
    };

    if let Err(e) = order_tx.send(cancel_msg) {
        tracing::error!(event = "shutdown_cancel_failed", error = %e);
    } else {
        info!(event = "cancel_sent");
    }
    tokio::time::sleep(Duration::from_millis(500)).await;
    info!(event = "system_shutdown_complete");
    notifier.alert("💤 Shutdown complete.".to_string());
}

/// TCP+TLS+WebSocket with TCP_NODELAY
async fn connect_ws() -> Result<tokio_tungstenite::WebSocketStream<tokio_native_tls::TlsStream<tokio::net::TcpStream>>, std::io::Error> {
    let tcp = tokio::net::TcpStream::connect("api.bitfinex.com:443").await?;
    tcp.set_nodelay(true)?;
    let connector = tokio_native_tls::TlsConnector::from(
        native_tls::TlsConnector::new().map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?
    );
    let tls = connector.connect("api.bitfinex.com", tcp).await
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    let (ws, _) = tokio_tungstenite::client_async("wss://api.bitfinex.com/ws/2", tls).await
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    Ok(ws)
}
