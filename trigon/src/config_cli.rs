// 🔺 Trigon Config CLI — Live parameter tuning via mmap
// Sniper Armada · Bot #4

use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use clap::{Parser, Subcommand};
use memmap2::MmapMut;
use anyhow::Result;

use sniper_types::trigon_types::*;
use sniper_types::moonshot_types::{str_to_symbol_hash, symbol_hash_to_str};
use sniper_types::PRICE_SCALE_I;

fn open_risk() -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(TRIGON_RISK_PATH)?;
    file.set_len(std::mem::size_of::<TrigonRiskState>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

fn open_engine() -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(TRIGON_ENGINE_PATH)?;
    file.set_len(std::mem::size_of::<TrigonEngineState>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

#[derive(Parser)]
#[command(name = "trigon-config", version = "1.0.0")]
#[command(about = "🔺 Trigon Config — Live mmap parameter tuning")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Show current state
    Status,
    /// Pause/unpause trading
    Pause { #[arg(long)] off: bool },
    /// Configure a triangle
    Triangle {
        #[arg(long)]
        idx: usize,
        #[arg(long)]
        leg0: Option<String>,
        #[arg(long)]
        leg1: Option<String>,
        #[arg(long)]
        leg2: Option<String>,
        #[arg(long)]
        min_profit_bps: Option<f64>,
        #[arg(long)]
        max_order_usd: Option<f64>,
        #[arg(long)]
        enable: bool,
        #[arg(long)]
        disable: bool,
    },
    /// Set fee rate
    Fee {
        #[arg(long)]
        bps: f64,
    },
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let risk_mmap = open_risk()?;
    let engine_mmap = open_engine()?;
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const TrigonRiskState) };
    let engine = unsafe { &*(engine_mmap.as_ptr() as *const TrigonEngineState) };

    match cli.command {
        Commands::Status => {
            let paused = risk.global_paused.load(Ordering::Acquire);
            let fee = risk.fee_bps.load(Ordering::Acquire);
            println!("🔺 Trigon Status");
            println!("  Paused: {}", if paused != 0 { "YES ⏸️" } else { "NO ▶️" });
            println!("  Fee: {} bps", fee);
            println!("  Total arbs: {}", engine.total_arbs.load(Ordering::Acquire));
            println!("  Total PnL: ${:.6}", engine.total_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64);
            println!("  Best profit: {:.2} bps", engine.best_profit_bps.load(Ordering::Acquire) as f64 / 100.0);
            println!();

            for t in 0..TRIGON_MAX_TRIANGLES {
                let tr = &risk.triangles[t];
                let et = &engine.triangles[t];
                let enabled = tr.enabled.load(Ordering::Acquire);
                let has_symbols = (0..3).any(|l| tr.leg_symbols[l].load(Ordering::Acquire) != 0);
                if !has_symbols { continue; }

                let syms: Vec<String> = (0..3).map(|l|
                    symbol_hash_to_str(tr.leg_symbols[l].load(Ordering::Acquire))
                ).collect();

                let profit = et.profit_bps.load(Ordering::Acquire) as f64 / 100.0;
                let rate = et.implied_rate.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;

                println!("  [{}] {} → {} → {} {}", t,
                    syms[0], syms[1], syms[2],
                    if enabled != 0 { "✅" } else { "⏸️" });
                println!("      rate={:.8} profit={:.2}bps execs={}", rate, profit,
                    et.executions.load(Ordering::Acquire));
            }
        }

        Commands::Pause { off } => {
            risk.global_paused.store(if off { 0 } else { 1 }, Ordering::Release);
            println!("🔺 Trigon: {}", if off { "UNPAUSED ▶️" } else { "PAUSED ⏸️" });
        }

        Commands::Triangle { idx, leg0, leg1, leg2, min_profit_bps, max_order_usd, enable, disable } => {
            if idx >= TRIGON_MAX_TRIANGLES {
                eprintln!("❌ Index must be < {}", TRIGON_MAX_TRIANGLES);
                return Ok(());
            }
            let tr = &risk.triangles[idx];

            if let Some(s) = leg0 { tr.leg_symbols[0].store(str_to_symbol_hash(&s), Ordering::Release); }
            if let Some(s) = leg1 { tr.leg_symbols[1].store(str_to_symbol_hash(&s), Ordering::Release); }
            if let Some(s) = leg2 { tr.leg_symbols[2].store(str_to_symbol_hash(&s), Ordering::Release); }
            if let Some(v) = min_profit_bps { tr.min_profit_bps.store((v * 100.0) as i64, Ordering::Release); }
            if let Some(v) = max_order_usd { tr.max_order_usd.store((v * PRICE_SCALE_I as f64) as u64, Ordering::Release); }
            if enable { tr.enabled.store(1, Ordering::Release); }
            if disable { tr.enabled.store(0, Ordering::Release); }

            println!("🔺 Triangle [{}] updated", idx);
        }

        Commands::Fee { bps } => {
            risk.fee_bps.store(bps as u64, Ordering::Release);
            println!("🔺 Fee set to {} bps", bps);
        }
    }

    Ok(())
}
