use std::time::Duration;
use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use memmap2::MmapMut;
use anyhow::Result;
use serde_json::json;

use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE};

// ── AI INFERENCE (LM Studio v0.4.7 on localhost:1234) ──

async fn fetch_ai_bias(client: &reqwest::Client, obi: f64, spread: f64, vol: f64, pos: f64) -> Result<i64> {
    let prompt = format!(
        "Market: OBI={:.3}, Spread={:.2}, Grid={:.2}, Pos={:.5}. Predict price direction bias in USD (-200 to 200). RETURN ONLY THE NUMBER.",
        obi, spread, vol, pos
    );

    let res = client.post("http://localhost:1234/v1/chat/completions")
        .json(&json!({
            "model": "phi-3.5-mini-instruct",
            "messages": [
                {"role": "system", "content": "You are an HFT alphagen. Output only a signed integer. No text."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "max_tokens": 8
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
        .timeout(Duration::from_millis(500))
        .build()?;

    println!("🐺 BEROUN SOVEREIGN AI v7.0 — LM Studio Sidecar");
    println!("   Model: phi-3.5-mini-instruct (localhost:1234)");
    println!("   Cycle: 5s | GPU: GTX 1060 6GB");
    println!("─────────────────────────────────────────────────");

    let mut consecutive_errors: u32 = 0;
    let mut last_bias: i64 = 0;

    loop {
        // Adaptive sleep: 5s normal, extend on errors
        let sleep_ms = if consecutive_errors > 3 { 10_000 } else { 5_000 };
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
