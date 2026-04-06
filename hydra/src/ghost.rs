// ═════════════════════════════════════════════════════════════
// 👻 GHOST CORE — Syntetická simulace profitu na stinných tradech
// ═════════════════════════════════════════════════════════════

use std::sync::atomic::Ordering;
use sniper_types::fee_types::{GlobalFeeMatrix, VENUE_BITFINEX};
use sniper_types::EngineState;

/// v11.3 FEE SENTINEL: Profitability Guard
#[inline(always)]
pub fn is_spread_profitable(spread_bps: u64, fee_matrix: &GlobalFeeMatrix) -> bool {
    let maker_fee = fee_matrix.venues[VENUE_BITFINEX].maker_fee_bps.load(Ordering::Relaxed); // bps×100
    let taker_fee = fee_matrix.venues[VENUE_BITFINEX].taker_fee_bps.load(Ordering::Relaxed); // bps×100
    
    // Round-trip = maker (our resting order) + taker (fill)
    // Scale: 1000 = 10 bps = 0.10%
    let round_trip_fee_bps = (maker_fee + taker_fee) / 100; // convert to bps
    
    if round_trip_fee_bps > 0 && spread_bps < round_trip_fee_bps * 2 {
        false
    } else {
        true
    }
}

pub struct GhostConfig {
    pub n_public_buy: usize,
    pub n_public_sell: usize,
    pub ghost_mode: bool,
}

/// v10.6 GHOST SPLIT
/// Calculates how many levels should be public vs ghost based on AI transparency.
/// Clears the ghost grid if ghost mode is active.
#[inline]
pub fn calculate_ghost_levels(engine: &EngineState, n_buy: usize, n_sell: usize) -> GhostConfig {
    let ghost_trans = engine.ghost_transparency.load(Ordering::Relaxed) as f64 / 10000.0;
    
    // n_public = how many levels are visible in orderbook
    // At transparency=1.0 (100%): all public. At 0.1: only ~1 level public per side.
    let n_public_buy = ((n_buy as f64 * ghost_trans).ceil() as usize).max(1).min(n_buy.max(1_usize));
    let n_public_sell = ((n_sell as f64 * ghost_trans).ceil() as usize).max(1).min(n_sell.max(1_usize));
    
    let ghost_mode = ghost_trans < 0.99;

    // Clear old ghost prices
    if ghost_mode {
        for gi in 0..sniper_types::MAX_GRID_LEVELS {
            engine.ghost_buy_prices[gi].store(0, Ordering::Relaxed);
            engine.ghost_sell_prices[gi].store(0, Ordering::Relaxed);
        }
        engine.ghost_active_mask.store(0, Ordering::Relaxed);
    }

    GhostConfig {
        n_public_buy,
        n_public_sell,
        ghost_mode,
    }
}
