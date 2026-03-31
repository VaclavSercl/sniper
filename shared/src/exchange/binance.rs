// ═══════════════════════════════════════════════════════════
// 🟡 Binance Exchange Implementation
// Sniper Armada · Phase 5.3 · v17.0
//
// Implements the Exchange abstraction for Binance Spot.
// Uses bookTicker stream for real-time BBA (Best Bid/Ask).
//
// Key differences from Bitfinex:
//   - Auth: HMAC-SHA256 + timestamp (REST only, WS is public)
//   - Ticker: JSON {"s":"BTCUSDT","b":"83000","a":"83010"} (not array)
//   - Orders: REST API with HMAC signature (not WebSocket)
//   - Symbols: lowercase without prefix (btcusdt, not tBTCUSD)
//   - Rate limit: 1200 requests/min (REST), 5 msg/sec (WS)
// ═══════════════════════════════════════════════════════════

use super::types::*;
use crate::PRICE_SCALE_I;
use crate::moonshot_types::str_to_symbol_hash;

/// Binance Spot WebSocket base URL
pub const WS_URL: &str = "wss://stream.binance.com:9443/ws";

/// Binance Spot REST API base URL
pub const REST_URL: &str = "https://api.binance.com";

/// Maximum symbols per combined stream connection
pub const MAX_STREAMS_PER_CONN: usize = 200;

/// Binance exchange configuration.
pub struct Binance {
    pub credentials: Option<ExchangeCredentials>,
}

impl Binance {
    /// Create a new Binance instance with optional credentials from env.
    pub fn new() -> Self {
        Self {
            credentials: ExchangeCredentials::from_env("BINANCE"),
        }
    }

    /// Create with explicit credentials.
    pub fn with_credentials(creds: ExchangeCredentials) -> Self {
        Self { credentials: Some(creds) }
    }

    /// Get exchange identifier.
    pub fn id(&self) -> ExchangeId { ExchangeId::Binance }

    /// Build a combined stream URL for multiple symbols.
    /// Example: wss://stream.binance.com:9443/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker
    pub fn combined_stream_url(symbols: &[&str]) -> String {
        let streams: Vec<String> = symbols.iter()
            .map(|s| format!("{}@bookTicker", s.to_lowercase()))
            .collect();
        format!("wss://stream.binance.com:9443/stream?streams={}", streams.join("/"))
    }

    /// Build a single symbol bookTicker stream URL.
    pub fn book_ticker_url(symbol: &str) -> String {
        format!("{}/{}@bookTicker", WS_URL, symbol.to_lowercase())
    }

    /// Build WebSocket subscribe message (for single connection, multiple streams).
    /// Binance WS uses JSON-RPC style: {"method":"SUBSCRIBE","params":["btcusdt@bookTicker"],"id":1}
    pub fn subscribe_book_ticker(symbols: &[&str], request_id: u64) -> String {
        let params: Vec<String> = symbols.iter()
            .map(|s| format!("\"{}@bookTicker\"", s.to_lowercase()))
            .collect();
        format!(
            r#"{{"method":"SUBSCRIBE","params":[{}],"id":{}}}"#,
            params.join(","),
            request_id
        )
    }

    /// Build WebSocket unsubscribe message.
    pub fn unsubscribe_book_ticker(symbols: &[&str], request_id: u64) -> String {
        let params: Vec<String> = symbols.iter()
            .map(|s| format!("\"{}@bookTicker\"", s.to_lowercase()))
            .collect();
        format!(
            r#"{{"method":"UNSUBSCRIBE","params":[{}],"id":{}}}"#,
            params.join(","),
            request_id
        )
    }

    /// Convert Binance symbol (e.g., "BTCUSDT") to Bitfinex-style hash.
    /// This allows cross-exchange price comparison using the same hash.
    pub fn symbol_to_hash(binance_symbol: &str) -> u64 {
        // Prefix with "b:" to distinguish from Bitfinex symbols
        let key = format!("b:{}", binance_symbol.to_uppercase());
        str_to_symbol_hash(&key)
    }

    /// Generate HMAC-SHA256 signature for Binance REST API.
    pub fn sign_request(&self, query_string: &str) -> Option<String> {
        let creds = self.credentials.as_ref()?;
        Some(super::hmac_sha256_hex(&creds.api_secret, query_string))
    }

    /// Get current server timestamp for request signing.
    pub fn timestamp_ms() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64
    }
}

impl Default for Binance {
    fn default() -> Self { Self::new() }
}

// ═══════════════════════════════════════════════════════════
// Fast Binance bookTicker Parser (Zero-allocation)
// ═══════════════════════════════════════════════════════════
//
// Input format (single stream):
// {"e":"bookTicker","u":400900217,"s":"BTCUSDT","b":"83000.50","B":"1.234","a":"83001.20","A":"0.567","T":1568014460891,"E":1568014460893}
//
// Input format (combined stream):
// {"stream":"btcusdt@bookTicker","data":{"e":"bookTicker",...}}
//
// We extract: symbol, bid, ask — everything else is ignored for speed.

/// Parse Binance bookTicker JSON into a UnifiedTick.
/// Returns None for non-bookTicker messages (pong, subscribe results, etc.)
///
/// This is a hand-rolled parser for maximum speed in the hot path.
/// It avoids serde_json deserialization overhead (~500ns → ~50ns).
pub fn fast_parse_book_ticker(data: &[u8]) -> Option<UnifiedTick> {
    // Quick reject: must start with '{'
    if data.len() < 50 || data[0] != b'{' { return None; }

    // Check if this is a combined stream (has "data" field)
    // If so, find the inner JSON object
    let inner = if let Some(pos) = find_key(data, b"\"data\"") {
        // Skip to the '{' after "data":
        let start = memchr_after(data, b'{', pos + 6)?;
        &data[start..]
    } else {
        data
    };

    // Verify it's a bookTicker event (check for "bookTicker" string)
    if !contains_bytes(inner, b"bookTicker") { return None; }

    // Extract symbol "s":"BTCUSDT"
    let symbol_str = extract_string_value(inner, b"\"s\"")?;
    let symbol = Binance::symbol_to_hash(symbol_str);

    // Extract bid "b":"83000.50"
    let bid_str = extract_string_value(inner, b"\"b\"")?;
    let bid = parse_fixed_point(bid_str)?;

    // Extract ask "a":"83001.20"
    let ask_str = extract_string_value(inner, b"\"a\"")?;
    let ask = parse_fixed_point(ask_str)?;

    // Extract bid volume "B":"1.234"
    let bid_vol = extract_string_value(inner, b"\"B\"")
        .and_then(parse_fixed_point)
        .unwrap_or(0);

    // Extract ask volume "A":"0.567"
    let ask_vol = extract_string_value(inner, b"\"A\"")
        .and_then(parse_fixed_point)
        .unwrap_or(0);

    // Extract event time "E":1568014460893
    let exchange_ts = extract_number_value(inner, b"\"E\"").unwrap_or(0);

    Some(UnifiedTick {
        exchange: ExchangeId::Binance,
        symbol,
        bid,
        ask,
        bid_vol,
        ask_vol,
        exchange_ts,
    })
}

// ═══════════════════════════════════════════════════════════
// Low-level parser helpers (zero-allocation)
// ═══════════════════════════════════════════════════════════

/// Find key position in JSON bytes.
#[inline]
fn find_key(data: &[u8], key: &[u8]) -> Option<usize> {
    data.windows(key.len()).position(|w| w == key)
}

/// Find next occurrence of byte after position.
#[inline]
fn memchr_after(data: &[u8], byte: u8, start: usize) -> Option<usize> {
    data[start..].iter().position(|&b| b == byte).map(|p| p + start)
}

/// Check if data contains a byte sequence.
#[inline]
fn contains_bytes(data: &[u8], needle: &[u8]) -> bool {
    data.windows(needle.len()).any(|w| w == needle)
}

/// Extract string value for a given key: "key":"value" → "value"
fn extract_string_value<'a>(data: &'a [u8], key: &[u8]) -> Option<&'a str> {
    let pos = find_key(data, key)?;
    // Skip key, then find ":"
    let after_key = pos + key.len();
    let colon = memchr_after(data, b':', after_key)?;
    // Find opening quote
    let quote_start = memchr_after(data, b'"', colon + 1)?;
    // Find closing quote
    let quote_end = memchr_after(data, b'"', quote_start + 1)?;
    std::str::from_utf8(&data[quote_start + 1..quote_end]).ok()
}

/// Extract numeric value for a given key: "key":12345 → 12345
fn extract_number_value(data: &[u8], key: &[u8]) -> Option<u64> {
    let pos = find_key(data, key)?;
    let after_key = pos + key.len();
    let colon = memchr_after(data, b':', after_key)?;
    let mut start = colon + 1;
    // Skip whitespace
    while start < data.len() && data[start] == b' ' { start += 1; }
    let mut end = start;
    while end < data.len() && data[end].is_ascii_digit() { end += 1; }
    if start >= end { return None; }
    std::str::from_utf8(&data[start..end]).ok()?.parse().ok()
}

/// Parse decimal string to fixed-point i64 (× PRICE_SCALE).
/// "83000.50" → 8300050000000 (if PRICE_SCALE = 1e8)
#[inline]
fn parse_fixed_point(s: &str) -> Option<i64> {
    let val: f64 = s.parse().ok()?;
    Some((val * PRICE_SCALE_I as f64) as i64)
}

// ═══════════════════════════════════════════════════════════
// Symbol Mapping: Binance ↔ Bitfinex
// ═══════════════════════════════════════════════════════════

/// Known cross-exchange symbol pairs for arbitrage detection.
/// Maps Binance symbol → equivalent Bitfinex symbol.
pub const CROSS_EXCHANGE_PAIRS: &[(&str, &str)] = &[
    ("BTCUSDT", "tBTCUST"),
    ("ETHUSDT", "tETHUST"),
    ("XRPUSDT", "tXRPUST"),
    ("SOLUSDT", "tSOLUST"),
    ("DOGEUSDT", "tDOGE:UST"),
    ("ADAUSDT", "tADAUST"),
    ("AVAXUSDT", "tAVAX:UST"),
    ("LTCUSDT", "tLTCUST"),
    ("LINKUSDT", "tLINK:UST"),
    ("DOTUSDT", "tDOT:UST"),
];

/// Get the Bitfinex equivalent symbol for a Binance symbol.
pub fn binance_to_bitfinex(binance_sym: &str) -> Option<&'static str> {
    CROSS_EXCHANGE_PAIRS.iter()
        .find(|(b, _)| *b == binance_sym)
        .map(|(_, bf)| *bf)
}

/// Get the Binance equivalent symbol for a Bitfinex symbol.
pub fn bitfinex_to_binance(bitfinex_sym: &str) -> Option<&'static str> {
    CROSS_EXCHANGE_PAIRS.iter()
        .find(|(_, bf)| *bf == bitfinex_sym)
        .map(|(b, _)| *b)
}

// ═══════════════════════════════════════════════════════════
// REST API Order Sender (HMAC-SHA256 Signed)
// ═══════════════════════════════════════════════════════════
//
// Binance uses REST API (not WebSocket) for order management.
// Every request must include:
//   1. X-MBX-APIKEY header
//   2. timestamp parameter (epoch ms)
//   3. HMAC-SHA256 signature of the query string
//
// Rate limit: 1200 requests/minute (weight-based)
// Order endpoint weight: 1 per order

/// Binance order time-in-force
#[derive(Debug, Clone, Copy)]
pub enum BinanceTimeInForce {
    /// Good Till Cancelled (stays until filled or cancelled)
    Gtc,
    /// Immediate Or Cancel (fill what you can, cancel rest)
    Ioc,
    /// Fill Or Kill (fill entirely or cancel entirely)
    Fok,
}

impl BinanceTimeInForce {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Gtc => "GTC",
            Self::Ioc => "IOC",
            Self::Fok => "FOK",
        }
    }
}

/// Binance order type string
#[derive(Debug, Clone, Copy)]
pub enum BinanceOrderType {
    Limit,
    Market,
    LimitMaker, // Post-only (rejected if would take)
}

impl BinanceOrderType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Limit => "LIMIT",
            Self::Market => "MARKET",
            Self::LimitMaker => "LIMIT_MAKER",
        }
    }
}

/// Convert universal OrderType → Binance types
pub fn to_binance_order_type(ot: &OrderType) -> (BinanceOrderType, BinanceTimeInForce) {
    match ot {
        OrderType::Limit => (BinanceOrderType::Limit, BinanceTimeInForce::Gtc),
        OrderType::LimitPostOnly => (BinanceOrderType::LimitMaker, BinanceTimeInForce::Gtc),
        OrderType::Ioc => (BinanceOrderType::Limit, BinanceTimeInForce::Ioc),
        OrderType::Market => (BinanceOrderType::Market, BinanceTimeInForce::Gtc),
    }
}

/// Signed request components — caller sends these with their HTTP client.
#[derive(Debug)]
pub struct SignedRequest {
    /// Full URL with query string and signature
    pub url: String,
    /// HTTP method
    pub method: &'static str,
    /// API Key header value (for X-MBX-APIKEY)
    pub api_key: String,
}

impl Binance {
    /// Build a signed query string: append timestamp + signature.
    fn sign_query(&self, params: &str) -> Option<String> {
        let creds = self.credentials.as_ref()?;
        let ts = Self::timestamp_ms();
        let full_params = if params.is_empty() {
            format!("timestamp={}", ts)
        } else {
            format!("{}&timestamp={}", params, ts)
        };
        let sig = super::hmac_sha256_hex(&creds.api_secret, &full_params);
        Some(format!("{}&signature={}", full_params, sig))
    }

    /// Build a signed request for any endpoint.
    fn signed_request(&self, method: &'static str, endpoint: &str, params: &str) -> Option<SignedRequest> {
        let creds = self.credentials.as_ref()?;
        let signed_qs = self.sign_query(params)?;
        Some(SignedRequest {
            url: format!("{}{}{}{}", REST_URL, endpoint, "?", signed_qs),
            method,
            api_key: creds.api_key.clone(),
        })
    }

    // ─── Order Endpoints ─────────────────────────────────

    /// POST /api/v3/order — Place a new order.
    ///
    /// Returns a `SignedRequest` ready to be executed by an HTTP client.
    /// The caller is responsible for sending the request and handling the response.
    ///
    /// # Example response (LIMIT):
    /// ```json
    /// {"symbol":"BTCUSDT","orderId":12345,"clientOrderId":"abc","status":"NEW",...}
    /// ```
    pub fn new_order(&self, req: &OrderRequest) -> Option<SignedRequest> {
        let (ot, tif) = to_binance_order_type(&req.order_type);
        let side = match req.side {
            OrderSide::Buy => "BUY",
            OrderSide::Sell => "SELL",
        };
        let price_f = req.price as f64 / crate::PRICE_SCALE;

        let mut params = format!(
            "symbol={}&side={}&type={}&quantity={:.8}",
            req.symbol, side, ot.as_str(), req.amount.abs()
        );

        // LIMIT and LIMIT_MAKER need price; MARKET does not
        match ot {
            BinanceOrderType::Limit => {
                params.push_str(&format!("&price={:.8}&timeInForce={}", price_f, tif.as_str()));
            }
            BinanceOrderType::LimitMaker => {
                params.push_str(&format!("&price={:.8}", price_f));
            }
            BinanceOrderType::Market => {
                // No price needed
            }
        }

        // newOrderRespType=RESULT gives fill info immediately
        params.push_str("&newOrderRespType=RESULT");

        self.signed_request("POST", "/api/v3/order", &params)
    }

    /// DELETE /api/v3/order — Cancel an order by orderId.
    pub fn cancel_order(&self, symbol: &str, order_id: u64) -> Option<SignedRequest> {
        let params = format!("symbol={}&orderId={}", symbol, order_id);
        self.signed_request("DELETE", "/api/v3/order", &params)
    }

    /// DELETE /api/v3/order — Cancel an order by clientOrderId.
    pub fn cancel_order_by_client_id(&self, symbol: &str, client_order_id: &str) -> Option<SignedRequest> {
        let params = format!("symbol={}&origClientOrderId={}", symbol, client_order_id);
        self.signed_request("DELETE", "/api/v3/order", &params)
    }

    /// DELETE /api/v3/openOrders — Cancel ALL open orders for a symbol.
    pub fn cancel_all_orders(&self, symbol: &str) -> Option<SignedRequest> {
        let params = format!("symbol={}", symbol);
        self.signed_request("DELETE", "/api/v3/openOrders", &params)
    }

    // ─── Account/Info Endpoints ──────────────────────────

    /// GET /api/v3/account — Get account info (balances, permissions).
    pub fn account_info(&self) -> Option<SignedRequest> {
        self.signed_request("GET", "/api/v3/account", "")
    }

    /// GET /api/v3/openOrders — Get all open orders (optionally per symbol).
    pub fn open_orders(&self, symbol: Option<&str>) -> Option<SignedRequest> {
        let params = match symbol {
            Some(s) => format!("symbol={}", s),
            None => String::new(),
        };
        self.signed_request("GET", "/api/v3/openOrders", &params)
    }

    /// GET /api/v3/myTrades — Get recent trades for a symbol.
    pub fn my_trades(&self, symbol: &str, limit: u32) -> Option<SignedRequest> {
        let params = format!("symbol={}&limit={}", symbol, limit);
        self.signed_request("GET", "/api/v3/myTrades", &params)
    }

    /// GET /api/v3/exchangeInfo — Get exchange info (no signature needed).
    /// Returns filters, min quantities, tick sizes, etc.
    pub fn exchange_info_url() -> String {
        format!("{}/api/v3/exchangeInfo", REST_URL)
    }

    /// GET /api/v3/time — Get server time (no signature needed).
    /// Use this to check clock sync.
    pub fn server_time_url() -> String {
        format!("{}/api/v3/time", REST_URL)
    }
}

// ═══════════════════════════════════════════════════════════
// Batch Order Conversion: Universal → Binance
// ═══════════════════════════════════════════════════════════

impl Binance {
    /// Convert a universal `OrderRequest` to Binance format and return a signed request.
    /// This is the main entry point for bots sending orders through the Exchange trait.
    pub fn submit_order(&self, req: &OrderRequest) -> Option<SignedRequest> {
        // Translate Bitfinex symbol to Binance if needed
        let binance_sym = if req.symbol.starts_with('t') {
            // Bitfinex format (tBTCUSD) → Binance (BTCUSDT)
            bitfinex_to_binance(&req.symbol)
                .map(|s| s.to_string())
                .unwrap_or_else(|| req.symbol.clone())
        } else {
            req.symbol.clone()
        };

        let translated_req = OrderRequest {
            gid: req.gid,
            symbol: binance_sym,
            side: req.side,
            price: req.price,
            amount: req.amount,
            order_type: req.order_type,
            flags: req.flags,
        };

        self.new_order(&translated_req)
    }

    /// Submit a batch of orders. Since Binance doesn't support atomic batches
    /// like Bitfinex's ox_multi, this returns a Vec of individual signed requests.
    /// The caller should send them sequentially with rate limiting.
    pub fn submit_batch(&self, batch: &BatchOrder) -> Vec<SignedRequest> {
        let mut requests = Vec::new();

        // Cancel phase
        if let Some(ref symbol) = batch.cancel_symbol {
            // Translate symbol if needed
            let binance_sym = if symbol.starts_with('t') {
                bitfinex_to_binance(symbol)
                    .unwrap_or(symbol)
            } else {
                symbol
            };
            if let Some(req) = self.cancel_all_orders(binance_sym) {
                requests.push(req);
            }
        }

        // Place new orders
        for order in &batch.orders {
            if let Some(req) = self.submit_order(order) {
                requests.push(req);
            }
        }

        requests
    }
}

