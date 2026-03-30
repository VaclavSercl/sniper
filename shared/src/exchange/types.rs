// ═══════════════════════════════════════════════════════════
// 🌐 Exchange Types — Universal Data Structures
// Sniper Armada · Phase 5.1 · v17.0
//
// Unified types that every Exchange implementation must use.
// All prices in fixed-point (×PRICE_SCALE), all amounts in fixed-point.
// Zero-copy compatible with mmap IPC.
// ═══════════════════════════════════════════════════════════

use std::fmt;

/// Unique identifier for each supported exchange.
/// Stored as u8 in mmap for minimal footprint.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum ExchangeId {
    Bitfinex = 0,
    Binance  = 1,
    // future: Kraken = 2, Bybit = 3, ...
}

impl fmt::Display for ExchangeId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ExchangeId::Bitfinex => write!(f, "Bitfinex"),
            ExchangeId::Binance  => write!(f, "Binance"),
        }
    }
}

/// Unified ticker update from any exchange.
/// This is what bots receive after `parse_message()`.
#[derive(Debug, Clone)]
pub struct UnifiedTick {
    /// Source exchange
    pub exchange: ExchangeId,
    /// Symbol hash (8-byte ASCII hash, same as moonshot `str_to_symbol_hash`)
    pub symbol: u64,
    /// Best bid price × PRICE_SCALE (i64 for negative/zero handling)
    pub bid: i64,
    /// Best ask price × PRICE_SCALE
    pub ask: i64,
    /// Bid volume × PRICE_SCALE (optional, 0 if unavailable)
    pub bid_vol: i64,
    /// Ask volume × PRICE_SCALE (optional, 0 if unavailable)
    pub ask_vol: i64,
    /// Exchange-local timestamp (epoch ms, 0 if unavailable)
    pub exchange_ts: u64,
}

/// Orderbook level for unified book snapshots/updates.
#[derive(Debug, Clone, Copy, Default)]
pub struct BookLevel {
    pub price: i64,   // × PRICE_SCALE
    pub amount: i64,  // × PRICE_SCALE (positive=bid, negative=ask on Bitfinex)
    pub count: u64,   // Number of orders at this level
}

/// Unified orderbook update from any exchange.
#[derive(Debug, Clone)]
pub struct UnifiedBookUpdate {
    pub exchange: ExchangeId,
    pub symbol: u64,
    /// true = full snapshot, false = incremental delta
    pub is_snapshot: bool,
    /// Updated levels (mixed bids+asks for Bitfinex, separated for Binance)
    pub levels: Vec<BookLevel>,
}

/// Order side
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderSide {
    Buy,
    Sell,
}

/// Order type (exchange-agnostic)
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderType {
    /// Limit order (post-only on most exchanges)
    Limit,
    /// Limit with post-only flag (maker only, cancel if would cross)
    LimitPostOnly,
    /// Immediate-or-Cancel (taker, partial fills OK)
    Ioc,
    /// Market order (taker, fill at best available)
    Market,
}

/// Order flags (bitfield)
#[derive(Debug, Clone, Copy, Default)]
pub struct OrderFlags {
    /// Bitfinex: flags field (4096 = post-only, etc.)
    pub raw_flags: u32,
    /// Reduce-only (futures)
    pub reduce_only: bool,
}

/// Universal order request — what bots submit to any exchange.
#[derive(Debug, Clone)]
pub struct OrderRequest {
    /// Bot group ID (Hydra=2000, Grid=3000, Trigon=4000, Moonshot=5000)
    pub gid: u32,
    /// Trading symbol (exchange-native string, e.g., "tBTCUSD" or "BTCUSDT")
    pub symbol: String,
    /// Order side
    pub side: OrderSide,
    /// Price × PRICE_SCALE (ignored for Market orders)
    pub price: i64,
    /// Amount (always positive, side determines direction)
    pub amount: f64,
    /// Order type
    pub order_type: OrderType,
    /// Exchange-specific flags
    pub flags: OrderFlags,
}

/// Batch order operation — cancel + place in one message.
#[derive(Debug, Clone)]
pub struct BatchOrder {
    /// Orders to cancel before placing new ones
    pub cancel_gid: Option<u32>,
    /// Cancel specific symbol (None = cancel all with gid)
    pub cancel_symbol: Option<String>,
    /// New orders to place
    pub orders: Vec<OrderRequest>,
}

/// Result of parsing a raw WebSocket message.
#[derive(Debug)]
pub enum ParsedMessage {
    /// Ticker update (BBA)
    Tick(UnifiedTick),
    /// Orderbook snapshot or delta
    Book(UnifiedBookUpdate),
    /// Channel subscribed confirmation { channel_id, symbol_hash }
    Subscribed { chan_id: i64, symbol: u64 },
    /// Authentication result
    Authenticated { success: bool },
    /// Heartbeat (no action needed)
    Heartbeat,
    /// Unrecognized message (ignored)
    Unknown,
}

/// Exchange credentials loaded from environment.
#[derive(Debug, Clone)]
pub struct ExchangeCredentials {
    pub api_key: String,
    pub api_secret: String,
}

impl ExchangeCredentials {
    /// Load credentials from environment variables.
    /// `prefix` = "BITFINEX" or "BINANCE" → reads {PREFIX}_API_KEY, {PREFIX}_API_SECRET
    pub fn from_env(prefix: &str) -> Option<Self> {
        let key = std::env::var(format!("{}_API_KEY", prefix)).ok()?;
        let secret = std::env::var(format!("{}_API_SECRET", prefix)).ok()?;
        if key.is_empty() || secret.is_empty() { return None; }
        Some(Self { api_key: key, api_secret: secret })
    }
}
