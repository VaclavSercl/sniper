// 📐 Grid L0 Engine — Dynamic Multi-Level Grid Trading
// Framework: SovereignEngine
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use anyhow::Result;
use tracing::info;
use serde_json::json;

use sniper_types::grid_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};
use sniper_types::framework::{SovereignEngine, SovereignRunner};
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::init_mmap;

const VERSION: &str = "1.1.0";

const GRID_ARRAY_CAP: usize = 32;
type GridLevels = arrayvec::ArrayVec<f64, GRID_ARRAY_CAP>;

fn calculate_grid_levels(
    center: f64,
    spacing: f64,
    num_buy: u32,
    num_sell: u32,
    mode: u32,
    geo_pct: f64,
) -> (GridLevels, GridLevels) {
    let mut buys = GridLevels::new();
    let mut sells = GridLevels::new();

    let mut current_buy = center;
    let buy_mult = 1.0 - geo_pct / 100.0;
    for _ in 1..=(num_buy as usize).min(GRID_ARRAY_CAP) {
        let price = if mode == 0 {
            current_buy - spacing
        } else {
            current_buy * buy_mult
        };
        current_buy = price;
        if price > 0.0 { let _ = buys.try_push(price); }
    }

    let mut current_sell = center;
    let sell_mult = 1.0 + geo_pct / 100.0;
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
    
    ryu1: ryu::Buffer,
    ryu2: ryu::Buffer,
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

                let paused = r.global_paused.load(Ordering::Acquire) != 0;
                let grid_inv = e.net_position.load(Ordering::Relaxed);
                l2p.grid_inventory.store(grid_inv, Ordering::Relaxed);
                
                let hedge_active = !sniper_types::l2_command::should_grid_place_bid(l2r);

                if !paused && self.last_grid_calc.elapsed().as_secs() >= 3 {
                    // ═══ HIVE MIND: Cross-Bot Toxic Storm (SIM v2.0) ═══
                    if let Ok(flag) = std::fs::read("/dev/shm/beroun/toxic_storm.bin") {
                        if !flag.is_empty() && flag[0] == 1 { return; }
                    }
                    let spacing = r.grid_spacing.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                    let num_buy = r.num_buy_levels.load(Ordering::Acquire);
                    let num_sell = r.num_sell_levels.load(Ordering::Acquire);
                    let mode = r.grid_mode.load(Ordering::Acquire);
                    let geo_pct = r.geometric_step_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                    let qty = r.order_qty.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                    let center_override = r.center_price_override.load(Ordering::Acquire) as f64 / PRICE_SCALE;

                    let mid_f64 = mid as f64 / PRICE_SCALE_I as f64;
                    let center = if center_override > 0.0 { center_override } else { mid_f64 };

                    if spacing > 0.0 && qty > 0.0 && center > 0.0 {
                        let anchor = l2w.grid_dynamic_anchor.load(Ordering::Relaxed);
                        let (buys, sells) = if anchor > 0 {
                            let mut warp_buys = GridLevels::new();
                            let mut warp_sells = GridLevels::new();
                            for lvl in 1..=30i64 {
                                if !hedge_active {
                                    if let Some(p) = sniper_types::l2_command::calculate_warped_grid_level(l2w, lvl, true) {
                                        if p > 0 { let _ = warp_buys.try_push(p as f64 / PRICE_SCALE_I as f64); }
                                    }
                                }
                                if let Some(p) = sniper_types::l2_command::calculate_warped_grid_level(l2w, lvl, false) {
                                    let _ = warp_sells.try_push(p as f64 / PRICE_SCALE_I as f64);
                                }
                            }
                            // Sort for safety (in-place, no realloc)
                            warp_buys.sort_unstable_by(|a, b| b.partial_cmp(a).unwrap());
                            warp_sells.sort_unstable_by(|a, b| a.partial_cmp(b).unwrap());
                            (warp_buys, warp_sells)
                        } else {
                            calculate_grid_levels(center, spacing, num_buy, num_sell, mode, geo_pct)
                        };

                        let buys_ref = unsafe { &mut (*(self.engine as *mut GridEngineState)).buy_levels };
                        for (i, &price) in buys.iter().enumerate() {
                            if i < GRID_MAX_LEVELS {
                                buys_ref[i].price.store((price * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                                buys_ref[i].quantity.store((qty * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                            }
                        }
                        
                        let e_mut = unsafe { &mut *(self.engine as *mut GridEngineState) };
                        e_mut.active_buy_levels.store(buys.len() as u32, Ordering::Release);

                        let sells_ref = unsafe { &mut (*(self.engine as *mut GridEngineState)).sell_levels };
                        for (i, &price) in sells.iter().enumerate() {
                            if i < GRID_MAX_LEVELS {
                                sells_ref[i].price.store((price * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                                sells_ref[i].quantity.store((qty * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                            }
                        }
                        e_mut.active_sell_levels.store(sells.len() as u32, Ordering::Release);

                        use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
                        let _symbol = b"tBTCUSD";
                        BitfinexVenue::write_batch_open_cancel_sym(out_buf, b"tBTCUSD");

                        for price in &buys {
                            BitfinexVenue::write_limit_order(
                                out_buf, 3000, b"tBTCUSD",
                                self.ryu1.format(qty), self.ryu2.format(*price),
                            );
                        }

                        for price in &sells {
                            BitfinexVenue::write_limit_order(
                                out_buf, 3000, b"tBTCUSD",
                                self.ryu1.format(-qty), self.ryu2.format(*price),
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
        ryu1: ryu::Buffer::new(),
        ryu2: ryu::Buffer::new(),
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignRunner::new(engine, venue, "Grid");
    runner.run().await?;
    
    Ok(())
}
