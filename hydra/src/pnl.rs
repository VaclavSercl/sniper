// ═════════════════════════════════════════════════════════════
// 📈 Trade Tracking & PnL Calculations
// ═════════════════════════════════════════════════════════════

use std::sync::atomic::Ordering;
use sniper_types::EngineState;
use tracing::info;

pub fn process_trade(engine: &EngineState, trade_amt: f64, trade_price: f64) {
    let scale = sniper_types::PRICE_SCALE;
    let old_pos_i = engine.net_position.load(Ordering::SeqCst);
    let old_pos = old_pos_i as f64 / scale;
    let old_aep_i = engine.average_entry_price.load(Ordering::SeqCst);
    let old_aep = old_aep_i as f64 / scale;

    // 1. REALIZED PnL (trade reduces/closes position)
    if (old_pos > 0.0 && trade_amt < 0.0) || (old_pos < 0.0 && trade_amt > 0.0) {
        let closed_amt = trade_amt.abs().min(old_pos.abs());
        let pnl_gain = if old_pos > 0.0 {
            (trade_price - old_aep) * closed_amt
        } else {
            (old_aep - trade_price) * closed_amt
        };
        engine.realized_pnl.fetch_add((pnl_gain * scale).round() as i64, Ordering::SeqCst);
        info!(event = "pnl_realized", gain = pnl_gain, closed = closed_amt, aep = old_aep);
    }

    // 2. AVERAGE ENTRY PRICE (WAP)
    let new_pos = old_pos + trade_amt;
    if new_pos.abs() > 1e-8 {
        if (old_pos >= 0.0 && trade_amt > 0.0) || (old_pos <= 0.0 && trade_amt < 0.0) {
            // Enlarging position → weighted average
            let new_aep = (old_pos.abs() * old_aep + trade_amt.abs() * trade_price) / new_pos.abs();
            engine.average_entry_price.store((new_aep * scale).round() as i64, Ordering::SeqCst);
        } else if (old_pos > 0.0 && new_pos < 0.0) || (old_pos < 0.0 && new_pos > 0.0) {
            // Position flipped → AEP = trade price
            engine.average_entry_price.store((trade_price * scale).round() as i64, Ordering::SeqCst);
        }
        // Partial close: AEP stays the same (no update needed)
    } else {
        // Position == 0 → reset AEP
        engine.average_entry_price.store(0, Ordering::SeqCst);
    }

    // 3. Update net_position
    engine.net_position.store((new_pos * scale).round() as i64, Ordering::SeqCst);

    // 4. ALPHA TRACKING: measure AI contribution
    let ai_bias_now = engine.current_ai_bias.load(Ordering::Acquire) as f64 / scale;
    if ai_bias_now.abs() > 0.01 {
        // For buys: positive bias = bought higher = negative alpha
        // For sells: positive bias = sold higher = positive alpha
        let alpha = ai_bias_now * trade_amt.abs() * trade_amt.signum();
        engine.ai_alpha_usd.fetch_add((alpha * scale).round() as i64, Ordering::SeqCst);
    }

    info!(event = "trade_executed", amount = trade_amt, price = trade_price,
          new_pos = new_pos, aep = engine.average_entry_price.load(Ordering::SeqCst) as f64 / scale);
    
    // v9.5: Fill-rate tracking
    if trade_amt > 0.0 {
        engine.buy_fill_count.fetch_add(1, Ordering::Relaxed);
    } else {
        engine.sell_fill_count.fetch_add(1, Ordering::Relaxed);
    }
    
    // v10.0: Monthly volume for fee tier
    let trade_vol_usd = (trade_amt.abs() * trade_price * scale) as u64;
    engine.monthly_volume_usd.fetch_add(trade_vol_usd, Ordering::Relaxed);

    // v9.2: HYBRID INTELLIGENCE — TradeAnalytics
    engine.session_fill_count.fetch_add(1, Ordering::Relaxed);
    if trade_amt > 0.0 {
        engine.session_buy_volume.fetch_add((trade_amt * scale) as u64, Ordering::Relaxed);
        engine.session_buy_usd.fetch_add(trade_vol_usd, Ordering::Relaxed);
    } else {
        engine.session_sell_volume.fetch_add((trade_amt.abs() * scale) as u64, Ordering::Relaxed);
        engine.session_sell_usd.fetch_add(trade_vol_usd, Ordering::Relaxed);
    }
}
