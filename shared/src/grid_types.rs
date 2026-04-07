// 📐 Grid Shared Types — Multi-Level Dynamic Grid Trading
// Part of sniper-shared crate
//
// mmap layout for Grid bot IPC. All fields are lock-free atomics.
// Grid bot places N buy levels + N sell levels around a center price.

use std::sync::atomic::{AtomicU64, AtomicI64, AtomicU32};

/// Maximum grid levels per side (BUY / SELL)
pub const GRID_MAX_LEVELS: usize = 10;

/// mmap file paths
pub const GRID_ENGINE_PATH: &str = "/dev/shm/beroun/grid_engine.bin";
pub const GRID_RISK_PATH: &str = "/dev/shm/beroun/grid_risk.bin";

// ═══════════════════════════════════════════════════════════
// Engine State — written by L0 (Rust), read by dashboard/brain
// ═══════════════════════════════════════════════════════════

/// Per-level state (BUY or SELL)
#[repr(C, align(64))]
pub struct GridLevelState {
    pub price: AtomicU64,           // Level price × PRICE_SCALE
    pub quantity: AtomicU64,        // Order size × PRICE_SCALE
    pub order_id: AtomicI64,        // Bitfinex order ID (0 = not placed)
    pub filled: AtomicU32,          // 1 = filled, 0 = open/pending
    pub fill_count: AtomicU64,      // Total fills at this level (lifetime)
    pub last_fill_ts: AtomicU64,    // Timestamp of last fill (epoch ms)
    pub _padding: [u8; 12],
}

/// Global grid engine state
#[repr(C, align(64))]
pub struct GridEngineState {
    /// Buy levels (sorted descending: [0] = closest to mid)
    pub buy_levels: [GridLevelState; GRID_MAX_LEVELS],
    /// Sell levels (sorted ascending: [0] = closest to mid)
    pub sell_levels: [GridLevelState; GRID_MAX_LEVELS],
    /// Current mid price (last trade × PRICE_SCALE)
    pub mid_price: AtomicU64,
    /// Current BID × PRICE_SCALE
    pub best_bid: AtomicU64,
    /// Current ASK × PRICE_SCALE
    pub best_ask: AtomicU64,
    /// Wallet BTC balance (physical)
    pub wallet_btc: AtomicU64,
    /// Wallet USD balance (physical)
    pub wallet_usd: AtomicU64,
    /// Realized PnL (USD × PRICE_SCALE)
    pub realized_pnl: AtomicI64,
    /// Unrealized PnL (mark-to-market)
    pub unrealized_pnl: AtomicI64,
    /// Net BTC position (signed × PRICE_SCALE)
    pub net_position: AtomicI64,
    /// Total fills (buy + sell)
    pub total_fills: AtomicU64,
    /// Daily realized PnL
    pub daily_pnl: AtomicI64,
    /// Engine heartbeat (epoch ms)
    pub heartbeat_ms: AtomicU64,
    /// Processing latency (ns)
    pub latency_ns: AtomicU64,
    /// Active buy levels count
    pub active_buy_levels: AtomicU32,
    /// Active sell levels count
    pub active_sell_levels: AtomicU32,
    /// Consecutive losses counter (for safety halt)
    pub consecutive_losses: AtomicU32,
    /// Active tracking fields
    pub active_buy_ids: [AtomicU64; GRID_MAX_LEVELS],
    pub active_sell_ids: [AtomicU64; GRID_MAX_LEVELS],
}

// ═══════════════════════════════════════════════════════════
// Risk State — written by L2 Oracle, read by L0
// ═══════════════════════════════════════════════════════════

/// Grid risk/config parameters
#[repr(C, align(64))]
pub struct GridRiskState {
    /// Trading symbol hash (e.g., "tBTCUSD")
    pub symbol_hash: AtomicU64,
    /// Grid spacing in USD (× PRICE_SCALE). Default: 1200
    pub grid_spacing: AtomicU64,
    /// Number of BUY levels (1-10)
    pub num_buy_levels: AtomicU32,
    /// Number of SELL levels (1-10)
    pub num_sell_levels: AtomicU32,
    /// Order quantity per level in BTC (× PRICE_SCALE)
    pub order_qty: AtomicU64,
    /// Grid mode: 0 = arithmetic (fixed $), 1 = geometric (fixed %)
    pub grid_mode: AtomicU32,
    /// Geometric step percentage (× PRICE_SCALE, e.g., 1.5 = 1.5%)
    pub geometric_step_pct: AtomicU64,
    /// Take profit multiplier (× PRICE_SCALE). Sell price = buy_price + grid_spacing × tp_mult
    pub tp_multiplier: AtomicU64,
    /// Global kill switch: 1 = paused
    pub global_paused: AtomicU64,
    /// Daily loss limit (USD × PRICE_SCALE)
    pub daily_loss_limit: AtomicI64,
    /// Max consecutive losses before 24h halt
    pub max_consecutive_losses: AtomicU32,
    /// Dynamic spacing: 0 = fixed, 1 = ATR-based
    pub dynamic_spacing: AtomicU32,
    /// ATR period for dynamic spacing (minutes)
    pub atr_period_minutes: AtomicU64,
    /// Center price override (0 = auto from last trade)
    pub center_price_override: AtomicU64,
    /// BTC volatility kill threshold (× PRICE_SCALE)
    pub btc_vol_kill_pct: AtomicU64,
    /// AI heartbeat (epoch ms)
    pub ai_heartbeat_ms: AtomicU64,
}

// ═══════════════════════════════════════════════════════════
// Default implementations
// ═══════════════════════════════════════════════════════════

impl Default for GridEngineState {
    fn default() -> Self {
        unsafe { std::mem::zeroed() }
    }
}

impl Default for GridRiskState {
    fn default() -> Self {
        let mut rs: GridRiskState = unsafe { std::mem::zeroed() };
        rs.global_paused = AtomicU64::new(1); // Default: paused
        rs.num_buy_levels = AtomicU32::new(5);
        rs.num_sell_levels = AtomicU32::new(5);
        rs.max_consecutive_losses = AtomicU32::new(3);
        rs
    }
}
