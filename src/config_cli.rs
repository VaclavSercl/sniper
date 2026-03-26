use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use memmap2::MmapMut;
use anyhow::{Result, Context, bail};
use clap::{Parser, Subcommand};

use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE};

// ── SAFETY LIMITS (anti-hallucination guardrails) ──
const GRID_MIN: f64 = 1.0;     // $1 minimum grid
const GRID_MAX: f64 = 200.0;   // $200 max grid
const BIAS_MAX: f64 = 500.0;   // ±$500 max bias
const INV_MIN: f64 = 0.0001;   // 0.0001 BTC minimum
const INV_MAX: f64 = 0.5;      // 0.5 BTC maximum
const MAX_CHANGE_PCT: f64 = 50.0; // Max 50% change per update

#[derive(Parser)]
#[command(name = "beroun-config")]
#[command(about = "🐺 Beroun Sniper v7.1 — Safe live parameter modifier (mmap)")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Nastaví grid_step (v USD) — clamp: $1-$200, max ±50% change
    SetGrid { value: f64 },
    /// Nastaví bias_offset (v USD) — clamp: ±$500
    SetBias { value: f64 },
    /// Nastaví max_inv_delta (v BTC) — clamp: 0.0001-0.5
    SetMaxInv { value: f64 },
    /// Zastaví/spustí bota (true=pause, false=resume)
    Pause { state: bool },
    /// Zobrazí aktuální parametry
    Show,
    /// Exportuje stav do JSON (pro Oracle/Gemini)
    ExportJson,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let s = PRICE_SCALE;

    // Open risk_state mmap
    let risk_file = OpenOptions::new()
        .read(true).write(true)
        .open(&*RISK_STATE_PATH)
        .context("Nelze otevřít risk_state.bin. Běží bot?")?;
    let risk_mmap = unsafe { MmapMut::map_mut(&risk_file)? };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const RiskState) };

    match cli.command {
        Commands::SetGrid { value } => {
            // 1. Absolute clamp
            if value < GRID_MIN || value > GRID_MAX {
                bail!("❌ Grid ${value} mimo povolený rozsah (${GRID_MIN}-${GRID_MAX})");
            }
            // 2. Rate-of-change check (max ±50%)
            let old = risk.grid_step.load(Ordering::Acquire) as f64 / s;
            if old > 0.0 {
                let change_pct = ((value - old) / old * 100.0).abs();
                if change_pct > MAX_CHANGE_PCT {
                    let clamped = if value > old {
                        old * (1.0 + MAX_CHANGE_PCT / 100.0)
                    } else {
                        old * (1.0 - MAX_CHANGE_PCT / 100.0)
                    };
                    eprintln!("⚠️  Změna {change_pct:.0}% > {MAX_CHANGE_PCT}% limit. Clamped: ${old:.2} → ${clamped:.2}");
                    risk.grid_step.store((clamped * s) as u64, Ordering::SeqCst);
                    println!("✅ Grid Step: ${clamped:.2} (clamped from ${value})");
                    return Ok(());
                }
            }
            risk.grid_step.store((value * s) as u64, Ordering::SeqCst);
            println!("✅ Grid Step: ${value} (was ${old:.2})");
        }
        Commands::SetBias { value } => {
            let clamped = value.clamp(-BIAS_MAX, BIAS_MAX);
            if (clamped - value).abs() > 0.01 {
                eprintln!("⚠️  Bias clamped: ${value} → ${clamped}");
            }
            risk.bias_offset.store((clamped * s) as i64, Ordering::SeqCst);
            println!("✅ Bias Offset: ${clamped}");
        }
        Commands::SetMaxInv { value } => {
            if value < INV_MIN || value > INV_MAX {
                bail!("❌ MaxInv {value} BTC mimo rozsah ({INV_MIN}-{INV_MAX})");
            }
            risk.max_inv_delta.store((value * s) as u64, Ordering::SeqCst);
            println!("✅ Max Inventory Delta: {value} BTC");
        }
        Commands::Pause { state } => {
            risk.paused.store(if state { 1 } else { 0 }, Ordering::SeqCst);
            println!("✅ Bot {}", if state { "⏸️  PAUSED" } else { "▶️  RESUMED" });
        }
        Commands::Show => {
            println!("🐺 Beroun Sniper — RiskState Live");
            println!("─────────────────────────────────");
            println!("  Grid Step:     ${:.2}", risk.grid_step.load(Ordering::Acquire) as f64 / s);
            println!("  Bias Offset:   ${:.2}", risk.bias_offset.load(Ordering::Acquire) as f64 / s);
            println!("  Max Inv Delta: {:.6} BTC", risk.max_inv_delta.load(Ordering::Acquire) as f64 / s);
            println!("  Paused:        {}", if risk.paused.load(Ordering::Acquire) != 0 { "YES ⏸️" } else { "NO ▶️" });
        }
        Commands::ExportJson => {
            // Read EngineState too
            let eng_file = OpenOptions::new().read(true)
                .open(&*ENGINE_STATE_PATH)
                .context("Nelze otevřít engine_state.bin")?;
            let eng_mmap = unsafe { memmap2::MmapOptions::new().map(&eng_file)? };
            let engine = unsafe { &*(eng_mmap.as_ptr() as *const EngineState) };

            let grid = risk.grid_step.load(Ordering::Acquire) as f64 / s;
            let bias = risk.bias_offset.load(Ordering::Acquire) as f64 / s;
            let max_inv = risk.max_inv_delta.load(Ordering::Acquire) as f64 / s;
            let best_bid = engine.best_bid.load(Ordering::Acquire) as f64 / s;
            let best_ask = engine.best_ask.load(Ordering::Acquire) as f64 / s;
            let micro_p = engine.micro_price.load(Ordering::Acquire) as f64 / s;
            let net_pos = engine.net_position.load(Ordering::Acquire) as f64 / s;
            let r_pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / s;
            let aep = engine.average_entry_price.load(Ordering::Acquire) as f64 / s;
            let obi = engine.l2_imbalance.load(Ordering::Acquire) as f64 / s;
            let ai_bias = engine.current_ai_bias.load(Ordering::Acquire) as f64 / s;
            let t2t = engine.t2t_micros.load(Ordering::Acquire);
            let skew = engine.current_skew.load(Ordering::Acquire) as f64 / s;

            let u_pnl = if aep > 0.0 && net_pos.abs() > 1e-8 {
                (micro_p - aep) * net_pos
            } else { 0.0 };

            let json = serde_json::json!({
                "timestamp": chrono::Utc::now().to_rfc3339(),
                "price": {
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "micro_price": micro_p,
                    "spread": best_ask - best_bid
                },
                "position": {
                    "net_btc": net_pos,
                    "avg_entry_price": aep,
                    "inventory_skew": skew
                },
                "pnl": {
                    "realized_usd": r_pnl,
                    "unrealized_usd": u_pnl,
                    "total_usd": r_pnl + u_pnl
                },
                "intelligence": {
                    "l2_obi": obi,
                    "ai_bias_usd": ai_bias,
                    "t2t_micros": t2t
                },
                "risk_params": {
                    "grid_step_usd": grid,
                    "bias_offset_usd": bias,
                    "max_inv_delta_btc": max_inv,
                    "paused": risk.paused.load(Ordering::Acquire) != 0
                }
            });

            println!("{}", serde_json::to_string_pretty(&json)?);
        }
    }

    Ok(())
}
