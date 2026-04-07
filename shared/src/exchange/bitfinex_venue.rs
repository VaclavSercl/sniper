// ═══════════════════════════════════════════════════════════
// 🔵 BitfinexVenue — VenueAdapter implementation for Bitfinex
// Sniper Armada · v20.0 Hexagonal Architecture
//
// Migrates all Bitfinex-specific logic into a single VenueAdapter
// implementation. No bot code references Bitfinex directly.
//
// Covers: auth, ticker parsing, book parsing, fill normalization,
//         order encoding (ox_multi), cancel, physics.
// ═══════════════════════════════════════════════════════════

use super::types::*;
use super::venue::*;
use crate::moonshot_types::str_to_symbol_hash;

/// Maximum channels we track (ticker + book per symbol, + auth channel)
const MAX_CHANNELS: usize = 64;

/// Bitfinex VenueAdapter — Zero-cost exchange abstraction.
///
/// Tracks channel→symbol mappings for incoming market data.
/// All parsing, encoding, and physics are encapsulated here.
pub struct BitfinexVenue {
    /// Channel ID → symbol hash mapping (populated on subscribe confirmation)
    chan_to_symbol: [(i64, u64); MAX_CHANNELS],
    /// Number of active channel mappings
    chan_count: usize,
    /// Symbol hash → BFX native string (e.g., hash → "tBTCUSD")
    symbols: arrayvec::ArrayVec<(u64, arrayvec::ArrayString<16>), 32>,
}

impl BitfinexVenue {
    pub fn new() -> Self {
        Self {
            chan_to_symbol: [(0, 0); MAX_CHANNELS],
            chan_count: 0,
            symbols: arrayvec::ArrayVec::new(),
        }
    }

    /// Register a symbol for hash↔string mapping.
    pub fn register_symbol(&mut self, symbol: &str) {
        let hash = str_to_symbol_hash(symbol);
        if !self.symbols.iter().any(|(h, _)| *h == hash)
            && let Ok(s) = arrayvec::ArrayString::try_from(symbol) {
                let _ = self.symbols.try_push((hash, s));
            }
    }

    /// Find symbol hash for a channel ID.
    #[inline]
    fn symbol_for_chan(&self, chan_id: i64) -> Option<u64> {
        self.chan_to_symbol[..self.chan_count]
            .iter()
            .find(|(c, _)| *c == chan_id)
            .map(|(_, s)| *s)
    }

    /// Register a channel→symbol mapping.
    fn register_channel(&mut self, chan_id: i64, symbol_hash: u64) {
        if self.chan_count < MAX_CHANNELS {
            self.chan_to_symbol[self.chan_count] = (chan_id, symbol_hash);
            self.chan_count += 1;
        }
    }

    /// Parse BFX ticker array: [chanId, [BID, BID_SIZE, ASK, ASK_SIZE, ...]]
    /// Reuses the fast zero-copy parser from exchange/mod.rs
    fn parse_ticker(&self, data: &[u8]) -> Option<VenueMessage> {
        let (chan_id, bid, ask) = super::fast_parse_ticker(data)?;
        let symbol = self.symbol_for_chan(chan_id)?;
        Some(VenueMessage::Tick(UnifiedTick {
            exchange: ExchangeId::Bitfinex,
            symbol,
            bid,
            ask,
            bid_vol: 0,
            ask_vol: 0,
            exchange_ts: 0,
        }))
    }

    /// Parse BFX execution reports from auth channel.
    /// Format: [0, "tu", [ID, PAIR, MTS, ORDER_ID, EXEC_AMOUNT, EXEC_PRICE, ...FEE, FEE_CURRENCY...]]
    /// Format: [0, "on", [...]] / [0, "ou", [...]] / [0, "oc", [...]]
    fn parse_execution(&self, data: &[u8]) -> Option<VenueMessage> {
        // Minimal check: must start with [0,"
        if data.len() < 10 || data[0] != b'[' { return None; }

        // Check for trade execution: "tu" (trade update)
        if data.len() > 6 && &data[1..5] == b"0,\"t" {
            return self.parse_fill(data);
        }

        // Check for order updates: "on" (new), "ou" (update), "oc" (cancel)
        if data.len() > 6 && &data[1..5] == b"0,\"o" {
            return self.parse_order_update(data);
        }

        None
    }

    /// Parse a BFX fill (trade execution).
    /// [0,"tu",[ID, PAIR, MTS_CREATE, ORDER_ID, EXEC_AMOUNT, EXEC_PRICE, TYPE, ...FEE, FEE_CURRENCY]]
    fn parse_fill(&self, data: &[u8]) -> Option<VenueMessage> {
        // Parse using serde_json for fill messages (not hot path — fills are rare)
        let v: serde_json::Value = serde_json::from_slice(data).ok()?;
        let arr = v.get(2)?.as_array()?;
        if arr.len() < 10 { return None; }

        let pair = arr.get(1)?.as_str().unwrap_or_default();
        let exec_amount = arr.get(4)?.as_f64()?;
        let exec_price = arr.get(5)?.as_f64()?;
        let fee = arr.get(9)?.as_f64().unwrap_or(0.0);
        let fee_cur = arr.get(10)?.as_str().unwrap_or("");
        let order_id = arr.get(3)?.as_u64().unwrap_or(0);

        let side = if exec_amount > 0.0 { OrderSide::Buy } else { OrderSide::Sell };

        Some(VenueMessage::Fill(UnifiedFill {
            exchange: ExchangeId::Bitfinex,
            client_id: order_id,
            symbol_hash: str_to_symbol_hash(pair),
            side,
            price: (exec_price * crate::PRICE_SCALE) as i64,
            amount: exec_amount.abs(),
            fee,
            fee_currency: str_to_symbol_hash(fee_cur),
            exchange_ts: arr.get(2)?.as_u64().unwrap_or(0),
        }))
    }

    /// Parse a BFX order update (on/ou/oc).
    fn parse_order_update(&self, data: &[u8]) -> Option<VenueMessage> {
        let v: serde_json::Value = serde_json::from_slice(data).ok()?;
        let type_str = v.get(1)?.as_str()?;
        let arr = v.get(2)?.as_array()?;
        if arr.len() < 5 { return None; }

        let order_id = arr.first()?.as_u64().unwrap_or(0);
        let remaining = arr.get(6)?.as_f64().unwrap_or(0.0).abs();
        let status_str = arr.get(13)?.as_str().unwrap_or("");

        let status = match type_str {
            "on" => OrderStatus::Accepted,
            "ou" => {
                if status_str.contains("PARTIALLY") { OrderStatus::PartiallyFilled }
                else { OrderStatus::Accepted }
            }
            "oc" => {
                if status_str.contains("CANCELED") { OrderStatus::Canceled }
                else if status_str.contains("EXECUTED") { OrderStatus::Filled }
                else { OrderStatus::Canceled }
            }
            _ => return None,
        };

        Some(VenueMessage::OrderUpdate(UnifiedOrderUpdate {
            exchange: ExchangeId::Bitfinex,
            client_id: order_id,
            exchange_order_id: order_id,
            status,
            remaining,
        }))
    }

    /// Encode symbol hash back to BFX native string for order placement.
    fn symbol_str(&self, hash: u64) -> &str {
        self.symbols.iter()
            .find(|(h, _)| *h == hash)
            .map(|(_, s)| s.as_str())
            .unwrap_or("tBTCUSD")
    }
}

// ═══════════════════════════════════════════════════════════
// Zero-Alloc Order Builders (for L0 hot path)
//
// These write BFX wire format directly to out_buf without heap
// allocations. Bots call these instead of manual extend_from_slice.
// This centralizes ALL Bitfinex encoding in one file.
// ═══════════════════════════════════════════════════════════

impl BitfinexVenue {
    // ── Update builder pro bleskové úpravy bez rušení fronty (Issue #11) ──

    /// Write an UPDATE (Amend) order into the batch.
    /// Pattern: `,["ou",{"id":123456789,"amount":"0.001","price":"68000.0"}]`
    #[inline]
    pub fn write_amend_order(
        buf: &mut bytes::BytesMut,
        order_id: u64,
        amount: &str,
        price: &str,
    ) {
        buf.extend_from_slice(b",[\"ou\",{\"id\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(order_id).as_bytes());
        buf.extend_from_slice(b",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\"}]");
    }

    // ── ox_multi batch builders ──

    /// Write the opening of an ox_multi batch with cancel-by-symbol.
    /// Pattern: `[0,"ox_multi",null,[["oc_multi",{"symbol":"tBTCUSD"}]`
    #[inline]
    pub fn write_batch_open_cancel_sym(buf: &mut bytes::BytesMut, symbol: &[u8]) {
        buf.extend_from_slice(b"[0,\"ox_multi\",null,[[\"oc_multi\",{\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\"}]");
    }

    /// Write the opening of an ox_multi batch with cancel-by-gid.
    /// Pattern: `[0,"ox_multi",null,[["oc_multi",{"gid":[3000]}]`
    #[inline]
    pub fn write_batch_open_cancel_gid(buf: &mut bytes::BytesMut, gid: u32) {
        buf.extend_from_slice(b"[0,\"ox_multi\",null,[[\"oc_multi\",{\"gid\":[");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b"]}]");
    }

    /// Write the opening of an ox_multi batch without cancel.
    /// Pattern: `[0,"ox_multi",null,[`
    #[inline]
    pub fn write_batch_open(buf: &mut bytes::BytesMut) {
        buf.extend_from_slice(b"[0,\"ox_multi\",null,[");
    }

    /// Write a LIMIT order into the batch (comma-separated).
    /// Pattern: `,[\"on\",{\"gid\":3000,\"symbol\":\"tBTCUSD\",\"amount\":\"0.001\",\"price\":\"68000.0\",\"type\":\"EXCHANGE LIMIT\"}]`
    #[inline]
    pub fn write_limit_order(
        buf: &mut bytes::BytesMut,
        gid: u32,
        symbol: &[u8],
        amount: &str,   // pre-formatted by ryu
        price: &str,    // pre-formatted by ryu
    ) {
        buf.extend_from_slice(b",[\"on\",{\"gid\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b",\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\"}]");
    }

    /// Write a LIMIT POST-ONLY order into the batch.
    #[inline]
    pub fn write_limit_postonly_order(
        buf: &mut bytes::BytesMut,
        gid: u32,
        symbol: &[u8],
        amount: &str,
        price: &str,
    ) {
        buf.extend_from_slice(b",[\"on\",{\"gid\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b",\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\",\"flags\":4096}]");
    }

    /// Write an FOK order into the batch (handles comma separation dynamically).
    #[inline]
    pub fn write_fok_order(
        buf: &mut bytes::BytesMut,
        gid: u32,
        symbol: &[u8],
        amount: &str,
        price: &str,
    ) {
        if !buf.ends_with(b"[") {
            buf.extend_from_slice(b",");
        }
        buf.extend_from_slice(b"[\"on\",{\"gid\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b",\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\",\"type\":\"EXCHANGE FOK\"}]");
    }

    /// Write an IOC order into the batch.
    #[inline]
    pub fn write_ioc_order(
        buf: &mut bytes::BytesMut,
        gid: u32,
        symbol: &[u8],
        amount: &str,
        price: &str,
    ) {
        buf.extend_from_slice(b",[\"on\",{\"gid\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b",\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]");
    }

    /// Write a standalone IOC order (not inside ox_multi batch).
    /// Pattern: `[0,"on",null,{"gid":2001,"symbol":"tBTCUSD","amount":"0.001","price":"68000","type":"EXCHANGE IOC"}]`
    #[inline]
    pub fn write_standalone_ioc(
        buf: &mut bytes::BytesMut,
        gid: u32,
        symbol: &[u8],
        amount: &str,
        price: &str,
    ) {
        buf.extend_from_slice(b"[0,\"on\",null,{\"gid\":");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b",\"symbol\":\"");
        buf.extend_from_slice(symbol);
        buf.extend_from_slice(b"\",\"amount\":\"");
        buf.extend_from_slice(amount.as_bytes());
        buf.extend_from_slice(b"\",\"price\":\"");
        buf.extend_from_slice(price.as_bytes());
        buf.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]");
    }

    /// Close the ox_multi batch.
    /// Pattern: `]]`
    #[inline]
    pub fn write_batch_close(buf: &mut bytes::BytesMut) {
        buf.extend_from_slice(b"]]");
    }

    /// Write a cancel-all-by-gid standalone message.
    #[inline]
    pub fn write_cancel_gid_standalone(buf: &mut bytes::BytesMut, gid: u32) {
        buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"gid\":[");
        let mut itoa_buf = itoa::Buffer::new();
        buf.extend_from_slice(itoa_buf.format(gid).as_bytes());
        buf.extend_from_slice(b"]}]");
    }
}

impl Default for BitfinexVenue {
    fn default() -> Self { Self::new() }
}

// ═══════════════════════════════════════════════════════════
// VenueAdapter Implementation
// ═══════════════════════════════════════════════════════════

impl VenueAdapter for BitfinexVenue {
    fn id(&self) -> ExchangeId { ExchangeId::Bitfinex }
    fn name(&self) -> &str { "Bitfinex" }
    fn env_prefix(&self) -> &str { "BITFINEX" }
    fn ws_url(&self) -> &str { "wss://api.bitfinex.com/ws/2" }

    fn physics(&self) -> ExchangePhysics {
        ExchangePhysics {
            maker_fee_bps: -2.0,    // Bitfinex maker rebate
            taker_fee_bps: 5.5,     // Bitfinex taker fee
            tick_size: 0.1,         // BTC/USD minimum price increment
            lot_size: 0.00001,      // Minimum BTC quantity
            min_order_size: 0.00004, // Minimum order size
            max_orders_per_sec: 90, // Rate limit
        }
    }

    fn auth_message(&self, creds: &ExchangeCredentials) -> Option<String> {
        Some(super::bitfinex_auth_message(&creds.api_key, &creds.api_secret))
    }

    fn subscribe_ticker(&self, symbol: &str) -> String {
        super::bitfinex_subscribe_ticker(symbol)
    }

    fn subscribe_book(&self, symbol: &str, precision: &str, depth: u32) -> String {
        super::bitfinex_subscribe_book(symbol, precision, depth)
    }

    fn parse_raw(&mut self, raw: &[u8]) -> VenueMessage {
        if raw.is_empty() { return VenueMessage::Unknown; }

        match raw[0] {
            // JSON object: system events (auth, subscribe, info)
            b'{' => {
                let v: serde_json::Value = match serde_json::from_slice(raw) {
                    Ok(v) => v,
                    Err(_) => return VenueMessage::Unknown,
                };

                if let Some(event) = v.get("event").and_then(|e| e.as_str()) {
                    match event {
                        "auth" => {
                            let success = v.get("status")
                                .and_then(|s| s.as_str())
                                .map(|s| s == "OK")
                                .unwrap_or(false);
                            return VenueMessage::Authenticated { success };
                        }
                        "subscribed" => {
                            let chan_id = v.get("chanId")
                                .and_then(|c| c.as_i64())
                                .unwrap_or(0);
                            // Extract symbol from "symbol" or "key" field
                            let sym_str = v.get("symbol")
                                .or_else(|| v.get("key"))
                                .and_then(|s| s.as_str())
                                .unwrap_or("");
                            let sym_hash = str_to_symbol_hash(sym_str);
                            self.register_channel(chan_id, sym_hash);
                            return VenueMessage::Subscribed {
                                chan_id,
                                symbol: sym_hash,
                            };
                        }
                        _ => {}
                    }
                }
                VenueMessage::Unknown
            }

            // JSON array: market data or execution reports
            b'[' => {
                // Check for heartbeat: [chanId, "hb"]
                if raw.len() > 5 {
                    // Fast check for "hb" pattern
                    let mut i = 1;
                    while i < raw.len() && raw[i] != b',' { i += 1; }
                    if i + 4 < raw.len() && raw[i+1] == b'"' && raw[i+2] == b'h' && raw[i+3] == b'b' {
                        return VenueMessage::Heartbeat;
                    }
                }

                // Channel 0 = auth channel (fills, orders)
                if raw.len() > 3 && raw[1] == b'0' && raw[2] == b',' {
                    if let Some(msg) = self.parse_execution(raw) {
                        return msg;
                    }
                    return VenueMessage::Unknown;
                }

                // Market data channel (ticker)
                if let Some(msg) = self.parse_ticker(raw) {
                    return msg;
                }

                VenueMessage::Unknown
            }

            _ => VenueMessage::Unknown,
        }
    }

    fn encode_batch(&self, batch: &VenueBatchOrder, buf: &mut bytes::BytesMut) {
        use std::fmt::Write;

        if batch.orders.is_empty() && batch.cancel_gid.is_none() && batch.cancel_cids.is_empty() {
            return;
        }

        let mut msg = String::with_capacity(512);
        msg.push_str("[0,\"ox_multi\",null,[");

        // Cancel section
        if let Some(gid) = batch.cancel_gid {
            if let Some(sym_hash) = batch.cancel_symbol {
                let sym = self.symbol_str(sym_hash);
                let _ = write!(msg, "[\"oc_multi\",{{\"symbol\":\"{}\",\"all\":1}}],", sym);
            } else {
                let _ = write!(msg, "[\"oc_multi\",{{\"gid\":[{}]}}],", gid);
            }
        }

        // Orders section
        let physics = self.physics();
        for (i, order) in batch.orders.iter().enumerate() {
            let sym = self.symbol_str(order.symbol_hash);
            let raw_price = order.price as f64 / crate::PRICE_SCALE;
            let is_bid = matches!(order.side, OrderSide::Buy);
            let aligned_price = physics.align_price(raw_price, is_bid);
            let aligned_qty = physics.align_qty(order.amount);

            let signed_amount = match order.side {
                OrderSide::Buy => aligned_qty,
                OrderSide::Sell => -aligned_qty,
            };

            let type_str = match order.order_type {
                OrderType::Limit => "EXCHANGE LIMIT",
                OrderType::LimitPostOnly => "EXCHANGE LIMIT",
                OrderType::Ioc => "EXCHANGE IOC",
                OrderType::Market => "EXCHANGE MARKET",
            };

            let flags = match order.order_type {
                OrderType::LimitPostOnly => 4096, // post-only
                _ => 0,
            };

            if flags > 0 {
                let _ = write!(
                    msg,
                    "[\"on\",{{\"gid\":{},\"symbol\":\"{}\",\"amount\":\"{:.5}\",\"price\":\"{:.2}\",\"type\":\"{}\",\"flags\":{}}}]",
                    order.gid, sym, signed_amount, aligned_price, type_str, flags
                );
            } else {
                let _ = write!(
                    msg,
                    "[\"on\",{{\"gid\":{},\"symbol\":\"{}\",\"amount\":\"{:.5}\",\"price\":\"{:.2}\",\"type\":\"{}\"}}]",
                    order.gid, sym, signed_amount, aligned_price, type_str
                );
            }

            if i + 1 < batch.orders.len() {
                msg.push(',');
            }
        }

        msg.push_str("]]");
        buf.extend_from_slice(msg.as_bytes());
    }

    fn encode_cancel_gid(&self, gid: u32, buf: &mut bytes::BytesMut) {
        let msg = format!("[0,\"oc_multi\",null,{{\"gid\":[{}]}}]", gid);
        buf.extend_from_slice(msg.as_bytes());
    }

    fn encode_cancel_all(&self, buf: &mut bytes::BytesMut) {
        buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"all\":1}]");
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
    fn test_physics_align_price() {
        let venue = BitfinexVenue::new();
        let p = venue.physics();
        // Bid: round down
        assert_eq!(p.align_price(68451.23, true), 68451.2);
        // Ask: round up
        assert_eq!(p.align_price(68451.23, false), 68451.3);
    }

    #[test]
    fn test_physics_align_qty() {
        let venue = BitfinexVenue::new();
        let p = venue.physics();
        assert!((p.align_qty(0.001234) - 0.00123).abs() < 1e-10);
    }

    #[test]
    fn test_parse_auth() {
        let mut venue = BitfinexVenue::new();
        let raw = br#"{"event":"auth","status":"OK","chanId":0}"#;
        match venue.parse_raw(raw) {
            VenueMessage::Authenticated { success } => assert!(success),
            _ => panic!("Expected Authenticated"),
        }
    }

    #[test]
    fn test_parse_auth_fail() {
        let mut venue = BitfinexVenue::new();
        let raw = br#"{"event":"auth","status":"FAILED","msg":"invalid key"}"#;
        match venue.parse_raw(raw) {
            VenueMessage::Authenticated { success } => assert!(!success),
            _ => panic!("Expected Authenticated"),
        }
    }

    #[test]
    fn test_parse_subscribed() {
        let mut venue = BitfinexVenue::new();
        let raw = br#"{"event":"subscribed","channel":"ticker","symbol":"tBTCUSD","chanId":42}"#;
        match venue.parse_raw(raw) {
            VenueMessage::Subscribed { chan_id, symbol } => {
                assert_eq!(chan_id, 42);
                assert_eq!(symbol, str_to_symbol_hash("tBTCUSD"));
            }
            _ => panic!("Expected Subscribed"),
        }
        // Verify channel mapping registered
        assert_eq!(venue.symbol_for_chan(42), Some(str_to_symbol_hash("tBTCUSD")));
    }

    #[test]
    fn test_parse_heartbeat() {
        let mut venue = BitfinexVenue::new();
        let raw = b"[42,\"hb\"]";
        match venue.parse_raw(raw) {
            VenueMessage::Heartbeat => {}
            _ => panic!("Expected Heartbeat"),
        }
    }

    #[test]
    fn test_encode_batch() {
        let mut venue = BitfinexVenue::new();
        venue.register_symbol("tBTCUSD");
        let hash = str_to_symbol_hash("tBTCUSD");

        let mut batch = VenueBatchOrder::default();
        batch.cancel_gid = Some(2000);
        batch.orders.push(VenueOrderRequest {
            client_id: 1,
            gid: 2000,
            symbol_hash: hash,
            side: OrderSide::Buy,
            price: (68000.0 * crate::PRICE_SCALE) as i64,
            amount: 0.001,
            order_type: OrderType::LimitPostOnly,
        });

        let mut buf = bytes::BytesMut::new();
        venue.encode_batch(&batch, &mut buf);
        let result = String::from_utf8(buf.to_vec()).unwrap();
        assert!(result.contains("ox_multi"));
        assert!(result.contains("tBTCUSD"));
        assert!(result.contains("4096"));  // post-only flag
    }
}
