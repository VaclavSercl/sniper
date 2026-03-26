use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use memmap2::MmapMut;
use anyhow::{Result, Context};
use clap::{Parser, Subcommand};

use beroun_types::{RiskState, RISK_STATE_PATH, PRICE_SCALE};

#[derive(Parser)]
#[command(name = "beroun-config")]
#[command(about = "🐺 Beroun Sniper v7.0 — Live parameter modifier (mmap)")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Nastaví grid_step (v USD)
    SetGrid { value: f64 },
    /// Nastaví bias_offset (v USD)
    SetBias { value: f64 },
    /// Nastaví max_inv_delta (v BTC)
    SetMaxInv { value: f64 },
    /// Zastaví/spustí bota (true=pause, false=resume)
    Pause { state: bool },
    /// Zobrazí aktuální parametry
    Show,
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&*RISK_STATE_PATH)
        .context("Nelze otevřít risk_state.bin. Běží bot?")?;

    let mmap = unsafe { MmapMut::map_mut(&file)? };
    let risk = unsafe { &*(mmap.as_ptr() as *const RiskState) };

    match cli.command {
        Commands::SetGrid { value } => {
            let scaled = (value * PRICE_SCALE) as u64;
            risk.grid_step.store(scaled, Ordering::SeqCst);
            println!("✅ Grid Step: ${value}");
        }
        Commands::SetBias { value } => {
            let scaled = (value * PRICE_SCALE) as i64;
            risk.bias_offset.store(scaled, Ordering::SeqCst);
            println!("✅ Bias Offset: ${value}");
        }
        Commands::SetMaxInv { value } => {
            let scaled = (value * PRICE_SCALE) as u64;
            risk.max_inv_delta.store(scaled, Ordering::SeqCst);
            println!("✅ Max Inventory Delta: {value} BTC");
        }
        Commands::Pause { state } => {
            risk.paused.store(if state { 1 } else { 0 }, Ordering::SeqCst);
            println!("✅ Bot {}", if state { "⏸️  PAUSED" } else { "▶️  RESUMED" });
        }
        Commands::Show => {
            let s = PRICE_SCALE;
            println!("🐺 Beroun Sniper — RiskState Live");
            println!("─────────────────────────────────");
            println!("  Grid Step:     ${:.2}", risk.grid_step.load(Ordering::Acquire) as f64 / s);
            println!("  Bias Offset:   ${:.2}", risk.bias_offset.load(Ordering::Acquire) as f64 / s);
            println!("  Max Inv Delta: {:.6} BTC", risk.max_inv_delta.load(Ordering::Acquire) as f64 / s);
            println!("  Paused:        {}", if risk.paused.load(Ordering::Acquire) != 0 { "YES ⏸️" } else { "NO ▶️" });
        }
    }

    Ok(())
}
