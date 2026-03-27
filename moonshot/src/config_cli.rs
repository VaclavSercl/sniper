// 🌙 Moonshot Config CLI — Live Parameter Modifier via mmap
// Sniper Armada · Bot #2
//
// Usage:
//   moonshot-config export-json     — dump current mmap state as JSON
//   moonshot-config get <field>     — read specific field
//   moonshot-config set <field> <v> — write to mmap (live, no restart needed)

use std::fs::OpenOptions;
use std::sync::atomic::Ordering;

use memmap2::MmapMut;
use clap::{Parser, Subcommand};
use serde_json::json;
use anyhow::Result;

use sniper_types::moonshot_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};

#[derive(Parser)]
#[command(name = "moonshot-config")]
#[command(about = "🌙 Moonshot live config via mmap")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Export full state as JSON
    ExportJson,
    /// Get a specific field
    Get { field: String },
    /// Set a specific field
    Set { field: String, value: String },
}

fn init_mmap_rw<T>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let e_mmap = init_mmap_rw::<MoonshotEngineState>(MOONSHOT_ENGINE_PATH)?;
    let r_mmap = init_mmap_rw::<MoonshotRiskState>(MOONSHOT_RISK_PATH)?;
    let engine = unsafe { &*(e_mmap.as_ptr() as *const MoonshotEngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const MoonshotRiskState) };

    match cli.command {
        Commands::ExportJson => {
            let paused = risk.global_paused.load(Ordering::Acquire);
            let daily_pnl = engine.daily_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;

            let mut pairs = Vec::new();
            for i in 0..MOONSHOT_MAX_PAIRS {
                let sym_hash = risk.pairs[i].symbol_hash.load(Ordering::Acquire);
                if sym_hash != 0 {
                    let symbol = symbol_hash_to_str(sym_hash);
                    pairs.push(json!({
                        "idx": i,
                        "symbol": symbol,
                        "drop_pct": risk.pairs[i].m_shot_price_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE,
                        "tp_pct": risk.pairs[i].tp_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE,
                        "sl_pct": risk.pairs[i].sl_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE,
                        "order_usd": risk.pairs[i].order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE,
                        "position": engine.pairs[i].net_position.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                        "pnl": engine.pairs[i].realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                        "fills": engine.pairs[i].fill_count.load(Ordering::Acquire),
                        "active": engine.pairs[i].active.load(Ordering::Acquire),
                    }));
                }
            }

            let state = json!({
                "bot": "moonshot",
                "version": "1.0.0",
                "global_paused": paused != 0,
                "daily_pnl": daily_pnl,
                "total_fills": engine.total_fills.load(Ordering::Acquire),
                "pairs": pairs,
            });

            println!("{}", serde_json::to_string_pretty(&state)?);
        }
        Commands::Get { field } => {
            match field.as_str() {
                "paused" => println!("{}", risk.global_paused.load(Ordering::Acquire)),
                "daily_pnl" => println!("{:.2}", engine.daily_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64),
                "total_fills" => println!("{}", engine.total_fills.load(Ordering::Acquire)),
                _ => eprintln!("Unknown field: {}", field),
            }
        }
        Commands::Set { field, value } => {
            match field.as_str() {
                "paused" => {
                    let v: u64 = value.parse()?;
                    risk.global_paused.store(v, Ordering::Release);
                    println!("✅ global_paused = {}", v);
                }
                _ => eprintln!("Unknown or read-only field: {}", field),
            }
        }
    }
    Ok(())
}
