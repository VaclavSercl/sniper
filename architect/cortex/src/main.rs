// ═══════════════════════════════════════════════════════════
// 🧠 SOVEREIGN CORTEX v14.0 — Entry Point
//
// Right Hemisphere (Data Plane):
//   L1 Tactical Shield (50ms)
//   GPU Inference (Phi-3.5, 2s cycle)
//   Macro Intelligence (Binance WS, F&G, RSS)
//   Sentinel (5-layer guardian)
//   UDS Server (/tmp/cortex.sock)
//
// Left Hemisphere (Python Commander):
//   L2 Strategic Oracle (Gemini CLI, 5min)
//   Telegram I/O
//   Natural Language Commands
//   Scheduled Reports
//
// Usage:
//   sovereign-cortex              # Full mode
//   sovereign-cortex --no-macro   # Skip macro intelligence
//   sovereign-cortex --no-gpu     # Skip GPU inference
// ═══════════════════════════════════════════════════════════

mod memory;
mod gpu;
mod l1;
mod macro_intel;
mod sentinel;
mod uds;

use memory::ArmadaMemory;
use std::sync::Arc;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let no_macro = args.iter().any(|a| a == "--no-macro");
    let no_gpu = args.iter().any(|a| a == "--no-gpu");

    // Load .env from project root (sniper/)
    load_dotenv();
    
    rustls::crypto::ring::default_provider().install_default().ok();

    println!("🧠 ══════════════════════════════════════════");
    println!("🧠  SOVEREIGN CORTEX v14.0");
    println!("🧠  Right Hemisphere — Data Plane");
    println!("🧠  L1 Tactical + Macro + Sentinel + UDS");
    println!("🧠 ══════════════════════════════════════════");
    if no_macro {
        println!("⚠️  MACRO DISABLED — no Binance WS, F&G, RSS");
    }
    println!();

    // 1. Initialize shared memory (mmap)
    let memory = Arc::new(ArmadaMemory::new()?);

    // 2. Print initial snapshot
    {
        let mem = &*memory;
        let snap = mem.snapshot_hydra();
        println!("📸 Initial Hydra snapshot:");
        println!("  Price:    ${:.2}", snap.micro_price);
        println!("  Bid/Ask:  ${:.2} / ${:.2}", snap.best_bid, snap.best_ask);
        println!("  Position: {:.6} BTC", snap.net_position);
        println!("  PnL:      ${:.4}", snap.realized_pnl);
        println!("  Grid:     ${:.2} ({} levels)", snap.grid_step, snap.grid_levels);
        println!("  Fills:    {} | Toxic: {}", snap.session_fills, snap.toxic_hits);
        println!("  F&G:      {} | Bias: {:+.4}", snap.fear_greed, snap.macro_bias);
        println!();
    }

    // 3. Launch GPU inference (spawns thread which natively handles retries)
    let gpu_tx = if !no_gpu {
        let mem = &*memory;
        let engine_ptr = mem.hydra_engine() as *const sniper_types::EngineState;
        let engine_ref: &'static sniper_types::EngineState = unsafe { &*engine_ptr };

        let tx = gpu::spawn_gpu_thread(engine_ref);
        println!("🤖 GPU Inference: INITIALIZED (Phi-3.5 Mini @ :1234)");
        Some(tx)
    } else {
        println!("⚠️  GPU DISABLED by --no-gpu");
        None
    };

    // 4. Launch L1 Tactical Shield (dedicated OS thread — 50ms cycle)
    {
        let mem = &*memory;
        let engine_ptr = mem.hydra_engine() as *const sniper_types::EngineState;
        let engine_ref: &'static sniper_types::EngineState = unsafe { &*engine_ptr };
        std::thread::Builder::new()
            .name("l1-hydra".into())
            .spawn(move || {
                l1::run_l1_hydra(engine_ref, gpu_tx);
            })?;
    }
    println!("🛡️ L1 Tactical Shield: ONLINE (50ms cycle)");

    // 5. Launch Macro Intelligence (3 dedicated OS threads)
    if !no_macro {
        let mem = &*memory;
        let engine_ptr = mem.hydra_engine() as *const sniper_types::EngineState;
        let engine_ref: &'static sniper_types::EngineState = unsafe { &*engine_ptr };

        tokio::spawn(async move { macro_intel::run_binance_ws(engine_ref).await; });

        let engine_fg = engine_ref;
        tokio::spawn(async move { macro_intel::run_fear_greed(engine_fg).await; });

        let engine_rss = engine_ref;
        tokio::spawn(async move { macro_intel::run_news_sentiment(engine_rss).await; });

        println!("🌍 Macro Intelligence: ONLINE (Binance WS + F&G + RSS)");
    }

    // 6. Launch Sentinel Guardian (tokio async task)
    let sentinel_memory = Arc::clone(&memory);
    let sentinel_handle = tokio::spawn(async move {
        sentinel::run_sentinel(sentinel_memory).await;
    });

    // 7. Launch UDS Server (hemisphere bridge)
    let uds_memory = Arc::clone(&memory);
    let uds_handle = tokio::spawn(async move {
        uds::run_uds_server(uds_memory).await;
    });

    // 8. Send boot alert
    sentinel::send_boot_alert().await;

    println!("🛡️ Sentinel: ONLINE (5-layer guardian)");
    println!("🔌 UDS Server: ONLINE (/tmp/cortex.sock)");
    println!();
    println!("✅ SOVEREIGN CORTEX v14.0 FULLY OPERATIONAL");
    println!("   L2 Oracle: Delegated to Python Commander (UDS)");
    println!("   Press Ctrl+C to stop.\n");

    // Wait for tasks
    let _ = tokio::join!(sentinel_handle, uds_handle);

    Ok(())
}

/// Load environment variables from the project root .env file.
fn load_dotenv() {
    let env_paths = [
        std::path::PathBuf::from("/home/wwwenda/sniper/.env"),
        std::path::PathBuf::from(".env"),
    ];

    for path in &env_paths {
        if let Ok(content) = std::fs::read_to_string(path) {
            let mut loaded = 0;
            for line in content.lines() {
                let line = line.trim();
                if line.is_empty() || line.starts_with('#') {
                    continue;
                }
                if let Some((key, value)) = line.split_once('=') {
                    let key = key.trim();
                    let value = value.trim();
                    if std::env::var(key).is_err() {
                        unsafe { std::env::set_var(key, value); }
                        loaded += 1;
                    }
                }
            }
            println!("📄 Loaded {loaded} env vars from {}", path.display());
            return;
        }
    }
    eprintln!("⚠️  No .env file found");
}
