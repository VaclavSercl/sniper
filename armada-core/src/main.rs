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

    // 2. Nastavení 10 Hz Heartbeatu (100 ms)
    let mut ticker = interval(Duration::from_millis(100));

    loop {
        ticker.tick().await;
        
        // 3. Výpočet Kelly Matici
        recalculate_kelly_matrix(armada_state, oracle_state);
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

fn recalculate_kelly_matrix(state: &mut ArmadaState, oracle: &sniper_types::armada_types::OracleState) {
    // Pokud je Kill-Switch nahoře, vše nulujeme!
    if state.is_kill_switch_active() {
        zero_all_capital(state);
        return;
    }

    let total_equity = state.total_equity.load(Ordering::Relaxed) as f64 / 1e8;
    // Jako safety fall-back nastavíme minimum na $1k (kdyby náhodou total_equity bylo 0)
    let active_equity = if total_equity < 1000.0 { 1000.0 } else { total_equity };
    
    // Čtení Oracle stavu (zkontrolujeme heartbeat staleness, max 5 vteřin tolerance)
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
        
    let hb = oracle.oracle_heartbeat_ms.load(Ordering::Acquire);
    let mut fractional_dampener = 0.5; // Zvýchozí Half-Kelly
    
    if hb == 0 || now_ms.saturating_sub(hb) > 5000 {
        // Blind mode (Orákulum nekomunikuje)
        // Zůstává 0.5 jako neutrální obrana
    } else {
        // Oracle je živé! Analyzujeme data
        let whale_warn = oracle.mempool_whale_warning.load(Ordering::Acquire);
        let sentiment = oracle.sentiment_score_fp.load(Ordering::Acquire) as f64 / 1e8;
        
        if whale_warn == 1 {
            // WHALE DUMP DETECTED z L3! Okamžitě srazíme tlumič na 1/10 (Crush long botů)
            fractional_dampener = 0.05; 
        } else if sentiment < -0.5 {
            fractional_dampener = 0.2; // Extrémní strach, jedeme čtvrtinový Kelly
        } else if sentiment > 0.5 {
            fractional_dampener = 0.8; // Bull run
        }
        
        static mut LAST_PRINT: u64 = 0;
        if now_ms - unsafe { LAST_PRINT } > 2000 {
            println!("[ARMADA] L3 ORACLE ACTIVE | Sent: {:.2} | Whale: {} | Dampener: {:.2}", sentiment, whale_warn, fractional_dampener);
            unsafe { LAST_PRINT = now_ms };
        }
    }
    
    let mut raw_k = [0.0f32; 5];
    let mut sum_k = 0.0;

    for i in 0..5 {
        let w = get_win_rate(i); 
        let r = get_risk_reward(i);

        let k = fractional_dampener * (w - ((1.0 - w) / r));
        
        // Kelly nikdy nesmí být záporný (nebudeme shortovat vlastní strategii)
        raw_k[i] = k.max(0.0) as f32;
        sum_k += raw_k[i];
    }

    // Aplikace Covariance Penalty (Korelační zámek)
    // Pokud bot 0 (Hydra) a bot 4 (Nexus) korelují nad 0.8
    if get_correlation(0, 4) > 0.8 {
        raw_k[0] *= 0.5; // Zkosíme alokaci oběma
        raw_k[4] *= 0.5;
        // Přepočítáme sum_k
        sum_k = raw_k.iter().sum();
    }

    // Normalizace a Distribuce Peněz
    let normalization_factor = if sum_k > 1.0 { 1.0 / sum_k } else { 1.0 };

    for i in 0..5 {
        let final_k = raw_k[i] * normalization_factor;
        let allocated_usd = active_equity * (final_k as f64);
        
        // Zápis do sdílené paměti!
        state.kelly_weight[i].store(final_k.to_bits(), Ordering::Relaxed);
        state.authorized_capital[i].store((allocated_usd * 1e8) as u64, Ordering::Relaxed);
    }
    
    // Zvedneme verzi, aby boti věděli, že mají nové limity
    let current_v = state.version.load(Ordering::Acquire);
    state.version.store(current_v + 1, Ordering::Release);
}
