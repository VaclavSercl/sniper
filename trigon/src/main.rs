// 🔺 Trigon L0 Engine — Triangular Arbitrage Scanner
// Framework: SovereignEngine
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use anyhow::Result;
use tracing::info;
use serde_json::json;

use sniper_types::trigon_types::*;
use sniper_types::moonshot_types::{symbol_hash_to_str};
use sniper_types::framework::{SovereignEngine, SovereignRunner};
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::init_mmap;

const VERSION: &str = "1.1.0";

fn calculate_triangle_i64(
    bids: &[u64; 3],
    asks: &[u64; 3],
    directions: &[u32; 3],
    fee_bps: u64, // e.g. 2000 = 20 bps
) -> (i64, i64) {
    let initial: u128 = 1_000_000_000_000_000;
    let mut amt = initial;

    for i in 0..3 {
        if directions[i] == 0 {
            if asks[i] == 0 { return (0, -10000_00); }
            amt = (amt * sniper_types::PRICE_SCALE_I as u128) / asks[i] as u128;
        } else {
            if bids[i] == 0 { return (0, -10000_00); }
            amt = (amt * bids[i] as u128) / sniper_types::PRICE_SCALE_I as u128;
        }
    }

    let rate_i64 = ((amt * sniper_types::PRICE_SCALE_I as u128) / initial) as i64;
    
    let fee_mult = 1_000_000 - fee_bps as u128;
    let mut out_amt = amt;
    for _ in 0..3 { out_amt = (out_amt * fee_mult) / 1_000_000; }
    
    let profit_bps = ((out_amt as i128 - initial as i128) * 10000 * 100) / initial as i128;
    
    (rate_i64, profit_bps as i64)
}

const MAX_TICKER_SLOTS: usize = 32;

/// Cache-friendly flat lookup table replacing HashMap.
/// Linear scan over 32 × 16B = 512B = 8 cache lines.
/// All hot path data lives in L1 cache.
struct FlatMap {
    keys: [u64; MAX_TICKER_SLOTS],
    vals: [u64; MAX_TICKER_SLOTS],
    len: usize,
}

impl FlatMap {
    const fn new() -> Self {
        Self { keys: [0; MAX_TICKER_SLOTS], vals: [0; MAX_TICKER_SLOTS], len: 0 }
    }

    #[inline]
    fn get(&self, key: u64) -> Option<u64> {
        // Linear scan — fast for ≤32 entries (fully in L1 cache)
        for i in 0..self.len {
            if self.keys[i] == key { return Some(self.vals[i]); }
        }
        None
    }

    #[inline]
    fn insert(&mut self, key: u64, val: u64) {
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

/// Same as FlatMap but keyed on i64 (for channel IDs)
struct FlatMapI64 {
    keys: [i64; MAX_TICKER_SLOTS],
    vals: [u64; MAX_TICKER_SLOTS],
    len: usize,
}

impl FlatMapI64 {
    const fn new() -> Self {
        Self { keys: [0; MAX_TICKER_SLOTS], vals: [0; MAX_TICKER_SLOTS], len: 0 }
    }

    #[inline]
    fn get(&self, key: i64) -> Option<u64> {
        for i in 0..self.len {
            if self.keys[i] == key { return Some(self.vals[i]); }
        }
        None
    }

    #[inline]
    fn insert(&mut self, key: i64, val: u64) {
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

struct TrigonEngine {
    notifier: Arc<AsyncNotifier>,
    engine: *const TrigonEngineState,
    risk: *const TrigonRiskState,
    fee_state: *const sniper_types::fee_types::GlobalFeeState,
    l2_cmd: *const sniper_types::l2_command::L2CommandMatrix,
    
    chan_to_symbol: FlatMapI64,
    symbol_bids_i: FlatMap,
    symbol_asks_i: FlatMap,
    
    subscribed_symbols: Vec<String>,
    last_scan: Instant,
    last_exec_ms: [u64; TRIGON_MAX_TRIANGLES],
    
    ryu1: ryu::Buffer,
    ryu2: ryu::Buffer,
}

unsafe impl Send for TrigonEngine {}
unsafe impl Sync for TrigonEngine {}

impl SovereignEngine for TrigonEngine {
    fn subscriptions(&mut self) -> Vec<String> {
        let r = unsafe { &*self.risk };
        let mut subs = Vec::new();
        for i in 0..TRIGON_MAX_TRIANGLES {
            for leg in 0..TRIGON_LEGS {
                let sym_hash = r.triangles[i].leg_symbols[leg].load(Ordering::Acquire);
                if sym_hash != 0 {
                    let symbol = symbol_hash_to_str(sym_hash);
                    if !self.subscribed_symbols.contains(&symbol) {
                        subs.push(json!({"event": "subscribe", "channel": "ticker", "symbol": symbol}).to_string());
                        self.subscribed_symbols.push(symbol);
                    }
                }
            }
        }
        
        if self.subscribed_symbols.is_empty() {
            for &(a, b, c) in KNOWN_TRIANGLES {
                for sym in [a, b, c] {
                    if !self.subscribed_symbols.contains(&sym.to_string()) {
                        subs.push(json!({"event": "subscribe", "channel": "ticker", "symbol": sym}).to_string());
                        self.subscribed_symbols.push(sym.to_string());
                    }
                }
            }
        }
        subs
    }

    fn on_start(&mut self) -> Result<()> {
        let _lock_guard = sniper_types::lock::ensure_single_instance("trigon-core")?;
        self.notifier.send(format!("🔺 Trigon v{} ONLINE", VERSION));
        Ok(())
    }

    fn on_auth(&mut self) {
        info!(event = "authenticated", bot = "trigon");
    }

    fn on_market_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut) {
        let loop_start = Instant::now();
        if let Some((chan, bid, ask)) = sniper_types::exchange::fast_parse_ticker(payload) {
            let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
            let engine = unsafe { &*self.engine };
            let risk = unsafe { &*self.risk };
            let fee_state = unsafe { &*self.fee_state };
            
            if let Some(sym_hash) = self.chan_to_symbol.get(chan) {
                let bid_f = bid as u64;
                let ask_f = ask as u64;
                self.symbol_bids_i.insert(sym_hash, bid_f);
                self.symbol_asks_i.insert(sym_hash, ask_f);

                for t in 0..TRIGON_MAX_TRIANGLES {
                    for l in 0..TRIGON_LEGS {
                        let leg_hash = risk.triangles[t].leg_symbols[l].load(Ordering::Acquire);
                        if leg_hash == sym_hash {
                            engine.triangles[t].legs[l].best_bid.store(bid as u64, Ordering::Release);
                            engine.triangles[t].legs[l].best_ask.store(ask as u64, Ordering::Release);
                            engine.triangles[t].legs[l].latency_ns.store(
                                loop_start.elapsed().as_nanos() as u64, Ordering::Release
                            );
                        }
                    }
                }

                if self.last_scan.elapsed().as_millis() >= 100 {
                    let fee_bps = fee_state.taker_fee_bps.load(Ordering::Relaxed);
                    let paused = risk.global_paused.load(Ordering::Acquire) != 0;
                    
                    let mut best_profit = -1000000i64;

                    for t in 0..TRIGON_MAX_TRIANGLES {
                        let tr = &risk.triangles[t];
                        if tr.enabled.load(Ordering::Acquire) == 0 { continue; }

                        let mut bids = [0u64; 3];
                        let mut asks = [0u64; 3];
                        let mut dirs = [0u32; 3];
                        let mut all_valid = true;

                        for l in 0..TRIGON_LEGS {
                            let h = tr.leg_symbols[l].load(Ordering::Acquire);
                            dirs[l] = tr.leg_directions[l].load(Ordering::Acquire);
                            if let (Some(b), Some(a)) = (self.symbol_bids_i.get(h), self.symbol_asks_i.get(h)) {
                                bids[l] = b;
                                asks[l] = a;
                            } else {
                                all_valid = false;
                                break;
                            }
                        }

                        if !all_valid { continue; }

                        let (rate, profit) = calculate_triangle_i64(&bids, &asks, &dirs, fee_bps);
                        let et = &engine.triangles[t];
                        et.implied_rate.store(rate, Ordering::Release);
                        et.profit_bps.store(profit / 100, Ordering::Release);
                        et.fee_cost_bps.store(fee_bps * 3 / 100, Ordering::Release);
                        et.last_calc_ns.store(now_ms * 1_000_000, Ordering::Release);

                        if profit > best_profit { best_profit = profit; }

                        let min_profit = (tr.min_profit_bps.load(Ordering::Acquire) * 100) as i64;
                        let cooldown = tr.cooldown_ms.load(Ordering::Acquire);
                        let max_usd = tr.max_order_usd.load(Ordering::Acquire) as i64;

                        let latency_pad = {
                            let l2_cmd = unsafe { &*self.l2_cmd };
                            let (v1, ok1) = sniper_types::l2_command::l2cmd_version_check(l2_cmd);
                            let pad = l2_cmd.latency_padding_bps.load(Ordering::Relaxed);
                            let kill = l2_cmd.latency_killswitch.load(Ordering::Relaxed);
                            let (v2, ok2) = sniper_types::l2_command::l2cmd_version_check(l2_cmd);
                            if v1 == v2 && ok1 && ok2 {
                                if kill == 1 { continue; }
                                (pad.max(0) * 100) as i64
                            } else { 0 }
                        };
                        let effective_min_profit = min_profit + latency_pad;

                        // ═══ HIVE MIND: Cross-Bot Toxic Storm (SIM v2.0) ═══
                        if let Ok(flag) = std::fs::read("/dev/shm/beroun/toxic_storm.bin") {
                            if !flag.is_empty() && flag[0] == 1 { continue; }
                        }

                        if !paused && profit > effective_min_profit
                            && et.executing.load(Ordering::Acquire) == 0
                            && (now_ms - self.last_exec_ms[t]) > cooldown
                            && max_usd > 0
                        {
                            et.executing.store(1, Ordering::Release);
                            out_buf.extend_from_slice(b"[0,\"ox_multi\",null,[");

                            for l in 0..TRIGON_LEGS {
                                let sym_hash = tr.leg_symbols[l].load(Ordering::Acquire);
                                let dir = dirs[l];
                                let sym = symbol_hash_to_str(sym_hash);
                                let price_i = if dir == 0 { asks[l] } else { bids[l] };
                                let price = (price_i as f64) / sniper_types::PRICE_SCALE_I as f64;

                                let max_usd_f = (max_usd as f64) / sniper_types::PRICE_SCALE_I as f64;
                                let mut qty = if price > 0.0 { max_usd_f / price } else { 0.0 };
                                if dir == 1 { qty = -qty; }

                                if l > 0 { out_buf.extend_from_slice(b","); }
                                out_buf.extend_from_slice(b"[\"on\",{\"gid\":4000,\"symbol\":\"");
                                out_buf.extend_from_slice(sym.as_bytes());
                                out_buf.extend_from_slice(b"\",\"amount\":\"");
                                out_buf.extend_from_slice(self.ryu1.format(qty).as_bytes());
                                out_buf.extend_from_slice(b"\",\"price\":\"");
                                out_buf.extend_from_slice(self.ryu2.format(price).as_bytes());
                                out_buf.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]");
                            }
                            out_buf.extend_from_slice(b"]]");

                            et.executions.fetch_add(1, Ordering::Relaxed);
                            self.last_exec_ms[t] = now_ms;
                            info!(event = "arb_execute", triangle = t, profit_bps = profit, rate = rate);
                            self.notifier.send(format!(
                                "💰 ARB EXEC! Tri#{} profit={} / 100 bps rate={} size=${:.2}",
                                t, profit, rate, (max_usd as f64) / sniper_types::PRICE_SCALE_I as f64));
                            
                            et.executing.store(0, Ordering::Release);
                        }
                    }
                    self.last_scan = Instant::now();
                }
            }
        }
    }

    fn on_system_event(&mut self, value: &serde_json::Value, _out_buf: &mut bytes::BytesMut) {
        if value["event"] == "subscribed" && value["channel"] == "ticker" {
            if let (Some(chan_id), Some(symbol_str)) = (value["chanId"].as_i64(), value["symbol"].as_str()) {
                self.chan_to_symbol.insert(chan_id, sniper_types::moonshot_types::str_to_symbol_hash(symbol_str));
                info!(event = "ticker_subscribed", chan_id = chan_id, symbol = symbol_str);
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
    
    let engine_mmap = init_mmap::<TrigonEngineState>(TRIGON_ENGINE_PATH)?;
    let risk_mmap = init_mmap::<TrigonRiskState>(TRIGON_RISK_PATH)?;
    let fee_mmap = init_mmap::<sniper_types::fee_types::GlobalFeeState>(
        sniper_types::fee_types::FEE_STATE_PATH)?;
    let l2_mmap = init_mmap::<sniper_types::l2_command::L2SharedState>(
        sniper_types::l2_command::L2_COMMAND_PATH,
    )?;

    let engine_ptr = engine_mmap.as_ptr() as *const TrigonEngineState;
    let risk_ptr = risk_mmap.as_ptr() as *const TrigonRiskState;
    let fee_ptr = fee_mmap.as_ptr() as *const sniper_types::fee_types::GlobalFeeState;
    let l2_shared = unsafe { &*(l2_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };

    let engine = TrigonEngine {
        notifier: Arc::new(AsyncNotifier::new("trigon", "🔺")),
        engine: engine_ptr,
        risk: risk_ptr,
        fee_state: fee_ptr,
        l2_cmd: &l2_shared.cmd,
        chan_to_symbol: FlatMapI64::new(),
        symbol_bids_i: FlatMap::new(),
        symbol_asks_i: FlatMap::new(),
        subscribed_symbols: Vec::new(),
        last_scan: Instant::now() - core::time::Duration::from_secs(10),
        last_exec_ms: [0; TRIGON_MAX_TRIANGLES],
        ryu1: ryu::Buffer::new(),
        ryu2: ryu::Buffer::new(),
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignRunner::new(engine, venue, "Trigon");
    runner.run().await?;
    
    Ok(())
}
