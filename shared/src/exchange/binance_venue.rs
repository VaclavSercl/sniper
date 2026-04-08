// ═══════════════════════════════════════════════════════════
// 🟡 BinanceVenue — VenueAdapter implementation for Binance Spot
// Sniper Armada · v20.0 Hexagonal Architecture
//
// Binance Spot uses a HYBRID transport model:
//   - Market data: WebSocket (bookTicker, aggTrade)
//   - Order execution: REST API (HMAC-SHA256 signed)
//
// This adapter normalizes Binance JSON into VenueMessage and
// provides REST order building through the existing Binance struct.
//
// Key difference: encode_batch() is NOT used for Binance orders.
// Instead, bots call BinanceVenue::rest_submit_batch() which returns
// SignedRequest objects for the HTTP client.
// ═══════════════════════════════════════════════════════════

use super::types::*;
use super::venue::*;
use super::binance::{self, Binance};


/// BinanceVenue — VenueAdapter for Binance Spot.
///
/// Handles:
/// - Market data parsing (bookTicker → UnifiedTick)
/// - REST order building (via inner Binance struct)
/// - Symbol registration & hash mapping
/// - ExchangePhysics for BTCUSDT
pub struct BinanceVenue {
    /// Inner Binance REST client (for order signing)
    pub rest: Binance,
    /// Symbol hash → Binance native string (e.g., hash → "BTCUSDT")
    symbols: arrayvec::ArrayVec<(u64, arrayvec::ArrayString<16>), 32>,
}

impl BinanceVenue {
    pub fn new() -> Self {
        Self {
            rest: Binance::new(),
            symbols: arrayvec::ArrayVec::new(),
        }
    }

    /// Register a symbol for hash↔string mapping.
    pub fn register_symbol(&mut self, symbol: &str) {
        let hash = binance::Binance::symbol_to_hash(symbol);
        if !self.symbols.iter().any(|(h, _)| *h == hash)
            && let Ok(s) = arrayvec::ArrayString::try_from(symbol) {
                let _ = self.symbols.try_push((hash, s));
            }
    }

    /// Submit a batch of orders via REST API.
    /// Returns SignedRequest objects for the HTTP client.
    /// Since Binance doesn't support atomic batches (like BFX ox_multi),
    /// each order becomes a separate HTTP request.
    pub fn rest_submit_batch(&self, batch: &VenueBatchOrder) -> Vec<binance::SignedRequest> {
        let mut requests = Vec::new();
        let physics = self.physics();

        // Cancel phase
        if let Some(sym_hash) = batch.cancel_symbol
            && let Some(sym) = self.symbol_for_hash(sym_hash)
                && let Some(req) = self.rest.cancel_all_orders(sym) {
                    requests.push(req);
                }

        // Place new orders
        for order in &batch.orders {
            let sym = self.symbol_for_hash(order.symbol_hash)
                .unwrap_or("BTCUSDT");
            let raw_price = order.price as f64 / crate::PRICE_SCALE;
            let is_bid = matches!(order.side, OrderSide::Buy);
            let aligned_price = physics.align_price(raw_price, is_bid);
            let aligned_qty = physics.align_qty(order.amount);

            let req = OrderRequest {
                gid: order.gid,
                symbol: sym.to_string(),
                side: order.side,
                price: (aligned_price * crate::PRICE_SCALE) as i64,
                amount: aligned_qty,
                order_type: order.order_type,
                flags: OrderFlags::default(),
            };

            if let Some(signed) = self.rest.new_order(&req) {
                requests.push(signed);
            }
        }

        requests
    }

    /// Parse Binance bookTicker message into VenueMessage.
    fn parse_book_ticker(&self, data: &[u8]) -> Option<VenueMessage> {
        let tick = binance::fast_parse_book_ticker(data)?;
        Some(VenueMessage::Tick(tick))
    }

    /// Parse Binance subscription result.
    fn parse_subscription_result(&self, data: &[u8]) -> Option<VenueMessage> {
        let v: serde_json::Value = serde_json::from_slice(data).ok()?;
        // {"result":null,"id":1} = subscription success
        if v.get("result").is_some() && v.get("id").is_some() {
            return Some(VenueMessage::Subscribed { chan_id: 0, symbol: 0 });
        }
        None
    }

    /// Parse Binance REST order response (for fill normalization).
    /// Called by the HTTP response handler, not by the WS parser.
    pub fn parse_rest_order_response(data: &[u8]) -> Option<VenueMessage> {
        let v: serde_json::Value = serde_json::from_slice(data).ok()?;

        let symbol = v.get("symbol")?.as_str()?;
        let order_id = v.get("orderId")?.as_u64()?;
        let status_str = v.get("status")?.as_str()?;

        let status = match status_str {
            "NEW" => OrderStatus::Accepted,
            "PARTIALLY_FILLED" => OrderStatus::PartiallyFilled,
            "FILLED" => OrderStatus::Filled,
            "CANCELED" | "EXPIRED" | "EXPIRED_IN_MATCH" => OrderStatus::Canceled,
            "REJECTED" => OrderStatus::Rejected,
            _ => return None,
        };

        // If FILLED or PARTIALLY_FILLED, extract fill info
        if status == OrderStatus::Filled || status == OrderStatus::PartiallyFilled {
            let exec_qty = v.get("executedQty")
                .and_then(|q| q.as_str())
                .and_then(|q| q.parse::<f64>().ok())
                .unwrap_or(0.0);
            let cum_quote = v.get("cummulativeQuoteQty")
                .and_then(|q| q.as_str())
                .and_then(|q| q.parse::<f64>().ok())
                .unwrap_or(0.0);
            let avg_price = if exec_qty > 0.0 { cum_quote / exec_qty } else { 0.0 };

            let side_str = v.get("side").and_then(|s| s.as_str()).unwrap_or("BUY");
            let side = if side_str == "BUY" { OrderSide::Buy } else { OrderSide::Sell };

            return Some(VenueMessage::Fill(UnifiedFill {
                exchange: ExchangeId::Binance,
                client_id: order_id,
                symbol_hash: Binance::symbol_to_hash(symbol),
                side,
                price: (avg_price * crate::PRICE_SCALE) as i64,
                amount: exec_qty,
                fee: 0.0, // Binance REST response doesn't include fees directly
                fee_currency: 0,
                exchange_ts: v.get("transactTime").and_then(|t| t.as_u64()).unwrap_or(0),
            }));
        }

        Some(VenueMessage::OrderUpdate(UnifiedOrderUpdate {
            exchange: ExchangeId::Binance,
            client_id: order_id,
            exchange_order_id: order_id,
            status,
            remaining: v.get("origQty")
                .and_then(|q| q.as_str())
                .and_then(|q| q.parse::<f64>().ok())
                .unwrap_or(0.0),
        }))
    }
}

impl Default for BinanceVenue {
    fn default() -> Self { Self::new() }
}

// ═══════════════════════════════════════════════════════════
// VenueAdapter Implementation
// ═══════════════════════════════════════════════════════════

impl VenueAdapter for BinanceVenue {
    fn id(&self) -> ExchangeId { ExchangeId::Binance }
    fn name(&self) -> &str { "Binance" }
    fn env_prefix(&self) -> &str { "BINANCE" }
    fn ws_url(&self) -> &str { "wss://stream.binance.com:9443/ws" }

    fn physics(&self) -> ExchangePhysics {
        ExchangePhysics {
            maker_fee_bps: 2.0,      // Binance maker fee (no rebate)
            taker_fee_bps: 4.0,      // Binance taker fee
            tick_size: 0.01,         // BTCUSDT minimum price increment
            lot_size: 0.00001,       // Minimum BTC quantity
            min_order_size: 0.00001, // Minimum order size
            max_orders_per_sec: 10,  // Conservative: 1200/min ÷ uniform
        }
    }

    fn auth_message(&self, _creds: &ExchangeCredentials) -> Option<String> {
        // Binance WS doesn't use auth messages — auth is REST-only (HMAC signatures)
        // Market data streams are public
        None
    }

    fn subscribe_ticker(&self, symbol: &str) -> String {
        Binance::subscribe_book_ticker(&[symbol], 1)
    }

    fn subscribe_book(&self, symbol: &str, _precision: &str, _depth: u32) -> String {
        // Binance uses bookTicker for BBA (no order book subscription via this method)
        Binance::subscribe_book_ticker(&[symbol], 2)
    }

    fn parse_raw(&mut self, raw: &[u8]) -> VenueMessage {
        if raw.is_empty() { return VenueMessage::Unknown; }

        match raw[0] {
            b'{' => {
                // Check for bookTicker data
                if let Some(msg) = self.parse_book_ticker(raw) {
                    return msg;
                }

                // Check for subscription result
                if let Some(msg) = self.parse_subscription_result(raw) {
                    return msg;
                }

                // Pong or other system messages
                VenueMessage::Unknown
            }
            _ => VenueMessage::Unknown,
        }
    }

    fn encode_batch(&self, _batch: &VenueBatchOrder, _buf: &mut bytes::BytesMut) {
        // Binance orders go through REST API, not WebSocket.
        // Use BinanceVenue::rest_submit_batch() instead.
        // This is intentionally a no-op for the WS out_buf.
    }

    fn encode_cancel_gid(&self, _gid: u32, _buf: &mut bytes::BytesMut) {
        // Binance doesn't have GID-based cancel. Use REST cancel_all_orders().
    }

    fn encode_cancel_all(&self, _buf: &mut bytes::BytesMut) {
        // Binance cancel-all is REST-only.
    }

    fn symbol_for_hash(&self, hash: u64) -> Option<&str> {
        self.symbols.iter()
            .find(|(h, _)| *h == hash)
            .map(|(_, s)| s.as_str())
    }
}

// ═══════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_physics() {
        let venue = BinanceVenue::new();
        let p = venue.physics();
        assert_eq!(p.align_price(83451.234, true), 83451.23); // tick=0.01, bid=floor
        assert_eq!(p.align_price(83451.234, false), 83451.24); // ask=ceil
    }

    #[test]
    fn test_parse_book_ticker() {
        let mut venue = BinanceVenue::new();
        let raw = br#"{"e":"bookTicker","u":400900,"s":"BTCUSDT","b":"83000.50","B":"1.23","a":"83001.20","A":"0.56","T":1568014460891,"E":1568014460893}"#;
        match venue.parse_raw(raw) {
            VenueMessage::Tick(tick) => {
                assert_eq!(tick.exchange, ExchangeId::Binance);
                assert!(tick.bid > 0);
                assert!(tick.ask > tick.bid);
            }
            _ => panic!("Expected Tick"),
        }
    }

    #[test]
    fn test_parse_subscription_result() {
        let mut venue = BinanceVenue::new();
        let raw = br#"{"result":null,"id":1}"#;
        match venue.parse_raw(raw) {
            VenueMessage::Subscribed { .. } => {}
            _ => panic!("Expected Subscribed"),
        }
    }

    #[test]
    fn test_parse_rest_fill() {
        let raw = br#"{"symbol":"BTCUSDT","orderId":12345,"status":"FILLED","side":"BUY","executedQty":"0.001","cummulativeQuoteQty":"83.0","transactTime":1700000000000,"origQty":"0.001"}"#;
        match BinanceVenue::parse_rest_order_response(raw) {
            Some(VenueMessage::Fill(fill)) => {
                assert_eq!(fill.exchange, ExchangeId::Binance);
                assert!(fill.amount > 0.0);
                assert!(fill.price > 0);
            }
            _ => panic!("Expected Fill"),
        }
    }

    #[test]
    fn test_register_symbol() {
        let mut venue = BinanceVenue::new();
        venue.register_symbol("BTCUSDT");
        let hash = Binance::symbol_to_hash("BTCUSDT");
        assert_eq!(venue.symbol_for_hash(hash), Some("BTCUSDT"));
    }

    #[test]
    fn test_auth_is_none() {
        let venue = BinanceVenue::new();
        let creds = ExchangeCredentials { api_key: "k".into(), api_secret: "s".into() };
        assert!(venue.auth_message(&creds).is_none());
    }
}
