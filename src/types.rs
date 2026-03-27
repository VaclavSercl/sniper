use std::sync::LazyLock;
use std::sync::atomic::{AtomicU64, AtomicI64, AtomicU32};

pub static RISK_STATE_PATH: LazyLock<String> = LazyLock::new(|| "/dev/shm/beroun/risk_state.bin".to_string());
pub static ENGINE_STATE_PATH: LazyLock<String> = LazyLock::new(|| "/dev/shm/beroun/engine_state.bin".to_string());

pub const PRICE_SCALE: f64 = 100_000_000.0;
pub const PRICE_SCALE_I: i64 = 100_000_000;
pub const BOOK_LEVELS: usize = 25;
pub const MAX_GRID_LEVELS: usize = 5;

/// v10.0: Trading pair configuration (Multi-Pair foundation)
pub const TRADING_SYMBOL: &str = "tBTCUSD";
pub const TRADING_BASE: &str = "BTC";     // Base currency for wallet tracking
pub const TRADING_QUOTE: &str = "USD";    // Quote currency

/// Fibonacci-like spacing multipliers for grid levels
/// Level 1: 1.0x grid, Level 2: 2.5x, Level 3: 4.5x, Level 4: 7.0x, Level 5: 10.0x
pub const LEVEL_SPACING: [f64; MAX_GRID_LEVELS] = [1.0, 2.5, 4.5, 7.0, 10.0];

/// Compact repr(C) — NO per-level cache line alignment.
/// All 25 levels = 600 bytes = ~10 cache lines → excellent spatial locality
/// for sort_unstable_by sequential scan. False sharing between adjacent
/// levels is not an issue because they're updated by the same pinned thread.
#[repr(C)]
pub struct OrderBookLevel {
    pub price: AtomicU64,
    pub amount: AtomicI64, // Positive for bids, negative for asks
    pub count: AtomicU64,
}

/// Memory layout optimized for mmap IPC — false sharing prevention.
///
/// Access pattern groups separated by 64-byte cache line padding:
///   1. HEARTBEAT zone: latency_ns (written 1/s by background task)
///   2. HOT zone: best_bid/ask, bids[], asks[] (written 100s/s by main loop)
///   3. COLD zone: wallets, anti-spam, checksum (written infrequently)
///
/// Without padding, heartbeat writing latency_ns would invalidate the
/// cache line containing best_bid → cache miss on every sniper_fire.
#[repr(C, align(64))]
pub struct EngineState {
    // --- HEARTBEAT: written by background task every 1s ---
    pub latency_ns: AtomicU64,
    pub _pad_heartbeat: [u8; 56], // 8 → 64 bytes (own cache line)

    // --- HOT: updated on every book tick by main loop ---
    pub best_bid: AtomicU64,
    pub best_ask: AtomicU64,
    pub bids: [OrderBookLevel; BOOK_LEVELS],
    pub asks: [OrderBookLevel; BOOK_LEVELS],

    // --- DASHBOARD METRICS (written by main loop on sniper_fire) ---
    pub t2t_micros: AtomicU64,    // Tick-to-Trade latency (µs)
    pub micro_price: AtomicU64,   // Volume-weighted mid-price
    pub current_skew: AtomicI64,  // Inventory Skew bias (signed)

    // --- ORDER TRACKING (v9.0 Hydra Grid: multi-level) ---
    pub active_buy_ids: [AtomicU64; MAX_GRID_LEVELS],
    pub active_sell_ids: [AtomicU64; MAX_GRID_LEVELS],

    // --- INTELLIGENCE METRICS (v6.2: OBI + Dynamic Sizing) ---
    pub l2_imbalance: AtomicI64,      // OBI (-1.0..1.0) * PRICE_SCALE
    pub current_order_usd: AtomicU64, // Dynamic order size * PRICE_SCALE

    // --- cache line boundary ---
    pub _pad_hot_cold: [u8; 8],  // 64-56=8 (7 atomics = 56 bytes)

    // --- COLD: updated infrequently ---
    pub net_position: AtomicI64,
    pub realized_pnl: AtomicI64,
    pub wallet_btc: AtomicU64,
    pub wallet_usd: AtomicU64,
    pub checksum: AtomicU32,
    pub _padding: [u8; 4],
    pub last_buy_price: AtomicI64,
    pub last_sell_price: AtomicI64,

    // --- PnL TRACKER (v7.0) ---
    pub average_entry_price: AtomicI64, // WAP of current position × PRICE_SCALE

    // --- AI MANAGER (v7.0) ---
    pub current_ai_bias: AtomicI64,     // AI predicted bias × PRICE_SCALE
    pub ai_heartbeat_ms: AtomicU64,     // epoch millis of last AI write (v8.0 safety fuse)
    pub ai_alpha_usd: AtomicI64,        // Cumulative execution alpha from AI bias × PRICE_SCALE

    // --- FILL-RATE TRACKER (v9.5) ---
    pub buy_fill_count: AtomicU64,      // Incremented on each buy fill (reset by vol engine)
    pub sell_fill_count: AtomicU64,     // Incremented on each sell fill (reset by vol engine)

    // --- FEE OPTIMIZER (v10.0) ---
    pub monthly_volume_usd: AtomicU64,  // 30-day trading volume in USD × PRICE_SCALE
}

impl Default for OrderBookLevel {
    fn default() -> Self {
        Self {
            price: AtomicU64::new(0),
            amount: AtomicI64::new(0),
            count: AtomicU64::new(0),
        }
    }
}

impl Default for EngineState {
    fn default() -> Self {
        const LEVEL_DEFAULT: OrderBookLevel = OrderBookLevel {
            price: AtomicU64::new(0),
            amount: AtomicI64::new(0),
            count: AtomicU64::new(0),
        };
        Self {
            latency_ns: AtomicU64::new(0),
            _pad_heartbeat: [0; 56],
            best_bid: AtomicU64::new(0),
            best_ask: AtomicU64::new(0),
            bids: [LEVEL_DEFAULT; BOOK_LEVELS],
            asks: [LEVEL_DEFAULT; BOOK_LEVELS],
            t2t_micros: AtomicU64::new(0),
            micro_price: AtomicU64::new(0),
            current_skew: AtomicI64::new(0),
            active_buy_ids: [const { AtomicU64::new(0) }; MAX_GRID_LEVELS],
            active_sell_ids: [const { AtomicU64::new(0) }; MAX_GRID_LEVELS],
            l2_imbalance: AtomicI64::new(0),
            current_order_usd: AtomicU64::new(0),
            _pad_hot_cold: [0; 8],
            net_position: AtomicI64::new(0),
            realized_pnl: AtomicI64::new(0),
            wallet_btc: AtomicU64::new(0),
            wallet_usd: AtomicU64::new(0),
            checksum: AtomicU32::new(0),
            _padding: [0; 4],
            last_buy_price: AtomicI64::new(0),
            last_sell_price: AtomicI64::new(0),
            average_entry_price: AtomicI64::new(0),
            current_ai_bias: AtomicI64::new(0),
            ai_heartbeat_ms: AtomicU64::new(0),
            ai_alpha_usd: AtomicI64::new(0),
            buy_fill_count: AtomicU64::new(0),
            sell_fill_count: AtomicU64::new(0),
            monthly_volume_usd: AtomicU64::new(0),
        }
    }
}

#[repr(C, align(64))]
pub struct RiskState {
    // --- Written by risk-control process ---
    pub paused: AtomicU64,
    pub _pad_paused: [u8; 56], // own cache line

    // --- Read-only from main loop (written rarely by risk-control) ---
    pub grid_step: AtomicU64,
    pub grid_size: AtomicU64,
    pub order_usd: AtomicU64,
    pub max_inv_delta: AtomicU64,
    pub bias_offset: AtomicI64,
    pub authorized_capital: AtomicU64,  // Capital Guard: max USD the bot may use (0 = unlimited)
    pub daily_loss_limit: AtomicU64,    // Daily Loss Limit: auto-pause if PnL drops below -this (0 = disabled)
    pub _padding: [u8; 8],
}

impl Default for RiskState {
    fn default() -> Self {
        Self { 
            paused: AtomicU64::new(0),
            _pad_paused: [0; 56],
            grid_step: AtomicU64::new((3.0 * PRICE_SCALE) as u64),
            grid_size: AtomicU64::new(3),  // v9.0 Hydra: 3 levels per side
            order_usd: AtomicU64::new((50.0 * PRICE_SCALE) as u64),
            max_inv_delta: AtomicU64::new((0.005 * PRICE_SCALE) as u64),
            bias_offset: AtomicI64::new(0),
            authorized_capital: AtomicU64::new((400.0 * PRICE_SCALE) as u64), // $400 default
            daily_loss_limit: AtomicU64::new((20.0 * PRICE_SCALE) as u64),    // $20 default
            _padding: [0; 8],
        }
    }
}
