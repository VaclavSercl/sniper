// 🐉 Hydra L0 Engine — Advanced Neural Cross Bot
// Framework: SovereignDualRunner (Dual-WS)
use std::sync::atomic::{Ordering, fence};
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH, Duration};
use std::collections::VecDeque;

use anyhow::{Context, Result};
use tracing::{info, error};
use serde_json::json;
use dotenvy::dotenv;

use simd_json::prelude::*;
use simd_json::BorrowedValue;

use sniper_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH};
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::init_mmap;
use sniper_types::framework::{SovereignEngine, SovereignDualRunner};

mod book;
pub use book::*;

mod pnl;
pub use pnl::*;

mod ghost;
pub use ghost::*;

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

fn zero_all_order_slots(eng: &EngineState) {
    for slot in eng.active_buy_ids.iter().chain(eng.active_sell_ids.iter()) {
        slot.store(0, Ordering::SeqCst);
    }
}

struct HydraEngine {
    notifier: Arc<AsyncNotifier>,
    engine: *mut EngineState,
    risk: *const RiskState,
    fee_state: *const sniper_types::fee_types::GlobalFeeState,
    l2cmd: *const sniper_types::l2_command::L2CommandMatrix,
    l2as: *const sniper_types::l2_command::L2ASMatrix,
    l2risk: *const sniper_types::l2_command::L2GlobalRiskMatrix,
    l2portfolio: *const sniper_types::l2_command::L2PortfolioTelemetry,
    l1ring: *const sniper_types::l2_command::L1TelemetryRing,
    
    // local loop variables
    chan_id: Option<i64>,
    last_upd: Instant,
    snapshot_loaded: bool,
    cs_debug_count: u32,
    cs_fail_count: u32,
    depth_history: VecDeque<f64>,
    was_in_hole: bool,

    ghost_last_micro: i64,
    ghost_velocity: f64,
    ghost_velocity_max: f64,
    ghost_last_inject: Instant,
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
        info!(event = "system_start", version = "12.1.0-hydra");

        Ok(())
    }

    fn on_auth(&mut self) {
        info!(event = "exec_auth_ok", bot = "hydra");
    }

    fn on_system_event(&mut self, value: &serde_json::Value, _out_buf: &mut bytes::BytesMut) {
        if value["event"] == "subscribed" {
            self.chan_id = value["chanId"].as_i64();
            self.snapshot_loaded = false;
            info!(event = "mdata_subscribed", chan_id = ?self.chan_id);
        } else if value["event"] == "error" {
            info!(event = "mdata_error", msg = ?value["msg"], code = ?value["code"]);
        }
    }

    fn on_shutdown(&mut self, out_buf: &mut bytes::BytesMut) {
        self.notifier.alert("🛑 *Shutdown*: cancelling orders...".to_string());
        let eng = unsafe { &*self.engine };
        let active_ids = collect_all_order_ids(eng);

        let cancel_msg = if !active_ids.is_empty() {
            let ids_str: Vec<String> = active_ids.iter().map(|id| id.to_string()).collect();
            format!(r#"[0,"oc_multi",null,{{"id":[{}]}}]"#, ids_str.join(","))
        } else {
            r#"[0,"oc_multi",null,{"all":1}]"#.to_string()
        };
        out_buf.extend_from_slice(cancel_msg.as_bytes());
        info!(event = "shutdown_cancel_sent");
    }

    fn on_market_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut) {
        let v = match simd_json::to_borrowed_value(payload) {
            Ok(v) => v,
            Err(_) => return,
        };

        if let BorrowedValue::Array(arr) = v {
            let engine = unsafe { &mut *self.engine };
            let _risk = unsafe { &*self.risk };
            
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
                        if self.cs_fail_count >= 5 {
                            error!(event = "checksum_persist", remote = remote_cs, local = local_cs);
                            panic!("L2 Checksum Drift detected (5 failures). Sovereign kill triggered to force SBP L2 Reconstruction.");
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
                        
                        // CLEAR ENTIRE BOOK BEFORE PROCESSING SNAPSHOT TO AVOID PHANTOM LIQUIDITY
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
                            info!(event = "raw_mdata_in", msg = ?entry);
                            if let Some(u) = entry.as_array()
                                && let (Some(price), Some(count), Some(amount)) = (safe_as_f64(&u[0]), safe_as_i64(&u[1]), safe_as_f64(&u[2])) {
                                    let p = (price * sniper_types::PRICE_SCALE).round() as u64;
                                    let a = (amount * sniper_types::PRICE_SCALE).round() as i64;
                                    let c = count as u64;
                                    if amount > 0.0 { update_book(&mut engine.bids, p, a, c); bc += 1; }
                                    else { update_book(&mut engine.asks, p, a, c); ac += 1; }
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
                        info!(event = "raw_mdata_single", msg = ?top_arr);
                        if let (Some(price), Some(count), Some(amount)) = (safe_as_f64(&top_arr[0]), safe_as_i64(&top_arr[1]), safe_as_f64(&top_arr[2])) {
                            let p = (price * sniper_types::PRICE_SCALE).round() as u64;
                            let a = (amount * sniper_types::PRICE_SCALE).round() as i64;
                            let c = count as u64;
                            if amount > 0.0 { update_book(&mut engine.bids, p, a, c); sort_book(&mut engine.bids, true); }
                            else { update_book(&mut engine.asks, p, a, c); sort_book(&mut engine.asks, false); }
                        }
                    }
                }

                let best_bid = engine.bids[0].price.load(Ordering::SeqCst);
                let best_ask = engine.asks[0].price.load(Ordering::SeqCst);
                engine.best_bid.store(best_bid, Ordering::SeqCst);
                engine.best_ask.store(best_ask, Ordering::SeqCst);

                // GHOST PROXIMITY CHECK (runs on EVERY book tick)
                if best_bid > 0 && best_ask > 0 {
                    let ghost_trans = engine.ghost_transparency.load(Ordering::Relaxed);
                    if ghost_trans < 10000 {
                        let bid_vol_g = engine.bids[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                        let ask_vol_g = engine.asks[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
                        let total_vol_g = bid_vol_g + ask_vol_g;
                        let micro_g = if total_vol_g > 0.0 {
                            ((best_bid as f64 * ask_vol_g + best_ask as f64 * bid_vol_g) / total_vol_g).round() as i64
                        } else {
                            ((best_bid as i64) + (best_ask as i64)) / 2
                        };

                        if self.ghost_last_micro > 0 {
                            let delta = (micro_g - self.ghost_last_micro).abs() as f64;
                            self.ghost_velocity = self.ghost_velocity * 0.8 + delta * 0.2;
                        }
                        self.ghost_last_micro = micro_g;

                        let ai_trigger = engine.ai_ghost_trigger_pct.load(Ordering::Relaxed).clamp(10, 500) as f64 / 100000.0;
                        let trigger_dist = (micro_g as f64 * ai_trigger) as i64;
                        let velocity_safe = self.ghost_velocity < self.ghost_velocity_max;
                        let min_inject_interval = self.ghost_last_inject.elapsed().as_millis() > 200;

                        if velocity_safe && min_inject_interval {
                            let mut mask = engine.ghost_active_mask.load(Ordering::Relaxed);
                            for i in 0..sniper_types::MAX_GRID_LEVELS {
                                let gbp = engine.ghost_buy_prices[i].load(Ordering::Relaxed);
                                if gbp > 0 {
                                    let dist = (micro_g - gbp).abs();
                                    let bit = 1u64 << i;
                                    if dist < trigger_dist && (mask & bit) == 0 {
                                        let gbp_u = gbp.max(1) as u64;
                                        let usd = engine.current_order_usd.load(Ordering::Relaxed);
                                        let mut amt_i = (usd * sniper_types::PRICE_SCALE as u64) / gbp_u;
                                        if amt_i < 15000 { amt_i = 15000; }
                                        
                                        let amt_f = (amt_i as f64) / sniper_types::PRICE_SCALE as f64;
                                        let price_f = (gbp as f64) / sniper_types::PRICE_SCALE;

                                        let mut ryu1 = ryu::Buffer::new();
                                        let mut ryu2 = ryu::Buffer::new();
                                        sniper_types::exchange::bitfinex_venue::BitfinexVenue::write_standalone_ioc(
                                            out_buf, sniper_types::BOT_GID_HYDRA,
                                            sniper_types::TRADING_SYMBOL.as_bytes(),
                                            ryu1.format(amt_f), ryu2.format(price_f),
                                        );
                                        
                                        mask |= bit;
                                        engine.ghost_active_mask.store(mask, Ordering::Relaxed);
                                        engine.ghost_injections.fetch_add(1, Ordering::Relaxed);
                                        self.ghost_last_inject = Instant::now();
                                    } else if dist >= trigger_dist * 3 && (mask & bit) != 0 {
                                        mask &= !bit;
                                        engine.ghost_active_mask.store(mask, Ordering::Relaxed);
                                    }
                                }
                                let gsp = engine.ghost_sell_prices[i].load(Ordering::Relaxed);
                                if gsp > 0 {
                                    let dist = (micro_g - gsp).abs();
                                    let bit = 1u64 << (i + sniper_types::MAX_GRID_LEVELS);
                                    if dist < trigger_dist && (mask & bit) == 0 {
                                        let gsp_u = gsp.max(1) as u64;
                                        let usd = engine.current_order_usd.load(Ordering::Relaxed);
                                        let mut amt_i = (usd * sniper_types::PRICE_SCALE as u64) / gsp_u;
                                        if amt_i < 15000 { amt_i = 15000; }
                                        
                                        let amt_f = -((amt_i as f64) / sniper_types::PRICE_SCALE as f64);
                                        let price_f = (gsp as f64) / sniper_types::PRICE_SCALE;

                                        let mut ryu1 = ryu::Buffer::new();
                                        let mut ryu2 = ryu::Buffer::new();
                                        sniper_types::exchange::bitfinex_venue::BitfinexVenue::write_standalone_ioc(
                                            out_buf, sniper_types::BOT_GID_HYDRA,
                                            sniper_types::TRADING_SYMBOL.as_bytes(),
                                            ryu1.format(amt_f), ryu2.format(price_f),
                                        );
                                        
                                        mask |= bit;
                                        engine.ghost_active_mask.store(mask, Ordering::Relaxed);
                                        engine.ghost_injections.fetch_add(1, Ordering::Relaxed);
                                        self.ghost_last_inject = Instant::now();
                                    } else if dist >= trigger_dist * 3 && (mask & bit) != 0 {
                                        mask &= !bit;
                                        engine.ghost_active_mask.store(mask, Ordering::Relaxed);
                                    }
                                }
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
                        if let (Some(trade_amt), Some(trade_price)) = (safe_as_f64(&trade[4]), safe_as_f64(&trade[5])) {
                            pnl::process_trade(engine, trade_amt, trade_price);
                        }
                    }
                } else if mt == "wu" || mt == "ws" {
                    let wd: Vec<&BorrowedValue> = if mt == "wu" {
                        vec![&arr[2]]
                    } else {
                        arr[2].as_array().map(|a| a.iter().collect()).unwrap_or_default()
                    };
                    for w in wd {
                        if let Some(w_arr) = w.as_array() {
                            if let (Some(wt), Some(cur), Some(bal)) = (w_arr.get(0).and_then(|x| x.as_str()), w_arr.get(1).and_then(|x| x.as_str()), w_arr.get(2).and_then(|x| safe_as_f64(x))) {
                                if wt == "exchange" {
                                    if cur == sniper_types::TRADING_BASE { engine.wallet_btc.store((bal * sniper_types::PRICE_SCALE) as u64, Ordering::SeqCst); }
                                    else if cur == sniper_types::TRADING_QUOTE || cur == "UST" { engine.wallet_usd.store((bal * sniper_types::PRICE_SCALE) as u64, Ordering::SeqCst); }
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
                                    o[3].as_str(), safe_as_f64(&o[6]), o[13].as_str()
                                )
                                && sym == sniper_types::TRADING_SYMBOL && (status.contains("ACTIVE") || status.contains("PARTIALLY")) {
                                    if amt > 0.0 { store_order_slot(&engine.active_buy_ids, id); }
                                    else { store_order_slot(&engine.active_sell_ids, id); }
                                }
                        }
                    }
                } else if mt == "on" || mt == "ou" || mt == "oc" {
                    if let Some(o) = arr.get(2).and_then(|a| a.as_array())
                        && let (Some(id), Some(sym), Some(amt), Some(status)) = (
                            o[0].as_u64().or_else(|| o[0].as_f64().map(|f| f as u64)),
                            o[3].as_str(), safe_as_f64(&o[6]), o[13].as_str()
                        )
                        && sym == sniper_types::TRADING_SYMBOL {
                            if status.contains("ACTIVE") || status.contains("PARTIALLY") {
                                if amt > 0.0 { store_order_slot(&engine.active_buy_ids, id); }
                                else { store_order_slot(&engine.active_sell_ids, id); }
                            } else if status.contains("CANCELED") || status.contains("EXECUTED") {
                                if amt > 0.0 { clear_order_slot(&engine.active_buy_ids, id); }
                                else { clear_order_slot(&engine.active_sell_ids, id); }
                            }
                        }
                }
            }
        } // ends BorrowedValue::Array
    } // ends on_market_message

    fn on_loop(&mut self, out_buf: &mut bytes::BytesMut) {
        let engine = unsafe { &*self.engine };
        let risk = unsafe { &*self.risk };
        let l2cmd = unsafe { &*self.l2cmd };
        let l2risk = unsafe { &*self.l2risk };
        let _l2as = unsafe { &*self.l2as };
        let fee_state = unsafe { &*self.fee_state };

        let best_bid = engine.best_bid.load(Ordering::Acquire);
        let best_ask = engine.best_ask.load(Ordering::Acquire);
        
        const MIN_TICK: i64 = 100_000_000;
        if best_bid == 0 || best_ask == 0 { return; }

        let now = Instant::now();
        let fire_ai = engine.ai_fire_interval_ms.load(Ordering::Relaxed).clamp(500, 10000);
        let anti_flicker = engine.ai_min_order_lifetime_ms.load(Ordering::Relaxed).clamp(50, 5000);
        let fire_interval = fire_ai.max(anti_flicker);
        
        if now.duration_since(self.last_upd).as_millis() <= fire_interval as u128 || risk.paused.load(Ordering::Acquire) != 0 {
            return;
        }

        let freeze_until = engine.sweep_freeze_until.load(Ordering::Acquire);
        if freeze_until > 0 {
            let now_ms_check = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
            if now_ms_check < freeze_until {
                self.last_upd = now; // prevent rapid retries
                return;
            } else {
                engine.sweep_freeze_until.store(0, Ordering::Release);
            }
        }

        // ═══ HIVE MIND: Cross-Bot Toxic Storm (SIM v2.0) ═══
        // 1-byte mmap written by ML Shield (50ms) + Rust Sentinel (5s)
        // 0 = clear, 1 = TOXIC STORM → skip order placement
        if let Ok(flag) = std::fs::read("/dev/shm/beroun/toxic_storm.bin") {
            if !flag.is_empty() && flag[0] == 1 {
                self.last_upd = now;
                return; // All bots defensive — no new orders
            }
        }

        let mid_i = ((best_bid as i64) + (best_ask as i64)) / 2;
        let bid_vol_0 = engine.bids[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
        let ask_vol_0 = engine.asks[0].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
        let total_vol_0 = bid_vol_0 + ask_vol_0;
        let micro_i = if total_vol_0 > 0.0 {
            ((best_bid as f64 * ask_vol_0 + best_ask as f64 * bid_vol_0) / total_vol_0).round() as i64
        } else { mid_i };
        let micro_f = micro_i as f64 / sniper_types::PRICE_SCALE;

        let bnb_mid_raw = engine.binance_mid_price.load(Ordering::Relaxed);
        let fair_value_i = if bnb_mid_raw > 0 {
            let local_w = (micro_i as f64) * 0.6;
            let bnb_w = (bnb_mid_raw as f64) * 0.3;
            let macro_raw = engine.macro_bias.load(Ordering::Relaxed);
            let macro_ts = engine.macro_source_ts.load(Ordering::Relaxed);
            let sentinel_now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
            let macro_shift = if sentinel_now.saturating_sub(macro_ts) < 600_000 {
                micro_i as f64 * 0.001 * (macro_raw as f64 / 10000.0) * 0.1
            } else { 0.0 };
            let fv = local_w + bnb_w + macro_shift;
            engine.global_fair_value.store(fv.round() as i64, Ordering::Relaxed);
            fv.round() as i64
        } else {
            engine.global_fair_value.store(micro_i, Ordering::Relaxed);
            micro_i
        };

        let divergence = (fair_value_i - micro_i).abs() as f64 / micro_i as f64;
        if divergence > 0.0005 && bnb_mid_raw > 0 {
            for lvl in 0..sniper_types::MAX_GRID_LEVELS {
                let ghost_buy = engine.ghost_buy_prices[lvl].load(Ordering::Relaxed);
                let ghost_sell = engine.ghost_sell_prices[lvl].load(Ordering::Relaxed);
                if ghost_buy != 0 {
                    let shift = ((fair_value_i - micro_i) as f64 * 0.5).round() as i64;
                    engine.ghost_buy_prices[lvl].store(ghost_buy + shift, Ordering::Relaxed);
                }
                if ghost_sell != 0 {
                    let shift = ((fair_value_i - micro_i) as f64 * 0.5).round() as i64;
                    engine.ghost_sell_prices[lvl].store(ghost_sell + shift, Ordering::Relaxed);
                }
            }
            engine.sentinel_repositions.fetch_add(1, Ordering::Relaxed);
        }

        let delta_lead_bps = if bnb_mid_raw > 0 && micro_i > 0 {
            let raw = ((bnb_mid_raw as f64 - micro_i as f64) / micro_i as f64) * 1_000_000.0;
            engine.delta_lead_raw_bps.store(raw.round() as i64, Ordering::Relaxed);
            raw
        } else {
            engine.delta_lead_raw_bps.store(0, Ordering::Relaxed);
            0.0
        };
        let delta_signal = (delta_lead_bps * 100.0).round() as i64;
        engine.delta_lead_signal.store(delta_signal.max(-10000).min(10000), Ordering::Relaxed);

        let delta_threshold = engine.ai_delta_threshold_bps.load(Ordering::Relaxed) as f64;
        let delta_active = delta_lead_bps.abs() > delta_threshold && bnb_mid_raw > 0;

        let mut sum_bid_vol: f64 = 0.0;
        let mut sum_ask_vol: f64 = 0.0;
        for i in 0..10 {
            sum_bid_vol += engine.bids[i].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
            sum_ask_vol += engine.asks[i].amount.load(Ordering::Relaxed).unsigned_abs() as f64;
        }
        let obi = if (sum_bid_vol + sum_ask_vol) > 0.0 {
            (sum_bid_vol - sum_ask_vol) / (sum_bid_vol + sum_ask_vol)
        } else { 0.0 };

        let total_depth = sum_bid_vol + sum_ask_vol;
        self.depth_history.push_back(total_depth);
        if self.depth_history.len() > 60 { self.depth_history.pop_front(); }
        let avg_depth = if !self.depth_history.is_empty() {
            self.depth_history.iter().sum::<f64>() / self.depth_history.len() as f64
        } else { total_depth };

        let liquidity_ratio = if avg_depth > 0.0 { total_depth / avg_depth } else { 1.0 };
        let in_liquidity_hole = liquidity_ratio < 0.5;
        let hole_recovering = liquidity_ratio >= 0.7;

        if in_liquidity_hole && !self.was_in_hole { self.was_in_hole = true; }
        else if hole_recovering && self.was_in_hole { self.was_in_hole = false; }

        let base_usd = risk.order_usd.load(Ordering::Acquire) as f64;
        let micro_bias = micro_i - mid_i;
        let final_order_usd = if in_liquidity_hole { base_usd * 0.5 }
        else if (obi > 0.2 && micro_bias > 0) || (obi < -0.2 && micro_bias < 0) { (base_usd * 1.5).clamp(base_usd * 0.5, base_usd * 2.0) }
        else if (obi > 0.1 && micro_bias < 0) || (obi < -0.1 && micro_bias > 0) { (base_usd * 0.7).clamp(base_usd * 0.5, base_usd * 2.0) }
        else { base_usd };

        let mut grid = risk.grid_step.load(Ordering::Acquire) as i64;
        if in_liquidity_hole { grid = (grid * 3).min(2_000_000_000); }
        let current_pos = engine.net_position.load(Ordering::Acquire);
        let max_pos = risk.max_inv_delta.load(Ordering::Acquire) as i64;
        let inv_skew = if max_pos > 0 {
            let ratio = (current_pos as f64 / max_pos as f64).clamp(-1.0, 1.0);
            (-ratio * grid as f64 * 2.0).round() as i64
        } else { 0 };

        let raw_bias = risk.bias_offset.load(Ordering::Acquire);
        let ai_hb = engine.ai_heartbeat_ms.load(Ordering::Acquire);
        let now_ms = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        let bias = if ai_hb > 0 && now_ms.saturating_sub(ai_hb) > 30_000 { 0 } else { raw_bias };
        
        let l1_skew = engine.l1_skew_adjustment.load(Ordering::Acquire);
        let macro_raw = engine.macro_bias.load(Ordering::Relaxed);
        let macro_ts = engine.macro_source_ts.load(Ordering::Relaxed);
        let macro_stale = now_ms.saturating_sub(macro_ts) > 600_000;
        let macro_bias_scaled = if !macro_stale && macro_raw.abs() > 500 {
            (grid as f64 * 0.1 * (macro_raw as f64 / 10000.0)).round() as i64
        } else { 0 };

        let bnb_sweep_ts = engine.binance_sweep_ts.load(Ordering::Relaxed);
        let bnb_sweep_age_ms = now_ms.saturating_sub(bnb_sweep_ts);
        if bnb_sweep_ts > 0 && bnb_sweep_age_ms < 3000 {
            let current_trans = engine.ghost_transparency.load(Ordering::Relaxed);
            if current_trans > 2000 { engine.ghost_transparency.store(1000, Ordering::Relaxed); }
        }
        let final_bias_with_l1 = bias + inv_skew + l1_skew + macro_bias_scaled;

        let delta_shift = if delta_active {
            let raw_shift = (delta_lead_bps / 100.0) * (micro_i as f64 / sniper_types::PRICE_SCALE);
            let max_shift = grid as f64 * 0.25;
            let clamped = raw_shift.max(-max_shift).min(max_shift);
            let shift_i = (clamped * sniper_types::PRICE_SCALE).round() as i64;
            engine.delta_repositions.fetch_add(1, Ordering::Relaxed);
            shift_i
        } else { 0 };

        unsafe { &*self.l2portfolio }.hydra_inventory.store(current_pos, Ordering::Relaxed);
        let (as_bid, as_sell) = sniper_types::l2_command::calculate_as_quotes(l2cmd, unsafe{&*self.l2as}, micro_i, current_pos);
        let vpin_bps = sniper_types::l2_command::vpin_shift_bps(l2risk);
        let vpin_delta = vpin_bps * (micro_i / 10_000);

        if sniper_types::l2_command::is_flash_crash_active(l2risk) {
            let drop = l2risk.flash_crash_drop_bps.load(Ordering::Relaxed);
            self.notifier.alert(format!("🚨 CROSS-BOT EMERGENCY: Flash crash {}bps detected by Moonshot — cancelling all Hydra orders!", drop));
            out_buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"all\":1}]");
            return;
        }

        let mut buy_i = (as_bid + vpin_delta - grid + final_bias_with_l1 + delta_shift).max(0);
        let mut sell_i = (as_sell + vpin_delta + grid + final_bias_with_l1 + delta_shift).max(0);

        let ba_i = best_ask as i64;
        let bb_i = best_bid as i64;
        if buy_i >= ba_i { buy_i = ba_i - MIN_TICK; }
        if sell_i <= bb_i { sell_i = bb_i + MIN_TICK; }
        if buy_i >= sell_i { return; }

        let spread_bps = ((sell_i - buy_i) as f64 / micro_i as f64 * 10000.0) as u64;
        if !ghost::is_spread_profitable(spread_bps, fee_state) {
            engine.fee_kills.fetch_add(1, Ordering::Relaxed);
            return;
        }

        let lb = engine.last_buy_price.load(Ordering::SeqCst);
        let ls = engine.last_sell_price.load(Ordering::SeqCst);
        let db = (buy_i - lb).abs();
        let ds = (sell_i - ls).abs();

        if db >= MIN_TICK || ds >= MIN_TICK || lb == 0 {
            let dll = risk.daily_loss_limit.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;
            let r_pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;
            if dll > 0.0 && r_pnl < -dll {
                let cancel_ids = collect_all_order_ids(engine);
                if !cancel_ids.is_empty() {
                    out_buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"id\":[");
                    let mut itoa_buf = itoa::Buffer::new();
                    for (i, &id) in cancel_ids.iter().enumerate() {
                        if i > 0 { out_buf.extend_from_slice(b","); }
                        out_buf.extend_from_slice(itoa_buf.format(id).as_bytes());
                    }
                    out_buf.extend_from_slice(b"]}]");
                }
                risk.paused.store(1, Ordering::SeqCst);
                self.notifier.alert(format!("🛑 *EMERGENCY STOP*\nDaily Loss Limit reached: `${:.2}` (limit `-${:.2}`)\nAll orders cancelled. System *LOCKED*.", r_pnl, dll));
                return;
            }

            let grid_levels = risk.grid_size.load(Ordering::Acquire).clamp(1, sniper_types::MAX_GRID_LEVELS as u64) as usize;
            let max_pos = risk.max_inv_delta.load(Ordering::Acquire) as i64;
            let pos_ratio = if max_pos > 0 { (current_pos as f64 / max_pos as f64).clamp(-1.0, 1.0) } else { 0.0 };

            let (n_buy, n_sell): (usize, usize) = if pos_ratio > 0.8 { (0, grid_levels) }
            else if pos_ratio > 0.4 { (1, grid_levels) }
            else if pos_ratio < -0.8 { (grid_levels, 0) }
            else if pos_ratio < -0.4 { (grid_levels, 1) }
            else { (grid_levels, grid_levels) };

            let cancel_ids = collect_all_order_ids(engine);
            
            let mut out_len_snap = out_buf.len();
            use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
            BitfinexVenue::write_batch_open(out_buf);
            
            let mut has_items = false;
            let mut itoa_buf = itoa::Buffer::new();

            if !cancel_ids.is_empty() {
                out_buf.extend_from_slice(b"[\"oc_multi\",{\"id\":[");
                for (i, &id) in cancel_ids.iter().enumerate() {
                    if i > 0 { out_buf.extend_from_slice(b","); }
                    out_buf.extend_from_slice(itoa_buf.format(id).as_bytes());
                }
                out_buf.extend_from_slice(b"]}]");
                has_items = true;
            }

            const MIN_ORDER_BTC: f64 = 0.00015;
            let w_btc = engine.wallet_btc.load(Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE;
            let w_usd = engine.wallet_usd.load(Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE;
            let amt = (final_order_usd / micro_f / sniper_types::PRICE_SCALE).max(MIN_ORDER_BTC);

            let auth_cap = risk.authorized_capital.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;
            let mid_price = micro_i as f64 / sniper_types::PRICE_SCALE;
            let pos_f64 = current_pos as f64 / sniper_types::PRICE_SCALE;
            let pos_value = pos_f64.abs() * mid_price;

            let cap_available = if pos_f64 < 0.0 || auth_cap <= 0.0 { w_usd } else { (auth_cap - pos_value).max(0.0).min(w_usd) };
            let btc_cap = if pos_f64 > 0.0 || auth_cap <= 0.0 { w_btc } else if mid_price > 0.0 { (auth_cap / mid_price).min(w_btc) } else { w_btc };

            let mut total_buy_usd = 0.0;
            let mut total_sell_btc = 0.0;

            let gc = ghost::calculate_ghost_levels(engine, n_buy, n_sell);
            let n_public_buy = gc.n_public_buy;
            let n_public_sell = gc.n_public_sell;
            let ghost_mode = gc.ghost_mode;

            let mut fbuf_amt = ryu::Buffer::new();
            let mut fbuf_price = ryu::Buffer::new();

            for i in 0..n_buy {
                let spacing = (grid as f64 * sniper_types::LEVEL_SPACING[i]) as i64;
                let bp_i = (micro_i - spacing + final_bias_with_l1).max(0).min(ba_i - MIN_TICK);
                let bp = bp_i as f64 / sniper_types::PRICE_SCALE;
                let cost = amt * bp;

                if i < n_public_buy {
                    if total_buy_usd + cost <= cap_available * 0.95 && amt >= MIN_ORDER_BTC {
                        if has_items { out_buf.extend_from_slice(b","); }
                        out_buf.extend_from_slice(b"[\"on\",{\"gid\":");
                        out_buf.extend_from_slice(itoa_buf.format(sniper_types::BOT_GID_HYDRA).as_bytes());
                        out_buf.extend_from_slice(b",\"symbol\":\"");
                        out_buf.extend_from_slice(sniper_types::TRADING_SYMBOL.as_bytes());
                        out_buf.extend_from_slice(b"\",\"amount\":\"");
                        out_buf.extend_from_slice(fbuf_amt.format_finite(amt).as_bytes());
                        out_buf.extend_from_slice(b"\",\"price\":\"");
                        out_buf.extend_from_slice(fbuf_price.format_finite(bp).as_bytes());
                        out_buf.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\",\"flags\":4096}]");
                        
                        has_items = true;
                        total_buy_usd += cost;
                    }
                } else if ghost_mode {
                    engine.ghost_buy_prices[i].store(bp_i, Ordering::Relaxed);
                }
            }

            for i in 0..n_sell {
                let spacing = (grid as f64 * sniper_types::LEVEL_SPACING[i]) as i64;
                let sp_i = (micro_i + spacing + final_bias_with_l1).max(0).max(bb_i + MIN_TICK);
                let sp = sp_i as f64 / sniper_types::PRICE_SCALE;

                if i < n_public_sell {
                    if total_sell_btc + amt <= btc_cap * 0.95 && amt >= MIN_ORDER_BTC {
                        if has_items { out_buf.extend_from_slice(b","); }
                        out_buf.extend_from_slice(b"[\"on\",{\"gid\":");
                        out_buf.extend_from_slice(itoa_buf.format(sniper_types::BOT_GID_HYDRA).as_bytes());
                        out_buf.extend_from_slice(b",\"symbol\":\"");
                        out_buf.extend_from_slice(sniper_types::TRADING_SYMBOL.as_bytes());
                        out_buf.extend_from_slice(b"\",\"amount\":\"");
                        out_buf.extend_from_slice(fbuf_amt.format_finite(-amt).as_bytes());
                        out_buf.extend_from_slice(b"\",\"price\":\"");
                        out_buf.extend_from_slice(fbuf_price.format_finite(sp).as_bytes());
                        out_buf.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\",\"flags\":4096}]");
                        
                        has_items = true;
                        total_sell_btc += amt;
                    }
                } else if ghost_mode {
                    engine.ghost_sell_prices[i].store(sp_i, Ordering::Relaxed);
                }
            }

            if !has_items { 
                out_buf.truncate(out_len_snap);
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
            engine.l2_imbalance.store((obi * sniper_types::PRICE_SCALE) as i64, Ordering::Relaxed);
            engine.current_order_usd.store(final_order_usd as u64, Ordering::Relaxed);
            self.last_upd = now;
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
    let fee_mmap = init_mmap::<sniper_types::fee_types::GlobalFeeState>(sniper_types::fee_types::FEE_STATE_PATH)?;
    let l2_mmap = init_mmap::<sniper_types::l2_command::L2SharedState>(sniper_types::l2_command::L2_COMMAND_PATH)?;

    let fee_state = unsafe { &*(fee_mmap.as_ptr() as *const sniper_types::fee_types::GlobalFeeState) };
    let l2_shared = unsafe { &*(l2_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };
    let engine_ptr = engine_mmap.as_mut_ptr() as *mut EngineState;
    let risk_ptr = risk_mmap.as_ptr() as *const RiskState;

    tokio::spawn(async move {
        let mut report_interval = tokio::time::interval(Duration::from_secs(3600));
        loop {
            report_interval.tick().await;
            tokio::task::spawn_blocking(|| {
                let _ = std::process::Command::new(std::env::current_exe().unwrap_or_default().with_file_name("hydra-config"))
                    .arg("export-json")
                    .stdout(std::fs::File::create("/dev/shm/beroun/state.json").unwrap_or_else(|_| std::fs::File::create("/dev/null").unwrap_or_else(|_| std::fs::File::open("/dev/null").unwrap())))
                    .status(); 
            });
        }
    });

    let engine_hb = engine_ptr as usize;
    tokio::spawn(async move {
        loop {
            if let Ok(now) = SystemTime::now().duration_since(UNIX_EPOCH) {
                unsafe { &*(engine_hb as *const EngineState) }.latency_ns.store(now.as_nanos() as u64, Ordering::Release);
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    });

    let vol_engine_ptr = engine_ptr as usize;
    let vol_risk_ptr = risk_ptr as usize;
    tokio::spawn(async move {
        let mut price_history: VecDeque<i64> = VecDeque::with_capacity(61);
        let base_grid: i64 = 200_000_000;    
        let max_grid: i64 = 2_000_000_000;   
        let min_grid: i64 = 200_000_000;     
        let vol_mult: f64 = 0.15;            
        let default_grid: u64 = 300_000_000; 

        let mut fill_check_counter: u32 = 0;
        let mut grid_mult: f64 = 1.0;        

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

                fill_check_counter += 1;
                if fill_check_counter >= 120 {
                    fill_check_counter = 0;
                    let buys = engine.buy_fill_count.swap(0, Ordering::Relaxed);
                    let sells = engine.sell_fill_count.swap(0, Ordering::Relaxed);
                    let total = buys + sells;

                    if total >= 4 {
                        let balance = 1.0 - ((buys as f64 - sells as f64).abs() / total as f64);
                        let fill_rate = total as f64;
                        let target_mult = if balance > 0.6 && fill_rate > 10.0 { 0.7 } else if balance > 0.4 { 1.0 } else { 1.4 };
                        grid_mult = grid_mult + (target_mult - grid_mult) * 0.2;
                        grid_mult = grid_mult.clamp(0.7, 1.5);
                    }
                }

                if price_history.len() >= 10 {
                    if let (Some(&min_p), Some(&max_p)) = (price_history.iter().min(), price_history.iter().max()) {
                        let range = max_p - min_p;
                        let dynamic = (range as f64 * vol_mult) as i64;
                        let adapted = ((base_grid + dynamic) as f64 * grid_mult) as i64;
                        let new_grid = adapted.clamp(min_grid, max_grid) as u64;

                        let l2_action_ms = engine.l2_last_action_ms.load(Ordering::Acquire);
                        let now_ms_vol = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
                        let l2_active = l2_action_ms > 0 && now_ms_vol.saturating_sub(l2_action_ms) < 600_000;

                        if !l2_active { risk.grid_step.store(new_grid, Ordering::Release); }
                    }
                } else {
                    risk.grid_step.store(default_grid, Ordering::Release);
                }
            }
        }
    });

    let engine = HydraEngine {
        notifier,
        engine: engine_ptr,
        risk: risk_ptr,
        fee_state,
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
        depth_history: VecDeque::with_capacity(61),
        was_in_hole: false,
        ghost_last_micro: 0,
        ghost_velocity: 0.0,
        ghost_velocity_max: 500_000_000.0,
        ghost_last_inject: Instant::now(),
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignDualRunner::new(engine, venue, "Hydra");
    runner.run().await?;
    
    Ok(())
}
