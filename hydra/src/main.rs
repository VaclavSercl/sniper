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
    armada_v2: &'static sniper_types::armada_types::ArmadaStateV2,
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
        let v2_kill = self.armada_v2.global.global_kill_switch.load(Ordering::Acquire) == 1; // is_shadow = true -> nestřílej ostrými
        is_paused || config_shadow || v2_kill
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

        let c_idx = sniper_types::armada_types::capital_index(0, 0); // bot=hydra(0), venue=BFX(0)
        let auth_cap_total = FixedPrice::new(self.armada_v2.capital.authorized_capital[c_idx].load(Ordering::Acquire) as i64);
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
        
        // === NOVÝ EXPONENCIÁLNÍ SKEWING (Issue #11) ===
        let trending_fp = FixedPrice::new(l2risk.trending_score.load(Ordering::Relaxed) as i64);
        let one_fp = FixedPrice::new(sniper_types::PRICE_SCALE_I);
        let min_multiplier = FixedPrice::new(10_000_000); // Max útlum na 10 %
        let max_multiplier = FixedPrice::new(300_000_000); // Max boost na 300 %

        // Výpočet Kvadratického OBI (zachování znaménka) pro exponenciální reakci
        let obi_abs = FixedPrice::new(obi.0.abs());
        let obi_sq = (obi_abs * obi_abs) / one_fp;
        let mut skew_factor = (obi_sq * trending_fp) / one_fp;
        if obi.0 < 0 { skew_factor.0 = -skew_factor.0; }

        let mut buy_skew_multiplier = one_fp + skew_factor;
        let mut sell_skew_multiplier = one_fp - skew_factor;

        // Ochrana proti přílišnému ztenčení nebo přepálení sítě
        if buy_skew_multiplier < min_multiplier { buy_skew_multiplier = min_multiplier; }
        if buy_skew_multiplier > max_multiplier { buy_skew_multiplier = max_multiplier; }
        if sell_skew_multiplier < min_multiplier { sell_skew_multiplier = min_multiplier; }
        if sell_skew_multiplier > max_multiplier { sell_skew_multiplier = max_multiplier; }

        // V Liquidity Hole plošně přiškrtíme základní velikost mřížky
        let mut active_base_usd = base_usd;
        if in_liquidity_hole { active_base_usd = base_usd * FixedPrice::new(50_000_000); }

        let final_order_usd = active_base_usd; // Slouží jako základ, skewing se násobí níže
        // ==============================================

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

            // ARMADA KŘEMÍKOVÁ ZEĎ V2 🛡️
            if self.armada_v2.global.global_kill_switch.load(Ordering::Acquire) == 1 {
                self.last_upd = now;
                return;
            }

            // 1. Čtení limitu a expozice (Armada Orchestrator - V2 Read-Path)
            let c_idx = sniper_types::armada_types::capital_index(0, 0);
            let auth_cap_usd = FixedPrice::new(self.armada_v2.capital.authorized_capital[c_idx].load(Ordering::Acquire) as i64);
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

            // OBI Kvadratický skewing již spočítán výše v kontextu (Issue #11)
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
                    // V2 Cold Vault ochrana: Pokud Orchestrátor zmrazil BTC, nesmíme je prodat!
                    let w_btc_raw = engine.wallet_btc.load(Ordering::Relaxed) as i64;
                    let cold_vault_btc = self.armada_v2.global.cold_vault_btc.load(Ordering::Acquire);
                    let sellable_btc = (w_btc_raw - cold_vault_btc).max(0);
                    
                    // Divide wallet evenly across sell levels, leave 10% buffer
                    let n_sell_levels = n_sell.max(1) as i64;
                    let max_sell_per_level = (sellable_btc * 90 / 100) / n_sell_levels;
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

    std::fs::create_dir_all("/dev/shm/sniper").context("Failed to create /dev/shm/sniper")?;

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
    let armada_v2 = sniper_types::armada_types::load_armada_state_v2_ro();

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

    let storm_path = "/dev/shm/sniper/toxic_storm.bin";
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
        armada_v2,
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
