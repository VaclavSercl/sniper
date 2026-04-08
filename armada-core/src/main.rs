use std::sync::atomic::Ordering;
use std::time::Duration;
use tokio::time::interval;
use anyhow::{Context, Result};
use sniper_types::armada_types::{ArmadaState, ArmadaStateV2, load_oracle_state_ro, capital_index, MAX_BOTS, MAX_VENUES};
use sniper_types::mmap_utils::{init_mmap, open_mmap_readonly};

const ARMADA_STATE_V1_PATH: &str = "/dev/shm/beroun/armada_state.bin";
const ARMADA_STATE_V2_PATH: &str = "/dev/shm/beroun/armada_state_v2.bin";

#[tokio::main]
async fn main() -> Result<()> {
    println!("[ARMADA] Booting Orchestrator Daemon v2.0 (Dual-Write: V1+V2)...");
    
    // ═══ V1 Legacy Shim (backward compat for bots not yet migrated) ═══
    let mut armada_v1_mmap = init_mmap::<ArmadaState>(ARMADA_STATE_V1_PATH)
        .context("Failed to initialize armada V1 state mmap")?;
    let state_v1 = unsafe { &mut *(armada_v1_mmap.as_mut_ptr() as *mut ArmadaState) };

    // ═══ V2 New Architecture (Issue #16, 1088B = 17 CL) ═══
    let mut armada_v2_mmap = init_mmap::<ArmadaStateV2>(ARMADA_STATE_V2_PATH)
        .context("Failed to initialize armada V2 state mmap")?;
    let state_v2 = unsafe { &mut *(armada_v2_mmap.as_mut_ptr() as *mut ArmadaStateV2) };

    // ═══ Read-Only References ═══
    let oracle_state = load_oracle_state_ro();
    let l2_state = sniper_types::l2_command::load_l2_shared_state_ro();

    // ═══ Toxic Storm: mmap pointer (ZERO-ALLOCATION read in hot loop) ═══
    // Fix B5: Replaced std::fs::read() + vec![0] with mmap pointer
    let toxic_mmap = open_mmap_readonly("/dev/shm/beroun/toxic_storm.bin")
        .context("Failed to mmap toxic_storm.bin")?;
    let toxic_ptr = toxic_mmap.as_ptr();

    // ═══ V2 Initial Config ═══
    state_v2.global.active_bots.store(MAX_BOTS as u8, Ordering::Relaxed);
    state_v2.global.active_venues.store(1, Ordering::Relaxed); // Currently 1 venue (BFX)

    println!("[ARMADA] V1 shim: {ARMADA_STATE_V1_PATH} ({}B)", std::mem::size_of::<ArmadaState>());
    println!("[ARMADA] V2 state: {ARMADA_STATE_V2_PATH} ({}B = {} CL)", 
             std::mem::size_of::<ArmadaStateV2>(),
             std::mem::size_of::<ArmadaStateV2>() / 64);
    println!("[ARMADA] Toxic storm: mmap pointer (zero-alloc)");
    println!("[ARMADA] Entering 10 Hz control loop...");

    // ═══ 10 Hz Heartbeat (100ms) ═══
    let mut ticker = interval(Duration::from_millis(100));

    loop {
        ticker.tick().await;
        
        recalculate_kelly_matrix(state_v1, state_v2, oracle_state, l2_state, toxic_ptr);
    }
}

fn zero_all_capital(state_v1: &mut ArmadaState, state_v2: &mut ArmadaStateV2) {
    // V1 shim
    for i in 0..MAX_BOTS {
        state_v1.authorized_capital[i].store(0, Ordering::Relaxed);
        state_v1.kelly_weight[i].store(0f32.to_bits(), Ordering::Relaxed);
    }
    
    // V2: Zero all bot×venue slots
    for i in 0..(MAX_BOTS * MAX_VENUES) {
        state_v2.capital.authorized_capital[i].store(0, Ordering::Relaxed);
        state_v2.kelly.kelly_weight[i].store(0f32.to_bits(), Ordering::Relaxed);
    }

    // V1 version bump (simple increment)
    let v1_ver = state_v1.version.load(Ordering::Acquire);
    state_v1.version.store(v1_ver + 1, Ordering::Release);

    // V2 SeqLock: odd=writing → write → fence → even=consistent
    let v2_ver = state_v2.global.version.load(Ordering::Acquire);
    let next_even = (v2_ver | 1) + 1; // Ensure even
    state_v2.global.version.store(next_even, Ordering::Release);
}

fn recalculate_kelly_matrix(
    state_v1: &mut ArmadaState,
    state_v2: &mut ArmadaStateV2,
    _oracle: &sniper_types::armada_types::OracleState,
    l2_state: &sniper_types::l2_command::L2SharedState,
    toxic_ptr: *const u8,
) {
    // ═══ GLOBAL KILL SWITCH (Check both V1 and V2) ═══
    if state_v1.is_kill_switch_active() || state_v2.is_kill_switch_active() {
        zero_all_capital(state_v1, state_v2);
        return;
    }

    // ═══ EQUITY (Read from V2 if available, fallback to V1) ═══
    let v2_equity = state_v2.global.total_equity.load(Ordering::Relaxed) as f64 / 1e8;
    let v1_equity = state_v1.total_equity.load(Ordering::Relaxed) as f64 / 1e8;
    let total_equity = if v2_equity > 0.0 { v2_equity } else { v1_equity };
    
    // Minimum operating capital (prevents division by zero, not phantom $10k)
    let total_active_equity = if total_equity < 100.0 { 100.0 } else { total_equity };

    // ═══ REGIME SCORES (from L2 command matrix) ═══
    let ranging = l2_state.global_risk.ranging_score.load(Ordering::Relaxed) as f64 / 1e8;
    let trending = l2_state.global_risk.trending_score.load(Ordering::Relaxed) as f64 / 1e8;

    // ═══ BOT WEIGHT MATRIX (Regime-Aware) ═══
    let mut raw_weights = [0.0f64; MAX_BOTS];
    raw_weights[0] = ranging * 2.0;  // Hydra: excels in range
    raw_weights[1] = trending * 3.0; // Moonshot: excels in trend
    raw_weights[2] = ranging * 1.5;  // Grid: excels in range
    raw_weights[3] = 1.0;            // Trigon: venue-neutral arb
    raw_weights[4] = trending * 3.0; // Nexus: cross-venue arb

    // ═══ TOXIC STORM CHECK (Zero-allocation mmap read) ═══
    // Fix B5: Was std::fs::read() + vec![0] = heap alloc every 100ms
    let is_toxic = unsafe { std::ptr::read_volatile(toxic_ptr) } == 1;
    if is_toxic {
        raw_weights[0] = 0.0; // Hydra: pause market making in storm
        raw_weights[2] = 0.0; // Grid: pause grid making in storm
    }

    let total_weight: f64 = raw_weights.iter().sum();

    // ═══ SEQLOCK V2: LOCK (Odd = writing in progress) ═══
    let current_v2_ver = state_v2.global.version.load(Ordering::Acquire);
    state_v2.global.version.store(current_v2_ver | 1, Ordering::Release);

    // ═══ WRITE CAPITAL TO BOTH V1 AND V2 ═══
    let active_venues = state_v2.global.active_venues.load(Ordering::Relaxed) as usize;
    let venues = if active_venues == 0 { 1 } else { active_venues };

    for bot in 0..MAX_BOTS {
        let allocated_usd = if total_weight > 0.0 {
            total_active_equity * (raw_weights[bot] / total_weight)
        } else {
            0.0
        };

        // Fix B6: Saturating cast (prevents u64::MAX on negative values)
        let allocated_fp = if allocated_usd > 0.0 {
            (allocated_usd * 1e8) as u64
        } else {
            0u64
        };

        // V1 Legacy Shim: single venue, aggregated capital
        state_v1.authorized_capital[bot].store(allocated_fp, Ordering::Relaxed);

        // V2: Distribute evenly across active venues (future: per-venue Kelly)
        let per_venue_fp = allocated_fp / venues as u64;
        for venue in 0..MAX_VENUES {
            let idx = capital_index(bot, venue);
            if venue < venues {
                state_v2.capital.authorized_capital[idx].store(per_venue_fp, Ordering::Relaxed);
            } else {
                state_v2.capital.authorized_capital[idx].store(0, Ordering::Relaxed);
            }
        }
    }

    // ═══ SYNC REGIME TO V2 ═══
    let regime_byte = if trending > ranging * 2.0 {
        1u8 // TREND
    } else if ranging > trending * 2.0 {
        0u8 // RANGE
    } else {
        2u8 // CHAOS
    };
    state_v2.global.regime.store(regime_byte, Ordering::Relaxed);

    // ═══ SEQLOCK V2: UNLOCK (Even = data consistent) ═══
    std::sync::atomic::fence(Ordering::SeqCst);
    state_v2.global.version.store((current_v2_ver | 1) + 1, Ordering::Release);

    // V1: Simple version bump (backward compat)
    let current_v1_ver = state_v1.version.load(Ordering::Acquire);
    state_v1.version.store(current_v1_ver + 1, Ordering::Release);
}
