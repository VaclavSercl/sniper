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
}

/// Central memory manager — opens mmap handles for all bots.
pub struct ArmadaMemory {
    hydra_engine: MmapMut,
    hydra_risk: MmapMut,
    // Future: moonshot_engine, grid_engine, trigon_engine
}

impl ArmadaMemory {
    /// Open all bot mmap files. Panics if Hydra files don't exist
    /// (other bots are optional — will be added when they have mmap).
    pub fn new() -> anyhow::Result<Self> {
        println!("🔗 Mapping shared memory...");

        let hydra_engine = Self::map_file("/dev/shm/beroun/engine_state.bin")?;
        let hydra_risk = Self::map_file("/dev/shm/beroun/risk_state.bin")?;

        let engine_size = std::mem::size_of::<EngineState>();
        let risk_size = std::mem::size_of::<RiskState>();
        println!("  ✅ Hydra engine_state.bin mapped ({engine_size} bytes → EngineState)");
        println!("  ✅ Hydra risk_state.bin mapped ({risk_size} bytes → RiskState)");

        Ok(ArmadaMemory {
            hydra_engine,
            hydra_risk,
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

    /// Type-safe reference to Hydra EngineState (zero-copy).
    pub fn hydra_engine(&self) -> &EngineState {
        unsafe { &*(self.hydra_engine.as_ptr() as *const EngineState) }
    }

    /// Type-safe reference to Hydra RiskState (zero-copy).
    pub fn hydra_risk(&self) -> &RiskState {
        unsafe { &*(self.hydra_risk.as_ptr() as *const RiskState) }
    }

    /// Mutable reference to Hydra RiskState (for L2 writes).
    pub fn hydra_risk_mut(&mut self) -> &mut RiskState {
        unsafe { &mut *(self.hydra_risk.as_mut_ptr() as *mut RiskState) }
    }

    /// Take an atomic snapshot of Hydra's full state.
    /// All reads are Acquire-ordered for consistency.
    pub fn snapshot_hydra(&self) -> BotSnapshot {
        let e = self.hydra_engine();
        let r = self.hydra_risk();

        let bid = e.best_bid.load(Ordering::Acquire) as f64 / PRICE_SCALE;
        let ask = e.best_ask.load(Ordering::Acquire) as f64 / PRICE_SCALE;

        let regime_id = e.l2_regime_id.load(Ordering::Relaxed);
        let intent_id = e.ai_intent.load(Ordering::Relaxed);

        BotSnapshot {
            name: "hydra",
            emoji: "🐍",
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
            // Risk params from RiskState
            grid_step: r.grid_step.load(Ordering::Acquire) as f64 / PRICE_SCALE,
            grid_levels: r.grid_size.load(Ordering::Relaxed),
            max_position: r.max_inv_delta.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
            authorized_capital: r.authorized_capital.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
            daily_loss_limit: r.daily_loss_limit.load(Ordering::Relaxed) as f64 / PRICE_SCALE,
            paused: r.paused.load(Ordering::Relaxed) != 0,
        }
    }

    /// Snapshot all bots (for L2 unified prompt).
    pub fn snapshot_all(&self) -> Vec<BotSnapshot> {
        let mut snapshots = Vec::with_capacity(4);
        snapshots.push(self.snapshot_hydra());
        // Future: snapshots.push(self.snapshot_moonshot());
        // Future: snapshots.push(self.snapshot_grid());
        // Future: snapshots.push(self.snapshot_trigon());
        snapshots
    }
}

/// Format a bot snapshot as a text block for the Gemini prompt.
pub fn format_snapshot_for_prompt(s: &BotSnapshot) -> String {
    format!(
        "═══ {emoji} {name} ═══\n\
         Status: {status}\n\
         Price: ${price:.2} (spread: ${spread:.2})\n\
         Position: {pos:.6} BTC | PnL: ${pnl:.4}\n\
         Grid: ${grid:.2} ({levels} levels) | MaxPos: {maxpos:.6} BTC\n\
         Capital: ${cap:.2} | DLL: -${dll:.2} | Paused: {paused}\n\
         Fills: {fills} | Toxic: {toxic} | T2T: {t2t}µs\n\
         L1 Conf: {conf:.0}% | FP: {fp:.0}% | Success: {sr:.0}%\n\
         Regime: {regime} | Intent: {intent}\n\
         F&G: {fg} | Macro Bias: {bias:+.4} | Shadow: {shadow}",
        emoji = s.emoji,
        name = s.name.to_uppercase(),
        status = if s.online { "🟢 ONLINE" } else { "🔴 OFFLINE" },
        price = s.micro_price,
        spread = s.spread,
        pos = s.net_position,
        pnl = s.realized_pnl,
        grid = s.grid_step,
        levels = s.grid_levels,
        maxpos = s.max_position,
        cap = s.authorized_capital,
        dll = s.daily_loss_limit,
        paused = if s.paused { "YES ⏸️" } else { "NO ▶️" },
        fills = s.session_fills,
        toxic = s.toxic_hits,
        t2t = s.t2t_micros,
        conf = s.l1_confidence * 100.0,
        fp = s.l1_fp_rate * 100.0,
        sr = s.l1_success_rate * 100.0,
        regime = s.l2_regime,
        intent = s.ai_intent,
        fg = s.fear_greed,
        bias = s.macro_bias,
        shadow = if s.shadow_mode { "YES 🌑" } else { "NO" },
    )
}
