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
#[command(about = "🐺 Beroun Sniper v9.0 — Safe live parameter modifier (mmap)")]
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
    /// Nastaví autorizovaný kapitál v USD
    SetCapital { value: f64 },
    /// Nastaví denní loss limit v USD (circuit breaker)
    SetLoss { value: f64 },
    /// Nastaví počet pater Hydra gridu (1-5)
    SetLevels { value: u64 },
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
            if !(GRID_MIN..=GRID_MAX).contains(&value) {
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
            if !(INV_MIN..=INV_MAX).contains(&value) {
                bail!("❌ MaxInv {value} BTC mimo rozsah ({INV_MIN}-{INV_MAX})");
            }
            risk.max_inv_delta.store((value * s) as u64, Ordering::SeqCst);
            println!("✅ Max Inventory Delta: {value} BTC");
        }
        Commands::Pause { state } => {
            risk.paused.store(if state { 1 } else { 0 }, Ordering::SeqCst);
            println!("✅ Bot {}", if state { "⏸️  PAUSED" } else { "▶️  RESUMED" });
        }
        Commands::SetCapital { value } => {
            if !(10.0..=50_000.0).contains(&value) {
                bail!("❌ Capital ${value} mimo rozsah ($10-$50,000)");
            }
            risk.authorized_capital.store((value * s) as u64, Ordering::SeqCst);
            println!("✅ Authorized Capital: ${value:.2}");
        }
        Commands::SetLoss { value } => {
            if !(1.0..=10_000.0).contains(&value) {
                bail!("❌ Loss limit ${value} mimo rozsah ($1-$10,000)");
            }
            risk.daily_loss_limit.store((value * s) as u64, Ordering::SeqCst);
            println!("✅ Daily Loss Limit: -${value:.2}");
        }
        Commands::SetLevels { value } => {
            if !(1..=5).contains(&value) {
                bail!("❌ Grid levels {value} mimo rozsah (1-5)");
            }
            risk.grid_size.store(value, Ordering::SeqCst);
            println!("✅ Grid Levels: {value}");
        }
        Commands::Show => {
            println!("🐺 Beroun Sniper v9.0 — RiskState Live");
            println!("─────────────────────────────────");
            println!("  Grid Step:     ${:.2}", risk.grid_step.load(Ordering::Acquire) as f64 / s);
            println!("  Grid Levels:   {}", risk.grid_size.load(Ordering::Acquire));
            println!("  Bias Offset:   ${:.2}", risk.bias_offset.load(Ordering::Acquire) as f64 / s);
            println!("  Max Inv Delta: {:.6} BTC", risk.max_inv_delta.load(Ordering::Acquire) as f64 / s);
            println!("  Auth Capital:  ${:.2}", risk.authorized_capital.load(Ordering::Acquire) as f64 / s);
            println!("  Loss Limit:    -${:.2}", risk.daily_loss_limit.load(Ordering::Acquire) as f64 / s);
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

            let ai_hb_ms = engine.ai_heartbeat_ms.load(Ordering::Acquire);
            let now_epoch = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH).unwrap_or_default()
                .as_millis() as u64;
            let ai_alive = ai_hb_ms > 0 && now_epoch.saturating_sub(ai_hb_ms) < 600_000; // 10min (2× Oracle cycle)

            let w_btc = engine.wallet_btc.load(Ordering::Acquire) as f64 / s;
            let w_usd = engine.wallet_usd.load(Ordering::Acquire) as f64 / s;
            let btc_value = w_btc * micro_p;
            let total_equity = w_usd + btc_value;

            // Pre-compute analytics (can't use let inside json! macro)
            let a_fills = engine.session_fill_count.load(Ordering::Acquire);
            let a_bv = engine.session_buy_volume.load(Ordering::Acquire) as f64 / s;
            let a_sv = engine.session_sell_volume.load(Ordering::Acquire) as f64 / s;
            let a_bu = engine.session_buy_usd.load(Ordering::Acquire) as f64 / s;
            let a_su = engine.session_sell_usd.load(Ordering::Acquire) as f64 / s;
            let a_spread = if a_bv > 0.0 && a_sv > 0.0 { (a_su / a_sv) - (a_bu / a_bv) } else { 0.0 };

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
                "equity": {
                    "total_usd": total_equity,
                    "wallet_usd": w_usd,
                    "wallet_btc": w_btc,
                    "btc_value_usd": btc_value
                },
                "volume": {
                    "monthly_usd": engine.monthly_volume_usd.load(Ordering::Acquire) as f64 / s,
                    "fee_tier": "Zero (since Dec 2025)",
                    "maker_fee_pct": 0.0,
                    "taker_fee_pct": 0.0,
                },
                "intelligence": {
                    "l2_obi": obi,
                    "ai_bias_usd": ai_bias,
                    "t2t_micros": t2t,
                    "ai_heartbeat_ms": ai_hb_ms,
                    "ai_alive": ai_alive
                },
                "analytics": {
                    "session_fills": a_fills,
                    "session_buy_volume_btc": a_bv,
                    "session_sell_volume_btc": a_sv,
                    "session_buy_usd": a_bu,
                    "session_sell_usd": a_su,
                    "net_spread_capture_per_btc": a_spread,
                    "toxic_flow_hits": engine.toxic_flow_hits.load(Ordering::Acquire),
                    "checkpoint_ms": engine.analytics_checkpoint_ms.load(Ordering::Acquire),
                },
                "l1_intelligence": {
                    "sweep_freeze_until_ms": engine.sweep_freeze_until.load(Ordering::Acquire),
                    "l1_skew_adjustment_usd": engine.l1_skew_adjustment.load(Ordering::Acquire) as f64 / s,
                },
                "risk_params": {
                    "grid_step_usd": grid,
                    "grid_levels": risk.grid_size.load(Ordering::Acquire),
                    "bias_offset_usd": bias,
                    "max_inv_delta_btc": max_inv,
                    "authorized_capital_usd": risk.authorized_capital.load(Ordering::Acquire) as f64 / s,
                    "daily_loss_limit_usd": risk.daily_loss_limit.load(Ordering::Acquire) as f64 / s,
                    "paused": risk.paused.load(Ordering::Acquire) != 0
                }
            });

            println!("{}", serde_json::to_string_pretty(&json)?);
        }
    }

    Ok(())
}
