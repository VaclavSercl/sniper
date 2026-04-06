use std::sync::atomic::{AtomicU32, AtomicU64, AtomicU8, Ordering};
use memmap2::MmapOptions;
use std::fs::File;

/// Armada Orchestrator MMap (IPC) Root
/// Namapováno na: `/dev/shm/beroun/armada_state.bin`
/// Cache-line aligned (64 bytes) to eliminate false sharing between daemon and bots.
#[repr(C, align(64))]
pub struct ArmadaState {
    /// Inkrementováno Orchestratorem po aplikaci nové investiční matice
    pub version: AtomicU64,
    
    /// Atomický Hard-Stop celé flotily. 0 = OK, 1 = PANIC / SHIELD ACTIVE
    pub global_kill_switch: AtomicU8,
    
    /// Globální Equity v USD (Fixed-Point, PRICE_SCALE = 1e8) 
    pub total_equity: AtomicU64,
    
    /// Value at Risk celé ARMADY (Fixed-Point, PRICE_SCALE = 1e8)
    pub global_var: AtomicU64,
    
    /// Autorizovaný kapitál pro jednotlivé válečné lodě
    /// Indexy: 0=Hydra, 1=Moonshot, 2=Grid, 3=Trigon, 4=Nexus
    /// Hodnota je v USD, Fixed-Point (PRICE_SCALE = 1e8)
    pub authorized_capital: [AtomicU64; 5],
    
    /// Vypočítaná Kellyho váha pro jednotlivé boty `K = f * (W - ((1-W)/R))`
    /// Uloženo formou bitů pro f32, rozsah <0.0 ; 1.0>
    pub kelly_weight: [AtomicU32; 5],
    
    /// Denní Realized PnL botů (aktualizováno PnL Daemonem, čteno Orchestratorem pro výpočet WinRate)
    pub daily_pnl: [AtomicU64; 5],
}

impl ArmadaState {
    /// Ochranná čtecí bariéra pro zjištění verze matice
    #[inline(always)]
    pub fn get_version(&self) -> u64 {
        self.version.load(Ordering::Acquire)
    }

    /// Extrémně rychlá kontrola globálního Kill-Switch v Hot Path
    #[inline(always)]
    pub fn is_kill_switch_active(&self) -> bool {
        self.global_kill_switch.load(Ordering::Relaxed) != 0
    }

    /// Helper pro čtení Float32 Kelly matice
    #[inline(always)]
    pub fn get_kelly_weight(&self, bot_index: usize) -> f32 {
        f32::from_bits(self.kelly_weight[bot_index].load(Ordering::Relaxed))
    }
}

impl Default for ArmadaState {
    fn default() -> Self {
        Self {
            version: AtomicU64::new(0),
            global_kill_switch: AtomicU8::new(0),
            total_equity: AtomicU64::new(0),
            global_var: AtomicU64::new(0),
            authorized_capital: [
                AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0),
                AtomicU64::new(0), AtomicU64::new(0),
            ],
            kelly_weight: [
                AtomicU32::new(0), AtomicU32::new(0), AtomicU32::new(0),
                AtomicU32::new(0), AtomicU32::new(0),
            ],
            daily_pnl: [
                AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0),
                AtomicU64::new(0), AtomicU64::new(0),
            ],
        }
    }
}

pub fn load_armada_state_ro() -> &'static ArmadaState {
    let file = File::open("/dev/shm/beroun/armada_state.bin")
        .expect("🔥 Kritická chyba: Armada State neexistuje. Je spuštěn armada-core?");
    
    // Otevřeno POUZE PRO ČTENÍ
    let mmap = unsafe { MmapOptions::new().map(&file).unwrap() };
    let mmap_ref = Box::leak(Box::new(mmap));
    
    let state_ptr = mmap_ref.as_ptr() as *const ArmadaState;
    unsafe { &*state_ptr }
}

#[repr(C, align(64))]
pub struct OracleState {
    pub sentiment_score_fp: std::sync::atomic::AtomicI64,
    pub mempool_whale_warning: std::sync::atomic::AtomicI64,
    pub market_regime: std::sync::atomic::AtomicI64,
    pub oracle_heartbeat_ms: std::sync::atomic::AtomicU64,
}

pub fn load_oracle_state_ro() -> &'static OracleState {
    let path = "/dev/shm/beroun/oracle_state.bin";
    let file = File::open(path).expect("🔥 Oracle State neexistuje. Spusťte oracle_daemon.py");
    let mmap = unsafe { MmapOptions::new().map(&file).unwrap() };
    let mmap_ref = Box::leak(Box::new(mmap));
    let state_ptr = mmap_ref.as_ptr() as *const OracleState;
    unsafe { &*state_ptr }
}
