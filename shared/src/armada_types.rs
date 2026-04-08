use std::sync::atomic::{AtomicI64, AtomicU32, AtomicU64, AtomicU8, Ordering};
use memmap2::MmapOptions;
use std::fs::File;

// ═══════════════════════════════════════════════════════════
// 🏛️ V1 — Legacy ArmadaState (Backward Compatibility Shim)
// ═══════════════════════════════════════════════════════════

/// Armada Orchestrator MMap (IPC) Root — V1 Legacy
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
    let size = std::mem::size_of::<OracleState>();

    // Safe boot: create file with zeros if it doesn't exist (oracle_daemon.py not started yet)
    if !std::path::Path::new(path).exists() {
        eprintln!("⚠️ [ARMADA] oracle_state.bin not found — creating zero-initialized placeholder");
        if let Ok(mut f) = File::create(path) {
            use std::io::Write;
            let _ = f.write_all(&vec![0u8; size]);
            let _ = f.flush();
        }
    }

    let file = File::open(path)
        .unwrap_or_else(|e| panic!("🔥 Cannot open oracle_state.bin even after create attempt: {e}"));
    let mmap = unsafe { MmapOptions::new().map(&file).unwrap() };
    let mmap_ref = Box::leak(Box::new(mmap));
    let state_ptr = mmap_ref.as_ptr() as *const OracleState;
    unsafe { &*state_ptr }
}

// ═══════════════════════════════════════════════════════════
// 🏛️ V2 — ArmadaStateV2 (Issue #16: Global Intelligence)
// ═══════════════════════════════════════════════════════════
//
// 1088B = 17 cache lines. Cache-line isolated topology:
//   CL0:     Global Read-Hot (version, kill_switch, equity, Cold Vault)
//   CL1:     Netting Engine (per-bot net offset)
//   CL2-6:   Capital Matrix [5 bots × 8 venues] = 320B
//   CL7-9:   Kelly Matrix [5 × 8] = 192B (160B data + align pad)
//   CL10-14: PnL Matrix [5 × 8] = 320B (AtomicI64!)
//   CL15-16: Venue Registry (fees, latency, alive status)
//
// Key invariant: CL0 (bot hot-reads) is physically isolated from
// CL2+ (orchestrator writes). Eliminates false sharing.
// ═══════════════════════════════════════════════════════════

pub const MAX_BOTS: usize = 5;
pub const MAX_VENUES: usize = 8;

/// Row-major index: bot's capital on a specific venue.
/// Layout guarantees bot's 8 venues are contiguous → 1 CL miss max per bot read.
#[inline(always)]
pub fn capital_index(bot: usize, venue: usize) -> usize {
    bot * MAX_VENUES + venue
}

/// CL0: Global State (Read-Hot by ALL bots in on_tick, Write-Rare by Orchestrator)
///
/// Layout (repr(C), x86_64):
///   [0-7]   version          AtomicU64
///   [8]     kill_switch      AtomicU8
///   [9]     active_venues    AtomicU8
///   [10]    active_bots      AtomicU8
///   [11]    regime           AtomicU8
///   [12-15] (implicit pad)   4B for AtomicU64 alignment
///   [16-23] total_equity     AtomicU64
///   [24-31] global_var       AtomicU64
///   [32-39] cold_vault_btc   AtomicI64
///   [40-47] cold_vault_vwap  AtomicU64
///   [48-63] _pad             16B → total = 64B = 1 CL ✓
#[repr(C, align(64))]
pub struct ArmadaGlobalCL {
    pub version: AtomicU64,           // SeqLock: odd=writing, even=consistent
    pub global_kill_switch: AtomicU8, // 0=OK, 1=PANIC
    pub active_venues: AtomicU8,      // 1-8
    pub active_bots: AtomicU8,        // 1-5
    pub regime: AtomicU8,             // 0=RANGE, 1=TREND, 2=CHAOS, 3=SHOCK
    pub total_equity: AtomicU64,      // FP 1e8
    pub global_var: AtomicU64,        // FP 1e8
    pub cold_vault_btc: AtomicI64,    // FP 1e8 (zmrazené BTC)
    pub cold_vault_vwap: AtomicU64,   // FP 1e8 (VWAP pořízení)
    pub _pad: [u8; 16],              // ★ OPRAVENO: 16B (ne 20B!) → 48+16=64B
}

/// CL1: Netting Engine (Write per-cycle by Orchestrator, 100ms)
///
/// Cross-bot netting: eliminates opposing orders within same venue.
/// e.g., Hydra sells 1 BTC + Grid buys 1 BTC → net offset = 0 → save fees.
#[repr(C, align(64))]
pub struct ArmadaNettingCL {
    pub netting_epoch: AtomicU64,     // Monotonic counter
    pub netting_net: [AtomicI64; 5],  // Net offset per bot (BTC FP 1e8)
    pub net_exposure_btc: AtomicI64,  // Celková flotila FP 1e8
    pub _pad: [u8; 8],
}

/// CL2-6: Capital Matrix [5 bots × 8 venues] = 320B = 5 CL
///
/// Index via `capital_index(bot, venue)`.
/// Each bot's 8 venues are contiguous (row-major) → bot reads max 1 CL.
#[repr(C, align(64))]
pub struct ArmadaCapitalMatrix {
    pub authorized_capital: [AtomicU64; 40], // [bot * MAX_VENUES + venue]
}

/// CL7-9: Kelly Matrix [5 × 8] (f32 stored as u32 bits) = 160B data → 192B aligned
///
/// Adaptive Fractional Kelly: K = f × (W - (1-W)/R), per bot per venue.
#[repr(C, align(64))]
pub struct ArmadaKellyMatrix {
    pub kelly_weight: [AtomicU32; 40], // 160B data, compiler pads to 192B via align(64)
}

/// CL10-14: PnL Matrix [5 × 8] = 320B = 5 CL
///
/// ★ OPRAVENO: AtomicI64 (ne U64!) — PnL MŮŽE být záporný!
#[repr(C, align(64))]
pub struct ArmadaPnlMatrix {
    pub daily_pnl: [AtomicI64; 40],   // FP 1e8, SIGNED
}

/// CL15-16: Venue Registry (Write-Rarely, ~1×/hod by fee updater)
///
/// Per-venue fees, alive status, and latency telemetry.
/// Prepared for Issue #17: Multi-Exchange Expansion (8 venues).
#[repr(C, align(64))]
pub struct ArmadaVenueRegistry {
    pub fee_maker_bps: [AtomicU32; 8],       // Per-venue maker fee (bps × 100)
    pub fee_taker_bps: [AtomicU32; 8],       // Per-venue taker fee
    pub venue_alive: [AtomicU8; 8],           // 0=dead, 1=alive, 2=degraded
    pub venue_latency_p95_us: [AtomicU32; 8], // Microseconds
    pub _pad: [u8; 24],
}

/// Root: ArmadaStateV2 — 1088B = 17 cache lines.
///
/// Cache-line isolated topology prevents false sharing:
/// - CL0 (version/kill_switch) NEVER invalidated by capital/kelly writes
/// - Each bot reads only its own row in capital matrix (1 CL miss max)
///
/// Mapped to: `/dev/shm/beroun/armada_state_v2.bin`
#[repr(C, align(64))]
pub struct ArmadaStateV2 {
    pub global: ArmadaGlobalCL,       // CL0
    pub netting: ArmadaNettingCL,     // CL1
    pub capital: ArmadaCapitalMatrix, // CL2-6
    pub kelly: ArmadaKellyMatrix,     // CL7-9
    pub pnl: ArmadaPnlMatrix,        // CL10-14
    pub venues: ArmadaVenueRegistry,  // CL15-16
}

impl Default for ArmadaStateV2 {
    fn default() -> Self {
        // Helper macro to create arrays of atomics initialized to 0
        macro_rules! atomic_array {
            ($ty:ty, $n:expr) => {{
                // SAFETY: AtomicXxx has the same repr as the underlying integer.
                // Zero is a valid state for all our atomic types.
                unsafe { std::mem::zeroed::<[$ty; $n]>() }
            }};
        }

        Self {
            global: ArmadaGlobalCL {
                version: AtomicU64::new(0),
                global_kill_switch: AtomicU8::new(0),
                active_venues: AtomicU8::new(1), // Default: 1 venue (BFX)
                active_bots: AtomicU8::new(MAX_BOTS as u8),
                regime: AtomicU8::new(0),        // Default: RANGE
                total_equity: AtomicU64::new(0),
                global_var: AtomicU64::new(0),
                cold_vault_btc: AtomicI64::new(0),
                cold_vault_vwap: AtomicU64::new(0),
                _pad: [0u8; 16],
            },
            netting: ArmadaNettingCL {
                netting_epoch: AtomicU64::new(0),
                netting_net: atomic_array!(AtomicI64, 5),
                net_exposure_btc: AtomicI64::new(0),
                _pad: [0u8; 8],
            },
            capital: ArmadaCapitalMatrix {
                authorized_capital: atomic_array!(AtomicU64, 40),
            },
            kelly: ArmadaKellyMatrix {
                kelly_weight: atomic_array!(AtomicU32, 40),
            },
            pnl: ArmadaPnlMatrix {
                daily_pnl: atomic_array!(AtomicI64, 40),
            },
            venues: ArmadaVenueRegistry {
                fee_maker_bps: atomic_array!(AtomicU32, 8),
                fee_taker_bps: atomic_array!(AtomicU32, 8),
                venue_alive: atomic_array!(AtomicU8, 8),
                venue_latency_p95_us: atomic_array!(AtomicU32, 8),
                _pad: [0u8; 24],
            },
        }
    }
}

impl ArmadaStateV2 {
    /// SeqLock read barrier — bot checks this FIRST in on_tick
    #[inline(always)]
    pub fn get_version(&self) -> u64 {
        self.global.version.load(Ordering::Acquire)
    }

    /// Zero-cost kill switch check (CL0, never false-shared with writes)
    #[inline(always)]
    pub fn is_kill_switch_active(&self) -> bool {
        self.global.global_kill_switch.load(Ordering::Relaxed) != 0
    }

    /// Read authorized capital for a specific bot on a specific venue
    #[inline(always)]
    pub fn get_capital(&self, bot: usize, venue: usize) -> u64 {
        self.capital.authorized_capital[capital_index(bot, venue)].load(Ordering::Relaxed)
    }

    /// Read Kelly weight for a specific bot on a specific venue
    #[inline(always)]
    pub fn get_kelly(&self, bot: usize, venue: usize) -> f32 {
        f32::from_bits(
            self.kelly.kelly_weight[capital_index(bot, venue)].load(Ordering::Relaxed)
        )
    }

    /// Read daily PnL (SIGNED) for a specific bot on a specific venue
    #[inline(always)]
    pub fn get_daily_pnl(&self, bot: usize, venue: usize) -> i64 {
        self.pnl.daily_pnl[capital_index(bot, venue)].load(Ordering::Relaxed)
    }

    /// Read Cold Vault frozen BTC amount
    #[inline(always)]
    pub fn get_cold_vault_btc(&self) -> i64 {
        self.global.cold_vault_btc.load(Ordering::Acquire)
    }

    /// Check if a specific venue is alive
    #[inline(always)]
    pub fn is_venue_alive(&self, venue: usize) -> bool {
        self.venues.venue_alive[venue].load(Ordering::Relaxed) == 1
    }
}

/// Load ArmadaStateV2 read-only via mmap.
/// Safe-creates zero-initialized file if it doesn't exist (boot race protection).
pub fn load_armada_state_v2_ro() -> &'static ArmadaStateV2 {
    let path = "/dev/shm/beroun/armada_state_v2.bin";
    let size = std::mem::size_of::<ArmadaStateV2>();

    let file = match File::open(path) {
        Ok(f) => f,
        Err(_) => {
            eprintln!("⚠️ [ARMADA-V2] armada_state_v2.bin not found — creating zero-initialized placeholder ({size}B)");
            if let Ok(mut f) = File::create(path) {
                use std::io::Write;
                let _ = f.write_all(&vec![0u8; size]);
                let _ = f.flush();
            }
            File::open(path)
                .unwrap_or_else(|e| panic!("🔥 Cannot open armada_state_v2.bin even after create: {e}"))
        }
    };

    let mmap = unsafe { MmapOptions::new().map(&file)
        .unwrap_or_else(|e| panic!("🔥 mmap failed on armada_state_v2.bin: {e}"))
    };
    let mmap_ref = Box::leak(Box::new(mmap));
    unsafe { &*(mmap_ref.as_ptr() as *const ArmadaStateV2) }
}
