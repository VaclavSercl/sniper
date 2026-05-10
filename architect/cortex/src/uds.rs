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

use crate::memory::{ArmadaMemory, enrich_with_pnl, BotEngine, BotRisk};
use serde::{Deserialize, Serialize};
use sniper_types::PRICE_SCALE;
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use bytes::BytesMut;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixListener;

const SOCKET_PATH: &str = "/tmp/cortex.sock";

// Safety clamps (same as l2.rs)
const GRID_FLOOR: f64 = 2.0;
const GRID_CEIL: f64 = 50.0;
const MAX_POS_FLOOR: f64 = 0.001;
const MAX_POS_CEIL: f64 = 0.02;

// ── Request / Response types ──

#[derive(Deserialize)]
struct UdsRequest<'a> {
    cmd: &'a str,
    #[serde(default)]
    bot: Option<&'a str>,
    #[serde(default)]
    value: Option<f64>,
    #[serde(default)]
    regime: Option<&'a str>,
    // L1 tuning (SET_L1_TUNING)
    #[serde(default)]
    skew_max_usd: Option<f64>,
    #[serde(default)]
    obi_threshold: Option<f64>,
    #[serde(default)]
    inference_interval_ms: Option<u64>,
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

pub async fn run_uds_server(memory: Arc<ArmadaMemory>) {
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
            let mut stream = stream;
            let mut buf = BytesMut::with_capacity(1024);
            
            // ZERO-COPY: Čtení do statického bufferu
            let _ = stream.read_buf(&mut buf).await;
            if buf.is_empty() { return; }

            let response = match serde_json::from_slice::<UdsRequest>(&buf) {
                Ok(req) => handle_request(req, &mem, uptime).await,
                Err(e) => UdsResponse::err(&format!("Invalid JSON slice: {e}")),
            };

            // ZERO-COPY: Přímý zápis na stream přes vec
            let mut out_buf = Vec::with_capacity(2048);
            if serde_json::to_writer(&mut out_buf, &response).is_ok() {
                out_buf.push(b'\n');
                let _ = stream.write_all(&out_buf).await;
            }
        });
    }
}

// ═══════════════════════════════════════════════════════════
// Command dispatcher
// ═══════════════════════════════════════════════════════════

async fn handle_request<'a>(
    req: UdsRequest<'a>,
    memory: &Arc<ArmadaMemory>,
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
            let mem = &**memory;
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
                    // Wallet
                    "wallet_btc": s.wallet_btc,
                    "wallet_usd": s.wallet_usd,
                })
            }).collect();

            // Aggregate wallet from Hydra (primary bot with WS connection)
            let hydra = &snapshots[0];
            let btc_price = if hydra.micro_price > 100.0 { hydra.micro_price } else { 0.0 };
            let wallet_total_usd = hydra.wallet_usd + hydra.wallet_btc * btc_price;

            // GPU liveness: check heartbeat from mmap (zero-fork, zero-cost)
            let gpu_alive = {
                let hydra = mem.hydra_engine();
                let hb = hydra.ai_heartbeat_ms.load(Ordering::Relaxed);
                hb > 0 && epoch_ms().saturating_sub(hb) < 30_000
            };

            UdsResponse::ok_with_data(serde_json::json!({
                "bots": bots,
                "gpu_status": if gpu_alive { "ONLINE" } else { "OFFLINE" },
                "uptime_s": uptime,
                "timestamp_ms": epoch_ms(),
                "wallet": {
                    "usd": hydra.wallet_usd,
                    "btc": hydra.wallet_btc,
                    "btc_price": btc_price,
                    "total_usd": wallet_total_usd,
                },
            }))
        }

        "SET_GRID" => {
            let Some(value) = req.value else {
                return UdsResponse::err("Missing 'value' field");
            };
            let clamped = value.clamp(GRID_FLOOR, GRID_CEIL);
            let bot_name = req.bot.unwrap_or("hydra");
            let mem = &**memory;
            let Some(risk) = mem.risk_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            let scaled = (clamped * PRICE_SCALE) as u64;
            let prev = match risk {
                BotRisk::Hydra(r) => {
                    let p = r.grid_step.swap(scaled, Ordering::SeqCst);
                    p as f64 / PRICE_SCALE
                }
                BotRisk::Grid(r) => {
                    let p = r.grid_spacing.swap(scaled, Ordering::SeqCst);
                    p as f64 / PRICE_SCALE
                }
                _ => return UdsResponse::err("Setting grid step is unsupported for this bot mode"),
            };
            println!("  🔌 [UDS] SET_GRID [{bot_name}]: ${prev:.2} → ${clamped:.2}");
            UdsResponse::ok_with_prev(prev)
        }

        "SET_MAXPOS" => {
            let Some(value) = req.value else {
                return UdsResponse::err("Missing 'value' field");
            };
            let clamped = value.clamp(MAX_POS_FLOOR, MAX_POS_CEIL);
            let bot_name = req.bot.unwrap_or("hydra");
            let mem = &**memory;
            let Some(risk) = mem.risk_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            let scaled = (clamped * PRICE_SCALE) as u64;
            let prev = match risk {
                BotRisk::Hydra(r) => {
                    let p = r.max_inv_delta.swap(scaled, Ordering::SeqCst);
                    p as f64 / PRICE_SCALE
                }
                // Fallback for safety - we primarily only want to adjust Hydra's max pos via L2 AI right now
                _ => return UdsResponse::err("Setting max position is restricted to Hydra for safety"),
            };
            println!("  🔌 [UDS] SET_MAXPOS [{bot_name}]: {prev:.6} → {clamped:.6} BTC");
            UdsResponse::ok_with_prev(prev)
        }

        "SET_REGIME" => {
            let Some(regime) = req.regime else {
                return UdsResponse::err("Missing 'regime' field");
            };
            let regime_id: u64 = match regime.to_uppercase().as_str() {
                "TRENDING" | "BULLISH_TREND" => 1,
                "RANGING" | "CHOPPING_RANGE" => 2,
                "CHAOS" | "BEARISH_SHOCK" => 3,
                _ => 0,
            };
            let bot_name = req.bot.unwrap_or("hydra");
            let mem = &**memory;
            let Some(engine) = mem.engine_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            let now = epoch_ms();
            if let BotEngine::Hydra(h) = engine {
                h.l2_regime_id.store(regime_id, Ordering::Release);
                h.l2_last_action_ms.store(now, Ordering::Release);
                h.ai_heartbeat_ms.store(now, Ordering::Release);
                h.ai_registry_version.fetch_add(1, Ordering::Release);
            }
            println!("  🔌 [UDS] SET_REGIME [{bot_name}]: {regime} (id={regime_id})");
            UdsResponse::ok()
        }

        "PAUSE" => {
            let bot_name = req.bot.unwrap_or("hydra");
            let mem = &**memory;
            let Some(risk) = mem.risk_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            match risk {
                BotRisk::Hydra(r) => r.paused.store(1, Ordering::SeqCst),
                BotRisk::Moonshot(r) => r.global_paused.store(1, Ordering::SeqCst),
                BotRisk::Grid(r) => r.global_paused.store(1, Ordering::SeqCst),
                BotRisk::Trigon(r) => r.global_paused.store(1, Ordering::SeqCst),
                BotRisk::Nexus(r) => r.emergency_pause.store(1, Ordering::SeqCst),
            }
            println!("  🔌 [UDS] PAUSE: {bot_name} paused");
            UdsResponse::ok()
        }

        "UNPAUSE" => {
            let bot_name = req.bot.unwrap_or("hydra");
            let mem = &**memory;
            let Some(risk) = mem.risk_for_bot(bot_name) else {
                return UdsResponse::err(&format!("Bot '{bot_name}' not found or offline"));
            };
            match risk {
                BotRisk::Hydra(r) => r.paused.store(0, Ordering::SeqCst),
                BotRisk::Moonshot(r) => r.global_paused.store(0, Ordering::SeqCst),
                BotRisk::Grid(r) => r.global_paused.store(0, Ordering::SeqCst),
                BotRisk::Trigon(r) => r.global_paused.store(0, Ordering::SeqCst),
                BotRisk::Nexus(r) => r.emergency_pause.store(0, Ordering::SeqCst),
            }
            println!("  🔌 [UDS] UNPAUSE: {bot_name} unpaused");
            UdsResponse::ok()
        }

        "GET_GPU_STATS" => {
            let stats = crate::gpu::get_gpu_stats();
            UdsResponse::ok_with_data(stats.to_json())
        }

        "SET_L1_TUNING" => {
            let skew = req.skew_max_usd.unwrap_or(3.0).clamp(0.5, 5.0);
            let obi = req.obi_threshold.unwrap_or(0.0).clamp(0.0, 0.8);
            let interval = req.inference_interval_ms.unwrap_or(2000).clamp(500, 10000);
            crate::gpu::set_l1_tuning(skew, obi, interval);
            println!("  🔌 [UDS] SET_L1_TUNING: skew_max=${skew:.1} obi_thr={obi:.2} interval={interval}ms");
            UdsResponse::ok_with_data(serde_json::json!({
                "skew_max_usd": skew,
                "obi_threshold": obi,
                "inference_interval_ms": interval,
            }))
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
