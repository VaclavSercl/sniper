use std::sync::atomic::Ordering;
use std::sync::Arc;
use tokio::sync::mpsc;
use std::time::{Instant, SystemTime, UNIX_EPOCH, Duration};
use serde_json::json;
use hmac::{Hmac, Mac};
use sha2::Sha384;
use dotenvy::dotenv;
use tracing::{info, Level, error};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};
use memmap2::MmapMut;
use anyhow::{Context, Result};
use std::collections::VecDeque;
use fs2::FileExt;

use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH};

use fastwebsockets::{OpCode, Payload};
use hyper::{Request, header::{CONNECTION, UPGRADE, HOST}};

struct SpawnExecutor;
impl<Fut> hyper::rt::Executor<Fut> for SpawnExecutor
where
    Fut: std::future::Future + Send + 'static,
    Fut::Output: Send + 'static,
{
    fn execute(&self, fut: Fut) {
        tokio::spawn(fut);
    }
}

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
                                .stdout(std::fs::File::create("/dev/shm/beroun/state.json").unwrap_or_else(|_| std::fs::File::create("/dev/null").unwrap()))
                                .status(); // .status() waits for child — prevents zombie
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

// ═══ HYDRA ORDER SLOT HELPERS (v9.0) ═══
#[inline]
fn store_order_slot(ids: &[std::sync::atomic::AtomicU64; beroun_types::MAX_GRID_LEVELS], id: u64) {
    for slot in ids {
        if slot.compare_exchange(0, id, Ordering::SeqCst, Ordering::SeqCst).is_ok() {
            return;
        }
    }
}

#[inline]
fn clear_order_slot(ids: &[std::sync::atomic::AtomicU64; beroun_types::MAX_GRID_LEVELS], id: u64) {
    for slot in ids {
        let _ = slot.compare_exchange(id, 0, Ordering::SeqCst, Ordering::SeqCst);
    }
}

fn collect_all_order_ids(eng: &EngineState) -> Vec<u64> {
    eng.active_buy_ids.iter().chain(eng.active_sell_ids.iter())
        .map(|s| s.load(Ordering::SeqCst))
        .filter(|&id| id > 0)
        .collect()
}

fn zero_all_order_slots(eng: &EngineState) {
    for slot in eng.active_buy_ids.iter().chain(eng.active_sell_ids.iter()) {
        slot.store(0, Ordering::SeqCst);
    }
}

fn main() -> Result<()> {
    dotenv().ok();

    // ═══ NON-BLOCKING DAILY LOG ROTATION (L2 Audit Trail) ═══
    // Sniper writes to a lock-free channel → background thread flushes to disk
    // Zero impact on L0 hot path latency
    let log_dir = "/home/wwwenda/hft-sniper/logs";
    let file_appender = tracing_appender::rolling::daily(log_dir, "trading.log");
    let (non_blocking, _log_guard) = tracing_appender::non_blocking(file_appender);
    tracing_subscriber::registry()
        .with(fmt::layer().with_target(false).json().with_writer(non_blocking))
        .with(EnvFilter::from_default_env().add_directive(Level::INFO.into()))
        .init();

    // ═══ VRSTVA 1: /dev/shm/beroun/ for mmap IPC (RAM-backed) ═══
    std::fs::create_dir_all("/dev/shm/beroun").context("Failed to create /dev/shm/beroun")?;

    // ═══ SINGLE-INSTANCE LOCK (Ghost-in-the-Machine prevention) ═══
    let lock_file = std::fs::File::create("/tmp/beroun-sniper.lock")
        .context("Failed to create lock file")?;
    if lock_file.try_lock_exclusive().is_err() {
        tracing::error!(event = "dual_instance_blocked",
            msg = "Another beroun-core is already running! Aborting to prevent dual-trading.");
        std::process::exit(1);
    }
    info!(event = "instance_lock_acquired", lock = "/tmp/beroun-sniper.lock");
    // lock_file must stay alive (held open) for the entire process lifetime
    let _lock_guard = lock_file;

    if let Some(core_ids) = core_affinity::get_core_ids()
        && core_ids.len() > 1 {
            core_affinity::set_for_current(core_ids[1]);
            info!(event = "cpu_pinned", core = 1, total_cores = core_ids.len());
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

    // ═══ VOLATILITY + ADAPTIVE GRID ENGINE (v9.5) ═══
    let vol_engine_ptr = engine_ptr as usize;
    let vol_risk_ptr = risk as *const RiskState as usize;
    tokio::spawn(async move {
        let mut price_history: VecDeque<i64> = VecDeque::with_capacity(61);
        let base_grid: i64 = 200_000_000;    // $2 base spread
        let max_grid: i64 = 2_000_000_000;   // $20 max spread (clamped)
        let min_grid: i64 = 200_000_000;     // $2 min spread (clamped)
        let vol_mult: f64 = 0.15;            // 15% of price range
        let default_grid: u64 = 300_000_000; // $3 fallback

        // v9.5: Adaptive grid state
        let mut fill_check_counter: u32 = 0;
        let fill_check_interval: u32 = 120;  // Every 60s (120 × 500ms)
        let mut grid_mult: f64 = 1.0;        // 0.7 (tight) → 1.5 (wide)

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

                // v9.5: Fill-rate adaptive grid (every 60s)
                fill_check_counter += 1;
                if fill_check_counter >= fill_check_interval {
                    fill_check_counter = 0;
                    let buys = engine.buy_fill_count.swap(0, Ordering::Relaxed);
                    let sells = engine.sell_fill_count.swap(0, Ordering::Relaxed);
                    let total = buys + sells;

                    if total >= 4 {
                        // Balance ratio: 1.0 = perfectly balanced, 0.0 = all one side
                        let balance = 1.0 - ((buys as f64 - sells as f64).abs() / total as f64);
                        // fill_rate: fills per minute
                        let fill_rate = total as f64; // Already per 60s interval

                        // High balance + high fill-rate → tighten grid (more spread capture)
                        // Low balance (one-sided) → widen grid (reduce adverse selection)
                        let target_mult = if balance > 0.6 && fill_rate > 10.0 {
                            0.7  // Aggressive: balanced, active market
                        } else if balance > 0.4 {
                            1.0  // Normal
                        } else {
                            1.4  // Defensive: one-sided fills, widen
                        };
                        // Smooth transition (20% step toward target)
                        grid_mult = grid_mult + (target_mult - grid_mult) * 0.2;
                        grid_mult = grid_mult.clamp(0.7, 1.5);

                        tracing::info!(event = "adaptive_grid",
                            buys = buys, sells = sells, balance = format!("{:.2}", balance),
                            fill_rate = total, grid_mult = format!("{:.2}", grid_mult));
                    }
                }

                if price_history.len() >= 10 {
                    let min_p = *price_history.iter().min().unwrap();
                    let max_p = *price_history.iter().max().unwrap();
                    let range = max_p - min_p;
                    let dynamic = (range as f64 * vol_mult) as i64;
                    let adapted = ((base_grid + dynamic) as f64 * grid_mult) as i64;
                    let new_grid = adapted.clamp(min_grid, max_grid) as u64;

                    // ═══ v10.5: L2 Oracle Grid Override ═══
                    // If L2 Oracle has written within last 10 minutes,
                    // respect its grid decision and skip vol-engine override.
                    let l2_action_ms = engine.l2_last_action_ms.load(Ordering::Acquire);
                    let now_ms_vol = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH).unwrap_or_default()
                        .as_millis() as u64;
                    let l2_active = l2_action_ms > 0 && now_ms_vol.saturating_sub(l2_action_ms) < 600_000;

                    if l2_active {
                        // L2 Oracle is active — do NOT overwrite grid_step
                        // (Oracle writes via beroun-config set-grid)
                        if range > 5_000_000_000 {
                            tracing::info!(event = "vol_engine_deferred",
                                range_usd = range / beroun_types::PRICE_SCALE_I,
                                vol_grid_usd = new_grid as i64 / beroun_types::PRICE_SCALE_I,
                                reason = "l2_oracle_active");
                        }
                    } else {
                        risk.grid_step.store(new_grid, Ordering::Release);
                        if range > 5_000_000_000 {
                            tracing::info!(event = "volatility_spike",
                                range_usd = range / beroun_types::PRICE_SCALE_I,
                                new_grid_usd = new_grid as i64 / beroun_types::PRICE_SCALE_I);
                        }
                    }
                } else {
                    risk.grid_step.store(default_grid, Ordering::Release);
                }
            }
        }
    });

    let key = std::env::var("BITFINEX_API_KEY").context("Missing API KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing API SECRET")?;

    info!(event = "system_start", version = "10.0.0-apex-predator");
    notifier.alert("*Beroun Sniper v10.4 NEURAL CROSS ONLINE*\n`Hydra Grid + L1 Shield + Neural Cross + Lesson Validator`".to_string());

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

        // Order channel: HFT loop → exec writer
        let (order_tx, mut order_rx) = tokio::sync::mpsc::unbounded_channel::<String>();
        // Error channel: any task → main loop (watchdog kill switch)
        let (err_tx, mut err_rx) = tokio::sync::mpsc::unbounded_channel::<&'static str>();

        // ── TASK 1: EXEC LOOP (Read + Write) ──
        let mut exec_ws = ws_exec;
        let err_tx_exec = err_tx.clone();
        let key_c = key.clone();
        let sec_c = sec.clone();
        
        let exec_engine = engine_ptr as usize;
        let exec_notifier = notifier.clone();

        let exec_handle = tokio::spawn(async move {
            let nonce = match SystemTime::now().duration_since(UNIX_EPOCH) {
                Ok(d) => d.as_millis().to_string(),
                Err(_) => { let _ = err_tx_exec.send("writer_time_error"); return; }
            };
            let auth_payload = format!("AUTH{}", nonce);
            let sig = get_sig(&sec_c, &auth_payload).await;
            let _ = exec_ws.write_frame(fastwebsockets::Frame::text(Payload::Owned(json!({"event":"conf","flags":131072}).to_string().into_bytes()))).await;
            let auth_msg = json!({"event":"auth","apiKey":key_c,"authSig":sig,"authPayload":auth_payload,"authNonce":nonce,"dms":4});
            if exec_ws.write_frame(fastwebsockets::Frame::text(Payload::Owned(auth_msg.to_string().into_bytes()))).await.is_err() {
                let _ = err_tx_exec.send("writer_auth_failed");
                return;
            }

            loop {
                tokio::select! {
                    msg = order_rx.recv() => {
                        if let Some(order_msg) = msg {
                            if exec_ws.write_frame(fastwebsockets::Frame::text(Payload::Owned(order_msg.into_bytes()))).await.is_err() {
                                let _ = err_tx_exec.send("writer_socket_error");
                                break;
                            }
                        } else { break; }
                    }
                    frame = tokio::time::timeout(Duration::from_secs(30), exec_ws.read_frame()) => {
                match frame {
                    Ok(Ok(frame)) => {
                        match frame.opcode {
                            OpCode::Text => {
                                let mut bytes = frame.payload.to_vec();
                                let v = match simd_json::to_borrowed_value(&mut bytes) {
                                    Ok(v) => v,
                                    Err(_) => continue,
                                };
                                if let BorrowedValue::Array(arr) = v {
                                    let engine = unsafe { &*(exec_engine as *const EngineState) };
                                    if arr[0].as_i64() == Some(0) {
                                        let mt = arr[1].as_str().unwrap_or("");
                                        if mt == "te"
                                            && let Some(trade) = arr[2].as_array()
                                                && let (Some(trade_amt), Some(trade_price)) = (safe_as_f64(&trade[4]), safe_as_f64(&trade[5])) {
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

                                                    // 4. ALPHA TRACKING: measure AI contribution
                                                    let ai_bias_now = engine.current_ai_bias.load(Ordering::Acquire) as f64 / scale;
                                                    if ai_bias_now.abs() > 0.01 {
                                                        // For buys: positive bias = bought higher = negative alpha
                                                        // For sells: positive bias = sold higher = positive alpha
                                                        let alpha = ai_bias_now * trade_amt.abs() * trade_amt.signum();
                                                        engine.ai_alpha_usd.fetch_add((alpha * scale).round() as i64, Ordering::SeqCst);
                                                    }

                                                    info!(event = "trade_executed", amount = trade_amt, price = trade_price,
                                                          new_pos = new_pos, aep = engine.average_entry_price.load(Ordering::SeqCst) as f64 / scale);
                                                    // v9.5: Fill-rate tracking
                                                    if trade_amt > 0.0 {
                                                        engine.buy_fill_count.fetch_add(1, Ordering::Relaxed);
                                                    } else {
                                                        engine.sell_fill_count.fetch_add(1, Ordering::Relaxed);
                                                    }
                                                    // v10.0: Monthly volume for fee tier
                                                    let trade_vol_usd = (trade_amt.abs() * trade_price * scale) as u64;
                                                    engine.monthly_volume_usd.fetch_add(trade_vol_usd, Ordering::Relaxed);

                                                    // v9.2: HYBRID INTELLIGENCE — TradeAnalytics
                                                    engine.session_fill_count.fetch_add(1, Ordering::Relaxed);
                                                    if trade_amt > 0.0 {
                                                        engine.session_buy_volume.fetch_add((trade_amt * scale) as u64, Ordering::Relaxed);
                                                        engine.session_buy_usd.fetch_add(trade_vol_usd, Ordering::Relaxed);
                                                    } else {
                                                        engine.session_sell_volume.fetch_add((trade_amt.abs() * scale) as u64, Ordering::Relaxed);
                                                        engine.session_sell_usd.fetch_add(trade_vol_usd, Ordering::Relaxed);
                                                    }
                                                    exec_notifier.trade(trade_amt, trade_price);
                                                }
                                        if mt == "wu" || mt == "ws" {
                                            let wd: Vec<&BorrowedValue> = if mt == "wu" { vec![&arr[2]] }
                                            else { arr[2].as_array().map(|a: &Vec<BorrowedValue>| a.iter().collect()).unwrap_or_default() };
                                            for w in wd {
                                                if let (Some(wt), Some(cur), Some(bal)) = (w[0].as_str(), w[1].as_str(), safe_as_f64(&w[2]))
                                                    && wt == "exchange" {
                                                        if cur == beroun_types::TRADING_BASE { engine.wallet_btc.store((bal * beroun_types::PRICE_SCALE) as u64, Ordering::SeqCst); }
                                                        else if cur == beroun_types::TRADING_QUOTE || cur == "UST" { engine.wallet_usd.store((bal * beroun_types::PRICE_SCALE) as u64, Ordering::SeqCst); }
                                                    }
                                            }
                                        }
                                        if mt == "n"
                                            && let Some(n) = arr[2].as_array() {
                                                info!(event = "bitfinex_notification",
                                                      ntype = n[1].as_str().unwrap_or("?"),
                                                      status = n[6].as_str().unwrap_or("?"),
                                                      text = n[7].as_str().unwrap_or(""));
                                            }
                                        // --- ORDER SNAPSHOT (state recovery after connect) ---
                                        if mt == "os"
                                            && let Some(orders) = arr[2].as_array() {
                                                for order in orders {
                                                    if let Some(o) = order.as_array()
                                                        && let (Some(id), Some(sym), Some(amt), Some(status)) = (
                                                            o[0].as_u64().or_else(|| o[0].as_f64().map(|f| f as u64)),
                                                            o[3].as_str(), safe_as_f64(&o[6]), o[13].as_str()
                                                        )
                                                            && sym == beroun_types::TRADING_SYMBOL && (status.contains("ACTIVE") || status.contains("PARTIALLY")) {
                                                                if amt > 0.0 { store_order_slot(&engine.active_buy_ids, id); }
                                                                else { store_order_slot(&engine.active_sell_ids, id); }
                                                                info!(event = "order_recovered", id = id, side = if amt > 0.0 { "buy" } else { "sell" });
                                                            }
                                                }
                                            }
                                        // --- ORDER LIFECYCLE (on=new, ou=update, oc=cancel) ---
                                        if (mt == "on" || mt == "ou" || mt == "oc")
                                            && let Some(o) = arr[2].as_array()
                                                && let (Some(id), Some(sym), Some(amt), Some(status)) = (
                                                    o[0].as_u64().or_else(|| o[0].as_f64().map(|f| f as u64)),
                                                    o[3].as_str(), safe_as_f64(&o[6]), o[13].as_str()
                                                )
                                                    && sym == beroun_types::TRADING_SYMBOL {
                                                        if status.contains("ACTIVE") || status.contains("PARTIALLY") {
                                                            if amt > 0.0 { store_order_slot(&engine.active_buy_ids, id); }
                                                            else { store_order_slot(&engine.active_sell_ids, id); }
                                                            info!(event = "order_tracked", mt = mt, id = id, side = if amt > 0.0 { "buy" } else { "sell" });
                                                        } else if status.contains("CANCELED") || status.contains("EXECUTED") {
                                                            if amt > 0.0 {
                                                                clear_order_slot(&engine.active_buy_ids, id);
                                                            } else {
                                                                clear_order_slot(&engine.active_sell_ids, id);
                                                            }
                                                            info!(event = "order_cleared", mt = mt, id = id, status = status);
                                                        }
                                                    }
                                    }
                                } else if let BorrowedValue::Object(obj) = v
                                    && obj.get("event") == Some(&BorrowedValue::from("auth")) && obj.get("status") == Some(&BorrowedValue::from("OK")) {
                                        info!(event = "exec_auth_ok");
                                    }
                            },
                            OpCode::Ping => { let _ = exec_ws.write_frame(fastwebsockets::Frame::pong(frame.payload)).await; }
                            OpCode::Close => { let _ = err_tx_exec.send("exec_socket_closed"); break; },
                            _ => {} // Ping/Pong/Binary
                        }
                    }
                    Ok(Err(e)) => { let _ = err_tx_exec.send("exec_socket_error"); error!(event = "exec_read_error", error = %e); break; }
                    Err(_) => { let _ = err_tx_exec.send("exec_socket_timeout"); info!(event = "exec_socket_timeout"); break; }
                }
            } // closes frame_res branch
            } // closes tokio::select!
            } // closes loop 
        });

        // ── TASK 3: MARKET DATA HFT LOOP (inline, highest priority) ──
        let mut mdata_read = ws_mdata;
        let _ = mdata_read.write_frame(fastwebsockets::Frame::text(Payload::Owned(json!({"event":"conf","flags":131072|536870912}).to_string().into_bytes()))).await;
        let _ = mdata_read.write_frame(fastwebsockets::Frame::text(Payload::Owned(json!({"event":"subscribe","channel":"book","symbol":beroun_types::TRADING_SYMBOL,"prec":"P0","freq":"F0","len":"25"}).to_string().into_bytes()))).await;

        let mut chan_id: Option<i64> = None;
        let mut last_upd = Instant::now();
        let mut snapshot_loaded = false;
        let mut cs_debug_count: u32 = 0;
        let mut cs_fail_count: u32 = 0;
        let mut should_reconnect = false;
        let mut shutdown_reason: &str = "unknown";
        let mut depth_history: VecDeque<f64> = VecDeque::with_capacity(61);
        let mut was_in_hole: bool = false;

        // ═══ v10.6 GHOST ORDERS STATE ═══
        let mut ghost_last_micro: i64 = 0;         // Previous micro_price for velocity
        let mut ghost_velocity: f64 = 0.0;         // Price velocity (ticks/update)
        let ghost_velocity_max: f64 = 500_000_000.0; // $5 per tick = too fast, reject
        let mut ghost_last_inject: Instant = Instant::now();

        loop {
            if should_reconnect { break; }
            tokio::select! {
                biased;
                // ── GRACEFUL SHUTDOWN ──
                _ = tokio::signal::ctrl_c() => {
                    info!(event = "shutdown_initiated", signal = "SIGINT");
                    graceful_shutdown(&order_tx, &notifier, engine_ptr).await;
                    exec_handle.abort();
                    return Ok(());
                }
                _ = sigterm.recv() => {
                    info!(event = "shutdown_initiated", signal = "SIGTERM");
                    graceful_shutdown(&order_tx, &notifier, engine_ptr).await;
                    exec_handle.abort();
                    return Ok(());
                }
                // ── WATCHDOG: task failure ──
                Some(reason) = err_rx.recv() => {
                    shutdown_reason = reason;
                    info!(event = "watchdog_triggered", reason = reason);
                    should_reconnect = true;
                }
                // ── MARKET DATA with 15s watchdog ──
                res = tokio::time::timeout(Duration::from_secs(15), mdata_read.read_frame()) => {
                    match res {
                        Ok(Ok(frame)) => {
                            match frame.opcode {
                                OpCode::Text => {
                                    let mut bytes = frame.payload.to_vec();
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
                                                    cs_fail_count += 1;
                                                    info!(event = "checksum_mismatch", remote = remote_cs, local = local_cs, consecutive = cs_fail_count);
                                                    if cs_fail_count >= 5 { shutdown_reason = "checksum_persist"; should_reconnect = true; }
                                                } else {
                                                    cs_fail_count = 0; // Reset on success
                                                    info!(event = "checksum_ok", cs = remote_cs);
                                                }
                                                continue;
                                            }

                                            // Book Update
                                            if let Some(top_arr) = arr[1].as_array() {
                                                let is_nested = top_arr.first().is_some_and(|e| e.as_array().is_some());
                                                if is_nested {
                                                    let mut bc = 0u32;
                                                    let mut ac = 0u32;
                                                    for entry in top_arr {
                                                        if let Some(u) = entry.as_array()
                                                            && let (Some(price), Some(count), Some(amount)) = (safe_as_f64(&u[0]), safe_as_i64(&u[1]), safe_as_f64(&u[2])) {
                                                                let p = (price * beroun_types::PRICE_SCALE).round() as u64;
                                                                let a = (amount * beroun_types::PRICE_SCALE).round() as i64;
                                                                let c = count as u64;
                                                                if amount > 0.0 { update_book(unsafe { &raw mut (*engine_ptr).bids }, p, a, c); bc += 1; }
                                                                else { update_book(unsafe { &raw mut (*engine_ptr).asks }, p, a, c); ac += 1; }
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

                                            // ═══ v10.6 GHOST PROXIMITY CHECK (runs on EVERY book tick) ═══
                                            if best_bid > 0 && best_ask > 0 {
                                                let ghost_trans = eng.ghost_transparency.load(Ordering::Relaxed);
                                                if ghost_trans < 10000 { // Ghost mode active
                                                    let bid_vol_g = eng.bids[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                                                    let ask_vol_g = eng.asks[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                                                    let total_vol_g = bid_vol_g + ask_vol_g;
                                                    let micro_g = if total_vol_g > 0.0 {
                                                        ((best_bid as f64 * ask_vol_g + best_ask as f64 * bid_vol_g) / total_vol_g).round() as i64
                                                    } else {
                                                        ((best_bid as i64) + (best_ask as i64)) / 2
                                                    };

                                                    // Velocity tracking (EMA: 80% old + 20% new)
                                                    if ghost_last_micro > 0 {
                                                        let delta = (micro_g - ghost_last_micro).abs() as f64;
                                                        ghost_velocity = ghost_velocity * 0.8 + delta * 0.2;
                                                    }
                                                    ghost_last_micro = micro_g;

                                                    // Check ghost levels for proximity injection
                                                    // v10.7: Dynamic trigger zone from AI registry
                                                    let ai_trigger = eng.ai_ghost_trigger_pct.load(Ordering::Relaxed).max(10).min(500) as f64 / 100000.0;
                                                    let trigger_dist = (micro_g as f64 * ai_trigger) as i64;
                                                    let velocity_safe = ghost_velocity < ghost_velocity_max;
                                                    let min_inject_interval = ghost_last_inject.elapsed().as_millis() > 200; // Rate limit: max 5/sec

                                                    if velocity_safe && min_inject_interval {
                                                        let mut mask = eng.ghost_active_mask.load(Ordering::Relaxed);
                                                        for i in 0..beroun_types::MAX_GRID_LEVELS {
                                                            // Ghost BUY levels
                                                            let gbp = eng.ghost_buy_prices[i].load(Ordering::Relaxed);
                                                            if gbp > 0 {
                                                                let dist = (micro_g - gbp).abs();
                                                                let bit = 1u64 << i;
                                                                if dist < trigger_dist && (mask & bit) == 0 {
                                                                    // FLASH INJECT: fire IOC buy
                                                                    let price_f = gbp as f64 / beroun_types::PRICE_SCALE;
                                                                    let amt_f = eng.current_order_usd.load(Ordering::Relaxed) as f64
                                                                        / beroun_types::PRICE_SCALE / price_f;
                                                                    let amt_f = amt_f.max(0.00015);
                                                                    let msg = format!(r#"[0,"on",null,{{"symbol":"{}","amount":"{:.5}","price":"{:.2}","type":"EXCHANGE IOC"}}]"#,
                                                                        beroun_types::TRADING_SYMBOL, amt_f, price_f);
                                                                    let _ = order_tx.send(msg);
                                                                    mask |= bit;
                                                                    eng.ghost_active_mask.store(mask, Ordering::Relaxed);
                                                                    eng.ghost_injections.fetch_add(1, Ordering::Relaxed);
                                                                    ghost_last_inject = Instant::now();
                                                                    tracing::info!(event = "ghost_inject", side = "buy", level = i,
                                                                        price = price_f, micro = micro_g as f64 / beroun_types::PRICE_SCALE,
                                                                        dist_usd = dist as f64 / beroun_types::PRICE_SCALE,
                                                                        velocity = format!("{:.2}", ghost_velocity / beroun_types::PRICE_SCALE));
                                                                } else if dist >= trigger_dist * 3 && (mask & bit) != 0 {
                                                                    // Price moved away: clear active flag
                                                                    mask &= !bit;
                                                                    eng.ghost_active_mask.store(mask, Ordering::Relaxed);
                                                                }
                                                            }
                                                            // Ghost SELL levels
                                                            let gsp = eng.ghost_sell_prices[i].load(Ordering::Relaxed);
                                                            if gsp > 0 {
                                                                let dist = (micro_g - gsp).abs();
                                                                let bit = 1u64 << (i + beroun_types::MAX_GRID_LEVELS);
                                                                if dist < trigger_dist && (mask & bit) == 0 {
                                                                    let price_f = gsp as f64 / beroun_types::PRICE_SCALE;
                                                                    let amt_f = eng.current_order_usd.load(Ordering::Relaxed) as f64
                                                                        / beroun_types::PRICE_SCALE / price_f;
                                                                    let amt_f = amt_f.max(0.00015);
                                                                    let msg = format!(r#"[0,"on",null,{{"symbol":"{}","amount":"{:.5}","price":"{:.2}","type":"EXCHANGE IOC"}}]"#,
                                                                        beroun_types::TRADING_SYMBOL, -amt_f, price_f);
                                                                    let _ = order_tx.send(msg);
                                                                    mask |= bit;
                                                                    eng.ghost_active_mask.store(mask, Ordering::Relaxed);
                                                                    eng.ghost_injections.fetch_add(1, Ordering::Relaxed);
                                                                    ghost_last_inject = Instant::now();
                                                                    tracing::info!(event = "ghost_inject", side = "sell", level = i,
                                                                        price = price_f, micro = micro_g as f64 / beroun_types::PRICE_SCALE,
                                                                        dist_usd = dist as f64 / beroun_types::PRICE_SCALE,
                                                                        velocity = format!("{:.2}", ghost_velocity / beroun_types::PRICE_SCALE));
                                                                } else if dist >= trigger_dist * 3 && (mask & bit) != 0 {
                                                                    mask &= !bit;
                                                                    eng.ghost_active_mask.store(mask, Ordering::Relaxed);
                                                                }
                                                            }
                                                        }
                                                    } else if !velocity_safe && min_inject_interval {
                                                        // Anti-toxic: velocity too high, reject injection
                                                        eng.ghost_velocity_rejects.fetch_add(1, Ordering::Relaxed);
                                                    }
                                                }
                                            }

                                            // ── SNIPER INTEL v6.2 (OBI + Dynamic Sizing) ──
                                            const MIN_TICK: i64 = 100_000_000;
                                            if best_bid > 0 && best_ask > 0 {
                                                let now = Instant::now();
                                                // v11.0: Fire interval = max(AI fire_interval, anti-flicker lifetime)
                                                let fire_ai = eng.ai_fire_interval_ms.load(Ordering::Relaxed).max(500).min(10000);
                                                let anti_flicker = eng.ai_min_order_lifetime_ms.load(Ordering::Relaxed).max(50).min(5000);
                                                let fire_interval = fire_ai.max(anti_flicker);
                                                if now.duration_since(last_upd).as_millis() > fire_interval as u128
                                                    && risk.paused.load(Ordering::Acquire) == 0 {
                                                        // ═══ L1 SWEEP FREEZE CHECK (v9.2 Hybrid Intelligence) ═══
                                                        let freeze_until = eng.sweep_freeze_until.load(Ordering::Acquire);
                                                        if freeze_until > 0 {
                                                            let now_ms_check = std::time::SystemTime::now()
                                                                .duration_since(std::time::UNIX_EPOCH).unwrap_or_default()
                                                                .as_millis() as u64;
                                                            if now_ms_check < freeze_until {
                                                                info!(event = "l1_sweep_freeze", remaining_ms = freeze_until - now_ms_check);
                                                                last_upd = now; // prevent rapid retries
                                                                continue;
                                                            } else {
                                                                eng.sweep_freeze_until.store(0, Ordering::Release);
                                                            }
                                                        }
                                                        // 1. MICRO-PRICE (volume-weighted mid from L1)
                                                        let mid_i = ((best_bid as i64) + (best_ask as i64)) / 2;
                                                        let bid_vol_0 = eng.bids[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                                                        let ask_vol_0 = eng.asks[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                                                        let total_vol_0 = bid_vol_0 + ask_vol_0;
                                                        let micro_i = if total_vol_0 > 0.0 {
                                                            ((best_bid as f64 * ask_vol_0 + best_ask as f64 * bid_vol_0) / total_vol_0).round() as i64
                                                        } else { mid_i };
                                                        let micro_f = micro_i as f64 / beroun_types::PRICE_SCALE;

                                                        // ═══ v11.0 SENTINEL: Global Fair Value ═══
                                                        // Weighted: 60% local micro + 30% Binance mid + 10% sentiment shift
                                                        let bnb_mid_raw = eng.binance_mid_price.load(Ordering::Relaxed);
                                                        let fair_value_i = if bnb_mid_raw > 0 {
                                                            let local_w = (micro_i as f64) * 0.6;
                                                            let bnb_w = (bnb_mid_raw as f64) * 0.3;
                                                            let macro_raw = eng.macro_bias.load(Ordering::Relaxed);
                                                            let macro_ts = eng.macro_source_ts.load(Ordering::Relaxed);
                                                            let sentinel_now = SystemTime::now()
                                                                .duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
                                                            let macro_shift = if sentinel_now.saturating_sub(macro_ts) < 600_000 {
                                                                micro_i as f64 * 0.001 * (macro_raw as f64 / 10000.0) * 0.1
                                                            } else { 0.0 };
                                                            let fv = local_w + bnb_w + macro_shift;
                                                            eng.global_fair_value.store(fv.round() as i64, Ordering::Relaxed);
                                                            fv.round() as i64
                                                        } else {
                                                            eng.global_fair_value.store(micro_i, Ordering::Relaxed);
                                                            micro_i
                                                        };

                                                        // Sentinel re-position: if fair value diverges > 0.05% from local
                                                        let divergence = (fair_value_i - micro_i).abs() as f64 / micro_i as f64;
                                                        if divergence > 0.0005 && bnb_mid_raw > 0 {
                                                            // Shift ghost grid center toward fair value
                                                            for lvl in 0..beroun_types::MAX_GRID_LEVELS {
                                                                let ghost_buy = eng.ghost_buy_prices[lvl].load(Ordering::Relaxed);
                                                                let ghost_sell = eng.ghost_sell_prices[lvl].load(Ordering::Relaxed);
                                                                if ghost_buy != 0 {
                                                                    let shift = ((fair_value_i - micro_i) as f64 * 0.5).round() as i64;
                                                                    eng.ghost_buy_prices[lvl].store(ghost_buy + shift, Ordering::Relaxed);
                                                                }
                                                                if ghost_sell != 0 {
                                                                    let shift = ((fair_value_i - micro_i) as f64 * 0.5).round() as i64;
                                                                    eng.ghost_sell_prices[lvl].store(ghost_sell + shift, Ordering::Relaxed);
                                                                }
                                                            }
                                                            eng.sentinel_repositions.fetch_add(1, Ordering::Relaxed);
                                                        }

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

                                                        // ═══ LIQUIDITY HOLE DETECTION (v9.5) ═══
                                                        let total_depth = sum_bid_vol + sum_ask_vol;
                                                        let depth_btc = total_depth / beroun_types::PRICE_SCALE;
                                                        depth_history.push_back(total_depth);
                                                        if depth_history.len() > 60 { depth_history.pop_front(); }
                                                        let avg_depth = if !depth_history.is_empty() {
                                                            depth_history.iter().sum::<f64>() / depth_history.len() as f64
                                                        } else { total_depth };

                                                        let liquidity_ratio = if avg_depth > 0.0 { total_depth / avg_depth } else { 1.0 };
                                                        let in_liquidity_hole = liquidity_ratio < 0.5;
                                                        let hole_recovering = liquidity_ratio >= 0.7;

                                                        if in_liquidity_hole && !was_in_hole {
                                                            tracing::warn!(event = "liquidity_hole",
                                                                depth_btc = format!("{:.4}", depth_btc),
                                                                avg_depth_btc = format!("{:.4}", avg_depth / beroun_types::PRICE_SCALE),
                                                                ratio = format!("{:.2}", liquidity_ratio));
                                                            was_in_hole = true;
                                                        } else if hole_recovering && was_in_hole {
                                                            tracing::info!(event = "liquidity_recovered",
                                                                depth_btc = format!("{:.4}", depth_btc),
                                                                ratio = format!("{:.2}", liquidity_ratio));
                                                            was_in_hole = false;
                                                        }

                                                        // 3. DYNAMIC SIZING (signal convergence)
                                                        let base_usd = risk.order_usd.load(Ordering::Acquire) as f64;
                                                        let micro_bias = micro_i - mid_i;
                                                        let final_order_usd = if in_liquidity_hole {
                                                            // Liquidity hole: reduce order size to 50%
                                                            base_usd * 0.5
                                                        } else if (obi > 0.2 && micro_bias > 0) || (obi < -0.2 && micro_bias < 0) {
                                                            (base_usd * 1.5).clamp(base_usd * 0.5, base_usd * 2.0)
                                                        } else if (obi > 0.1 && micro_bias < 0) || (obi < -0.1 && micro_bias > 0) {
                                                            (base_usd * 0.7).clamp(base_usd * 0.5, base_usd * 2.0)
                                                        } else {
                                                            base_usd
                                                        };

                                                        // 4. INVENTORY SKEW
                                                        let mut grid = risk.grid_step.load(Ordering::Acquire) as i64;
                                                        // v9.5: Liquidity hole → emergency grid widening (3×)
                                                        if in_liquidity_hole {
                                                            grid = (grid * 3).min(2_000_000_000); // Max $20
                                                        }
                                                        let current_pos = eng.net_position.load(Ordering::Acquire);
                                                        let max_pos = risk.max_inv_delta.load(Ordering::Acquire) as i64;
                                                        let inv_skew = if max_pos > 0 {
                                                            let ratio = (current_pos as f64 / max_pos as f64).clamp(-1.0, 1.0);
                                                            (-ratio * grid as f64 * 2.0).round() as i64
                                                        } else { 0 };

                                                        // 5. FINAL PRICES (with AI Safety Fuse)
                                                        let raw_bias = risk.bias_offset.load(Ordering::Acquire);
                                                        let ai_hb = eng.ai_heartbeat_ms.load(Ordering::Acquire);
                                                        let now_ms = std::time::SystemTime::now()
                                                            .duration_since(std::time::UNIX_EPOCH).unwrap_or_default()
                                                            .as_millis() as u64;
                                                        // If AI heartbeat is >30s stale, zero bias (safety fuse)
                                                        let bias = if ai_hb > 0 && now_ms.saturating_sub(ai_hb) > 30_000 {
                                                            0 // AI is dead, play safe
                                                        } else {
                                                            raw_bias
                                                        };
                                                        let final_bias = bias + inv_skew;
                                                        // ═══ L1 MICRO-SKEW (v9.2 Hybrid Intelligence) ═══
                                                        let l1_skew = eng.l1_skew_adjustment.load(Ordering::Acquire);

                                                        // ═══ v10.9 MACRO BIAS (Omniscient Predator) ═══
                                                        // macro_bias: -10000..+10000 → scaled to grid-fraction
                                                        let macro_raw = eng.macro_bias.load(Ordering::Relaxed);
                                                        let macro_ts = eng.macro_source_ts.load(Ordering::Relaxed);
                                                        let macro_stale = now_ms.saturating_sub(macro_ts) > 600_000; // >10min = stale
                                                        let macro_bias_scaled = if !macro_stale && macro_raw.abs() > 500 {
                                                            // 10% of grid × normalized bias
                                                            (grid as f64 * 0.1 * (macro_raw as f64 / 10000.0)).round() as i64
                                                        } else { 0 };

                                                        // v10.9: Binance sweep → force ghost for 5s
                                                        let bnb_sweep_ts = eng.binance_sweep_ts.load(Ordering::Relaxed);
                                                        let bnb_sweep_age_ms = now_ms.saturating_sub(bnb_sweep_ts);
                                                        if bnb_sweep_ts > 0 && bnb_sweep_age_ms < 3000 {
                                                            // Binance sweep < 3s ago → force ghost mode
                                                            let current_trans = eng.ghost_transparency.load(Ordering::Relaxed);
                                                            if current_trans > 2000 {
                                                                eng.ghost_transparency.store(1000, Ordering::Relaxed);
                                                                tracing::info!(event = "binance_ghost_trigger",
                                                                    age_ms = bnb_sweep_age_ms,
                                                                    old_transparency = current_trans);
                                                            }
                                                        }

                                                        let final_bias_with_l1 = final_bias + l1_skew + macro_bias_scaled;
                                                        let mut buy_i = (micro_i - grid + final_bias_with_l1).max(0);
                                                        let mut sell_i = (micro_i + grid + final_bias_with_l1).max(0);

                                                        // ═══ ANTI-CROSS GUARD (L0 Safety) ═══
                                                        // Prevent POSTONLY CANCELED: bid must be below best ask, ask must be above best bid
                                                        let ba_i = best_ask as i64;
                                                        let bb_i = best_bid as i64;
                                                        if buy_i >= ba_i {
                                                            buy_i = ba_i - MIN_TICK;
                                                            info!(event = "anti_cross_guard", side = "buy", clamped_to = buy_i as f64 / beroun_types::PRICE_SCALE, best_ask = ba_i as f64 / beroun_types::PRICE_SCALE);
                                                        }
                                                        if sell_i <= bb_i {
                                                            sell_i = bb_i + MIN_TICK;
                                                            info!(event = "anti_cross_guard", side = "sell", clamped_to = sell_i as f64 / beroun_types::PRICE_SCALE, best_bid = bb_i as f64 / beroun_types::PRICE_SCALE);
                                                        }
                                                        // Spread integrity: if inverted after clamping, skip cycle
                                                        if buy_i >= sell_i {
                                                            info!(event = "spread_inverted", buy = buy_i as f64 / beroun_types::PRICE_SCALE,
                                                                  sell = sell_i as f64 / beroun_types::PRICE_SCALE, grid = grid, bias = final_bias);
                                                            continue;
                                                        }

                                                        let lb = eng.last_buy_price.load(Ordering::SeqCst);
                                                        let ls = eng.last_sell_price.load(Ordering::SeqCst);
                                                        let db = (buy_i - lb).abs();
                                                        let ds = (sell_i - ls).abs();

                                                        if db >= MIN_TICK || ds >= MIN_TICK || lb == 0 {
                                                            // ═══ DAILY LOSS LIMIT — Circuit Breaker ═══
                                                            let dll = risk.daily_loss_limit.load(Ordering::Acquire) as f64 / beroun_types::PRICE_SCALE;
                                                            let r_pnl = eng.realized_pnl.load(Ordering::Acquire) as f64 / beroun_types::PRICE_SCALE;
                                                            if dll > 0.0 && r_pnl < -dll {
                                                                // EMERGENCY STOP: cancel all, pause, alert
                                                                let cancel_ids = collect_all_order_ids(eng);
                                                                if !cancel_ids.is_empty() {
                                                                    let ids_str: Vec<String> = cancel_ids.iter().map(|id| id.to_string()).collect();
                                                                    let _ = order_tx.send(format!(r#"[0,"oc_multi",null,{{"id":[{}]}}]"#, ids_str.join(",")));
                                                                }
                                                                risk.paused.store(1, Ordering::SeqCst);
                                                                notifier.alert(format!(
                                                                    "🛑 *EMERGENCY STOP*\nDaily Loss Limit reached: `${:.2}` (limit `-${:.2}`)\nAll orders cancelled. System *LOCKED*.\n_Dnes to trh vyhrál, odpočiň si, admirále._",
                                                                    r_pnl, dll
                                                                ));
                                                                info!(event = "dll_triggered", realized_pnl = r_pnl, limit = -dll);
                                                                continue;
                                                            }

                                                            // ═══ HYDRA GRID v9.0 (Multi-Level + Inventory Throttling) ═══
                                                            let grid_levels = risk.grid_size.load(Ordering::Acquire).clamp(1, beroun_types::MAX_GRID_LEVELS as u64) as usize;
                                                            let pos_ratio = if max_pos > 0 {
                                                                (current_pos as f64 / max_pos as f64).clamp(-1.0, 1.0)
                                                            } else { 0.0 };

                                                            // INVENTORY THROTTLING: asymmetric levels
                                                            let (n_buy, n_sell) = if pos_ratio > 0.8 {
                                                                (0, grid_levels)       // Hard cap long → sell only
                                                            } else if pos_ratio > 0.4 {
                                                                (1, grid_levels)       // Soft cap → 1 buy
                                                            } else if pos_ratio < -0.8 {
                                                                (grid_levels, 0)       // Hard cap short → buy only
                                                            } else if pos_ratio < -0.4 {
                                                                (grid_levels, 1)       // Soft cap → 1 sell
                                                            } else {
                                                                (grid_levels, grid_levels) // Neutral: full both sides
                                                            };

                                                            // 6a. CANCEL ALL TRACKED ORDERS
                                                            let cancel_ids = collect_all_order_ids(eng);
                                                            let oc_payload = if !cancel_ids.is_empty() {
                                                                let ids_str: Vec<String> = cancel_ids.iter().map(|id| id.to_string()).collect();
                                                                format!(r#"["oc_multi",{{"id":[{}]}}],"#, ids_str.join(","))
                                                            } else { String::new() };

                                                            // 6b. BUILD MULTI-LEVEL ORDERS (with Capital Guard)
                                                            const MIN_ORDER_BTC: f64 = 0.00015;
                                                            let w_btc = eng.wallet_btc.load(Ordering::Relaxed) as f64 / beroun_types::PRICE_SCALE;
                                                            let w_usd = eng.wallet_usd.load(Ordering::Relaxed) as f64 / beroun_types::PRICE_SCALE;
                                                            let amt = (final_order_usd / micro_f / beroun_types::PRICE_SCALE).max(MIN_ORDER_BTC);

                                                            // ═══ CAPITAL GUARD (v9.0) ═══
                                                            // Enforce authorized capital for position-INCREASING orders.
                                                            // Position-CLOSING orders (buy when short, sell when long) always allowed.
                                                            let auth_cap = risk.authorized_capital.load(Ordering::Acquire) as f64 / beroun_types::PRICE_SCALE;
                                                            let mid_price = micro_i as f64 / beroun_types::PRICE_SCALE;
                                                            let pos_f64 = current_pos as f64 / beroun_types::PRICE_SCALE;
                                                            let pos_value = pos_f64.abs() * mid_price;

                                                            // Buy cap: if short (pos < 0), buys CLOSE position → use full wallet
                                                            //          if long/neutral, buys INCREASE position → cap by auth_capital
                                                            let cap_available = if pos_f64 < 0.0 || auth_cap <= 0.0 {
                                                                w_usd // Closing short or unlimited: full wallet
                                                            } else {
                                                                (auth_cap - pos_value).max(0.0).min(w_usd)
                                                            };
                                                            // Sell cap: if long (pos > 0), sells CLOSE position → use full wallet_btc
                                                            //           if short/neutral, sells INCREASE position → cap by auth_capital
                                                            let btc_cap = if pos_f64 > 0.0 || auth_cap <= 0.0 {
                                                                w_btc // Closing long or unlimited: full BTC wallet
                                                            } else if mid_price > 0.0 {
                                                                (auth_cap / mid_price).min(w_btc)
                                                            } else {
                                                                w_btc
                                                            };

                                                            let mut order_parts: Vec<String> = Vec::with_capacity(10);
                                                            let mut total_buy_usd = 0.0;
                                                            let mut total_sell_btc = 0.0;

                                                            // BUY LEVELS (fibonacci spacing from micro-price)
                                                            // ═══ v10.6 GHOST SPLIT ═══
                                                            let ghost_trans = eng.ghost_transparency.load(Ordering::Relaxed) as f64 / 10000.0;
                                                            // n_public = how many levels are visible in orderbook
                                                            // At transparency=1.0 (100%): all public. At 0.1: only ~1 level public per side.
                                                            let n_public_buy = ((n_buy as f64 * ghost_trans).ceil() as usize).max(1).min(n_buy);
                                                            let n_public_sell = ((n_sell as f64 * ghost_trans).ceil() as usize).max(1).min(n_sell);
                                                            let ghost_mode = ghost_trans < 0.99;

                                                            // Clear old ghost prices
                                                            if ghost_mode {
                                                                for gi in 0..beroun_types::MAX_GRID_LEVELS {
                                                                    eng.ghost_buy_prices[gi].store(0, Ordering::Relaxed);
                                                                    eng.ghost_sell_prices[gi].store(0, Ordering::Relaxed);
                                                                }
                                                                eng.ghost_active_mask.store(0, Ordering::Relaxed);
                                                            }

                                                            for i in 0..n_buy {
                                                                let spacing = (grid as f64 * beroun_types::LEVEL_SPACING[i]) as i64;
                                                                let bp_i = (micro_i - spacing + final_bias).max(0).min(ba_i - MIN_TICK);
                                                                let bp = bp_i as f64 / beroun_types::PRICE_SCALE;
                                                                let cost = amt * bp;

                                                                if i < n_public_buy {
                                                                    // PUBLIC: visible in orderbook (POSTONLY)
                                                                    if total_buy_usd + cost <= cap_available * 0.95 && amt >= MIN_ORDER_BTC {
                                                                        order_parts.push(format!(
                                                                            r#"["on",{{"symbol":"{}","amount":"{:.5}","price":"{:.2}","type":"EXCHANGE LIMIT","flags":4096}}]"#,
                                                                            beroun_types::TRADING_SYMBOL, amt, bp
                                                                        ));
                                                                        total_buy_usd += cost;
                                                                    }
                                                                } else if ghost_mode {
                                                                    // GHOST: stored in shadow grid, fires on proximity
                                                                    eng.ghost_buy_prices[i].store(bp_i, Ordering::Relaxed);
                                                                }
                                                            }

                                                            // SELL LEVELS (fibonacci spacing from micro-price)
                                                            for i in 0..n_sell {
                                                                let spacing = (grid as f64 * beroun_types::LEVEL_SPACING[i]) as i64;
                                                                let sp_i = (micro_i + spacing + final_bias).max(0).max(bb_i + MIN_TICK);
                                                                let sp = sp_i as f64 / beroun_types::PRICE_SCALE;

                                                                if i < n_public_sell {
                                                                    // PUBLIC: visible in orderbook (POSTONLY)
                                                                    if total_sell_btc + amt <= btc_cap * 0.95 && amt >= MIN_ORDER_BTC {
                                                                        order_parts.push(format!(
                                                                            r#"["on",{{"symbol":"{}","amount":"{:.5}","price":"{:.2}","type":"EXCHANGE LIMIT","flags":4096}}]"#,
                                                                            beroun_types::TRADING_SYMBOL, -amt, sp
                                                                        ));
                                                                        total_sell_btc += amt;
                                                                    }
                                                                } else if ghost_mode {
                                                                    // GHOST: stored in shadow grid, fires on proximity
                                                                    eng.ghost_sell_prices[i].store(sp_i, Ordering::Relaxed);
                                                                }
                                                            }

                                                            if order_parts.is_empty() {
                                                                info!(event = "hydra_skipped", reason = "no_valid_levels",
                                                                      wallet_btc = w_btc, wallet_usd = w_usd, pos_ratio = format!("{:.2}", pos_ratio));
                                                                continue;
                                                            }

                                                            let orders_str = order_parts.join(",");
                                                            let msg = format!(r#"[0,"ox_multi",null,[{}{}]]"#, oc_payload, orders_str);
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
                                                            info!(event = "hydra_fire", bid = best_bid, ask = best_ask,
                                                                  micro = micro_i, levels_buy = n_buy, levels_sell = n_sell,
                                                                  obi = format!("{:.3}", obi),
                                                                  order_usd = final_order_usd as f64 / beroun_types::PRICE_SCALE,
                                                                  inv_skew = inv_skew, position = current_pos,
                                                                  pos_ratio = format!("{:.2}", pos_ratio),
                                                                  dynamic_grid = grid, total_orders = order_parts.len(),
                                                                  ghost_mode = ghost_mode,
                                                                  public_buy = n_public_buy, public_sell = n_public_sell,
                                                                  ghost_buy = if ghost_mode { n_buy - n_public_buy } else { 0 },
                                                                  ghost_sell = if ghost_mode { n_sell - n_public_sell } else { 0 });
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
                                },
                                OpCode::Ping => { let _ = mdata_read.write_frame(fastwebsockets::Frame::pong(frame.payload)).await; }
                                OpCode::Close => { info!(event = "mdata_socket_closed"); shutdown_reason = "mdata_closed"; should_reconnect = true; },
                                _ => {} // Ping/Pong/Binary
                            }
                        }
                        Ok(Err(e)) => { info!(event = "mdata_read_error", error = %e); shutdown_reason = "mdata_error"; should_reconnect = true; }
                        Err(_) => { info!(event = "mdata_timeout_15s"); shutdown_reason = "mdata_timeout"; should_reconnect = true; }
                    }
                }
            }
        }

        // ═══ RECONNECT CLEANUP ═══
        info!(event = "reconnecting", reason = shutdown_reason);
        exec_handle.abort();

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
        zero_all_order_slots(eng);

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
    let active_ids = collect_all_order_ids(eng);

    let cancel_msg = if !active_ids.is_empty() {
        let ids_str: Vec<String> = active_ids.iter().map(|id| id.to_string()).collect();
        info!(event = "shutdown_cancel_targeted", count = active_ids.len(), ids = ?active_ids);
        format!(r#"[0,"oc_multi",null,{{"id":[{}]}}]"#, ids_str.join(","))
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
async fn connect_ws() -> Result<fastwebsockets::FragmentCollector<hyper_util::rt::tokio::TokioIo<hyper::upgrade::Upgraded>>, Box<dyn std::error::Error + Send + Sync>> {
    let tcp = tokio::net::TcpStream::connect("api.bitfinex.com:443").await?;
    tcp.set_nodelay(true)?;
    let connector = tokio_native_tls::TlsConnector::from(
        native_tls::TlsConnector::new().map_err(std::io::Error::other)?
    );
    let tls = connector.connect("api.bitfinex.com", tcp).await
        .map_err(std::io::Error::other)?;
        
    let req = Request::builder()
        .method("GET")
        .uri("wss://api.bitfinex.com/ws/2")
        .header(HOST, "api.bitfinex.com")
        .header(UPGRADE, "websocket")
        .header(CONNECTION, "upgrade")
        .header("Sec-WebSocket-Key", fastwebsockets::handshake::generate_key())
        .header("Sec-WebSocket-Version", "13")
        .body(http_body_util::Empty::<hyper::body::Bytes>::new())
        .map_err(std::io::Error::other)?;

    let (ws, _) = fastwebsockets::handshake::client(&SpawnExecutor, req, tls).await
        .map_err(std::io::Error::other)?;
        
    Ok(fastwebsockets::FragmentCollector::new(ws))
}
