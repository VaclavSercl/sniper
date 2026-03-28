// ═══════════════════════════════════════════════════════════
// 🛡️ SOVEREIGN CORTEX — L1 Tactical Module
// 50ms cycle: OBI skewing, sweep detection, ghost mode,
// adaptive learning, confidence scoring
//
// Phase 2: Pure Rust reimplementation of l1_shield.py
// Zero-copy mmap access via sniper_types
// ═══════════════════════════════════════════════════════════

use crate::gpu::L1GpuRequest;
use sniper_types::{EngineState, PRICE_SCALE, BOOK_LEVELS};
use std::collections::VecDeque;
use std::sync::atomic::Ordering;
use std::sync::mpsc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const CYCLE_MS: u64 = 50;
const OBI_SKEW_FACTOR: f64 = 0.3;
const MAX_SKEW_USD: f64 = 3.0;
const SWEEP_VOL_DROP_PCT: f64 = 0.85;
const SWEEP_FREEZE_MS: u64 = 4000;
const SWEEP_DEBOUNCE_MS: u64 = 2000;
const SWEEP_MIN_VOLUME: f64 = 0.05;  // Ignore sweeps on thin books (<0.05 BTC total)
const BOOK_DEPTH: usize = 10;

// Adaptive learning
const CONFIDENCE_WINDOW: usize = 100;
const ADAPTATION_INTERVAL_SECS: u64 = 120;
const MIN_SWEEP_THRESHOLD: f64 = 0.60;
const MAX_SWEEP_THRESHOLD: f64 = 0.90;

// Anti-paralysis
const PARALYSIS_FREEZE_RATIO: f64 = 0.60;
const PARALYSIS_CHECK_WINDOW_SECS: f64 = 300.0;
const PARALYSIS_DESENSITIZE_STEP: f64 = 0.05;
const MAX_CONSECUTIVE_FREEZES: u32 = 4;

// Ghost mode
const GHOST_TOXIC_ACTIVATE: u64 = 300;
const GHOST_OBI_ACTIVATE: f64 = -0.85;
const GHOST_CALM_DEACTIVATE: u64 = 50;
const GHOST_TRANSPARENCY_STEALTH: u64 = 1000;
const GHOST_TRANSPARENCY_PUBLIC: u64 = 10000;
const GHOST_COOLDOWN_SECS: f64 = 120.0;

struct BookLevel {
    price: f64,
    amount: f64,
}

pub struct AdaptiveL1Brain {
    sweep_threshold: f64,
    true_positives: u32,
    false_positives: u32,
    total_sweeps: u32,
    last_adaptation: Instant,
    obi_history: VecDeque<f64>,
    depth_history: VecDeque<f64>,
    bid_price_history: VecDeque<f64>,
    // Anti-paralysis
    freeze_time_ms: u64,
    active_time_ms: u64,
    uptime_window_start: Instant,
    consecutive_freezes: u32,
    // Ghost mode
    ghost_active: bool,
    ghost_last_change: f64,
}

impl AdaptiveL1Brain {
    fn new() -> Self {
        Self {
            sweep_threshold: SWEEP_VOL_DROP_PCT,
            true_positives: 0,
            false_positives: 0,
            total_sweeps: 0,
            last_adaptation: Instant::now(),
            obi_history: VecDeque::with_capacity(120),
            depth_history: VecDeque::with_capacity(120),
            bid_price_history: VecDeque::with_capacity(50),
            freeze_time_ms: 0,
            active_time_ms: 0,
            uptime_window_start: Instant::now(),
            consecutive_freezes: 0,
            ghost_active: false,
            ghost_last_change: 0.0,
        }
    }

    fn record_sweep(&mut self, pnl_before: f64, pnl_after: f64) {
        let was_helpful = pnl_after >= pnl_before;
        self.total_sweeps += 1;
        if was_helpful { self.true_positives += 1; }
        else { self.false_positives += 1; }
    }

    fn record_freeze_time(&mut self, ms: u64) {
        self.freeze_time_ms += ms;
        if self.uptime_window_start.elapsed().as_secs_f64() > PARALYSIS_CHECK_WINDOW_SECS {
            self.freeze_time_ms = 0;
            self.active_time_ms = 0;
            self.uptime_window_start = Instant::now();
        }
    }

    fn record_active_time(&mut self) {
        self.active_time_ms += CYCLE_MS;
        // Don't reset consecutive_freezes here — that was the bug!
        // Instead, decay naturally in the sliding-window check below
    }

    fn record_consecutive_freeze(&mut self) {
        self.consecutive_freezes += 1;
    }

    /// v14.0: Check if L1 is in paralysis (flapping).
    /// Uses uptime ratio over rolling window instead of broken consecutive counter.
    fn check_paralysis(&mut self) {
        let uptime = self.get_uptime_pct();
        // If frozen >50% of time in the window, desensitize
        if uptime < 0.50 && (self.freeze_time_ms + self.active_time_ms) > 10_000 {
            self.sweep_threshold = (self.sweep_threshold + PARALYSIS_DESENSITIZE_STEP)
                .min(MAX_SWEEP_THRESHOLD);
            println!("  🆘 L1 ANTI-FLAP: uptime {:.0}% → threshold raised to {:.2}",
                uptime * 100.0, self.sweep_threshold);
            // Reset window
            self.freeze_time_ms = 0;
            self.active_time_ms = 0;
            self.uptime_window_start = Instant::now();
            self.consecutive_freezes = 0;
        }
    }

    fn get_uptime_pct(&self) -> f64 {
        let total = self.freeze_time_ms + self.active_time_ms;
        if total < 1000 { return 1.0; }
        self.active_time_ms as f64 / total as f64
    }

    fn adapt_threshold(&mut self) {
        if self.last_adaptation.elapsed().as_secs() < ADAPTATION_INTERVAL_SECS { return; }
        self.last_adaptation = Instant::now();

        let uptime = self.get_uptime_pct();
        if uptime < (1.0 - PARALYSIS_FREEZE_RATIO) {
            self.sweep_threshold = (self.sweep_threshold + PARALYSIS_DESENSITIZE_STEP)
                .min(MAX_SWEEP_THRESHOLD);
            println!("  🆘 L1 ANTI-PARALYSIS: uptime {:.0}% → threshold {:.2}",
                uptime * 100.0, self.sweep_threshold);
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

        let fp_rate = self.false_positives as f64 / total as f64;
        let success_rate = self.true_positives as f64 / total as f64;

        if fp_rate > 0.5 {
            self.sweep_threshold = (self.sweep_threshold + 0.05).min(MAX_SWEEP_THRESHOLD);
        } else if fp_rate < 0.1 && success_rate > 0.8 && uptime > 0.8 {
            self.sweep_threshold = (self.sweep_threshold - 0.03).max(MIN_SWEEP_THRESHOLD);
        }

        self.true_positives = (self.true_positives / 2).max(1);
        self.false_positives /= 2;
    }

    fn evaluate_ghost_mode(&mut self, toxic_hits: u64, obi: f64, l2_regime: u64) -> Option<u64> {
        let now = epoch_secs();
        if now - self.ghost_last_change < GHOST_COOLDOWN_SECS { return None; }

        if !self.ghost_active {
            if toxic_hits > GHOST_TOXIC_ACTIVATE {
                self.ghost_active = true;
                self.ghost_last_change = now;
                println!("  👻 GHOST MODE ON: toxic={toxic_hits}");
                return Some(GHOST_TRANSPARENCY_STEALTH);
            }
            if obi < GHOST_OBI_ACTIVATE {
                self.ghost_active = true;
                self.ghost_last_change = now;
                println!("  👻 GHOST MODE ON: obi={obi:.3}");
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

    fn compute_confidence(&self, obi: f64, flicker_rate: f64, iceberg_score: f64, depth_ratio: f64) -> f64 {
        let mut conf: f64 = 0.0;
        conf += (obi.abs() * 0.3).min(0.3);
        conf += (flicker_rate * 0.25).min(0.25);
        conf += iceberg_score * 0.2;
        if depth_ratio < 0.5 {
            conf += 0.25 * (1.0 - depth_ratio * 2.0);
        }
        conf.clamp(0.0, 1.0)
    }
}

// ═══ PURE MATH FUNCTIONS ═══

fn read_orderbook_levels(engine: &EngineState, is_bids: bool, n: usize) -> Vec<BookLevel> {
    let n = n.min(BOOK_LEVELS);
    let mut levels = Vec::with_capacity(n);
    let source = if is_bids { &engine.bids } else { &engine.asks };
    for i in 0..n {
        let price = source[i].price.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
        let amount = source[i].amount.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
        if price > 0.0 {
            levels.push(BookLevel { price, amount: amount.abs() });
        }
    }
    levels
}

fn compute_obi(bids: &[BookLevel], asks: &[BookLevel], depth: usize) -> f64 {
    let bid_vol: f64 = bids.iter().take(depth).map(|l| l.amount).sum();
    let ask_vol: f64 = asks.iter().take(depth).map(|l| l.amount).sum();
    let total = bid_vol + ask_vol;
    if total < 1e-10 { return 0.0; }
    (bid_vol - ask_vol) / total
}

fn detect_sweep(prev: &[BookLevel], curr: &[BookLevel], threshold: f64) -> bool {
    if prev.is_empty() || curr.is_empty() { return false; }
    let prev_vol: f64 = prev.iter().map(|l| l.amount).sum();
    let curr_vol: f64 = curr.iter().map(|l| l.amount).sum();
    // Need meaningful volume to detect a sweep (avoid false positives on thin books)
    if prev_vol < SWEEP_MIN_VOLUME { return false; }
    let drop = (prev_vol - curr_vol) / prev_vol;
    drop > threshold
}

fn detect_flickering(history: &VecDeque<f64>, window: usize) -> (bool, f64) {
    if history.len() < window { return (false, 0.0); }
    let recent: Vec<f64> = history.iter().rev().take(window).copied().collect();
    let changes = recent.windows(2).filter(|w| w[0] != w[1]).count();
    let rate = changes as f64 / window as f64;
    (rate > 0.7, rate)
}

fn compute_iceberg_score(levels: &[BookLevel]) -> f64 {
    if levels.len() < 3 { return 0.0; }
    let mut price_counts = std::collections::HashMap::new();
    for l in levels {
        let p = (l.price * 100.0).round() as i64;
        *price_counts.entry(p).or_insert(0u32) += 1;
    }
    let repeated = price_counts.values().filter(|&&c| c > 1).count();
    (repeated as f64 / 3.0).min(1.0)
}

fn epoch_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64
}

fn epoch_secs() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64()
}

/// Run the L1 tactical loop for Hydra (50ms cycle).
/// gpu_tx: Optional channel to fire snapshots for GPU inference (non-blocking).
pub fn run_l1_hydra(engine: &EngineState, gpu_tx: Option<mpsc::SyncSender<L1GpuRequest>>) {
    println!("🛡️ [L1] Hydra tactical shield starting ({}ms cycle)...", CYCLE_MS);

    let mut brain = AdaptiveL1Brain::new();
    let mut prev_bids: Vec<BookLevel> = Vec::new();
    let mut prev_asks: Vec<BookLevel> = Vec::new();
    let mut last_sweep_ms: u64 = 0;
    let mut pnl_at_sweep: f64 = 0.0;
    let mut cycle: u64 = 0;

    loop {
        let t0 = Instant::now();

        // ── READ ORDERBOOK ──
        let bids = read_orderbook_levels(engine, true, BOOK_DEPTH);
        let asks = read_orderbook_levels(engine, false, BOOK_DEPTH);

        // ── OBI MICRO-SKEWING ──
        let obi = compute_obi(&bids, &asks, 3);
        if brain.obi_history.len() == 120 { brain.obi_history.pop_front(); }
        brain.obi_history.push_back(obi);

        let skew_usd = (obi * OBI_SKEW_FACTOR * MAX_SKEW_USD).clamp(-MAX_SKEW_USD, MAX_SKEW_USD);
        let skew_scaled = (skew_usd * PRICE_SCALE) as i64;
        engine.l1_skew_adjustment.store(skew_scaled, Ordering::Release);

        // ── DEPTH TRACKING ──
        let bid_depth: f64 = bids.iter().map(|l| l.amount).sum();
        let ask_depth: f64 = asks.iter().map(|l| l.amount).sum();
        let total_depth = bid_depth + ask_depth;
        if brain.depth_history.len() == 120 { brain.depth_history.pop_front(); }
        brain.depth_history.push_back(total_depth);

        let avg_depth = if brain.depth_history.is_empty() {
            total_depth
        } else {
            brain.depth_history.iter().sum::<f64>() / brain.depth_history.len() as f64
        };
        let depth_ratio = total_depth / avg_depth.max(0.001);

        // ── FLICKERING DETECTION ──
        if let Some(first_bid) = bids.first() {
            if brain.bid_price_history.len() == 50 { brain.bid_price_history.pop_front(); }
            brain.bid_price_history.push_back(first_bid.price);
        }
        let (_is_flickering, flicker_rate) = detect_flickering(&brain.bid_price_history, 20);

        // ── ICEBERG DETECTION ──
        let iceberg_score = compute_iceberg_score(&bids).max(compute_iceberg_score(&asks));

        // ── CONFIDENCE SCORING ──
        let confidence = brain.compute_confidence(obi, flicker_rate, iceberg_score, depth_ratio);
        engine.l1_confidence_score.store((confidence * 10000.0) as u64, Ordering::Release);

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

                    pnl_at_sweep = engine.realized_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;

                    let toxic = engine.toxic_flow_hits.load(Ordering::Relaxed);
                    let new_toxic = if toxic > 1_000_000 { 1 } else { toxic + 1 };
                    engine.toxic_flow_hits.store(new_toxic, Ordering::Release);

                    brain.record_sweep(pnl_at_sweep, pnl_at_sweep);

                    if cycle % 100 == 0 {
                        let side = if bid_sweep { "BID" } else { "ASK" };
                        println!("  🚨 L1: Sweep ({side})! Freeze {freeze_ms}ms. Toxic: {new_toxic}");
                    }
                } else {
                    brain.record_active_time();
                }
            }
        }

        // ── GPU INFERENCE (fire-and-forget every ~2s = 40 cycles) ──
        if let Some(ref tx) = gpu_tx {
            if cycle % 40 == 0 {
                let regime = match engine.l2_regime_id.load(Ordering::Relaxed) {
                    1 => "TRENDING",
                    2 => "RANGING",
                    3 => "CHAOS",
                    _ => "UNKNOWN",
                };

                // OBI history: last 2 values for momentum
                let obi_prev = [
                    brain.obi_history.iter().rev().nth(1).copied().unwrap_or(0.0),
                    brain.obi_history.iter().rev().nth(2).copied().unwrap_or(0.0),
                ];

                // Depth trend from recent history
                let depth_trend = if brain.depth_history.len() >= 10 {
                    let recent: f64 = brain.depth_history.iter().rev().take(5).sum::<f64>() / 5.0;
                    let older: f64 = brain.depth_history.iter().rev().skip(5).take(5).sum::<f64>() / 5.0;
                    if older < 0.001 { "STABLE" }
                    else if recent / older < 0.7 { "THINNING" }
                    else if recent / older > 1.3 { "GROWING" }
                    else { "STABLE" }
                } else {
                    "STABLE"
                };

                let _ = tx.try_send(L1GpuRequest {
                    price: engine.micro_price.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
                    best_bid: engine.best_bid.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
                    best_ask: engine.best_ask.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
                    obi,
                    obi_prev,
                    bid_depth,
                    ask_depth,
                    depth_trend,
                    toxic_hits: engine.toxic_flow_hits.load(Ordering::Relaxed),
                    sweeps_recent: brain.total_sweeps as u64,
                    confidence,
                    net_position: engine.net_position.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
                    regime,
                    fear_greed: engine.macro_fear_greed.load(Ordering::Relaxed),
                    macro_bias: engine.macro_bias.load(Ordering::Relaxed) as f64 / 10000.0,
                    portfolio_hedged: false, // TODO: read CL4 mmap when cortex integrates l2_command
                });
            }
        }

        prev_bids = bids;
        prev_asks = asks;
        cycle += 1;

        // ── ADAPTIVE LEARNING ──
        brain.adapt_threshold();

        // ── ANTI-FLAP CHECK (every 10s = 200 cycles) ──
        if cycle % 200 == 0 {
            brain.check_paralysis();
        }

        // ── PERIODIC STATUS + GHOST MODE (every ~60s = 1200 cycles) ──
        if cycle % 1200 == 0 {
            let toxic = engine.toxic_flow_hits.load(Ordering::Relaxed);
            let l2_regime = engine.l2_regime_id.load(Ordering::Relaxed);

            // Write L1 metrics
            let total = brain.true_positives + brain.false_positives;
            let fp_rate = if total > 0 { brain.false_positives as f64 / total as f64 } else { 0.0 };
            let success_rate = if total > 0 { brain.true_positives as f64 / total as f64 } else { 0.0 };
            engine.l1_false_positive_rate.store((fp_rate * 10000.0) as u64, Ordering::Release);
            engine.l1_sweep_success_rate.store((success_rate * 10000.0) as u64, Ordering::Release);
            engine.l1_uptime_pct.store((brain.get_uptime_pct() * 10000.0) as u64, Ordering::Release);

            // Ghost mode evaluation
            if let Some(ghost_val) = brain.evaluate_ghost_mode(toxic, obi, l2_regime) {
                engine.ghost_transparency.store(ghost_val, Ordering::Release);
            }

            // Check L2 learning trigger
            if engine.ai_learning_trigger.load(Ordering::Relaxed) == 1 {
                brain.adapt_threshold();
                engine.ai_learning_trigger.store(0, Ordering::Release);
                println!("  🧠 L1: L2 requested learning cycle");
            }

            let mid = engine.micro_price.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            println!("  🛡️ L1[{cycle}]: OBI={obi:+.3} Skew=${skew_usd:+.2} \
                Mid=${mid:.0} Toxic={toxic} Conf={confidence:.2} \
                Thresh={:.2} Up={:.0}% Ghost={}",
                brain.sweep_threshold, brain.get_uptime_pct() * 100.0,
                if brain.ghost_active { "ON" } else { "OFF" });
        }

        // ── SLEEP ──
        let elapsed = t0.elapsed();
        let sleep_dur = Duration::from_millis(CYCLE_MS).saturating_sub(elapsed);
        if !sleep_dur.is_zero() {
            std::thread::sleep(sleep_dur);
        }
    }
}
