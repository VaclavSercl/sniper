// ═══════════════════════════════════════════════════════════
// L2 Command Matrix — Issue #18 Quick Wins
// "Thin L1, Fat L2" paradigm: L2 pre-computes, L1 just reads
//
// Single cache-line (64 bytes), all atomics.
// Written by: L2 Oracle (Python, every 5 min)
// Read by: Hydra, Moonshot, Trigon L1 hot-path
//
// mmap path: /dev/shm/beroun/l2_command.bin
// ═══════════════════════════════════════════════════════════

use std::sync::atomic::{AtomicI64, AtomicU64};

/// mmap file path — shared by all components
pub const L2_COMMAND_PATH: &str = "/dev/shm/beroun/l2_command.bin";

/// L2 Command Matrix — pre-computed decisions for L1 consumption
///
/// Layout: 1 cache line (64 bytes), align(64) prevents false sharing.
/// `config_version` provides logical Torn Read protection:
///   - L2 increments to ODD before writing (= "writing in progress")
///   - L2 increments to EVEN after writing (= "data consistent")
///   - L1 reads version before AND after — if changed or odd, discard & retry
#[repr(C, align(64))]
pub struct L2CommandMatrix {
    /// Logical Torn Read protection (odd = writing, even = consistent)
    pub config_version: AtomicU64,           // offset 0

    // ═══ HYDRA: Asymmetric Quote Fading ═══
    /// BID fade: move bids deeper by this many bps (0 = no fade)
    /// Positive = further from mid (defensive vs. falling knife)
    pub bid_fade_bps: AtomicI64,             // offset 8
    /// ASK fade: move asks deeper by this many bps (0 = no fade)
    /// Positive = further from mid (defensive vs. pump)
    pub ask_fade_bps: AtomicI64,             // offset 16

    // ═══ MOONSHOT: Pre-computed Trigger Price ═══
    /// Absolute trigger price × PRICE_SCALE (L2 pre-computes: price - 3σ)
    /// L1 just does: if best_bid < moonshot_trigger_price → FIRE
    pub moonshot_trigger_price: AtomicI64,   // offset 24
    /// Armed flag: 0 = scanner only, 1 = execution allowed
    /// L2 sets to 1 only when OI/volume conditions are met
    pub moonshot_armed: AtomicU64,           // offset 32

    // ═══ TRIGON: Latency-Aware Profit Padding ═══
    /// L2-computed latency padding in bps (added to min_profit_bps)
    /// L2 reads Tick-to-Trade ring buffer, computes p95, writes this
    pub latency_padding_bps: AtomicI64,      // offset 40
    /// Kill switch: if measured latency > this ms, Trigon stops firing
    pub latency_killswitch_ms: AtomicU64,    // offset 48

    /// L2 heartbeat — epoch ms of last write
    pub l2_heartbeat_ms: AtomicU64,          // offset 56
}

impl Default for L2CommandMatrix {
    fn default() -> Self {
        Self {
            config_version: AtomicU64::new(0),
            bid_fade_bps: AtomicI64::new(0),
            ask_fade_bps: AtomicI64::new(0),
            moonshot_trigger_price: AtomicI64::new(0),
            moonshot_armed: AtomicU64::new(0),
            latency_padding_bps: AtomicI64::new(0),
            latency_killswitch_ms: AtomicU64::new(500), // 500ms default killswitch
            l2_heartbeat_ms: AtomicU64::new(0),
        }
    }
}

/// Helper: Check if L2CommandMatrix data is consistent (not mid-write)
/// Returns (version, is_valid). L1 should:
///   1. let (v1, ok1) = l2cmd_version_check(cmd);
///   2. read fields...
///   3. let (v2, ok2) = l2cmd_version_check(cmd);
///   4. if v1 != v2 || !ok1 || !ok2 { discard, retry next cycle }
#[inline(always)]
pub fn l2cmd_version_check(cmd: &L2CommandMatrix) -> (u64, bool) {
    let v = cmd.config_version.load(std::sync::atomic::Ordering::Acquire);
    (v, v % 2 == 0)  // even = consistent, odd = mid-write
}
