// 📐 Grid L0 Engine — Dynamic Multi-Level Grid Trading
// Framework: SovereignEngine v12/2026 (Zero-f64 / Zero-Allocation Refactor)
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use anyhow::Result;
use tracing::info;
use serde_json::json;

use sniper_types::grid_types::*;
use sniper_types::PRICE_SCALE_I;
use sniper_types::framework::{SovereignEngine, SovereignDualRunner};
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::init_mmap;
use sniper_types::math::FixedPrice;

const VERSION: &str = "12.2.0-grid-zerofpu";
const GRID_ARRAY_CAP: usize = 32;
type GridLevels = arrayvec::ArrayVec<FixedPrice, GRID_ARRAY_CAP>;

// BEZ ALOKACÍ: Extrémně rychlý formátovač FixedPrice na string/buff na stacku 
struct FixedFormat {
    buf: [u8; 32],
    len: usize,
}

impl FixedFormat {
    #[inline(always)]
    fn new(mut val: i64) -> Self {
        let mut s = Self { buf: [0; 32], len: 0 };
        if val == 0 {
            s.buf[0] = b'0';
            s.len = 1;
            return s;
        }
        if val < 0 {
            s.buf[0] = b'-';
            s.len = 1;
            val = -val;
        }
        let int_part = val / PRICE_SCALE_I;
        let mut frac_part = val % PRICE_SCALE_I;
        
        let mut itoa_buf = itoa::Buffer::new();
        let int_str = itoa_buf.format(int_part).as_bytes();
        s.buf[s.len..s.len + int_str.len()].copy_from_slice(int_str);
        s.len += int_str.len();

        if frac_part > 0 {
            s.buf[s.len] = b'.';
            s.len += 1;
            let mut f_buf = [b'0'; 8];
            let mut temp = frac_part as u64;
            let mut idx = 7;
            while temp > 0 {
                f_buf[idx] = b'0' + (temp % 10) as u8;
                temp /= 10;
                if idx > 0 { idx -= 1; } else { break; }
            }
            let mut end = 8;
            while end > 0 && f_buf[end-1] == b'0' { end -= 1; }
            s.buf[s.len..s.len + end].copy_from_slice(&f_buf[..end]);
            s.len += end;
        }
        s
    }

    #[inline(always)]
    fn as_str(&self) -> &str {
        unsafe { std::str::from_utf8_unchecked(&self.buf[..self.len]) }
    }
}

use simd_json::prelude::*;
use simd_json::BorrowedValue;

#[inline(always)]
fn extract_u64_scaled(v: &BorrowedValue) -> Option<u64> {
    if let Some(i) = v.as_i64() {
        if i >= 0 { return Some((i * PRICE_SCALE_I) as u64); }
        None
    } else if let Some(f) = v.as_f64() {
        Some((f * sniper_types::PRICE_SCALE as f64).round() as u64)
    } else if let Some(s) = v.as_str() {
        s.parse::<f64>().ok().map(|f| (f * sniper_types::PRICE_SCALE as f64).round() as u64)
    } else {
        None
    }
}

#[inline(always)]
fn store_order_slot(ids: &[std::sync::atomic::AtomicU64; GRID_MAX_LEVELS], id: u64) {
    for slot in ids {
        if slot.compare_exchange(0, id, Ordering::Relaxed, Ordering::Relaxed).is_ok() {
            return;
        }
    }
}

#[inline(always)]
fn clear_order_slot(ids: &[std::sync::atomic::AtomicU64; GRID_MAX_LEVELS], id: u64) {
    for slot in ids {
        let _ = slot.compare_exchange(id, 0, Ordering::Relaxed, Ordering::Relaxed);
    }
}

fn calculate_grid_levels(
    center: FixedPrice,
    spacing: FixedPrice,
    num_buy: u32,
    num_sell: u32,
    mode: u32,
    geo_pct: FixedPrice,
    grid_inv: i64,
) -> (GridLevels, GridLevels) {
    let mut buys = GridLevels::new();
    let mut sells = GridLevels::new();

    let mut current_buy = center;
    let f_100 = FixedPrice::new(100 * PRICE_SCALE_I);
    let scale_fp = FixedPrice::new(PRICE_SCALE_I);
    let buy_mult = scale_fp - (geo_pct / f_100);
    
    for _ in 1..=(num_buy as usize).min(GRID_ARRAY_CAP) {
        let price = if mode == 0 {
            current_buy - spacing
        } else {
            current_buy * buy_mult
        };
        current_buy = price;
        if price.0 > 0 { let _ = buys.try_push(price); }
    }

    let mut current_sell = center;
    
    // ZLATÉ PRAVIDLO: Inventory Skew Penalty (Pasivní akumulace)
    // Pokud držíme BTC, stavíme prodejní limity blíže k trhu pro rychlejší exit v zisku.
    let inventory_btc = grid_inv as f64 / sniper_types::PRICE_SCALE as f64;
    let inventory_penalty_factor = if inventory_btc > 0.0 {
        // Redukce sell spacingu úměrně k drženému BTC (až o 80 %)
        let penalty = 1.0 - inventory_btc.min(0.8);
        penalty.max(0.2)
    } else {
        1.0
    };

    let sell_spacing = FixedPrice::new((spacing.0 as f64 * inventory_penalty_factor) as i64);
    let sell_geo_pct = FixedPrice::new((geo_pct.0 as f64 * inventory_penalty_factor) as i64);
    let sell_mult = scale_fp + (sell_geo_pct / f_100);
    
    for _ in 1..=(num_sell as usize).min(GRID_ARRAY_CAP) {
        let price = if mode == 0 {
            current_sell + sell_spacing
        } else {
            current_sell * sell_mult
        };
        current_sell = price;
        let _ = sells.try_push(price);
    }

    (buys, sells)
}

struct GridEngine {
    notifier: Arc<AsyncNotifier>,
    engine: *const GridEngineState,
    risk: *const GridRiskState,
    l2_warp: *const sniper_types::l2_command::L2GridWarpMatrix,
    l2_risk: *const sniper_types::l2_command::L2GlobalRiskMatrix,
    l2_portfolio: *const sniper_types::l2_command::L2PortfolioTelemetry,
    
    ticker_chan: Option<i64>,
    last_grid_calc: Instant,
    toxic_storm_ptr: *const u8,
    armada_v2: &'static sniper_types::armada_types::ArmadaStateV2,
    last_anchor_price: i64,
    last_trending_score: i64,
}

unsafe impl Send for GridEngine {}
unsafe impl Sync for GridEngine {}

impl SovereignEngine for GridEngine {
    fn subscriptions(&mut self) -> Vec<String> {
        vec![
            json!({"event":"conf","flags":131072|536870912}).to_string(),
            json!({"event": "subscribe", "channel": "ticker", "symbol": "tBTCUSD"}).to_string()
        ]
    }

    fn on_start(&mut self) -> Result<()> {
        let _lock_guard = sniper_types::lock::ensure_single_instance("grid-core")?;
        self.notifier.send(format!("📐 Grid v{} ONLINE", VERSION));
        Ok(())
    }

    fn on_auth(&mut self) {
        info!(event = "authenticated", bot = "grid");
    }

    fn is_shadow(&self) -> bool {
        let risk = unsafe { &*self.risk };
        let is_paused = risk.global_paused.load(Ordering::Acquire) != 0;
        let config_shadow = std::fs::read_to_string("config.yaml").unwrap_or_default().contains("is_shadow: true");
        let v2_kill = self.armada_v2.global.global_kill_switch.load(Ordering::Acquire) == 1;
        is_paused || config_shadow || v2_kill
    }

    fn best_bid_ask(&self) -> (f64, f64) {
        let engine = unsafe { &*self.engine };
        (
            engine.best_bid.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64,
            engine.best_ask.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64
        )
    }

    fn add_virtual_pnl(&self, amount: i64) {
        let eng = unsafe { &*self.engine };
        eng.virtual_realized_pnl.fetch_add(amount, Ordering::Relaxed);
    }

    fn on_market_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut) {
        let loop_start = Instant::now();
        let e = unsafe { &*self.engine };
        let r = unsafe { &*self.risk };
        let l2p = unsafe { &*self.l2_portfolio };
        let l2r = unsafe { &*self.l2_risk };
        let l2w = unsafe { &*self.l2_warp };

        // Parse JSON for auth channel execution stream (wallets, orders)
        if payload.len() > 2 && payload[0] == b'[' {
            // Keep a clone for JSON so we don't mess up fast_parse_ticker if it fails
            let mut pl_clone = payload.to_vec();
            if let Ok(v) = simd_json::to_borrowed_value(&mut pl_clone) {
                if let Some(arr) = v.as_array() {
                    if let Some(0) = arr[0].as_i64() {
                    if arr.len() > 1 {
                        let mt = arr[1].as_str().unwrap_or("");
                        if mt == "wu" || mt == "ws" {
                            let iter: Box<dyn Iterator<Item = &BorrowedValue>> = if mt == "wu" {
                                Box::new(std::iter::once(&arr[2]))
                            } else {
                                if let Some(a) = arr[2].as_array() {
                                    Box::new(a.iter())
                                } else {
                                    Box::new(std::iter::empty())
                                }
                            };
                            for w in iter {
                                if let Some(w_arr) = w.as_array() {
                                    if let (Some(wt), Some(cur), Some(bal)) = (w_arr.get(0).and_then(|x| x.as_str()), w_arr.get(1).and_then(|x| x.as_str()), w_arr.get(2).and_then(|x| extract_u64_scaled(x))) {
                                        if wt == "exchange" {
                                            if cur == sniper_types::TRADING_BASE { e.wallet_btc.store(bal, Ordering::SeqCst); }
                                            else if cur == sniper_types::TRADING_QUOTE || cur == "UST" { e.wallet_usd.store(bal, Ordering::SeqCst); }
                                        }
                                    }
                                }
                            }
                        } else if mt == "os" || mt == "on" || mt == "ou" || mt == "oc" {
                            let e_mut = unsafe { &mut *(self.engine as *const GridEngineState as *mut GridEngineState) };
                            if mt == "os" {
                                if let Some(orders) = arr.get(2).and_then(|a| a.as_array()) {
                                    for order in orders {
                                        if let Some(o) = order.as_array()
                                            && let (Some(id), Some(sym), Some(amt_val), Some(status)) = (
                                                o.get(0).and_then(|v| v.as_u64().or_else(|| v.as_f64().map(|f| f as u64))),
                                                o.get(3).and_then(|v| v.as_str()), 
                                                o.get(6).and_then(|v| v.as_f64()),
                                                o.get(13).and_then(|v| v.as_str())
                                            )
                                            && sym == "tBTCUSD" {
                                                if status.contains("ACTIVE") || status.contains("PARTIALLY") {
                                                    if amt_val > 0.0 { store_order_slot(&e_mut.active_buy_ids, id); }
                                                    else { store_order_slot(&e_mut.active_sell_ids, id); }
                                                }
                                            }
                                    }
                                }
                            } else {
                                if let Some(o) = arr.get(2).and_then(|a| a.as_array())
                                    && let (Some(id), Some(sym), Some(amt_val), Some(status)) = (
                                        o.get(0).and_then(|v| v.as_u64().or_else(|| v.as_f64().map(|f| f as u64))),
                                        o.get(3).and_then(|v| v.as_str()), 
                                        o.get(6).and_then(|v| v.as_f64()),
                                        o.get(13).and_then(|v| v.as_str())
                                    )
                                    && sym == "tBTCUSD" {
                                        if status.contains("ACTIVE") || status.contains("PARTIALLY") {
                                            if amt_val > 0.0 { store_order_slot(&e_mut.active_buy_ids, id); }
                                            else { store_order_slot(&e_mut.active_sell_ids, id); }
                                        } else if status.contains("CANCELED") || status.contains("EXECUTED") {
                                            if amt_val > 0.0 { clear_order_slot(&e_mut.active_buy_ids, id); }
                                            else { clear_order_slot(&e_mut.active_sell_ids, id); }
                                        }
                                    }
                            }
                        }
                    }
                }
            }
        }
    }

        if let Some((chan, bid, ask)) = sniper_types::exchange::fast_parse_ticker(payload) {
            if Some(chan) == self.ticker_chan {
                let mid = (bid + ask) / 2;
                e.best_bid.store(bid as u64, Ordering::Release);
                e.best_ask.store(ask as u64, Ordering::Release);
                e.mid_price.store(mid as u64, Ordering::Release);
                e.latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);

                let grid_inv = e.net_position.load(Ordering::Relaxed);
                l2p.grid_inventory.store(grid_inv, Ordering::Relaxed);
                
                let hedge_active = !sniper_types::l2_command::should_grid_place_bid(l2r);

                let trending_score_raw = l2r.trending_score.load(Ordering::Relaxed) as i64;
                let base_spacing_raw = r.grid_spacing.load(Ordering::Acquire) as i64;
                let spacing_fp = FixedPrice::new(base_spacing_raw);
                
                // ZÁSAH 1: Skewed Delta-Trigger
                let delta_price = (mid as i64 - self.last_anchor_price).abs();
                let spacing_threshold = (spacing_fp.0 * 35) / 100; // 35% posun
                let trend_delta = (trending_score_raw - self.last_trending_score).abs();
                let time_elapsed = self.last_grid_calc.elapsed().as_secs();

                // Přepočítáme mřížku jen pokud se cena pohnula o 35% rozteče, 
                // změnila se drasticky toxicita, nebo uběhlo 60 vteřin (Fallback)
                if delta_price > spacing_threshold || trend_delta > 200_000 || time_elapsed > 60 {
                    let mut new_anchor_price = mid as i64;
                    // One-Way Anchor Freeze (Zlaté pravidlo Bitcoinu): Nezlevňujeme při poklesu s plnou taškou
                    let c_idx = sniper_types::armada_types::capital_index(2, 0); // Grid = 2
                    let armada_cap = self.armada_v2.capital.authorized_capital[c_idx].load(Ordering::Acquire) as i64;
                    let inventory_usd = (grid_inv as f64 / sniper_types::PRICE_SCALE as f64) * mid as f64;
                    let cap_pct = if armada_cap > 0 { inventory_usd / armada_cap as f64 } else { 0.0 };
                    if cap_pct > 0.5 && new_anchor_price < self.last_anchor_price {
                        new_anchor_price = self.last_anchor_price; // Zastaví kráčení dolů
                    }
                    self.last_anchor_price = new_anchor_price;
                    self.last_trending_score = trending_score_raw;
                    
                    // ═══ HIVE MIND: Cross-Bot Toxic Storm ═══
                    let storm_byte = unsafe { std::ptr::read_volatile(self.toxic_storm_ptr) };
                    if storm_byte == 1 { return; }
                    
                    // ARMADA KŘEMÍKOVÁ ZEĎ V2 🛡️
                    if self.armada_v2.global.global_kill_switch.load(Ordering::Acquire) == 1 {
                        return;
                    }
                    
                    let trending_score = trending_score_raw as f64 / 1e8;
                    let expansion_multiplier = 1.0 + (trending_score.max(0.0) * 2.0);
                    let spacing = FixedPrice::new((base_spacing_raw as f64 * expansion_multiplier) as i64);
                    let num_buy = r.num_buy_levels.load(Ordering::Acquire);
                    let num_sell = r.num_sell_levels.load(Ordering::Acquire);
                    let mode = r.grid_mode.load(Ordering::Acquire);
                    let geo_pct = FixedPrice::new(r.geometric_step_pct.load(Ordering::Acquire) as i64);
                    
                    let base_qty = FixedPrice::new(r.order_qty.load(Ordering::Acquire) as i64);
                    let center_override_fp = FixedPrice::new(r.center_price_override.load(Ordering::Acquire) as i64);

                    let mid_fp = FixedPrice::new(mid as i64);
                    let center = if center_override_fp.0 > 0 { center_override_fp } else { FixedPrice::new(new_anchor_price) };
                    
                    // ZÁSAH 2: Zero-Bound Toxic Shrinking (Zlaté pravidlo Bitcoinu)
                    let mut buy_qty = base_qty;
                    let mut sell_qty = base_qty;
                    
                    if trending_score_raw < -500_000 {
                        // Silná toxicita dolů: Osekáme nákupy na 30 % (nechytáme plnou kudlu)
                        // Prodáváme rychleji to, co jsme nabrali (150 % objemu)
                        buy_qty = FixedPrice::new((buy_qty.0 as i128 * 30_000_000 / sniper_types::PRICE_SCALE_I as i128) as i64);
                        sell_qty = FixedPrice::new((sell_qty.0 as i128 * 150_000_000 / sniper_types::PRICE_SCALE_I as i128) as i64);
                    } else if trending_score_raw > 500_000 {
                        // Trend nahoru: Zadržujeme prodeje (zlato roste), kupujeme agresivněji
                        sell_qty = FixedPrice::new((sell_qty.0 as i128 * 30_000_000 / sniper_types::PRICE_SCALE_I as i128) as i64);
                        buy_qty = FixedPrice::new((buy_qty.0 as i128 * 150_000_000 / sniper_types::PRICE_SCALE_I as i128) as i64);
                    }

                    // Graceful shrink through Armada Limit
                    let total_orders = (num_buy + num_sell) as i64;
                    if total_orders > 0 {
                        let max_total_qty = FixedPrice::new(armada_cap) / mid_fp;
                        let max_qty_per_order = max_total_qty / FixedPrice::new(total_orders * sniper_types::PRICE_SCALE_I as i64);
                        if buy_qty > max_qty_per_order { buy_qty = max_qty_per_order; }
                        if sell_qty > max_qty_per_order { sell_qty = max_qty_per_order; }
                    }

                    if spacing.0 > 0 && buy_qty.0 >= 15000 && center.0 > 0 {
                        let anchor = l2w.grid_dynamic_anchor.load(Ordering::Relaxed);
                        let (mut buys, mut sells) = if anchor > 0 {
                            let mut warp_buys = GridLevels::new();
                            let mut warp_sells = GridLevels::new();
                            for lvl in 1..=30i64 {
                                if !hedge_active {
                                    if let Some(p) = sniper_types::l2_command::calculate_warped_grid_level(l2w, lvl, true) {
                                        if p > 0 { let _ = warp_buys.try_push(FixedPrice::new(p as i64)); }
                                    }
                                }
                                if let Some(p) = sniper_types::l2_command::calculate_warped_grid_level(l2w, lvl, false) {
                                    let _ = warp_sells.try_push(FixedPrice::new(p as i64));
                                }
                            }
                            warp_buys.sort_unstable_by(|a, b| b.cmp(a));
                            warp_sells.sort_unstable_by(|a, b| a.cmp(b));
                            (warp_buys, warp_sells)
                        } else {
                            calculate_grid_levels(center, spacing, num_buy, num_sell, mode, geo_pct, grid_inv)
                        };

                        let buys_ref = unsafe { &mut (*(self.engine as *mut GridEngineState)).buy_levels };
                        for (i, &price) in buys.iter().enumerate() {
                            if i < GRID_MAX_LEVELS {
                                buys_ref[i].price.store(price.0 as u64, Ordering::Release);
                                buys_ref[i].quantity.store(buy_qty.0 as u64, Ordering::Release);
                            }
                        }
                        
                        let e_mut = unsafe { &mut *(self.engine as *mut GridEngineState) };
                        e_mut.active_buy_levels.store(buys.len() as u32, Ordering::Release);

                        let sells_ref = unsafe { &mut (*(self.engine as *mut GridEngineState)).sell_levels };
                        for (i, &price) in sells.iter().enumerate() {
                            if i < GRID_MAX_LEVELS {
                                sells_ref[i].price.store(price.0 as u64, Ordering::Release);
                                sells_ref[i].quantity.store(sell_qty.0 as u64, Ordering::Release);
                            }
                        }
                        e_mut.active_sell_levels.store(sells.len() as u32, Ordering::Release);

                        use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
                        let symbol = b"tBTCUSD";
                        BitfinexVenue::write_batch_open_cancel_gid(out_buf, sniper_types::BOT_GID_GRID);

                        let mut remaining_usd = (e.wallet_usd.load(Ordering::Relaxed) as i64 * 98) / 100;
                        for price in &buys {
                            let ask_fp = FixedPrice::new(e.best_ask.load(Ordering::Relaxed) as i64);
                            let safe_price = if ask_fp.0 > 0 && price.0 >= ask_fp.0 { ask_fp.0 - 10_000 } else { price.0 };
                            let p_fp = FixedPrice::new(safe_price);
                            
                            let mut final_qty = buy_qty.0;
                            let usd_cost = p_fp * FixedPrice::new(final_qty);
                            
                            let min_usd_allowed = (FixedPrice::new(15000) * p_fp).0;
                            if remaining_usd < min_usd_allowed { break; } 
                            
                            if usd_cost.0 > remaining_usd {
                                final_qty = (FixedPrice::new(remaining_usd) / p_fp).0;
                            }
                            if final_qty < 15000 { break; }
                            
                            remaining_usd -= (p_fp * FixedPrice::new(final_qty)).0;

                            let q_fmt = FixedFormat::new(final_qty);
                            let p_fmt = FixedFormat::new(safe_price);
                            // POST-ONLY GARANCE FLAG (4096)
                            // POST-ONLY GARANCE FLAG (4096)
                            BitfinexVenue::write_limit_postonly_order(
                                out_buf, 3000, symbol,
                                q_fmt.as_str(), p_fmt.as_str(),
                            );
                        }

                        let cold_vault_btc = self.armada_v2.global.cold_vault_btc.load(Ordering::Acquire);
                        let w_btc_raw = e.wallet_btc.load(Ordering::Relaxed) as i64;
                        let mut remaining_btc = ((w_btc_raw - cold_vault_btc).max(0) * 98) / 100;
                        let min_profit = 10_000; // 1 tick
                        for price in &sells {
                            let bid_fp = FixedPrice::new(e.best_bid.load(Ordering::Relaxed) as i64);
                            let mut safe_price = if bid_fp.0 > 0 && price.0 <= bid_fp.0 { bid_fp.0 + 10_000 } else { price.0 };

                            // 1. Zlatá podlaha (Breakeven Floor pro Sell příkazy)
                            let vwap = e.vwap.load(Ordering::Relaxed) as i64;
                            if vwap > 0 {
                                let min_allowed_sell_price = vwap + min_profit;
                                if safe_price < min_allowed_sell_price {
                                    safe_price = min_allowed_sell_price;
                                }
                            }

                            let mut final_qty = sell_qty.0;
                            if remaining_btc < 15000 { break; } 
                            
                            if final_qty > remaining_btc {
                                final_qty = remaining_btc;
                            }
                            if final_qty < 15000 { break; }
                            
                            remaining_btc -= final_qty;

                            let q_fmt = FixedFormat::new(-final_qty);
                            let p_fmt = FixedFormat::new(safe_price);
                            // POST-ONLY GARANCE FLAG (4096)
                            // POST-ONLY GARANCE FLAG (4096)
                            BitfinexVenue::write_limit_postonly_order(
                                out_buf, 3000, symbol,
                                q_fmt.as_str(), p_fmt.as_str(),
                            );
                        }

                        BitfinexVenue::write_batch_close(out_buf);
                        self.last_grid_calc = Instant::now();
                    }
                }
            }
        }
    }

    fn on_system_event(&mut self, value: &serde_json::Value, _out_buf: &mut bytes::BytesMut) {
        if value["event"] == "subscribed" && value["channel"] == "ticker" {
            if let Some(cid) = value["chanId"].as_i64() {
                self.ticker_chan = Some(cid);
                info!(event = "ticker_subscribed", chan_id = cid);
            }
        }
    }

    fn on_loop(&mut self, _out_buf: &mut bytes::BytesMut) {
        let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        unsafe { &*self.engine }.heartbeat_ms.store(now_ms, Ordering::Release);
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();
    
    let engine_mmap = init_mmap::<GridEngineState>(GRID_ENGINE_PATH)?;
    let risk_mmap = init_mmap::<GridRiskState>(GRID_RISK_PATH)?;
    let l2_mmap = init_mmap::<sniper_types::l2_command::L2SharedState>(
        sniper_types::l2_command::L2_COMMAND_PATH,
    )?;

    let storm_path = "/dev/shm/sniper/toxic_storm.bin";
    if !std::path::Path::new(storm_path).exists() { let _ = std::fs::write(storm_path, [0u8]); }
    let toxic_storm_mmap = sniper_types::mmap_utils::open_mmap_readonly(storm_path).unwrap();

    let engine_ptr = engine_mmap.as_ptr() as *const GridEngineState;
    let risk_ptr = risk_mmap.as_ptr() as *const GridRiskState;
    let l2_shared = unsafe { &*(l2_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };

    let engine = GridEngine {
        notifier: Arc::new(AsyncNotifier::new("grid", "📐")),
        engine: engine_ptr,
        risk: risk_ptr,
        l2_warp: &l2_shared.grid_warp,
        l2_risk: &l2_shared.global_risk,
        l2_portfolio: &l2_shared.portfolio,
        ticker_chan: None,
        last_grid_calc: Instant::now() - core::time::Duration::from_secs(10), // force init run
        toxic_storm_ptr: toxic_storm_mmap.as_ptr(),
        armada_v2: sniper_types::armada_types::load_armada_state_v2_ro(),
        last_anchor_price: 0,
        last_trending_score: 0,
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignDualRunner::new(engine, venue, "Grid");
    runner.run().await?;
    
    Ok(())
}
