// 💰 PnL Shared Types — Volatility-Neutral FIFO PnL Engine
// Part of sniper-shared crate
//
// mmap layout for real-time PnL data read by all dashboards.
// Written by pnl_daemon.py, read by Rust dashboards + Architect.
//
// Three PnL layers:
//   1. Trading PnL (FIFO, volatility-immune) — main metric
//   2. Inventory PnL (mark-to-market, info only)
//   3. Wallet PnL (Bitfinex REST, Architect only)

use std::sync::atomic::{AtomicU64, AtomicI64, AtomicU32};

pub const PNL_STATE_PATH: &str = "/dev/shm/beroun/pnl_state.bin";
pub const MAX_PNL_BOTS: usize = 16; // hydra=0..7 (live), shadow=8..15

/// Bot index constants for PnlGlobalState.bots[]
pub const PNL_BOT_HYDRA: usize = 0;
pub const PNL_BOT_MOONSHOT: usize = 1;
pub const PNL_BOT_GRID: usize = 2;
pub const PNL_BOT_TRIGON: usize = 3;

// ═══════════════════════════════════════════════════════════
// Per-Bot PnL State
// ═══════════════════════════════════════════════════════════

/// Per-bot PnL metrics, cache-line aligned.
/// All monetary values are × PRICE_SCALE (1e8) for fixed-point precision.
#[repr(C, align(64))]
pub struct PnlBotState {
    // ═══ Trading PnL (FIFO, volatility-immune) ═══
    /// Net realized PnL in last 1 hour (after fees) × PRICE_SCALE
    pub realized_1h: AtomicI64,
    /// Net realized PnL in last 24 hours × PRICE_SCALE
    pub realized_24h: AtomicI64,
    /// Net realized PnL in last 7 days × PRICE_SCALE
    pub realized_7d: AtomicI64,
    /// Net realized PnL in last 30 days × PRICE_SCALE
    pub realized_30d: AtomicI64,

    // ═══ Fees (separated for transparency) ═══
    /// Total fees paid in last 1 hour × PRICE_SCALE
    pub fees_1h: AtomicI64,
    /// Total fees paid in last 24 hours × PRICE_SCALE
    pub fees_24h: AtomicI64,

    // ═══ Activity ═══
    /// Number of fills in last 1 hour
    pub fills_1h: AtomicU32,
    /// Number of fills in last 24 hours
    pub fills_24h: AtomicU32,
    /// Number of FIFO-closed round-trips in last 24 hours
    pub closed_trades_24h: AtomicU32,
    /// Lifetime total FIFO-closed round-trips
    pub total_closed: AtomicU64,

    // ═══ Inventory (INFO ONLY — volatility-exposed) ═══
    /// Current net position × PRICE_SCALE (positive = long, negative = short)
    pub net_position: AtomicI64,
    /// FIFO queue front entry price × PRICE_SCALE
    pub fifo_front_price: AtomicU64,
    /// Current market price × PRICE_SCALE
    pub mark_price: AtomicU64,
    /// Unrealized PnL: position × (mark - fifo_front) × PRICE_SCALE
    pub inventory_pnl: AtomicI64,

    // ═══ Per-trade averages ═══
    /// Average net PnL per closed trade (24h) × PRICE_SCALE
    pub avg_pnl_per_trade: AtomicI64,
    /// Average fee per fill (24h) × PRICE_SCALE
    pub avg_fee_per_fill: AtomicI64,
    /// Timestamp of last fill (epoch ms)
    pub last_fill_ms: AtomicU64,

    /// Bot name hash (for identification)
    pub bot_name_hash: AtomicU64,
    pub _padding: [u8; 24],
}

// ═══════════════════════════════════════════════════════════
// Global PnL State
// ═══════════════════════════════════════════════════════════

/// Global PnL state across all bots + wallet info.
#[repr(C, align(64))]
pub struct PnlGlobalState {
    /// Per-bot PnL data (indexed by PNL_BOT_* constants)
    pub bots: [PnlBotState; MAX_PNL_BOTS],

    // ═══ Armada Totals (sum across all bots) ═══
    pub total_realized_1h: AtomicI64,
    pub total_realized_24h: AtomicI64,
    pub total_realized_7d: AtomicI64,
    pub total_realized_30d: AtomicI64,
    pub total_fees_24h: AtomicI64,
    pub total_fills_24h: AtomicU32,

    // ═══ Wallet (Bitfinex REST, INFO ONLY) ═══
    /// USD balance on exchange × PRICE_SCALE
    pub wallet_usd: AtomicI64,
    /// BTC balance on exchange × PRICE_SCALE
    pub wallet_btc: AtomicI64,
    /// Total portfolio value in USD × PRICE_SCALE
    pub wallet_total_usd: AtomicI64,
    /// Wallet value change in last 1 hour × PRICE_SCALE
    pub wallet_delta_1h: AtomicI64,
    /// Wallet value change in last 24 hours × PRICE_SCALE
    pub wallet_delta_24h: AtomicI64,

    /// Engine heartbeat (epoch ms)
    pub heartbeat_ms: AtomicU64,

    pub _padding: [u8; 24],
}

// ═══════════════════════════════════════════════════════════
// Defaults
// ═══════════════════════════════════════════════════════════

impl Default for PnlGlobalState {
    fn default() -> Self {
        unsafe { std::mem::zeroed() }
    }
}
