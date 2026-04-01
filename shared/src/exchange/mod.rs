// ═══════════════════════════════════════════════════════════
// 🌐 Exchange Module — Universal Exchange Abstraction
// Sniper Armada · Phase 5.1 · v17.0
//
// Defines the `Exchange` trait and shared utilities.
// Every exchange implementation (Bitfinex, Binance, ...) must
// implement this trait. Bots interact ONLY through the trait.
// ═══════════════════════════════════════════════════════════

pub mod types;
pub mod bitfinex;
pub mod binance;
pub mod cross_types;
pub mod venue;

pub use types::*;

use hmac::{Hmac, Mac};
use sha2::Sha384;

type HmacSha384 = Hmac<Sha384>;

// ═══════════════════════════════════════════════════════════
// Shared Auth Utilities (used by Bitfinex, extractable)
// ═══════════════════════════════════════════════════════════

/// HMAC-SHA384 signature (Bitfinex auth).
/// Extracted from 4 duplicate copies across Hydra/Moonshot/Grid/Trigon.
pub fn hmac_sha384_hex(secret: &str, payload: &str) -> String {
    let mut mac = HmacSha384::new_from_slice(secret.as_bytes())
        .expect("HMAC-SHA384: invalid key length");
    mac.update(payload.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// HMAC-SHA256 signature (Binance auth). Phase 5.3.
#[allow(dead_code)]
pub fn hmac_sha256_hex(secret: &str, payload: &str) -> String {
    use sha2::Sha256;
    type HmacSha256 = Hmac<Sha256>;
    let mut mac = HmacSha256::new_from_slice(secret.as_bytes())
        .expect("HMAC-SHA256: invalid key length");
    mac.update(payload.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

// ═══════════════════════════════════════════════════════════
// Fast Ticker Parser (shared across Moonshot/Grid/Trigon)
// ═══════════════════════════════════════════════════════════

/// Parse Bitfinex ticker array: `[chanId, [BID, BID_SIZE, ASK, ...]]`
/// Returns (channel_id, bid_fixed, ask_fixed) with prices × PRICE_SCALE.
///
/// Extracted from 3 duplicate copies in moonshot/grid/trigon.
/// Hydra uses a different book-based parser (not ticker).
pub fn fast_parse_ticker(data: &[u8]) -> Option<(i64, i64, i64)> {
    if data.len() < 10 || data[0] != b'[' { return None; }
    let mut i = 1;
    // Parse channel ID
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() { return None; }
    let chan_id = std::str::from_utf8(&data[1..i]).ok()?.parse::<i64>().ok()?;
    // Skip heartbeat: ,"hb"
    if i + 2 < data.len() && data[i+1] == b'"' && data[i+2] == b'h' { return None; }
    // Find inner array
    while i < data.len() && data[i] != b'[' { i += 1; }
    if i >= data.len() { return None; }
    i += 1;
    // BID
    let start = i;
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() || start >= i { return None; }
    let bid = (std::str::from_utf8(&data[start..i]).ok()?.parse::<f64>().ok()?
        * crate::PRICE_SCALE_I as f64) as i64;
    i += 1;
    if i >= data.len() { return None; }
    // Skip BID_SIZE
    while i < data.len() && data[i] != b',' { i += 1; }
    i += 1;
    if i >= data.len() { return None; }
    // ASK
    let start = i;
    while i < data.len() && data[i] != b',' && data[i] != b']' { i += 1; }
    if start >= i { return None; }
    let ask = (std::str::from_utf8(&data[start..i]).ok()?.parse::<f64>().ok()?
        * crate::PRICE_SCALE_I as f64) as i64;
    Some((chan_id, bid, ask))
}

// ═══════════════════════════════════════════════════════════
// Bitfinex Auth Message Builder
// ═══════════════════════════════════════════════════════════

/// Build Bitfinex WebSocket auth JSON message.
/// Replaces duplicated auth logic in all 4 bots.
pub fn bitfinex_auth_message(key: &str, secret: &str) -> String {
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .to_string();
    let auth_payload = format!("AUTH{}", nonce);
    let sig = hmac_sha384_hex(secret, &auth_payload);
    format!(
        r#"{{"event":"auth","apiKey":"{}","authSig":"{}","authPayload":"{}","authNonce":"{}","dms":4}}"#,
        key, sig, auth_payload, nonce
    )
}

/// Build Bitfinex ticker subscription message.
pub fn bitfinex_subscribe_ticker(symbol: &str) -> String {
    format!(r#"{{"event":"subscribe","channel":"ticker","symbol":"{}"}}"#, symbol)
}

/// Build Bitfinex book subscription message.
pub fn bitfinex_subscribe_book(symbol: &str, precision: &str, length: u32) -> String {
    format!(
        r#"{{"event":"subscribe","channel":"book","symbol":"{}","prec":"{}","len":"{}"}}"#,
        symbol, precision, length
    )
}

// ═══════════════════════════════════════════════════════════
// Order Formatting (Bitfinex-specific)
// ═══════════════════════════════════════════════════════════

/// Format a Bitfinex cancel-all-by-GID payload.
/// Returns: `["oc_multi",{"gid":[<gids>]}],`
pub fn bitfinex_cancel_gid(gids: &[u32]) -> String {
    let gid_str: Vec<String> = gids.iter().map(|g| g.to_string()).collect();
    format!(r#"["oc_multi",{{"gid":[{}]}}],"#, gid_str.join(","))
}

/// Format a Bitfinex cancel-all for a symbol.
/// Returns: `["oc_multi",{"symbol":"tBTCUSD","all":1}],`
pub fn bitfinex_cancel_symbol(symbol: &str) -> String {
    format!(r#"["oc_multi",{{"symbol":"{}","all":1}}],"#, symbol)
}

/// Format a Bitfinex single order (for ox_multi batch).
/// Returns: `["on",{"gid":2000,"symbol":"tBTCUSD","amount":"0.001","price":"83000","type":"EXCHANGE LIMIT","flags":4096}]`
pub fn bitfinex_order_json(req: &OrderRequest) -> String {
    let type_str = match req.order_type {
        OrderType::Limit => "EXCHANGE LIMIT",
        OrderType::LimitPostOnly => "EXCHANGE LIMIT",
        OrderType::Ioc => "EXCHANGE IOC",
        OrderType::Market => "EXCHANGE MARKET",
    };
    let flags = match req.order_type {
        OrderType::LimitPostOnly => 4096, // post-only
        _ => req.flags.raw_flags,
    };
    let signed_amount = match req.side {
        OrderSide::Buy => req.amount,
        OrderSide::Sell => -req.amount,
    };
    let price_f = req.price as f64 / crate::PRICE_SCALE;
    if flags > 0 {
        format!(
            r#"["on",{{"gid":{},"symbol":"{}","amount":"{:.5}","price":"{:.2}","type":"{}","flags":{}}}]"#,
            req.gid, req.symbol, signed_amount, price_f, type_str, flags
        )
    } else {
        format!(
            r#"["on",{{"gid":{},"symbol":"{}","amount":"{:.5}","price":"{:.2}","type":"{}"}}]"#,
            req.gid, req.symbol, signed_amount, price_f, type_str
        )
    }
}

/// Wrap orders into a Bitfinex `ox_multi` batch message.
/// `cancel_payload` is the optional cancel prefix (from `bitfinex_cancel_gid`).
pub fn bitfinex_ox_multi(cancel_payload: &str, orders: &[OrderRequest]) -> String {
    let order_parts: Vec<String> = orders.iter().map(bitfinex_order_json).collect();
    format!(
        r#"[0,"ox_multi",null,[{}{}]]"#,
        cancel_payload,
        order_parts.join(",")
    )
}
