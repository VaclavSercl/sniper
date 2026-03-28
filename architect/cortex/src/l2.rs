// ═══════════════════════════════════════════════════════════
// 🌐 SOVEREIGN CORTEX — L2 Strategic Module
// Unified 5-minute Oracle cycle: snapshot → Gemini → report
// ═══════════════════════════════════════════════════════════

use crate::memory::{ArmadaMemory, format_snapshot_for_prompt};
use crate::telegram;
use std::process::Command;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::RwLock;
use tokio::time;

const L2_INTERVAL_SECS: u64 = 300; // 5 minutes

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

                    // 6. Build Telegram report
                    let report = build_oracle_report(&snapshots, &response, cycle);
                    if let Err(e) = telegram::send(&report).await {
                        eprintln!("  ⚠️ Telegram send failed: {e}");
                    }

                    // TODO Phase 1.5: Parse JSON response and write decisions to RiskState via mmap
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

fn build_oracle_report(snapshots: &[crate::memory::BotSnapshot], gemini_response: &str, cycle: u64) -> String {
    let now = chrono_now();

    // Determine health from first bot
    let health = if let Some(s) = snapshots.first() {
        if s.toxic_hits < 5 && s.session_fills > 0 { "🟢" }
        else if s.toxic_hits < 20 { "🟡" }
        else { "🔴" }
    } else { "❓" };

    let mut report = format!(
        "{health} *SOVEREIGN CORTEX v13.0 | CYCLE #{cycle}* `{now}`\n\
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

    // Truncate Gemini response for Telegram (4096 char limit)
    let gemini_short = if gemini_response.len() > 1500 {
        &gemini_response[..1500]
    } else {
        gemini_response
    };

    report.push_str(&format!("\n🧠 *Oracle:*\n_{gemini_short}_"));
    report
}

fn build_fallback_report(snapshots: &[crate::memory::BotSnapshot], cycle: u64) -> String {
    let now = chrono_now();
    let mut report = format!(
        "🟡 *SOVEREIGN CORTEX v13.0 | CYCLE #{cycle}* `{now}`\n\
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
    // Simple HH:MM CET without chrono dependency
    let epoch = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let cet = epoch + 3600; // UTC+1 CET
    let hours = (cet % 86400) / 3600;
    let mins = (cet % 3600) / 60;
    format!("{hours:02}:{mins:02}")
}
