use std::time::Duration;
use std::fs::{OpenOptions, self};
use std::sync::atomic::Ordering;
use std::process::Command;
use memmap2::MmapMut;
use anyhow::{Result, Context};
use serde_json::{Value, json};

use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE_I};

fn init_mmap_ptr<T: Default>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

async fn check_local_ai() -> Option<String> {
    let client = reqwest::Client::builder().timeout(Duration::from_secs(2)).build().ok()?;
    let res = client.get("http://localhost:11434/api/tags").send().await.ok()?;
    if res.status().is_success() {
        let json: Value = res.json().await.ok()?;
        json["models"][0]["name"].as_str().map(|s| s.to_string())
    } else {
        None
    }
}

async fn call_local_ai_filter(model: &str, price: f64, pos: f64) -> Result<String> {
    let client = reqwest::Client::new();
    let prompt = format!("You are an HFT Risk Assistant. Market Price: {}, Position: {}. Detect if there is extreme volatility or anomalous price action. Return JSON: {{\"risk_level\": \"LOW|HIGH\", \"reasoning\": \"...\"}}", price, pos);
    
    let res = client.post("http://localhost:11434/api/generate")
        .json(&json!({
            "model": model,
            "prompt": prompt,
            "stream": false,
            "format": "json"
        }))
        .send()
        .await?;

    let json: Value = res.json().await?;
    Ok(json["response"].as_str().unwrap_or("{}").to_string())
}

#[tokio::main]
async fn main() -> Result<()> {
    println!("--- BEROUN SNIPER SOVEREIGN AI (HYBRID EDITION) ---");
    let e_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;
    let r_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;

    let engine = unsafe { &*(e_mmap.as_ptr() as *const EngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const RiskState) };

    loop {
        let pos = engine.net_position.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let price = engine.best_bid.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        
        let mut local_risk_intel = String::from("No local data");
        if let Some(local_model) = check_local_ai().await {
            if let Ok(intel) = call_local_ai_filter(&local_model, price, pos).await {
                local_risk_intel = intel;
            }
        }

        let mut final_bias = 0.0;
        if pos > 0.001 { final_bias = -1.5; }
        else if pos < -0.001 { final_bias = 1.5; }

        let bias_scaled = (final_bias * PRICE_SCALE_I as f64) as i64;
        risk.bias_offset.store(bias_scaled, Ordering::Release);

        let lat = engine.latency_ns.load(Ordering::Acquire);
        let pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;

        if price > 0.0 {
            println!("SOVEREIGN AI | Price: {:.2} | Pos: {:.4} | Bias: {:.1} | Local AI: {} | PnL: {:.2}", 
                price, pos, final_bias, local_risk_intel, pnl
            );
        }

        tokio::time::sleep(Duration::from_millis(1000)).await;
    }
}
