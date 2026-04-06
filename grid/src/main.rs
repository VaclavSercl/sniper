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
use sniper_types::framework::{SovereignEngine, SovereignRunner};
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

fn calculate_grid_levels(
    center: FixedPrice,
    spacing: FixedPrice,
    num_buy: u32,
    num_sell: u32,
    mode: u32,
    geo_pct: FixedPrice,
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
    let sell_mult = scale_fp + (geo_pct / f_100);
    for _ in 1..=(num_sell as usize).min(GRID_ARRAY_CAP) {
        let price = if mode == 0 {
            current_sell + spacing
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
    armada_state: &'static sniper_types::armada_types::ArmadaState,
}

unsafe impl Send for GridEngine {}
unsafe impl Sync for GridEngine {}

impl SovereignEngine for GridEngine {
    fn subscriptions(&mut self) -> Vec<String> {
        vec![json!({"event": "subscribe", "channel": "ticker", "symbol": "tBTCUSD"}).to_string()]
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
        risk.global_paused.load(Ordering::Acquire) != 0
    }

    fn best_bid_ask(&self) -> (f64, f64) {
        let engine = unsafe { &*self.engine };
        (
            engine.best_bid.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64,
            engine.best_ask.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64
        )
    }

    fn on_market_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut) {
        let loop_start = Instant::now();
        if let Some((chan, bid, ask)) = sniper_types::exchange::fast_parse_ticker(payload) {
            if Some(chan) == self.ticker_chan {
                let e = unsafe { &*self.engine };
                let r = unsafe { &*self.risk };
                let l2p = unsafe { &*self.l2_portfolio };
                let l2r = unsafe { &*self.l2_risk };
                let l2w = unsafe { &*self.l2_warp };

                let mid = (bid + ask) / 2;
                e.best_bid.store(bid as u64, Ordering::Release);
                e.best_ask.store(ask as u64, Ordering::Release);
                e.mid_price.store(mid as u64, Ordering::Release);
                e.latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);

                let grid_inv = e.net_position.load(Ordering::Relaxed);
                l2p.grid_inventory.store(grid_inv, Ordering::Relaxed);
                
                let hedge_active = !sniper_types::l2_command::should_grid_place_bid(l2r);

                if self.last_grid_calc.elapsed().as_secs() >= 3 {
                    // ═══ HIVE MIND: Cross-Bot Toxic Storm ═══
                    let storm_byte = unsafe { std::ptr::read_volatile(self.toxic_storm_ptr) };
                    if storm_byte == 1 { return; }
                    
                    // ARMADA KŘEMÍKOVÁ ZEĎ 🛡️
                    if self.armada_state.is_kill_switch_active() {
                        return;
                    }
                    
                    let spacing = FixedPrice::new(r.grid_spacing.load(Ordering::Acquire) as i64);
                    let num_buy = r.num_buy_levels.load(Ordering::Acquire);
                    let num_sell = r.num_sell_levels.load(Ordering::Acquire);
                    let mode = r.grid_mode.load(Ordering::Acquire);
                    let geo_pct = FixedPrice::new(r.geometric_step_pct.load(Ordering::Acquire) as i64);
                    
                    let mut qty = FixedPrice::new(r.order_qty.load(Ordering::Acquire) as i64);
                    let center_override_fp = FixedPrice::new(r.center_price_override.load(Ordering::Acquire) as i64);

                    let mid_fp = FixedPrice::new(mid as i64);
                    let center = if center_override_fp.0 > 0 { center_override_fp } else { mid_fp };
                    
                    // Graceful shrink through Armada Limit
                    let armada_cap = self.armada_state.authorized_capital[2].load(Ordering::Acquire) as i64;
                    let total_orders = (num_buy + num_sell) as i64;
                    if total_orders > 0 {
                        let max_total_qty = FixedPrice::new(armada_cap) / mid_fp;
                        let max_qty_per_order = max_total_qty / FixedPrice::new(total_orders * sniper_types::PRICE_SCALE_I as i64);
                        if qty > max_qty_per_order {
                            qty = max_qty_per_order;
                        }
                    }

                    if spacing.0 > 0 && qty.0 >= 15000 && center.0 > 0 {
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
                            calculate_grid_levels(center, spacing, num_buy, num_sell, mode, geo_pct)
                        };

                        let buys_ref = unsafe { &mut (*(self.engine as *mut GridEngineState)).buy_levels };
                        for (i, &price) in buys.iter().enumerate() {
                            if i < GRID_MAX_LEVELS {
                                buys_ref[i].price.store(price.0 as u64, Ordering::Release);
                                buys_ref[i].quantity.store(qty.0 as u64, Ordering::Release);
                            }
                        }
                        
                        let e_mut = unsafe { &mut *(self.engine as *mut GridEngineState) };
                        e_mut.active_buy_levels.store(buys.len() as u32, Ordering::Release);

                        let sells_ref = unsafe { &mut (*(self.engine as *mut GridEngineState)).sell_levels };
                        for (i, &price) in sells.iter().enumerate() {
                            if i < GRID_MAX_LEVELS {
                                sells_ref[i].price.store(price.0 as u64, Ordering::Release);
                                sells_ref[i].quantity.store(qty.0 as u64, Ordering::Release);
                            }
                        }
                        e_mut.active_sell_levels.store(sells.len() as u32, Ordering::Release);

                        use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
                        let symbol = b"tBTCUSD";
                        BitfinexVenue::write_batch_open_cancel_sym(out_buf, symbol);

                        for price in &buys {
                            let q_fmt = FixedFormat::new(qty.0);
                            let p_fmt = FixedFormat::new(price.0);
                            BitfinexVenue::write_limit_order(
                                out_buf, 3000, symbol,
                                q_fmt.as_str(), p_fmt.as_str(),
                            );
                        }

                        for price in &sells {
                            let q_fmt = FixedFormat::new(-qty.0);
                            let p_fmt = FixedFormat::new(price.0);
                            BitfinexVenue::write_limit_order(
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

    let storm_path = "/dev/shm/beroun/toxic_storm.bin";
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
        armada_state: sniper_types::armada_types::load_armada_state_ro(),
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignRunner::new(engine, venue, "Grid");
    runner.run().await?;
    
    Ok(())
}
