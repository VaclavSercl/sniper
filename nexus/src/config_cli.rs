use clap::{Parser, Subcommand};
use anyhow::{Context, Result};
use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use memmap2::MmapMut;

use sniper_types::exchange::cross_types::{CrossExchangeState, CROSS_EXCHANGE_PATH};
use sniper_types::PRICE_SCALE;

const VERSION: &str = "1.0.0";

#[derive(Parser, Debug)]
#[command(name = "nexus-config", version = VERSION, about = "Nexus 🪐 Configuration CLI")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Ukáže aktuální konfiguraci
    Show,
    /// Nastaví nouzové zastavení (pause true/false)
    Pause { state: String },
    /// Nastaví maximální povolenou expozici na Bitfinexu (USD)
    SetExposureBfx {
        #[arg(value_name = "USD")]
        amount: f64,
    },
    /// Nastaví maximální povolenou expozici na Binance (USD)
    SetExposureBnb {
        #[arg(value_name = "USD")]
        amount: f64,
    },
    /// Nastaví denní loss limit bariéru (USD)
    SetLoss {
        #[arg(value_name = "USD")]
        amount: f64,
    },
    /// Exportuje konfiguraci jako JSON (pro AI Oracle)
    ExportJson,
}

fn open_mmap() -> Result<MmapMut> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(CROSS_EXCHANGE_PATH)
        .context(format!("Nelze otevřít mmap pro úpravu parametrů: {}", CROSS_EXCHANGE_PATH))?;
    unsafe { MmapMut::map_mut(&file).context("Mmap fail") }
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    let mut mmap = open_mmap()?;
    let state = unsafe { &mut *(mmap.as_mut_ptr() as *mut CrossExchangeState) };

    match cli.command {
        Commands::Show => {
            let paused = state.emergency_pause.load(Ordering::Acquire) != 0;
            let ext_bfx = state.max_exposure_bitfinex_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;
            let ext_bnb = state.max_exposure_binance_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;
            let loss = state.daily_loss_limit.load(Ordering::Acquire) as f64 / PRICE_SCALE;
            let daily = state.daily_cross_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE;
            
            println!("🪐 NEXUS CONFIGURATION:");
            println!("  Přestávka (Pause): {}", if paused { "ANO 🔴" } else { "NE 🟢" });
            println!("  Max Expozice BFX:  ${:.2}", ext_bfx);
            println!("  Max Expozice BNB:  ${:.2}", ext_bnb);
            println!("  Denní loss limit:  ${:.2}", loss);
            println!("  Dnešní PnL (cross):${:.2}", daily);
        }
        Commands::ExportJson => {
            let paused = state.emergency_pause.load(Ordering::Acquire) != 0;
            let ext_bfx = state.max_exposure_bitfinex_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;
            let ext_bnb = state.max_exposure_binance_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;
            let loss = state.daily_loss_limit.load(Ordering::Acquire) as f64 / PRICE_SCALE;

            let js = serde_json::json!({
                "paused": paused,
                "max_exposure_bitfinex_usd": ext_bfx,
                "max_exposure_binance_usd": ext_bnb,
                "daily_loss_limit": loss
            });
            println!("{}", js);
        }
        Commands::Pause { state: val } => {
            let b = val == "true";
            state.emergency_pause.store(if b { 1 } else { 0 }, Ordering::Release);
            println!("🪐 Nexus {}.", if b { "pozastaven" } else { "spuštěn" });
        }
        Commands::SetExposureBfx { amount } => {
            let clamped = amount.clamp(1.0, 50000.0);
            state.max_exposure_bitfinex_usd.store((clamped * PRICE_SCALE) as i64, Ordering::Release);
            println!("🪐 Max pozice na BFX omezena na ${:.2}", clamped);
        }
        Commands::SetExposureBnb { amount } => {
            let clamped = amount.clamp(1.0, 50000.0);
            state.max_exposure_binance_usd.store((clamped * PRICE_SCALE) as i64, Ordering::Release);
            println!("🪐 Max pozice na BNB omezena na ${:.2}", clamped);
        }
        Commands::SetLoss { amount } => {
            let clamped = amount.clamp(1.0, 5000.0);
            state.daily_loss_limit.store((clamped * PRICE_SCALE) as i64, Ordering::Release);
            println!("🪐 Denní loss limit bariéra nastavena na ${:.2}", clamped);
        }
    }
    
    // Uložit / flush
    mmap.flush()?;
    Ok(())
}
