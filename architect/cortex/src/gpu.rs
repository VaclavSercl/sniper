// ═══════════════════════════════════════════════════════════
// 🤖 SOVEREIGN CORTEX — GPU Inference Module (LM Studio)
// Non-blocking L1 tactical AI via local Phi-3.5 Mini
//
// Architecture:
//   L1 loop (50ms) → pushes snapshots to channel (every 2s)
//   GPU thread → pops from channel, infers, writes result to mmap
//   L1 NEVER waits for GPU — fire-and-forget
//
// v13.1: Merged prompt design:
//   - JSON structured input (better for small models)
//   - Discrete actions (HOLD, SKEW_BID, SKEW_ASK, PAUSE_TRADING)
//   - System prompt enforcing strict JSON-only output
//   - OBI history + macro context for trend awareness
// ═══════════════════════════════════════════════════════════

use sniper_types::{EngineState, PRICE_SCALE};
use std::sync::atomic::Ordering;
use std::sync::mpsc;
use std::time::{Duration, Instant};

const LMS_URL: &str = "http://localhost:1234/v1/chat/completions";
const LMS_MODEL: &str = "phi-3.5-mini-instruct";
const INFERENCE_INTERVAL_MS: u64 = 2000;
const MAX_TOKENS: u32 = 80;

// System prompt: strict, no-nonsense, JSON-only.
// Optimized for small models that tend to "chat" and wrap JSON in markdown.
const SYSTEM_PROMPT: &str = "\
<role>You are SNIPER-L1, a deterministic HFT tactical micro-controller for BTC-USD.</role>\
<task>Map market microstructure data to exactly ONE tactical action.</task>\
<rules>\
1. OUTPUT STRICTLY VALID JSON. No markdown, no text outside JSON.\
2. VPIN and Portfolio Hedging are handled by L2 Oracle. You control ONLY short-term quote skew.\
3. confidence_pct: 0-100. Below 50 = uncertain noise. Above 80 = strong divergence only.\
4. reason: max 8 words.\
</rules>\
<logic>\
ACTION: SKEW_BID  | WHEN: OBI > +0.3 AND depth GROWING\
ACTION: SKEW_ASK  | WHEN: OBI < -0.3 AND depth THINNING\
ACTION: PAUSE_TRADING | WHEN: sweeps > 5 OR toxic > 500\
ACTION: HOLD      | WHEN: neutral, conflicting, or momentum reversal in OBI prev\
</logic>\
<example>{\"action\":\"HOLD\",\"confidence_pct\":65,\"reason\":\"OBI neutral depth stable\"}</example>";

/// Snapshot sent from L1 to GPU thread.
#[derive(Clone)]
pub struct L1GpuRequest {
    pub price: f64,
    pub best_bid: f64,
    pub best_ask: f64,
    pub obi: f64,
    pub obi_prev: [f64; 2],        // 2 previous OBI values for momentum
    pub bid_depth: f64,
    pub ask_depth: f64,
    pub depth_trend: &'static str, // "THINNING" | "STABLE" | "GROWING"
    pub toxic_hits: u64,
    pub sweeps_recent: u64,        // sweep count in last 5 min
    pub confidence: f64,
    pub net_position: f64,
    pub regime: &'static str,
    pub fear_greed: u64,
    pub macro_bias: f64,
    pub portfolio_hedged: bool,  // CL4: Aegis shield active → skip inference
}

/// Response from GPU inference — discrete action + confidence.
#[derive(Debug, serde::Deserialize, Default)]
struct GpuDecision {
    action: Option<String>,       // HOLD | SKEW_BID | SKEW_ASK | PAUSE_TRADING
    confidence_pct: Option<u32>,  // 0-100
    reason: Option<String>,       // Short explanation
}

// ── Telemetry: Ring Buffer + Evaluator ──

const RING_SIZE: usize = 10_000; // ~5.5h at 2s intervals
const EVAL_WINDOW_MS: u64 = 60_000; // Evaluate 60s after decision
const EVAL_INTERVAL_MS: u64 = 60_000; // Run evaluator every 60s

/// One record of a GPU decision + pre/post market snapshot.
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
#[derive(Default)]
pub struct GpuStats {
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
    // L2-tunable params (written by UDS SET_L1_TUNING, read by GPU thread)
    pub skew_max_usd: f64,          // default 3.0, range [0.5, 5.0]
    pub obi_threshold: f64,         // default 0.0, range [0.0, 0.8]
    pub inference_interval_ms: u64, // default 2000, range [500, 10000]
}

impl GpuStats {
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

fn action_to_id(action: &str) -> u8 {
    match action {
        "SKEW_BID" => 1,
        "SKEW_ASK" => 2,
        "PAUSE_TRADING" | "PAUSE" => 3,
        _ => 0, // HOLD
    }
}

/// Thread-safe stats accessible from UDS via Arc<Mutex>.
use std::sync::{Arc, Mutex};
static GPU_STATS: std::sync::LazyLock<Arc<Mutex<GpuStats>>> =
    std::sync::LazyLock::new(|| Arc::new(Mutex::new(GpuStats::default())));

/// Get a clone of current GPU stats (called from UDS handler).
pub fn get_gpu_stats() -> GpuStats {
    GPU_STATS.lock().map(|s| GpuStats { ..*s }).unwrap_or_default()
}

/// Set L1 tuning parameters (called from UDS SET_L1_TUNING).
pub fn set_l1_tuning(skew_max: f64, obi_threshold: f64, interval_ms: u64) {
    if let Ok(mut stats) = GPU_STATS.lock() {
        stats.skew_max_usd = skew_max;
        stats.obi_threshold = obi_threshold;
        stats.inference_interval_ms = interval_ms;
        println!("  🤖 [GPU] L2 tuning applied: skew_max=${skew_max:.1} obi_thr={obi_threshold:.2} interval={interval_ms}ms");
    }
}

/// Read current L1 tuning (called by GPU consumer thread).
fn read_l1_tuning() -> (f64, f64, u64) {
    GPU_STATS.lock()
        .map(|s| (s.skew_max_usd, s.obi_threshold, s.inference_interval_ms))
        .unwrap_or((3.0, 0.0, INFERENCE_INTERVAL_MS))
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

    println!("  🤖 [GPU] Inference thread started (Phi-3.5 Mini @ localhost:1234)");
    tx
}

fn run_gpu_consumer(rx: mpsc::Receiver<L1GpuRequest>, engine: &EngineState) {
    let mut last_inference = Instant::now() - Duration::from_secs(10);
    let mut last_eval = Instant::now();
    let mut inference_count: u64 = 0;
    let mut consecutive_failures: u64 = 0;

    // Telemetry ring buffer (thread-local, zero I/O)
    let mut ring = vec![DecisionRecord::default(); RING_SIZE];
    let mut write_idx: usize = 0;

    // Initialize stats start time + defaults
    if let Ok(mut stats) = GPU_STATS.lock() {
        stats.start_ms = epoch_ms();
        stats.skew_max_usd = 3.0;
        stats.obi_threshold = 0.0;
        stats.inference_interval_ms = INFERENCE_INTERVAL_MS;
    }

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

        // Compact one-line prompt
        let fg_label = match latest.fear_greed {
            0..=24 => "EXTREME_FEAR",
            25..=49 => "FEAR",
            50..=74 => "GREED",
            _ => "EXTREME_GREED",
        };

        let spread = latest.best_ask - latest.best_bid;
        let prompt_str = format!(
            "[BTC bid:{:.0} ask:{:.0} spr:{:.1}] [OBI:{:+.2} prev:{:+.2},{:+.2}] \
             [LOB:{} {:.0}/{:.0}] [RISK tox:{} swp:{}] \
             [POS {:.5}] [MACRO reg:{} F&G:{}({}) bias:{:+.2}]",
            latest.best_bid, latest.best_ask, spread,
            latest.obi, latest.obi_prev[0], latest.obi_prev[1],
            latest.depth_trend, latest.bid_depth, latest.ask_depth,
            latest.toxic_hits, latest.sweeps_recent,
            latest.net_position,
            latest.regime, latest.fear_greed, fg_label, latest.macro_bias,
        );

        match call_lms(&prompt_str) {
            Ok(decision) => {
                // PRE-decision snapshot (before applying)
                let now_ms = epoch_ms();
                let pre_pnl = engine.realized_pnl.load(Ordering::Relaxed);
                let pre_fills = engine.session_fill_count.load(Ordering::Relaxed);
                let pre_toxic = engine.toxic_flow_hits.load(Ordering::Relaxed);
                let pre_price = engine.micro_price.load(Ordering::Relaxed);
                let pre_obi = engine.l2_imbalance.load(Ordering::Relaxed);
                let action_str = decision.action.as_deref().unwrap_or("HOLD");
                let action_id = action_to_id(action_str);
                let conf = decision.confidence_pct.unwrap_or(0) as u8;

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
                    println!("  🤖 [GPU] #{inference_count}: {action_str} ({conf}%) — {}",
                        decision.reason.as_deref().unwrap_or(""));
                }

                engine.ai_heartbeat_ms.store(now_ms, Ordering::Release);

                // Update total inferences
                if let Ok(mut stats) = GPU_STATS.lock() {
                    stats.total_inferences = inference_count;
                }

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

    let mut stats = match GPU_STATS.lock() {
        Ok(s) => s,
        Err(_) => return,
    };

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
                stats.skew_bid_total += 1;
                if pnl_delta > 0 { stats.skew_bid_wins += 1; }
                if new_toxic { stats.skew_bid_toxic += 1; }
            }
            2 => { // SKEW_ASK
                stats.skew_ask_total += 1;
                if pnl_delta > 0 { stats.skew_ask_wins += 1; }
                if new_toxic { stats.skew_ask_toxic += 1; }
            }
            3 => { // PAUSE
                stats.pause_total += 1;
                // Correct if price moved >$5 (we avoided a hit)
                if price_move > 500_000_000 { stats.pause_correct += 1; }
            }
            _ => { // HOLD
                stats.hold_total += 1;
            }
        }
        stats.total_pnl_delta += pnl_delta;
    }
}

/// Translate discrete GPU action into mmap writes.
/// Reads L2-tuned parameters from GpuStats.
fn apply_gpu_decision(decision: &GpuDecision, engine: &EngineState) {
    let confidence = decision.confidence_pct.unwrap_or(0);
    let action = decision.action.as_deref().unwrap_or("HOLD");

    // Read L2-tunable params
    let (skew_max_usd, obi_threshold, _) = read_l1_tuning();

    // OBI gate: skip SKEW actions if OBI below threshold
    let current_obi = (engine.l2_imbalance.load(Ordering::Relaxed) as f64 / PRICE_SCALE).abs();
    let obi_gated = current_obi < obi_threshold;

    // Scale confidence → skew magnitude (0-100% → $0-$skew_max)
    let skew_magnitude = (confidence as f64 / 100.0) * skew_max_usd * PRICE_SCALE;

    match action {
        "SKEW_BID" if !obi_gated => {
            // Skew quotes toward buy side (negative skew = cheaper bids)
            let skew = -(skew_magnitude as i64);
            blend_skew(engine, skew);
        }
        "SKEW_ASK" if !obi_gated => {
            // Skew quotes toward sell side (positive skew = cheaper asks)
            let skew = skew_magnitude as i64;
            blend_skew(engine, skew);
        }
        "SKEW_BID" | "SKEW_ASK" => {
            // OBI below threshold — treat as HOLD (decay)
            let current = engine.l1_skew_adjustment.load(Ordering::Relaxed);
            let decayed = (current as f64 * 0.95) as i64;
            engine.l1_skew_adjustment.store(decayed, Ordering::Release);
        }
        "PAUSE_TRADING" => {
            // Set freeze for 4 seconds
            let now_ms = epoch_ms();
            let current_freeze = engine.sweep_freeze_until.load(Ordering::Relaxed);
            if now_ms > current_freeze {
                engine.sweep_freeze_until.store(now_ms + 4000, Ordering::Release);
                engine.ai_freeze_ms.store(4000, Ordering::Release);
            }
        }
        _ => {
            // HOLD — gently decay skew toward zero
            let current = engine.l1_skew_adjustment.load(Ordering::Relaxed);
            let decayed = (current as f64 * 0.95) as i64; // 5% decay per inference
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
fn round3(v: f64) -> f64 { (v * 1000.0).round() / 1000.0 }
fn round4(v: f64) -> f64 { (v * 10000.0).round() / 10000.0 }
fn round6(v: f64) -> f64 { (v * 1000000.0).round() / 1000000.0 }

fn call_lms(user_prompt: &str) -> anyhow::Result<GpuDecision> {
    let body = serde_json::json!({
        "model": LMS_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
        "max_tokens": MAX_TOKENS,
    });

    let json_body = serde_json::to_string(&body)?;

    let agent = ureq::Agent::config_builder()
        .timeout_global(Some(std::time::Duration::from_millis(5000)))
        .build()
        .new_agent();

    let resp_body: String = agent.post(LMS_URL)
        .header("Content-Type", "application/json")
        .send(&json_body)?
        .body_mut()
        .read_to_string()?;

    let resp: serde_json::Value = serde_json::from_str(&resp_body)?;

    let content = resp["choices"][0]["message"]["content"]
        .as_str()
        .unwrap_or("{}");

    // Sanitizer: find first { and last }, strip everything else
    parse_gpu_json(content)
}

fn parse_gpu_json(raw: &str) -> anyhow::Result<GpuDecision> {
    if let Ok(d) = serde_json::from_str::<GpuDecision>(raw.trim()) {
        return Ok(d);
    }

    // Sanitizer for models that wrap JSON in markdown or prose
    if let Some(start) = raw.find('{') {
        if let Some(end) = raw.rfind('}') {
            if let Ok(d) = serde_json::from_str::<GpuDecision>(&raw[start..=end]) {
                return Ok(d);
            }
        }
    }

    anyhow::bail!("Cannot parse GPU response: {}", &raw[..raw.len().min(100)])
}
