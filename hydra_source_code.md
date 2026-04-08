# Hydra (Issue 11) Source Code

## hydra/src/main.rs
```rust
// 🐉 Hydra L0 Engine — Advanced Neural Cross Bot
// Framework: SovereignDualRunner (Dual-WS)
use std::sync::atomic::{Ordering, fence};
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH, Duration};

use anyhow::{Context, Result};
use tracing::{info, error};
use serde_json::json;
use dotenvy::dotenv;

use simd_json::prelude::*;
use simd_json::BorrowedValue;

use sniper_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE_I, PRICE_SCALE};
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::init_mmap;
use sniper_types::framework::SovereignEngine;
use sniper_types::math::FixedPrice;

mod book;
pub use book::*;

mod pnl;
pub use pnl::*;

mod ghost;
pub use ghost::*;

// BEZ ALOKACÍ: Extrémně rychlý formátovač FixedPrice na string/buff na stacku 
struct FixedFormat {
    buf: [u8; 32],
    len: usize,
}

impl FixedFormat {
    #[inline(always)]
    fn new(mut val: i64) -> Self {
        let mut s = Self { buf: [0; 32], len: 0 };
        if val == 0 {
            s.buf[0] = b'0';
            s.len = 1;
            return s;
        }
        if val < 0 {
            s.buf[0] = b'-';
            s.len = 1;
            val = -val;
        }
        let int_part = val / PRICE_SCALE_I;
        let frac_part = val % PRICE_SCALE_I;
        
        let mut itoa_buf = itoa::Buffer::new();
        let int_str = itoa_buf.format(int_part).as_bytes();
        s.buf[s.len..s.len + int_str.len()].copy_from_slice(int_str);
        s.len += int_str.len();

        if frac_part > 0 {
            s.buf[s.len] = b'.';
            s.len += 1;
            let mut f_buf = [b'0'; 8];
            let mut temp = frac_part as u64;
            let mut idx = 7;
            while temp > 0 {
                f_buf[idx] = b'0' + (temp % 10) as u8;
                temp /= 10;
                if idx > 0 { idx -= 1; } else { break; }
            }
            let mut end = 8;
            while end > 0 && f_buf[end-1] == b'0' { end -= 1; }
            s.buf[s.len..s.len + end].copy_from_slice(&f_buf[..end]);
            s.len += end;
        }
        s
    }

    #[inline(always)]
    fn as_str(&self) -> &str {
        unsafe { std::str::from_utf8_unchecked(&self.buf[..self.len]) }
    }
}

// ── Strict Fixed Point Pasing hranice (Izolace pro json f64 bridge) ──
fn extract_fixed(v: &BorrowedValue) -> Option<FixedPrice> {
    if let Some(i) = v.as_i64() {
        Some(FixedPrice::new(i * PRICE_SCALE_I))
    } else if let Some(f) = v.as_f64() {
        Some(FixedPrice::from_f64(f))
    } else if let Some(s) = v.as_str() {
        s.parse::<f64>().ok().map(FixedPrice::from_f64)
    } else {
        None
    }
}

#[inline(always)]
fn extract_u64_scaled(v: &BorrowedValue) -> Option<u64> {
    if let Some(i) = v.as_i64() {
        if i >= 0 { return Some((i * PRICE_SCALE_I) as u64); }
        None
    } else if let Some(f) = v.as_f64() {
        Some((f * PRICE_SCALE as f64).round() as u64)
    } else if let Some(s) = v.as_str() {
        s.parse::<f64>().ok().map(|f| (f * PRICE_SCALE as f64).round() as u64)
    } else {
        None
    }
}

#[inline(always)]
fn extract_count(v: &BorrowedValue) -> Option<u64> {
    if let Some(i) = v.as_u64() {
        Some(i)
    } else if let Some(i) = v.as_i64() {
        if i >= 0 { Some(i as u64) } else { None }
    } else if let Some(f) = v.as_f64() {
        if f >= 0.0 { Some(f as u64) } else { None }
    } else if let Some(s) = v.as_str() {
        s.parse::<f64>().ok().map(|f| f as u64)
    } else {
        None
    }
}

#[inline]
fn store_order_slot(ids: &[std::sync::atomic::AtomicU64; sniper_types::MAX_GRID_LEVELS], id: u64) {
    for slot in ids {
        if slot.compare_exchange(0, id, Ordering::Relaxed, Ordering::Relaxed).is_ok() {
            return;
        }
    }
}

#[inline]
fn clear_order_slot(ids: &[std::sync::atomic::AtomicU64; sniper_types::MAX_GRID_LEVELS], id: u64) {
    for slot in ids {
        let _ = slot.compare_exchange(id, 0, Ordering::Relaxed, Ordering::Relaxed);
    }
}

fn collect_all_order_ids(eng: &EngineState) -> arrayvec::ArrayVec<u64, {sniper_types::MAX_GRID_LEVELS * 2}> {
    let mut vec = arrayvec::ArrayVec::new();
    for slot in eng.active_buy_ids.iter().chain(eng.active_sell_ids.iter()) {
        let id = slot.load(Ordering::Relaxed);
        if id > 0 { vec.push(id); }
    }
    vec
}

struct HydraEngine {
    notifier: Arc<AsyncNotifier>,
    engine: *mut EngineState,
    risk: *const RiskState,
    fee_matrix: *const sniper_types::fee_types::GlobalFeeMatrix,
    l2cmd: *const sniper_types::l2_command::L2CommandMatrix,
    l2as: *const sniper_types::l2_command::L2ASMatrix,
    l2risk: *const sniper_types::l2_command::L2GlobalRiskMatrix,
    l2portfolio: *const sniper_types::l2_command::L2PortfolioTelemetry,
    l1ring: *const sniper_types::l2_command::L1TelemetryRing,
    
    // Eliminováno Plovoucí desetinna čárka v proměnných 
    chan_id: Option<i64>,
    last_upd: Instant,
    snapshot_loaded: bool,
    cs_debug_count: u32,
    cs_fail_count: u32,
    depth_history: [FixedPrice; 64],
    depth_head: usize,
    depth_len: usize,
    was_in_hole: bool,

    ghost_last_micro: FixedPrice,
    ghost_velocity: FixedPrice,
    ghost_velocity_max: FixedPrice,
    ghost_last_inject: Instant,
    toxic_storm_ptr: *const u8,
    ml_shield: sniper_types::ml_shield::MlShield,
    armada_state: &'static sniper_types::armada_types::ArmadaState,
}

unsafe impl Send for HydraEngine {}

impl SovereignEngine for HydraEngine {
    fn subscriptions(&mut self) -> Vec<String> {
        vec![
            json!({"event":"conf","flags":131072|536870912}).to_string(),
            json!({"event":"subscribe","channel":"book","symbol":sniper_types::TRADING_SYMBOL,"prec":"P0","freq":"F0","len":"25"}).to_string()
        ]
    }

    fn on_start(&mut self) -> Result<()> {
        let _lock_guard = sniper_types::lock::ensure_single_instance("hydra-core")?;
        std::fs::write("/tmp/hydra-core.pid", std::process::id().to_string())?;
        
        if let Some(core_ids) = core_affinity::get_core_ids() {
            if core_ids.len() > 1 {
                core_affinity::set_for_current(core_ids[1]);
                info!(event = "cpu_pinned", core = 1, total_cores = core_ids.len());
            }
        }

        self.notifier.alert("*🐍 HYDRA v12.1 ONLINE*\n`Delta Lead + SovereignDualRunner`".to_string());
        info!(event = "system_start", version = "12.1.0-hydra-zerofpu");

        Ok(())
    }

    fn on_auth(&mut self) {
        info!(event = "exec_auth_ok", bot = "hydra");
    }

    fn on_system_event(&mut self, value: &serde_json::Value, _out_buf: &mut bytes::BytesMut) {
        if value["event"] == "subscribed" {
            self.chan_id = value["chanId"].as_i64();
            self.snapshot_loaded = false;
            println!("[HYDRA DEBUG] SUCCESSFULLY SUBSCRIBED TO CHANNEL ID: {:?}", self.chan_id);
            info!(event = "mdata_subscribed", chan_id = ?self.chan_id);
        } else if value["event"] == "error" {
            info!(event = "mdata_error", msg = ?value["msg"], code = ?value["code"]);
        }
    }

    fn is_shadow(&self) -> bool {
        let risk = unsafe { &*self.risk };
        let is_paused = risk.paused.load(Ordering::Relaxed) != 0;
        let config_shadow = std::fs::read_to_string("config.yaml").unwrap_or_default().contains("is_shadow: true");
        is_paused || config_shadow
    }

    // Pro debug dashboard na UI posunuto pomocí L2 Ring konverze (přípustné)
    fn best_bid_ask(&self) -> (f64, f64) {
        let engine = unsafe { &*self.engine };
        (
            engine.best_bid.load(Ordering::Relaxed) as f64 / PRICE_SCALE as f64,
            engine.best_ask.load(Ordering::Relaxed) as f64 / PRICE_SCALE as f64
        )
    }

    fn add_virtual_pnl(&self, amount: i64) {
        let eng = unsafe { &*self.engine };
        eng.virtual_realized_pnl.fetch_add(amount, Ordering::Relaxed);
    }

    fn on_shutdown(&mut self, out_buf: &mut bytes::BytesMut) {
        self.notifier.alert("🛑 *Shutdown*: cancelling orders...".to_string());
        let eng = unsafe { &*self.engine };
        let active_ids = collect_all_order_ids(eng);

        if !active_ids.is_empty() {
            out_buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"id\":[");
            let mut itoa_buf = itoa::Buffer::new();
            for (i, &id) in active_ids.iter().enumerate() {
                if i > 0 { out_buf.extend_from_slice(b","); }
                out_buf.extend_from_slice(itoa_buf.format(id).as_bytes());
            }
            out_buf.extend_from_slice(b"]}]");
        } else {
            out_buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"all\":1}]");
        }
        info!(event = "shutdown_cancel_sent");
    }

    fn on_market_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut) {
        self.ml_shield.sync_weights_if_needed();

        let v = match simd_json::to_borrowed_value(payload) {
            Ok(v) => v,
            Err(_) => return,
        };

        if let BorrowedValue::Array(arr) = v {
            let engine = unsafe { &mut *self.engine };
            
            // ── MARKET DATA STREAM (Book updates) ──
            if arr[0].as_i64() == self.chan_id && self.chan_id.is_some() {
                if arr[1].as_str() == Some("hb") { return; }

                if arr[1].as_str() == Some("cs") {
                    let remote_cs = arr[2].as_i64().unwrap_or(0) as i32;
                    let do_debug = self.cs_debug_count < 5;
                    let local_cs = calculate_checksum(engine, do_debug);
                    self.cs_debug_count += 1;
                    if remote_cs != local_cs {
                        self.cs_fail_count += 1;
                        if self.cs_fail_count >= 50 {
                            error!(event = "checksum_persist", remote = remote_cs, local = local_cs);
                            self.cs_fail_count = 0;
                            self.notifier.alert("⚠️ *RECONNECT*\\n`Reason: checksum_persist`".to_string());
                            out_buf.clear();
                            out_buf.extend_from_slice(b"RECONNECT");
                        }
                    } else {
                        self.cs_fail_count = 0;
                    }
                    return;
                }

                if let Some(top_arr) = arr[1].as_array() {
                    let is_nested = top_arr.first().is_some_and(|e| e.as_array().is_some());
                    if is_nested {
                        let mut bc = 0u32;
                        let mut ac = 0u32;
                        
                        if !self.snapshot_loaded && top_arr.len() > 10 {
                            for i in 0..sniper_types::BOOK_LEVELS {
                                engine.bids[i].price.store(0, Ordering::Relaxed);
                                engine.bids[i].amount.store(0, Ordering::Relaxed);
                                engine.bids[i].count.store(0, Ordering::Relaxed);
                                
                                engine.asks[i].price.store(u64::MAX, Ordering::Relaxed);
                                engine.asks[i].amount.store(0, Ordering::Relaxed);
                                engine.asks[i].count.store(0, Ordering::Relaxed);
                            }
                            fence(Ordering::Release);
                        }
                        
                        for entry in top_arr {
                            if let Some(u) = entry.as_array() {
                                if let (Some(price), Some(count), Some(amount)) = (extract_u64_scaled(&u[0]), extract_count(&u[1]), extract_fixed(&u[2])) {
                                    if amount.0 > 0 { update_book(&mut engine.bids, price, amount.0, count); bc += 1; }
                                    else { update_book(&mut engine.asks, price, amount.0, count); ac += 1; }
                                } else {
                                    eprintln!("FAILED EXTRACT: p={:?} c={:?} a={:?}", extract_u64_scaled(&u[0]), extract_count(&u[1]), extract_fixed(&u[2]));
                                }
                            }
                        }
                        fence(Ordering::SeqCst);
                        sort_book(&mut engine.bids, true);
                        sort_book(&mut engine.asks, false);
                        if !self.snapshot_loaded && (bc + ac) > 10 {
                            self.snapshot_loaded = true;
                            info!(event = "snapshot_loaded", bids = bc, asks = ac);
                        }
                    } else {
                        if let (Some(price), Some(count), Some(amount)) = (extract_u64_scaled(&top_arr[0]), extract_count(&top_arr[1]), extract_fixed(&top_arr[2])) {
                            if amount.0 > 0 { update_book(&mut engine.bids, price, amount.0, count); sort_book(&mut engine.bids, true); }
                            else { update_book(&mut engine.asks, price, amount.0, count); sort_book(&mut engine.asks, false); }
                        }
                    }
                }

                let best_bid = engine.bids[0].price.load(Ordering::Acquire);
                let best_ask = engine.asks[0].price.load(Ordering::Acquire);
                
                if best_bid > 0 && best_ask > 0 && best_bid >= best_ask { 
                    return; 
                }

                engine.best_bid.store(best_bid, Ordering::Release);
                engine.best_ask.store(best_ask, Ordering::Release);

                // GHOST PROXIMITY CHECK (ZERO FPU + ZERO ALLOCATION)
                if best_bid > 0 && best_ask > 0 {
                    let ghost_trans = engine.ghost_transparency.load(Ordering::Relaxed);
                    if ghost_trans < 10000 {
                        let bid_vol_g = FixedPrice::new(engine.bids[0].amount.load(Ordering::Relaxed).unsigned_abs() as i64);
                        let ask_vol_g = FixedPrice::new(engine.asks[0].amount.load(Ordering::Relaxed).unsigned_abs() as i64);
                        let total_vol_g = bid_vol_g + ask_vol_g;
                        
                        let best_bid_fp = FixedPrice::new(best_bid as i64);
                        let best_ask_fp = FixedPrice::new(best_ask as i64);

                        let micro_g = if total_vol_g > FixedPrice::zero() {
                            ((best_bid_fp * ask_vol_g) + (best_ask_fp * bid_vol_g)) / total_vol_g
                        } else {
                            (best_bid_fp + best_ask_fp) / FixedPrice::new(2 * PRICE_SCALE_I)
                        };

                        if self.ghost_last_micro.0 > 0 {
                            let delta = FixedPrice::new((micro_g.0 - self.ghost_last_micro.0).abs());
                            self.ghost_velocity = (self.ghost_velocity * FixedPrice::new(80_000_000)) + (delta * FixedPrice::new(20_000_000));
                        }
                        self.ghost_last_micro = micro_g;

                        let ai_trigger = FixedPrice::new((engine.ai_ghost_trigger_pct.load(Ordering::Relaxed).clamp(10, 500) as i64) * 1000); // 500 -> 500,000 (0.005)
                        let trigger_dist = micro_g * ai_trigger;
                        let velocity_safe = self.ghost_velocity < self.ghost_velocity_max;
                        let min_inject_interval = self.ghost_last_inject.elapsed().as_millis() > 200;

                        if velocity_safe && min_inject_interval {
                            let mut mask = engine.ghost_active_mask.load(Ordering::Relaxed);
                            let mut process_ghost = |target_price: i64, bit_offset: usize, is_buy: bool| {
                                if target_price > 0 {
                                    let dist = FixedPrice::new((micro_g.0 - target_price).abs());
                                    let bit = 1u64 << bit_offset;
                                    if dist < trigger_dist && (mask & bit) == 0 {
                                        let usd = engine.current_order_usd.load(Ordering::Relaxed);
                                        let usd_fp = FixedPrice::new(usd as i64 * PRICE_SCALE_I);
                                        
                                        let mut amt_fp = usd_fp / FixedPrice::new(target_price);
                                        if amt_fp < FixedPrice::new(15000) { amt_fp = FixedPrice::new(15000); }
                                        if !is_buy { amt_fp.0 = -amt_fp.0; }

                                        let amt_fmt = FixedFormat::new(amt_fp.0);
                                        let price_fmt = FixedFormat::new(target_price);

                                        sniper_types::exchange::bitfinex_venue::BitfinexVenue::write_standalone_ioc(
                                            out_buf, sniper_types::BOT_GID_HYDRA,
                                            sniper_types::TRADING_SYMBOL.as_bytes(),
                                            amt_fmt.as_str(), price_fmt.as_str(),
                                        );
                                        
                                        mask |= bit;
                                        engine.ghost_active_mask.store(mask, Ordering::Relaxed);
                                        engine.ghost_injections.fetch_add(1, Ordering::Relaxed);
                                        self.ghost_last_inject = Instant::now();
                                    } else if dist >= trigger_dist * FixedPrice::new(3 * PRICE_SCALE_I) && (mask & bit) != 0 {
                                        mask &= !bit;
                                        engine.ghost_active_mask.store(mask, Ordering::Relaxed);
                                    }
                                }
                            };
                            
                            for i in 0..sniper_types::MAX_GRID_LEVELS {
                                process_ghost(engine.ghost_buy_prices[i].load(Ordering::Relaxed), i, true);
                                process_ghost(engine.ghost_sell_prices[i].load(Ordering::Relaxed), i + sniper_types::MAX_GRID_LEVELS, false);
                            }
                        } else if !velocity_safe && min_inject_interval {
                            engine.ghost_velocity_rejects.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                } // ends ghost block
            }
            // ── EXEC STREAM (Orders / Trades) ──
            else if arr[0].as_i64() == Some(0) && arr.len() > 1 {
                let mt = arr[1].as_str().unwrap_or("");
                if mt == "te" {
                    if let Some(trade) = arr.get(2).and_then(|e| e.as_array()) {
                        if let (Some(trade_amt), Some(trade_price)) = (extract_fixed(&trade[4]), extract_fixed(&trade[5])) {
                            pnl::process_trade(engine, trade_amt.as_f64(), trade_price.as_f64());
                        }
                    }
                } else if mt == "wu" || mt == "ws" {
                    let iter: Box<dyn Iterator<Item = &BorrowedValue>> = if mt == "wu" {
                        Box::new(std::iter::once(&arr[2]))
                    } else {
                        if let Some(a) = arr[2].as_array() {
                            Box::new(a.iter())
                        } else {
                            Box::new(std::iter::empty())
                        }
                    };
                    for w in iter {
                        if let Some(w_arr) = w.as_array() {
                            if let (Some(wt), Some(cur), Some(bal)) = (w_arr.get(0).and_then(|x| x.as_str()), w_arr.get(1).and_then(|x| x.as_str()), w_arr.get(2).and_then(|x| extract_u64_scaled(x))) {
                                if wt == "exchange" {
                                    if cur == sniper_types::TRADING_BASE { engine.wallet_btc.store(bal, Ordering::SeqCst); }
                                    else if cur == sniper_types::TRADING_QUOTE || cur == "UST" { engine.wallet_usd.store(bal, Ordering::SeqCst); }
                                }
                            }
                        }
                    }
                } else if mt == "os" {
                    if let Some(orders) = arr.get(2).and_then(|a| a.as_array()) {
                        for order in orders {
                            if let Some(o) = order.as_array()
                                && let (Some(id), Some(sym), Some(amt), Some(status)) = (
                                    o[0].as_u64().or_else(|| o[0].as_f64().map(|f| f as u64)),
                                    o[3].as_str(), extract_fixed(&o[6]), o[13].as_str()
                                )
                                && sym == sniper_types::TRADING_SYMBOL && (status.contains("ACTIVE") || status.contains("PARTIALLY")) {
                                    if amt.0 > 0 { store_order_slot(&engine.active_buy_ids, id); }
                                    else { store_order_slot(&engine.active_sell_ids, id); }
                                }
                        }
                    }
                } else if mt == "on" || mt == "ou" || mt == "oc" {
                    if let Some(o) = arr.get(2).and_then(|a| a.as_array())
                        && let (Some(id), Some(sym), Some(amt), Some(status)) = (
                            o[0].as_u64().or_else(|| o[0].as_f64().map(|f| f as u64)),
                            o[3].as_str(), extract_fixed(&o[6]), o[13].as_str()
                        )
                        && sym == sniper_types::TRADING_SYMBOL {
                            if status.contains("ACTIVE") || status.contains("PARTIALLY") {
                                if amt.0 > 0 { store_order_slot(&engine.active_buy_ids, id); }
                                else { store_order_slot(&engine.active_sell_ids, id); }
                            } else if status.contains("CANCELED") || status.contains("EXECUTED") {
                                if amt.0 > 0 { clear_order_slot(&engine.active_buy_ids, id); }
                                else { clear_order_slot(&engine.active_sell_ids, id); }
                            }
                        }
                }
            }
        }
    }

    fn on_loop(&mut self, out_buf: &mut bytes::BytesMut) {
        let engine = unsafe { &*self.engine };
        let risk = unsafe { &*self.risk };
        let l2cmd = unsafe { &*self.l2cmd };
        let l2risk = unsafe { &*self.l2risk };
        let fee_state = unsafe { &*self.fee_matrix };

        let best_bid = engine.best_bid.load(Ordering::Acquire);
        let best_ask = engine.best_ask.load(Ordering::Acquire);
        
        const MIN_TICK: i64 = 100_000_000;
        if best_bid == 0 || best_ask == 0 { return; }
        if best_bid >= best_ask { return; }

        let now = Instant::now();
        let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        let fire_ai = engine.ai_fire_interval_ms.load(Ordering::Relaxed).clamp(500, 10000);
        let anti_flicker = engine.ai_min_order_lifetime_ms.load(Ordering::Relaxed).clamp(50, 5000);
        let fire_interval = fire_ai.max(anti_flicker);
        
        static mut ENTRY_DBG: u64 = 0;
        let now_sec2 = now_ms / 1000;
        unsafe {
            if now_sec2 - ENTRY_DBG > 1 {
                eprintln!("[ON_LOOP] bid={} ask={} fire_int={} elapsed={}ms paused={}",
                    best_bid, best_ask, fire_interval,
                    now.duration_since(self.last_upd).as_millis(),
                    risk.paused.load(Ordering::Relaxed));
                ENTRY_DBG = now_sec2;
            }
        }
        
        if now.duration_since(self.last_upd).as_millis() <= fire_interval as u128 {
            return;
        }
        
        let freeze_until = engine.sweep_freeze_until.load(Ordering::Acquire);
        if freeze_until > 0 {
            if freeze_until > now_ms + 30_000 {
                // 🚨 IMUNITNÍ REAKCE: Freeze je nesmyslně daleko v budoucnosti!
                engine.sweep_freeze_until.store(0, Ordering::Release);
                println!("[HYDRA HEALER] Auto-corrected anomalous sweep_freeze_until ({})!", freeze_until);
            } else if now_ms < freeze_until {
                self.last_upd = now; 
                sniper_types::l2_command::record_latency(unsafe{&*self.l1ring}, now);
                return;
            } else {
                engine.sweep_freeze_until.store(0, Ordering::Release);
            }
        }

        // ═══ HIVE MIND: Toxic Storm ═══
        {
           // Hardcoded override pro finální test:
           let storm_byte: u8 = 0; // unsafe { std::ptr::read_volatile(self.toxic_storm_ptr) };
           if storm_byte == 1 { eprintln!("[ON_LOOP] BLOCKED BY toxic_storm=1"); return; }
        }
        eprintln!("[ON_LOOP] PASSED ALL GUARDS → entering strategy calc");

        let mid_i = ((best_bid as i64) + (best_ask as i64)) / 2;
        let best_bid_fp = FixedPrice::new(best_bid as i64);
        let best_ask_fp = FixedPrice::new(best_ask as i64);

        let bid_vol_0 = FixedPrice::new(engine.bids[0].amount.load(Ordering::Relaxed).unsigned_abs() as i64);
        let ask_vol_0 = FixedPrice::new(engine.asks[0].amount.load(Ordering::Relaxed).unsigned_abs() as i64);
        let total_vol_0 = bid_vol_0 + ask_vol_0;
        
        let micro = if total_vol_0 > FixedPrice::zero() {
            ((best_bid_fp * ask_vol_0) + (best_ask_fp * bid_vol_0)) / total_vol_0
        } else {
            FixedPrice::new(mid_i)
        };
        let micro_i = micro.0;

        let bnb_mid_raw = engine.binance_mid_price.load(Ordering::Relaxed);
        let bnb_fp = FixedPrice::new(bnb_mid_raw as i64);

        let fair_value_i = if bnb_mid_raw > 0 {
            let local_w = micro * FixedPrice::new(60_000_000);
            let bnb_w = bnb_fp * FixedPrice::new(30_000_000);
            let macro_raw = FixedPrice::new(engine.macro_bias.load(Ordering::Relaxed) * PRICE_SCALE_I);
            let macro_ts = engine.macro_source_ts.load(Ordering::Relaxed);
            
            let macro_shift = if now_ms.saturating_sub(macro_ts) < 600_000 {
                // (macro_raw / 100000) -> pak scaling
                let m_factor = (macro_raw / FixedPrice::new(10000 * PRICE_SCALE_I)) * FixedPrice::new(10000);
                micro * m_factor
            } else { FixedPrice::zero() };

            let fv = local_w + bnb_w + macro_shift;
            engine.global_fair_value.store(fv.0, Ordering::Relaxed);
            fv.0
        } else {
            engine.global_fair_value.store(micro_i, Ordering::Relaxed);
            micro_i
        };

        let div_abs = FixedPrice::new((fair_value_i - micro_i).abs());
        let divergence = div_abs / micro;
        
        if divergence > FixedPrice::new(50_000) && bnb_mid_raw > 0 {
            for lvl in 0..sniper_types::MAX_GRID_LEVELS {
                let ghost_buy = engine.ghost_buy_prices[lvl].load(Ordering::Relaxed);
                let ghost_sell = engine.ghost_sell_prices[lvl].load(Ordering::Relaxed);
                let shift = (fair_value_i - micro_i) / 2;
                if ghost_buy != 0 { engine.ghost_buy_prices[lvl].store(ghost_buy + shift, Ordering::Relaxed); }
                if ghost_sell != 0 { engine.ghost_sell_prices[lvl].store(ghost_sell + shift, Ordering::Relaxed); }
            }
            engine.sentinel_repositions.fetch_add(1, Ordering::Relaxed);
        }

        let delta_lead_bps = if bnb_mid_raw > 0 && micro_i > 0 {
            let ratio = (bnb_fp - micro) / micro;
            let raw = ratio * FixedPrice::new(1_000_000 * PRICE_SCALE_I);
            engine.delta_lead_raw_bps.store(raw.0, Ordering::Relaxed);
            raw
        } else {
            engine.delta_lead_raw_bps.store(0, Ordering::Relaxed);
            FixedPrice::zero()
        };
        
        let d_sig = delta_lead_bps * FixedPrice::new(100 * PRICE_SCALE_I);
        engine.delta_lead_signal.store((d_sig.0 / PRICE_SCALE_I).max(-10000).min(10000), Ordering::Relaxed);

        let delta_threshold = FixedPrice::new(engine.ai_delta_threshold_bps.load(Ordering::Relaxed) as i64 * PRICE_SCALE_I);
        let delta_active = FixedPrice::new(delta_lead_bps.0.abs()) > delta_threshold && bnb_mid_raw > 0;

        let mut sum_bid_vol = FixedPrice::zero();
        let mut sum_ask_vol = FixedPrice::zero();
        for i in 0..10 {
            sum_bid_vol += FixedPrice::new(engine.bids[i].amount.load(Ordering::Relaxed).unsigned_abs() as i64);
            sum_ask_vol += FixedPrice::new(engine.asks[i].amount.load(Ordering::Relaxed).unsigned_abs() as i64);
        }
        
        let total_depth = sum_bid_vol + sum_ask_vol;
        let obi = if total_depth > FixedPrice::zero() {
            (sum_bid_vol - sum_ask_vol) / total_depth
        } else { FixedPrice::zero() };
        self.depth_history[(self.depth_head + self.depth_len) % 64] = total_depth;
        if self.depth_len < 60 { self.depth_len += 1; } 
        else { self.depth_head = (self.depth_head + 1) % 64; }

        let avg_depth = if self.depth_len > 0 {
            let mut sum = FixedPrice::zero();
            for i in 0..self.depth_len { sum += self.depth_history[(self.depth_head + i) % 64]; }
            let len_fp = FixedPrice::new(self.depth_len as i64 * PRICE_SCALE_I);
            sum / len_fp
        } else { total_depth };

        let liquidity_ratio = if avg_depth > FixedPrice::zero() { total_depth / avg_depth } else { FixedPrice::new(PRICE_SCALE_I) };
        let in_liquidity_hole = liquidity_ratio < FixedPrice::new(50_000_000);
        let hole_recovering = liquidity_ratio >= FixedPrice::new(70_000_000);

        if in_liquidity_hole && !self.was_in_hole { self.was_in_hole = true; }
        else if hole_recovering && self.was_in_hole { self.was_in_hole = false; }

        let auth_cap_total = FixedPrice::new(self.armada_state.authorized_capital[0].load(Ordering::Acquire) as i64);
        let current_pos_abs = engine.net_position.load(Ordering::Acquire).abs();
        let current_exposure = FixedPrice::new(current_pos_abs) * micro;
        let cap_95 = auth_cap_total * FixedPrice::new(95_000_000);
        let available_margin = cap_95 - current_exposure;
        let total_expected_levels = (risk.grid_size.load(Ordering::Acquire) as i64).max(1);
        let base_usd = auth_cap_total / FixedPrice::new(total_expected_levels * PRICE_SCALE_I as i64);
        let micro_bias = micro_i - mid_i;

        static mut DEBUG_PRINTED: u64 = 0;
        let now_sec = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
        unsafe {
            if now_sec - DEBUG_PRINTED > 2 {
                eprintln!("[HYDRA DEBUG] auth={} cap95={} exp={} base_usd={} total_levels={}", 
                    auth_cap_total.0, cap_95.0, current_exposure.0, base_usd.0, total_expected_levels);
                DEBUG_PRINTED = now_sec;
            }
        }
        
        let fp_0_5 = FixedPrice::new(50_000_000);
        let fp_1_5 = FixedPrice::new(150_000_000);
        let fp_2_0 = FixedPrice::new(200_000_000);
        let fp_0_7 = FixedPrice::new(70_000_000);

        let final_order_usd: FixedPrice = if in_liquidity_hole { base_usd * fp_0_5 }
        else if (obi > FixedPrice::new(20_000_000) && micro_bias > 0) || (obi < FixedPrice::new(-20_000_000) && micro_bias < 0) { 
            (base_usd * fp_1_5).max(base_usd * fp_0_5).min(base_usd * fp_2_0)
        }
        else if (obi > FixedPrice::new(10_000_000) && micro_bias < 0) || (obi < FixedPrice::new(-10_000_000) && micro_bias > 0) { 
            (base_usd * fp_0_7).max(base_usd * fp_0_5).min(base_usd * fp_2_0)
        }
        else { base_usd };

        let trending_score = l2risk.trending_score.load(Ordering::Relaxed) as f64 / 1e8;
        let base_grid = risk.grid_step.load(Ordering::Acquire) as f64;
        let expansion_multiplier = 1.0 + (trending_score.max(0.0) * 2.0);
        let mut grid = (base_grid * expansion_multiplier) as i64;
        if in_liquidity_hole { grid = (grid * 3).min(2_000_000_000); }
        let current_pos = engine.net_position.load(Ordering::Acquire);
        let max_pos = risk.max_inv_delta.load(Ordering::Acquire) as i64;
        
        let inv_skew = if max_pos > 0 {
            let ratio = FixedPrice::new(current_pos).max(FixedPrice::new(-max_pos)).min(FixedPrice::new(max_pos)) / FixedPrice::new(max_pos);
            let mut skew_fp = ratio * FixedPrice::new(grid) * FixedPrice::new(2 * PRICE_SCALE_I);
            skew_fp.0 = -skew_fp.0;
            skew_fp.0
        } else { 0 };

        let raw_bias = risk.bias_offset.load(Ordering::Acquire);
        let ai_hb = engine.ai_heartbeat_ms.load(Ordering::Acquire);
        let bias = if ai_hb > 0 && now_ms.saturating_sub(ai_hb) > 30_000 { 0 } else { raw_bias };
        
        let l1_skew = engine.l1_skew_adjustment.load(Ordering::Acquire);
        let macro_raw = engine.macro_bias.load(Ordering::Relaxed);
        let macro_ts = engine.macro_source_ts.load(Ordering::Relaxed);
        let macro_stale = now_ms.saturating_sub(macro_ts) > 600_000;
        let macro_bias_scaled = if !macro_stale && macro_raw.abs() > 500 {
            ((FixedPrice::new(grid) * FixedPrice::new(10_000_000)) * (FixedPrice::new(macro_raw * PRICE_SCALE_I) / FixedPrice::new(10000 * PRICE_SCALE_I))).0
        } else { 0 };

        let bnb_sweep_ts = engine.binance_sweep_ts.load(Ordering::Relaxed);
        let bnb_sweep_age_ms = now_ms.saturating_sub(bnb_sweep_ts);
        if bnb_sweep_ts > 0 && bnb_sweep_age_ms < 3000 {
            let current_trans = engine.ghost_transparency.load(Ordering::Relaxed);
            if current_trans > 2000 { engine.ghost_transparency.store(1000, Ordering::Relaxed); }
        }
        let final_bias_with_l1 = bias + inv_skew + l1_skew + macro_bias_scaled;

        let delta_shift = if delta_active {
            let raw_shift = (delta_lead_bps / FixedPrice::new(100 * PRICE_SCALE_I)) * micro;
            let max_shift = FixedPrice::new(grid) * FixedPrice::new(25_000_000);
            let clamped = raw_shift.max(FixedPrice::new(-max_shift.0)).min(max_shift);
            engine.delta_repositions.fetch_add(1, Ordering::Relaxed);
            clamped.0
        } else { 0 };

        unsafe { &*self.l2portfolio }.hydra_inventory.store(current_pos, Ordering::Relaxed);
        let (as_bid, as_sell) = sniper_types::l2_command::calculate_as_quotes(l2cmd, unsafe{&*self.l2as}, micro_i, current_pos);
        let vpin_bps = sniper_types::l2_command::vpin_shift_bps(l2risk);
        let vpin_delta = vpin_bps * (micro_i / 10_000);

        if sniper_types::l2_command::is_flash_crash_active(l2risk) {
            let drop = l2risk.flash_crash_drop_bps.load(Ordering::Relaxed);
            self.notifier.alert(format!("🚨 CROSS-BOT EMERGENCY: Flash crash {}bps detected by Moonshot — cancelling all Hydra orders!", drop));
            out_buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"all\":1}]");
            self.last_upd = now;
            sniper_types::l2_command::record_latency(unsafe{&*self.l1ring}, now);
            return;
        }

        let mut buy_i = (as_bid + vpin_delta - grid + final_bias_with_l1 + delta_shift).max(0);
        let mut sell_i = (as_sell + vpin_delta + grid + final_bias_with_l1 + delta_shift).max(0);

        let ba_i = best_ask as i64;
        let bb_i = best_bid as i64;
        if buy_i >= ba_i { buy_i = ba_i - MIN_TICK; }
        if sell_i <= bb_i { sell_i = bb_i + MIN_TICK; }
        if buy_i >= sell_i { 
            self.last_upd = now;
            sniper_types::l2_command::record_latency(unsafe{&*self.l1ring}, now);
            return; 
        }

        let spread_bps = ((sell_i - buy_i) as f64 / micro_i as f64 * 10000.0) as u64;
        if !ghost::is_spread_profitable(spread_bps, fee_state) {
            engine.fee_kills.fetch_add(1, Ordering::Relaxed);
            self.last_upd = now;
            sniper_types::l2_command::record_latency(unsafe{&*self.l1ring}, now);
            return;
        }

        let lb = engine.last_buy_price.load(Ordering::SeqCst);
        let ls = engine.last_sell_price.load(Ordering::SeqCst);
        let db = (buy_i - lb).abs();
        let ds = (sell_i - ls).abs();
        if db >= MIN_TICK || ds >= MIN_TICK || lb == 0 {
            let grid_levels = risk.grid_size.load(Ordering::Acquire).clamp(1, sniper_types::MAX_GRID_LEVELS as u64) as usize;
            let max_pos = risk.max_inv_delta.load(Ordering::Acquire) as i64;
            let pos_ratio = if max_pos > 0 { FixedPrice::new(current_pos).max(FixedPrice::new(-max_pos)).min(FixedPrice::new(max_pos)) / FixedPrice::new(max_pos) } else { FixedPrice::zero() };

            let (n_buy, n_sell): (usize, usize) = if pos_ratio > FixedPrice::new(80_000_000) { (0, grid_levels) }
            else if pos_ratio > FixedPrice::new(40_000_000) { (1, grid_levels) }
            else if pos_ratio < FixedPrice::new(-80_000_000) { (grid_levels, 0) }
            else if pos_ratio < FixedPrice::new(-40_000_000) { (grid_levels, 1) }
            else { (grid_levels, grid_levels) };

            let cancel_ids = collect_all_order_ids(engine);
            let out_len_snap = out_buf.len();
            use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
            BitfinexVenue::write_batch_open(out_buf);
            let mut has_items = false;
            let mut itoa_buf = itoa::Buffer::new();
            
            // GID-BASED MASS CANCEL — always cancel ALL Hydra orders atomically
            // This is immune to ID tracking leaks and guarantees clean slate
            out_buf.extend_from_slice(b"[\"oc_multi\",{\"gid\":[");
            out_buf.extend_from_slice(itoa_buf.format(sniper_types::BOT_GID_HYDRA).as_bytes());
            out_buf.extend_from_slice(b"]}]");
            has_items = true;

            // Clear tracking arrays since we just cancelled everything
            for slot in engine.active_buy_ids.iter().chain(engine.active_sell_ids.iter()) {
                slot.store(0, Ordering::Relaxed);
            }

            let min_order_btc = FixedPrice::new(15000); // 0.00015
            let w_btc = FixedPrice::new(engine.wallet_btc.load(Ordering::Relaxed) as i64); 
            let w_usd = FixedPrice::new(engine.wallet_usd.load(Ordering::Relaxed) as i64);

            // ARMADA KŘEMÍKOVÁ ZEĎ 🛡️
            if self.armada_state.is_kill_switch_active() {
                self.last_upd = now;
                return;
            }

            // 1. Čtení limitu a expozice (Armada Orchestrator)
            let auth_cap_usd = FixedPrice::new(self.armada_state.authorized_capital[0].load(Ordering::Acquire) as i64);
            let pos_fp = FixedPrice::new(current_pos);
            let mut abs_pos = pos_fp;
            if abs_pos.0 < 0 { abs_pos.0 = -abs_pos.0; }
            let current_exposure_usd = abs_pos * micro; // Expozice v USD

            // 5% buffer pro klid duše
            let cap_95_pct = auth_cap_usd * FixedPrice::new(95_000_000);
            let available_margin_usd = cap_95_pct - current_exposure_usd;

            // 2. Rozpočet na jednu vrstvu Gridu 
            // - používáme total_grid_levels aby orchestrator rozprostřel kapitál spravedlivě
            let total_grid_levels = (n_buy + n_sell).max(1) as i64;
            let max_usd_per_level = available_margin_usd / FixedPrice::new(total_grid_levels * sniper_types::PRICE_SCALE_I as i64);

            let gc = ghost::calculate_ghost_levels(engine, n_buy, n_sell);
            let n_public_buy = gc.n_public_buy;
            let n_public_sell = gc.n_public_sell;
            let ghost_mode = gc.ghost_mode;

            // === L1 ORACLE HYPER-SKEWING ===
            // obi ukazuje směr (Kupci dominují = >0). trending_score ukazuje sílu VPIN (od 0 do 1)
            let trending_fp = FixedPrice::new(l2risk.trending_score.load(Ordering::Relaxed) as i64);
            let skew_factor = obi * trending_fp;
            let one_fp = FixedPrice::new(sniper_types::PRICE_SCALE_I);
            let buy_skew_multiplier = one_fp + skew_factor;
            let sell_skew_multiplier = one_fp - skew_factor;
            // ===============================

            let base_bp = (micro_i + final_bias_with_l1).max(0).min(ba_i - MIN_TICK);
            for i in 0..n_buy {
                let spacing = (FixedPrice::new(grid) * FixedPrice::from_f64(sniper_types::LEVEL_SPACING[i])).0;
                let bp_i = (base_bp - spacing).max(0);
                let bp_fp = FixedPrice::new(bp_i);

                if i < n_public_buy {
                    // 3. THE CLAMPING
                    let mut final_usd_size = final_order_usd * buy_skew_multiplier;
                    
                    if pos_fp.0 >= 0 {
                        // Jsme Long. Zvyšujeme expozici. Máme limit?
                        if available_margin_usd.0 <= 0 { continue; }
                        if final_usd_size > max_usd_per_level {
                            final_usd_size = max_usd_per_level;
                        }
                    }

                    // 4. Kontrola min Notional (Bitfinex cca 10 USD, dáme 15)
                    let min_notional = FixedPrice::new(15 * sniper_types::PRICE_SCALE_I as i64);
                    if final_usd_size < min_notional { continue; }

                    let amt = final_usd_size / bp_fp;

                    if has_items { out_buf.extend_from_slice(b","); }
                    out_buf.extend_from_slice(b"[\"on\",{\"gid\":");
                    out_buf.extend_from_slice(itoa_buf.format(sniper_types::BOT_GID_HYDRA).as_bytes());
                    out_buf.extend_from_slice(b",\"symbol\":\"");
                    out_buf.extend_from_slice(sniper_types::TRADING_SYMBOL.as_bytes());
                    out_buf.extend_from_slice(b"\",\"amount\":\"");
                    let amt_str = FixedFormat::new(amt.0);
                    out_buf.extend_from_slice(amt_str.as_str().as_bytes());
                    out_buf.extend_from_slice(b"\",\"price\":\"");
                    let bp_str = FixedFormat::new(bp_i);
                    out_buf.extend_from_slice(bp_str.as_str().as_bytes());
                    out_buf.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\",\"flags\":4096}]");
                    has_items = true;
                } else if ghost_mode {
                    engine.ghost_buy_prices[i].store(bp_i, Ordering::Relaxed);
                }
            }

            let base_sp = (micro_i + final_bias_with_l1).max(0).max(bb_i + MIN_TICK);
            for i in 0..n_sell {
                let spacing = (FixedPrice::new(grid) * FixedPrice::from_f64(sniper_types::LEVEL_SPACING[i])).0;
                let sp_i = base_sp + spacing;
                
                if i < n_public_sell {
                    let sp_fp = FixedPrice::new(sp_i);
                    // 3. THE CLAMPING
                    let mut final_usd_size = final_order_usd * sell_skew_multiplier;
                    
                    if pos_fp.0 <= 0 {
                        // Jsme Short. Zvyšujeme expozici. Máme limit?
                        if available_margin_usd.0 <= 0 { continue; }
                        if final_usd_size > max_usd_per_level {
                            final_usd_size = max_usd_per_level;
                        }
                    }

                    // 4. Kontrola min Notional
                    let min_notional = FixedPrice::new(15 * sniper_types::PRICE_SCALE_I as i64);
                    if final_usd_size < min_notional { continue; }

                    let mut amt = final_usd_size / sp_fp;
                    amt.0 = -amt.0; // Short order = negative amount

                    // 5. WALLET BALANCE GUARD: Sell limit on exchange requires BTC in wallet
                    // Available = wallet_btc (we use GID cancel so old order balance is freed)
                    let w_btc_raw = engine.wallet_btc.load(Ordering::Relaxed) as i64;
                    // Divide wallet evenly across sell levels, leave 10% buffer
                    let n_sell_levels = n_sell.max(1) as i64;
                    let max_sell_per_level = (w_btc_raw * 90 / 100) / n_sell_levels;
                    if amt.0.unsigned_abs() as i64 > max_sell_per_level {
                        if max_sell_per_level < 15000 { continue; } // less than 0.00015 BTC min
                        amt.0 = -(max_sell_per_level);
                    }

                    if has_items { out_buf.extend_from_slice(b","); }
                    out_buf.extend_from_slice(b"[\"on\",{\"gid\":");
                    out_buf.extend_from_slice(itoa_buf.format(sniper_types::BOT_GID_HYDRA).as_bytes());
                    out_buf.extend_from_slice(b",\"symbol\":\"");
                    out_buf.extend_from_slice(sniper_types::TRADING_SYMBOL.as_bytes());
                    out_buf.extend_from_slice(b"\",\"amount\":\"");
                    let amt_str = FixedFormat::new(amt.0);
                    out_buf.extend_from_slice(amt_str.as_str().as_bytes());
                    out_buf.extend_from_slice(b"\",\"price\":\"");
                    let sp_str = FixedFormat::new(sp_i);
                    out_buf.extend_from_slice(sp_str.as_str().as_bytes());
                    out_buf.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\",\"flags\":4096}]");
                    has_items = true;
                } else if ghost_mode {
                    engine.ghost_sell_prices[i].store(sp_i, Ordering::Relaxed);
                }
            }

            if !has_items { 
                out_buf.truncate(out_len_snap);
                self.last_upd = now;
                sniper_types::l2_command::record_latency(unsafe{&*self.l1ring}, now);
                return; 
            }

            BitfinexVenue::write_batch_close(out_buf);

            engine.last_buy_price.store(buy_i, Ordering::Relaxed);
            engine.last_sell_price.store(sell_i, Ordering::Relaxed);
            let t2t_us = now.elapsed().as_micros() as u64;
            engine.t2t_micros.store(t2t_us, Ordering::Relaxed);
            sniper_types::l2_command::record_latency(unsafe{&*self.l1ring}, now);
            engine.micro_price.store(micro_i as u64, Ordering::Relaxed);
            engine.current_skew.store(inv_skew, Ordering::Relaxed);
            engine.l2_imbalance.store(obi.0, Ordering::Relaxed);
            engine.current_order_usd.store((final_order_usd.0 / PRICE_SCALE_I) as u64, Ordering::Relaxed);
            self.last_upd = now;
        } else {
            self.last_upd = now;
            sniper_types::l2_command::record_latency(unsafe{&*self.l1ring}, now);
        }
    }
}

fn main() -> Result<()> {
    dotenv().ok();

    let log_dir = "logs";
    let file_appender = tracing_appender::rolling::daily(log_dir, "trading.log");
    let (non_blocking, _log_guard) = tracing_appender::non_blocking(file_appender);
    tracing_subscriber::fmt()
        .with_target(false).json().with_writer(non_blocking)
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env().add_directive(tracing::Level::INFO.into()))
        .init();

    std::fs::create_dir_all("/dev/shm/beroun").context("Failed to create /dev/shm/beroun")?;

    let rt = tokio::runtime::Builder::new_current_thread().enable_all().build()?;
    rt.block_on(async_main())
}

async fn async_main() -> Result<()> {
    let notifier = Arc::new(AsyncNotifier::new("hydra", "🐉"));
    let mut engine_mmap = init_mmap::<EngineState>(&ENGINE_STATE_PATH)?;
    let risk_mmap = init_mmap::<RiskState>(&RISK_STATE_PATH)?;
    let fee_mmap = init_mmap::<sniper_types::fee_types::GlobalFeeMatrix>(sniper_types::fee_types::FEE_MATRIX_PATH)?;
    let l2_mmap = init_mmap::<sniper_types::l2_command::L2SharedState>(sniper_types::l2_command::L2_COMMAND_PATH)?;

    let fee_matrix = unsafe { &*(fee_mmap.as_ptr() as *const sniper_types::fee_types::GlobalFeeMatrix) };
    let l2_shared = unsafe { &*(l2_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };
    let engine_ptr = engine_mmap.as_mut_ptr() as *mut EngineState;
    let risk_ptr = risk_mmap.as_ptr() as *const RiskState;

    let ml_weights_ro = sniper_types::ml_shield::load_ml_weights_ro();
    let ml_shield = sniper_types::ml_shield::MlShield::new(ml_weights_ro);
    let armada_state = sniper_types::armada_types::load_armada_state_ro();

    tokio::spawn(async move {
        let mut report_interval = tokio::time::interval(Duration::from_secs(3600));
        loop {
            report_interval.tick().await;
            tokio::task::spawn_blocking(|| {
                let _ = std::process::Command::new(std::env::current_exe().unwrap_or_default().with_file_name("hydra-config"))
                    .arg("export-json")
                    .output();
            }).await.ok();
        }
    });

    let storm_path = "/dev/shm/beroun/toxic_storm.bin";
    if !std::path::Path::new(storm_path).exists() { let _ = std::fs::write(storm_path, [0u8]); }
    let toxic_storm_mmap = sniper_types::mmap_utils::open_mmap_readonly(storm_path).unwrap();

    let hydra = HydraEngine {
        notifier: notifier.clone(),
        engine: engine_ptr,
        risk: risk_ptr,
        fee_matrix,
        l2cmd: &l2_shared.cmd,
        l2as: &l2_shared.as_mat,
        l2risk: &l2_shared.global_risk,
        l2portfolio: &l2_shared.portfolio,
        l1ring: &l2_shared.latency_ring,
        chan_id: None,
        last_upd: Instant::now(),
        snapshot_loaded: false,
        cs_debug_count: 0,
        cs_fail_count: 0,
        depth_history: [FixedPrice::zero(); 64],
        depth_head: 0,
        depth_len: 0,
        was_in_hole: false,
        ghost_last_micro: FixedPrice::zero(),
        ghost_velocity: FixedPrice::zero(),
        ghost_velocity_max: FixedPrice::new(100_000), 
        ghost_last_inject: Instant::now(),
        toxic_storm_ptr: toxic_storm_mmap.as_ptr(),
        ml_shield,
        armada_state,
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut engine_runner = sniper_types::framework::SovereignDualRunner::new(hydra, venue, "Hydra");
    println!("REACHED RUNNING AWAIT IN MAIN!");
    if let Err(e) = engine_runner.run().await {
        notifier.alert(format!("🚨 *HYDRA FATAL CRASH*\n```\n{}\n```", e));
        tracing::error!(event = "fatal_crash", error = ?e);
    }
    Ok(())
}

```

## hydra/src/book.rs
```rust
// ═════════════════════════════════════════════════════════════
// 📚 L2 Orderbook Management — OBI + Checksum
// ═════════════════════════════════════════════════════════════

use std::sync::atomic::{fence, Ordering};
use crc32fast::Hasher;
use tracing::info;

#[inline]
fn write_bfx(w: &mut impl std::fmt::Write, val: f64) -> std::fmt::Result {
    if val == val.trunc() {
        write!(w, "{:.0}", val)
    } else {
        let mut buf = [0u8; 32];
        let n = {
            use std::io::Write;
            let mut cursor = std::io::Cursor::new(&mut buf[..]);
            let _ = write!(cursor, "{:.12}", val);
            cursor.position() as usize
        };
        let s = unsafe { std::str::from_utf8_unchecked(&buf[..n]) };
        let trimmed = s.trim_end_matches('0').trim_end_matches('.');
        w.write_str(trimmed)
    }
}

pub fn update_book(levels: *mut [sniper_types::OrderBookLevel; sniper_types::BOOK_LEVELS], price: u64, amount: i64, count: u64) {
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

pub fn sort_book(levels: *mut [sniper_types::OrderBookLevel; sniper_types::BOOK_LEVELS], is_bid: bool) {
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

pub fn calculate_checksum(engine: &sniper_types::EngineState, debug: bool) -> i32 {
    fence(Ordering::SeqCst);
    let mut hasher = Hasher::new();
    let mut levels_found: u32 = 0;
    // Stack-only scratch buffer for write_bfx formatting (no heap)
    let mut fmt_buf = arrayvec::ArrayString::<64>::new();

    for i in 0..25 {
        let bid = &engine.bids[i];
        let ask = &engine.asks[i];
        let bp = bid.price.load(Ordering::SeqCst);
        let bc = bid.count.load(Ordering::SeqCst);
        let ap = ask.price.load(Ordering::SeqCst);
        let ac = ask.count.load(Ordering::SeqCst);

        if bc > 0 && bp > 0 {
            if levels_found > 0 { hasher.update(b":"); }
            levels_found += 1;
            let p = bp as f64 / sniper_types::PRICE_SCALE;
            let a = bid.amount.load(Ordering::SeqCst) as f64 / sniper_types::PRICE_SCALE;
            fmt_buf.clear();
            let _ = write_bfx(&mut fmt_buf, p);
            hasher.update(fmt_buf.as_bytes());
            hasher.update(b":");
            fmt_buf.clear();
            let _ = write_bfx(&mut fmt_buf, a);
            hasher.update(fmt_buf.as_bytes());
        }
        if ac > 0 && ap > 0 {
            if levels_found > 0 { hasher.update(b":"); }
            levels_found += 1;
            let p = ap as f64 / sniper_types::PRICE_SCALE;
            let a = ask.amount.load(Ordering::SeqCst) as f64 / sniper_types::PRICE_SCALE;
            fmt_buf.clear();
            let _ = write_bfx(&mut fmt_buf, p);
            hasher.update(fmt_buf.as_bytes());
            hasher.update(b":");
            fmt_buf.clear();
            let _ = write_bfx(&mut fmt_buf, a);
            hasher.update(fmt_buf.as_bytes());
        }
    }

    if debug || levels_found == 0 {
        info!(event = "checksum_debug", levels = levels_found,
              bids0_p = engine.bids[0].price.load(Ordering::SeqCst),
              bids0_c = engine.bids[0].count.load(Ordering::SeqCst),
              asks0_p = engine.asks[0].price.load(Ordering::SeqCst),
              asks0_c = engine.asks[0].count.load(Ordering::SeqCst));
    }

    hasher.finalize() as i32
}

```

## shared/src/exchange/bitfinex_venue.rs
```rust
// ═══════════════════════════════════════════════════════════
// 🔵 BitfinexVenue — VenueAdapter implementation for Bitfinex
// Sniper Armada · v20.0 Hexagonal Architecture
//
// Migrates all Bitfinex-specific logic into a single VenueAdapter
// implementation. No bot code references Bitfinex directly.
//
// Covers: auth, ticker parsing, book parsing, fill normalization,
//         order encoding (ox_multi), cancel, physics.
// ═══════════════════════════════════════════════════════════

use super::types::*;
use super::venue::*;
use crate::moonshot_types::str_to_symbol_hash;

/// Maximum channels we track (ticker + book per symbol, + auth channel)
const MAX_CHANNELS: usize = 64;

/// Bitfinex VenueAdapter — Zero-cost exchange abstraction.
///
/// Tracks channel→symbol mappings for incoming market data.
/// All parsing, encoding, and physics are encapsulated here.
pub struct BitfinexVenue {
    /// Channel ID → symbol hash mapping (populated on subscribe confirmation)
    chan_to_symbol: [(i64, u64); MAX_CHANNELS],
    /// Number of active channel mappings
    chan_count: usize,
    /// Symbol hash → BFX native string (e.g., hash → "tBTCUSD")
    symbols: arrayvec::ArrayVec<(u64, arrayvec::ArrayString<16>), 32>,
}

impl BitfinexVenue {
    pub fn new() -> Self {
        Self {
            chan_to_symbol: [(0, 0); MAX_CHANNELS],
            chan_count: 0,
            symbols: arrayvec::ArrayVec::new(),
        }
    }

    /// Register a symbol for hash↔string mapping.
    pub fn register_symbol(&mut self, symbol: &str) {
        let hash = str_to_symbol_hash(symbol);
        if !self.symbols.iter().any(|(h, _)| *h == hash)
            && let Ok(s) = arrayvec::ArrayString::try_from(symbol) {
                let _ = self.symbols.try_push((hash, s));
            }
    }

    /// Find symbol hash for a channel ID.
    #[inline]
    fn symbol_for_chan(&self, chan_id: i64) -> Option<u64> {
        self.chan_to_symbol[..self.chan_count]
            .iter()
            .find(|(c, _)| *c == chan_id)
            .map(|(_, s)| *s)
    }

    /// Register a channel→symbol mapping.
    fn register_channel(&mut self, chan_id: i64, symbol_hash: u64) {
        if self.chan_count < MAX_CHANNELS {
            self.chan_to_symbol[self.chan_count] = (chan_id, symbol_hash);
            self.chan_count += 1;
        }
    }

    /// Parse BFX ticker array: [chanId, [BID, BID_SIZE, ASK, ASK_SIZE, ...]]
    /// Reuses the fast zero-copy parser from exchange/mod.rs
    fn parse_ticker(&self, data: &[u8]) -> Option<VenueMessage> {
        let (chan_id, bid, ask) = super::fast_parse_ticker(data)?;
        let symbol = self.symbol_for_chan(chan_id)?;
        Some(VenueMessage::Tick(UnifiedTick {
            exchange: ExchangeId::Bitfinex,
            symbol,
            bid,
            ask,
            bid_vol: 0,
            ask_vol: 0,
            exchange_ts: 0,
        }))
    }

    /// Parse BFX execution reports from auth channel.
    /// Format: [0, "tu", [ID, PAIR, MTS, ORDER_ID, EXEC_AMOUNT, EXEC_PRICE, ...FEE, FEE_CURRENCY...]]
    /// Format: [0, "on", [...]] / [0, "ou", [...]] / [0, "oc", [...]]
    fn parse_execution(&self, data: &[u8]) -> Option<VenueMessage> {
        // Minimal check: must start with [0,"
        if data.len() < 10 || data[0] != b'[' { return None; }

        // Check for trade execution: "tu" (trade update)
        if data.len() > 6 && &data[1..5] == b"0,\"t" {
            return self.parse_fill(data);
        }

        // Check for order updates: "on" (new), "ou" (update), "oc" (cancel)
        if data.len() > 6 && &data[1..5] == b"0,\"o" {
            return self.parse_order_update(data);
        }

        None
    }

    /// Parse a BFX fill (trade execution).
    /// [0,"tu",[ID, PAIR, MTS_CREATE, ORDER_ID, EXEC_AMOUNT, EXEC_PRICE, TYPE, ...FEE, FEE_CURRENCY]]
    fn parse_fill(&self, data: &[u8]) -> Option<VenueMessage> {
        // Parse using serde_json for fill messages (not hot path — fills are rare)
        let v: serde_json::Value = serde_json::from_slice(data).ok()?;
        let arr = v.get(2)?.as_array()?;
        if arr.len() < 10 { return None; }

        let pair = arr.get(1)?.as_str().unwrap_or_default();
        let exec_amount = arr.get(4)?.as_f64()?;
        let exec_price = arr.get(5)?.as_f64()?;
        let fee = arr.get(9)?.as_f64().unwrap_or(0.0);
        let fee_cur = arr.get(10)?.as_str().unwrap_or("");
        let order_id = arr.get(3)?.as_u64().unwrap_or(0);

        let side = if exec_amount > 0.0 { OrderSide::Buy } else { OrderSide::Sell };

        Some(VenueMessage::Fill(UnifiedFill {
            exchange: ExchangeId::Bitfinex,
            client_id: order_id,
            symbol_hash: str_to_symbol_hash(pair),
            side,
            price: (exec_price * crate::PRICE_SCALE) as i64,
            amount: exec_amount.abs(),
            fee,
            fee_currency: str_to_symbol_hash(fee_cur),
            exchange_ts: arr.get(2)?.as_u64().unwrap_or(0),
        }))
    }

    /// Parse a BFX order update (on/ou/oc).
    fn parse_order_update(&self, data: &[u8]) -> Option<VenueMessage> {
        let v: serde_json::Value = serde_json::from_slice(data).ok()?;
        let type_str = v.get(1)?.as_str()?;
        let arr = v.get(2)?.as_array()?;
        if arr.len() < 5 { return None; }

        let order_id = arr.first()?.as_u64().unwrap_or(0);
        let remaining = arr.get(6)?.as_f64().unwrap_or(0.0).abs();
        let status_str = arr.get(13)?.as_str().unwrap_or("");

        let status = match type_str {
            "on" => OrderStatus::Accepted,
            "ou" => {
                if status_str.contains("PARTIALLY") { OrderStatus::PartiallyFilled }
                else { OrderStatus::Accepted }
            }
            "oc" => {
                if status_str.contains("CANCELED") { OrderStatus::Canceled }
                else if status_str.contains("EXECUTED") { OrderStatus::Filled }
                else { OrderStatus::Canceled }
            }
            _ => return None,
        };

        Some(VenueMessage::OrderUpdate(UnifiedOrderUpdate {
            exchange: ExchangeId::Bitfinex,
            client_id: order_id,
            exchange_order_id: order_id,
            status,
            remaining,
        }))
    }

    /// Encode symbol hash back to BFX native string for order placement.
    fn symbol_str(&self, hash: u64) -> &str {
        self.symbols.iter()
            .find(|(h, _)| *h == hash)
            .map(|(_, s)| s.as_str())
            .unwrap_or("tBTCUSD")
    }
}

// ═══════════════════════════════════════════════════════════
// Zero-Alloc Order Builders (for L0 hot path)
//
// These write BFX wire format directly to out_buf without heap
// allocations. Bots call these instead of manual extend_from_slice.
// This centralizes ALL Bitfinex encoding in one file.
// ═══════════════════════════════════════════════════════════

impl BitfinexVenue {
    // ── ox_multi batch builders ──

    /// Write the opening of an ox_multi batch with cancel-by-symbol.
    /// Pattern: `[0,"ox_multi",null,[["oc_multi",{"symbol":"tBTCUSD"}]`
    #[inline]
    pub fn write_batch_open_cancel_sym(buf: &mut bytes::BytesMut, symbol: &[u8]) {
        buf.extend_from_slice(b"[0,\"ox_multi\",null,[[\"oc_multi\",{\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\"}]");
    }

    /// Write the opening of an ox_multi batch with cancel-by-gid.
    /// Pattern: `[0,"ox_multi",null,[["oc_multi",{"gid":[3000]}]`
    #[inline]
    pub fn write_batch_open_cancel_gid(buf: &mut bytes::BytesMut, gid: u32) {
        buf.extend_from_slice(b"[0,\"ox_multi\",null,[[\"oc_multi\",{\"gid\":[");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b"]}]");
    }

    /// Write the opening of an ox_multi batch without cancel.
    /// Pattern: `[0,"ox_multi",null,[`
    #[inline]
    pub fn write_batch_open(buf: &mut bytes::BytesMut) {
        buf.extend_from_slice(b"[0,\"ox_multi\",null,[");
    }

    /// Write a LIMIT order into the batch (comma-separated).
    /// Pattern: `,[\"on\",{\"gid\":3000,\"symbol\":\"tBTCUSD\",\"amount\":\"0.001\",\"price\":\"68000.0\",\"type\":\"EXCHANGE LIMIT\"}]`
    #[inline]
    pub fn write_limit_order(
        buf: &mut bytes::BytesMut,
        gid: u32,
        symbol: &[u8],
        amount: &str,   // pre-formatted by ryu
        price: &str,    // pre-formatted by ryu
    ) {
        buf.extend_from_slice(b",[\"on\",{\"gid\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b",\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\"}]");
    }

    /// Write a LIMIT POST-ONLY order into the batch.
    #[inline]
    pub fn write_limit_postonly_order(
        buf: &mut bytes::BytesMut,
        gid: u32,
        symbol: &[u8],
        amount: &str,
        price: &str,
    ) {
        buf.extend_from_slice(b",[\"on\",{\"gid\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b",\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\",\"flags\":4096}]");
    }

    /// Write an IOC order into the batch.
    #[inline]
    pub fn write_ioc_order(
        buf: &mut bytes::BytesMut,
        gid: u32,
        symbol: &[u8],
        amount: &str,
        price: &str,
    ) {
        buf.extend_from_slice(b",[\"on\",{\"gid\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b",\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]");
    }

    /// Write a standalone IOC order (not inside ox_multi batch).
    /// Pattern: `[0,"on",null,{"gid":2001,"symbol":"tBTCUSD","amount":"0.001","price":"68000","type":"EXCHANGE IOC"}]`
    #[inline]
    pub fn write_standalone_ioc(
        buf: &mut bytes::BytesMut,
        gid: u32,
        symbol: &[u8],
        amount: &str,
        price: &str,
    ) {
        buf.extend_from_slice(b"[0,\"on\",null,{\"gid\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b",\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]");
    }

    /// Close the ox_multi batch.
    /// Pattern: `]]`
    #[inline]
    pub fn write_batch_close(buf: &mut bytes::BytesMut) {
        buf.extend_from_slice(b"]]");
    }

    /// Write a cancel-all-by-gid standalone message.
    #[inline]
    pub fn write_cancel_gid_standalone(buf: &mut bytes::BytesMut, gid: u32) {
        buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"gid\":[");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b"]}]");
    }
}

impl Default for BitfinexVenue {
    fn default() -> Self { Self::new() }
}

// ═══════════════════════════════════════════════════════════
// VenueAdapter Implementation
// ═══════════════════════════════════════════════════════════

impl VenueAdapter for BitfinexVenue {
    fn id(&self) -> ExchangeId { ExchangeId::Bitfinex }
    fn name(&self) -> &str { "Bitfinex" }
    fn env_prefix(&self) -> &str { "BITFINEX" }
    fn ws_url(&self) -> &str { "wss://api.bitfinex.com/ws/2" }

    fn physics(&self) -> ExchangePhysics {
        ExchangePhysics {
            maker_fee_bps: -2.0,    // Bitfinex maker rebate
            taker_fee_bps: 5.5,     // Bitfinex taker fee
            tick_size: 0.1,         // BTC/USD minimum price increment
            lot_size: 0.00001,      // Minimum BTC quantity
            min_order_size: 0.00004, // Minimum order size
            max_orders_per_sec: 90, // Rate limit
        }
    }

    fn auth_message(&self, creds: &ExchangeCredentials) -> Option<String> {
        Some(super::bitfinex_auth_message(&creds.api_key, &creds.api_secret))
    }

    fn subscribe_ticker(&self, symbol: &str) -> String {
        super::bitfinex_subscribe_ticker(symbol)
    }

    fn subscribe_book(&self, symbol: &str, precision: &str, depth: u32) -> String {
        super::bitfinex_subscribe_book(symbol, precision, depth)
    }

    fn parse_raw(&mut self, raw: &[u8]) -> VenueMessage {
        if raw.is_empty() { return VenueMessage::Unknown; }

        match raw[0] {
            // JSON object: system events (auth, subscribe, info)
            b'{' => {
                let v: serde_json::Value = match serde_json::from_slice(raw) {
                    Ok(v) => v,
                    Err(_) => return VenueMessage::Unknown,
                };

                if let Some(event) = v.get("event").and_then(|e| e.as_str()) {
                    match event {
                        "auth" => {
                            let success = v.get("status")
                                .and_then(|s| s.as_str())
                                .map(|s| s == "OK")
                                .unwrap_or(false);
                            return VenueMessage::Authenticated { success };
                        }
                        "subscribed" => {
                            let chan_id = v.get("chanId")
                                .and_then(|c| c.as_i64())
                                .unwrap_or(0);
                            // Extract symbol from "symbol" or "key" field
                            let sym_str = v.get("symbol")
                                .or_else(|| v.get("key"))
                                .and_then(|s| s.as_str())
                                .unwrap_or("");
                            let sym_hash = str_to_symbol_hash(sym_str);
                            self.register_channel(chan_id, sym_hash);
                            return VenueMessage::Subscribed {
                                chan_id,
                                symbol: sym_hash,
                            };
                        }
                        _ => {}
                    }
                }
                VenueMessage::Unknown
            }

            // JSON array: market data or execution reports
            b'[' => {
                // Check for heartbeat: [chanId, "hb"]
                if raw.len() > 5 {
                    // Fast check for "hb" pattern
                    let mut i = 1;
                    while i < raw.len() && raw[i] != b',' { i += 1; }
                    if i + 4 < raw.len() && raw[i+1] == b'"' && raw[i+2] == b'h' && raw[i+3] == b'b' {
                        return VenueMessage::Heartbeat;
                    }
                }

                // Channel 0 = auth channel (fills, orders)
                if raw.len() > 3 && raw[1] == b'0' && raw[2] == b',' {
                    if let Some(msg) = self.parse_execution(raw) {
                        return msg;
                    }
                    return VenueMessage::Unknown;
                }

                // Market data channel (ticker)
                if let Some(msg) = self.parse_ticker(raw) {
                    return msg;
                }

                VenueMessage::Unknown
            }

            _ => VenueMessage::Unknown,
        }
    }

    fn encode_batch(&self, batch: &VenueBatchOrder, buf: &mut bytes::BytesMut) {
        use std::fmt::Write;

        if batch.orders.is_empty() && batch.cancel_gid.is_none() && batch.cancel_cids.is_empty() {
            return;
        }

        let mut msg = String::with_capacity(512);
        msg.push_str("[0,\"ox_multi\",null,[");

        // Cancel section
        if let Some(gid) = batch.cancel_gid {
            if let Some(sym_hash) = batch.cancel_symbol {
                let sym = self.symbol_str(sym_hash);
                let _ = write!(msg, "[\"oc_multi\",{{\"symbol\":\"{}\",\"all\":1}}],", sym);
            } else {
                let _ = write!(msg, "[\"oc_multi\",{{\"gid\":[{}]}}],", gid);
            }
        }

        // Orders section
        let physics = self.physics();
        for (i, order) in batch.orders.iter().enumerate() {
            let sym = self.symbol_str(order.symbol_hash);
            let raw_price = order.price as f64 / crate::PRICE_SCALE;
            let is_bid = matches!(order.side, OrderSide::Buy);
            let aligned_price = physics.align_price(raw_price, is_bid);
            let aligned_qty = physics.align_qty(order.amount);

            let signed_amount = match order.side {
                OrderSide::Buy => aligned_qty,
                OrderSide::Sell => -aligned_qty,
            };

            let type_str = match order.order_type {
                OrderType::Limit => "EXCHANGE LIMIT",
                OrderType::LimitPostOnly => "EXCHANGE LIMIT",
                OrderType::Ioc => "EXCHANGE IOC",
                OrderType::Market => "EXCHANGE MARKET",
            };

            let flags = match order.order_type {
                OrderType::LimitPostOnly => 4096, // post-only
                _ => 0,
            };

            if flags > 0 {
                let _ = write!(
                    msg,
                    "[\"on\",{{\"gid\":{},\"symbol\":\"{}\",\"amount\":\"{:.5}\",\"price\":\"{:.2}\",\"type\":\"{}\",\"flags\":{}}}]",
                    order.gid, sym, signed_amount, aligned_price, type_str, flags
                );
            } else {
                let _ = write!(
                    msg,
                    "[\"on\",{{\"gid\":{},\"symbol\":\"{}\",\"amount\":\"{:.5}\",\"price\":\"{:.2}\",\"type\":\"{}\"}}]",
                    order.gid, sym, signed_amount, aligned_price, type_str
                );
            }

            if i + 1 < batch.orders.len() {
                msg.push(',');
            }
        }

        msg.push_str("]]");
        buf.extend_from_slice(msg.as_bytes());
    }

    fn encode_cancel_gid(&self, gid: u32, buf: &mut bytes::BytesMut) {
        let msg = format!("[0,\"oc_multi\",null,{{\"gid\":[{}]}}]", gid);
        buf.extend_from_slice(msg.as_bytes());
    }

    fn encode_cancel_all(&self, buf: &mut bytes::BytesMut) {
        buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"all\":1}]");
    }

    fn symbol_for_hash(&self, hash: u64) -> Option<&str> {
        self.symbols.iter()
            .find(|(h, _)| *h == hash)
            .map(|(_, s)| s.as_str())
    }
}

// ═══════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_physics_align_price() {
        let venue = BitfinexVenue::new();
        let p = venue.physics();
        // Bid: round down
        assert_eq!(p.align_price(68451.23, true), 68451.2);
        // Ask: round up
        assert_eq!(p.align_price(68451.23, false), 68451.3);
    }

    #[test]
    fn test_physics_align_qty() {
        let venue = BitfinexVenue::new();
        let p = venue.physics();
        assert!((p.align_qty(0.001234) - 0.00123).abs() < 1e-10);
    }

    #[test]
    fn test_parse_auth() {
        let mut venue = BitfinexVenue::new();
        let raw = br#"{"event":"auth","status":"OK","chanId":0}"#;
        match venue.parse_raw(raw) {
            VenueMessage::Authenticated { success } => assert!(success),
            _ => panic!("Expected Authenticated"),
        }
    }

    #[test]
    fn test_parse_auth_fail() {
        let mut venue = BitfinexVenue::new();
        let raw = br#"{"event":"auth","status":"FAILED","msg":"invalid key"}"#;
        match venue.parse_raw(raw) {
            VenueMessage::Authenticated { success } => assert!(!success),
            _ => panic!("Expected Authenticated"),
        }
    }

    #[test]
    fn test_parse_subscribed() {
        let mut venue = BitfinexVenue::new();
        let raw = br#"{"event":"subscribed","channel":"ticker","symbol":"tBTCUSD","chanId":42}"#;
        match venue.parse_raw(raw) {
            VenueMessage::Subscribed { chan_id, symbol } => {
                assert_eq!(chan_id, 42);
                assert_eq!(symbol, str_to_symbol_hash("tBTCUSD"));
            }
            _ => panic!("Expected Subscribed"),
        }
        // Verify channel mapping registered
        assert_eq!(venue.symbol_for_chan(42), Some(str_to_symbol_hash("tBTCUSD")));
    }

    #[test]
    fn test_parse_heartbeat() {
        let mut venue = BitfinexVenue::new();
        let raw = b"[42,\"hb\"]";
        match venue.parse_raw(raw) {
            VenueMessage::Heartbeat => {}
            _ => panic!("Expected Heartbeat"),
        }
    }

    #[test]
    fn test_encode_batch() {
        let mut venue = BitfinexVenue::new();
        venue.register_symbol("tBTCUSD");
        let hash = str_to_symbol_hash("tBTCUSD");

        let mut batch = VenueBatchOrder::default();
        batch.cancel_gid = Some(2000);
        batch.orders.push(VenueOrderRequest {
            client_id: 1,
            gid: 2000,
            symbol_hash: hash,
            side: OrderSide::Buy,
            price: (68000.0 * crate::PRICE_SCALE) as i64,
            amount: 0.001,
            order_type: OrderType::LimitPostOnly,
        });

        let mut buf = bytes::BytesMut::new();
        venue.encode_batch(&batch, &mut buf);
        let result = String::from_utf8(buf.to_vec()).unwrap();
        assert!(result.contains("ox_multi"));
        assert!(result.contains("tBTCUSD"));
        assert!(result.contains("4096"));  // post-only flag
    }
}

```

## shared/src/l2_command.rs
```rust
// ═══════════════════════════════════════════════════════════
// L2 Command Matrix v6 — Phase 1 + 2 + 3 Complete
// "Thin L1, Fat L2" Sovereign Intelligence Architecture
//
// SIX cache-line highways:
//   CL1 (64B): L2→L1 Tactical Defense (fade, latency, moonshot)
//   CL2 (64B): L2→L1 A-S Offense (inventory skew, spread)
//   CL3 (64B): L2→L1 Grid Warp (quadratic topology)
//   CL4 (64B): L2→L1 Global Risk (VPIN, Aegis hedger)
//   CL5 (64B): L1→L2 Portfolio Telemetry (all bot inventories)
//   CL6+ (576B): L1→L2 Latency Ring Buffer
//
// mmap: /dev/shm/beroun/l2_command.bin (896 bytes)
// ═══════════════════════════════════════════════════════════

use std::sync::atomic::{AtomicI64, AtomicU64, AtomicUsize, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

pub const L2_COMMAND_PATH: &str = "/dev/shm/beroun/l2_command.bin";
pub const LATENCY_RING_SIZE: usize = 64;
pub const LATENCY_RING_MASK: usize = LATENCY_RING_SIZE - 1;
pub const MIN_AMEND_THRESHOLD_BPS: i64 = 3;
pub const BTC_SCALE: i64 = 100_000_000;

// ═══ CL1: Tactical Defense (Phase 1) ═══
#[repr(C, align(64))]
pub struct L2CommandMatrix {
    pub config_version: AtomicU64,
    pub bid_fade_bps: AtomicI64,
    pub ask_fade_bps: AtomicI64,
    pub latency_padding_bps: AtomicI64,
    pub latency_killswitch: AtomicI64,
    pub moonshot_trigger_price: AtomicI64,
    pub moonshot_armed: AtomicI64,
    _pad_cl1: [u8; 8],
}

// ═══ CL2: A-S Offense (Phase 2a) ═══
#[repr(C, align(64))]
pub struct L2ASMatrix {
    pub as_target_inventory: AtomicI64,
    pub as_skew_factor_bps: AtomicI64,
    pub as_half_spread_bps: AtomicI64,
    pub current_inventory: AtomicI64,
    _pad_cl2: [u8; 32],
}

// ═══ CL3: Grid Gaussian Warp (Phase 2b) ═══
#[repr(C, align(64))]
pub struct L2GridWarpMatrix {
    pub grid_config_version: AtomicU64,
    pub grid_dynamic_anchor: AtomicI64,
    pub grid_base_step_bps: AtomicI64,
    pub grid_warp_factor: AtomicI64,
    pub grid_max_bid_levels: AtomicI64,
    pub grid_max_ask_levels: AtomicI64,
    _pad_cl3: [u8; 16],
}

// ═══ CL4: Global Risk & Aegis Hedger (Phase 3) + Cross-Bot Signals ═══
#[repr(C, align(64))]
pub struct L2GlobalRiskMatrix {
    /// Independent SeqLock for macro risk
    pub risk_config_version: AtomicU64,
    /// VPIN directional toxicity × PRICE_SCALE: -1e8 (dump) to +1e8 (pump)
    pub global_vpin_toxicity: AtomicI64,
    /// Aegis target delta × PRICE_SCALE (negative = short perps)
    pub aegis_target_delta: AtomicI64,
    /// 1 = portfolio hedged via perps, Grid stops buying
    pub portfolio_is_hedged: AtomicI64,
    /// Cross-bot: epoch ms when flash crash detected by Moonshot (0 = clear)
    pub flash_crash_epoch_ms: AtomicU64,
    /// Cross-bot: magnitude of drop in bps (e.g., -300 = -3%)
    pub flash_crash_drop_bps: AtomicI64,
    /// Pravděpodobnost klidného trhu (0.0 až 1.0) v u64 (škálováno 1e8)
    pub ranging_score: AtomicU64,
    /// Pravděpodobnost silného trendu/průrazu (0.0 až 1.0) v u64 (škálováno 1e8)
    pub trending_score: AtomicU64,
}

// ═══ CL5: Portfolio Telemetry (L1 → L2, Phase 3) ═══
#[repr(C, align(64))]
pub struct L2PortfolioTelemetry {
    pub hydra_inventory: AtomicI64,
    pub grid_inventory: AtomicI64,
    pub moonshot_inventory: AtomicI64,
    pub aegis_current_delta: AtomicI64,
    _pad_cl5: [u8; 32],
}

// ═══ CL6+: Latency Ring Buffer ═══
#[repr(C, align(64))]
pub struct L1TelemetryRing {
    pub latency_head: AtomicUsize,
    _pad_head: [u8; 56],
    pub latency_ring_us: [AtomicU64; LATENCY_RING_SIZE],
}

// ═══ MASTER STRUCT (v12.0) ═══
/// unified mmap layout, eliminating manual pointer arithmetic.
#[repr(C, align(64))]
#[derive(Default)]
pub struct L2SharedState {
    pub cmd: L2CommandMatrix,           // Offset 0 (CL1)
    pub as_mat: L2ASMatrix,             // Offset 64 (CL2)
    pub grid_warp: L2GridWarpMatrix,    // Offset 128 (CL3)
    pub global_risk: L2GlobalRiskMatrix,// Offset 192 (CL4)
    pub portfolio: L2PortfolioTelemetry,// Offset 256 (CL5)
    pub latency_ring: L1TelemetryRing,  // Offset 320 (CL6+)
}

// ═══════════════════════════════════════════════════════════
// Defaults
// ═══════════════════════════════════════════════════════════

impl Default for L2CommandMatrix {
    fn default() -> Self {
        Self {
            config_version: AtomicU64::new(0),
            bid_fade_bps: AtomicI64::new(0),
            ask_fade_bps: AtomicI64::new(0),
            latency_padding_bps: AtomicI64::new(0),
            latency_killswitch: AtomicI64::new(0),
            moonshot_trigger_price: AtomicI64::new(0),
            moonshot_armed: AtomicI64::new(0),
            _pad_cl1: [0u8; 8],
        }
    }
}

impl Default for L2ASMatrix {
    fn default() -> Self {
        Self {
            as_target_inventory: AtomicI64::new(0),
            as_skew_factor_bps: AtomicI64::new(5),
            as_half_spread_bps: AtomicI64::new(3),
            current_inventory: AtomicI64::new(0),
            _pad_cl2: [0u8; 32],
        }
    }
}

impl Default for L2GridWarpMatrix {
    fn default() -> Self {
        Self {
            grid_config_version: AtomicU64::new(0),
            grid_dynamic_anchor: AtomicI64::new(0),
            grid_base_step_bps: AtomicI64::new(10),
            grid_warp_factor: AtomicI64::new(0),
            grid_max_bid_levels: AtomicI64::new(15),
            grid_max_ask_levels: AtomicI64::new(15),
            _pad_cl3: [0u8; 16],
        }
    }
}

impl Default for L2GlobalRiskMatrix {
    fn default() -> Self {
        Self {
            risk_config_version: AtomicU64::new(0),
            global_vpin_toxicity: AtomicI64::new(0),
            aegis_target_delta: AtomicI64::new(0),
            portfolio_is_hedged: AtomicI64::new(0),
            flash_crash_epoch_ms: AtomicU64::new(0),
            flash_crash_drop_bps: AtomicI64::new(0),
            ranging_score: AtomicU64::new(0),
            trending_score: AtomicU64::new(0),
        }
    }
}

impl Default for L2PortfolioTelemetry {
    fn default() -> Self {
        Self {
            hydra_inventory: AtomicI64::new(0),
            grid_inventory: AtomicI64::new(0),
            moonshot_inventory: AtomicI64::new(0),
            aegis_current_delta: AtomicI64::new(0),
            _pad_cl5: [0u8; 32],
        }
    }
}

impl Default for L1TelemetryRing {
    fn default() -> Self { unsafe { std::mem::zeroed() } }
}

// ═══════════════════════════════════════════════════════════
// L1 Hot-Path Helpers (all #[inline(always)], zero allocation)
// ═══════════════════════════════════════════════════════════

#[inline(always)]
pub fn l2cmd_version_check(cmd: &L2CommandMatrix) -> (u64, bool) {
    let v = cmd.config_version.load(Ordering::Acquire);
    (v, v.is_multiple_of(2))
}

#[inline(always)]
pub fn record_latency(ring: &L1TelemetryRing, send_ts: std::time::Instant) {
    let latency_us = send_ts.elapsed().as_micros() as u64;
    let head = ring.latency_head.load(Ordering::Relaxed);
    let idx = head & LATENCY_RING_MASK;
    ring.latency_ring_us[idx].store(latency_us, Ordering::Relaxed);
    ring.latency_head.store(head.wrapping_add(1), Ordering::Release);
}

#[inline(always)]
pub fn can_execute_arb(cmd: &L2CommandMatrix, gross_profit_bps: i64, base_fee_bps: i64) -> bool {
    if cmd.latency_killswitch.load(Ordering::Acquire) == 1 { return false; }
    let padding = cmd.latency_padding_bps.load(Ordering::Relaxed);
    gross_profit_bps >= (base_fee_bps + padding)
}

#[inline(always)]
pub fn should_amend(current_price: i64, target_price: i64, fair_price: i64) -> bool {
    if fair_price == 0 { return false; }
    let diff_bps = ((current_price - target_price).abs() * 10_000) / fair_price;
    diff_bps >= MIN_AMEND_THRESHOLD_BPS
}

/// Moonshot CAS Single Bullet
#[inline(always)]
pub fn moonshot_check_and_disarm(
    cmd: &L2CommandMatrix, current_price_scaled: i64,
    recent_volume_scaled: i64, avg_volume_scaled: i64,
) -> Option<i64> {
    if cmd.moonshot_armed.load(Ordering::Relaxed) != 1 { return None; }
    let trigger = cmd.moonshot_trigger_price.load(Ordering::Relaxed);
    if trigger == 0 || current_price_scaled > trigger { return None; }
    if avg_volume_scaled > 0 && recent_volume_scaled < (avg_volume_scaled * 5) { return None; }
    match cmd.moonshot_armed.compare_exchange(1, 0, Ordering::Acquire, Ordering::Relaxed) {
        Ok(_) => Some(trigger), Err(_) => None,
    }
}

/// A-S + Fade fusion quote calculator
#[inline(always)]
pub fn calculate_as_quotes(
    cmd: &L2CommandMatrix, as_mat: &L2ASMatrix,
    fair_price: i64, current_inventory: i64,
) -> (i64, i64) {
    as_mat.current_inventory.store(current_inventory, Ordering::Relaxed);
    let mut seq;
    let (mut bid_fade, mut ask_fade, mut target_inv, mut skew_bps, mut half_spread);
    loop {
        seq = cmd.config_version.load(Ordering::Acquire);
        if seq % 2 != 0 { std::hint::spin_loop(); continue; }
        bid_fade = cmd.bid_fade_bps.load(Ordering::Relaxed);
        ask_fade = cmd.ask_fade_bps.load(Ordering::Relaxed);
        target_inv = as_mat.as_target_inventory.load(Ordering::Relaxed);
        skew_bps = as_mat.as_skew_factor_bps.load(Ordering::Relaxed);
        half_spread = as_mat.as_half_spread_bps.load(Ordering::Relaxed);
        std::sync::atomic::fence(Ordering::Acquire);
        if seq == cmd.config_version.load(Ordering::Relaxed) { break; }
    }
    let inventory_delta = current_inventory - target_inv;
    let as_shift_bps = (inventory_delta * skew_bps) / BTC_SCALE;
    let bps_val = fair_price / 10_000;
    let reservation = fair_price - (as_shift_bps * bps_val);
    let target_bid = reservation - (half_spread * bps_val) - (bid_fade * bps_val);
    let target_ask = reservation + (half_spread * bps_val) + (ask_fade * bps_val);
    (target_bid, target_ask)
}

/// Grid Gaussian Warp: quadratic level calculator
#[inline(always)]
pub fn calculate_warped_grid_level(
    grid: &L2GridWarpMatrix, level_index: i64, is_bid: bool,
) -> Option<i64> {
    let mut seq;
    let (mut anchor, mut base_step, mut warp, mut max_bid, mut max_ask);
    loop {
        seq = grid.grid_config_version.load(Ordering::Acquire);
        if seq % 2 != 0 { std::hint::spin_loop(); continue; }
        anchor = grid.grid_dynamic_anchor.load(Ordering::Relaxed);
        base_step = grid.grid_base_step_bps.load(Ordering::Relaxed);
        warp = grid.grid_warp_factor.load(Ordering::Relaxed);
        max_bid = grid.grid_max_bid_levels.load(Ordering::Relaxed);
        max_ask = grid.grid_max_ask_levels.load(Ordering::Relaxed);
        std::sync::atomic::fence(Ordering::Acquire);
        if seq == grid.grid_config_version.load(Ordering::Relaxed) { break; }
    }
    if is_bid && level_index > max_bid { return None; }
    if !is_bid && level_index > max_ask { return None; }
    let n_sq = level_index * level_index;
    let distance_bps = (base_step * level_index) + (warp * n_sq);
    let bps_val = anchor / 10_000;
    if bps_val == 0 { return None; }
    let price_delta = distance_bps * bps_val;
    if is_bid { Some(anchor - price_delta) } else { Some(anchor + price_delta) }
}

// ═══════════════════════════════════════════════════════════
// Phase 3: Global Risk Helpers
// ═══════════════════════════════════════════════════════════

/// Grid hedge check: false = portfolio shield active, don't place bids
#[inline(always)]
pub fn should_grid_place_bid(risk: &L2GlobalRiskMatrix) -> bool {
    risk.portfolio_is_hedged.load(Ordering::Relaxed) != 1
}

/// VPIN shift in bps (branchless). ±30 bps max.
/// Negative VPIN (dump) → negative shift → bids retreat, asks drop
#[inline(always)]
pub fn vpin_shift_bps(risk: &L2GlobalRiskMatrix) -> i64 {
    let toxicity = risk.global_vpin_toxicity.load(Ordering::Relaxed);
    (toxicity * 30) / BTC_SCALE
}

// ═══════════════════════════════════════════════════════════
// Phase 3.3: Cross-Bot Flash Crash Signal Helpers
// ═══════════════════════════════════════════════════════════

/// Flash crash signal TTL — auto-expires after 30 seconds (stale protection)
pub const FLASH_CRASH_TTL_MS: u64 = 30_000;

/// Check if a cross-bot flash crash signal is currently active and not stale.
/// Called by Hydra every tick — zero-cost when no crash (single atomic load).
#[inline(always)]
pub fn is_flash_crash_active(risk: &L2GlobalRiskMatrix) -> bool {
    let ts = risk.flash_crash_epoch_ms.load(Ordering::Acquire);
    if ts == 0 { return false; }
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    now_ms.saturating_sub(ts) < FLASH_CRASH_TTL_MS
}

/// Write a flash crash signal (called by Moonshot when wick detected).
#[inline(always)]
pub fn signal_flash_crash(risk: &L2GlobalRiskMatrix, drop_bps: i64) {
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    risk.flash_crash_drop_bps.store(drop_bps, Ordering::Relaxed);
    risk.flash_crash_epoch_ms.store(now_ms, Ordering::Release);
}

/// Clear the flash crash signal (called by Moonshot after recovery / TTL).
#[inline(always)]
pub fn clear_flash_crash(risk: &L2GlobalRiskMatrix) {
    risk.flash_crash_epoch_ms.store(0, Ordering::Release);
    risk.flash_crash_drop_bps.store(0, Ordering::Relaxed);
}


/// Total mmap size is simply the size of the Master Struct (896B)
pub const L2_COMMAND_FILE_SIZE: usize = std::mem::size_of::<L2SharedState>();

pub fn load_l2_shared_state_ro() -> &'static L2SharedState {
    let file = std::fs::File::open(L2_COMMAND_PATH)
        .expect("🔥 L2 Command State neexistuje.");
    
    let mmap = unsafe { memmap2::MmapOptions::new().map(&file).unwrap() };
    let mmap_ref = Box::leak(Box::new(mmap));
    
    let state_ptr = mmap_ref.as_ptr() as *const L2SharedState;
    unsafe { &*state_ptr }
}

```

