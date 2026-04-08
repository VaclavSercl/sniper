# MOONSHOT (Issue 12) - Analýza Mozku

## moonshot/src/main.rs
```rust
// 🚀 Moonshot L0 Engine — Flash Crash & Volatility Bot
// Sniper Armada · Bot #2 · v14.4.0 (Zero-Allocation / Zero-f64 Refactor)
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use anyhow::Result;
use tracing::{info, warn};
use serde_json::json;

use sniper_types::moonshot_types::*;
use sniper_types::PRICE_SCALE_I;
use sniper_types::framework::{SovereignEngine, SovereignDualRunner};
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::init_mmap;
use sniper_types::math::FixedPrice;
mod book;
use book::*;

#[inline(always)]
fn get_book_wap(book: &[sniper_types::OrderBookLevel; 25], target_usd_fp: FixedPrice) -> FixedPrice {
    let mut remaining_usd = target_usd_fp.0;
    let mut total_coin_cost = FixedPrice::zero();

    for level in book.iter() {
        let price = level.price.load(Ordering::Relaxed) as i64;
        let count = level.count.load(Ordering::Relaxed);
        let amount = level.amount.load(Ordering::Relaxed).abs();
        
        if price > 0 && count > 0 {
            let p_fp = FixedPrice::new(price);
            let a_fp = FixedPrice::new(amount);
            let level_usd = p_fp * a_fp;
            
            if level_usd.0 >= remaining_usd {
                let fraction_coin = FixedPrice::new(remaining_usd) / p_fp;
                total_coin_cost += fraction_coin;
                remaining_usd = 0;
                break;
            } else {
                total_coin_cost += a_fp;
                remaining_usd -= level_usd.0;
            }
        }
    }
    
    if total_coin_cost.0 > 0 {
        FixedPrice::new(target_usd_fp.0 - remaining_usd) / total_coin_cost
    } else {
        FixedPrice::zero()
    }
}

const VERSION: &str = "14.4.0-moonshot-zerofpu";
const MAX_TICKER_SLOTS: usize = MOONSHOT_MAX_PAIRS;

/// Cache-friendly flat lookup table replacing HashMap for channel IDs.
struct FlatMapI64 {
    keys: [i64; MAX_TICKER_SLOTS],
    vals: [usize; MAX_TICKER_SLOTS],
    len: usize,
}

impl FlatMapI64 {
    const fn new() -> Self {
        Self { keys: [0; MAX_TICKER_SLOTS], vals: [0; MAX_TICKER_SLOTS], len: 0 }
    }

    #[inline]
    fn get(&self, key: i64) -> Option<usize> {
        for i in 0..self.len {
            if self.keys[i] == key { return Some(self.vals[i]); }
        }
        None
    }

    #[inline]
    fn insert(&mut self, key: i64, val: usize) {
        for i in 0..self.len {
            if self.keys[i] == key { self.vals[i] = val; return; }
        }
        if self.len < MAX_TICKER_SLOTS {
            self.keys[self.len] = key;
            self.vals[self.len] = val;
            self.len += 1;
        }
    }
}

// L0 Stack-only řetězec nahrazující alokaci Stringu v poli párů
struct FixedSymbol {
    buf: [u8; 16],
    len: usize,
}

impl FixedSymbol {
    const fn new() -> Self { Self { buf: [0; 16], len: 0 } }
    fn from_str(s: &str) -> Self {
        let bytes = s.as_bytes();
        let len = bytes.len().min(16);
        let mut buf = [0; 16];
        buf[..len].copy_from_slice(&bytes[..len]);
        Self { buf, len }
    }
    #[inline(always)]
    fn as_bytes(&self) -> &[u8] { &self.buf[..self.len] }
    #[inline(always)]
    fn as_str(&self) -> &str { unsafe { std::str::from_utf8_unchecked(self.as_bytes()) } }
    #[inline(always)]
    fn is_empty(&self) -> bool { self.len == 0 }
}

// Extrémně rychlý formátovač FixedPrice na string/buff na stacku (Zero-Allocation)
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

use simd_json::prelude::*;
use simd_json::BorrowedValue;

#[inline(always)]
fn extract_u64_scaled(v: &BorrowedValue) -> Option<u64> {
    if let Some(i) = v.as_i64() {
        if i >= 0 { return Some((i * PRICE_SCALE_I) as u64); }
        None
    } else if let Some(f) = v.as_f64() {
        Some((f * sniper_types::PRICE_SCALE).round() as u64)
    } else if let Some(s) = v.as_str() {
        s.parse::<f64>().ok().map(|f| (f * sniper_types::PRICE_SCALE).round() as u64)
    } else {
        None
    }
}

struct MoonshotEngine {
    notifier: Arc<AsyncNotifier>,
    engine: *const MoonshotEngineState,
    risk: *const MoonshotRiskState,
    fee_matrix: *const sniper_types::fee_types::GlobalFeeMatrix,
    l2cmd: *const sniper_types::l2_command::L2CommandMatrix,
    l1ring: *const sniper_types::l2_command::L1TelemetryRing,
    toxic_storm_ptr: *const u8,
    l2_risk: *const sniper_types::l2_command::L2GlobalRiskMatrix,
    
    chan_to_idx: FlatMapI64,
    idx_to_symbol: [FixedSymbol; MOONSHOT_MAX_PAIRS],
    last_order_ts: [Instant; MOONSHOT_MAX_PAIRS],
    armada_state: &'static sniper_types::armada_types::ArmadaState,
    bids: [[sniper_types::OrderBookLevel; 25]; MOONSHOT_MAX_PAIRS],
    asks: [[sniper_types::OrderBookLevel; 25]; MOONSHOT_MAX_PAIRS],
    snapshot_loaded: [bool; MOONSHOT_MAX_PAIRS],
}

unsafe impl Send for MoonshotEngine {}
unsafe impl Sync for MoonshotEngine {}

impl SovereignEngine for MoonshotEngine {
    fn subscriptions(&mut self) -> Vec<String> {
        let risk_state = unsafe { &*self.risk };
        let mut subs = Vec::new();
        subs.push(json!({"event":"conf","flags":131072|536870912}).to_string());
        
        for i in 0..MOONSHOT_MAX_PAIRS {
            let symbol_hash = risk_state.pairs[i].symbol_hash.load(Ordering::Acquire);
            if symbol_hash != 0 {
                let symbol = symbol_hash_to_str(symbol_hash);
                subs.push(json!({
                    "event": "subscribe",
                    "channel": "book",
                    "symbol": symbol,
                    "prec": "P0",
                    "freq": "F0",
                    "len": "25"
                }).to_string());
                self.idx_to_symbol[i] = FixedSymbol::from_str(&symbol);
            }
        }
        subs
    }

    fn on_start(&mut self) -> Result<()> {
        let _lock_guard = sniper_types::lock::ensure_single_instance("moonshot-core")?;
        self.notifier.send(format!("🚀 Moonshot v{} ONLINE", VERSION));
        Ok(())
    }

    fn on_auth(&mut self) {
        info!(event = "authenticated", bot = "moonshot");
    }

    fn is_shadow(&self) -> bool {
        let risk = unsafe { &*self.risk };
        let is_paused = risk.global_paused.load(Ordering::Acquire) != 0;
        let config_shadow = std::fs::read_to_string("config.yaml").unwrap_or_default().contains("is_shadow: true");
        is_paused || config_shadow
    }

    fn best_bid_ask(&self) -> (f64, f64) {
        let e = unsafe { &(*self.engine).pairs[0] }; 
        (
            e.best_bid.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE,
            e.best_ask.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE
        )
    }

    fn add_virtual_pnl(&self, amount: i64) {
        let eng = unsafe { &*self.engine };
        eng.virtual_realized_pnl.fetch_add(amount, Ordering::Relaxed);
    }

    fn on_market_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut) {
        let loop_start = Instant::now();

        // Check authentication / wallet stream
        if payload.len() > 2 && payload[0] == b'[' {
            let mut pl_clone = payload.to_vec();
            if let Ok(v) = simd_json::to_borrowed_value(&mut pl_clone)
                && let Some(arr) = v.as_array()
                    && let Some(0) = arr[0].as_i64()
                        && arr.len() > 1 {
                            let mt = arr[1].as_str().unwrap_or("");
                            if mt == "wu" || mt == "ws" {
                                let iter: Box<dyn Iterator<Item = &BorrowedValue>> = if mt == "wu" {
                                    Box::new(std::iter::once(&arr[2]))
                                } else {
                                    if let Some(a) = arr[2].as_array() { Box::new(a.iter()) } else { Box::new(std::iter::empty()) }
                                };
                                let e_global = unsafe { &*self.engine };
                                for w in iter {
                                    if let Some(w_arr) = w.as_array()
                                        && let (Some(wt), Some(cur), Some(bal)) = (w_arr.get(0).and_then(|x| x.as_str()), w_arr.get(1).and_then(|x| x.as_str()), w_arr.get(2).and_then(|x| extract_u64_scaled(x)))
                                            && wt == "exchange" {
                                                if cur == sniper_types::TRADING_BASE { e_global.wallet_btc.store(bal, Ordering::SeqCst); }
                                                else if cur == sniper_types::TRADING_QUOTE || cur == "UST" { e_global.wallet_usd.store(bal, Ordering::SeqCst); }
                                            }
                                }
                            }
                        }
        }

        let mut pl_clone = payload.to_vec();
        if let Ok(v) = simd_json::to_borrowed_value(&mut pl_clone)
            && let Some(arr) = v.as_array()
                && let Some(chan_id) = arr[0].as_i64()
                    && let Some(idx) = self.chan_to_idx.get(chan_id) {
                        if arr.len() > 1 {
                            let mt = arr[1].as_str().unwrap_or("");
                            if mt == "hb" || mt == "cs" { return; }
                        }
                        
                        if let Some(top_arr) = arr.get(1).and_then(|x| x.as_array()) {
                            let is_nested = top_arr.first().is_some_and(|e| e.as_array().is_some());
                            if is_nested {
                                for entry in top_arr {
                                    if let Some(u) = entry.as_array() {
                                        let price = extract_u64_scaled(&u[0]).unwrap_or(0);
                                        let count = u[1].as_i64().unwrap_or(0) as u64;
                                        if let Some(amount) = u[2].as_f64() {
                                            let amt_i = (amount * sniper_types::PRICE_SCALE).round() as i64;
                                            if amt_i > 0 { update_book(&mut self.bids[idx], price, amt_i, count); }
                                            else { update_book(&mut self.asks[idx], price, amt_i, count); }
                                        }
                                    }
                                }
                                sort_book(&mut self.bids[idx], true);
                                sort_book(&mut self.asks[idx], false);
                                self.snapshot_loaded[idx] = true;
                            } else {
                                let price = extract_u64_scaled(&top_arr[0]).unwrap_or(0);
                                let count = top_arr[1].as_i64().unwrap_or(0) as u64;
                                if let Some(amount) = top_arr[2].as_f64() {
                                    let amt_i = (amount * sniper_types::PRICE_SCALE).round() as i64;
                                    if amt_i > 0 { update_book(&mut self.bids[idx], price, amt_i, count); sort_book(&mut self.bids[idx], true); }
                                    else { update_book(&mut self.asks[idx], price, amt_i, count); sort_book(&mut self.asks[idx], false); }
                                }
                            }

                            let bid = self.bids[idx][0].price.load(Ordering::Acquire);
                            let ask = self.asks[idx][0].price.load(Ordering::Acquire);
                            if bid > 0 && ask > 0 {
                                let mid_price = (bid + ask) / 2;
                                let e = unsafe { &(*self.engine).pairs[idx] };
                                let r = unsafe { &(*self.risk).pairs[idx] };
                                e.best_bid.store(bid, Ordering::Release);
                                e.best_ask.store(ask, Ordering::Release);
                                e.last_trade.store(mid_price, Ordering::Release);
                                e.latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);

                                if self.armada_state.is_kill_switch_active() { return; }

                                let l2cmd = unsafe { &*self.l2cmd };
                                let l2_risk = unsafe { &*self.l2_risk };

                                let _bid_fp = FixedPrice::new(bid as i64);
                                let ask_fp = FixedPrice::new(ask as i64);
                                let mid_fp = FixedPrice::new(mid_price as i64);

                                if let Some(trigger) = sniper_types::l2_command::moonshot_check_and_disarm(l2cmd, mid_price as i64, 1, 0) {
                                    let order_usd_fp = FixedPrice::new(self.armada_state.authorized_capital[1].load(Ordering::Acquire) as i64);
                                    let trigger_fp = FixedPrice::new(trigger);
                                    
                                    if order_usd_fp.0 > 0 && mid_fp.0 > 0 {
                                        // SLIPPAGE MATRIX LOGIC
                                        let wap_fp = get_book_wap(&self.asks[idx], order_usd_fp);
                                        
                                        let mut safe_usd_fp = order_usd_fp;
                                        let mut final_exec_price = mid_fp;
                                        
                                        if wap_fp.0 > 0 {
                                            let std_mid = mid_fp.0.max(1);
                                            let slippage_bps = ((wap_fp.0 - std_mid) * 10000) / std_mid;
                                            if slippage_bps > 30 {
                                                safe_usd_fp = order_usd_fp / FixedPrice::new(2 * sniper_types::PRICE_SCALE_I);
                                                warn!("Slippage Matrix Alarm: {} bps. Cutting volume by half.", slippage_bps);
                                            }
                                            final_exec_price = wap_fp;
                                        }

                                        let coin_amount = (safe_usd_fp / final_exec_price).max(FixedPrice::new(15000));
                                        let symbol = &self.idx_to_symbol[idx];
                                        if !symbol.is_empty() {
                                            let amt_fmt = FixedFormat::new(coin_amount.0);
                                            let price_fmt = FixedFormat::new(final_exec_price.0);

                                            sniper_types::exchange::bitfinex_venue::BitfinexVenue::write_standalone_ioc(
                                                out_buf, 2001, symbol.as_bytes(),
                                                amt_fmt.as_str(), price_fmt.as_str(),
                                            );

                                            warn!(event = "moonshot_fired", symbol = %symbol.as_str(), price = final_exec_price.as_f64());
                                            self.notifier.send(format!("🚀 MOONSHOT FIRED! {} @ ${:.2} (WAP) (trigger ${:.2})", 
                                                symbol.as_str(), final_exec_price.as_f64(), trigger_fp.as_f64()));

                                            let f_10k = FixedPrice::new(10000 * sniper_types::PRICE_SCALE_I);
                                            let drop_ratio = (mid_fp - trigger_fp) / mid_fp;
                                            let drop_bps_fp = drop_ratio * f_10k;
                                            sniper_types::l2_command::signal_flash_crash(l2_risk, drop_bps_fp.0 / sniper_types::PRICE_SCALE_I);
                                            self.last_order_ts[idx] = Instant::now();
                                        }
                                    }
                                } else if self.last_order_ts[idx].elapsed().as_millis() > 50 {
                                    let f_100 = FixedPrice::new(100 * sniper_types::PRICE_SCALE_I);
                                    let drop_fp = FixedPrice::new(r.m_shot_price_pct.load(Ordering::Acquire) as i64);
                                    let tp_fp = FixedPrice::new(r.tp_pct.load(Ordering::Acquire) as i64);
                                    let order_usd_fp = FixedPrice::new(self.armada_state.authorized_capital[1].load(Ordering::Acquire) as i64);

                                    if drop_fp.0 > 0 && order_usd_fp.0 > 0 && mid_fp.0 > 0 {
                                        let multiplier_fp = FixedPrice::new(sniper_types::PRICE_SCALE_I) - (drop_fp / f_100);
                                        let buy_p_fp = mid_fp * multiplier_fp;
                                        
                                        let tp_mult = FixedPrice::new(sniper_types::PRICE_SCALE_I) + (tp_fp / f_100);
                                        let sell_p_fp = buy_p_fp * tp_mult;
                                        
                                        let coin_amount = (order_usd_fp / buy_p_fp).max(FixedPrice::new(15000));

                                        if buy_p_fp < ask_fp {
                                            let symbol = &self.idx_to_symbol[idx];
                                            if !symbol.is_empty() {
                                                use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
                                                BitfinexVenue::write_batch_open_cancel_gid(out_buf, sniper_types::BOT_GID_MOONSHOT);
                                                
                                                let coin_fmt = FixedFormat::new(coin_amount.0);
                                                let buy_p_fmt = FixedFormat::new(buy_p_fp.0);
                                                
                                                BitfinexVenue::write_limit_order(
                                                    out_buf, 2000, symbol.as_bytes(),
                                                    coin_fmt.as_str(), buy_p_fmt.as_str(),
                                                );
                                                
                                                let neg_coin_fmt = FixedFormat::new(-coin_amount.0);
                                                let sell_p_fmt = FixedFormat::new(sell_p_fp.0);
                                                
                                                BitfinexVenue::write_limit_order(
                                                    out_buf, 2000, symbol.as_bytes(),
                                                    neg_coin_fmt.as_str(), sell_p_fmt.as_str(),
                                                );
                                                BitfinexVenue::write_batch_close(out_buf);
                                                
                                                e.buy_order_price.store(buy_p_fp.0 as u64, Ordering::Release);
                                                self.last_order_ts[idx] = Instant::now();
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
    }

    fn on_system_event(&mut self, value: &serde_json::Value, _out_buf: &mut bytes::BytesMut) {
        if value["event"] == "subscribed" && value["channel"] == "ticker"
            && let (Some(chan_id), Some(symbol)) = (value["chanId"].as_i64(), value["symbol"].as_str()) {
                for (idx, sym) in self.idx_to_symbol.iter().enumerate() {
                    if sym.as_str() == symbol {
                        self.chan_to_idx.insert(chan_id, idx);
                        info!(event = "pair_subscribed", symbol = symbol, idx = idx, chan_id = chan_id);
                        break;
                    }
                }
            }
    }

    fn on_loop(&mut self, _out_buf: &mut bytes::BytesMut) {
        let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        let e_global = unsafe { &*self.engine };
        e_global.heartbeat_ms.store(now_ms, Ordering::Release);
        
        let bid = e_global.pairs[0].best_bid.load(Ordering::Relaxed) as i64;
        let ask = e_global.pairs[0].best_ask.load(Ordering::Relaxed) as i64;
        if ask > 0 && bid > 0 {
            let spread_bps = ((ask - bid) * 10000) / ask;
            let fee_ref = unsafe { &*self.fee_matrix };
            let bfx_taker = fee_ref.venues[sniper_types::fee_types::VENUE_BITFINEX].taker_fee_bps.load(Ordering::Relaxed) as i64;
            let slippage = 3; // 3 bps slippage estimate
            let required_spread = (bfx_taker * 2) + slippage;
            
            let risk = unsafe { &*self.risk };
            if spread_bps < required_spread {
                risk.global_paused.store(1, Ordering::Relaxed);
            } else {
                risk.global_paused.store(0, Ordering::Relaxed);
            }
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();
    
    let engine_mmap = init_mmap::<MoonshotEngineState>(MOONSHOT_ENGINE_PATH)?;
    let risk_mmap = init_mmap::<MoonshotRiskState>(MOONSHOT_RISK_PATH)?;
    let l2_shared_mmap = init_mmap::<sniper_types::l2_command::L2SharedState>(
        sniper_types::l2_command::L2_COMMAND_PATH,
    )?;
    let fee_mmap = init_mmap::<sniper_types::fee_types::GlobalFeeMatrix>(
        sniper_types::fee_types::FEE_MATRIX_PATH,
    )?;
    
    let storm_path = "/dev/shm/beroun/toxic_storm.bin";
    if !std::path::Path::new(storm_path).exists() { let _ = std::fs::write(storm_path, [0u8]); }
    let toxic_storm_mmap = sniper_types::mmap_utils::open_mmap_readonly(storm_path).unwrap();

    let engine_ptr = engine_mmap.as_ptr() as *const MoonshotEngineState;
    let risk_ptr = risk_mmap.as_ptr() as *const MoonshotRiskState;
    let fee_ptr = fee_mmap.as_ptr() as *const sniper_types::fee_types::GlobalFeeMatrix;
    let l2_shared = unsafe { &*(l2_shared_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };
    
    let engine = MoonshotEngine {
        notifier: Arc::new(AsyncNotifier::new("moonshot", "🚀")),
        engine: engine_ptr,
        risk: risk_ptr,
        fee_matrix: fee_ptr,
        l2cmd: &l2_shared.cmd,
        l1ring: &l2_shared.latency_ring,
        toxic_storm_ptr: toxic_storm_mmap.as_ptr(),
        l2_risk: &l2_shared.global_risk,
        chan_to_idx: FlatMapI64::new(),
        idx_to_symbol: core::array::from_fn(|_| FixedSymbol::new()),
        last_order_ts: core::array::from_fn(|_| Instant::now()),
        armada_state: sniper_types::armada_types::load_armada_state_ro(),
        bids: core::array::from_fn(|_| core::array::from_fn(|_| sniper_types::OrderBookLevel::default())),
        asks: core::array::from_fn(|_| core::array::from_fn(|_| sniper_types::OrderBookLevel::default())),
        snapshot_loaded: [false; MOONSHOT_MAX_PAIRS],
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignDualRunner::new(engine, venue, "Moonshot");
    runner.run().await?;
    
    Ok(())
}

```

## moonshot/src/book.rs (The Slippage Matrix)
```rust
use std::sync::atomic::Ordering;

pub fn update_book(levels: &mut [sniper_types::OrderBookLevel; 25], price: u64, amount: i64, count: u64) {
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

pub fn sort_book(levels: &mut [sniper_types::OrderBookLevel; 25], is_bid: bool) {
    levels.sort_unstable_by(|a, b| {
        let pa = a.price.load(Ordering::Acquire);
        let pb = b.price.load(Ordering::Acquire);
        if pa == 0 && pb == 0 { return std::cmp::Ordering::Equal; }
        if pa == 0 { return std::cmp::Ordering::Greater; }
        if pb == 0 { return std::cmp::Ordering::Less; }
        if is_bid { pb.cmp(&pa) } else { pa.cmp(&pb) }
    });
}

```
