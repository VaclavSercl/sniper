// ═══════════════════════════════════════════════════════════
// 📊 SOVEREIGN CORTEX — Health State Writer
// Writes Cortex telemetry to /dev/shm/beroun/cortex_state.json
// for the Architect Dashboard to consume.
// ═══════════════════════════════════════════════════════════

use crate::memory::ArmadaMemory;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::RwLock;
use tokio::time;

const STATE_FILE: &str = "/dev/shm/beroun/cortex_state.json";
const UPDATE_INTERVAL_SECS: u64 = 5;

/// Run the Cortex health writer loop (writes every 5s).
pub async fn run_health_writer(memory: Arc<RwLock<ArmadaMemory>>) {
    println!("📊 [HEALTH] Writing cortex_state.json every {}s", UPDATE_INTERVAL_SECS);
    let start = SystemTime::now();

    loop {
        let state = {
            let mem = memory.read().await;
            let snapshots = mem.snapshot_all();
            let uptime_s = start.elapsed().unwrap_or_default().as_secs();
            let now_ms = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as u64;

            let online_count = snapshots.iter().filter(|s| s.online).count();
            let total_pnl: f64 = snapshots.iter().map(|s| s.realized_pnl).sum();
            let total_fills: u64 = snapshots.iter().map(|s| s.session_fills).sum();

            let bots: Vec<serde_json::Value> = snapshots.iter().map(|s| {
                serde_json::json!({
                    "name": s.name,
                    "emoji": s.emoji,
                    "online": s.online,
                    "price": format!("{:.2}", s.micro_price),
                    "position": format!("{:.6}", s.net_position),
                    "pnl": format!("{:.4}", s.realized_pnl),
                    "grid": format!("{:.2}", s.grid_step),
                    "fills": s.session_fills,
                    "toxic": s.toxic_hits,
                    "regime": s.l2_regime,
                    "intent": s.ai_intent,
                    "confidence": format!("{:.0}", s.l1_confidence * 100.0),
                    "paused": s.paused,
                    "shadow": s.shadow_mode,
                })
            }).collect();

            serde_json::json!({
                "timestamp_ms": now_ms,
                "uptime_s": uptime_s,
                "version": "13.0",
                "modules": {
                    "l1": true,
                    "l2": true,
                    "macro": true,
                    "gpu": true,
                },
                "bots_online": online_count,
                "bots_total": snapshots.len(),
                "total_pnl": format!("{:.4}", total_pnl),
                "total_fills": total_fills,
                "fear_greed": snapshots.first().map(|s| s.fear_greed).unwrap_or(0),
                "macro_bias": format!("{:.4}", snapshots.first().map(|s| s.macro_bias).unwrap_or(0.0)),
                "bots": bots,
            })
        };

        // Atomic write (tmp → rename)
        let tmp = format!("{STATE_FILE}.tmp");
        if let Ok(json) = serde_json::to_string_pretty(&state) {
            if std::fs::write(&tmp, &json).is_ok() {
                let _ = std::fs::rename(&tmp, STATE_FILE);
            }
        }

        time::sleep(Duration::from_secs(UPDATE_INTERVAL_SECS)).await;
    }
}
