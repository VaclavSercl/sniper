// ═══════════════════════════════════════════════════════════
// 🌐 VenueAdapter — Hexagonal Port for Exchange Connectivity
// Sniper Armada · SIM v2.0 · 2026
//
// This trait defines the universal interface that every exchange
// must implement. Strategy code (SovereignEngine) NEVER knows
// which exchange it's running on.
//
// Adding a new exchange = implement this trait in ~250 lines.
// No bot code changes. No framework changes.
//
// Fixes 4 critical traps:
// 1. Fill normalization (OrderUpdate + Fill variants)
// 2. Exchange Physics (tick_size, lot_size, fees)
// 3. Zero-alloc BatchOrder (ArrayVec, stack-only)
// 4. Symbology alignment (price/qty rounding)
//
// Inspired by: barter-rs, NautilusTrader, Hummingbot
// ═══════════════════════════════════════════════════════════

use super::types::*;
use arrayvec::ArrayVec;

// ── Maximum orders per batch (stack-allocated) ──
pub const BATCH_MAX_ORDERS: usize = 16;
pub const BATCH_MAX_CANCELS: usize = 16;

// ═══════════════════════════════════════════════════════════
// Exchange Physics — "Gravity" of each venue
// ═══════════════════════════════════════════════════════════

/// The physical constraints of an exchange.
/// Each venue has different tick sizes, lot sizes, and fee structures.
/// AI (L2 Gemini) reads these to auto-tune strategy parameters.
#[derive(Debug, Clone, Copy)]
pub struct ExchangePhysics {
    /// Maker fee in basis points (e.g., -2.0 = 2bps rebate on BFX)
    pub maker_fee_bps: f64,
    /// Taker fee in basis points (e.g., 5.5 on BFX)
    pub taker_fee_bps: f64,
    /// Minimum price increment (e.g., 0.1 on BFX, 0.5 on Kraken)
    pub tick_size: f64,
    /// Minimum quantity increment (e.g., 0.00001 BTC)
    pub lot_size: f64,
    /// Minimum order size in base currency
    pub min_order_size: f64,
    /// Maximum orders per second (rate limit)
    pub max_orders_per_sec: u32,
}

impl ExchangePhysics {
    /// Align a price to the venue's tick size (round toward mid).
    /// Prevents HTTP 400 rejections for invalid precision.
    #[inline]
    pub fn align_price(&self, price: f64, is_bid: bool) -> f64 {
        if self.tick_size <= 0.0 { return price; }
        if is_bid {
            // Bids: round DOWN to avoid crossing
            (price / self.tick_size).floor() * self.tick_size
        } else {
            // Asks: round UP to avoid crossing
            (price / self.tick_size).ceil() * self.tick_size
        }
    }

    /// Align quantity to the venue's lot size.
    #[inline]
    pub fn align_qty(&self, qty: f64) -> f64 {
        if self.lot_size <= 0.0 { return qty; }
        (qty / self.lot_size).floor() * self.lot_size
    }
}

// ═══════════════════════════════════════════════════════════
// Normalized Execution Reports (Fills + Order Updates)
// ═══════════════════════════════════════════════════════════

/// Order status from any exchange (normalized).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderStatus {
    Accepted,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
}

/// Normalized order update from any exchange.
#[derive(Debug, Clone)]
pub struct UnifiedOrderUpdate {
    pub exchange: ExchangeId,
    /// Bot's internal client order ID (mapped by adapter)
    pub client_id: u64,
    /// Exchange-native order ID (opaque, for logging)
    pub exchange_order_id: u64,
    pub status: OrderStatus,
    /// Remaining amount (0.0 if fully filled)
    pub remaining: f64,
}

/// Normalized fill (trade execution) from any exchange.
/// This is the PnL event — feeds into pnl_daemon and AI Tribunal.
#[derive(Debug, Clone)]
pub struct UnifiedFill {
    pub exchange: ExchangeId,
    pub client_id: u64,
    pub symbol_hash: u64,
    pub side: OrderSide,
    /// Execution price × PRICE_SCALE
    pub price: i64,
    /// Filled amount (always positive)
    pub amount: f64,
    /// Fee paid (positive = cost, negative = rebate)
    pub fee: f64,
    /// Fee currency hash
    pub fee_currency: u64,
    /// Exchange timestamp (epoch ms)
    pub exchange_ts: u64,
}

/// Result of parsing a raw WebSocket message (extended).
#[derive(Debug)]
pub enum VenueMessage {
    /// Ticker update (BBA)
    Tick(UnifiedTick),
    /// Orderbook snapshot or delta
    Book(UnifiedBookUpdate),
    /// Channel subscribed confirmation
    Subscribed { chan_id: i64, symbol: u64 },
    /// Authentication result
    Authenticated { success: bool },
    /// Order state change (accepted, canceled, etc.)
    OrderUpdate(UnifiedOrderUpdate),
    /// Trade execution (PnL event)
    Fill(UnifiedFill),
    /// Heartbeat (no action needed)
    Heartbeat,
    /// Unrecognized message (ignored)
    Unknown,
}

// ═══════════════════════════════════════════════════════════
// Zero-Allocation BatchOrder (Stack-only via ArrayVec)
// ═══════════════════════════════════════════════════════════

/// Stack-allocated batch order — zero heap allocations in hot path.
/// Max 16 orders + 16 cancels per batch (covers all current bots).
#[derive(Debug, Clone)]
pub struct VenueBatchOrder {
    /// Client IDs of orders to cancel before placing new ones
    pub cancel_cids: ArrayVec<u64, BATCH_MAX_CANCELS>,
    /// Cancel by GID (exchange-specific group cancel)
    pub cancel_gid: Option<u32>,
    /// Cancel all orders for a symbol
    pub cancel_symbol: Option<u64>,
    /// New orders to place
    pub orders: ArrayVec<VenueOrderRequest, BATCH_MAX_ORDERS>,
}

/// Stack-friendly order request (no heap String).
/// Symbol is stored as a u64 hash — adapter maps to exchange-native string.
#[derive(Debug, Clone, Copy)]
pub struct VenueOrderRequest {
    /// Bot's internal client order ID
    pub client_id: u64,
    /// Bot group ID (Hydra=1000, Grid=3000, etc.)
    pub gid: u32,
    /// Symbol hash (8-byte ASCII hash)
    pub symbol_hash: u64,
    /// Order side
    pub side: OrderSide,
    /// Price × PRICE_SCALE (ignored for Market orders)
    pub price: i64,
    /// Amount (always positive)
    pub amount: f64,
    /// Order type
    pub order_type: OrderType,
}

impl Default for VenueBatchOrder {
    fn default() -> Self {
        Self {
            cancel_cids: ArrayVec::new(),
            cancel_gid: None,
            cancel_symbol: None,
            orders: ArrayVec::new(),
        }
    }
}

// ═══════════════════════════════════════════════════════════
// The Trait — Hexagonal Port
// ═══════════════════════════════════════════════════════════

/// Hexagonal Port — exchange-agnostic venue interface.
///
/// Every exchange (Bitfinex, Kraken, Bybit, OKX...) implements
/// this trait. The `SovereignRunner<E, V>` uses monomorphization
/// (static dispatch) to inline the entire execution path.
///
/// **Zero-Cost Abstraction:** No vtable, no dyn, no Box.
/// The compiler specializes `SovereignRunner<HydraEngine, BitfinexVenue>`
/// into a single optimized binary identical to handwritten code.
pub trait VenueAdapter: Send + Sync {
    /// Exchange identifier (used in mmap, logging, metrics)
    fn id(&self) -> ExchangeId;

    /// Human-readable name for logging
    fn name(&self) -> &str;

    /// Environment variable prefix for credentials (e.g., "BITFINEX")
    fn env_prefix(&self) -> &str;

    /// WebSocket URL for market data + execution
    fn ws_url(&self) -> &str;

    /// Exchange physics: tick size, lot size, fees, rate limits.
    /// AI reads this to auto-tune strategy parameters per venue.
    fn physics(&self) -> ExchangePhysics;

    /// Build authenticated WebSocket message.
    /// Returns None if credentials are not configured.
    fn auth_message(&self, creds: &ExchangeCredentials) -> Option<String>;

    /// Build ticker subscription message for a symbol.
    fn subscribe_ticker(&self, symbol: &str) -> String;

    /// Build book subscription message for a symbol.
    fn subscribe_book(&self, symbol: &str, precision: &str, depth: u32) -> String;

    /// Parse a raw WebSocket text frame into a normalized message.
    /// This is the NORMALIZATION LAYER: exchange-specific JSON → VenueMessage.
    ///
    /// Must handle: ticks, book updates, auth, order updates, fills.
    /// `&mut self` because some adapters need to track channel→symbol mappings.
    fn parse_raw(&mut self, raw: &[u8]) -> VenueMessage;

    /// Encode a batch order into exchange-native wire format.
    /// Returns bytes to write directly to the WebSocket.
    ///
    /// The adapter MUST call `physics().align_price()` and `align_qty()`
    /// on all orders before encoding to prevent rejections.
    fn encode_batch(&self, batch: &VenueBatchOrder, buf: &mut bytes::BytesMut);

    /// Encode a cancel-all-by-GID message.
    fn encode_cancel_gid(&self, gid: u32, buf: &mut bytes::BytesMut);

    /// Encode an emergency cancel-all message.
    fn encode_cancel_all(&self, buf: &mut bytes::BytesMut);

    /// Map a symbol hash back to exchange-native string.
    /// Used by fill normalization and logging.
    fn symbol_for_hash(&self, hash: u64) -> Option<&str> { let _ = hash; None }
}
