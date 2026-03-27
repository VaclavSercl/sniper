// 📐 Grid Config CLI — Live Parameter Modifier via mmap
// Sniper Armada · Bot #3

use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use memmap2::MmapMut;
use clap::{Parser, Subcommand};
use serde_json::json;
use anyhow::Result;
use sniper_types::grid_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};

#[derive(Parser)]
#[command(name = "grid-config", about = "📐 Grid live config via mmap")]
struct Cli { #[command(subcommand)] command: Commands }

#[derive(Subcommand)]
enum Commands { ExportJson, Get { field: String }, Set { field: String, value: String } }

fn init_mmap_rw<T>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let e_mmap = init_mmap_rw::<GridEngineState>(GRID_ENGINE_PATH)?;
    let r_mmap = init_mmap_rw::<GridRiskState>(GRID_RISK_PATH)?;
    let engine = unsafe { &*(e_mmap.as_ptr() as *const GridEngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const GridRiskState) };

    match cli.command {
        Commands::ExportJson => {
            let state = json!({
                "bot": "grid", "version": "1.0.0",
                "paused": risk.global_paused.load(Ordering::Acquire) != 0,
                "mid_price": engine.mid_price.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                "spacing": risk.grid_spacing.load(Ordering::Acquire) as f64 / PRICE_SCALE,
                "buy_levels": risk.num_buy_levels.load(Ordering::Acquire),
                "sell_levels": risk.num_sell_levels.load(Ordering::Acquire),
                "order_qty": risk.order_qty.load(Ordering::Acquire) as f64 / PRICE_SCALE,
                "mode": if risk.grid_mode.load(Ordering::Acquire) == 0 { "arithmetic" } else { "geometric" },
                "pnl": engine.realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                "fills": engine.total_fills.load(Ordering::Acquire),
            });
            println!("{}", serde_json::to_string_pretty(&state)?);
        }
        Commands::Get { field } => match field.as_str() {
            "paused" => println!("{}", risk.global_paused.load(Ordering::Acquire)),
            "spacing" => println!("{:.0}", risk.grid_spacing.load(Ordering::Acquire) as f64 / PRICE_SCALE),
            "fills" => println!("{}", engine.total_fills.load(Ordering::Acquire)),
            _ => eprintln!("Unknown: {}", field),
        },
        Commands::Set { field, value } => match field.as_str() {
            "paused" => { risk.global_paused.store(value.parse()?, Ordering::Release); println!("✅ paused = {}", value); }
            "spacing" => { risk.grid_spacing.store((value.parse::<f64>()? * PRICE_SCALE) as u64, Ordering::Release); println!("✅ spacing = {}", value); }
            _ => eprintln!("Unknown/read-only: {}", field),
        },
    }
    Ok(())
}
