use std::sync::atomic::{AtomicU64, AtomicI64};

#[repr(C, align(64))]
pub struct EngineState {
    pub best_bid: AtomicU64,
    pub best_ask: AtomicU64,
    pub latency_ns: AtomicU64,
    pub net_position: AtomicI64,
    pub realized_pnl: AtomicI64,
    pub wallet_btc: AtomicU64,
    pub wallet_usd: AtomicU64,
    pub _padding: [u8; 8],
}

#[repr(C, align(64))]
pub struct RiskState {
    pub grid_step: AtomicU64,
    pub grid_size: AtomicU64,
    pub order_usd: AtomicU64,
    pub max_inv_delta: AtomicU64,
    pub bias_offset: AtomicI64,
    pub paused: AtomicU64,
    pub _padding: [u8; 16],
}

fn main() {
    println!("EngineState size: {}", std::mem::size_of::<EngineState>());
    println!("RiskState size: {}", std::mem::size_of::<RiskState>());
}
