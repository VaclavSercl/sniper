/// Fixed-point math utilities for Sniper platform.
/// All prices are stored as i64/u64 × PRICE_SCALE (1e8) for atomic operations.

use crate::{PRICE_SCALE, PRICE_SCALE_I};

/// Convert fixed-point i64 to f64 price
#[inline(always)]
pub fn fixed_to_f64(v: i64) -> f64 {
    v as f64 / PRICE_SCALE
}

/// Convert fixed-point u64 to f64 price
#[inline(always)]
pub fn ufixed_to_f64(v: u64) -> f64 {
    v as f64 / PRICE_SCALE
}

/// Convert f64 price to fixed-point i64
#[inline(always)]
pub fn f64_to_fixed(v: f64) -> i64 {
    (v * PRICE_SCALE).round() as i64
}

/// Convert f64 price to fixed-point u64
#[inline(always)]
pub fn f64_to_ufixed(v: f64) -> u64 {
    (v * PRICE_SCALE).round() as u64
}

/// Convert fixed-point to USD string (2 decimals)
#[inline]
pub fn fixed_to_usd(v: i64) -> String {
    format!("${:.2}", v as f64 / PRICE_SCALE)
}

/// Convert basis points (i64 × 100) to f64
#[inline(always)]
pub fn bps_to_f64(bps_raw: i64) -> f64 {
    bps_raw as f64 / 100.0
}

/// Convert fee bps (u64 × 10000) to percentage f64
#[inline(always)]
pub fn fee_bps_to_pct(fee_bps: u64) -> f64 {
    fee_bps as f64 / 10000.0 * 100.0
}

/// Calculate round-trip fee from maker+taker bps
#[inline(always)]
pub fn round_trip_fee_pct(maker_bps: u64, taker_bps: u64) -> f64 {
    (maker_bps + taker_bps) as f64 / 10000.0 * 100.0
}

/// Scale-aware division: a / b where both are fixed-point
#[inline(always)]
pub fn fixed_div(a: i64, b: i64) -> f64 {
    if b == 0 { return 0.0; }
    a as f64 / b as f64
}

/// Clamp value to range
#[inline(always)]
pub fn clamp_f64(val: f64, min: f64, max: f64) -> f64 {
    val.max(min).min(max)
}

/// Fixed-point integer division (avoids PRICE_SCALE_I import noise)
#[inline(always)]
pub fn fixed_i_div(a: i64) -> i64 {
    a / PRICE_SCALE_I
}
