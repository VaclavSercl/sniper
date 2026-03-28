// ═══════════════════════════════════════════════════════════
// 🤖 SOVEREIGN CORTEX — GPU Inference Module (LM Studio)
// Non-blocking L1 tactical AI via local Phi-3.5 Mini
//
// Architecture:
//   L1 loop (50ms) → pushes snapshots to channel
//   GPU thread → pops from channel, infers, writes result to mmap
//   L1 NEVER waits for GPU — fire-and-forget
// ═══════════════════════════════════════════════════════════

use sniper_types::{EngineState, PRICE_SCALE};
use std::sync::atomic::Ordering;
use std::sync::mpsc;
use std::time::{Duration, Instant};

const LMS_URL: &str = "http://localhost:1234/v1/chat/completions";
const LMS_MODEL: &str = "phi-3.5-mini-instruct";
const INFERENCE_INTERVAL_MS: u64 = 2000; // Max 1 inference every 2s (GPU throttle)
const MAX_TOKENS: u32 = 60;

/// Snapshot sent from L1 to GPU thread.
#[derive(Clone)]
pub struct L1GpuRequest {
    pub price: f64,
    pub obi: f64,
    pub bid_depth: f64,
    pub ask_depth: f64,
    pub toxic_hits: u64,
    pub confidence: f64,
    pub net_position: f64,
    pub spread: f64,
    pub regime: &'static str,
}

/// Response from GPU inference.
#[derive(Debug, serde::Deserialize, Default)]
struct GpuDecision {
    bias: Option<f64>,
    risk: Option<String>,
}

/// Create L1→GPU channel. Returns sender for L1 and spawns the GPU consumer thread.
pub fn spawn_gpu_thread(engine: &'static EngineState) -> mpsc::SyncSender<L1GpuRequest> {
    // Bounded channel (capacity 1) — L1 never blocks, latest wins
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
    let mut last_inference = Instant::now() - Duration::from_secs(10); // Allow immediate first

    loop {
        // Block waiting for next L1 snapshot request
        let req = match rx.recv() {
            Ok(r) => r,
            Err(_) => break, // Channel closed
        };

        // Throttle: skip if too soon
        if last_inference.elapsed().as_millis() < INFERENCE_INTERVAL_MS as u128 {
            continue;
        }

        // Drain channel — keep only latest
        let mut latest = req;
        while let Ok(newer) = rx.try_recv() {
            latest = newer;
        }

        // Build prompt
        let prompt = format!(
            "BTC ${:.0} spread=${:.2} OBI={:+.3} bid_depth={:.3}BTC ask_depth={:.3}BTC \
             toxic={} conf={:.0}% pos={:.6}BTC regime={}. \
             Respond ONLY JSON: {{\"bias\": float(-1..1), \"risk\": \"low|med|high\"}}",
            latest.price, latest.spread, latest.obi,
            latest.bid_depth, latest.ask_depth,
            latest.toxic_hits, latest.confidence * 100.0,
            latest.net_position, latest.regime,
        );

        match call_lms(&prompt) {
            Ok(decision) => {
                // Write AI bias to mmap
                if let Some(bias) = decision.bias {
                    let clamped = bias.clamp(-1.0, 1.0);
                    // Scale bias to skew adjustment: ±$3 max
                    let skew_boost = clamped * 3.0 * PRICE_SCALE;
                    // Combine with existing L1 OBI skew (additive GPU bias)
                    let current_skew = engine.l1_skew_adjustment.load(Ordering::Relaxed) as f64;
                    let gpu_weight = 0.3; // 30% GPU, 70% pure OBI
                    let blended = current_skew * (1.0 - gpu_weight) + skew_boost * gpu_weight;
                    engine.l1_skew_adjustment.store(blended as i64, Ordering::Release);
                }

                // Update AI heartbeat
                let now_ms = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_millis() as u64;
                engine.ai_heartbeat_ms.store(now_ms, Ordering::Release);

                last_inference = Instant::now();
            }
            Err(e) => {
                eprintln!("  ⚠️ [GPU] Inference failed: {e}");
                // Don't update last_inference — retry sooner
            }
        }
    }
}

fn call_lms(prompt: &str) -> anyhow::Result<GpuDecision> {
    let body = serde_json::json!({
        "model": LMS_MODEL,
        "messages": [{"role": "user", "content": prompt}],
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

    // Extract JSON from possible markdown wrapping
    parse_gpu_json(content)
}

fn parse_gpu_json(raw: &str) -> anyhow::Result<GpuDecision> {
    // Try direct parse
    if let Ok(d) = serde_json::from_str::<GpuDecision>(raw.trim()) {
        return Ok(d);
    }

    // Extract from ```json ... ``` block
    if let Some(start) = raw.find('{') {
        if let Some(end) = raw.rfind('}') {
            if let Ok(d) = serde_json::from_str::<GpuDecision>(&raw[start..=end]) {
                return Ok(d);
            }
        }
    }

    anyhow::bail!("Cannot parse GPU response: {}", &raw[..raw.len().min(100)])
}
