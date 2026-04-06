// ═════════════════════════════════════════════════════════════
// 👻 Ghost Liquidity & Fee Guards
// ═════════════════════════════════════════════════════════════

use std::sync::atomic::Ordering;
use sniper_types::fee_types::{GlobalFeeMatrix, VENUE_BITFINEX};
use sniper_types::EngineState;

/// v11.3 FEE SENTINEL: Profitability Guard
/// Returns true if the spread is wide enough to cover the round-trip fee.
#[inline(always)]
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
