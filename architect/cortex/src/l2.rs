// ═══════════════════════════════════════════════════════════
// 🌐 SOVEREIGN CORTEX — L2 Strategic Module
// Unified 5-minute Oracle cycle: snapshot → Gemini → dispatch
// Phase 1.5: Full JSON parsing + mmap write-back
// ═══════════════════════════════════════════════════════════

use crate::memory::{ArmadaMemory, format_snapshot_for_prompt};
use crate::telegram;
use serde::Deserialize;
use sniper_types::PRICE_SCALE;
use std::process::Command;
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::Duration;
use tokio::sync::RwLock;
use tokio::time;

const L2_INTERVAL_SECS: u64 = 300; // 5 minutes

// Safety clamps — Gemini cannot set values outside these ranges
const GRID_FLOOR: f64 = 2.0;
const GRID_CEIL: f64 = 50.0;
const MAX_POS_FLOOR: f64 = 0.001;
const MAX_POS_CEIL: f64 = 0.02;

/// Gemini's expected JSON response (permissive deserialization)
#[derive(Debug, Deserialize, Default)]
struct GeminiDecision {
    regime: Option<String>,
    hydra: Option<HydraDecision>,
    reasoning: Option<String>,
    tactical_recommendation: Option<String>,
    strategic_insight: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
struct HydraDecision {
    grid_step: Option<f64>,
    max_position: Option<f64>,
    risk_level: Option<String>,
}

/// Run the L2 strategic loop forever.
pub async fn run_l2_loop(memory: Arc<RwLock<ArmadaMemory>>, dry_run: bool) {
    // Wait 10s before first cycle to let bots stabilize
    time::sleep(Duration::from_secs(10)).await;
    println!("🌐 [L2] Strategic Oracle online (cycle every {}s)", L2_INTERVAL_SECS);

    let mut cycle: u64 = 0;

    loop {
        cycle += 1;
        println!("\n═══ L2 ORACLE CYCLE #{cycle} ═══");

        // 1. Atomic snapshot of all bots (~400ns)
        let snapshots = {
            let mem = memory.read().await;
            mem.snapshot_all()
        };

        // 2. Build per-bot context blocks
        let mut bot_context = String::new();
        for snap in &snapshots {
            bot_context.push_str(&format_snapshot_for_prompt(snap));
            bot_context.push('\n');
        }

        // 3. Summary for logging
        for snap in &snapshots {
            let status = if snap.online { "🟢" } else { "🔴" };
            println!("  {status} {} ${:.2} pos={:.6} pnl=${:.4} grid=${:.2} fills={}",
                snap.name, snap.micro_price, snap.net_position,
                snap.realized_pnl, snap.grid_step, snap.session_fills);
        }

        if dry_run {
            println!("  [DRY-RUN] Skipping Gemini call. Snapshot:\n{bot_context}");
        } else {
            // 4. Build Gemini prompt
            let prompt = build_gemini_prompt(&bot_context, cycle);

            // 5. Call Gemini CLI (async-safe)
            println!("  🤖 Calling Gemini CLI...");
            match call_gemini(&prompt).await {
                Ok(response) => {
                    println!("  ✅ Gemini responded ({} bytes)", response.len());

                    // 6. Parse JSON and apply decisions
                    let decision = parse_gemini_response(&response);
                    if let Some(ref d) = decision {
                        let mem = memory.read().await;
                        apply_decision(d, &mem, cycle);
                    }

                    // 7. Build and send Telegram report
                    let report = build_oracle_report(&snapshots, &response, &decision, cycle);
                    if let Err(e) = telegram::send(&report).await {
                        eprintln!("  ⚠️ Telegram send failed: {e}");
                    }
                }
                Err(e) => {
                    eprintln!("  ❌ Gemini error: {e}");
                    // Send degraded report without AI analysis
                    let report = build_fallback_report(&snapshots, cycle);
                    let _ = telegram::send(&report).await;
                }
            }
        }

        println!("═══ L2 CYCLE #{cycle} COMPLETE ═══");
        time::sleep(Duration::from_secs(L2_INTERVAL_SECS)).await;
    }
}

/// Parse Gemini's response — extract JSON from possibly markdown-wrapped output.
fn parse_gemini_response(raw: &str) -> Option<GeminiDecision> {
    // Try direct parse first
    if let Ok(d) = serde_json::from_str::<GeminiDecision>(raw.trim()) {
        return Some(d);
    }

    // Try to find JSON block in markdown-wrapped response
    let trimmed = raw.trim();
    // Look for { ... } containing "regime"
    if let Some(start) = trimmed.find('{') {
        if let Some(end) = trimmed.rfind('}') {
            let json_str = &trimmed[start..=end];
            if let Ok(d) = serde_json::from_str::<GeminiDecision>(json_str) {
                return Some(d);
            }
        }
    }

    eprintln!("  ⚠️ Could not parse Gemini JSON from response");
    None
}

/// Apply Gemini's decision to mmap (direct atomic writes — no subprocess).
fn apply_decision(decision: &GeminiDecision, memory: &ArmadaMemory, cycle: u64) {
    let risk = memory.hydra_risk();
    let engine = memory.hydra_engine();

    // 1. Grid step (clamped to safety range)
    if let Some(ref hydra) = decision.hydra {
        if let Some(grid) = hydra.grid_step {
            let clamped = grid.clamp(GRID_FLOOR, GRID_CEIL);
            let scaled = (clamped * PRICE_SCALE) as u64;
            risk.grid_step.store(scaled, Ordering::SeqCst);
            println!("  📐 Grid: ${clamped:.2} (raw: ${grid:.2})");
        }

        if let Some(max_pos) = hydra.max_position {
            let clamped = max_pos.clamp(MAX_POS_FLOOR, MAX_POS_CEIL);
            let scaled = (clamped * PRICE_SCALE) as u64;
            risk.max_inv_delta.store(scaled, Ordering::SeqCst);
            println!("  📦 MaxPos: {clamped:.6} BTC (raw: {max_pos:.6})");
        }
    }

    // 2. Regime to EngineState
    if let Some(ref regime) = decision.regime {
        let regime_id: u64 = match regime.to_uppercase().as_str() {
            "TRENDING" => 1,
            "RANGING" => 2,
            "CHAOS" => 3,
            _ => 0,
        };
        engine.l2_regime_id.store(regime_id, Ordering::Release);
        println!("  📈 Regime: {regime} (id={regime_id})");
    }

    // 3. L2 action timestamp + AI heartbeat
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    engine.l2_last_action_ms.store(now_ms, Ordering::Release);
    engine.ai_heartbeat_ms.store(now_ms, Ordering::Release);
    engine.ai_registry_version.fetch_add(1, Ordering::Release);

    println!("  ✅ Applied (cycle #{cycle}, registry v{})",
        engine.ai_registry_version.load(Ordering::Relaxed));
}

fn build_gemini_prompt(bot_context: &str, cycle: u64) -> String {
    format!(
r#"You are SNIPER, the Sovereign Oracle for the entire Sniper Armada v13.0.
You manage ALL trading bots simultaneously. Make COORDINATED decisions.
You have PERMANENT MEMORY from past cycles. This is cycle #{cycle}.

═══ SYSTEM ARCHITECTURE ═══
L0 = Rust HFT Engines (µs execution, atomic mmap IPC)
L1 = Tactical Shield (50ms OBI skewing, sweep detection)
L2 = YOU — Sovereign Oracle (5min strategic analysis, macro + cross-bot correlation)

═══ LIVE ARMADA STATE ═══
{bot_context}

═══ YOUR MISSION ═══
STEP 1: Classify market regime (TRENDING / RANGING / CHAOS)
STEP 2: For EACH bot — evaluate performance and recommend parameter changes
STEP 3: Check for cross-bot risks (total exposure, correlated positions)
STEP 4: Provide a SPECIFIC tactical recommendation for the next 30 minutes

RESPOND WITH EXACTLY THIS JSON (no markdown):
{{"regime": "TRENDING|RANGING|CHAOS",
  "hydra": {{"grid_step": 8.0, "max_position": 0.005, "risk_level": "low|medium|high"}},
  "reasoning": "2-3 sentence analysis",
  "tactical_recommendation": "Specific actionable advice",
  "strategic_insight": "High-level market insight for the operator"}}"#
    )
}

async fn call_gemini(prompt: &str) -> anyhow::Result<String> {
    let prompt_owned = prompt.to_string();
    let output = tokio::task::spawn_blocking(move || {
        Command::new("gemini")
            .arg("-p")
            .arg(&prompt_owned)
            .output()
    })
    .await??;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!("Gemini CLI failed: {stderr}");
    }

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    Ok(stdout)
}

fn build_oracle_report(
    snapshots: &[crate::memory::BotSnapshot],
    gemini_response: &str,
    decision: &Option<GeminiDecision>,
    cycle: u64,
) -> String {
    let now = chrono_now();

    let health = if let Some(s) = snapshots.first() {
        if s.toxic_hits < 5 && s.session_fills > 0 { "🟢" }
        else if s.toxic_hits < 20 { "🟡" }
        else { "🔴" }
    } else { "❓" };

    let mut report = format!(
        "{health} *SOVEREIGN CORTEX v13.0 | #{cycle}* `{now}`\n\
         ━━━━━━━━━━━━━━━━━━━━━\n"
    );

    for s in snapshots {
        let icon = if s.online { "🟢" } else { "🔴" };
        report.push_str(&format!(
            "\n{icon} {emoji} *{name}*\n\
             💲 `${price:.2}` | 📦 `{pos:.5} BTC` | 💰 `${pnl:.4}`\n\
             📐 Grid `${grid:.2}` ({levels}L) | Fills `{fills}` | Toxic `{toxic}`\n",
            emoji = s.emoji,
            name = s.name.to_uppercase(),
            price = s.micro_price,
            pos = s.net_position,
            pnl = s.realized_pnl,
            grid = s.grid_step,
            levels = s.grid_levels,
            fills = s.session_fills,
            toxic = s.toxic_hits,
        ));
    }

    // Add decision summary
    if let Some(d) = decision {
        let regime = d.regime.as_deref().unwrap_or("?");
        let regime_icon = match regime {
            "TRENDING" => "📈",
            "RANGING" => "↔️",
            "CHAOS" => "🌪️",
            _ => "❓",
        };

        report.push_str(&format!("\n{regime_icon} *Režim:* `{regime}`\n"));

        if let Some(ref hydra) = d.hydra {
            report.push_str(&format!(
                "📐 Grid: `${:.2}` | MaxPos: `{:.4} BTC` | Risk: `{}`\n",
                hydra.grid_step.unwrap_or(0.0),
                hydra.max_position.unwrap_or(0.0),
                hydra.risk_level.as_deref().unwrap_or("?"),
            ));
        }

        if let Some(ref reasoning) = d.reasoning {
            report.push_str(&format!("\n🧠 _{reasoning}_\n"));
        }
        if let Some(ref tactic) = d.tactical_recommendation {
            report.push_str(&format!("🎯 _{tactic}_\n"));
        }
        if let Some(ref insight) = d.strategic_insight {
            report.push_str(&format!("💡 _{insight}_\n"));
        }
    } else {
        // No parsed decision — show raw Gemini response
        let gemini_short = if gemini_response.len() > 1200 {
            &gemini_response[..1200]
        } else {
            gemini_response
        };
        report.push_str(&format!("\n🧠 *Oracle (raw):*\n_{gemini_short}_"));
    }

    report
}

fn build_fallback_report(snapshots: &[crate::memory::BotSnapshot], cycle: u64) -> String {
    let now = chrono_now();
    let mut report = format!(
        "🟡 *SOVEREIGN CORTEX v13.0 | #{cycle}* `{now}`\n\
         ━━━━━━━━━━━━━━━━━━━━━\n\
         ⚠️ _Gemini nedostupné — pouze lokální data_\n"
    );

    for s in snapshots {
        let icon = if s.online { "🟢" } else { "🔴" };
        report.push_str(&format!(
            "\n{icon} {} *{}* | `${:.2}` | pos `{:.5}` | pnl `${:.4}`\n",
            s.emoji, s.name.to_uppercase(), s.micro_price, s.net_position, s.realized_pnl,
        ));
    }
    report
}

fn chrono_now() -> String {
    let epoch = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let cet = epoch + 3600; // UTC+1 CET
    let hours = (cet % 86400) / 3600;
    let mins = (cet % 3600) / 60;
    format!("{hours:02}:{mins:02}")
}
