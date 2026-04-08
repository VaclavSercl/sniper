// 🔺 Trigon Shared Types — Triangular Arbitrage Engine
// Part of sniper-shared crate
//
// Mmap layout for Trigon bot IPC. All fields are lock-free atomics.
// Cache-aligned to 64 bytes per triangle for L1 cache line isolation.
//
// Triangular Arbitrage: A→B→C→A
// Example: USD→BTC→ETH→USD (tBTCUSD, tETHBTC, tETHUSD)
// Profit if: price(A→B) × price(B→C) × price(C→A) > 1 + 3×fee

use std::sync::atomic::{AtomicU64, AtomicI64, AtomicU32};

/// Maximum number of simultaneously tracked triangles
pub const TRIGON_MAX_TRIANGLES: usize = 24;

/// Maximum number of legs per triangle (always 3 but for array sizing)
pub const TRIGON_LEGS: usize = 3;

/// mmap file paths
pub const TRIGON_ENGINE_PATH: &str = "/dev/shm/sniper/trigon_engine.bin";
pub const TRIGON_RISK_PATH: &str = "/dev/shm/sniper/trigon_risk.bin";

// ═══════════════════════════════════════════════════════════
// Leg State — one side of the triangle (e.g., tBTCUSD)
// ═══════════════════════════════════════════════════════════

/// Per-leg market data
#[repr(C, align(64))]
pub struct TrigonLeg {
    pub symbol_hash: AtomicU64,        // 8-byte ASCII symbol hash
    pub best_bid: AtomicU64,           // Current BID × PRICE_SCALE
    pub best_ask: AtomicU64,           // Current ASK × PRICE_SCALE
    pub spread_bps: AtomicU64,         // Spread in basis points × 100
    pub volume_24h: AtomicU64,         // 24h volume (USD equiv)
    pub latency_ns: AtomicU64,         // Last tick processing latency
    pub direction: AtomicU32,          // 0 = BUY (A→B), 1 = SELL (B→A)
    pub chan_id: AtomicI64,            // WebSocket channel ID
    pub _padding: [u8; 12],
}

// ═══════════════════════════════════════════════════════════
// Triangle State — 3 legs forming an arbitrage opportunity
// ═══════════════════════════════════════════════════════════

/// Per-triangle engine state (live calculation)
#[repr(C, align(64))]
pub struct TrigonTriangle {
    pub legs: [TrigonLeg; TRIGON_LEGS],
    pub implied_rate: AtomicI64,       // A→B→C→A rate × PRICE_SCALE (>1.0 = profit)
    pub profit_bps: AtomicI64,         // Profit in bps after fees (negative = loss)
    pub fee_cost_bps: AtomicU64,       // Total round-trip fee in bps
    pub last_calc_ns: AtomicU64,       // Timestamp of last calculation
    pub executions: AtomicU64,         // Lifetime successful arb executions
    pub total_pnl: AtomicI64,          // Lifetime PnL from this triangle (USD × PRICE_SCALE)
    pub active: AtomicU32,             // 1 = actively monitored, 0 = disabled
    pub executing: AtomicU32,          // 1 = currently executing arb, 0 = idle
}

/// Global Trigon engine state
#[repr(C, align(64))]
pub struct TrigonEngineState {
    pub triangles: [TrigonTriangle; TRIGON_MAX_TRIANGLES],
    pub wallet_usd: AtomicU64,         // USD balance × PRICE_SCALE
    pub wallet_btc: AtomicU64,
    pub wallet_eth: AtomicU64,
    pub total_arbs: AtomicU64,         // Total successful arbitrages (lifetime)
    pub total_pnl: AtomicI64,          // Total PnL across all triangles
    pub daily_pnl: AtomicI64,          // Daily realized PnL
    pub virtual_realized_pnl: AtomicI64,
    pub heartbeat_ms: AtomicU64,       // Engine heartbeat (epoch ms)
    pub scan_latency_ns: AtomicU64,    // Full scan latency (all triangles)
    pub best_profit_bps: AtomicI64,    // Current best opportunity (bps)
}

// ═══════════════════════════════════════════════════════════
// Risk State — written by L2 Oracle (Python), read by L0 (Rust)
// ═══════════════════════════════════════════════════════════

/// Per-triangle risk parameters
#[repr(C, align(64))]
pub struct TrigonTriangleRisk {
    pub leg_symbols: [AtomicU64; TRIGON_LEGS], // Symbol hashes for each leg
    pub leg_directions: [AtomicU32; TRIGON_LEGS], // 0=BUY, 1=SELL per leg
    pub min_profit_bps: AtomicI64,     // Minimum profit threshold to execute (bps)
    pub max_order_usd: AtomicU64,      // Maximum order size per execution (USD × PRICE_SCALE)
    pub cooldown_ms: AtomicU64,        // Minimum time between executions (ms)
    pub enabled: AtomicU32,            // 1 = enabled, 0 = disabled
    pub _padding: [u8; 20],
}

/// Global Trigon risk state
#[repr(C, align(64))]
pub struct TrigonRiskState {
    pub triangles: [TrigonTriangleRisk; TRIGON_MAX_TRIANGLES],
    pub global_paused: AtomicU64,      // 1 = all paused, 0 = live
    pub daily_loss_limit: AtomicI64,   // DLL (USD × PRICE_SCALE, positive)
    pub max_concurrent: AtomicU32,     // Max simultaneous executions
    pub fee_bps: AtomicU64,            // Exchange fee in bps (per trade)
    pub ai_heartbeat_ms: AtomicU64,    // Stale > 120min → safe mode
}

// ═══════════════════════════════════════════════════════════
// Default implementations
// ═══════════════════════════════════════════════════════════

impl Default for TrigonEngineState {
    fn default() -> Self {
        unsafe { std::mem::zeroed() }
    }
}

impl TrigonEngineState {
    pub fn optimistic_flush_active_orders(&self) {
        for t in 0..TRIGON_MAX_TRIANGLES {
            self.triangles[t].executing.store(0, std::sync::atomic::Ordering::Release);
        }
    }
}

impl Default for TrigonRiskState {
    fn default() -> Self {
        let mut rs: TrigonRiskState = unsafe { std::mem::zeroed() };
        rs.global_paused = AtomicU64::new(1); // Paused by default
        rs.fee_bps = AtomicU64::new(20);       // 0.20% default Bitfinex fee
        rs.max_concurrent = AtomicU32::new(1);
        rs
    }
}

// ═══════════════════════════════════════════════════════════
// Pre-defined Triangle Configurations
// ═══════════════════════════════════════════════════════════

/// Bitfinex triangles — BTC + Fiat + Stablecoins only (no altcoins)
/// Format: (leg_A, leg_B, leg_C)
pub const KNOWN_TRIANGLES: &[(&str, &str, &str)] = &[
    // ═══ EUR CROSSES ═══
    // USD → BTC → EUR → UST
    ("tBTCUSD", "tBTCEUR", "tEURUST"),
    // USD → BTC → EURQ → USD  (Quantoz tokenized EUR)
    ("tBTCUSD", "tBTC:EURQ", "tEURQ:USD"),
    // USD → BTC → EURR → USD  (tokenized EUR #2)
    ("tBTCUSD", "tBTC:EURR", "tEURR:USD"),

    // ═══ GBP CROSSES ═══
    // USD → BTC → GBP → UST
    ("tBTCUSD", "tBTCGBP", "tGBPUST"),

    // ═══ STABLECOIN ARBITRAGE ═══
    // USD → BTC → UST → USD  (USDT peg deviation)
    ("tBTCUSD", "tBTCUST", "tUSTUSD"),
    // UST → UDC → USD  (USDC/USDT/USD triple stablecoin arb)
    ("tUSTUSD", "tUDCUST", "tUDCUSD"),

    // ═══ CROSS-STABLECOIN EUR ═══
    // UST → BTC → EURQ → UST  (EUR/USDT via EURQ token)
    ("tBTCUST", "tBTC:EURQ", "tEURQ:UST"),
    // UST → BTC → EURR → UST  (EUR/USDT via EURR token)
    ("tBTCUST", "tBTC:EURR", "tEURR:UST"),

    // ═══ ETH CROSSES (M4 Scale-up) ═══
    ("tETHUSD", "tETHUST", "tUSTUSD"),
    ("tETHUSD", "tETHEUR", "tEURUST"),
    ("tETHUSD", "tETHGBP", "tGBPUST"),

    // ═══ SOL CROSSES (M4 Scale-up) ═══
    ("tSOLUSD", "tSOLUST", "tUSTUSD"),
    ("tSOLUSD", "tSOLEUR", "tEURUST"),
    ("tSOLUSD", "tSOLGBP", "tGBPUST"),
];
