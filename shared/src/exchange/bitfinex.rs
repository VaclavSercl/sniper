// ═══════════════════════════════════════════════════════════
// 🔵 Bitfinex Exchange Implementation
// Sniper Armada · Phase 5.1 · v17.0
//
// Implements the Exchange trait for Bitfinex.
// This is the CANONICAL implementation — all Bitfinex-specific
// logic lives here, not scattered across individual bots.
// ═══════════════════════════════════════════════════════════

use super::types::*;

/// Bitfinex WebSocket API endpoint
pub const WS_URL: &str = "wss://api.bitfinex.com/ws/2";

/// Bitfinex exchange configuration and state.
pub struct Bitfinex {
    pub credentials: Option<ExchangeCredentials>,
}

impl Bitfinex {
    /// Create a new Bitfinex instance with optional credentials.
    pub fn new() -> Self {
        Self {
            credentials: ExchangeCredentials::from_env("BITFINEX"),
        }
    }

    /// Create with explicit credentials (for testing).
    pub fn with_credentials(creds: ExchangeCredentials) -> Self {
        Self { credentials: Some(creds) }
    }

    /// Get the WebSocket URL.
    pub fn ws_url(&self) -> &str { WS_URL }

    /// Get exchange identifier.
    pub fn id(&self) -> ExchangeId { ExchangeId::Bitfinex }

    /// Build the authentication message for WebSocket.
    /// Returns None if no credentials are configured.
    pub fn auth_message(&self) -> Option<String> {
        let creds = self.credentials.as_ref()?;
        Some(super::bitfinex_auth_message(&creds.api_key, &creds.api_secret))
    }

    /// Build a ticker subscription message.
    pub fn subscribe_ticker(&self, symbol: &str) -> String {
        super::bitfinex_subscribe_ticker(symbol)
    }

    /// Build a book subscription message.
    pub fn subscribe_book(&self, symbol: &str, precision: &str, length: u32) -> String {
        super::bitfinex_subscribe_book(symbol, precision, length)
    }

    /// Parse a raw WebSocket frame into a unified message.
    /// Uses the shared `fast_parse_ticker` for ticker data.
    pub fn parse_ticker_bytes(&self, data: &[u8]) -> Option<(i64, i64, i64)> {
        super::fast_parse_ticker(data)
    }

    /// Format a batch order (cancel + place) for Bitfinex.
    pub fn format_batch_order(&self, batch: &BatchOrder) -> String {
        let cancel = if let Some(gid) = batch.cancel_gid {
            if let Some(ref sym) = batch.cancel_symbol {
                super::bitfinex_cancel_symbol(sym)
            } else {
                super::bitfinex_cancel_gid(&[gid])
            }
        } else {
            String::new()
        };
        super::bitfinex_ox_multi(&cancel, &batch.orders)
    }

    /// Format a single order for Bitfinex.
    pub fn format_order(&self, order: &OrderRequest) -> String {
        super::bitfinex_order_json(order)
    }

    /// Cancel all orders with given GID.
    pub fn cancel_all_message(&self, gid: u32) -> String {
        format!(r#"[0,"oc_multi",null,{{"gid":[{}]}}]"#, gid)
    }

    /// Cancel all orders (emergency).
    pub fn cancel_everything_message(&self) -> String {
        r#"[0,"oc_multi",null,{"all":1}]"#.to_string()
    }
}

impl Default for Bitfinex {
    fn default() -> Self { Self::new() }
}
