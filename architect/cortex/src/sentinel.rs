// ═══════════════════════════════════════════════════════════
// 🛡️ SOVEREIGN CORTEX — Sentinel Module (4-Layer Guardian)
//
// Layer 1: Mmap Watchdog  — heartbeat check on all bots (1s loop)
// Layer 2: AI Timeout     — tracks L1/L2 responsiveness
// Layer 3: Business Logic — PnL anomaly, toxic spike, position drift
// Layer 4: Boot Alert     — startup notification + version info
//
// All alerts pushed to Python Commander via /tmp/commander_events.sock.
// ═══════════════════════════════════════════════════════════

use crate::memory::ArmadaMemory;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::AsyncWriteExt;
use tokio::net::UnixStream;
use tokio::sync::RwLock;
use tokio::time;

const EVENT_SOCKET: &str = "/tmp/commander_events.sock";

/// Fire-and-forget alert push to Python Commander via UDS.
async fn push_alert(level: &str, msg: &str) {
    let safe_msg = msg.replace('"', "'").replace('\n', " | ");
    let payload = format!(
        "{{\"cmd\":\"ALERT\",\"level\":\"{level}\",\"msg\":\"{safe_msg}\"}}\n"
    );
    match UnixStream::connect(EVENT_SOCKET).await {
        Ok(mut stream) => { let _ = stream.write_all(payload.as_bytes()).await; }
        Err(_) => { eprintln!("  ⚠️ [Sentinel] Commander offline. Alert dropped."); }
    }
}

// ── Thresholds ──
const MMAP_STALE_SECS: u64 = 5;       // Bot heartbeat timeout
const PNL_CRASH_THRESHOLD: f64 = -5.0; // Emergency PnL drop per check
const TOXIC_SPIKE_LIMIT: u64 = 1000;   // Toxic fill threshold
const POSITION_DRIFT_BTC: f64 = 0.01;  // Max inventory before alert
const SPREAD_EXPLOSION_MULT: f64 = 3.0;// Spread > 3x grid step
const SENTINEL_INTERVAL_SECS: u64 = 5; // Check every 5 seconds
const COOLDOWN_SECS: u64 = 900;        // 15 min anti-spam per alert type

// ── History for delta detection ──
struct SentinelHistory {
    prev_pnl: f64,
    prev_toxic: u64,
    prev_fills: u64,
    zero_fill_cycles: u64,
    last_alerts: HashMap<String, Instant>,
}

impl SentinelHistory {
    fn new() -> Self {
        Self {
            prev_pnl: 0.0,
            prev_toxic: 0,
            prev_fills: 0,
            zero_fill_cycles: 0,
            last_alerts: HashMap::new(),
        }
    }

    /// Check anti-spam cooldown. Returns true if alert should be sent.
    fn should_alert(&mut self, alert_type: &str) -> bool {
        let now = Instant::now();
        if let Some(last) = self.last_alerts.get(alert_type) {
            if now.duration_since(*last).as_secs() < COOLDOWN_SECS {
                return false;
            }
        }
        self.last_alerts.insert(alert_type.to_string(), now);
        true
    }
}

// ═══════════════════════════════════════════════════════════
// LAYER 4: Boot Alert — called once at startup
// ═══════════════════════════════════════════════════════════

pub async fn send_boot_alert() {
    let hostname = std::fs::read_to_string("/etc/hostname")
        .unwrap_or_else(|_| "unknown".into())
        .trim()
        .to_string();

    let now = epoch_secs();
    let hours = ((now + 3600) % 86400) / 3600;
    let mins = ((now + 3600) % 3600) / 60;

    let msg = format!(
        "🟢 SOVEREIGN CORTEX v14.0 BOOT | {hostname} | {hours:02}:{mins:02} CET | Sentinel ONLINE (5 vrstev) | Pokud vidis v noci = server restart!"
    );

    push_alert("INFO", &msg).await;
}

// ═══════════════════════════════════════════════════════════
// LAYERS 1-3: Main Sentinel Loop
// ═══════════════════════════════════════════════════════════

pub async fn run_sentinel(memory: Arc<RwLock<ArmadaMemory>>) {
    println!("🛡️ [SENTINEL] Online — checking every {}s", SENTINEL_INTERVAL_SECS);
    println!("   Thresholds: mmap={}s pnl=${} toxic={} pos={}BTC spread={}x",
        MMAP_STALE_SECS, PNL_CRASH_THRESHOLD, TOXIC_SPIKE_LIMIT,
        POSITION_DRIFT_BTC, SPREAD_EXPLOSION_MULT);

    let mut history = SentinelHistory::new();
    let mut first_run = true;
    let mut cycle_count: u64 = 0;

    loop {
        time::sleep(Duration::from_secs(SENTINEL_INTERVAL_SECS)).await;

        let snapshots = {
            let mem = memory.read().await;
            mem.snapshot_all()
        };

        let mut alerts: Vec<String> = Vec::new();

        for snap in &snapshots {
            if !snap.online {
                continue; // Skip offline bots
            }

            // ── LAYER 1: Mmap Heartbeat ──
            // latency_ns is written every 1s by the bot's background task.
            // If it's 0 or we can detect staleness from t2t_micros timestamp
            // We use the health writer cycle (5s cortex_state.json) as cross-check.
            // A bot that's "online" but has latency_ns=0 is suspicious.
            if snap.t2t_micros == 0 && snap.session_fills > 100 {
                // Bot has fills but T2T is 0 → stale or frozen
                if history.should_alert(&format!("{}_frozen", snap.name)) {
                    let msg = format!(
                        "🚨 SENTINEL: {} {} ZAMRZLA\n\
                         T2T latence = 0 po {} fillech\n\
                         Bot bezi ale neobchoduje!",
                        snap.emoji, snap.name.to_uppercase(), snap.session_fills
                    );
                    alerts.push(msg);
                }
            }

            // ── LAYER 3a: Position Drift ──
            if snap.net_position.abs() > POSITION_DRIFT_BTC {
                if history.should_alert(&format!("{}_drift", snap.name)) {
                    let msg = format!(
                        "⚠️ SENTINEL: {} {} INVENTORY DRIFT\n\
                         📦 Pozice: {:.5} BTC (limit {:.3})\n\
                         Riziko expozice!",
                        snap.emoji, snap.name.to_uppercase(),
                        snap.net_position, POSITION_DRIFT_BTC
                    );
                    alerts.push(msg);
                }
            }

            // ── LAYER 3b: Spread Explosion ──
            if snap.grid_step > 0.0 && snap.spread > snap.grid_step * SPREAD_EXPLOSION_MULT {
                if history.should_alert(&format!("{}_spread", snap.name)) {
                    let msg = format!(
                        "⚡ SENTINEL: {} {} SPREAD EXPLOZE\n\
                         Spread: ${:.2} > {:.0}x Grid ${:.2}\n\
                         Mozny flash crash!",
                        snap.emoji, snap.name.to_uppercase(),
                        snap.spread, SPREAD_EXPLOSION_MULT, snap.grid_step
                    );
                    alerts.push(msg);
                }
            }

            // ── LAYER 3c: Toxic Spike ──
            if snap.toxic_hits > TOXIC_SPIKE_LIMIT {
                if history.should_alert(&format!("{}_toxic", snap.name)) {
                    let msg = format!(
                        "☠️ SENTINEL: {} {} TOXIC SPIKE\n\
                         Toxic fills: {} > limit {}\n\
                         Zvaz PAUSE!",
                        snap.emoji, snap.name.to_uppercase(),
                        snap.toxic_hits, TOXIC_SPIKE_LIMIT
                    );
                    alerts.push(msg);
                }
            }
        }

        // ── LAYER 3d: PnL Crash Detection (across all bots) ──
        if !first_run {
            let total_pnl: f64 = snapshots.iter().map(|s| s.realized_pnl).sum();
            let pnl_delta = total_pnl - history.prev_pnl;

            if pnl_delta < PNL_CRASH_THRESHOLD {
                if history.should_alert("pnl_crash") {
                    let msg = format!(
                        "🚨 SENTINEL: VELKA ZTRATA\n\
                         ━━━━━━━━━━━━━━━━━━━\n\
                         💸 PnL delta: ${pnl_delta:.2} za {}s\n\
                         📊 Celkove PnL: ${total_pnl:.4}\n\
                         ⚠️ Zvaz PAUSE vsech botu!",
                        SENTINEL_INTERVAL_SECS
                    );
                    alerts.push(msg);
                }
            }

            // ── LAYER 3e: Zero Activity (1 hour of no fills) ──
            let total_fills: u64 = snapshots.iter().map(|s| s.session_fills).sum();
            let fills_delta = total_fills.saturating_sub(history.prev_fills);
            if fills_delta == 0 && history.prev_fills > 0 {
                history.zero_fill_cycles += 1;
                // 720 cycles × 5s = 1 hour without any fill
                if history.zero_fill_cycles >= 720 {
                    if history.should_alert("zero_activity") {
                        let online = snapshots.iter().filter(|s| s.online).count();
                        if online > 0 {
                            let hours = history.zero_fill_cycles * SENTINEL_INTERVAL_SECS / 3600;
                            let msg = format!(
                                "⚠️ SENTINEL: ZADNA AKTIVITA\n\
                                 {online} botu online ale 0 novych fillu za {hours}h+\n\
                                 Mozny problem s WS konekci!"
                            );
                            alerts.push(msg);
                        }
                    }
                    history.zero_fill_cycles = 0; // Reset after alert
                }
            } else {
                history.zero_fill_cycles = 0; // Reset on any fill
            }

            history.prev_pnl = total_pnl;
            history.prev_fills = total_fills;
            history.prev_toxic = snapshots.iter().map(|s| s.toxic_hits).sum();
        } else {
            // Initialize history on first run
            history.prev_pnl = snapshots.iter().map(|s| s.realized_pnl).sum();
            history.prev_fills = snapshots.iter().map(|s| s.session_fills).sum();
            history.prev_toxic = snapshots.iter().map(|s| s.toxic_hits).sum();
            first_run = false;
        }

        // ── Send alerts ──
        for alert in &alerts {
            eprintln!("  🚨 [SENTINEL] {}", alert.lines().next().unwrap_or(""));
            push_alert("WARNING", alert).await;
            // Small delay between alerts to avoid rate limiting
            time::sleep(Duration::from_millis(500)).await;
        }

        // ═══ LAYER 5: System Resources (every 60s = 12th cycle) ═══
        if cycle_count % 12 == 0 {
            check_system_resources(&mut history).await;
        }
        cycle_count += 1;
    }
}

// ═══════════════════════════════════════════════════════════
// LAYER 2: AI Timeout Guard (integrated into gpu.rs/l2.rs)
// GPU call timeout is enforced in gpu.rs via ureq timeout.
// L2 Gemini timeout via subprocess with 30s limit.
// This function checks LM Studio liveness from Sentinel.
// ═══════════════════════════════════════════════════════════

async fn check_lm_studio(history: &mut SentinelHistory) {
    let result = tokio::task::spawn_blocking(|| {
        let agent = ureq::Agent::config_builder()
            .timeout_global(Some(std::time::Duration::from_secs(3)))
            .build()
            .new_agent();
        agent.get("http://localhost:1234/v1/models")
            .call()
            .is_ok()
    }).await;

    let alive = result.unwrap_or(false);
    if !alive && history.should_alert("lms_down") {
        let msg = "⚠️ SENTINEL: LM Studio OFFLINE\n\
                   GPU inference na :1234 neodpovida\n\
                   L1 bezi v fallback modu (bez AI)";
        push_alert("WARNING", msg).await;
    }
}

// ═══════════════════════════════════════════════════════════
// LAYER 5: System Resources (GPU temp, VRAM, disk, RAM, net)
// ═══════════════════════════════════════════════════════════

async fn check_system_resources(history: &mut SentinelHistory) {
    // ── GPU Temperature ──
    if let Some((temp, mem_used, mem_total)) = gpu_stats() {
        if temp > 85 && history.should_alert("gpu_hot") {
            let msg = format!(
                "🌡️ SENTINEL: GPU OVERHEATING\n\
                 Teplota: {temp}°C > 85°C limit\n\
                 VRAM: {mem_used}/{mem_total} MB"
            );
            push_alert("FATAL", &msg).await;
        }
        let pct = if mem_total > 0 { mem_used * 100 / mem_total } else { 0 };
        if pct > 95 && history.should_alert("gpu_vram") {
            let msg = format!(
                "💾 SENTINEL: GPU VRAM CRITICAL\n\
                 {mem_used}/{mem_total} MB ({pct}%)"
            );
            push_alert("FATAL", &msg).await;
        }
    }

    // ── Disk Usage ──
    if let Some(pct) = disk_usage_pct() {
        if pct > 90 && history.should_alert("disk_full") {
            let msg = format!(
                "💾 SENTINEL: DISK CRITICAL\n\
                 Pouziti: {pct}% > 90%"
            );
            push_alert("WARNING", &msg).await;
        }
    }

    // ── RAM Usage ──
    if let Some(pct) = ram_usage_pct() {
        if pct > 90 && history.should_alert("ram_full") {
            let msg = format!(
                "💾 SENTINEL: RAM CRITICAL\n\
                 Pouziti: {pct}% > 90%"
            );
            push_alert("WARNING", &msg).await;
        }
    }

    // ── LM Studio ──
    check_lm_studio(history).await;

    // ── Network (Bitfinex) ──
    let net_ok = tokio::task::spawn_blocking(|| {
        std::process::Command::new("ping")
            .args(["-c1", "-W2", "api.bitfinex.com"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }).await.unwrap_or(false);

    if !net_ok && history.should_alert("network_down") {
        let msg = "🌐 SENTINEL: NETWORK DOWN\n\
                   api.bitfinex.com nedostupne!\n\
                   WS konekce pravdepodobne padla";
        push_alert("WARNING", msg).await;
    }
}

// ── System helpers (pure Rust, no bash) ──

fn gpu_stats() -> Option<(u64, u64, u64)> {
    let output = std::process::Command::new("nvidia-smi")
        .args(["--query-gpu=temperature.gpu,memory.used,memory.total",
               "--format=csv,noheader,nounits"])
        .output()
        .ok()?;
    if !output.status.success() { return None; }
    let s = String::from_utf8_lossy(&output.stdout);
    let parts: Vec<&str> = s.trim().split(',').map(|p| p.trim()).collect();
    if parts.len() >= 3 {
        Some((
            parts[0].parse().unwrap_or(0),
            parts[1].parse().unwrap_or(0),
            parts[2].parse().unwrap_or(0),
        ))
    } else {
        None
    }
}

fn disk_usage_pct() -> Option<u64> {
    let output = std::process::Command::new("df")
        .args(["/", "--output=pcent"])
        .output()
        .ok()?;
    let s = String::from_utf8_lossy(&output.stdout);
    for line in s.lines().skip(1) {
        let pct: u64 = line.trim().trim_end_matches('%').parse().ok()?;
        return Some(pct);
    }
    None
}

fn ram_usage_pct() -> Option<u64> {
    let content = std::fs::read_to_string("/proc/meminfo").ok()?;
    let mut total = 0u64;
    let mut available = 0u64;
    for line in content.lines() {
        if line.starts_with("MemTotal:") {
            total = line.split_whitespace().nth(1)?.parse().ok()?;
        } else if line.starts_with("MemAvailable:") {
            available = line.split_whitespace().nth(1)?.parse().ok()?;
        }
    }
    if total > 0 {
        Some(((total - available) * 100) / total)
    } else {
        None
    }
}

fn epoch_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
