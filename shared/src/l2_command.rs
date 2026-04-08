// ═══════════════════════════════════════════════════════════
// L2 Command Matrix v6 — Phase 1 + 2 + 3 Complete
// "Thin L1, Fat L2" Sovereign Intelligence Architecture
//
// SIX cache-line highways:
//   CL1 (64B): L2→L1 Tactical Defense (fade, latency, moonshot)
//   CL2 (64B): L2→L1 A-S Offense (inventory skew, spread)
//   CL3 (64B): L2→L1 Grid Warp (quadratic topology)
//   CL4 (64B): L2→L1 Global Risk (VPIN, Aegis hedger)
//   CL5 (64B): L1→L2 Portfolio Telemetry (all bot inventories)
//   CL6+ (576B): L1→L2 Latency Ring Buffer
//
// mmap: /dev/shm/beroun/l2_command.bin (896 bytes)
// ═══════════════════════════════════════════════════════════

use std::sync::atomic::{AtomicI64, AtomicU64, AtomicUsize, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

pub const L2_COMMAND_PATH: &str = "/dev/shm/beroun/l2_command.bin";
pub const LATENCY_RING_SIZE: usize = 64;
pub const LATENCY_RING_MASK: usize = LATENCY_RING_SIZE - 1;
pub const MIN_AMEND_THRESHOLD_BPS: i64 = 3;
pub const BTC_SCALE: i64 = 100_000_000;

// ═══ CL1: Tactical Defense (Phase 1) ═══
#[repr(C, align(64))]
pub struct L2CommandMatrix {
    pub config_version: AtomicU64,
    pub bid_fade_bps: AtomicI64,
    pub ask_fade_bps: AtomicI64,
    pub latency_padding_bps: AtomicI64,
    pub latency_killswitch: AtomicI64,
    pub moonshot_trigger_price: AtomicI64,
    pub moonshot_armed: AtomicI64,
    _pad_cl1: [u8; 8],
}

// ═══ CL2: A-S Offense (Phase 2a) ═══
#[repr(C, align(64))]
pub struct L2ASMatrix {
    pub as_target_inventory: AtomicI64,
    pub as_skew_factor_bps: AtomicI64,
    pub as_half_spread_bps: AtomicI64,
    pub current_inventory: AtomicI64,
    _pad_cl2: [u8; 32],
}

// ═══ CL3: Grid Gaussian Warp (Phase 2b) ═══
#[repr(C, align(64))]
pub struct L2GridWarpMatrix {
    pub grid_config_version: AtomicU64,
    pub grid_dynamic_anchor: AtomicI64,
    pub grid_base_step_bps: AtomicI64,
    pub grid_warp_factor: AtomicI64,
    pub grid_max_bid_levels: AtomicI64,
    pub grid_max_ask_levels: AtomicI64,
    _pad_cl3: [u8; 16],
}

// ═══ CL4: Global Risk & Aegis Hedger (Phase 3) + Cross-Bot Signals ═══
#[repr(C, align(64))]
pub struct L2GlobalRiskMatrix {
    /// Independent SeqLock for macro risk
    pub risk_config_version: AtomicU64,
    /// VPIN directional toxicity × PRICE_SCALE: -1e8 (dump) to +1e8 (pump)
    pub global_vpin_toxicity: AtomicI64,
    /// Aegis target delta × PRICE_SCALE (negative = short perps)
    pub aegis_target_delta: AtomicI64,
    /// 1 = portfolio hedged via perps, Grid stops buying
    pub portfolio_is_hedged: AtomicI64,
    /// Cross-bot: epoch ms when flash crash detected by Moonshot (0 = clear)
    pub flash_crash_epoch_ms: AtomicU64,
    /// Cross-bot: magnitude of drop in bps (e.g., -300 = -3%)
    pub flash_crash_drop_bps: AtomicI64,
    /// Pravděpodobnost klidného trhu (0.0 až 1.0) v u64 (škálováno 1e8)
    pub ranging_score: AtomicU64,
    /// Pravděpodobnost silného trendu/průrazu (0.0 až 1.0) v u64 (škálováno 1e8)
    pub trending_score: AtomicU64,
}

// ═══ CL5: Portfolio Telemetry (L1 → L2, Phase 3) ═══
#[repr(C, align(64))]
pub struct L2PortfolioTelemetry {
    pub hydra_inventory: AtomicI64,
    pub grid_inventory: AtomicI64,
    pub moonshot_inventory: AtomicI64,
    pub aegis_current_delta: AtomicI64,
    _pad_cl5: [u8; 32],
}

// ═══ CL6+: Latency Ring Buffer ═══
#[repr(C, align(64))]
pub struct L1TelemetryRing {
    pub latency_head: AtomicUsize,
    _pad_head: [u8; 56],
    pub latency_ring_us: [AtomicU64; LATENCY_RING_SIZE],
}

// ═══ MASTER STRUCT (v12.0) ═══
/// unified mmap layout, eliminating manual pointer arithmetic.
#[repr(C, align(64))]
#[derive(Default)]
pub struct L2SharedState {
    pub cmd: L2CommandMatrix,           // Offset 0 (CL1)
    pub as_mat: L2ASMatrix,             // Offset 64 (CL2)
    pub grid_warp: L2GridWarpMatrix,    // Offset 128 (CL3)
    pub global_risk: L2GlobalRiskMatrix,// Offset 192 (CL4)
    pub portfolio: L2PortfolioTelemetry,// Offset 256 (CL5)
    pub latency_ring: L1TelemetryRing,  // Offset 320 (CL6+)
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
            as_skew_factor_bps: AtomicI64::new(5),
            as_half_spread_bps: AtomicI64::new(3),
            current_inventory: AtomicI64::new(0),
            _pad_cl2: [0u8; 32],
        }
    }
}

impl Default for L2GridWarpMatrix {
    fn default() -> Self {
        Self {
            grid_config_version: AtomicU64::new(0),
            grid_dynamic_anchor: AtomicI64::new(0),
            grid_base_step_bps: AtomicI64::new(10),
            grid_warp_factor: AtomicI64::new(0),
            grid_max_bid_levels: AtomicI64::new(15),
            grid_max_ask_levels: AtomicI64::new(15),
            _pad_cl3: [0u8; 16],
        }
    }
}

impl Default for L2GlobalRiskMatrix {
    fn default() -> Self {
        Self {
            risk_config_version: AtomicU64::new(0),
            global_vpin_toxicity: AtomicI64::new(0),
            aegis_target_delta: AtomicI64::new(0),
            portfolio_is_hedged: AtomicI64::new(0),
            flash_crash_epoch_ms: AtomicU64::new(0),
            flash_crash_drop_bps: AtomicI64::new(0),
            ranging_score: AtomicU64::new(0),
            trending_score: AtomicU64::new(0),
        }
    }
}

impl Default for L2PortfolioTelemetry {
    fn default() -> Self {
        Self {
            hydra_inventory: AtomicI64::new(0),
            grid_inventory: AtomicI64::new(0),
            moonshot_inventory: AtomicI64::new(0),
            aegis_current_delta: AtomicI64::new(0),
            _pad_cl5: [0u8; 32],
        }
    }
}

impl Default for L1TelemetryRing {
    fn default() -> Self { unsafe { std::mem::zeroed() } }
}

// ═══════════════════════════════════════════════════════════
// L1 Hot-Path Helpers (all #[inline(always)], zero allocation)
// ═══════════════════════════════════════════════════════════

#[inline(always)]
pub fn l2cmd_version_check(cmd: &L2CommandMatrix) -> (u64, bool) {
    let v = cmd.config_version.load(Ordering::Acquire);
    (v, v.is_multiple_of(2))
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
    cmd: &L2CommandMatrix, current_price_scaled: i64,
    recent_volume_scaled: i64, avg_volume_scaled: i64,
) -> Option<i64> {
    if cmd.moonshot_armed.load(Ordering::Relaxed) != 1 { return None; }
    let trigger = cmd.moonshot_trigger_price.load(Ordering::Relaxed);
    if trigger == 0 || current_price_scaled > trigger { return None; }
    if avg_volume_scaled > 0 && recent_volume_scaled < (avg_volume_scaled * 5) { return None; }
    match cmd.moonshot_armed.compare_exchange(1, 0, Ordering::Acquire, Ordering::Relaxed) {
        Ok(_) => Some(trigger), Err(_) => None,
    }
}

/// A-S + Fade fusion quote calculator
#[inline(always)]
pub fn calculate_as_quotes(
    cmd: &L2CommandMatrix, as_mat: &L2ASMatrix,
    fair_price: i64, current_inventory: i64,
) -> (i64, i64) {
    as_mat.current_inventory.store(current_inventory, Ordering::Relaxed);
    let mut seq;
    let (mut bid_fade, mut ask_fade, mut target_inv, mut skew_bps, mut half_spread);
    loop {
        seq = cmd.config_version.load(Ordering::Acquire);
        if seq % 2 != 0 { std::hint::spin_loop(); continue; }
        bid_fade = cmd.bid_fade_bps.load(Ordering::Relaxed);
        ask_fade = cmd.ask_fade_bps.load(Ordering::Relaxed);
        target_inv = as_mat.as_target_inventory.load(Ordering::Relaxed);
        skew_bps = as_mat.as_skew_factor_bps.load(Ordering::Relaxed);
        half_spread = as_mat.as_half_spread_bps.load(Ordering::Relaxed);
        std::sync::atomic::fence(Ordering::Acquire);
        if seq == cmd.config_version.load(Ordering::Relaxed) { break; }
    }
    let inventory_delta = current_inventory - target_inv;
    let as_shift_bps = (inventory_delta * skew_bps) / BTC_SCALE;
    let bps_val = fair_price / 10_000;
    let reservation = fair_price - (as_shift_bps * bps_val);
    let target_bid = reservation - (half_spread * bps_val) - (bid_fade * bps_val);
    let target_ask = reservation + (half_spread * bps_val) + (ask_fade * bps_val);
    (target_bid, target_ask)
}

/// Grid Gaussian Warp: quadratic level calculator
#[inline(always)]
pub fn calculate_warped_grid_level(
    grid: &L2GridWarpMatrix, level_index: i64, is_bid: bool,
) -> Option<i64> {
    let mut seq;
    let (mut anchor, mut base_step, mut warp, mut max_bid, mut max_ask);
    loop {
        seq = grid.grid_config_version.load(Ordering::Acquire);
        if seq % 2 != 0 { std::hint::spin_loop(); continue; }
        anchor = grid.grid_dynamic_anchor.load(Ordering::Relaxed);
        base_step = grid.grid_base_step_bps.load(Ordering::Relaxed);
        warp = grid.grid_warp_factor.load(Ordering::Relaxed);
        max_bid = grid.grid_max_bid_levels.load(Ordering::Relaxed);
        max_ask = grid.grid_max_ask_levels.load(Ordering::Relaxed);
        std::sync::atomic::fence(Ordering::Acquire);
        if seq == grid.grid_config_version.load(Ordering::Relaxed) { break; }
    }
    if is_bid && level_index > max_bid { return None; }
    if !is_bid && level_index > max_ask { return None; }
    let n_sq = level_index * level_index;
    let distance_bps = (base_step * level_index) + (warp * n_sq);
    let bps_val = anchor / 10_000;
    if bps_val == 0 { return None; }
    let price_delta = distance_bps * bps_val;
    if is_bid { Some(anchor - price_delta) } else { Some(anchor + price_delta) }
}

// ═══════════════════════════════════════════════════════════
// Phase 3: Global Risk Helpers
// ═══════════════════════════════════════════════════════════

/// Grid hedge check: false = portfolio shield active, don't place bids
#[inline(always)]
pub fn should_grid_place_bid(risk: &L2GlobalRiskMatrix) -> bool {
    risk.portfolio_is_hedged.load(Ordering::Relaxed) != 1
}

/// VPIN shift in bps (branchless). ±30 bps max.
/// Negative VPIN (dump) → negative shift → bids retreat, asks drop
#[inline(always)]
pub fn vpin_shift_bps(risk: &L2GlobalRiskMatrix) -> i64 {
    let toxicity = risk.global_vpin_toxicity.load(Ordering::Relaxed);
    (toxicity * 30) / BTC_SCALE
}

// ═══════════════════════════════════════════════════════════
// Phase 3.3: Cross-Bot Flash Crash Signal Helpers
// ═══════════════════════════════════════════════════════════

/// Flash crash signal TTL — auto-expires after 30 seconds (stale protection)
pub const FLASH_CRASH_TTL_MS: u64 = 30_000;

/// Check if a cross-bot flash crash signal is currently active and not stale.
/// Called by Hydra every tick — zero-cost when no crash (single atomic load).
#[inline(always)]
pub fn is_flash_crash_active(risk: &L2GlobalRiskMatrix) -> bool {
    let ts = risk.flash_crash_epoch_ms.load(Ordering::Acquire);
    if ts == 0 { return false; }
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    now_ms.saturating_sub(ts) < FLASH_CRASH_TTL_MS
}

/// Write a flash crash signal (called by Moonshot when wick detected).
#[inline(always)]
pub fn signal_flash_crash(risk: &L2GlobalRiskMatrix, drop_bps: i64) {
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    risk.flash_crash_drop_bps.store(drop_bps, Ordering::Relaxed);
    risk.flash_crash_epoch_ms.store(now_ms, Ordering::Release);
}

/// Clear the flash crash signal (called by Moonshot after recovery / TTL).
#[inline(always)]
pub fn clear_flash_crash(risk: &L2GlobalRiskMatrix) {
    risk.flash_crash_epoch_ms.store(0, Ordering::Release);
    risk.flash_crash_drop_bps.store(0, Ordering::Relaxed);
}


/// Total mmap size is simply the size of the Master Struct (896B)
pub const L2_COMMAND_FILE_SIZE: usize = std::mem::size_of::<L2SharedState>();

pub fn load_l2_shared_state_ro() -> &'static L2SharedState {
    let file = std::fs::File::open(L2_COMMAND_PATH)
        .expect("🔥 L2 Command State neexistuje.");
    
    let mmap = unsafe { memmap2::MmapOptions::new().map(&file).unwrap() };
    let mmap_ref = Box::leak(Box::new(mmap));
    
    let state_ptr = mmap_ref.as_ptr() as *const L2SharedState;
    unsafe { &*state_ptr }
}
