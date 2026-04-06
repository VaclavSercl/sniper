// 🚀 Moonshot L0 Engine — Flash Crash & Volatility Bot
// Sniper Armada · Bot #2 · v14.3.0
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

const VERSION: &str = "14.3.0";

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

struct MoonshotEngine {
    notifier: Arc<AsyncNotifier>,
    engine: *const MoonshotEngineState,
    risk: *const MoonshotRiskState,
    l2cmd: *const sniper_types::l2_command::L2CommandMatrix,
    l2_risk: *const sniper_types::l2_command::L2GlobalRiskMatrix,
    
    chan_to_idx: FlatMapI64,
    idx_to_symbol: [String; MOONSHOT_MAX_PAIRS],
    last_order_ts: Vec<Instant>,
    
    ryu1: ryu::Buffer,
    ryu2: ryu::Buffer,
}

// Musíme ručně implementovat Send pro surové pointery
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
                self.idx_to_symbol[i] = symbol;
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
        let e = unsafe { &(*self.engine).pairs[0] }; // Using index 0 for global price estimate
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
                e.last_trade.store(((bid + ask) / 2) as u64, Ordering::Release);
                e.latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);

                // ═══ HIVE MIND: Cross-Bot Toxic Storm (SIM v2.0) ═══
                if let Ok(flag) = std::fs::read("/dev/shm/beroun/toxic_storm.bin") {
                    if !flag.is_empty() && flag[0] == 1 { return; }
                }
                let mid_price = (bid + ask) / 2;
                let l2cmd = unsafe { &*self.l2cmd };
                let l2_risk = unsafe { &*self.l2_risk };

                if let Some(trigger) = sniper_types::l2_command::moonshot_check_and_disarm(
                    l2cmd, mid_price as i64, 1, 0
                ) {
                    let order_usd = r.order_usd.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;
                    let mid_f64 = mid_price as f64 / PRICE_SCALE_I as f64;
                    
                    if order_usd > 0.0 && mid_f64 > 0.0 {
                        let coin_amount = (order_usd / mid_f64).max(0.00015);
                        let symbol = &self.idx_to_symbol[idx];
                        if !symbol.is_empty() {
                            sniper_types::exchange::bitfinex_venue::BitfinexVenue::write_standalone_ioc(
                                out_buf, 2001, symbol.as_bytes(),
                                self.ryu1.format(coin_amount), self.ryu2.format(mid_f64),
                            );

                            warn!(event = "moonshot_fired", symbol = %symbol, price = mid_f64);
                            self.notifier.send(format!("🚀 MOONSHOT FIRED! {} @ ${:.2} (trigger ${:.2})", 
                                symbol, mid_f64, trigger as f64 / PRICE_SCALE_I as f64));

                            let drop_bps = ((mid_f64 - (trigger as f64 / PRICE_SCALE_I as f64)) / mid_f64 * 10_000.0) as i64;
                            sniper_types::l2_command::signal_flash_crash(l2_risk, drop_bps);
                            self.last_order_ts[idx] = Instant::now();
                        }
                    }
                } else if self.last_order_ts[idx].elapsed().as_millis() > 50 {
                    // Passive limit orders logic
                    let drop_pct = r.m_shot_price_pct.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;
                    let tp_pct = r.tp_pct.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;
                    let order_usd = r.order_usd.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;

                    let mid_f64 = mid_price as f64 / PRICE_SCALE_I as f64;
                    if drop_pct > 0.0 && order_usd > 0.0 && mid_f64 > 0.0 {
                        let buy_p = mid_f64 * (1.0 - (drop_pct / 100.0));
                        let sell_p = buy_p * (1.0 + (tp_pct / 100.0));
                        let coin_amount = (order_usd / buy_p).max(0.00015);
                        let ask_f64 = ask as f64 / PRICE_SCALE_I as f64;

                        if buy_p < ask_f64 {
                            let symbol = &self.idx_to_symbol[idx];
                            if !symbol.is_empty() {
                                use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
                                BitfinexVenue::write_batch_open_cancel_sym(out_buf, symbol.as_bytes());
                                BitfinexVenue::write_limit_order(
                                    out_buf, 2000, symbol.as_bytes(),
                                    self.ryu1.format(coin_amount), self.ryu2.format(buy_p),
                                );
                                BitfinexVenue::write_limit_order(
                                    out_buf, 2000, symbol.as_bytes(),
                                    self.ryu1.format(-coin_amount), self.ryu2.format(sell_p),
                                );
                                BitfinexVenue::write_batch_close(out_buf);
                                
                                e.buy_order_price.store((buy_p * PRICE_SCALE_I as f64) as u64, Ordering::Release);
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
                    if sym == symbol {
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
    
    let engine_ptr = engine_mmap.as_ptr() as *const MoonshotEngineState;
    let risk_ptr = risk_mmap.as_ptr() as *const MoonshotRiskState;
    let l2_shared = unsafe { &*(l2_shared_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };
    
    let engine = MoonshotEngine {
        notifier: Arc::new(AsyncNotifier::new("moonshot", "🚀")),
        engine: engine_ptr,
        risk: risk_ptr,
        l2cmd: &l2_shared.cmd,
        l2_risk: &l2_shared.global_risk,
        chan_to_idx: FlatMapI64::new(),
        idx_to_symbol: core::array::from_fn(|_| String::new()),
        last_order_ts: vec![Instant::now(); MOONSHOT_MAX_PAIRS],
        ryu1: ryu::Buffer::new(),
        ryu2: ryu::Buffer::new(),
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignRunner::new(engine, venue, "Moonshot");
    runner.run().await?;
    
    Ok(())
}
