use std::sync::atomic::Ordering;
use std::time::Duration;
use tokio::time::interval;
use anyhow::{Context, Result};
use sniper_types::armada_types::{ArmadaState, load_oracle_state_ro};
use sniper_types::mmap_utils::init_mmap;

const ARMADA_STATE_PATH: &str = "/dev/shm/beroun/armada_state.bin";

#[tokio::main]
async fn main() -> Result<()> {
    println!("[ARMADA] Booting Orchestrator Daemon...");
    
    // 1. Otevření /dev/shm/beroun/armada_state.bin přes MmapMut
    let mut armada_mmap = init_mmap::<ArmadaState>(ARMADA_STATE_PATH)
        .context("Failed to initialize armada state mmap")?;
    let armada_state = unsafe { &mut *(armada_mmap.as_mut_ptr() as *mut ArmadaState) };

    // Nové L3 Orákulum (Read-Only)
    let oracle_state = load_oracle_state_ro();
    let l2_state = sniper_types::l2_command::load_l2_shared_state_ro();

    // 2. Nastavení 10 Hz Heartbeatu (100 ms)
    let mut ticker = interval(Duration::from_millis(100));

    loop {
        ticker.tick().await;
        
        // 3. Výpočet Kelly Matici
        recalculate_kelly_matrix(armada_state, oracle_state, l2_state);
    }
}

// Simulace L2 dat / PnL dat - TBD v dalších fázích
fn get_win_rate(_bot_id: usize) -> f64 {
    // Pro prototyp vracíme stabilní hodnotu. Realná implementace bude číst daily_pnl
    0.55
}

fn get_risk_reward(_bot_id: usize) -> f64 {
    1.5
}

fn get_correlation(_bot_a: usize, _bot_b: usize) -> f64 {
    // Pokud bychom chtěli detekovat korelaci
    0.5
}

fn zero_all_capital(state: &mut ArmadaState) {
    for i in 0..5 {
        state.authorized_capital[i].store(0, Ordering::Relaxed);
        state.kelly_weight[i].store(0f32.to_bits(), Ordering::Relaxed);
    }
    let current_v = state.version.load(Ordering::Acquire);
    state.version.store(current_v + 1, Ordering::Release);
}

fn recalculate_kelly_matrix(
    state: &mut ArmadaState, 
    oracle: &sniper_types::armada_types::OracleState,
    l2_state: &sniper_types::l2_command::L2SharedState
) {
    if state.is_kill_switch_active() {
        zero_all_capital(state);
        return;
    }

    let total_equity = state.total_equity.load(Ordering::Relaxed) as f64 / 1e8;
    let total_active_equity = if total_equity < 1000.0 { 10000.0 } else { total_equity };

    let ranging = l2_state.global_risk.ranging_score.load(Ordering::Relaxed) as f64 / 1e8;
    let trending = l2_state.global_risk.trending_score.load(Ordering::Relaxed) as f64 / 1e8;

    let mut raw_weights = [0.0; 5];
    raw_weights[0] = ranging * 2.0; // Hydra
    raw_weights[1] = trending * 3.0; // Moonshot
    raw_weights[2] = ranging * 1.5; // Grid
    raw_weights[3] = 1.0;           // Trigon
    raw_weights[4] = trending * 3.0; // Nexus

    let toxic_storm_active = std::fs::read("/dev/shm/beroun/toxic_storm.bin").unwrap_or_else(|_| vec![0]).first().cloned().unwrap_or(0) == 1;
    if toxic_storm_active {
        raw_weights[0] = 0.0;
        raw_weights[2] = 0.0;
    }

    let total_weight: f64 = raw_weights.iter().sum();

    for i in 0..5 {
        let allocated_usd = if total_weight > 0.0 {
            total_active_equity * (raw_weights[i] / total_weight)
        } else {
            0.0
        };

        state.authorized_capital[i].store((allocated_usd * 1e8) as u64, Ordering::Relaxed);
    }
    
    // Zvedneme verzi, aby boti věděli, že mají nové limity
    let current_v = state.version.load(Ordering::Acquire);
    state.version.store(current_v + 1, Ordering::Release);
}
