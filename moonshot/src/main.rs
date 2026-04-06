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
use sniper_types::framework::{SovereignEngine, SovereignRunner};
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::init_mmap;
use sniper_types::math::FixedPrice;

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
        let mut frac_part = val % PRICE_SCALE_I;
        
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
}

unsafe impl Send for MoonshotEngine {}
unsafe impl Sync for MoonshotEngine {}

impl SovereignEngine for MoonshotEngine {
    fn subscriptions(&mut self) -> Vec<String> {
        let risk_state = unsafe { &*self.risk };
        let mut subs = Vec::new();
        
        for i in 0..MOONSHOT_MAX_PAIRS {
            let symbol_hash = risk_state.pairs[i].symbol_hash.load(Ordering::Acquire);
            if symbol_hash != 0 {
                let symbol = symbol_hash_to_str(symbol_hash);
                subs.push(json!({
                    "event": "subscribe",
                    "channel": "ticker",
                    "symbol": symbol
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
        risk.global_paused.load(Ordering::Acquire) != 0
    }

    fn best_bid_ask(&self) -> (f64, f64) {
        let e = unsafe { &(*self.engine).pairs[0] }; 
        (
            e.best_bid.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64,
            e.best_ask.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64
        )
    }

    fn on_market_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut) {
        let loop_start = Instant::now();
        if let Some((chan, bid, ask)) = sniper_types::exchange::fast_parse_ticker(payload) {
            if let Some(idx) = self.chan_to_idx.get(chan) {
                let e = unsafe { &(*self.engine).pairs[idx] };
                let r = unsafe { &(*self.risk).pairs[idx] };

                e.best_bid.store(bid as u64, Ordering::Release);
                e.best_ask.store(ask as u64, Ordering::Release);
                let mid_price = (bid + ask) / 2;
                e.last_trade.store(mid_price as u64, Ordering::Release);
                e.latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);

                let storm_byte = unsafe { std::ptr::read_volatile(self.toxic_storm_ptr) };
                if storm_byte == 1 {
                    return;
                }

                let l2cmd = unsafe { &*self.l2cmd };
                let l2_risk = unsafe { &*self.l2_risk };

                let bid_fp = FixedPrice::new(bid);
                let ask_fp = FixedPrice::new(ask);
                let mid_fp = FixedPrice::new(mid_price);

                if let Some(trigger) = sniper_types::l2_command::moonshot_check_and_disarm(
                    l2cmd, mid_price as i64, 1, 0
                ) {
                    let order_usd_fp = FixedPrice::new(r.order_usd.load(Ordering::Acquire) as i64 * PRICE_SCALE_I);
                    let trigger_fp = FixedPrice::new(trigger as i64);
                    
                    if order_usd_fp.0 > 0 && mid_fp.0 > 0 {
                        let coin_amount = (order_usd_fp / mid_fp).max(FixedPrice::new(15000));
                        let symbol = &self.idx_to_symbol[idx];
                        if !symbol.is_empty() {
                            let amt_fmt = FixedFormat::new(coin_amount.0);
                            let price_fmt = FixedFormat::new(mid_price);

                            sniper_types::exchange::bitfinex_venue::BitfinexVenue::write_standalone_ioc(
                                out_buf, 2001, symbol.as_bytes(),
                                amt_fmt.as_str(), price_fmt.as_str(),
                            );

                            let micro_f = mid_fp.as_f64();
                            warn!(event = "moonshot_fired", symbol = %symbol.as_str(), price = micro_f);
                            self.notifier.send(format!("🚀 MOONSHOT FIRED! {} @ ${:.2} (trigger ${:.2})", 
                                symbol.as_str(), micro_f, trigger_fp.as_f64()));

                            let f_10k = FixedPrice::new(10000 * PRICE_SCALE_I);
                            let drop_ratio = (mid_fp - trigger_fp) / mid_fp;
                            let drop_bps_fp = drop_ratio * f_10k;
                            
                            sniper_types::l2_command::signal_flash_crash(l2_risk, drop_bps_fp.0 / PRICE_SCALE_I);
                            self.last_order_ts[idx] = Instant::now();
                        }
                    }
                } else if self.last_order_ts[idx].elapsed().as_millis() > 50 {
                    let f_100 = FixedPrice::new(100 * PRICE_SCALE_I);
                    let drop_fp = FixedPrice::new(r.m_shot_price_pct.load(Ordering::Acquire) as i64);
                    let tp_fp = FixedPrice::new(r.tp_pct.load(Ordering::Acquire) as i64);
                    let order_usd_fp = FixedPrice::new(r.order_usd.load(Ordering::Acquire) as i64 * PRICE_SCALE_I);

                    if drop_fp.0 > 0 && order_usd_fp.0 > 0 && mid_fp.0 > 0 {
                        let multiplier_fp = FixedPrice::new(PRICE_SCALE_I) - (drop_fp / f_100);
                        let buy_p_fp = mid_fp * multiplier_fp;
                        
                        let tp_mult = FixedPrice::new(PRICE_SCALE_I) + (tp_fp / f_100);
                        let sell_p_fp = buy_p_fp * tp_mult;
                        
                        let coin_amount = (order_usd_fp / buy_p_fp).max(FixedPrice::new(15000));

                        if buy_p_fp < ask_fp {
                            let symbol = &self.idx_to_symbol[idx];
                            if !symbol.is_empty() {
                                use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
                                BitfinexVenue::write_batch_open_cancel_sym(out_buf, symbol.as_bytes());
                                
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

    fn on_system_event(&mut self, value: &serde_json::Value, _out_buf: &mut bytes::BytesMut) {
        if value["event"] == "subscribed" && value["channel"] == "ticker" {
            if let (Some(chan_id), Some(symbol)) = (value["chanId"].as_i64(), value["symbol"].as_str()) {
                for (idx, sym) in self.idx_to_symbol.iter().enumerate() {
                    if sym.as_str() == symbol {
                        self.chan_to_idx.insert(chan_id, idx);
                        info!(event = "pair_subscribed", symbol = symbol, idx = idx, chan_id = chan_id);
                        break;
                    }
                }
            }
        }
    }

    fn on_loop(&mut self, _out_buf: &mut bytes::BytesMut) {
        let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        unsafe { &*self.engine }.heartbeat_ms.store(now_ms, Ordering::Release);
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
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignRunner::new(engine, venue, "Moonshot");
    runner.run().await?;
    
    Ok(())
}
