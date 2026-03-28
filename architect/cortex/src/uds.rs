// ═══════════════════════════════════════════════════════════
// 🔌 SOVEREIGN CORTEX — Unix Domain Socket IPC (v14.0)
//
// The bridge between hemispheres:
//   Right (Cortex/Rust) ←→ Left (Commander/Python)
//
// Protocol: JSON-line over /tmp/cortex.sock
// Each message = one JSON object + \n
// Each connection handles one request → one response → close
//
// Commands:
//   PING           → {pong: true, uptime_s: ...}
//   GET_SNAPSHOT    → full mmap dump of all bots
//   SET_GRID        → write grid_step to mmap
//   SET_MAXPOS      → write max_position to mmap
//   SET_REGIME      → write regime to mmap
//   PAUSE           → pause a bot
//   UNPAUSE         → unpause a bot
// ═══════════════════════════════════════════════════════════

use crate::memory::{ArmadaMemory, enrich_with_pnl};
use serde::{Deserialize, Serialize};
use sniper_types::PRICE_SCALE;
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;
use tokio::sync::RwLock;

const SOCKET_PATH: &str = "/tmp/cortex.sock";

// Safety clamps (same as l2.rs)
const GRID_FLOOR: f64 = 2.0;
const GRID_CEIL: f64 = 50.0;
const MAX_POS_FLOOR: f64 = 0.001;
const MAX_POS_CEIL: f64 = 0.02;

// ── Request / Response types ──

#[derive(Deserialize)]
struct UdsRequest {
    cmd: String,
    #[serde(default)]
    bot: Option<String>,
    #[serde(default)]
    value: Option<f64>,
    #[serde(default)]
    regime: Option<String>,
}

#[derive(Serialize)]
struct UdsResponse {
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    data: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    prev: Option<f64>,
}

impl UdsResponse {
    fn ok() -> Self {
        Self { ok: true, error: None, data: None, prev: None }
    }
    fn ok_with_data(data: serde_json::Value) -> Self {
        Self { ok: true, error: None, data: Some(data), prev: None }
    }
    fn ok_with_prev(prev: f64) -> Self {
        Self { ok: true, error: None, data: None, prev: Some(prev) }
    }
    fn err(msg: &str) -> Self {
        Self { ok: false, error: Some(msg.to_string()), data: None, prev: None }
    }
}

// ═══════════════════════════════════════════════════════════
// Main UDS loop
// ═══════════════════════════════════════════════════════════

pub async fn run_uds_server(memory: Arc<RwLock<ArmadaMemory>>) {
    // Remove stale socket
    let _ = std::fs::remove_file(SOCKET_PATH);

    let listener = match UnixListener::bind(SOCKET_PATH) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("🔌 [UDS] Failed to bind {SOCKET_PATH}: {e}");
            return;
        }
    };

    // Set permissions (owner-only)
    let _ = std::fs::set_permissions(
        SOCKET_PATH,
        std::os::unix::fs::PermissionsExt::from_mode(0o600),
    );

    let start = Instant::now();
    println!("🔌 [UDS] Listening on {SOCKET_PATH}");

    loop {
        let (stream, _) = match listener.accept().await {
            Ok(s) => s,
            Err(e) => {
                eprintln!("🔌 [UDS] Accept error: {e}");
                continue;
            }
        };

        let mem = Arc::clone(&memory);
        let uptime = start.elapsed().as_secs();

        tokio::spawn(async move {
            let (reader, mut writer) = stream.into_split();
            let mut buf_reader = BufReader::new(reader);
            let mut line = String::new();

            if buf_reader.read_line(&mut line).await.is_err() {
                return;
            }

            let response = match serde_json::from_str::<UdsRequest>(line.trim()) {
                Ok(req) => handle_request(req, &mem, uptime).await,
                Err(e) => UdsResponse::err(&format!("Invalid JSON: {e}")),
            };

            if let Ok(json) = serde_json::to_string(&response) {
                let _ = writer.write_all(json.as_bytes()).await;
                let _ = writer.write_all(b"\n").await;
            }
        });
    }
}

// ═══════════════════════════════════════════════════════════
// Command dispatcher
// ═══════════════════════════════════════════════════════════

async fn handle_request(
    req: UdsRequest,
    memory: &Arc<RwLock<ArmadaMemory>>,
    uptime: u64,
) -> UdsResponse {
    match req.cmd.to_uppercase().as_str() {
        "PING" => {
            UdsResponse::ok_with_data(serde_json::json!({
                "pong": true,
                "uptime_s": uptime,
                "version": "14.0",
            }))
        }

        "GET_SNAPSHOT" => {
            let mem = memory.read().await;
            let mut snapshots = mem.snapshot_all();
            enrich_with_pnl(&mut snapshots);

            let bots: Vec<serde_json::Value> = snapshots.iter().map(|s| {
                serde_json::json!({
                    "name": s.name,
                    "emoji": s.emoji,
                    "online": s.online,
                    "price": s.micro_price,
                    "position": s.net_position,
                    "pnl": s.realized_pnl,
                    "grid_step": s.grid_step,
                    "grid_levels": s.grid_levels,
                    "max_position": s.max_position,
                    "fills": s.session_fills,
                    "toxic": s.toxic_hits,
                    "spread": s.spread,
                    "obi": s.l1_confidence,
                    "fear_greed": s.fear_greed,
                    "macro_bias": s.macro_bias,
                    "regime": s.l2_regime,
                    "paused": s.paused,
                    "shadow": s.shadow_mode,
                    "t2t_micros": s.t2t_micros,
                    // PnL from FIFO
                    "pnl_1h": s.pnl_1h,
                    "pnl_24h": s.pnl_24h,
                    "pnl_7d": s.pnl_7d,
                    "pnl_30d": s.pnl_30d,
                    "fills_24h": s.fills_24h_fifo,
                    "closed_trades_24h": s.closed_trades_24h,
                })
            }).collect();

            let gpu_alive = std::process::Command::new("curl")
                .args(["-s", "--max-time", "1", "http://localhost:1234/v1/models"])
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false);

            UdsResponse::ok_with_data(serde_json::json!({
                "bots": bots,
                "gpu_status": if gpu_alive { "ONLINE" } else { "OFFLINE" },
                "uptime_s": uptime,
                "timestamp_ms": epoch_ms(),
            }))
        }

        "SET_GRID" => {
            let Some(value) = req.value else {
                return UdsResponse::err("Missing 'value' field");
            };
            let clamped = value.clamp(GRID_FLOOR, GRID_CEIL);
            let bot_name = req.bot.as_deref().unwrap_or("hydra");
            let mem = memory.read().await;
            let Some(risk) = mem.risk_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            let prev_raw = risk.grid_step.load(Ordering::SeqCst);
            let prev = prev_raw as f64 / PRICE_SCALE;
            let scaled = (clamped * PRICE_SCALE) as u64;
            risk.grid_step.store(scaled, Ordering::SeqCst);
            println!("  🔌 [UDS] SET_GRID [{bot_name}]: ${prev:.2} → ${clamped:.2}");
            UdsResponse::ok_with_prev(prev)
        }

        "SET_MAXPOS" => {
            let Some(value) = req.value else {
                return UdsResponse::err("Missing 'value' field");
            };
            let clamped = value.clamp(MAX_POS_FLOOR, MAX_POS_CEIL);
            let bot_name = req.bot.as_deref().unwrap_or("hydra");
            let mem = memory.read().await;
            let Some(risk) = mem.risk_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            let prev_raw = risk.max_inv_delta.load(Ordering::SeqCst);
            let prev = prev_raw as f64 / PRICE_SCALE;
            let scaled = (clamped * PRICE_SCALE) as u64;
            risk.max_inv_delta.store(scaled, Ordering::SeqCst);
            println!("  🔌 [UDS] SET_MAXPOS [{bot_name}]: {prev:.6} → {clamped:.6} BTC");
            UdsResponse::ok_with_prev(prev)
        }

        "SET_REGIME" => {
            let Some(ref regime) = req.regime else {
                return UdsResponse::err("Missing 'regime' field");
            };
            let regime_id: u64 = match regime.to_uppercase().as_str() {
                "TRENDING" | "BULLISH_TREND" => 1,
                "RANGING" | "CHOPPING_RANGE" => 2,
                "CHAOS" | "BEARISH_SHOCK" => 3,
                _ => 0,
            };
            let bot_name = req.bot.as_deref().unwrap_or("hydra");
            let mem = memory.read().await;
            let Some(engine) = mem.engine_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            engine.l2_regime_id.store(regime_id, Ordering::Release);
            let now = epoch_ms();
            engine.l2_last_action_ms.store(now, Ordering::Release);
            engine.ai_heartbeat_ms.store(now, Ordering::Release);
            engine.ai_registry_version.fetch_add(1, Ordering::Release);
            println!("  🔌 [UDS] SET_REGIME [{bot_name}]: {regime} (id={regime_id})");
            UdsResponse::ok()
        }

        "PAUSE" => {
            let bot_name = req.bot.as_deref().unwrap_or("hydra");
            let mem = memory.read().await;
            let Some(risk) = mem.risk_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            risk.paused.store(1, Ordering::SeqCst);
            println!("  🔌 [UDS] PAUSE: {bot_name} paused");
            UdsResponse::ok()
        }

        "UNPAUSE" => {
            let bot_name = req.bot.as_deref().unwrap_or("hydra");
            let mem = memory.read().await;
            let Some(risk) = mem.risk_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            risk.paused.store(0, Ordering::SeqCst);
            println!("  🔌 [UDS] UNPAUSE: {bot_name} unpaused");
            UdsResponse::ok()
        }

        "GET_GPU_STATS" => {
            let stats = crate::gpu::get_gpu_stats();
            UdsResponse::ok_with_data(stats.to_json())
        }

        _ => UdsResponse::err(&format!("Unknown command: {}", req.cmd)),
    }
}

fn epoch_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
