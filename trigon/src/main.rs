// 🔺 Trigon L0 Engine — Triangular Arbitrage Scanner
// Framework: SovereignEngine v12/2026 (Zero-f64 / Zero-Allocation Refactor)
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
use sniper_types::math::FixedPrice;
use sniper_types::PRICE_SCALE_I;

const VERSION: &str = "13.0.0-trigon-zerofpu";
const MAX_TICKER_SLOTS: usize = 32;

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

#[derive(Clone, Copy)]
struct FixedSymbol {
    buf: [u8; 16],
    len: usize,
}

impl PartialEq for FixedSymbol {
    #[inline(always)]
    fn eq(&self, other: &Self) -> bool {
        self.len == other.len && self.buf[..self.len] == other.buf[..other.len]
    }
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
    fn as_str(&self) -> &str { unsafe { std::str::from_utf8_unchecked(&self.buf[..self.len]) } }
}

/// Výpočet čistým i64 FixedPrice modelem bez f64 zaokrouhlení
#[inline(always)]
fn calculate_triangle_fp(
    bids: &[u64; 3],
    asks: &[u64; 3],
    directions: &[u32; 3],
    fee_bps: u64,
) -> (i64, i64) {
    let initial = FixedPrice::new(PRICE_SCALE_I);
    let mut amt = initial;

    for i in 0..3 {
        if directions[i] == 0 {
            if asks[i] == 0 { return (0, -10000_00); }
            amt = amt / FixedPrice::new(asks[i] as i64);
        } else {
            if bids[i] == 0 { return (0, -10000_00); }
            amt = amt * FixedPrice::new(bids[i] as i64);
        }
    }

    let profit_diff = amt - initial;
    // BPS úprava z FixedPrice scale (1e8). BPS je 1_000_000 scale, vynásobíme 100 pro dvě desetinná místa bps
    let gross_profit_bps_100x = profit_diff.0 / 100;
    // Odečteme poplatky jako fixní flat hodnoty bps * 3 nožky ( fee_bps je např. 2000 což je 20.00 BPS )
    let net_profit_bps = gross_profit_bps_100x - (fee_bps as i64 * 3);
    
    (amt.0, net_profit_bps)
}

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
    
    subscribed_symbols: [FixedSymbol; 128],
    subscribed_count: usize,
    last_scan: Instant,
    last_exec_ms: [u64; TRIGON_MAX_TRIANGLES],
    toxic_storm_ptr: *const u8,
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
                    let symbol_str = symbol_hash_to_str(sym_hash);
                    let fsym = FixedSymbol::from_str(&symbol_str);
                    
                    let mut found = false;
                    for i in 0..self.subscribed_count {
                        if self.subscribed_symbols[i] == fsym { found = true; break; }
                    }
                    if !found {
                        subs.push(json!({"event": "subscribe", "channel": "ticker", "symbol": symbol_str}).to_string());
                        if self.subscribed_count < 128 {
                            self.subscribed_symbols[self.subscribed_count] = fsym;
                            self.subscribed_count += 1;
                        }
                    }
                }
            }
        }
        
        if self.subscribed_count == 0 {
            for &(a, b, c) in KNOWN_TRIANGLES {
                for sym in [a, b, c] {
                    let fsym = FixedSymbol::from_str(sym);
                    let mut found = false;
                    for i in 0..self.subscribed_count {
                        if self.subscribed_symbols[i] == fsym { found = true; break; }
                    }
                    if !found {
                        subs.push(json!({"event": "subscribe", "channel": "ticker", "symbol": sym}).to_string());
                        if self.subscribed_count < 128 {
                            self.subscribed_symbols[self.subscribed_count] = fsym;
                            self.subscribed_count += 1;
                        }
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

    fn is_shadow(&self) -> bool {
        let risk = unsafe { &*self.risk };
        risk.global_paused.load(Ordering::Acquire) != 0
    }

    fn best_bid_ask(&self) -> (f64, f64) {
        let engine = unsafe { &*self.engine };
        (
            engine.triangles[0].legs[0].best_bid.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64,
            engine.triangles[0].legs[0].best_ask.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64
        )
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
                    let _paused = risk.global_paused.load(Ordering::Acquire) != 0;
                    
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

                        // FPU EXPELLED: Rate a profit bps z čisté matice registru i64/i128
                        let (rate, profit) = calculate_triangle_fp(&bids, &asks, &dirs, fee_bps);
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

                        // ═══ HIVE MIND: Toxic Storm check ═══
                        let storm_byte = unsafe { std::ptr::read_volatile(self.toxic_storm_ptr) };
                        if storm_byte == 1 { continue; }

                        if profit > effective_min_profit
                            && et.executing.load(Ordering::Acquire) == 0
                            && (now_ms - self.last_exec_ms[t]) > cooldown
                            && max_usd > 0
                        {
                            et.executing.store(1, Ordering::Release);
                            use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
                            BitfinexVenue::write_batch_open(out_buf);

                            let max_usd_fp = FixedPrice::new(max_usd * PRICE_SCALE_I);

                            for l in 0..TRIGON_LEGS {
                                let sym_hash = tr.leg_symbols[l].load(Ordering::Acquire);
                                let dir = dirs[l];
                                let sym_str = symbol_hash_to_str(sym_hash);
                                let fsym = FixedSymbol::from_str(&sym_str);
                                
                                let price_i = if dir == 0 { asks[l] } else { bids[l] };
                                let price_fp = FixedPrice::new(price_i as i64);

                                let mut qty_fp = if price_fp.0 > 0 { max_usd_fp / price_fp } else { FixedPrice::zero() };
                                if dir == 1 { qty_fp.0 = -qty_fp.0; }

                                let q_fmt = FixedFormat::new(qty_fp.0);
                                let p_fmt = FixedFormat::new(price_fp.0);

                                if l == 0 {
                                    out_buf.extend_from_slice(b"[\"on\",{\"gid\":");
                                    let mut itoa_buf = itoa::Buffer::new();
                                    out_buf.extend_from_slice(itoa_buf.format(4000u32).as_bytes());
                                    out_buf.extend_from_slice(b",\"symbol\":\"");
                                    out_buf.extend_from_slice(fsym.as_bytes());
                                    out_buf.extend_from_slice(b"\",\"amount\":\"");
                                    out_buf.extend_from_slice(q_fmt.as_str().as_bytes());
                                    out_buf.extend_from_slice(b"\",\"price\":\"");
                                    out_buf.extend_from_slice(p_fmt.as_str().as_bytes());
                                    out_buf.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]");
                                } else {
                                    BitfinexVenue::write_ioc_order(
                                        out_buf, 4000, fsym.as_bytes(),
                                        q_fmt.as_str(), p_fmt.as_str(),
                                    );
                                }
                            }
                            BitfinexVenue::write_batch_close(out_buf);

                            et.executions.fetch_add(1, Ordering::Relaxed);
                            self.last_exec_ms[t] = now_ms;
                            
                            let f_profit = profit as f64 / 100.0;
                            let f_usd = (max_usd as f64) / PRICE_SCALE_I as f64;
                            info!(event = "arb_execute", triangle = t, profit_bps = f_profit, rate = rate);
                            self.notifier.send(format!(
                                "💰 ARB EXEC! Tri#{} profit={:.2} bps rate={} size=${:.2}",
                                t, f_profit, rate, f_usd));
                            
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

    let storm_path = "/dev/shm/beroun/toxic_storm.bin";
    if !std::path::Path::new(storm_path).exists() { let _ = std::fs::write(storm_path, [0u8]); }
    let toxic_storm_mmap = sniper_types::mmap_utils::open_mmap_readonly(storm_path).unwrap();

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
        subscribed_symbols: [FixedSymbol::new(); 128],
        subscribed_count: 0,
        last_scan: Instant::now() - core::time::Duration::from_secs(10),
        last_exec_ms: [0; TRIGON_MAX_TRIANGLES],
        toxic_storm_ptr: toxic_storm_mmap.as_ptr(),
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignRunner::new(engine, venue, "Trigon");
    runner.run().await?;
    
    Ok(())
}
