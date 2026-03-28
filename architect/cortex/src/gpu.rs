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
You are a High-Frequency Trading (HFT) tactical micro-controller. \
Your ONLY goal is to analyze Order Book Imbalance (OBI) and short-term volatility to output a single tactical action. \
CRITICAL RULES: \
1. Output EXACTLY AND ONLY valid JSON. \
2. No pleasantries, no markdown formatting, no backticks, no explanations outside the JSON. \
3. Valid actions: HOLD, SKEW_BID, SKEW_ASK, PAUSE_TRADING. \
4. confidence_pct: 0-100 integer. \
5. reason: max 8 words.";

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
}

/// Response from GPU inference — discrete action + confidence.
#[derive(Debug, serde::Deserialize, Default)]
struct GpuDecision {
    action: Option<String>,       // HOLD | SKEW_BID | SKEW_ASK | PAUSE_TRADING
    confidence_pct: Option<u32>,  // 0-100
    reason: Option<String>,       // Short explanation
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
    let mut inference_count: u64 = 0;

    loop {
        let req = match rx.recv() {
            Ok(r) => r,
            Err(_) => break,
        };

        if last_inference.elapsed().as_millis() < INFERENCE_INTERVAL_MS as u128 {
            continue;
        }

        // Drain channel — keep only latest
        let mut latest = req;
        while let Ok(newer) = rx.try_recv() {
            latest = newer;
        }

        // Build structured JSON input (small models handle JSON input better than prose)
        let user_prompt = serde_json::json!({
            "market_data": {
                "symbol": "BTC-USD",
                "best_bid": round2(latest.best_bid),
                "best_ask": round2(latest.best_ask),
                "spread": round2(latest.best_ask - latest.best_bid),
                "obi_current": round3(latest.obi),
                "obi_prev": [round3(latest.obi_prev[0]), round3(latest.obi_prev[1])],
                "bid_depth_btc": round3(latest.bid_depth),
                "ask_depth_btc": round3(latest.ask_depth),
                "depth_trend": latest.depth_trend,
                "recent_sweeps_5min": latest.sweeps_recent,
            },
            "bot_state": {
                "position_btc": round6(latest.net_position),
                "toxic_fills": latest.toxic_hits,
                "confidence_pct": (latest.confidence * 100.0).round() as u32,
                "regime": latest.regime,
            },
            "macro": {
                "fear_greed": latest.fear_greed,
                "sentiment_bias": round4(latest.macro_bias),
            },
            "respond_with_format": {
                "action": "HOLD|SKEW_BID|SKEW_ASK|PAUSE_TRADING",
                "confidence_pct": "0-100",
                "reason": "max 8 words"
            }
        });

        let prompt_str = serde_json::to_string(&user_prompt).unwrap_or_default();

        match call_lms(&prompt_str) {
            Ok(decision) => {
                apply_gpu_decision(&decision, engine);

                inference_count += 1;
                last_inference = Instant::now();

                // Log every 30th inference (~1 min)
                if inference_count % 30 == 1 {
                    println!("  🤖 [GPU] #{inference_count}: {} ({}%) — {}",
                        decision.action.as_deref().unwrap_or("?"),
                        decision.confidence_pct.unwrap_or(0),
                        decision.reason.as_deref().unwrap_or(""));
                }

                engine.ai_heartbeat_ms.store(epoch_ms(), Ordering::Release);
            }
            Err(e) => {
                eprintln!("  ⚠️ [GPU] Inference failed: {e}");
            }
        }
    }
}

/// Translate discrete GPU action into mmap writes.
fn apply_gpu_decision(decision: &GpuDecision, engine: &EngineState) {
    let confidence = decision.confidence_pct.unwrap_or(0);
    let action = decision.action.as_deref().unwrap_or("HOLD");

    // Scale confidence → skew magnitude (0-100% → $0-$3)
    let skew_magnitude = (confidence as f64 / 100.0) * 3.0 * PRICE_SCALE;

    match action {
        "SKEW_BID" => {
            // Skew quotes toward buy side (negative skew = cheaper bids)
            let skew = -(skew_magnitude as i64);
            blend_skew(engine, skew);
        }
        "SKEW_ASK" => {
            // Skew quotes toward sell side (positive skew = cheaper asks)
            let skew = skew_magnitude as i64;
            blend_skew(engine, skew);
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

    let resp_body: String = ureq::post(LMS_URL)
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
