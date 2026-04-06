// shared/src/fee_types.rs
use std::sync::atomic::AtomicU64;

pub const FEE_MATRIX_PATH: &str = "/dev/shm/beroun/fee_matrix.bin";

// Pevné O(1) indexy burz
pub const VENUE_BITFINEX: usize = 0;
pub const VENUE_BINANCE: usize = 1;
pub const VENUE_BYBIT: usize = 2;
pub const VENUE_OKX: usize = 3;
pub const MAX_VENUES: usize = 8;

#[repr(C, align(64))]
pub struct VenueFeeState {
    // Poplatky uložené v bps * 100 (např. 200 = 2.00 bps)
    pub maker_fee_bps: AtomicU64,
    pub taker_fee_bps: AtomicU64,
    pub last_update_ms: AtomicU64,
    pub is_online: std::sync::atomic::AtomicU32,
    pub _pad: [u8; 36], // Výplň pro dokonalé zarovnání 64 bajtů (L1 Cache Line)
}

#[repr(C, align(64))]
pub struct GlobalFeeMatrix {
    pub venues: [VenueFeeState; MAX_VENUES],
}
