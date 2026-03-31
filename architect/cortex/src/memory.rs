// ═══════════════════════════════════════════════════════════
// 🧠 SOVEREIGN CORTEX — Memory Module
// Type-safe mmap access to ALL Armada bot states
// Zero-copy, zero-offset-bugs (uses sniper_shared types)
// ═══════════════════════════════════════════════════════════

use memmap2::{MmapMut, MmapOptions};
use sniper_types::{EngineState, RiskState, PRICE_SCALE};
use std::fs::OpenOptions;
use std::path::Path;
use std::sync::atomic::Ordering;

/// Snapshot of a single bot's state — safe to serialize/format for prompts.
#[allow(dead_code)]
pub struct BotSnapshot {
    pub name: &'static str,
    pub emoji: &'static str,
    pub online: bool,
    pub best_bid: f64,
    pub best_ask: f64,
    pub spread: f64,
    pub micro_price: f64,
    pub net_position: f64,
    pub realized_pnl: f64,
    pub session_fills: u64,
    pub toxic_hits: u64,
    pub t2t_micros: u64,
    pub l1_confidence: f64,
    pub l1_fp_rate: f64,
    pub l1_success_rate: f64,
    pub l2_regime: &'static str,
    pub fear_greed: u64,
    pub macro_bias: f64,
    pub shadow_mode: bool,
    pub ai_intent: &'static str,
    // Risk params
    pub grid_step: f64,
    pub grid_levels: u64,
    pub max_position: f64,
    pub authorized_capital: f64,
    pub daily_loss_limit: f64,
    pub paused: bool,
    // PnL from FIFO engine (pnl_state.bin mmap)
    pub pnl_1h: f64,
    pub pnl_24h: f64,
    pub pnl_7d: f64,
    pub pnl_30d: f64,
    pub fills_24h_fifo: u32,
    pub closed_trades_24h: u32,
    // Wallet (from Bitfinex WebSocket wu/ws events)
    pub wallet_btc: f64,
    pub wallet_usd: f64,
}

/// Central memory manager — opens mmap handles for all bots.
pub struct ArmadaMemory {
    hydra_engine: MmapMut,
    hydra_risk: MmapMut,
    moonshot_engine: Option<MmapMut>,
    moonshot_risk: Option<MmapMut>,
    grid_engine: Option<MmapMut>,
    grid_risk: Option<MmapMut>,
    trigon_engine: Option<MmapMut>,
    trigon_risk: Option<MmapMut>,
}

impl ArmadaMemory {
    /// Open all bot mmap files. Hydra is required, others are optional.
    pub fn new() -> anyhow::Result<Self> {
        println!("🔗 Mapping shared memory...");

        let hydra_engine = Self::map_file("/dev/shm/beroun/engine_state.bin")?;
        let hydra_risk = Self::map_file("/dev/shm/beroun/risk_state.bin")?;

        let engine_size = std::mem::size_of::<EngineState>();
        let risk_size = std::mem::size_of::<RiskState>();
        println!("  ✅ Hydra engine_state.bin mapped ({engine_size} bytes → EngineState)");
        println!("  ✅ Hydra risk_state.bin mapped ({risk_size} bytes → RiskState)");

        // Optional bots — logged but not fatal
        let moonshot_engine = Self::try_map_file("/dev/shm/beroun/moonshot_engine.bin", "Moonshot");
        let moonshot_risk = Self::try_map_file("/dev/shm/beroun/moonshot_risk.bin", "Moonshot Risk");
        let grid_engine = Self::try_map_file("/dev/shm/beroun/grid_engine.bin", "Grid");
        let grid_risk = Self::try_map_file("/dev/shm/beroun/grid_risk.bin", "Grid Risk");
        let trigon_engine = Self::try_map_file("/dev/shm/beroun/trigon_engine.bin", "Trigon");
        let trigon_risk = Self::try_map_file("/dev/shm/beroun/trigon_risk.bin", "Trigon Risk");

        Ok(ArmadaMemory {
            hydra_engine, hydra_risk,
            moonshot_engine, moonshot_risk,
            grid_engine, grid_risk,
            trigon_engine, trigon_risk,
        })
    }

    fn map_file(path: &str) -> anyhow::Result<MmapMut> {
        if !Path::new(path).exists() {
            anyhow::bail!("mmap file not found: {path}");
        }
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(path)?;
        let mm = unsafe { MmapOptions::new().map_mut(&file)? };
        Ok(mm)
    }

    fn try_map_file(path: &str, name: &str) -> Option<MmapMut> {
        match Self::map_file(path) {
            Ok(mm) => {
                println!("  ✅ {name} mapped ({} bytes)", mm.len());
                Some(mm)
            }
            Err(_) => {
                println!("  ⬜ {name} not found (bot offline)");
                None
            }
        }
    }

    /// Type-safe reference to Hydra EngineState (zero-copy).
    pub fn hydra_engine(&self) -> &EngineState {
        unsafe { &*(self.hydra_engine.as_ptr() as *const EngineState) }
    }

    /// Type-safe reference to Hydra RiskState (zero-copy).
    pub fn hydra_risk(&self) -> &RiskState {
        unsafe { &*(self.hydra_risk.as_ptr() as *const RiskState) }
    }

    /// Get RiskState for any bot by name. Returns None if bot's mmap is offline.
    pub fn risk_for_bot(&self, name: &str) -> Option<&RiskState> {
        match name {
            "hydra" => Some(self.hydra_risk()),
            "moonshot" => self.moonshot_risk.as_ref()
                .map(|m| unsafe { &*(m.as_ptr() as *const RiskState) }),
            "grid" => self.grid_risk.as_ref()
                .map(|m| unsafe { &*(m.as_ptr() as *const RiskState) }),
            "trigon" => self.trigon_risk.as_ref()
                .map(|m| unsafe { &*(m.as_ptr() as *const RiskState) }),
            _ => None,
        }
    }

    /// Get EngineState for any bot by name. Returns None if bot's mmap is offline.
    pub fn engine_for_bot(&self, name: &str) -> Option<&EngineState> {
        match name {
            "hydra" => Some(self.hydra_engine()),
            "moonshot" => self.moonshot_engine.as_ref()
                .map(|m| unsafe { &*(m.as_ptr() as *const EngineState) }),
            "grid" => self.grid_engine.as_ref()
                .map(|m| unsafe { &*(m.as_ptr() as *const EngineState) }),
            "trigon" => self.trigon_engine.as_ref()
                .map(|m| unsafe { &*(m.as_ptr() as *const EngineState) }),
            _ => None,
        }
    }

    /// Take an atomic snapshot of Hydra's full state.
    /// All reads are Acquire-ordered for consistency.
    pub fn snapshot_hydra(&self) -> BotSnapshot {
        self.snapshot_bot(self.hydra_engine(), self.hydra_risk(), "hydra", "🐍")
    }

    /// Snapshot all online bots (for L2 unified prompt).
    pub fn snapshot_all(&self) -> Vec<BotSnapshot> {
        let mut snapshots = Vec::with_capacity(5);
        snapshots.push(self.snapshot_hydra());
        if let (Some(e), Some(r)) = (&self.moonshot_engine, &self.moonshot_risk) {
            let engine = unsafe { &*(e.as_ptr() as *const EngineState) };
            let risk = unsafe { &*(r.as_ptr() as *const RiskState) };
            snapshots.push(self.snapshot_bot(engine, risk, "moonshot", "🌙"));
        }
        if let (Some(e), Some(r)) = (&self.grid_engine, &self.grid_risk) {
            let engine = unsafe { &*(e.as_ptr() as *const EngineState) };
            let risk = unsafe { &*(r.as_ptr() as *const RiskState) };
            snapshots.push(self.snapshot_bot(engine, risk, "grid", "📐"));
        }
        if let (Some(e), Some(r)) = (&self.trigon_engine, &self.trigon_risk) {
            let engine = unsafe { &*(e.as_ptr() as *const EngineState) };
            let risk = unsafe { &*(r.as_ptr() as *const RiskState) };
            snapshots.push(self.snapshot_bot(engine, risk, "trigon", "🔺"));
        }
        // Nexus: process-based detection (uses CrossExchangeState, not EngineState)
        snapshots.push(self.snapshot_nexus());
        snapshots
    }

    /// Nexus snapshot — lightweight, process-based (no EngineState mmap).
    fn snapshot_nexus(&self) -> BotSnapshot {
        let online = std::process::Command::new("pgrep")
            .args(["-f", "nexus-core"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);

        BotSnapshot {
            name: "nexus", emoji: "🪐", online,
            best_bid: 0.0, best_ask: 0.0, spread: 0.0,
            micro_price: 0.0, net_position: 0.0, realized_pnl: 0.0,
            session_fills: 0, toxic_hits: 0, t2t_micros: 0,
            l1_confidence: 0.0, l1_fp_rate: 0.0, l1_success_rate: 0.0,
            l2_regime: "N/A", fear_greed: 0, macro_bias: 0.0,
            shadow_mode: false, ai_intent: "SOVEREIGN",
            grid_step: 0.0, grid_levels: 0, max_position: 0.0,
            authorized_capital: 0.0, daily_loss_limit: 0.0, paused: false,
            pnl_1h: 0.0, pnl_24h: 0.0, pnl_7d: 0.0, pnl_30d: 0.0,
            fills_24h_fifo: 0, closed_trades_24h: 0,
            wallet_btc: 0.0, wallet_usd: 0.0,
        }
    }

    /// Generic bot snapshot from EngineState + RiskState references.
    fn snapshot_bot(&self, e: &EngineState, r: &RiskState, name: &'static str, emoji: &'static str) -> BotSnapshot {
        let bid = e.best_bid.load(Ordering::Acquire) as f64 / PRICE_SCALE;
        let ask = e.best_ask.load(Ordering::Acquire) as f64 / PRICE_SCALE;

        let regime_id = e.l2_regime_id.load(Ordering::Relaxed);
        let intent_id = e.ai_intent.load(Ordering::Relaxed);

        BotSnapshot {
            name,
            emoji,
            online: e.latency_ns.load(Ordering::Relaxed) > 0,
            best_bid: bid,
            best_ask: ask,
            spread: ask - bid,
            micro_price: e.micro_price.load(Ordering::Acquire) as f64 / PRICE_SCALE,
            net_position: e.net_position.load(Ordering::Acquire) as f64 / PRICE_SCALE,
            realized_pnl: e.realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE,
            session_fills: e.session_fill_count.load(Ordering::Relaxed),
            toxic_hits: e.toxic_flow_hits.load(Ordering::Relaxed),
            t2t_micros: e.t2t_micros.load(Ordering::Relaxed),
            l1_confidence: e.l1_confidence_score.load(Ordering::Relaxed) as f64 / 10000.0,
            l1_fp_rate: e.l1_false_positive_rate.load(Ordering::Relaxed) as f64 / 10000.0,
            l1_success_rate: e.l1_sweep_success_rate.load(Ordering::Relaxed) as f64 / 10000.0,
            l2_regime: match regime_id {
                1 => "TRENDING",
                2 => "RANGING",
                3 => "CHAOS",
                _ => "UNKNOWN",
            },
            fear_greed: e.macro_fear_greed.load(Ordering::Relaxed),
            macro_bias: e.macro_bias.load(Ordering::Relaxed) as f64 / 10000.0,
            shadow_mode: e.is_shadow_mode.load(Ordering::Relaxed) == 1,
            ai_intent: match intent_id {
                0 => "SOVEREIGN",
                1 => "AGGRESSIVE",
                2 => "DEFENSIVE",
                3 => "SCOUT",
                _ => "UNKNOWN",
            },
            grid_step: r.grid_step.load(Ordering::Acquire) as f64 / PRICE_SCALE,
            grid_levels: r.grid_size.load(Ordering::Relaxed),
            max_position: r.max_inv_delta.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
            authorized_capital: r.authorized_capital.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
            daily_loss_limit: r.daily_loss_limit.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
            paused: r.paused.load(Ordering::Relaxed) != 0,
            // PnL fields — filled after snapshot via enrich_with_pnl()
            pnl_1h: 0.0,
            pnl_24h: 0.0,
            pnl_7d: 0.0,
            pnl_30d: 0.0,
            fills_24h_fifo: 0,
            closed_trades_24h: 0,
            wallet_btc: e.wallet_btc.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
            wallet_usd: e.wallet_usd.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
        }
    }
}

// ═══ PnL mmap reader ═══
// Reads from /dev/shm/beroun/pnl_state.bin written by pnl_daemon.py
// Layout: 8 bots × 192 bytes each.
// Per-bot offsets: realized_1h(i64@0), realized_24h(i64@8), realized_7d(i64@16),
//   fills_24h(u32@52), closed_24h(u32@56)

const PNL_MMAP_PATH: &str = "/dev/shm/beroun/pnl_state.bin";
const PNL_BOT_SIZE: usize = 192;

fn bot_pnl_index(name: &str) -> Option<usize> {
    match name {
        "hydra" => Some(0),
        "moonshot" => Some(1),
        "grid" => Some(2),
        "trigon" => Some(3),
        "nexus" => Some(4),
        _ => None,
    }
}

/// Enrich snapshots with PnL data from pnl_state.bin mmap.
pub fn enrich_with_pnl(snapshots: &mut [BotSnapshot]) {
    let path = std::path::Path::new(PNL_MMAP_PATH);
    if !path.exists() {
        return;
    }
    let data = match std::fs::read(path) {
        Ok(d) => d,
        Err(_) => return,
    };

    for snap in snapshots.iter_mut() {
        if let Some(idx) = bot_pnl_index(snap.name) {
            let base = idx * PNL_BOT_SIZE;
            if base + PNL_BOT_SIZE <= data.len() {
                snap.pnl_1h = read_i64(&data, base) as f64 / PRICE_SCALE;
                snap.pnl_24h = read_i64(&data, base + 8) as f64 / PRICE_SCALE;
                snap.pnl_7d = read_i64(&data, base + 16) as f64 / PRICE_SCALE;
                snap.pnl_30d = read_i64(&data, base + 24) as f64 / PRICE_SCALE;
                snap.fills_24h_fifo = read_u32(&data, base + 52);
                snap.closed_trades_24h = read_u32(&data, base + 56);
            }
        }
    }
}

fn read_i64(data: &[u8], offset: usize) -> i64 {
    i64::from_le_bytes(data[offset..offset+8].try_into().unwrap_or([0;8]))
}

fn read_u32(data: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(data[offset..offset+4].try_into().unwrap_or([0;4]))
}


