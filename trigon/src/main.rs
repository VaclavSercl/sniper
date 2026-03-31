// 🔺 Trigon L0 Engine — Triangular Arbitrage Scanner
// Sniper Armada · Bot #4 · v1.0.0
//
// Monitors N triangles (A→B→C→A) for cross-pair price inefficiencies.
// When implied_rate > 1 + 3×fee → execute all 3 legs atomically.
//
// Architecture:
//   - L0 (this): Rust async, WebSocket, real-time triangle scanning
//   - L1: Python l1_shield.py (liquidity filter, execution risk)
//   - L2: Python sniper_orchestrator.py (triangle selection, fee optimization)
//
// mmap IPC:
//   - /dev/shm/beroun/trigon_engine.bin (written by L0, read by dashboard/brain)
//   - /dev/shm/beroun/trigon_risk.bin (written by L2, read by L0)

use std::time::{SystemTime, UNIX_EPOCH, Instant, Duration};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::collections::HashMap;

use futures_util::{StreamExt, SinkExt};
use serde_json::json;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use dotenvy::dotenv;
use tracing::{info, warn, error, Level};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};
use anyhow::{Context, Result};

use sniper_types::trigon_types::*;
use sniper_types::moonshot_types::{str_to_symbol_hash, symbol_hash_to_str};
use sniper_types::exchange::bitfinex;

// ═════════════════════════════════════════════════════════════
// Shared modules (v12.0 — unified from shared crate)
// ═════════════════════════════════════════════════════════════
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::init_mmap;

const VERSION: &str = "1.0.0";

// Auth → shared exchange module (Phase 5.2)

// ═══════════════════════════════════════════════════════════
// Triangle Calculator
// ═══════════════════════════════════════════════════════════
/// Calculate implied arbitrage rate for a triangle.
/// For legs with direction=0 (BUY): use ASK price (pay more)
/// For legs with direction=1 (SELL): use BID price (receive less)
/// Returns (implied_rate, profit_bps_after_fees)
fn calculate_triangle_i64(
    bids: &[u64; 3],
    asks: &[u64; 3],
    directions: &[u32; 3],
    fee_bps: u64, // e.g. 2000 = 20 bps
) -> (i64, i64) {
    let initial: u128 = 1_000_000_000_000_000;
    let mut amt = initial;

    for i in 0..3 {
        if directions[i] == 0 {
            if asks[i] == 0 { return (0, -10000_00); }
            amt = (amt * sniper_types::PRICE_SCALE_I as u128) / asks[i] as u128;
        } else {
            if bids[i] == 0 { return (0, -10000_00); }
            amt = (amt * bids[i] as u128) / sniper_types::PRICE_SCALE_I as u128;
        }
    }

    let rate_i64 = ((amt * sniper_types::PRICE_SCALE_I as u128) / initial) as i64;
    
    let fee_mult = 1_000_000 - fee_bps as u128;
    let mut out_amt = amt;
    for _ in 0..3 { out_amt = (out_amt * fee_mult) / 1_000_000; }
    
    let profit_bps = ((out_amt as i128 - initial as i128) * 10000 * 100) / initial as i128;
    
    (rate_i64, profit_bps as i64)
}

// Ticker parser → shared exchange module (Phase 5.2)

// ═══════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════
#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();
    tracing_subscriber::registry()
        .with(fmt::layer().with_target(false).json())
        .with(EnvFilter::from_default_env().add_directive(Level::INFO.into()))
        .init();

    let notifier = Arc::new(AsyncNotifier::new("trigon", "🔺"));
    let engine_mmap = init_mmap::<TrigonEngineState>(TRIGON_ENGINE_PATH)?;
    let risk_mmap = init_mmap::<TrigonRiskState>(TRIGON_RISK_PATH)?;
    let fee_mmap = init_mmap::<sniper_types::fee_types::GlobalFeeState>(
        sniper_types::fee_types::FEE_STATE_PATH)?;
    let engine = unsafe { &*(engine_mmap.as_ptr() as *const TrigonEngineState) };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const TrigonRiskState) };
    let fee_state = unsafe { &*(fee_mmap.as_ptr() as *const sniper_types::fee_types::GlobalFeeState) };
    // L2 Shared State — Master Struct
    let l2_shared_mmap = init_mmap::<sniper_types::l2_command::L2SharedState>(
        sniper_types::l2_command::L2_COMMAND_PATH,
    )?;
    let l2_shared = unsafe { &*(l2_shared_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };
    let l2cmd = &l2_shared.cmd;

    let key = std::env::var("BITFINEX_API_KEY").context("Missing BITFINEX_API_KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing BITFINEX_API_SECRET")?;

    info!(event = "system_start", version = VERSION, bot = "trigon", strategy = "triangular_arbitrage");

    // ═══ SINGLE-INSTANCE LOCK (v12.0 — shared module) ═══
    let _lock_guard = sniper_types::lock::ensure_single_instance("trigon-core")?;

    notifier.send(format!("🔺 Trigon v{} (Triangular Arbitrage) ONLINE", VERSION));

    // ═══ MAIN RECONNECT LOOP ═══
    loop {
        let ws_result = connect_async(bitfinex::WS_URL).await;
        let (ws, _) = match ws_result {
            Ok(v) => v,
            Err(e) => { warn!(event = "ws_fail", error = %e); tokio::time::sleep(Duration::from_secs(60)).await; continue; }
        };

        let (mut write, mut read) = ws.split();

        // Auth (shared exchange module)
        let auth_msg = sniper_types::exchange::bitfinex_auth_message(&key, &sec);
        write.send(Message::Text(auth_msg.into())).await?;

        // Collect all unique symbols from configured triangles
        let mut chan_to_symbol: HashMap<i64, u64> = HashMap::new();
        let mut order_msg = bytes::BytesMut::with_capacity(1024);
        let mut ryu1 = ryu::Buffer::new();
        let mut ryu2 = ryu::Buffer::new();
        let mut symbol_bids_i: HashMap<u64, u64> = HashMap::new();
        let mut symbol_asks_i: HashMap<u64, u64> = HashMap::new();
        let mut subscribed_symbols: Vec<String> = Vec::new();

        for i in 0..TRIGON_MAX_TRIANGLES {
            for leg in 0..TRIGON_LEGS {
                let sym_hash = risk.triangles[i].leg_symbols[leg].load(Ordering::Acquire);
                if sym_hash != 0 {
                    let symbol = symbol_hash_to_str(sym_hash);
                    if !subscribed_symbols.contains(&symbol) {
                        write.send(Message::Text(
                            json!({"event":"subscribe","channel":"ticker","symbol":&symbol}).to_string().into()
                        )).await?;
                        subscribed_symbols.push(symbol.clone());
                        info!(event = "subscribing", symbol = symbol);
                    }
                }
            }
        }

        // If no triangles configured, subscribe to default set
        if subscribed_symbols.is_empty() {
            for &(a, b, c) in KNOWN_TRIANGLES {
                for sym in [a, b, c] {
                    if !subscribed_symbols.contains(&sym.to_string()) {
                        write.send(Message::Text(
                            json!({"event":"subscribe","channel":"ticker","symbol":sym}).to_string().into()
                        )).await?;
                        subscribed_symbols.push(sym.to_string());
                    }
                }
            }
            info!(event = "default_triangles", count = KNOWN_TRIANGLES.len());
        }

        let mut authed = false;
        let mut last_scan = Instant::now();
        let mut last_exec_ms: [u64; TRIGON_MAX_TRIANGLES] = [0; TRIGON_MAX_TRIANGLES];

        // ═══ MESSAGE LOOP ═══
        while let Some(msg) = read.next().await {
            let loop_start = Instant::now();
            let msg = match msg { Ok(m) => m, Err(_) => break };

            let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
            engine.heartbeat_ms.store(now_ms, Ordering::Release);

            if let Message::Text(text) = msg {
                let bytes = text.as_bytes();

                // Fast path: ticker update
                if let Some((chan, bid, ask)) = sniper_types::exchange::fast_parse_ticker(bytes) {
                    if let Some(&sym_hash) = chan_to_symbol.get(&chan) {
                        let bid_f = bid as u64;
                        let ask_f = ask as u64;
                        symbol_bids_i.insert(sym_hash, bid_f);
                        symbol_asks_i.insert(sym_hash, ask_f);

                        // Update leg data in engine state
                        for t in 0..TRIGON_MAX_TRIANGLES {
                            for l in 0..TRIGON_LEGS {
                                let leg_hash = risk.triangles[t].leg_symbols[l].load(Ordering::Acquire);
                                if leg_hash == sym_hash {
                                    engine.triangles[t].legs[l].best_bid.store(bid as u64, Ordering::Release);
                                    engine.triangles[t].legs[l].best_ask.store(ask as u64, Ordering::Release);
                                    engine.triangles[t].legs[l].latency_ns.store(
                                        loop_start.elapsed().as_nanos() as u64, Ordering::Release
                                    );
                                }
                            }
                        }

                        // Triangle scan every 100ms
                        if authed && last_scan.elapsed().as_millis() >= 100 {
                            // Read real taker fee from shared GlobalFeeState
                            // All 3 legs are IOC → taker fee applies to each
                            // Scale: bps×100 → bps (e.g., 2000 → 20 bps)
                            let fee_bps = fee_state.taker_fee_bps.load(Ordering::Relaxed);
                            let paused = risk.global_paused.load(Ordering::Acquire) != 0;
                            let mut best_profit = -1000000i64;

                            for t in 0..TRIGON_MAX_TRIANGLES {
                                let tr = &risk.triangles[t];
                                if tr.enabled.load(Ordering::Acquire) == 0 { continue; }

                                let mut bids = [0u64; 3];
                                let mut asks = [0u64; 3];
                                let mut dirs = [0u32; 3];
                                let mut all_valid = true;

                                for l in 0..TRIGON_LEGS {
                                    let h = tr.leg_symbols[l].load(Ordering::Acquire);
                                    dirs[l] = tr.leg_directions[l].load(Ordering::Acquire);
                                    if let (Some(&b), Some(&a)) = (symbol_bids_i.get(&h), symbol_asks_i.get(&h)) {
                                        bids[l] = b;
                                        asks[l] = a;
                                    } else {
                                        all_valid = false;
                                        break;
                                    }
                                }

                                if !all_valid { continue; }

                                let (rate, profit) = calculate_triangle_i64(&bids, &asks, &dirs, fee_bps);
                                let et = &engine.triangles[t];
                                et.implied_rate.store(rate, Ordering::Release);
                                et.profit_bps.store(profit / 100, Ordering::Release);
                                et.fee_cost_bps.store(fee_bps * 3 / 100, Ordering::Release);
                                et.last_calc_ns.store(now_ms * 1_000_000, Ordering::Release);

                                if profit > best_profit { best_profit = profit; }

                                // Execute if profitable and not paused
                                let min_profit = (tr.min_profit_bps.load(Ordering::Acquire) * 100) as i64;
                                let cooldown = tr.cooldown_ms.load(Ordering::Acquire);
                                let max_usd = tr.max_order_usd.load(Ordering::Acquire) as i64;

                                // ═══ v14.2 LATENCY-AWARE PADDING (Issue #18) ═══
                                // L2 pre-computes p95 Tick-to-Trade → padding & killswitch
                                // L1: O(1) check via SeqLock-protected reads
                                let latency_pad = {
                                    let (v1, ok1) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
                                    let pad = l2cmd.latency_padding_bps.load(Ordering::Relaxed);
                                    let kill = l2cmd.latency_killswitch.load(Ordering::Relaxed);
                                    let (v2, ok2) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
                                    if v1 == v2 && ok1 && ok2 {
                                        if kill == 1 { continue; } // Killswitch: exchange overloaded
                                        (pad.max(0) * 100) as i64
                                    } else { 0 }
                                };
                                let effective_min_profit = min_profit + latency_pad;

                                if !paused && profit > effective_min_profit
                                    && et.executing.load(Ordering::Acquire) == 0
                                    && (now_ms - last_exec_ms[t]) > cooldown
                                    && max_usd > 0
                                {
                                    et.executing.store(1, Ordering::Release);

                                    order_msg.clear();
                                    order_msg.extend_from_slice(b"[0,\"ox_multi\",null,[");

                                    for l in 0..TRIGON_LEGS {
                                        let sym_hash = tr.leg_symbols[l].load(Ordering::Acquire);
                                        let dir = dirs[l];
                                        let sym = symbol_hash_to_str(sym_hash);
                                        let price_i = if dir == 0 { asks[l] } else { bids[l] };
                                        let price = (price_i as f64) / sniper_types::PRICE_SCALE_I as f64;

                                        // Leg quantity
                                        let max_usd_f = (max_usd as f64) / sniper_types::PRICE_SCALE_I as f64;
                                        let mut qty = if price > 0.0 { max_usd_f / price } else { 0.0 };
                                        if dir == 1 { qty = -qty; } // SELL is negative

                                        if l > 0 { order_msg.extend_from_slice(b","); }
                                        order_msg.extend_from_slice(b"[\"on\",{\"gid\":4000,\"symbol\":\"");
                                        order_msg.extend_from_slice(sym.as_bytes());
                                        order_msg.extend_from_slice(b"\",\"amount\":\"");
                                        order_msg.extend_from_slice(ryu1.format(qty).as_bytes());
                                        order_msg.extend_from_slice(b"\",\"price\":\"");
                                        order_msg.extend_from_slice(ryu2.format(price).as_bytes());
                                        order_msg.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]");
                                    }
                                    order_msg.extend_from_slice(b"]]");

                                    let text_msg = unsafe { String::from_utf8_unchecked(order_msg.to_vec()) };
                                    match write.send(Message::Text(text_msg.into())).await {
                                        Ok(_) => {
                                            et.executions.fetch_add(1, Ordering::Relaxed);
                                            last_exec_ms[t] = now_ms;
                                            info!(event = "arb_execute", triangle = t,
                                                profit_bps = profit,
                                                rate = rate,
                                                max_usd_f = format!("{:.2}", (max_usd as f64) / sniper_types::PRICE_SCALE_I as f64));
                                            notifier.send(format!(
                                                "💰 ARB EXEC! Tri#{} profit={} / 100 bps rate={} size=${:.2}",
                                                t, profit, rate, (max_usd as f64) / sniper_types::PRICE_SCALE_I as f64));
                                        }
                                        Err(e) => {
                                            warn!(event = "arb_send_fail", triangle = t, error = %e);
                                        }
                                    }
                                    et.executing.store(0, Ordering::Release);
                                }
                            }

                            engine.best_profit_bps.store(best_profit / 100, Ordering::Release);
                            engine.scan_latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);
                            last_scan = Instant::now();
                        }
                    }
                    continue;
                }

                // Slow path: events
                if bytes.first() == Some(&b'{') {
                    let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();
                    if v["event"] == "auth" {
                        if v["status"] == "OK" {
                            authed = true;
                            info!(event = "authenticated", bot = "trigon");
                        } else {
                            error!(event = "auth_failed", bot = "trigon", status = %v["status"], msg = %v["msg"]);
                            notifier.send(format!("❌ AUTH FAILED: {}", v["msg"]));
                        }
                    }
                    if v["event"] == "subscribed" && v["channel"] == "ticker" {
                        if let (Some(cid), Some(sym)) = (v["chanId"].as_i64(), v["symbol"].as_str()) {
                            let hash = str_to_symbol_hash(sym);
                            chan_to_symbol.insert(cid, hash);
                            info!(event = "subscribed", symbol = sym, chan_id = cid);
                        }
                    }
                }
            }
        }

        warn!(event = "ws_disconnected", bot = "trigon");
        notifier.send("⚠️ Trigon: WebSocket disconnected, reconnecting...".to_string());
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
