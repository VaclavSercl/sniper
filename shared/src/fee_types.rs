// 💰 Global Fee State — Single Source of Truth for ALL bots & AI
// Part of sniper-shared crate
//
// Written by: Fee Monitor Daemon (architect/fee_monitor.py) — fetches from Bitfinex /v2/auth/r/summary
// Read by: Hydra, Moonshot, Grid, Trigon, Cortex, L1, L2
//
// mmap path: /dev/shm/beroun/fee_state.bin
// Update frequency: every 30 minutes
//
// Layout: 1 cache line (64 bytes), all atomics.

use std::sync::atomic::{AtomicU64, AtomicI64};

/// mmap file path — shared by all components
pub const FEE_STATE_PATH: &str = "/dev/shm/beroun/fee_state.bin";

/// Global fee state — written by PnL daemon, read by all bots
///
/// Fee values are in basis points × 100 (for 0.01 bps precision):
///   0    = free
///   1000 = 10 bps = 0.10%
///   2000 = 20 bps = 0.20%
///
/// Example: Bitfinex "Taker 0.20%" → taker_fee_bps = 2000
#[repr(C, align(64))]
pub struct GlobalFeeState {
    /// Exchange maker fee (bps × 100). Hydra typically gets this.
    pub maker_fee_bps: AtomicU64,         // offset 0
    /// Exchange taker fee (bps × 100). IOC orders always pay taker.
    pub taker_fee_bps: AtomicU64,         // offset 8
    /// Derivative maker fee (bps × 100, for futures if used)
    pub deriv_maker_bps: AtomicU64,       // offset 16
    /// Derivative taker fee (bps × 100)
    pub deriv_taker_bps: AtomicU64,       // offset 24
    /// Epoch ms of last successful fee fetch from API
    pub last_updated_ms: AtomicU64,       // offset 32
    /// 30-day trading volume in USD (for tier calculation)
    pub monthly_volume_usd: AtomicI64,    // offset 40
    /// Fee tier level (0 = lowest, higher = better rates)
    pub fee_tier: AtomicU64,              // offset 48
    /// Writer heartbeat (epoch ms) — stale > 2h = fallback to defaults
    pub heartbeat_ms: AtomicU64,          // offset 56
}

impl Default for GlobalFeeState {
    fn default() -> Self {
        Self {
            // Conservative defaults (Bitfinex standard tier)
            maker_fee_bps: AtomicU64::new(1000),   // 0.10%
            taker_fee_bps: AtomicU64::new(2000),    // 0.20%
            deriv_maker_bps: AtomicU64::new(200),   // 0.02%
            deriv_taker_bps: AtomicU64::new(650),   // 0.065%
            last_updated_ms: AtomicU64::new(0),
            monthly_volume_usd: AtomicI64::new(0),
            fee_tier: AtomicU64::new(0),
            heartbeat_ms: AtomicU64::new(0),
        }
    }
}

/// Helper to read fee as f64 percentage (e.g., 0.001 for 0.10%)
pub fn fee_bps_to_pct(bps100: u64) -> f64 {
    bps100 as f64 / 1_000_000.0
}

/// Helper to read fee as bps (e.g., 10.0 for 0.10%)
pub fn fee_bps_to_bps(bps100: u64) -> f64 {
    bps100 as f64 / 100.0
}
