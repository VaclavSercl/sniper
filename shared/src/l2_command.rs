// ═══════════════════════════════════════════════════════════
// L2 Command Matrix v4 — Phase 1 Defense + Phase 2 Offense
// "Thin L1, Fat L2" + A-S Model + SPSC Ring Buffer
//
// THREE independent cache-line highways:
//   Cache Line 1 (64B): L2 → L1 Tactical Defense (Phase 1)
//   Cache Line 2 (64B): L2 → L1 Structural Offense (Phase 2 A-S)
//   Cache Line 3+ (576B): L1 → L2 Telemetry (Ring Buffer)
//
// SeqLock (config_version) protects BOTH cache lines 1+2 atomically.
// mmap path: /dev/shm/beroun/l2_command.bin
// ═══════════════════════════════════════════════════════════

use std::sync::atomic::{AtomicI64, AtomicU64, AtomicUsize, Ordering};

pub const L2_COMMAND_PATH: &str = "/dev/shm/beroun/l2_command.bin";

pub const LATENCY_RING_SIZE: usize = 64;
pub const LATENCY_RING_MASK: usize = LATENCY_RING_SIZE - 1;

pub const MIN_AMEND_THRESHOLD_BPS: i64 = 3;

pub const BTC_SCALE: i64 = 100_000_000;

// ═══════════════════════════════════════════════════════════
// CACHE LINE 1: Tactical Defense (L2 → L1) — Phase 1
// 7 fields × 8B = 56B + 8B pad = 64B
// ═══════════════════════════════════════════════════════════

#[repr(C, align(64))]
pub struct L2CommandMatrix {
    /// SeqLock: protects BOTH cache lines 1 and 2
    pub config_version: AtomicU64,           // CL1 offset 0

    // ═══ HYDRA: Asymmetric Quote Fading ═══
    pub bid_fade_bps: AtomicI64,             // CL1 offset 8
    pub ask_fade_bps: AtomicI64,             // CL1 offset 16

    // ═══ TRIGON: Latency-Aware Profit Padding ═══
    pub latency_padding_bps: AtomicI64,      // CL1 offset 24
    pub latency_killswitch: AtomicI64,       // CL1 offset 32

    // ═══ 🌙 MOONSHOT: CAS Tripwire ═══
    pub moonshot_trigger_price: AtomicI64,   // CL1 offset 40
    pub moonshot_armed: AtomicI64,           // CL1 offset 48

    _pad_cl1: [u8; 8],                       // CL1 offset 56 (pad to 64B)
}

// ═══════════════════════════════════════════════════════════
// CACHE LINE 2: Structural Offense / A-S Model (L2 → L1) — Phase 2
// Protected by same SeqLock (config_version) as Cache Line 1
// 3 L2→L1 params + 1 L1→L2 report + pad = 64B
// ═══════════════════════════════════════════════════════════

/// Avellaneda-Stoikov Regime-Aware Market Making parameters
#[repr(C, align(64))]
pub struct L2ASMatrix {
    /// Target inventory × PRICE_SCALE (0 = delta-neutral, +50M = +0.5 BTC long)
    /// L2 sets based on HMM regime: bull → positive target, bear → negative
    pub as_target_inventory: AtomicI64,      // CL2 offset 0

    /// Skew factor: bps shift per 1 BTC deviation from target
    /// = gamma × variance, pre-computed by L2 (higher = more aggressive rebalancing)
    pub as_skew_factor_bps: AtomicI64,       // CL2 offset 8

    /// Half of optimal spread in bps (bid = reservation - half, ask = reservation + half)
    pub as_half_spread_bps: AtomicI64,       // CL2 offset 16

    // ═══ L1 → L2: Inventory Report ═══
    /// L1 atomically writes current position here (× PRICE_SCALE)
    /// L2 reads this to compute regime awareness
    pub current_inventory: AtomicI64,        // CL2 offset 24

    _pad_cl2: [u8; 32],                      // CL2 offset 32 (pad to 64B)
}

// ═══════════════════════════════════════════════════════════
// CACHE LINE 3+: Telemetry (L1 → L2) — SPSC Ring Buffer
// ═══════════════════════════════════════════════════════════

#[repr(C, align(64))]
pub struct L1TelemetryRing {
    pub latency_head: AtomicUsize,
    _pad_head: [u8; 56],
    pub latency_ring_us: [AtomicU64; LATENCY_RING_SIZE],
}

// ═══════════════════════════════════════════════════════════
// Defaults
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
            _pad_cl1: [0u8; 8],
        }
    }
}

impl Default for L2ASMatrix {
    fn default() -> Self {
        Self {
            as_target_inventory: AtomicI64::new(0),
            as_skew_factor_bps: AtomicI64::new(5),  // conservative default
            as_half_spread_bps: AtomicI64::new(3),   // 3 bps half-spread
            current_inventory: AtomicI64::new(0),
            _pad_cl2: [0u8; 32],
        }
    }
}

impl Default for L1TelemetryRing {
    fn default() -> Self {
        unsafe { std::mem::zeroed() }
    }
}

// ═══════════════════════════════════════════════════════════
// L1 Hot-Path Helpers
// ═══════════════════════════════════════════════════════════

#[inline(always)]
pub fn l2cmd_version_check(cmd: &L2CommandMatrix) -> (u64, bool) {
    let v = cmd.config_version.load(Ordering::Acquire);
    (v, v % 2 == 0)
}

#[inline(always)]
pub fn record_latency(ring: &L1TelemetryRing, send_ts: std::time::Instant) {
    let latency_us = send_ts.elapsed().as_micros() as u64;
    let head = ring.latency_head.load(Ordering::Relaxed);
    let idx = head & LATENCY_RING_MASK;
    ring.latency_ring_us[idx].store(latency_us, Ordering::Relaxed);
    ring.latency_head.store(head.wrapping_add(1), Ordering::Release);
}

#[inline(always)]
pub fn can_execute_arb(cmd: &L2CommandMatrix, gross_profit_bps: i64, base_fee_bps: i64) -> bool {
    if cmd.latency_killswitch.load(Ordering::Acquire) == 1 { return false; }
    let padding = cmd.latency_padding_bps.load(Ordering::Relaxed);
    gross_profit_bps >= (base_fee_bps + padding)
}

#[inline(always)]
pub fn should_amend(current_price: i64, target_price: i64, fair_price: i64) -> bool {
    if fair_price == 0 { return false; }
    let diff_bps = ((current_price - target_price).abs() * 10_000) / fair_price;
    diff_bps >= MIN_AMEND_THRESHOLD_BPS
}

/// Moonshot CAS Single Bullet
#[inline(always)]
pub fn moonshot_check_and_disarm(
    cmd: &L2CommandMatrix,
    current_price_scaled: i64,
    recent_volume_scaled: i64,
    avg_volume_scaled: i64,
) -> Option<i64> {
    if cmd.moonshot_armed.load(Ordering::Relaxed) != 1 { return None; }
    let trigger = cmd.moonshot_trigger_price.load(Ordering::Relaxed);
    if trigger == 0 || current_price_scaled > trigger { return None; }
    if avg_volume_scaled > 0 && recent_volume_scaled < (avg_volume_scaled * 5) { return None; }
    match cmd.moonshot_armed.compare_exchange(1, 0, Ordering::Acquire, Ordering::Relaxed) {
        Ok(_) => Some(trigger),
        Err(_) => None,
    }
}

/// 🐍 A-S Quote Calculator: Offense + Defense fusion (O(1), zero allocation)
///
/// Computes optimal bid/ask by fusing:
///   1. A-S Reservation Price (inventory skew toward target)
///   2. Phase 1 Quote Fading (toxic flow defense)
///
/// Returns (target_bid_scaled, target_ask_scaled)
#[inline(always)]
pub fn calculate_as_quotes(
    cmd: &L2CommandMatrix,
    as_mat: &L2ASMatrix,
    fair_price: i64,
    current_inventory: i64,
) -> (i64, i64) {
    // Report inventory to L2 (lock-free, separate cache line)
    as_mat.current_inventory.store(current_inventory, Ordering::Relaxed);

    // SeqLock read: version protects BOTH cache lines
    let mut seq;
    let (mut bid_fade, mut ask_fade, mut target_inv, mut skew_bps, mut half_spread);
    loop {
        seq = cmd.config_version.load(Ordering::Acquire);
        if seq % 2 != 0 { std::hint::spin_loop(); continue; }

        // Cache Line 1: defense
        bid_fade = cmd.bid_fade_bps.load(Ordering::Relaxed);
        ask_fade = cmd.ask_fade_bps.load(Ordering::Relaxed);

        // Cache Line 2: offense (A-S)
        target_inv = as_mat.as_target_inventory.load(Ordering::Relaxed);
        skew_bps = as_mat.as_skew_factor_bps.load(Ordering::Relaxed);
        half_spread = as_mat.as_half_spread_bps.load(Ordering::Relaxed);

        std::sync::atomic::fence(Ordering::Acquire);
        if seq == cmd.config_version.load(Ordering::Relaxed) { break; }
    }

    // A-S Reservation Price: shift center based on inventory delta
    let inventory_delta = current_inventory - target_inv;
    let as_shift_bps = (inventory_delta * skew_bps) / BTC_SCALE;

    let bps_val = fair_price / 10_000;

    // Reservation price = fair - (inventory_overshoot × skew)
    // Overweight long → reservation drops → asks get cheaper → dump inventory
    let reservation = fair_price - (as_shift_bps * bps_val);

    // 💥 FUSION: A-S spread + Phase 1 toxic fade
    let target_bid = reservation - (half_spread * bps_val) - (bid_fade * bps_val);
    let target_ask = reservation + (half_spread * bps_val) + (ask_fade * bps_val);

    (target_bid, target_ask)
}

/// Total mmap file size: CL1(64) + CL2(64) + Ring(64+512) = 704B
pub const L2_COMMAND_FILE_SIZE: usize =
    std::mem::size_of::<L2CommandMatrix>()
    + std::mem::size_of::<L2ASMatrix>()
    + std::mem::size_of::<L1TelemetryRing>();
