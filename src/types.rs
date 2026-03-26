use std::sync::LazyLock;
use std::sync::atomic::{AtomicU64, AtomicI64, AtomicU32};

pub static RISK_STATE_PATH: LazyLock<String> = LazyLock::new(|| "/home/wwwenda/hft-sniper/runtime/risk_state.bin".to_string());
pub static ENGINE_STATE_PATH: LazyLock<String> = LazyLock::new(|| "/home/wwwenda/hft-sniper/runtime/engine_state.bin".to_string());

pub const PRICE_SCALE: f64 = 100_000_000.0;
pub const PRICE_SCALE_I: i64 = 100_000_000;
pub const BOOK_LEVELS: usize = 25;

#[repr(C)]
pub struct OrderBookLevel {
    pub price: AtomicU64,
    pub amount: AtomicI64, // Positive for bids, negative for asks
    pub count: AtomicU64,
}

#[repr(C, align(64))]
pub struct EngineState {
    pub best_bid: AtomicU64,
    pub best_ask: AtomicU64,
    pub bids: [OrderBookLevel; BOOK_LEVELS],
    pub asks: [OrderBookLevel; BOOK_LEVELS],
    pub latency_ns: AtomicU64,
    pub net_position: AtomicI64,
    pub realized_pnl: AtomicI64,
    pub wallet_btc: AtomicU64,
    pub wallet_usd: AtomicU64,
    pub checksum: AtomicU32,
    pub _padding: [u8; 4],
    // Anti-spam: track last submitted order prices (scaled i64).
    // Orders are only re-submitted when price changes by >= MIN_TICK.
    pub last_buy_price: AtomicI64,
    pub last_sell_price: AtomicI64,
}

impl Default for OrderBookLevel {
    fn default() -> Self {
        Self {
            price: AtomicU64::new(0),
            amount: AtomicI64::new(0),
            count: AtomicU64::new(0),
        }
    }
}

impl Default for EngineState {
    fn default() -> Self {
        const LEVEL_DEFAULT: OrderBookLevel = OrderBookLevel {
            price: AtomicU64::new(0),
            amount: AtomicI64::new(0),
            count: AtomicU64::new(0),
        };
        Self {
            best_bid: AtomicU64::new(0),
            best_ask: AtomicU64::new(0),
            bids: [LEVEL_DEFAULT; BOOK_LEVELS],
            asks: [LEVEL_DEFAULT; BOOK_LEVELS],
            latency_ns: AtomicU64::new(0),
            net_position: AtomicI64::new(0),
            realized_pnl: AtomicI64::new(0),
            wallet_btc: AtomicU64::new(0),
            wallet_usd: AtomicU64::new(0),
            checksum: AtomicU32::new(0),
            _padding: [0; 4],
            last_buy_price: AtomicI64::new(0),
            last_sell_price: AtomicI64::new(0),
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
            grid_step: AtomicU64::new((3.0 * PRICE_SCALE) as u64),
            grid_size: AtomicU64::new(2),
            order_usd: AtomicU64::new((50.0 * PRICE_SCALE) as u64),
            max_inv_delta: AtomicU64::new((0.005 * PRICE_SCALE) as u64),
            bias_offset: AtomicI64::new(0),
            paused: AtomicU64::new(0),
            _padding: [0; 16]
        }
    }
}
