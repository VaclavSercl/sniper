use std::sync::LazyLock;
use std::sync::atomic::{AtomicU64, AtomicI64};

pub static RISK_STATE_PATH: LazyLock<String> = LazyLock::new(|| "/home/wwwenda/HFT-Sniper/runtime/risk_state.bin".to_string());
pub static ENGINE_STATE_PATH: LazyLock<String> = LazyLock::new(|| "/home/wwwenda/HFT-Sniper/runtime/engine_state.bin".to_string());

pub const PRICE_SCALE: f64 = 100_000_000.0;
pub const PRICE_SCALE_I: i64 = 100_000_000;

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

impl Default for EngineState {
    fn default() -> Self {
        Self {
            best_bid: AtomicU64::new(0),
            best_ask: AtomicU64::new(0),
            latency_ns: AtomicU64::new(0),
            net_position: AtomicI64::new(0),
            realized_pnl: AtomicI64::new(0),
            wallet_btc: AtomicU64::new(0),
            wallet_usd: AtomicU64::new(0),
            _padding: [0; 8],
        }
    }
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

impl Default for RiskState {
    fn default() -> Self {
        Self { 
            grid_step: AtomicU64::new((10.0 * PRICE_SCALE) as u64),
            grid_size: AtomicU64::new(2),
            order_usd: AtomicU64::new((50.0 * PRICE_SCALE) as u64),
            max_inv_delta: AtomicU64::new((0.005 * PRICE_SCALE) as u64),
            bias_offset: AtomicI64::new(0),
            paused: AtomicU64::new(0),
            _padding: [0; 16]
        }
    }
}
