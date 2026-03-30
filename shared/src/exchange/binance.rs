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
