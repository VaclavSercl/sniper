// ═══════════════════════════════════════════════════════════
// 🧠 SOVEREIGN CORTEX v13.0 — Entry Point
//
// The unified AI brain for the entire Sniper Armada.
// Phase 1: Memory + L2 Strategic Oracle (Gemini CLI)
// Phase 2: L1 Tactical Shield (OBI, sweep, ghost)
// Phase 3: Macro Intelligence (Binance WS, F&G, News)
//
// Usage:
//   sovereign-cortex              # Full mode
//   sovereign-cortex --dry-run    # Read-only (no Gemini, no writes)
//   sovereign-cortex --no-macro   # Skip macro intelligence module
// ═══════════════════════════════════════════════════════════

mod memory;
mod l1;
mod l2;
mod macro_intel;
mod telegram;

use memory::ArmadaMemory;
use std::sync::Arc;
use tokio::sync::RwLock;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let dry_run = args.iter().any(|a| a == "--dry-run");
    let no_macro = args.iter().any(|a| a == "--no-macro");

    // Load .env from project root (sniper/)
    load_dotenv();

    println!("🧠 ══════════════════════════════════════════");
    println!("🧠  SOVEREIGN CORTEX v13.0");
    println!("🧠  L1 Tactical + L2 Strategic + Macro Intel");
    println!("🧠 ══════════════════════════════════════════");
    if dry_run {
        println!("⚠️  DRY-RUN MODE — no Gemini calls, no writes");
    }
    if no_macro {
        println!("⚠️  MACRO DISABLED — no Binance WS, F&G, RSS");
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

    // 3. Launch L1 Tactical Shield (dedicated OS thread — 50ms cycle)
    {
        let mem = memory.read().await;
        let engine_ptr = mem.hydra_engine() as *const sniper_types::EngineState;
        // SAFETY: EngineState uses only Atomics — safe to share across threads.
        let engine_ref: &'static sniper_types::EngineState = unsafe { &*engine_ptr };
        std::thread::Builder::new()
            .name("l1-hydra".into())
            .spawn(move || {
                l1::run_l1_hydra(engine_ref);
            })?;
    }
    println!("🛡️ L1 Tactical Shield: ONLINE (50ms cycle)");

    // 4. Launch Macro Intelligence (3 dedicated OS threads)
    if !no_macro {
        let mem = memory.read().await;
        let engine_ptr = mem.hydra_engine() as *const sniper_types::EngineState;
        let engine_ref: &'static sniper_types::EngineState = unsafe { &*engine_ptr };

        // Binance WebSocket
        let engine_binance = engine_ref;
        std::thread::Builder::new()
            .name("macro-binance".into())
            .spawn(move || {
                macro_intel::run_binance_ws(engine_binance);
            })?;

        // Fear & Greed
        let engine_fg = engine_ref;
        std::thread::Builder::new()
            .name("macro-fg".into())
            .spawn(move || {
                macro_intel::run_fear_greed(engine_fg);
            })?;

        // News RSS Sentiment
        let engine_rss = engine_ref;
        std::thread::Builder::new()
            .name("macro-rss".into())
            .spawn(move || {
                macro_intel::run_news_sentiment(engine_rss);
            })?;

        println!("🌍 Macro Intelligence: ONLINE (Binance WS + F&G + RSS)");
    }

    // 5. Launch L2 Strategic Oracle (tokio async task)
    let l2_memory = Arc::clone(&memory);
    let l2_handle = tokio::spawn(async move {
        l2::run_l2_loop(l2_memory, dry_run).await;
    });

    println!("🌐 L2 Strategic Oracle: ONLINE (5 min cycle)");
    println!();
    println!("✅ SOVEREIGN CORTEX FULLY OPERATIONAL");
    println!("   Press Ctrl+C to stop.\n");

    // Wait for tasks
    let _ = tokio::join!(l2_handle);

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
