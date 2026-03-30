// ═══════════════════════════════════════════════════════════
// 🌐 Cross-Exchange Price Types — Shared mmap for Multi-Exchange BBA
// Sniper Armada · Phase 5.4 · v17.0
//
// Stores best bid/ask from Bitfinex AND Binance for the same pairs.
// All bots read this mmap to detect cross-exchange arbitrage.
//
// Layout: 64-byte aligned per pair for L1 cache isolation.
// Written by: L2 Oracle (Python) or dedicated price_bridge daemon
// Read by: Trigon, Hydra, Moonshot, Grid (zero-copy via mmap)
// ═══════════════════════════════════════════════════════════

use std::sync::atomic::{AtomicI64, AtomicU64, AtomicU32};

/// Maximum cross-exchange pairs tracked simultaneously
pub const MAX_CROSS_PAIRS: usize = 16;

/// mmap file path
pub const CROSS_EXCHANGE_PATH: &str = "/dev/shm/beroun/cross_exchange.bin";

// ═══════════════════════════════════════════════════════════
// Per-Pair Cross-Exchange State
// ═══════════════════════════════════════════════════════════

/// BBA snapshot from a single exchange for one pair.
#[repr(C, align(64))]
pub struct ExchangeBBA {
    /// Best bid price × PRICE_SCALE
    pub bid: AtomicI64,
    /// Best ask price × PRICE_SCALE
    pub ask: AtomicI64,
    /// Bid volume × PRICE_SCALE
    pub bid_vol: AtomicI64,
    /// Ask volume × PRICE_SCALE  
    pub ask_vol: AtomicI64,
    /// Last update timestamp (epoch ms)
    pub last_update_ms: AtomicU64,
    /// Exchange ID (0=Bitfinex, 1=Binance)
    pub exchange_id: AtomicU32,
    /// Is this exchange connected and streaming? (1=yes, 0=no)
    pub connected: AtomicU32,
    pub _padding: [u8; 16],
}

/// Cross-exchange pair state — one pair across two exchanges.
#[repr(C, align(64))]
pub struct CrossPairState {
    /// Bitfinex BBA for this pair
    pub bitfinex: ExchangeBBA,
    /// Binance BBA for this pair
    pub binance: ExchangeBBA,

    // ═══ Derived Arbitrage Metrics (computed by writer) ═══

    /// Cross-exchange spread: binance_bid - bitfinex_ask (positive = arb opportunity)
    /// Direction: buy on Bitfinex, sell on Binance
    pub spread_buy_bfx_sell_bnb: AtomicI64,
    /// Cross-exchange spread: bitfinex_bid - binance_ask (positive = reverse arb)
    /// Direction: buy on Binance, sell on Bitfinex
    pub spread_buy_bnb_sell_bfx: AtomicI64,
    /// Best spread in bps (max of both directions) × 100
    pub best_spread_bps: AtomicI64,
    /// Direction of best spread: 0 = buy_bfx_sell_bnb, 1 = buy_bnb_sell_bfx
    pub best_direction: AtomicU32,
    /// Number of times spread exceeded threshold (lifetime)
    pub arb_signals: AtomicU64,
    /// Last time spread exceeded threshold (epoch ms)
    pub last_arb_signal_ms: AtomicU64,

    // ═══ Pair Identity ═══

    /// Bitfinex symbol hash (e.g., str_to_symbol_hash("tBTCUST"))
    pub bitfinex_symbol: AtomicU64,
    /// Binance symbol hash (e.g., Binance::symbol_to_hash("BTCUSDT"))
    pub binance_symbol: AtomicU64,
    /// Pair index (0-15)
    pub pair_idx: AtomicU32,
    /// Enabled flag (1=active, 0=disabled)
    pub enabled: AtomicU32,
}

/// Global cross-exchange state — all pairs + metadata.
#[repr(C, align(64))]
pub struct CrossExchangeState {
    /// Per-pair state
    pub pairs: [CrossPairState; MAX_CROSS_PAIRS],

    // ═══ Global Metadata ═══

    /// Number of active pairs (0-16)
    pub active_pairs: AtomicU32,
    /// Global heartbeat (writer updates this every tick)
    pub heartbeat_ms: AtomicU64,
    /// Bitfinex connection alive flag
    pub bitfinex_alive: AtomicU32,
    /// Binance connection alive flag
    pub binance_alive: AtomicU32,

    // ═══ Risk Limits (Phase 5.5) ═══

    /// Maximum USD exposure on Bitfinex
    pub max_exposure_bitfinex_usd: AtomicI64,
    /// Maximum USD exposure on Binance
    pub max_exposure_binance_usd: AtomicI64,
    /// Bitfinex→Binance latency difference (ms, signed)
    pub latency_diff_ms: AtomicI64,
    /// Emergency pause (1 = halt all cross-exchange trading)
    pub emergency_pause: AtomicU32,
    /// Daily cross-exchange PnL × PRICE_SCALE
    pub daily_cross_pnl: AtomicI64,
    /// Daily loss limit × PRICE_SCALE (positive value, triggers pause if exceeded)
    pub daily_loss_limit: AtomicI64,
}

impl Default for CrossExchangeState {
    fn default() -> Self {
        let mut state: CrossExchangeState = unsafe { std::mem::zeroed() };
        // Safe defaults
        state.emergency_pause = AtomicU32::new(1); // Paused until explicitly enabled
        state.max_exposure_bitfinex_usd = AtomicI64::new(500_00000000); // $500
        state.max_exposure_binance_usd = AtomicI64::new(500_00000000);  // $500
        state.daily_loss_limit = AtomicI64::new(50_00000000);           // $50
        state
    }
}
