// ═══════════════════════════════════════════════════════════
// L2 Command Matrix v2 — Issue #18 Quick Wins
// "Thin L1, Fat L2" + SPSC Ring Buffer + False Sharing Prevention
//
// TWO independent data highways:
//   Cache Line 1: L2 → L1 (General commands soldiers)
//   Cache Line 2+: L1 → L2 (Soldiers report telemetry)
//
// Written by: L2 Oracle (Python) ← config, Rust L1 ← telemetry
// Read by: L1 bots + L2 Oracle (bidirectional)
//
// mmap path: /dev/shm/beroun/l2_command.bin
// ═══════════════════════════════════════════════════════════

use std::sync::atomic::{AtomicI64, AtomicU64, AtomicUsize, Ordering};

/// mmap file path
pub const L2_COMMAND_PATH: &str = "/dev/shm/beroun/l2_command.bin";

/// Ring buffer size (must be power of 2 for bitwise AND indexing)
pub const LATENCY_RING_SIZE: usize = 64;
pub const LATENCY_RING_MASK: usize = LATENCY_RING_SIZE - 1;

/// Minimum accumulated fade (bps) before Hydra sends Amend API call
/// Prevents API rate limit exhaustion from micro-adjustments
pub const MIN_AMEND_THRESHOLD_BPS: i64 = 3;

// ═══════════════════════════════════════════════════════════
// CACHE LINE 1: General commands (L2 → L1)
// Written by L2 Oracle (Python), read by L1 (Rust)
// align(64) guarantees own cache line — no false sharing
// ═══════════════════════════════════════════════════════════

#[repr(C, align(64))]
pub struct L2CommandMatrix {
    /// SeqLock: L2 sets to ODD before write, EVEN after write
    /// L1 reads before+after — discard if changed or odd
    pub config_version: AtomicU64,           // offset 0

    // ═══ HYDRA: Asymmetric Quote Fading ═══
    /// BID fade: push bids deeper by N bps (positive = defensive vs falling knife)
    pub bid_fade_bps: AtomicI64,             // offset 8
    /// ASK fade: push asks deeper by N bps (positive = defensive vs pump)
    pub ask_fade_bps: AtomicI64,             // offset 16

    // ═══ TRIGON: Latency-Aware Profit Padding ═══
    /// L2-computed padding added to min_profit (from p95 calculation)
    pub latency_padding_bps: AtomicI64,      // offset 24
    /// Kill switch: 1 = stop all arbitrage, 0 = OK
    pub latency_killswitch: AtomicI64,       // offset 32

    // Padding to fill 64 bytes (5 × 8B = 40B, need 24B pad)
    _pad_control: [u8; 24],                  // offset 40-63
}

// ═══════════════════════════════════════════════════════════
// CACHE LINE 2+: Soldier telemetry (L1 → L2)
// Written by L1 (Rust), read by L2 (Python)
// Separate struct = separate cache line = ZERO false sharing
// ═══════════════════════════════════════════════════════════

/// Lock-Free SPSC Ring Buffer for Tick-to-Trade latency measurement
/// L1 writes latencies, L2 reads and computes p95
#[repr(C, align(64))]
pub struct L1TelemetryRing {
    /// Ring buffer write head (L1 increments with Release ordering)
    pub latency_head: AtomicUsize,           // offset 0

    // Padding: isolate head from ring data (own cache line)
    _pad_head: [u8; 56],                     // offset 8-63

    /// 64 most recent Tick-to-Trade latencies in microseconds
    /// L1 writes with Relaxed, L2 reads after observing head advance
    pub latency_ring_us: [AtomicU64; LATENCY_RING_SIZE], // offset 64+
}

// ═══════════════════════════════════════════════════════════
// Default implementations
// ═══════════════════════════════════════════════════════════

impl Default for L2CommandMatrix {
    fn default() -> Self {
        Self {
            config_version: AtomicU64::new(0),
            bid_fade_bps: AtomicI64::new(0),
            ask_fade_bps: AtomicI64::new(0),
            latency_padding_bps: AtomicI64::new(0),
            latency_killswitch: AtomicI64::new(0),
            _pad_control: [0u8; 24],
        }
    }
}

impl Default for L1TelemetryRing {
    fn default() -> Self {
        // Safety: All AtomicU64 zeroed = valid (latency 0µs, head 0)
        unsafe { std::mem::zeroed() }
    }
}

// ═══════════════════════════════════════════════════════════
// L1 Hot-Path Helpers (all #[inline(always)], zero allocation)
// ═══════════════════════════════════════════════════════════

/// SeqLock read: returns (version, is_consistent)
/// L1 must call before AND after reading fields
#[inline(always)]
pub fn l2cmd_version_check(cmd: &L2CommandMatrix) -> (u64, bool) {
    let v = cmd.config_version.load(Ordering::Acquire);
    (v, v % 2 == 0) // even = consistent, odd = L2 mid-write
}

/// Record Tick-to-Trade latency into ring buffer (O(1), lock-free)
/// Called by Trigon L1 on each Order ACK/Fill callback
#[inline(always)]
pub fn record_latency(ring: &L1TelemetryRing, send_ts: std::time::Instant) {
    let latency_us = send_ts.elapsed().as_micros() as u64;

    // Lock-free O(1) write: bitwise AND is 1 CPU cycle (vs modulo ~15 cycles)
    let head = ring.latency_head.load(Ordering::Relaxed);
    let idx = head & LATENCY_RING_MASK;

    ring.latency_ring_us[idx].store(latency_us, Ordering::Relaxed);

    // Release ordering: guarantees Python sees data before head advance
    ring.latency_head.store(head.wrapping_add(1), Ordering::Release);
}

/// Check if arbitrage should fire (O(1), no allocation)
/// Returns true if latency conditions allow execution
#[inline(always)]
pub fn can_execute_arb(cmd: &L2CommandMatrix, gross_profit_bps: i64, base_fee_bps: i64) -> bool {
    if cmd.latency_killswitch.load(Ordering::Acquire) == 1 {
        return false;
    }
    let padding = cmd.latency_padding_bps.load(Ordering::Relaxed);
    gross_profit_bps >= (base_fee_bps + padding)
}

/// Check if Hydra should amend order (hysteresis prevents API rate limit burn)
/// Returns true only if accumulated fade exceeds threshold
#[inline(always)]
pub fn should_amend(current_price: i64, target_price: i64, fair_price: i64) -> bool {
    if fair_price == 0 { return false; }
    let diff_bps = ((current_price - target_price).abs() * 10_000) / fair_price;
    diff_bps >= MIN_AMEND_THRESHOLD_BPS
}

/// Total mmap file size: L2CommandMatrix (64B) + L1TelemetryRing (64 + 64*8 = 576B) = 640B
pub const L2_COMMAND_FILE_SIZE: usize =
    std::mem::size_of::<L2CommandMatrix>() + std::mem::size_of::<L1TelemetryRing>();
