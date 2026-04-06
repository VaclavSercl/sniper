// ═══════════════════════════════════════════════════════════
// 🤖 SOVEREIGN CORTEX — L1 AI Inference Module
// v21.1 Pure Rust Hive: Candle In-Process Logit Sniping
//
// Architecture:
//   Candle in-process (zero HTTP, <20ms CPU mode)
//   Phi-3.5 Q4_K_M GGUF — 1 forward pass → 4 logits → action
//
//   L1 loop (50ms) → pushes snapshots to channel (every 2s)
//   GPU thread → pops from channel, infers, writes result to mmap
//   L1 NEVER waits for GPU — fire-and-forget
// ═══════════════════════════════════════════════════════════

use sniper_types::{EngineState, PRICE_SCALE};
use std::sync::atomic::Ordering;
use std::sync::mpsc;
use sniper_types::math::FixedPrice;
use std::time::{Duration, Instant};

const INFERENCE_INTERVAL_MS: u64 = 2000;



/// Snapshot sent from L1 to GPU thread.
#[allow(dead_code)]
#[derive(Clone)]
pub struct L1GpuRequest {
    pub price: FixedPrice,
    pub best_bid: FixedPrice,
    pub best_ask: FixedPrice,
    pub obi: FixedPrice,
    pub obi_prev: [FixedPrice; 2],        // 2 previous OBI values for momentum
    pub bid_depth: FixedPrice,
    pub ask_depth: FixedPrice,
    pub depth_trend: &'static str, // "THINNING" | "STABLE" | "GROWING"
    pub toxic_hits: u64,
    pub sweeps_recent: u64,        // sweep count in last 5 min
    pub confidence: FixedPrice,
    pub net_position: FixedPrice,
    pub regime: &'static str,
    pub fear_greed: u64,
    pub macro_bias: FixedPrice,
    pub portfolio_hedged: bool,  // CL4: Aegis shield active → skip inference
}

/// Response from GPU inference — discrete action + confidence.
#[derive(Debug, Clone, Copy, Default)]
enum GpuAction {
    #[default]
    Hold,
    SkewBid,
    SkewAsk,
    Pause,
}

#[derive(Debug, Default)]
struct GpuDecision {
    action: GpuAction,
    confidence_pct: u32,
}

// ── Telemetry: Ring Buffer + Evaluator ──

const RING_SIZE: usize = 10_000; // ~5.5h at 2s intervals
const EVAL_WINDOW_MS: u64 = 60_000; // Evaluate 60s after decision
const EVAL_INTERVAL_MS: u64 = 60_000; // Run evaluator every 60s

/// One record of a GPU decision + pre/post market snapshot.
#[allow(dead_code)]
#[derive(Clone, Copy, Default)]
struct DecisionRecord {
    timestamp_ms: u64,
    action: u8,          // 0=HOLD, 1=SKEW_BID, 2=SKEW_ASK, 3=PAUSE
    confidence: u8,
    // PRE-decision snapshot
    pre_pnl: i64,
    pre_fills: u64,
    pre_toxic: u64,
    pre_price: u64,
    pre_obi: i64,
    // POST-decision (filled by evaluator)
    post_pnl: i64,
    post_price: u64,
    post_toxic: u64,
    evaluated: bool,
}

/// Cumulative statistics — exposed via UDS GET_GPU_STATS.
use std::sync::atomic::{AtomicU64, AtomicI64};

pub struct GpuStatsSnapshot {
    pub total_inferences: u64,
    pub skew_bid_total: u64,
    pub skew_bid_wins: u64,
    pub skew_bid_toxic: u64,
    pub skew_ask_total: u64,
    pub skew_ask_wins: u64,
    pub skew_ask_toxic: u64,
    pub pause_total: u64,
    pub pause_correct: u64,
    pub hold_total: u64,
    pub total_pnl_delta: i64,
    pub start_ms: u64,
    pub skew_max_usd: f64,
    pub obi_threshold: f64,
    pub inference_interval_ms: u64,
}

impl GpuStatsSnapshot {
    pub fn to_json(&self) -> serde_json::Value {
        let uptime_h = (epoch_ms().saturating_sub(self.start_ms)) as f64 / 3_600_000.0;
        let sb_wr = if self.skew_bid_total > 0 { self.skew_bid_wins as f64 / self.skew_bid_total as f64 * 100.0 } else { 0.0 };
        let sa_wr = if self.skew_ask_total > 0 { self.skew_ask_wins as f64 / self.skew_ask_total as f64 * 100.0 } else { 0.0 };
        let pa = if self.pause_total > 0 { self.pause_correct as f64 / self.pause_total as f64 * 100.0 } else { 0.0 };
        let total_actions = self.skew_bid_total + self.skew_ask_total + self.pause_total;
        let total_toxic = self.skew_bid_toxic + self.skew_ask_toxic;
        let toxic_pct = if total_actions > 0 { total_toxic as f64 / total_actions as f64 * 100.0 } else { 0.0 };
        serde_json::json!({
            "total_inferences": self.total_inferences,
            "skew_bid": { "total": self.skew_bid_total, "wins": self.skew_bid_wins, "toxic": self.skew_bid_toxic, "win_rate": round2(sb_wr) },
            "skew_ask": { "total": self.skew_ask_total, "wins": self.skew_ask_wins, "toxic": self.skew_ask_toxic, "win_rate": round2(sa_wr) },
            "pause":    { "total": self.pause_total, "correct": self.pause_correct, "accuracy": round2(pa) },
            "hold":     { "total": self.hold_total },
            "net_pnl_impact_usd": round4(self.total_pnl_delta as f64 / PRICE_SCALE),
            "toxic_rate_pct": round2(toxic_pct),
            "uptime_hours": round2(uptime_h),
            "l1_tuning": {
                "skew_max_usd": self.skew_max_usd,
                "obi_threshold": self.obi_threshold,
                "inference_interval_ms": self.inference_interval_ms,
            },
        })
    }
}

pub struct AtomicGpuStats {
    pub total_inferences: AtomicU64,
    pub skew_bid_total: AtomicU64,
    pub skew_bid_wins: AtomicU64,
    pub skew_bid_toxic: AtomicU64,
    pub skew_ask_total: AtomicU64,
    pub skew_ask_wins: AtomicU64,
    pub skew_ask_toxic: AtomicU64,
    pub pause_total: AtomicU64,
    pub pause_correct: AtomicU64,
    pub hold_total: AtomicU64,
    pub total_pnl_delta: AtomicI64,
    pub start_ms: AtomicU64,
    pub skew_max_usd_bits: AtomicU64,
    pub obi_threshold_bits: AtomicU64,
    pub inference_interval_ms: AtomicU64,
}

impl AtomicGpuStats {
    pub const fn new() -> Self {
        Self {
            total_inferences: AtomicU64::new(0),
            skew_bid_total: AtomicU64::new(0),
            skew_bid_wins: AtomicU64::new(0),
            skew_bid_toxic: AtomicU64::new(0),
            skew_ask_total: AtomicU64::new(0),
            skew_ask_wins: AtomicU64::new(0),
            skew_ask_toxic: AtomicU64::new(0),
            pause_total: AtomicU64::new(0),
            pause_correct: AtomicU64::new(0),
            hold_total: AtomicU64::new(0),
            total_pnl_delta: AtomicI64::new(0),
            start_ms: AtomicU64::new(0),
            skew_max_usd_bits: AtomicU64::new(0),
            obi_threshold_bits: AtomicU64::new(0),
            inference_interval_ms: AtomicU64::new(INFERENCE_INTERVAL_MS),
        }
    }

    pub fn snapshot(&self) -> GpuStatsSnapshot {
        GpuStatsSnapshot {
            total_inferences: self.total_inferences.load(Ordering::Relaxed),
            skew_bid_total: self.skew_bid_total.load(Ordering::Relaxed),
            skew_bid_wins: self.skew_bid_wins.load(Ordering::Relaxed),
            skew_bid_toxic: self.skew_bid_toxic.load(Ordering::Relaxed),
            skew_ask_total: self.skew_ask_total.load(Ordering::Relaxed),
            skew_ask_wins: self.skew_ask_wins.load(Ordering::Relaxed),
            skew_ask_toxic: self.skew_ask_toxic.load(Ordering::Relaxed),
            pause_total: self.pause_total.load(Ordering::Relaxed),
            pause_correct: self.pause_correct.load(Ordering::Relaxed),
            hold_total: self.hold_total.load(Ordering::Relaxed),
            total_pnl_delta: self.total_pnl_delta.load(Ordering::Relaxed),
            start_ms: self.start_ms.load(Ordering::Relaxed),
            skew_max_usd: f64::from_bits(self.skew_max_usd_bits.load(Ordering::Relaxed)),
            obi_threshold: f64::from_bits(self.obi_threshold_bits.load(Ordering::Relaxed)),
            inference_interval_ms: self.inference_interval_ms.load(Ordering::Relaxed),
        }
    }
}

static GPU_STATS: AtomicGpuStats = AtomicGpuStats::new();

pub fn get_gpu_stats() -> GpuStatsSnapshot {
    GPU_STATS.snapshot()
}

pub fn set_l1_tuning(skew_max: f64, obi_threshold: f64, interval_ms: u64) {
    GPU_STATS.skew_max_usd_bits.store(skew_max.to_bits(), Ordering::Release);
    GPU_STATS.obi_threshold_bits.store(obi_threshold.to_bits(), Ordering::Release);
    GPU_STATS.inference_interval_ms.store(interval_ms, Ordering::Release);
    println!("  🤖 [GPU] L2 tuning applied: skew_max=${skew_max:.1} obi_thr={obi_threshold:.2} interval={interval_ms}ms");
}

fn read_l1_tuning() -> (f64, f64, u64) {
    let skew_max = f64::from_bits(GPU_STATS.skew_max_usd_bits.load(Ordering::Acquire));
    let obi_thresh = f64::from_bits(GPU_STATS.obi_threshold_bits.load(Ordering::Acquire));
    let interval = GPU_STATS.inference_interval_ms.load(Ordering::Acquire);
    let skew_max = if skew_max == 0.0 { 3.0 } else { skew_max }; // defaults fallback
    let interval = if interval == 0 { INFERENCE_INTERVAL_MS } else { interval };
    (skew_max, obi_thresh, interval)
}

/// Create L1→GPU channel. Returns sender for L1 and spawns the GPU consumer thread.
pub fn spawn_gpu_thread(engine: &'static EngineState) -> mpsc::SyncSender<L1GpuRequest> {
    let (tx, rx) = mpsc::sync_channel::<L1GpuRequest>(1);

    std::thread::Builder::new()
        .name("gpu-inference".into())
        .spawn(move || {
            run_gpu_consumer(rx, engine);
        })
        .expect("Failed to spawn GPU thread");

    println!("  🧠 [GPU] Inference thread started (Candle Logit Sniping)");
    tx
}

fn run_gpu_consumer(rx: mpsc::Receiver<L1GpuRequest>, engine: &EngineState) {
    let mut last_inference = Instant::now() - Duration::from_secs(10);
    let mut last_eval = Instant::now();
    let mut inference_count: u64 = 0;
    let mut consecutive_failures: u64 = 0;

    // ── Boot Candle L1 Brain ──
    let mut brain = candle_brain::CandleL1Brain::boot_default()
        .expect("🔴 FATAL: Candle L1 Brain boot failed. Cannot run without AI inference.");

    // Telemetry ring buffer (thread-local, zero I/O)
    let mut ring = vec![DecisionRecord::default(); RING_SIZE];
    let mut write_idx: usize = 0;

    GPU_STATS.start_ms.store(epoch_ms(), Ordering::Release);
    GPU_STATS.skew_max_usd_bits.store(3.0f64.to_bits(), Ordering::Release);
    GPU_STATS.obi_threshold_bits.store(0.0f64.to_bits(), Ordering::Release);
    GPU_STATS.inference_interval_ms.store(INFERENCE_INTERVAL_MS, Ordering::Release);

    loop {
        let req = match rx.recv() {
            Ok(r) => r,
            Err(_) => break,
        };

        // Read L2-tunable inference interval
        let (_, _, tuned_interval) = read_l1_tuning();
        if last_inference.elapsed().as_millis() < tuned_interval as u128 {
            continue;
        }

        // Drain channel — keep only latest
        let mut latest = req;
        while let Ok(newer) = rx.try_recv() {
            latest = newer;
        }

        // ── HEDGE GATE: Skip inference if Aegis shield active ──
        // When portfolio_is_hedged=1, Hydra already applies VPIN shift in L1,
        // Grid blocks all bids. LLM inference would just return HOLD anyway.
        // Save 10-50ms GPU cycles + thermal.
        if latest.portfolio_hedged {
            engine.ai_heartbeat_ms.store(epoch_ms(), Ordering::Release);
            last_inference = Instant::now();
            continue;
        }

        let ask_f64 = latest.best_ask.as_f64();
        let bid_f64 = latest.best_bid.as_f64();
        let spread = ask_f64 - bid_f64;
        let spread_bps = if bid_f64 > 0.0 { spread / bid_f64 * 10000.0 } else { 0.0 };

        // ── Candle Logit Sniping Inference ──
        let tox = latest.toxic_hits as f64 / 1000.0_f64.max(1.0);
        brain.reset_cache();
        let result = match brain.reflex_action(latest.obi.as_f64(), spread_bps, latest.macro_bias.as_f64(), tox.min(1.0)) {
            Ok((action, confidence)) => {
                let (gpu_action, conf_pct) = match action {
                    candle_brain::HftAction::Hold => (GpuAction::Hold, (confidence * 100.0) as u32),
                    candle_brain::HftAction::Bid => (GpuAction::SkewBid, (confidence * 100.0) as u32),
                    candle_brain::HftAction::Ask => (GpuAction::SkewAsk, (confidence * 100.0) as u32),
                    candle_brain::HftAction::Kill => (GpuAction::Pause, (confidence * 100.0) as u32),
                };
                Ok(GpuDecision {
                    action: gpu_action,
                    confidence_pct: conf_pct,
                })
            }
            Err(e) => Err(anyhow::anyhow!("Candle inference: {}", e)),
        };

        match result {
            Ok(decision) => {
                let now_ms = epoch_ms();
                let pre_pnl = engine.realized_pnl.load(Ordering::Relaxed);
                let pre_fills = engine.session_fill_count.load(Ordering::Relaxed);
                let pre_toxic = engine.toxic_flow_hits.load(Ordering::Relaxed);
                let pre_price = engine.micro_price.load(Ordering::Relaxed);
                let pre_obi = engine.l2_imbalance.load(Ordering::Relaxed);
                let action_id = match decision.action {
                    GpuAction::Hold => 0u8,
                    GpuAction::SkewBid => 1u8,
                    GpuAction::SkewAsk => 2u8,
                    GpuAction::Pause => 3u8,
                };
                let action_str = match decision.action {
                    GpuAction::Hold => "HOLD",
                    GpuAction::SkewBid => "SKEW_BID",
                    GpuAction::SkewAsk => "SKEW_ASK",
                    GpuAction::Pause => "PAUSE",
                };
                let conf = decision.confidence_pct as u8;

                // Apply decision to mmap
                apply_gpu_decision(&decision, engine);

                // Record in ring buffer (~50ns)
                ring[write_idx % RING_SIZE] = DecisionRecord {
                    timestamp_ms: now_ms,
                    action: action_id,
                    confidence: conf,
                    pre_pnl,
                    pre_fills,
                    pre_toxic,
                    pre_price,
                    pre_obi,
                    post_pnl: 0,
                    post_price: 0,
                    post_toxic: 0,
                    evaluated: false,
                };
                write_idx += 1;

                inference_count += 1;
                last_inference = Instant::now();

                // Log every 30th inference (~1 min)
                if inference_count % 30 == 1 {
                    println!("  🤖 [GPU] #{inference_count}: {action_str} ({conf}%)");
                }

                engine.ai_heartbeat_ms.store(now_ms, Ordering::Release);

                GPU_STATS.total_inferences.store(inference_count, Ordering::Relaxed);

                // Reset failure counter on success
                consecutive_failures = 0;
            }
            Err(e) => {
                eprintln!("  ⚠️ [GPU] Inference failed: {e}");
                consecutive_failures += 1;

                // Alert after 30 consecutive failures (~2.5 min of dead GPU)
                if consecutive_failures == 30 {
                    eprintln!("  🚨 [GPU] 30 consecutive failures — sending Sentinel alert");
                    let alert_msg = format!(
                        "{{\"cmd\":\"ALERT\",\"level\":\"WARNING\",\"msg\":\"🤖 SENTINEL: GPU INFERENCE DOWN | {} consecutive failures | Last error: {}\"}}\n",
                        consecutive_failures,
                        e.to_string().replace('"', "'").chars().take(100).collect::<String>()
                    );
                    if let Ok(mut stream) = std::os::unix::net::UnixStream::connect("/tmp/commander_events.sock") {
                        use std::io::Write;
                        let _ = stream.write_all(alert_msg.as_bytes());
                    }
                }
            }
        }

        // Run evaluator every 60s
        if last_eval.elapsed().as_millis() >= EVAL_INTERVAL_MS as u128 {
            evaluate_ring(&mut ring, engine);
            last_eval = Instant::now();
        }
    }
}

/// Counterfactual evaluator: fills POST snapshots and classifies outcomes.
fn evaluate_ring(ring: &mut [DecisionRecord], engine: &EngineState) {
    let now_ms = epoch_ms();
    let post_pnl = engine.realized_pnl.load(Ordering::Relaxed);
    let post_price = engine.micro_price.load(Ordering::Relaxed);
    let post_toxic = engine.toxic_flow_hits.load(Ordering::Relaxed);



    for record in ring.iter_mut() {
        if record.evaluated || record.timestamp_ms == 0 {
            continue;
        }
        // Only evaluate records older than 60s
        if now_ms.saturating_sub(record.timestamp_ms) < EVAL_WINDOW_MS {
            continue;
        }

        record.post_pnl = post_pnl;
        record.post_price = post_price;
        record.post_toxic = post_toxic;
        record.evaluated = true;

        let pnl_delta = record.post_pnl - record.pre_pnl;
        let new_toxic = record.post_toxic > record.pre_toxic;
        let price_move = (record.post_price as i64 - record.pre_price as i64).unsigned_abs();

        match record.action {
            1 => { // SKEW_BID
                GPU_STATS.skew_bid_total.fetch_add(1, Ordering::Relaxed);
                if pnl_delta > 0 { GPU_STATS.skew_bid_wins.fetch_add(1, Ordering::Relaxed); }
                if new_toxic { GPU_STATS.skew_bid_toxic.fetch_add(1, Ordering::Relaxed); }
            }
            2 => { // SKEW_ASK
                GPU_STATS.skew_ask_total.fetch_add(1, Ordering::Relaxed);
                if pnl_delta > 0 { GPU_STATS.skew_ask_wins.fetch_add(1, Ordering::Relaxed); }
                if new_toxic { GPU_STATS.skew_ask_toxic.fetch_add(1, Ordering::Relaxed); }
            }
            3 => { // PAUSE
                GPU_STATS.pause_total.fetch_add(1, Ordering::Relaxed);
                // Correct if price moved >$5 (we avoided a hit)
                if price_move > 500_000_000 { GPU_STATS.pause_correct.fetch_add(1, Ordering::Relaxed); }
            }
            _ => { // HOLD
                GPU_STATS.hold_total.fetch_add(1, Ordering::Relaxed);
            }
        }
        GPU_STATS.total_pnl_delta.fetch_add(pnl_delta, Ordering::Relaxed);
    }
}

/// Translate discrete GPU action into mmap writes.
/// Reads L2-tuned parameters from GpuStats.
fn apply_gpu_decision(decision: &GpuDecision, engine: &EngineState) {
    let confidence = decision.confidence_pct;

    // Read L2-tunable params
    let (skew_max_usd, obi_threshold, _) = read_l1_tuning();

    // OBI gate: skip SKEW actions if OBI below threshold
    let current_obi = (engine.l2_imbalance.load(Ordering::Relaxed) as f64 / PRICE_SCALE).abs();
    let obi_gated = current_obi < obi_threshold;

    // Scale confidence → skew magnitude (0-100% → $0-$skew_max)
    let skew_magnitude = (confidence as f64 / 100.0) * skew_max_usd * PRICE_SCALE;

    match decision.action {
        GpuAction::SkewBid if !obi_gated => {
            let skew = -(skew_magnitude as i64);
            blend_skew(engine, skew);
        }
        GpuAction::SkewAsk if !obi_gated => {
            let skew = skew_magnitude as i64;
            blend_skew(engine, skew);
        }
        GpuAction::SkewBid | GpuAction::SkewAsk => {
            // OBI below threshold — treat as HOLD (decay)
            let current = engine.l1_skew_adjustment.load(Ordering::Relaxed);
            let decayed = (current as f64 * 0.95) as i64;
            engine.l1_skew_adjustment.store(decayed, Ordering::Release);
        }
        GpuAction::Pause => {
            let now_ms = epoch_ms();
            let current_freeze = engine.sweep_freeze_until.load(Ordering::Relaxed);
            if now_ms > current_freeze {
                engine.sweep_freeze_until.store(now_ms + 4000, Ordering::Release);
                engine.ai_freeze_ms.store(4000, Ordering::Release);
            }
        }
        GpuAction::Hold => {
            // HOLD — gently decay skew toward zero
            let current = engine.l1_skew_adjustment.load(Ordering::Relaxed);
            let decayed = (current as f64 * 0.95) as i64;
            engine.l1_skew_adjustment.store(decayed, Ordering::Release);
        }
    }
}

/// Blend GPU skew with existing OBI skew (30% GPU, 70% OBI).
fn blend_skew(engine: &EngineState, gpu_skew: i64) {
    let current_skew = engine.l1_skew_adjustment.load(Ordering::Relaxed);
    let blended = (current_skew as f64 * 0.7 + gpu_skew as f64 * 0.3) as i64;
    engine.l1_skew_adjustment.store(blended, Ordering::Release);
}

fn epoch_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn round2(v: f64) -> f64 { (v * 100.0).round() / 100.0 }
fn round4(v: f64) -> f64 { (v * 10000.0).round() / 10000.0 }
