// ═══════════════════════════════════════════════════════════
// L2 Command Matrix v3 — Issue #18 Quick Wins (Complete Phase 1)
// "Thin L1, Fat L2" + SPSC Ring Buffer + CAS Single Bullet
//
// TWO independent data highways:
//   Cache Line 1 (64B): L2 → L1 (General commands soldiers)
//   Cache Line 2+ (576B): L1 → L2 (Soldiers report telemetry)
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
// CACHE LINE 1: General commands (L2 → L1) — EXACTLY 64 BYTES
// Written by L2 Oracle (Python), read by L1 (Rust)
// 7 fields × 8B = 56B + 8B pad = 64B = 1 x86-64 cache line
// ═══════════════════════════════════════════════════════════

#[repr(C, align(64))]
pub struct L2CommandMatrix {
    /// SeqLock: L2 sets to ODD before write, EVEN after write
    pub config_version: AtomicU64,           // offset 0   (8B)

    // ═══ HYDRA: Asymmetric Quote Fading ═══
    /// BID fade: push bids deeper by N bps (positive = defensive vs falling knife)
    pub bid_fade_bps: AtomicI64,             // offset 8   (8B)
    /// ASK fade: push asks deeper by N bps (positive = defensive vs pump)
    pub ask_fade_bps: AtomicI64,             // offset 16  (8B)

    // ═══ TRIGON: Latency-Aware Profit Padding ═══
    /// L2-computed padding added to min_profit (from p95 Tick-to-Trade)
    pub latency_padding_bps: AtomicI64,      // offset 24  (8B)
    /// Kill switch: 1 = stop all arbitrage, 0 = OK
    pub latency_killswitch: AtomicI64,       // offset 32  (8B)

    // ═══ 🌙 MOONSHOT: Pre-computed Trigger + CAS Armed ═══
    /// Absolute trigger price × PRICE_SCALE (L2 pre-computes: ema_price - 3.5σ)
    /// L1 just does: if current_price < moonshot_trigger_price → check fire
    pub moonshot_trigger_price: AtomicI64,   // offset 40  (8B)
    /// CAS armed flag: 1 = loaded (awaiting crash), 0 = safe/fired
    /// L1 uses compare_exchange(1→0) to guarantee Single Bullet Pattern
    pub moonshot_armed: AtomicI64,           // offset 48  (8B)

    // Padding: 7 × 8B = 56B → 8B pad to fill cache line
    _pad_control: [u8; 8],                   // offset 56  (8B)
}

// ═══════════════════════════════════════════════════════════
// CACHE LINE 2+: Soldier telemetry (L1 → L2)
// Separate struct = separate cache line = ZERO false sharing
// ═══════════════════════════════════════════════════════════

/// Lock-Free SPSC Ring Buffer for Tick-to-Trade latency measurement
#[repr(C, align(64))]
pub struct L1TelemetryRing {
    /// Ring buffer write head (L1 increments with Release ordering)
    pub latency_head: AtomicUsize,           // offset 0

    // Padding: isolate head from ring data (own cache line)
    _pad_head: [u8; 56],                     // offset 8-63

    /// 64 most recent Tick-to-Trade latencies in microseconds
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
            moonshot_trigger_price: AtomicI64::new(0),
            moonshot_armed: AtomicI64::new(0),
            _pad_control: [0u8; 8],
        }
    }
}

impl Default for L1TelemetryRing {
    fn default() -> Self {
        unsafe { std::mem::zeroed() }
    }
}

// ═══════════════════════════════════════════════════════════
// L1 Hot-Path Helpers (all #[inline(always)], zero allocation)
// ═══════════════════════════════════════════════════════════

/// SeqLock read: returns (version, is_consistent)
#[inline(always)]
pub fn l2cmd_version_check(cmd: &L2CommandMatrix) -> (u64, bool) {
    let v = cmd.config_version.load(Ordering::Acquire);
    (v, v % 2 == 0)
}

/// Record Tick-to-Trade latency into ring buffer (O(1), lock-free)
#[inline(always)]
pub fn record_latency(ring: &L1TelemetryRing, send_ts: std::time::Instant) {
    let latency_us = send_ts.elapsed().as_micros() as u64;
    let head = ring.latency_head.load(Ordering::Relaxed);
    let idx = head & LATENCY_RING_MASK;
    ring.latency_ring_us[idx].store(latency_us, Ordering::Relaxed);
    ring.latency_head.store(head.wrapping_add(1), Ordering::Release);
}

/// Check if arbitrage should fire (O(1))
#[inline(always)]
pub fn can_execute_arb(cmd: &L2CommandMatrix, gross_profit_bps: i64, base_fee_bps: i64) -> bool {
    if cmd.latency_killswitch.load(Ordering::Acquire) == 1 { return false; }
    let padding = cmd.latency_padding_bps.load(Ordering::Relaxed);
    gross_profit_bps >= (base_fee_bps + padding)
}

/// Check if Hydra should amend order (hysteresis prevents API rate limit burn)
#[inline(always)]
pub fn should_amend(current_price: i64, target_price: i64, fair_price: i64) -> bool {
    if fair_price == 0 { return false; }
    let diff_bps = ((current_price - target_price).abs() * 10_000) / fair_price;
    diff_bps >= MIN_AMEND_THRESHOLD_BPS
}

/// 🌙 Moonshot Tripwire: O(1) check + CAS Single Bullet
///
/// Returns Some(trigger_price) if we should fire, None otherwise.
/// Uses hardware Compare-And-Swap to guarantee exactly ONE execution
/// across all threads/ticks, even during 100k ticks/sec flash crash.
///
/// Caller must check SeqLock BEFORE calling this (for trigger_price consistency).
#[inline(always)]
pub fn moonshot_check_and_disarm(
    cmd: &L2CommandMatrix,
    current_price_scaled: i64,
    recent_volume_scaled: i64,
    avg_volume_scaled: i64,
) -> Option<i64> {
    // 1. O(1): Is weapon armed? (L2 detected macro capitulation)
    //    Branch predictor skips this 99.999% of the time
    if cmd.moonshot_armed.load(Ordering::Relaxed) != 1 {
        return None;
    }

    // 2. O(1): Price tripwire — just an integer comparison
    let trigger = cmd.moonshot_trigger_price.load(Ordering::Relaxed);
    if trigger == 0 || current_price_scaled > trigger {
        return None;
    }

    // 3. 🛡️ Anti-spoofing: volume must be abnormal (5× above average)
    //    Filters out empty-book HFT spoofing from real liquidation cascades
    if avg_volume_scaled > 0 && recent_volume_scaled < (avg_volume_scaled * 5) {
        return None;
    }

    // 4. 🚨 ATOMIC DISARM: Compare-And-Swap (1 → 0)
    //    Hardware guarantees exactly ONE thread wins this race.
    //    All other ticks see "already fired" and return None.
    match cmd.moonshot_armed.compare_exchange(
        1,                  // expect: armed
        0,                  // set: disarmed (fired)
        Ordering::Acquire,  // success: full barrier
        Ordering::Relaxed,  // failure: someone else won
    ) {
        Ok(_) => Some(trigger),  // 🔥 WE FIRED — caller executes IOC buy
        Err(_) => None,          // Another tick beat us, no-op
    }
}

/// Total mmap file size
pub const L2_COMMAND_FILE_SIZE: usize =
    std::mem::size_of::<L2CommandMatrix>() + std::mem::size_of::<L1TelemetryRing>();
