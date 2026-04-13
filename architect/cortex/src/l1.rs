// ═══════════════════════════════════════════════════════════
// 🛡️ SOVEREIGN CORTEX — L1 Tactical Module
// 50ms cycle: OBI skewing, sweep detection, ghost mode,
// adaptive learning, confidence scoring
//
// Phase 3: Pure FixedPrice hot-path (Zero-Cost Abstraction)
// ZERO-ALLOCATION & ZERO-f64 ENFORCED!
// ═══════════════════════════════════════════════════════════

use crate::gpu::L1GpuRequest;
use sniper_types::{EngineState, PRICE_SCALE_I, BOOK_LEVELS};
use sniper_types::math::FixedPrice;
use std::sync::atomic::Ordering;
use std::sync::mpsc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const CYCLE_MS: u64 = 50;
const OBI_SKEW_FACTOR: i64 = 30_000_000; // 0.30
const MAX_SKEW_USD: i64 = 300_000_000; // 3.00
const SWEEP_VOL_DROP_PCT: i64 = 85_000_000; // 0.85
const SWEEP_FREEZE_MS: u64 = 4000;
const SWEEP_DEBOUNCE_MS: u64 = 2000;
const SWEEP_MIN_VOLUME: i64 = 5_000_000; // 0.05 BTC
const BOOK_DEPTH: usize = 10;

// Adaptive learning
const ADAPTATION_INTERVAL_SECS: u64 = 120;
const MIN_SWEEP_THRESHOLD: i64 = 60_000_000; // 0.60
const MAX_SWEEP_THRESHOLD: i64 = 90_000_000; // 0.90

// Anti-paralysis
const PARALYSIS_FREEZE_RATIO: i64 = 60_000_000; // 0.60
const PARALYSIS_CHECK_WINDOW_SECS: u64 = 300;
const PARALYSIS_DESENSITIZE_STEP: i64 = 5_000_000; // 0.05

// Ghost mode
const GHOST_TOXIC_ACTIVATE: u64 = 300;
const GHOST_OBI_ACTIVATE: i64 = -85_000_000; // -0.85
const GHOST_CALM_DEACTIVATE: u64 = 50;
const GHOST_TRANSPARENCY_STEALTH: u64 = 1000;
const GHOST_TRANSPARENCY_PUBLIC: u64 = 10000;
const GHOST_COOLDOWN_SECS: u64 = 120;

#[derive(Clone, Copy, Default, Debug)]
struct BookLevel {
    price: FixedPrice,
    amount: FixedPrice,
}

// ── ZERO ALLOCATION RING BUFFER ──
#[derive(Clone, Copy)]
struct RingBuffer<const N: usize> {
    data: [FixedPrice; N],
    head: usize,
    len: usize,
}

impl<const N: usize> RingBuffer<N> {
    fn new() -> Self {
        Self { data: [FixedPrice::zero(); N], head: 0, len: 0 }
    }
    
    #[inline(always)]
    fn push(&mut self, val: FixedPrice) {
        self.data[(self.head + self.len) % N] = val;
        if self.len < N { self.len += 1; }
        else { self.head = (self.head + 1) % N; }
    }
    
    #[inline(always)]
    fn get_rev(&self, nth: usize) -> Option<FixedPrice> {
        if nth >= self.len { return None; }
        let idx = (self.head + self.len - 1 - nth) % N;
        Some(self.data[idx])
    }
    
    #[inline(always)]
    fn is_empty(&self) -> bool { self.len == 0 }
    
    #[inline(always)]
    fn len(&self) -> usize { self.len }
}

pub struct AdaptiveL1Brain {
    sweep_threshold: FixedPrice,
    true_positives: u32,
    false_positives: u32,
    total_sweeps: u32,
    last_adaptation: Instant,
    obi_history: RingBuffer<120>,
    depth_history: RingBuffer<120>,
    bid_price_history: RingBuffer<50>,
    // Anti-paralysis
    freeze_time_ms: u64,
    active_time_ms: u64,
    uptime_window_start: Instant,
    consecutive_freezes: u32,
    // Ghost mode
    ghost_active: bool,
    ghost_last_change: u64,
}

impl AdaptiveL1Brain {
    fn new() -> Self {
        Self {
            sweep_threshold: FixedPrice::new(SWEEP_VOL_DROP_PCT),
            true_positives: 0,
            false_positives: 0,
            total_sweeps: 0,
            last_adaptation: Instant::now(),
            obi_history: RingBuffer::new(),
            depth_history: RingBuffer::new(),
            bid_price_history: RingBuffer::new(),
            freeze_time_ms: 0,
            active_time_ms: 0,
            uptime_window_start: Instant::now(),
            consecutive_freezes: 0,
            ghost_active: false,
            ghost_last_change: 0,
        }
    }

    fn record_sweep(&mut self, pnl_before: FixedPrice, pnl_after: FixedPrice) {
        let was_helpful = pnl_after >= pnl_before;
        self.total_sweeps += 1;
        if was_helpful { self.true_positives += 1; }
        else { self.false_positives += 1; }
    }

    fn record_freeze_time(&mut self, ms: u64) {
        self.freeze_time_ms += ms;
        if self.uptime_window_start.elapsed().as_secs() > PARALYSIS_CHECK_WINDOW_SECS {
            self.freeze_time_ms = 0;
            self.active_time_ms = 0;
            self.uptime_window_start = Instant::now();
        }
    }

    fn record_active_time(&mut self) {
        self.active_time_ms += CYCLE_MS;
    }

    fn record_consecutive_freeze(&mut self) {
        self.consecutive_freezes += 1;
    }

    fn get_uptime_pct(&self) -> FixedPrice {
        let total = self.freeze_time_ms + self.active_time_ms;
        if total < 1000 { return FixedPrice::new(PRICE_SCALE_I); } // 1.0
        FixedPrice::new(((self.active_time_ms as u128 * PRICE_SCALE_I as u128) / total as u128) as i64)
    }

    fn check_paralysis(&mut self) {
        let uptime = self.get_uptime_pct();
        let half = FixedPrice::new(50_000_000);
        if uptime < half && (self.freeze_time_ms + self.active_time_ms) > 10_000 {
            let next_thresh = self.sweep_threshold + FixedPrice::new(PARALYSIS_DESENSITIZE_STEP);
            self.sweep_threshold = std::cmp::min(next_thresh, FixedPrice::new(MAX_SWEEP_THRESHOLD));
            
            // Zero-f64 print formatting
            let u_pct = (uptime.0 * 100) / PRICE_SCALE_I;
            let t_val = (self.sweep_threshold.0 * 100) / PRICE_SCALE_I;
            println!("  🆘 L1 ANTI-FLAP: uptime {u_pct}% → threshold raised to {t_val}%");
            
            self.freeze_time_ms = 0;
            self.active_time_ms = 0;
            self.uptime_window_start = Instant::now();
            self.consecutive_freezes = 0;
        }
    }

    fn adapt_threshold(&mut self) {
        if self.last_adaptation.elapsed().as_secs() < ADAPTATION_INTERVAL_SECS { return; }
        self.last_adaptation = Instant::now();

        let uptime = self.get_uptime_pct();
        let one = FixedPrice::new(PRICE_SCALE_I);
        if uptime < (one - FixedPrice::new(PARALYSIS_FREEZE_RATIO)) {
            let next_thresh = self.sweep_threshold + FixedPrice::new(PARALYSIS_DESENSITIZE_STEP);
            self.sweep_threshold = std::cmp::min(next_thresh, FixedPrice::new(MAX_SWEEP_THRESHOLD));
            self.freeze_time_ms = 0;
            self.active_time_ms = 0;
            self.uptime_window_start = Instant::now();
            self.consecutive_freezes = 0;
            self.true_positives = 1;
            self.false_positives = 1;
            return;
        }

        let total = self.true_positives + self.false_positives;
        if total < 5 { return; }

        let fp_rate = FixedPrice::new(((self.false_positives as u128 * PRICE_SCALE_I as u128) / total as u128) as i64);
        let success_rate = FixedPrice::new(((self.true_positives as u128 * PRICE_SCALE_I as u128) / total as u128) as i64);

        if fp_rate > FixedPrice::new(50_000_000) {
            let next_thresh = self.sweep_threshold + FixedPrice::new(5_000_000);
            self.sweep_threshold = std::cmp::min(next_thresh, FixedPrice::new(MAX_SWEEP_THRESHOLD));
        } else if fp_rate < FixedPrice::new(10_000_000) && success_rate > FixedPrice::new(80_000_000) && uptime > FixedPrice::new(80_000_000) {
            let next_thresh = self.sweep_threshold - FixedPrice::new(3_000_000);
            self.sweep_threshold = std::cmp::max(next_thresh, FixedPrice::new(MIN_SWEEP_THRESHOLD));
        }

        self.true_positives = std::cmp::max(self.true_positives / 2, 1);
        self.false_positives /= 2;
    }

    fn evaluate_ghost_mode(&mut self, toxic_hits: u64, obi: FixedPrice, l2_regime: u64) -> Option<u64> {
        let now = epoch_secs();
        if now.saturating_sub(self.ghost_last_change) < GHOST_COOLDOWN_SECS { return None; }

        if !self.ghost_active {
            if toxic_hits > GHOST_TOXIC_ACTIVATE {
                self.ghost_active = true;
                self.ghost_last_change = now;
                println!("  👻 GHOST MODE ON: toxic={toxic_hits}");
                return Some(GHOST_TRANSPARENCY_STEALTH);
            }
            if obi < FixedPrice::new(GHOST_OBI_ACTIVATE) {
                self.ghost_active = true;
                self.ghost_last_change = now;
                let o_pct = (obi.0 * 100) / PRICE_SCALE_I;
                println!("  👻 GHOST MODE ON: obi={o_pct}%");
                return Some(GHOST_TRANSPARENCY_STEALTH);
            }
        } else if toxic_hits < GHOST_CALM_DEACTIVATE && l2_regime == 2 {
            self.ghost_active = false;
            self.ghost_last_change = now;
            println!("  👁️ GHOST MODE OFF: calm market");
            return Some(GHOST_TRANSPARENCY_PUBLIC);
        }

        None
    }

    fn compute_confidence(&self, obi: FixedPrice, flicker_rate: FixedPrice, iceberg_score: FixedPrice, depth_ratio: FixedPrice) -> FixedPrice {
        let mut conf = FixedPrice::zero();
        
        let obi_abs = if obi.0 < 0 { FixedPrice::new(-obi.0) } else { obi };
        let obi_contribution = obi_abs * FixedPrice::new(30_000_000);
        let obi_cap = FixedPrice::new(30_000_000);
        conf += std::cmp::min(obi_contribution, obi_cap);
        
        let flicker_contrib = flicker_rate * FixedPrice::new(25_000_000);
        let flicker_cap = FixedPrice::new(25_000_000);
        conf += std::cmp::min(flicker_contrib, flicker_cap);
        
        conf += iceberg_score * FixedPrice::new(20_000_000);
        
        let half = FixedPrice::new(50_000_000);
        if depth_ratio < half {
            let one = FixedPrice::new(PRICE_SCALE_I);
            let two = FixedPrice::new(2_000_000_000); // 2.0
            let depth_contrib = FixedPrice::new(25_000_000) * (one - depth_ratio * two);
            conf += depth_contrib;
        }
        
        std::cmp::min(std::cmp::max(conf, FixedPrice::zero()), FixedPrice::new(PRICE_SCALE_I))
    }
}

// ═══ PURE MATH FUNCTIONS ═══

fn read_orderbook_levels(engine: &EngineState, is_bids: bool, n: usize) -> arrayvec::ArrayVec<BookLevel, BOOK_DEPTH> {
    let n = std::cmp::min(n, std::cmp::min(BOOK_LEVELS, BOOK_DEPTH));
    let mut levels = arrayvec::ArrayVec::new();
    let source = if is_bids { &engine.bids } else { &engine.asks };
    for i in 0..n {
        let price = source[i].price.load(Ordering::Relaxed) as i64;
        let amount = source[i].amount.load(Ordering::Relaxed);
        if price > 0 {
            levels.push(BookLevel { price: FixedPrice::new(price), amount: FixedPrice::new(amount.abs()) });
        }
    }
    levels
}

fn compute_obi(bids: &[BookLevel], asks: &[BookLevel], depth: usize) -> FixedPrice {
    let bid_vol = bids.iter().take(depth).fold(FixedPrice::zero(), |acc, l| acc + l.amount);
    let ask_vol = asks.iter().take(depth).fold(FixedPrice::zero(), |acc, l| acc + l.amount);
    let total = bid_vol + ask_vol;
    if total.0 < 1000 * PRICE_SCALE_I { return FixedPrice::zero(); }
    let diff = bid_vol - ask_vol;
    diff / total
}

fn detect_sweep(prev: &[BookLevel], curr: &[BookLevel], threshold: FixedPrice) -> bool {
    if prev.is_empty() || curr.is_empty() { return false; }
    let prev_vol = prev.iter().fold(FixedPrice::zero(), |acc, l| acc + l.amount);
    let curr_vol = curr.iter().fold(FixedPrice::zero(), |acc, l| acc + l.amount);

    let min_vol = FixedPrice::new(SWEEP_MIN_VOLUME);
    if prev_vol < min_vol { return false; }
    
    // Only sweep if volume dropped
    if curr_vol >= prev_vol { return false; }
    
    let drop = (prev_vol - curr_vol) / prev_vol;
    drop > threshold
}

// ZERO-ALLOCATION IN-PLACE ALGORITHM
fn detect_flickering(history: &RingBuffer<50>, window: usize) -> (bool, FixedPrice) {
    let n = std::cmp::min(history.len(), window);
    if n < 2 { return (false, FixedPrice::zero()); }
    
    let mut changes = 0;
    let mut prev = history.get_rev(0).unwrap();
    
    for i in 1..n {
        let curr = history.get_rev(i).unwrap();
        if curr.0 != prev.0 { changes += 1; }
        prev = curr;
    }
    
    let rate = FixedPrice::new((changes as i64 * PRICE_SCALE_I) / n as i64);
    (rate > FixedPrice::new(70_000_000), rate)
}

fn compute_iceberg_score(levels: &[BookLevel]) -> FixedPrice {
    if levels.len() < 3 { return FixedPrice::zero(); }
    let mut repeated = 0usize;
    for i in 0..levels.len() {
        let mut count = 0u32;
        for j in 0..levels.len() {
            if levels[j].price.0 == levels[i].price.0 { count += 1; }
        }
        if count > 1 {
            let mut already_counted = false;
            for k in 0..i {
                if levels[k].price.0 == levels[i].price.0 { already_counted = true; break; }
            }
            if !already_counted { repeated += 1; }
        }
    }
    let mut score = FixedPrice::new((repeated as i64 * PRICE_SCALE_I) / 3);
    if score > FixedPrice::new(PRICE_SCALE_I) { score = FixedPrice::new(PRICE_SCALE_I); }
    score
}

fn epoch_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64
}

fn epoch_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

pub fn run_l1_hydra(engine: &EngineState, gpu_tx: Option<mpsc::SyncSender<L1GpuRequest>>) {
    println!("🛡️ [L1] Hydra tactical shield starting ({}ms cycle)...", CYCLE_MS);

    let mut brain = AdaptiveL1Brain::new();
    let mut prev_bids: arrayvec::ArrayVec<BookLevel, BOOK_DEPTH> = arrayvec::ArrayVec::new();
    let mut prev_asks: arrayvec::ArrayVec<BookLevel, BOOK_DEPTH> = arrayvec::ArrayVec::new();
    let mut last_sweep_ms: u64 = 0;

    let mut cycle: u64 = 0;

    let l2cmd_mmap = {
        let f = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false)
            .open(sniper_types::l2_command::L2_COMMAND_PATH).unwrap();
        unsafe { memmap2::MmapMut::map_mut(&f).unwrap() }
    };
    let global_risk = unsafe {
        &*(&l2cmd_mmap[std::mem::size_of::<sniper_types::l2_command::L2CommandMatrix>()
            + std::mem::size_of::<sniper_types::l2_command::L2ASMatrix>()
            + std::mem::size_of::<sniper_types::l2_command::L2GridWarpMatrix>()]
            as *const _ as *const sniper_types::l2_command::L2GlobalRiskMatrix)
    };

    loop {
        let t0 = Instant::now();

        // ── READ ORDERBOOK ──
        let bids = read_orderbook_levels(engine, true, BOOK_DEPTH);
        let asks = read_orderbook_levels(engine, false, BOOK_DEPTH);

        // ── OBI MICRO-SKEWING ──
        let obi = compute_obi(&bids, &asks, 3);
        brain.obi_history.push(obi);

        let mut skew = obi * FixedPrice::new(OBI_SKEW_FACTOR) * FixedPrice::new(MAX_SKEW_USD);
        let max_skew = FixedPrice::new(MAX_SKEW_USD * PRICE_SCALE_I);
        if skew > max_skew { skew = max_skew; }
        if skew < FixedPrice::new(-max_skew.0) { skew = FixedPrice::new(-max_skew.0); }
        engine.l1_skew_adjustment.store(skew.0, Ordering::Release);

        // ── DEPTH TRACKING ──
        let bid_depth = bids.iter().fold(FixedPrice::zero(), |acc, l| acc + l.amount);
        let ask_depth = asks.iter().fold(FixedPrice::zero(), |acc, l| acc + l.amount);
        let total_depth = bid_depth + ask_depth;
        
        brain.depth_history.push(total_depth);

        let avg_depth = if brain.depth_history.is_empty() {
            total_depth
        } else {
            let mut sum = FixedPrice::zero();
            let len = brain.depth_history.len();
            for i in 0..len { sum += brain.depth_history.get_rev(i).unwrap(); }
            FixedPrice::new(sum.0 / len as i64)
        };
        
        let depth_ratio = if avg_depth.0 > 100_000 {
            total_depth / avg_depth
        } else {
            FixedPrice::new(PRICE_SCALE_I)
        };

        // ── FLICKERING DETECTION ──
        if let Some(first_bid) = bids.first() {
            brain.bid_price_history.push(first_bid.price);
        }
        let (_is_flickering, flicker_rate) = detect_flickering(&brain.bid_price_history, 20);

        // ── ICEBERG DETECTION ──
        let ice_bids = compute_iceberg_score(&bids);
        let ice_tasks = compute_iceberg_score(&asks);
        let iceberg_score = std::cmp::max(ice_bids, ice_tasks);

        // ── CONFIDENCE SCORING ──
        let confidence = brain.compute_confidence(obi, flicker_rate, iceberg_score, depth_ratio);
        engine.l1_confidence_score.store((confidence.0 * 10000 / PRICE_SCALE_I) as u64, Ordering::Release);

        // ── SWEEP PROTECTION ──
        if cycle > 5 {
            let now_ms = epoch_ms();
            if now_ms - last_sweep_ms > SWEEP_DEBOUNCE_MS {
                let bid_sweep = detect_sweep(&prev_bids, &bids, brain.sweep_threshold);
                let ask_sweep = detect_sweep(&prev_asks, &asks, brain.sweep_threshold);

                if bid_sweep || ask_sweep {
                    let dynamic_freeze = engine.ai_freeze_ms.load(Ordering::Relaxed);
                    let freeze_ms = if (500..=30000).contains(&dynamic_freeze) {
                        dynamic_freeze
                    } else {
                        SWEEP_FREEZE_MS
                    };
                    let freeze_until = now_ms + freeze_ms;
                    engine.sweep_freeze_until.store(freeze_until, Ordering::Release);
                    last_sweep_ms = now_ms;

                    brain.record_consecutive_freeze();
                    brain.record_freeze_time(freeze_ms);

                    let qty_fp = FixedPrice::new(engine.net_position.load(Ordering::Relaxed));
                    let qty_int = qty_fp.0 / PRICE_SCALE_I;
                    println!("  [L1] 🧹 SWEEP: Toxic surge detected! Panic selling {} BTC", qty_int);
                    
                    let toxic = engine.toxic_flow_hits.load(Ordering::Relaxed);
                    let new_toxic = if toxic > 1_000_000 { 1 } else { toxic + 1 };
                    engine.toxic_flow_hits.store(new_toxic, Ordering::Release);

                    brain.record_sweep(FixedPrice::zero(), FixedPrice::zero());

                    if cycle.is_multiple_of(100) {
                        let side = if bid_sweep { "BID" } else { "ASK" };
                        println!("  🚨 L1: Sweep ({side})! Freeze {freeze_ms}ms. Toxic: {new_toxic}");
                    }
                } else {
                    brain.record_active_time();
                }
            }
        }

        // ── GPU INFERENCE (fire-and-forget every ~2s = 40 cycles) ──
        if let Some(ref tx) = gpu_tx
            && cycle.is_multiple_of(40) {
                let regime = match engine.l2_regime_id.load(Ordering::Relaxed) {
                    1 => "TRENDING",
                    2 => "RANGING",
                    3 => "CHAOS",
                    _ => "UNKNOWN",
                };

                let obi_prev = [
                    brain.obi_history.get_rev(1).unwrap_or(FixedPrice::zero()),
                    brain.obi_history.get_rev(2).unwrap_or(FixedPrice::zero()),
                ];

                let depth_trend = if brain.depth_history.len() >= 10 {
                    let mut recent_sum = FixedPrice::zero();
                    for i in 0..5 { recent_sum += brain.depth_history.get_rev(i).unwrap(); }
                    
                    let mut older_sum = FixedPrice::zero();
                    for i in 5..10 { older_sum += brain.depth_history.get_rev(i).unwrap(); }
                    
                    if older_sum.0 < 100_000 { "STABLE" }
                    else if (recent_sum * FixedPrice::new(PRICE_SCALE_I)) / older_sum < FixedPrice::new(70_000_000) { "THINNING" }
                    else if (recent_sum * FixedPrice::new(PRICE_SCALE_I)) / older_sum > FixedPrice::new(130_000_000) { "GROWING" }
                    else { "STABLE" }
                } else {
                    "STABLE"
                };

                let _ = tx.try_send(L1GpuRequest {
                    price: FixedPrice::new(engine.micro_price.load(Ordering::Relaxed) as i64),
                    best_bid: FixedPrice::new(engine.best_bid.load(Ordering::Relaxed) as i64),
                    best_ask: FixedPrice::new(engine.best_ask.load(Ordering::Relaxed) as i64),
                    obi,
                    obi_prev,
                    bid_depth,
                    ask_depth,
                    depth_trend,
                    toxic_hits: engine.toxic_flow_hits.load(Ordering::Relaxed),
                    sweeps_recent: brain.total_sweeps as u64,
                    confidence,
                    net_position: FixedPrice::new(engine.net_position.load(Ordering::Relaxed)),
                    regime,
                    fear_greed: engine.macro_fear_greed.load(Ordering::Relaxed),
                    macro_bias: FixedPrice::new(engine.macro_bias.load(Ordering::Relaxed) * (PRICE_SCALE_I / 10000)),
                    portfolio_hedged: global_risk.portfolio_is_hedged.load(Ordering::Relaxed) == 1,
                });
            }

        prev_bids = bids;
        prev_asks = asks;
        cycle += 1;

        // ── ADAPTIVE LEARNING ──
        brain.adapt_threshold();

        // ── ANTI-FLAP CHECK (every 10s = 200 cycles) ──
        if cycle.is_multiple_of(200) {
            brain.check_paralysis();
        }

        // ── ZERO-f64 LOGGING (every ~60s = 1200 cycles) ──
        if cycle.is_multiple_of(1200) {
            let toxic = engine.toxic_flow_hits.load(Ordering::Relaxed);
            let l2_regime = engine.l2_regime_id.load(Ordering::Relaxed);

            let total = brain.true_positives + brain.false_positives;
            let fp_rate = if total > 0 { FixedPrice::new((brain.false_positives as i64 * PRICE_SCALE_I) / total as i64) } else { FixedPrice::zero() };
            let success_rate = if total > 0 { FixedPrice::new((brain.true_positives as i64 * PRICE_SCALE_I) / total as i64) } else { FixedPrice::zero() };
            
            engine.l1_false_positive_rate.store((fp_rate.0 * 10000 / PRICE_SCALE_I) as u64, Ordering::Release);
            engine.l1_sweep_success_rate.store((success_rate.0 * 10000 / PRICE_SCALE_I) as u64, Ordering::Release);
            engine.l1_uptime_pct.store((brain.get_uptime_pct().0 * 10000 / PRICE_SCALE_I) as u64, Ordering::Release);

            if let Some(ghost_val) = brain.evaluate_ghost_mode(toxic, obi, l2_regime) {
                engine.ghost_transparency.store(ghost_val, Ordering::Release);
            }

            if engine.ai_learning_trigger.load(Ordering::Relaxed) == 1 {
                brain.adapt_threshold();
                engine.ai_learning_trigger.store(0, Ordering::Release);
                println!("  🧠 L1: L2 requested learning cycle");
            }

            let mid = engine.micro_price.load(Ordering::Relaxed) / PRICE_SCALE_I as u64;
            
            // FPU-FREE Int Math pro Log Formatování:
            let obi_i = obi.0 / (PRICE_SCALE_I / 1000); 
            let skew_i = skew.0 / PRICE_SCALE_I;
            let skew_f = (skew.0.abs() % PRICE_SCALE_I) / (PRICE_SCALE_I / 100);
            let conf_i = (confidence.0 * 100) / PRICE_SCALE_I;
            let thr_i = (brain.sweep_threshold.0 * 100) / PRICE_SCALE_I;
            let up_i = (brain.get_uptime_pct().0 * 100) / PRICE_SCALE_I;
            let ghost_str = if brain.ghost_active { "ON" } else { "OFF" };

            println!("  🛡️ L1[{cycle}]: OBI={obi_i}e-3 Skew=${skew_i}.{skew_f:02} \
                Mid=${mid} Toxic={toxic} Conf={conf_i}% \
                Thresh={thr_i}% Up={up_i}% Ghost={ghost_str}");
        }

        // ── SLEEP ──
        let elapsed = t0.elapsed();
        let sleep_dur = Duration::from_millis(CYCLE_MS).saturating_sub(elapsed);
        if !sleep_dur.is_zero() {
            std::thread::sleep(sleep_dur);
        }
    }
}
