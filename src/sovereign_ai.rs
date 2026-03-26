use std::time::Duration;
use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use memmap2::MmapMut;
use anyhow::Result;
use serde_json::json;

use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE};

// ── AI INFERENCE (LM Studio v0.4.7 on localhost:1234) ──

async fn fetch_ai_bias(client: &reqwest::Client, obi: f64, spread: f64, vol: f64, pos: f64) -> Result<i64> {
    // Ultra-compact prompt for minimal tokenization overhead
    let prompt = format!("OBI:{:+.2},SPR:{:.1},G:{:.1},P:{:+.4}", obi, spread, vol, pos);

    let res = client.post("http://localhost:1234/v1/chat/completions")
        .json(&json!({
            "model": "phi-3.5-mini-instruct",
            "messages": [
                {"role": "system", "content": "You are a quant signal. Output only a single integer from -200 to 200."},
                {"role": "user", "content": prompt}
            ],
            // Greedy Sampling — fastest possible inference (2026 best practice)
            "temperature": 0.0,       // Greedy decoding, no randomness
            "top_p": 0.1,             // Nucleus sampling — top candidates only
            "max_tokens": 5,          // "-200" = 4 tokens max
            "presence_penalty": 0.0,  // No penalty overhead
            "frequency_penalty": 0.0,
            "stream": false           // Full response, no chunking
        }))
        .send()
        .await?;

    let json: serde_json::Value = res.json().await?;
    let content = json["choices"][0]["message"]["content"].as_str().unwrap_or("0");
    let cleaned: String = content.trim().chars().filter(|c| c.is_ascii_digit() || *c == '-').collect();
    Ok(cleaned.parse::<i64>().unwrap_or(0))
}

fn init_mmap<T: Default>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

#[tokio::main]
async fn main() -> Result<()> {
    let e_mmap = init_mmap::<EngineState>(&ENGINE_STATE_PATH)?;
    let r_mmap = init_mmap::<RiskState>(&RISK_STATE_PATH)?;

    let engine = unsafe { &*(e_mmap.as_ptr() as *const EngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const RiskState) };

    let client = reqwest::Client::builder()
        .timeout(Duration::from_millis(300)) // Greedy inference: ~60ms warm, ~200ms cold
        .build()?;

    println!("🐺 BEROUN SOVEREIGN AI v8.0 — LM Studio Sidecar");
    println!("   Model: phi-3.5-mini-instruct (localhost:1234)");
    println!("   Cycle: 5s (normal) / 10s (thermal) | GPU: GTX 1060 6GB");
    println!("─────────────────────────────────────────────────");

    let mut consecutive_errors: u32 = 0;
    let mut last_bias: i64 = 0;
    let mut thermal_throttle = false;

    loop {
        // GPU Thermal Guard: read temperature via nvidia-smi
        let gpu_temp = std::process::Command::new("nvidia-smi")
            .args(["--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"])
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .and_then(|s| s.trim().parse::<u32>().ok())
            .unwrap_or(0);

        if gpu_temp >= 82 && !thermal_throttle {
            thermal_throttle = true;
            println!("🌡️ THERMAL THROTTLE ON: {}°C ≥ 82°C → cycle 10s", gpu_temp);
        } else if gpu_temp < 75 && thermal_throttle {
            thermal_throttle = false;
            println!("❄️ THERMAL NORMAL: {}°C < 75°C → cycle 5s", gpu_temp);
        }

        // Adaptive sleep: 5s normal, 10s thermal, extend on errors
        let sleep_ms = match (thermal_throttle, consecutive_errors > 3) {
            (_, true) => 15_000,   // Error backoff
            (true, _) => 10_000,   // Thermal throttle
            _ => 5_000,            // Normal
        };
        tokio::time::sleep(Duration::from_millis(sleep_ms)).await;

        if risk.paused.load(Ordering::Acquire) != 0 { continue; }

        // Read market state from mmap
        let scale = PRICE_SCALE;
        let bb = engine.best_bid.load(Ordering::Acquire) as f64 / scale;
        let ba = engine.best_ask.load(Ordering::Acquire) as f64 / scale;
        if bb == 0.0 || ba == 0.0 { continue; }

        let obi = engine.l2_imbalance.load(Ordering::Acquire) as f64 / scale;
        let grid = risk.grid_step.load(Ordering::Acquire) as f64 / scale;
        let pos = engine.net_position.load(Ordering::Acquire) as f64 / scale;
        let spread = ba - bb;

        // AI inference (GPU, ~50-200ms)
        match fetch_ai_bias(&client, obi, spread, grid, pos).await {
            Ok(raw_bias) => {
                consecutive_errors = 0;

                // Safety clamp in USD: max ±2× grid (grid is already in USD)
                let max_bias_usd = (grid * 2.0) as i64;
                let clamped_usd = raw_bias.clamp(-max_bias_usd, max_bias_usd);

                // Scale to PRICE_SCALE for mmap (single scale, not double)
                let bias_scaled = (clamped_usd as f64 * scale) as i64;

                // Only update if bias changed
                if clamped_usd != last_bias {
                    risk.bias_offset.store(bias_scaled, Ordering::SeqCst);
                    engine.current_ai_bias.store(bias_scaled, Ordering::SeqCst);
                    last_bias = clamped_usd;
                }

                // Heartbeat: write epoch millis so Sniper knows AI is alive
                let now_ms = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH).unwrap_or_default()
                    .as_millis() as u64;
                engine.ai_heartbeat_ms.store(now_ms, Ordering::SeqCst);

                println!("AI │ OBI={:+.3} │ Spread=${:.2} │ Pos={:+.5} │ Raw={:+} │ Bias=${:+} │ Grid=${:.2}",
                    obi, spread, pos, raw_bias, clamped_usd, grid);
            }
            Err(e) => {
                consecutive_errors += 1;
                eprintln!("⚠️  AI error (#{consecutive_errors}): {e}");

                // After 5 consecutive errors, zero out bias for safety
                if consecutive_errors >= 5 {
                    risk.bias_offset.store(0, Ordering::SeqCst);
                    engine.current_ai_bias.store(0, Ordering::SeqCst);
                    last_bias = 0;
                    eprintln!("🛑 AI bias zeroed (safety fallback)");
                }
            }
        }
    }
}
