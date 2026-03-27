// 🌙 Moonshot Shared Types — Multi-Symbol Flash Crash Catcher
// Part of sniper-shared crate
//
// Mmap layout for Moonshot bot IPC. All fields are lock-free atomics.
// Cache-aligned to 64 bytes per pair for L1 cache line isolation.

use std::sync::atomic::{AtomicU64, AtomicI64, AtomicU32};

/// Maximum number of simultaneously tracked pairs (AI selects up to 20)
pub const MOONSHOT_MAX_PAIRS: usize = 20;

/// mmap file paths (in /dev/shm for zero-copy IPC)
pub const MOONSHOT_ENGINE_PATH: &str = "/dev/shm/beroun/moonshot_engine.bin";
pub const MOONSHOT_RISK_PATH: &str = "/dev/shm/beroun/moonshot_risk.bin";

// ═══════════════════════════════════════════════════════════
// Engine State — written by L0 (Rust main.rs), read by dashboard/brain/scripts
// ═══════════════════════════════════════════════════════════

/// Per-pair engine state (live market data + position tracking)
#[repr(C, align(64))]
pub struct MoonshotPairEngine {
    pub best_bid: AtomicU64,           // Current BID × PRICE_SCALE
    pub best_ask: AtomicU64,           // Current ASK × PRICE_SCALE
    pub last_trade: AtomicU64,         // Last trade price × PRICE_SCALE
    pub latency_ns: AtomicU64,         // Processing latency (nanoseconds)
    pub net_position: AtomicI64,       // Open position (signed, × PRICE_SCALE)
    pub realized_pnl: AtomicI64,       // Realized PnL (USD × PRICE_SCALE)
    pub unrealized_pnl: AtomicI64,     // Mark-to-market unrealized PnL
    pub active: AtomicU32,             // 1 = pair active, 0 = inactive
    pub buy_order_price: AtomicU64,    // Current ghost BUY level × PRICE_SCALE
    pub sell_order_id: AtomicI64,      // Active TP order ID (0 = none)
    pub fill_count: AtomicU64,         // Total fills (lifetime)
    pub last_fill_ts: AtomicU64,       // Timestamp of last fill (epoch ms)
    pub _padding: [u8; 8],
}

/// Global moonshot engine state
#[repr(C, align(64))]
pub struct MoonshotEngineState {
    pub pairs: [MoonshotPairEngine; MOONSHOT_MAX_PAIRS],
    pub wallet_btc: AtomicU64,         // Total BTC balance × PRICE_SCALE
    pub wallet_usd: AtomicU64,         // Total USD balance × PRICE_SCALE
    pub total_fills: AtomicU64,        // Global fill counter
    pub daily_pnl: AtomicI64,          // Daily realized PnL (USD × PRICE_SCALE)
    pub heartbeat_ms: AtomicU64,       // Last engine heartbeat (epoch ms)
}

// ═══════════════════════════════════════════════════════════
// Risk State — written by L2 Oracle (Python), read by L0 (Rust)
// ═══════════════════════════════════════════════════════════

/// Per-pair risk parameters (AI-driven, written by sniper_orchestrator.py)
#[repr(C, align(64))]
pub struct MoonshotPairRisk {
    /// 8-byte ASCII symbol hash (e.g., "tBTCUSD\0" as u64)
    pub symbol_hash: AtomicU64,
    /// Drop distance: BUY placed this % below market (× PRICE_SCALE)
    pub m_shot_price_pct: AtomicU64,
    /// Minimum distance: below this, order is replaced (× PRICE_SCALE)
    pub m_shot_price_min_pct: AtomicU64,
    /// Delay to move BUY down on price drop (milliseconds)
    pub m_shot_replace_delay_ms: AtomicU64,
    /// Delay to move BUY up on price rise — anti pump-chase (milliseconds)
    pub m_shot_raise_wait_ms: AtomicU64,
    /// Take-profit percentage (× PRICE_SCALE)
    pub tp_pct: AtomicU64,
    /// Stop-loss percentage (× PRICE_SCALE)
    pub sl_pct: AtomicU64,
    /// Order size in USD (× PRICE_SCALE)
    pub order_usd: AtomicU64,
    /// Maximum open position per pair (signed, × PRICE_SCALE)
    pub max_position: AtomicI64,
    /// Fee in basis points (for fee sentinel)
    pub fee_bps: AtomicU64,
}

/// Global moonshot risk state
#[repr(C, align(64))]
pub struct MoonshotRiskState {
    pub pairs: [MoonshotPairRisk; MOONSHOT_MAX_PAIRS],
    /// Global kill switch: 1 = trading paused, 0 = live
    pub global_paused: AtomicU64,
    /// Daily loss limit in USD (positive number, × PRICE_SCALE)
    pub daily_loss_limit: AtomicI64,
    /// Auto-kill when BTC hourly volatility exceeds this % (× PRICE_SCALE)
    pub btc_volatility_kill_pct: AtomicU64,
    /// AI orchestrator hearbeat (epoch ms) — stale > 120min → safe mode
    pub ai_heartbeat_ms: AtomicU64,
}

// ═══════════════════════════════════════════════════════════
// Symbol Hash Helpers (zero-copy ASCII ↔ u64)
// ═══════════════════════════════════════════════════════════

/// Convert up to 8-byte ASCII string to u64 hash (e.g., "tBTCUSD" → u64)
pub fn str_to_symbol_hash(s: &str) -> u64 {
    let mut bytes = [0u8; 8];
    let len = s.len().min(8);
    bytes[..len].copy_from_slice(&s.as_bytes()[..len]);
    u64::from_le_bytes(bytes)
}

/// Convert u64 hash back to ASCII string
pub fn symbol_hash_to_str(h: u64) -> String {
    let bytes = h.to_le_bytes();
    let end = bytes.iter().position(|&b| b == 0).unwrap_or(8);
    String::from_utf8_lossy(&bytes[..end]).to_string()
}

// ═══════════════════════════════════════════════════════════
// Default implementations (safe initialization)
// ═══════════════════════════════════════════════════════════

impl Default for MoonshotEngineState {
    fn default() -> Self {
        // Safety: All fields are AtomicU64/AtomicI64/AtomicU32 — zeroed is valid
        unsafe { std::mem::zeroed() }
    }
}

impl Default for MoonshotRiskState {
    fn default() -> Self {
        let mut rs: MoonshotRiskState = unsafe { std::mem::zeroed() };
        // Default: trading paused until AI activates
        rs.global_paused = AtomicU64::new(1);
        rs
    }
}
