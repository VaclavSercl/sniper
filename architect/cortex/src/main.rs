// ═══════════════════════════════════════════════════════════
// 🧠 SOVEREIGN CORTEX v13.0 — Entry Point
//
// The unified AI brain for the entire Sniper Armada.
// Phase 1: Memory + L2 Strategic Oracle (Gemini CLI)
//
// Usage:
//   sovereign-cortex              # Full mode (reads mmap + calls Gemini)
//   sovereign-cortex --dry-run    # Read-only (prints snapshots, no Gemini)
// ═══════════════════════════════════════════════════════════

mod memory;
mod l2;
mod telegram;

use memory::ArmadaMemory;
use std::sync::Arc;
use tokio::sync::RwLock;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let dry_run = args.iter().any(|a| a == "--dry-run");

    // Load .env from project root (sniper/)
    load_dotenv();

    println!("🧠 ══════════════════════════════════════════");
    println!("🧠  SOVEREIGN CORTEX v13.0 — Phase 1");
    println!("🧠  Memory + L2 Strategic Oracle");
    println!("🧠 ══════════════════════════════════════════");
    if dry_run {
        println!("⚠️  DRY-RUN MODE — no Gemini calls, no Telegram");
    }
    println!();

    // 1. Initialize shared memory (mmap)
    let memory = Arc::new(RwLock::new(ArmadaMemory::new()?));

    // 2. Print initial snapshot
    {
        let mem = memory.read().await;
        let snap = mem.snapshot_hydra();
        println!("📸 Initial Hydra snapshot:");
        println!("  Price:    ${:.2}", snap.micro_price);
        println!("  Bid/Ask:  ${:.2} / ${:.2}", snap.best_bid, snap.best_ask);
        println!("  Position: {:.6} BTC", snap.net_position);
        println!("  PnL:      ${:.4}", snap.realized_pnl);
        println!("  Grid:     ${:.2} ({} levels)", snap.grid_step, snap.grid_levels);
        println!("  Fills:    {} | Toxic: {}", snap.session_fills, snap.toxic_hits);
        println!("  Regime:   {} | Intent: {}", snap.l2_regime, snap.ai_intent);
        println!("  F&G:      {} | Bias: {:+.4}", snap.fear_greed, snap.macro_bias);
        println!("  Shadow:   {} | Paused: {}", snap.shadow_mode, snap.paused);
        println!();
    }

    // 3. Launch L2 Strategic Oracle
    let l2_memory = Arc::clone(&memory);
    let l2_handle = tokio::spawn(async move {
        l2::run_l2_loop(l2_memory, dry_run).await;
    });

    println!("✅ Cortex online. L2 Oracle running (5 min cycle).");
    println!("   Press Ctrl+C to stop.\n");

    // Wait for tasks
    let _ = tokio::join!(l2_handle);

    Ok(())
}

/// Load environment variables from the project root .env file.
fn load_dotenv() {
    // Walk up from executable to find sniper/.env
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
                        // SAFETY: Called before any threads are spawned
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
