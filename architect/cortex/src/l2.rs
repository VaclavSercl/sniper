// ═══════════════════════════════════════════════════════════
// 🌐 SOVEREIGN CORTEX — L2 Strategic Module
// Unified 5-minute Oracle cycle: snapshot → Gemini → dispatch
//
// v13.1: Merged prompt design:
//   - User's Chain-of-Thought (global_reasoning FIRST)
//   - User's per-bot specific output fields
//   - My feedback loop (previous cycle results)
//   - My safety clamps and PnL delta
//   - My macro section with F&G/bias/sweeps
// ═══════════════════════════════════════════════════════════

use crate::memory::{ArmadaMemory, BotSnapshot, format_snapshot_for_prompt};
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

// ═══ Gemini JSON Schema (merged: user's CoT + per-bot fields) ═══

#[derive(Debug, Deserialize, Default, Clone)]
struct GeminiDecision {
    global_reasoning: Option<String>,
    global_regime: Option<String>,  // BEARISH_SHOCK | BULLISH_TREND | CHOPPING_RANGE
    hydra: Option<HydraDecision>,
    moonshot: Option<MoonshotDecision>,
    grid: Option<GridDecision>,
    // Backwards-compatible: accept old field names too
    regime: Option<String>,
    reasoning: Option<String>,
    tactical_recommendation: Option<String>,
    strategic_insight: Option<String>,
}

impl GeminiDecision {
    /// Get regime from either new or old field name.
    fn effective_regime(&self) -> &str {
        self.global_regime.as_deref()
            .or(self.regime.as_deref())
            .unwrap_or("UNKNOWN")
    }

    /// Get reasoning from either new or old field name.
    fn effective_reasoning(&self) -> &str {
        self.global_reasoning.as_deref()
            .or(self.reasoning.as_deref())
            .unwrap_or("")
    }
}

#[derive(Debug, Deserialize, Default, Clone)]
struct HydraDecision {
    recommended_grid_step: Option<f64>,
    max_position_limit: Option<f64>,
    pause_trading: Option<bool>,
    // Backwards compat
    grid_step: Option<f64>,
    max_position: Option<f64>,
    risk_level: Option<String>,
}

impl HydraDecision {
    fn effective_grid(&self) -> Option<f64> {
        self.recommended_grid_step.or(self.grid_step)
    }
    fn effective_max_pos(&self) -> Option<f64> {
        self.max_position_limit.or(self.max_position)
    }
}

#[derive(Debug, Deserialize, Default, Clone)]
struct MoonshotDecision {
    opportunity_bias: Option<String>, // LONG | SHORT | NEUTRAL
}

#[derive(Debug, Deserialize, Default, Clone)]
struct GridDecision {
    action: Option<String>, // KEEP_PAUSED | RESUME
}

// ═══ Feedback Loop State ═══

struct CycleHistory {
    prev_pnl: f64,
    prev_fills: u64,
    prev_toxic: u64,
    prev_decision: Option<GeminiDecision>,
}

impl CycleHistory {
    fn new() -> Self {
        Self {
            prev_pnl: 0.0,
            prev_fills: 0,
            prev_toxic: 0,
            prev_decision: None,
        }
    }
}

/// Run the L2 strategic loop forever.
pub async fn run_l2_loop(memory: Arc<RwLock<ArmadaMemory>>, dry_run: bool) {
    time::sleep(Duration::from_secs(10)).await;
    println!("🌐 [L2] Strategic Oracle online (cycle every {}s)", L2_INTERVAL_SECS);

    let mut cycle: u64 = 0;
    let mut history = CycleHistory::new();

    loop {
        cycle += 1;
        println!("\n═══ L2 ORACLE CYCLE #{cycle} ═══");

        // 1. Atomic snapshot of all bots (~400ns)
        let snapshots = {
            let mem = memory.read().await;
            mem.snapshot_all()
        };

        // 2. Log summary
        for snap in &snapshots {
            let status = if snap.online { "🟢" } else { "🔴" };
            println!("  {status} {} ${:.2} pos={:.6} pnl=${:.4} grid=${:.2} fills={}",
                snap.name, snap.micro_price, snap.net_position,
                snap.realized_pnl, snap.grid_step, snap.session_fills);
        }

        if dry_run {
            // Build context for display but don't call Gemini
            let mut bot_context = String::new();
            for snap in &snapshots {
                bot_context.push_str(&format_snapshot_for_prompt(snap));
                bot_context.push('\n');
            }
            println!("  [DRY-RUN] Skipping Gemini call. Snapshot:\n{bot_context}");
        } else {
            // 3. Build Gemini prompt with feedback loop
            let prompt = build_gemini_prompt(&snapshots, &history, cycle);

            // 4. Call Gemini CLI
            println!("  🤖 Calling Gemini CLI...");
            match call_gemini(&prompt).await {
                Ok(response) => {
                    println!("  ✅ Gemini responded ({} bytes)", response.len());

                    // 5. Parse JSON and apply decisions
                    let decision = parse_gemini_response(&response);
                    if let Some(ref d) = decision {
                        let mem = memory.read().await;
                        apply_decision(d, &mem, cycle);
                    }

                    // 6. Build and send Telegram report
                    let report = build_oracle_report(&snapshots, &response, &decision, cycle);
                    if let Err(e) = telegram::send(&report).await {
                        eprintln!("  ⚠️ Telegram send failed: {e}");
                    }

                    // 7. Update feedback loop history
                    if let Some(hydra) = snapshots.first() {
                        history.prev_pnl = hydra.realized_pnl;
                        history.prev_fills = hydra.session_fills;
                        history.prev_toxic = hydra.toxic_hits;
                    }
                    history.prev_decision = decision;
                }
                Err(e) => {
                    eprintln!("  ❌ Gemini error: {e}");
                    let report = build_fallback_report(&snapshots, cycle);
                    let _ = telegram::send(&report).await;
                }
            }
        }

        println!("═══ L2 CYCLE #{cycle} COMPLETE ═══");
        time::sleep(Duration::from_secs(L2_INTERVAL_SECS)).await;
    }
}

fn parse_gemini_response(raw: &str) -> Option<GeminiDecision> {
    if let Ok(d) = serde_json::from_str::<GeminiDecision>(raw.trim()) {
        return Some(d);
    }

    // Sanitizer: find first { and last }, strip everything else
    if let Some(start) = raw.find('{') {
        if let Some(end) = raw.rfind('}') {
            if let Ok(d) = serde_json::from_str::<GeminiDecision>(&raw[start..=end]) {
                return Some(d);
            }
        }
    }

    eprintln!("  ⚠️ Could not parse Gemini JSON from response");
    None
}

/// Apply Gemini's decision to mmap (direct atomic writes).
fn apply_decision(decision: &GeminiDecision, memory: &ArmadaMemory, cycle: u64) {
    let risk = memory.hydra_risk();
    let engine = memory.hydra_engine();

    // 1. Hydra parameters
    if let Some(ref hydra) = decision.hydra {
        // Check for pause_trading
        if hydra.pause_trading == Some(true) {
            risk.paused.store(1, Ordering::SeqCst);
            println!("  ⏸️ HYDRA PAUSED by Oracle");
        } else if hydra.pause_trading == Some(false) {
            risk.paused.store(0, Ordering::SeqCst);
        }

        // Grid step (clamped)
        if let Some(grid) = hydra.effective_grid() {
            let clamped = grid.clamp(GRID_FLOOR, GRID_CEIL);
            let scaled = (clamped * PRICE_SCALE) as u64;
            risk.grid_step.store(scaled, Ordering::SeqCst);
            println!("  📐 Grid: ${clamped:.2} (raw: ${grid:.2})");
        }

        // Max position (clamped)
        if let Some(max_pos) = hydra.effective_max_pos() {
            let clamped = max_pos.clamp(MAX_POS_FLOOR, MAX_POS_CEIL);
            let scaled = (clamped * PRICE_SCALE) as u64;
            risk.max_inv_delta.store(scaled, Ordering::SeqCst);
            println!("  📦 MaxPos: {clamped:.6} BTC (raw: {max_pos:.6})");
        }
    }

    // 2. Regime to EngineState
    let regime = decision.effective_regime();
    let regime_id: u64 = match regime.to_uppercase().as_str() {
        "TRENDING" | "BULLISH_TREND" => 1,
        "RANGING" | "CHOPPING_RANGE" => 2,
        "CHAOS" | "BEARISH_SHOCK" => 3,
        _ => 0,
    };
    engine.l2_regime_id.store(regime_id, Ordering::Release);
    println!("  📈 Regime: {regime} (id={regime_id})");

    // 3. Timestamps + registry
    let now_ms = epoch_ms();
    engine.l2_last_action_ms.store(now_ms, Ordering::Release);
    engine.ai_heartbeat_ms.store(now_ms, Ordering::Release);
    engine.ai_registry_version.fetch_add(1, Ordering::Release);

    println!("  ✅ Applied (cycle #{cycle}, registry v{})",
        engine.ai_registry_version.load(Ordering::Relaxed));
}

// ═══ PROMPT BUILDER ═══

fn build_gemini_prompt(
    snapshots: &[BotSnapshot],
    history: &CycleHistory,
    cycle: u64,
) -> String {
    let hydra = snapshots.first();

    // Macro section
    let (fg, bias) = if let Some(h) = hydra {
        (h.fear_greed, h.macro_bias)
    } else {
        (50, 0.0)
    };

    let fg_label = match fg {
        0..=24 => "Extreme Fear",
        25..=49 => "Fear",
        50..=74 => "Greed",
        _ => "Extreme Greed",
    };

    let bias_label = if bias < -0.3 { "Bearish" }
        else if bias > 0.3 { "Bullish" }
        else { "Neutral" };

    // Feedback loop: what happened since last cycle?
    let feedback = if cycle > 1 && history.prev_decision.is_some() {
        let d = history.prev_decision.as_ref().unwrap();
        let prev_regime = d.effective_regime();
        let prev_grid = d.hydra.as_ref()
            .and_then(|h| h.effective_grid())
            .map(|g| format!("${g:.2}"))
            .unwrap_or_else(|| "unchanged".to_string());
        let prev_max = d.hydra.as_ref()
            .and_then(|h| h.effective_max_pos())
            .map(|p| format!("{p:.4}"))
            .unwrap_or_else(|| "unchanged".to_string());

        let current_pnl = hydra.map(|h| h.realized_pnl).unwrap_or(0.0);
        let pnl_delta = current_pnl - history.prev_pnl;
        let current_fills = hydra.map(|h| h.session_fills).unwrap_or(0);
        let fills_delta = current_fills.saturating_sub(history.prev_fills);
        let current_toxic = hydra.map(|h| h.toxic_hits).unwrap_or(0);
        let toxic_delta = current_toxic.saturating_sub(history.prev_toxic);

        let assessment = if pnl_delta > 0.0 { "IMPROVED ✅" }
            else if pnl_delta < -0.5 { "DEGRADED ❌" }
            else { "STABLE" };

        format!(
            "\n═══ PREVIOUS CYCLE FEEDBACK ═══\n\
             Cycle #{}: You set regime={prev_regime}, grid={prev_grid}, max_pos={prev_max}\n\
             Result: PnL ${:.4} → ${:.4} ({assessment}, delta: ${pnl_delta:+.4})\n\
             New fills: +{fills_delta} | New toxic: +{toxic_delta}\n",
            cycle - 1, history.prev_pnl, current_pnl,
        )
    } else {
        "\n═══ PREVIOUS CYCLE FEEDBACK ═══\nFirst cycle — no prior data available.\n".to_string()
    };

    // Bot states - condensed format
    let mut bot_states = String::new();
    for snap in snapshots {
        let status = if snap.online { "ONLINE" } else { "OFFLINE" };
        bot_states.push_str(&format!(
            "\n[{emoji} {name}] {status}\n\
             Price=${price:.0} Spread=${spread:.2} Pos={pos:.6}BTC PnL=${pnl:.4}\n\
             Grid=${grid:.2}({levels}L) MaxPos={maxp:.4} Fills={fills} Toxic={toxic}\n\
             L1: Conf={conf:.0}% FP={fp:.0}% Success={succ:.0}% Uptime={up:.0}%\n",
            emoji = snap.emoji, name = snap.name.to_uppercase(), status = status,
            price = snap.micro_price, spread = snap.spread,
            pos = snap.net_position, pnl = snap.realized_pnl,
            grid = snap.grid_step, levels = snap.grid_levels,
            maxp = snap.max_position, fills = snap.session_fills, toxic = snap.toxic_hits,
            conf = snap.l1_confidence * 100.0, fp = snap.l1_fp_rate * 100.0,
            succ = snap.l1_success_rate * 100.0, up = 99.0, // TODO: from mmap
        ));
    }

    format!(
r#"You are SNIPER, the Sovereign Oracle Cortex managing an automated BTC trading Armada.
Analyze the global macro environment and the exact state of all sub-bots.
Make highly coordinated, multi-bot strategic decisions to maximize PnL and survive flash crashes.

RULES:
- Grid step MUST be between ${GRID_FLOOR} and ${GRID_CEIL}
- Max position MUST be between {MAX_POS_FLOOR} and {MAX_POS_CEIL} BTC
- If F&G < 20: prefer DEFENSIVE posture (wider grids, lower exposure)
- If toxic > 500: consider pausing or widening grid significantly
- Always fill "global_reasoning" FIRST to establish logic BEFORE setting parameters
- Respond ONLY in valid JSON. No markdown, no prose outside JSON.

═══ MACRO INTELLIGENCE ═══
Fear & Greed Index: {fg} ({fg_label})
News Sentiment: {bias:+.4} ({bias_label})
Cycle: #{cycle} (every 5 min)
{feedback}
═══ ARMADA STATE ═══{bot_states}
═══ RESPOND WITH THIS JSON ═══
{{"global_reasoning": "Analyze macro + cross-bot correlations here FIRST...",
  "global_regime": "BEARISH_SHOCK|BULLISH_TREND|CHOPPING_RANGE",
  "hydra": {{"recommended_grid_step": float, "max_position_limit": float, "pause_trading": boolean}},
  "moonshot": {{"opportunity_bias": "LONG|SHORT|NEUTRAL"}},
  "grid": {{"action": "KEEP_PAUSED|RESUME"}}}}"#
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
    snapshots: &[BotSnapshot],
    _gemini_response: &str,
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
        "{health} *SOVEREIGN CORTEX v13.1 | #{cycle}* `{now}`\n\
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

    if let Some(d) = decision {
        let regime = d.effective_regime();
        let regime_icon = match regime {
            r if r.contains("BULL") || r == "TRENDING" => "📈",
            r if r.contains("BEAR") || r == "CHAOS" => "🌪️",
            _ => "↔️",
        };

        report.push_str(&format!("\n{regime_icon} *Režim:* `{regime}`\n"));

        if let Some(ref hydra) = d.hydra {
            let grid = hydra.effective_grid().unwrap_or(0.0);
            let max_p = hydra.effective_max_pos().unwrap_or(0.0);
            let paused = if hydra.pause_trading == Some(true) { "⏸️ YES" } else { "▶️ NO" };
            report.push_str(&format!(
                "📐 Grid: `${grid:.2}` | MaxPos: `{max_p:.4}` | Paused: `{paused}`\n",
            ));
        }

        if let Some(ref ms) = d.moonshot {
            if let Some(ref bias) = ms.opportunity_bias {
                report.push_str(&format!("🌙 Moonshot: `{bias}`\n"));
            }
        }

        if let Some(ref g) = d.grid {
            if let Some(ref action) = g.action {
                report.push_str(&format!("📐 Grid Bot: `{action}`\n"));
            }
        }

        let reasoning = d.effective_reasoning();
        if !reasoning.is_empty() {
            report.push_str(&format!("\n🧠 _{reasoning}_\n"));
        }

        if let Some(ref tactic) = d.tactical_recommendation {
            report.push_str(&format!("🎯 _{tactic}_\n"));
        }
        if let Some(ref insight) = d.strategic_insight {
            report.push_str(&format!("💡 _{insight}_\n"));
        }
    }

    report
}

fn build_fallback_report(snapshots: &[BotSnapshot], cycle: u64) -> String {
    let now = chrono_now();
    let mut report = format!(
        "🟡 *SOVEREIGN CORTEX v13.1 | #{cycle}* `{now}`\n\
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

fn epoch_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn chrono_now() -> String {
    let epoch = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let cet = epoch + 3600;
    let hours = (cet % 86400) / 3600;
    let mins = (cet % 3600) / 60;
    format!("{hours:02}:{mins:02}")
}
