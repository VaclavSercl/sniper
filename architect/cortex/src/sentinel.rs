// ═══════════════════════════════════════════════════════════
// 🛡️ SOVEREIGN CORTEX — Sentinel Module (4-Layer Guardian)
//
// Layer 1: Mmap Watchdog  — heartbeat check on all bots (1s loop)
// Layer 2: AI Timeout     — tracks L1/L2 responsiveness
// Layer 3: Business Logic — PnL anomaly, toxic spike, position drift
// Layer 4: Boot Alert     — startup notification + version info
// Layer 5: System Resources — GPU temp, VRAM, disk, RAM, net
// Layer 6: Regime Sentinel — flash crash, liquidity drain, sweep storm
//
// Writes regime_alert to /dev/shm/beroun/toxic_storm.bin (shared with ML Shield).
// All alerts pushed to Python Commander via /tmp/commander_events.sock.
// ═══════════════════════════════════════════════════════════

use crate::memory::ArmadaMemory;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::AsyncWriteExt;
use tokio::net::UnixStream;
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
const POSITION_DRIFT_BTC: f64 = 0.01;  // Max inventory before alert
const SPREAD_EXPLOSION_MULT: f64 = 3.0;// Spread > 3x grid step
const SENTINEL_INTERVAL_SECS: u64 = 5; // Check every 5 seconds
const COOLDOWN_SECS: u64 = 900;        // 15 min anti-spam per alert type
const SUSTAINED_THRESHOLD: u64 = 60;   // 60 × 5s = 5 min sustained before alert
const CRITICAL_PCT: f64 = 95.0;        // Resource critical threshold

// ── Regime Sentinel (Layer 6) ──
const HIVE_MIND_PATH: &str = "/dev/shm/beroun/toxic_storm.bin";
const FLASH_CRASH_PCT: f64 = 0.005;    // 0.5% price drop = flash crash
const FLASH_CRASH_WINDOW: usize = 6;    // 6 × 5s = 30s window
const SWEEP_STORM_COUNT: usize = 3;     // 3 spread explosions in window
const REGIME_CALM_CYCLES: u64 = 12;     // 12 × 5s = 60s calm = deactivate

// ── History for delta detection ──
struct SentinelHistory {
    prev_pnl: f64,
    prev_toxic: u64,
    prev_fills: u64,
    zero_fill_cycles: u64,
    last_alerts: HashMap<String, Instant>,
    // Per-bot delta tracking for toxic rate
    prev_toxic_per_bot: HashMap<String, u64>,
    prev_fills_per_bot: HashMap<String, u64>,
    // Sustained resource monitoring
    cpu_high_cycles: u64,
    ram_high_cycles: u64,
    gpu_temp_high_cycles: u64,
    vram_high_cycles: u64,
    disk_high_cycles: u64,
    prev_cpu_idle: u64,
    prev_cpu_total: u64,
    // Layer 6: Regime detection
    price_ring: Vec<f64>,               // ring buffer of mid prices
    price_ring_idx: usize,
    spread_explosion_ring: Vec<bool>,    // ring of spread explosion events
    spread_ring_idx: usize,
    regime_calm_counter: u64,
    regime_active: bool,
}

impl SentinelHistory {
    fn new() -> Self {
        Self {
            prev_pnl: 0.0,
            prev_toxic: 0,
            prev_fills: 0,
            zero_fill_cycles: 0,
            last_alerts: HashMap::new(),
            prev_toxic_per_bot: HashMap::new(),
            prev_fills_per_bot: HashMap::new(),
            cpu_high_cycles: 0,
            ram_high_cycles: 0,
            gpu_temp_high_cycles: 0,
            vram_high_cycles: 0,
            disk_high_cycles: 0,
            prev_cpu_idle: 0,
            prev_cpu_total: 0,
            // Layer 6
            price_ring: vec![0.0; FLASH_CRASH_WINDOW + 1],
            price_ring_idx: 0,
            spread_explosion_ring: vec![false; FLASH_CRASH_WINDOW + 1],
            spread_ring_idx: 0,
            regime_calm_counter: REGIME_CALM_CYCLES,
            regime_active: false,
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

pub async fn run_sentinel(memory: Arc<ArmadaMemory>) {
    println!("🛡️ [SENTINEL] Online — checking every {}s", SENTINEL_INTERVAL_SECS);
    println!("   Thresholds: mmap={}s pnl=${} toxic_rate=60% pos={}BTC spread={}x",
        MMAP_STALE_SECS, PNL_CRASH_THRESHOLD,
        POSITION_DRIFT_BTC, SPREAD_EXPLOSION_MULT);

    let mut history = SentinelHistory::new();
    let mut first_run = true;
    let mut cycle_count: u64 = 0;

    loop {
        time::sleep(Duration::from_secs(SENTINEL_INTERVAL_SECS)).await;

        let snapshots = {
            let mem = &*memory;
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

            // ── LAYER 3c: Toxic Rate Alert (delta-based) ──
            // Track toxic RATE (delta) not absolute counter.
            // Alert when >60% of new fills in this interval are toxic.
            // Skip on first_run to avoid false alarm from cumulative counters.
            if !first_run {
                let prev_toxic_for_bot = *history.prev_toxic_per_bot
                    .get(snap.name).unwrap_or(&0);
                let toxic_delta = snap.toxic_hits.saturating_sub(prev_toxic_for_bot);
                let prev_fills_for_bot = *history.prev_fills_per_bot
                    .get(snap.name).unwrap_or(&0);
                let fills_delta = snap.session_fills.saturating_sub(prev_fills_for_bot);

                // Only alert if meaningful activity (>20 new fills) AND high toxic rate (>60%)
                if fills_delta > 20 && toxic_delta > 0 {
                    let toxic_rate = toxic_delta as f64 / fills_delta as f64;
                    if toxic_rate > 0.60 {
                        if history.should_alert(&format!("{}_toxic", snap.name)) {
                            let msg = format!(
                                "☠️ SENTINEL: {} {} TOXIC RATE HIGH\n\
                                 Toxic: {} / {} fills ({:.0}%) za posledni interval\n\
                                 Celkem toxic: {} | Rate > 60%",
                                snap.emoji, snap.name.to_uppercase(),
                                toxic_delta, fills_delta,
                                toxic_rate * 100.0,
                                snap.toxic_hits
                            );
                            alerts.push(msg);
                        }
                    }
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
            // Per-bot tracking for delta-based toxic rate
            for snap in &snapshots {
                history.prev_toxic_per_bot.insert(snap.name.to_string(), snap.toxic_hits);
                history.prev_fills_per_bot.insert(snap.name.to_string(), snap.session_fills);
            }
        } else {
            // Initialize history on first run
            history.prev_pnl = snapshots.iter().map(|s| s.realized_pnl).sum();
            history.prev_fills = snapshots.iter().map(|s| s.session_fills).sum();
            history.prev_toxic = snapshots.iter().map(|s| s.toxic_hits).sum();
            for snap in &snapshots {
                history.prev_toxic_per_bot.insert(snap.name.to_string(), snap.toxic_hits);
                history.prev_fills_per_bot.insert(snap.name.to_string(), snap.session_fills);
            }
            first_run = false;
        }

        // ── Send alerts ──
        for alert in &alerts {
            eprintln!("  🚨 [SENTINEL] {}", alert.lines().next().unwrap_or(""));
            push_alert("WARNING", alert).await;
            // Small delay between alerts to avoid rate limiting
            time::sleep(Duration::from_millis(500)).await;
        }

        // ═══ LAYER 6: Regime Sentinel (flash crash, liquidity drain, sweep) ═══
        {
            // Get reference price from first online bot
            let current_price = snapshots.iter()
                .find(|s| s.online && s.micro_price > 0.0)
                .map(|s| s.micro_price)
                .unwrap_or(0.0);

            if current_price > 0.0 {
                // Store price in ring buffer
                history.price_ring[history.price_ring_idx] = current_price;
                history.price_ring_idx = (history.price_ring_idx + 1) % history.price_ring.len();

                // Check for flash crash: price drop > FLASH_CRASH_PCT in FLASH_CRASH_WINDOW
                let oldest_idx = history.price_ring_idx; // oldest entry
                let oldest_price = history.price_ring[oldest_idx];
                let mut flash_crash = false;
                if oldest_price > 0.0 {
                    let drop_pct = (oldest_price - current_price) / oldest_price;
                    if drop_pct > FLASH_CRASH_PCT {
                        flash_crash = true;
                        if history.should_alert("flash_crash") {
                            let msg = format!(
                                "🔴 REGIME SENTINEL: FLASH CRASH DETECTED\n\
                                 ━━━━━━━━━━━━━━━━━━━━━━\n\
                                 💥 Price: ${oldest_price:.2} → ${current_price:.2} ({:.2}% drop in {}s)\n\
                                 🛡️ Hive Mind ACTIVATED — all bots defensive",
                                drop_pct * 100.0, FLASH_CRASH_WINDOW * SENTINEL_INTERVAL_SECS as usize
                            );
                            alerts.push(msg);
                        }
                    }
                }

                // Check spread explosion (liquidity drain)
                let spread_exploded = snapshots.iter().any(|s| {
                    s.online && s.grid_step > 0.0 && s.spread > s.grid_step * 5.0
                });
                history.spread_explosion_ring[history.spread_ring_idx] = spread_exploded;
                history.spread_ring_idx = (history.spread_ring_idx + 1) % history.spread_explosion_ring.len();

                // Sweep storm: count spread explosions in window
                let sweep_count = history.spread_explosion_ring.iter().filter(|&&x| x).count();
                let sweep_storm = sweep_count >= SWEEP_STORM_COUNT;

                // Composite regime alert
                if flash_crash || sweep_storm {
                    history.regime_calm_counter = 0;
                    if !history.regime_active {
                        history.regime_active = true;
                        // Write to Hive Mind mmap
                        if let Ok(mut f) = std::fs::OpenOptions::new().write(true).open(HIVE_MIND_PATH) {
                            use std::io::Write;
                            let _ = f.write_all(&[1u8]);
                        }
                        if sweep_storm && !flash_crash {
                            if history.should_alert("sweep_storm") {
                                let msg = format!(
                                    "⚡ REGIME SENTINEL: SWEEP STORM\n\
                                     ━━━━━━━━━━━━━━━━━━━━━━\n\
                                     🌩️ {sweep_count} spread explosions in {}s\n\
                                     🛡️ Hive Mind ACTIVATED — liquidity drain detected",
                                    FLASH_CRASH_WINDOW * SENTINEL_INTERVAL_SECS as usize
                                );
                                alerts.push(msg);
                            }
                        }
                    }
                } else {
                    history.regime_calm_counter += 1;
                    if history.regime_calm_counter >= REGIME_CALM_CYCLES && history.regime_active {
                        history.regime_active = false;
                        // Clear Hive Mind mmap
                        if let Ok(mut f) = std::fs::OpenOptions::new().write(true).open(HIVE_MIND_PATH) {
                            use std::io::Write;
                            let _ = f.write_all(&[0u8]);
                        }
                        eprintln!("  ☀️ [SENTINEL] Regime CLEARED — resuming normal ops");
                    }
                }
            }
        }

        // ═══ LAYER 5: System Resources (every 60s = 12th cycle) ═══
        if cycle_count % 12 == 0 {
            check_system_resources(&mut history).await;
        }
        cycle_count += 1;
    }
}

// ═══════════════════════════════════════════════════════════
// LAYER 2: AI Timeout Guard
// Candle inference is in-process (no HTTP). Timeout enforced
// in gpu.rs via mpsc channel drain. L2 ZeroClaw timeout via
// subprocess with 30s limit.
// ═══════════════════════════════════════════════════════════



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

        // Sustained GPU temp tracking (> 85°C for 5 min)
        if temp > 85 {
            history.gpu_temp_high_cycles += 1;
            if history.gpu_temp_high_cycles == SUSTAINED_THRESHOLD && history.should_alert("gpu_temp_sustained") {
                let msg = format!(
                    "🔥 SENTINEL: GPU TEPLOTA SUSTAINED\n\
                     {temp}°C po dobu 5+ minut!\n\
                     Riziko throttlingu a degradace!"
                );
                push_alert("FATAL", &msg).await;
            }
        } else {
            history.gpu_temp_high_cycles = 0;
        }

        // Sustained VRAM > 95%
        if pct > 95 {
            history.vram_high_cycles += 1;
            if history.vram_high_cycles == SUSTAINED_THRESHOLD && history.should_alert("vram_sustained") {
                let msg = format!(
                    "💾 SENTINEL: VRAM > 95% SUSTAINED\n\
                     {mem_used}/{mem_total} MB po dobu 5+ minut"
                );
                push_alert("FATAL", &msg).await;
            }
        } else {
            history.vram_high_cycles = 0;
        }
    }

    // ── CPU Usage (from /proc/stat) ──
    if let Some(cpu_pct) = cpu_usage_pct(history) {
        if cpu_pct > CRITICAL_PCT {
            history.cpu_high_cycles += 1;
            if history.cpu_high_cycles == SUSTAINED_THRESHOLD && history.should_alert("cpu_sustained") {
                let msg = format!(
                    "🔥 SENTINEL: CPU > 95% SUSTAINED\n\
                     CPU pouziti: {cpu_pct:.0}% po dobu 5+ minut\n\
                     Mozny bottleneck exekuce!"
                );
                push_alert("WARNING", &msg).await;
            }
        } else {
            history.cpu_high_cycles = 0;
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

        // Sustained disk > 95%
        if pct > 95 {
            history.disk_high_cycles += 1;
            if history.disk_high_cycles == SUSTAINED_THRESHOLD && history.should_alert("disk_sustained") {
                let msg = format!(
                    "💾 SENTINEL: DISK > 95% SUSTAINED\n\
                     {pct}% po dobu 5+ minut\n\
                     Urgentne uvolnit misto!"
                );
                push_alert("FATAL", &msg).await;
            }
        } else {
            history.disk_high_cycles = 0;
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

        // Sustained RAM > 95%
        if pct > 95 {
            history.ram_high_cycles += 1;
            if history.ram_high_cycles == SUSTAINED_THRESHOLD && history.should_alert("ram_sustained") {
                let msg = format!(
                    "💾 SENTINEL: RAM > 95% SUSTAINED\n\
                     {pct}% po dobu 5+ minut\n\
                     Mozny OOM killer!"
                );
                push_alert("FATAL", &msg).await;
            }
        } else {
            history.ram_high_cycles = 0;
        }
    }



    // ── Network (Bitfinex) — async TCP connect, no fork ──
    let net_ok = tokio::time::timeout(
        Duration::from_secs(2),
        tokio::net::TcpStream::connect("api.bitfinex.com:443"),
    ).await.map(|r| r.is_ok()).unwrap_or(false);

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

fn cpu_usage_pct(history: &mut SentinelHistory) -> Option<f64> {
    let content = std::fs::read_to_string("/proc/stat").ok()?;
    let first_line = content.lines().next()?;
    let parts: Vec<u64> = first_line.split_whitespace()
        .skip(1)  // skip "cpu"
        .filter_map(|s| s.parse().ok())
        .collect();
    if parts.len() < 4 { return None; }
    let idle = parts[3];
    let total: u64 = parts.iter().sum();
    let d_idle = idle.saturating_sub(history.prev_cpu_idle);
    let d_total = total.saturating_sub(history.prev_cpu_total);
    history.prev_cpu_idle = idle;
    history.prev_cpu_total = total;
    if d_total > 0 {
        Some(100.0 * (1.0 - d_idle as f64 / d_total as f64))
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
