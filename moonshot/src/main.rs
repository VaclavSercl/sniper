// 🌙 Moonshot L0 Engine — Multi-Symbol Flash Crash Catcher
// Sniper Armada · Bot #2 · v1.0.0
//
// Captures rapid price drops ("wicks") across up to 20 Bitfinex pairs.
// AI-driven pair selection via L2 Oracle (sniper_orchestrator.py).
// Ghost BUY orders are placed X% below market and dynamically repositioned.
//
// Architecture:
//   - L0 (this): Rust async, fastwebsockets, zero-alloc hot path
//   - L1: Python l1_shield.py (toxic flow filter, delta modifiers)
//   - L2: Python sniper_orchestrator.py (Gemini AI pair selection, 66min cycle)
//
// mmap IPC:
//   - /dev/shm/beroun/moonshot_engine.bin (written by L0, read by dashboard/brain)
//   - /dev/shm/beroun/moonshot_risk.bin (written by L2, read by L0)

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

use sniper_types::moonshot_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};
use sniper_types::exchange::bitfinex;

const VERSION: &str = "1.0.0";

// ═══════════════════════════════════════════════════════════
// Shared modules (v12.0 — unified from shared crate)
// ═══════════════════════════════════════════════════════════
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::init_mmap;


// Auth + ticker parser → shared exchange module (Phase 5.2)
// See: sniper_types::exchange::{hmac_sha384_hex, fast_parse_ticker, bitfinex_auth_message}

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

    let notifier = Arc::new(AsyncNotifier::new("moonshot", "🌙"));
    let engine_mmap = init_mmap::<MoonshotEngineState>(MOONSHOT_ENGINE_PATH)?;
    let risk_mmap = init_mmap::<MoonshotRiskState>(MOONSHOT_RISK_PATH)?;

    let engine = unsafe { &*(engine_mmap.as_ptr() as *const MoonshotEngineState) };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const MoonshotRiskState) };

    // L2 Shared State — Master Struct
    let l2_shared_mmap = init_mmap::<sniper_types::l2_command::L2SharedState>(
        sniper_types::l2_command::L2_COMMAND_PATH,
    )?;
    let l2_shared = unsafe { &*(l2_shared_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };
    let l2cmd = &l2_shared.cmd;
    let l2_risk = &l2_shared.global_risk;

    let key = std::env::var("BITFINEX_API_KEY").context("Missing BITFINEX_API_KEY")?;
    let sec = std::env::var("BITFINEX_API_SECRET").context("Missing BITFINEX_API_SECRET")?;

    info!(event = "system_start", version = VERSION, bot = "moonshot", strategy = "flash_crash_multi_symbol");

    // ═══ SINGLE-INSTANCE LOCK (v12.0 — shared module) ═══
    let _lock_guard = sniper_types::lock::ensure_single_instance("moonshot-core")?;

    notifier.send(format!("🌙 Moonshot v{} (Multi-Symbol AI) ONLINE", VERSION));

    // ═══ MAIN RECONNECT LOOP ═══
    loop {
        let ws_result = connect_async(bitfinex::WS_URL).await;
        let (ws, _) = match ws_result {
            Ok(v) => v,
            Err(e) => {
                warn!(event = "ws_connect_fail", error = %e);
                tokio::time::sleep(Duration::from_secs(60)).await;
                continue;
            }
        };

        let (mut write, mut read) = ws.split();

        // ═══ AUTH (shared exchange module) ═══
        let auth_msg = sniper_types::exchange::bitfinex_auth_message(&key, &sec);
        write.send(Message::Text(auth_msg.into())).await?;

        // ═══ MULTI-SYMBOL SUBSCRIPTION ═══
        let mut chan_to_idx: HashMap<i64, usize> = HashMap::new();
        let mut idx_to_symbol: HashMap<usize, String> = HashMap::new();
        let mut last_order_ts = vec![Instant::now(); MOONSHOT_MAX_PAIRS];

        // Subscribe to all pairs configured by AI (from risk mmap)
        for i in 0..MOONSHOT_MAX_PAIRS {
            let symbol_hash = risk.pairs[i].symbol_hash.load(Ordering::Acquire);
            if symbol_hash != 0 {
                let symbol = symbol_hash_to_str(symbol_hash);
                write.send(Message::Text(
                    json!({"event": "subscribe", "channel": "ticker", "symbol": symbol}).to_string().into()
                )).await?;
                idx_to_symbol.insert(i, symbol);
            }
        }

        let mut authed = false;
        let mut order_msg = bytes::BytesMut::with_capacity(1024);
        let mut itoa_buf = itoa::Buffer::new();
        let mut ryu1 = ryu::Buffer::new();
        let mut ryu2 = ryu::Buffer::new();

        // ═══ MESSAGE LOOP ═══
        while let Some(msg) = read.next().await {
            let loop_start = Instant::now();
            let msg = match msg { Ok(m) => m, Err(_) => break };

            // Update engine heartbeat
            let now_ms = SystemTime::now().duration_since(UNIX_EPOCH)
                .unwrap_or_default().as_millis() as u64;
            engine.heartbeat_ms.store(now_ms, Ordering::Release);

            if let Message::Text(text) = msg {
                let bytes = text.as_bytes();

                // ═══ FAST PATH: Ticker ═══
                if let Some((chan, bid, ask)) = sniper_types::exchange::fast_parse_ticker(bytes) {
                    if let Some(&idx) = chan_to_idx.get(&chan) {
                        let e = &engine.pairs[idx];
                        let r = &risk.pairs[idx];

                        // Write market data
                        e.best_bid.store(bid as u64, Ordering::Release);
                        e.best_ask.store(ask as u64, Ordering::Release);
                        e.last_trade.store(((bid + ask) / 2) as u64, Ordering::Release);
                        e.latency_ns.store(loop_start.elapsed().as_nanos() as u64, Ordering::Release);

                        // ═══ MOONSHOT LOGIC ═══
                        let paused = risk.global_paused.load(Ordering::Acquire) != 0;
                        let min_interval_ms = 50;

                        // ═══ v14.2 CAS TRIPWIRE (Issue #18) ═══
                        // L2 pre-computes trigger_price (ema - 3.5σ) and arms weapon
                        // L1: O(1) price comparison + CAS atomic disarm = Single Bullet
                        if authed && !paused {
                            let mid_price = (bid + ask) / 2;
                            // Volume filter disabled here — L2 controls arming via leverage flush
                            // Pass avg_vol=0 to bypass anti-spoofing (L2 is the gatekeeper)
                            if let Some(trigger) = sniper_types::l2_command::moonshot_check_and_disarm(
                                l2cmd, mid_price as i64, 1, 0  // avg=0 bypasses volume check
                            ) {
                                // 🚀 MOONSHOT FIRED! CAS guarantees single execution
                                let order_usd = r.order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                                let mid_f64 = mid_price as f64 / PRICE_SCALE_I as f64;
                                if order_usd > 0.0 && mid_f64 > 0.0 {
                                    let coin_amount = (order_usd / mid_f64).max(0.00015);
                                    if let Some(symbol) = idx_to_symbol.get(&idx) {
                                        order_msg.clear();
                                        order_msg.extend_from_slice(b"[0,\"on\",null,{\"gid\":2001,\"symbol\":\"");
                                        order_msg.extend_from_slice(symbol.as_bytes());
                                        order_msg.extend_from_slice(b"\",\"amount\":\"");
                                        order_msg.extend_from_slice(ryu1.format(coin_amount).as_bytes());
                                        order_msg.extend_from_slice(b"\",\"price\":\"");
                                        order_msg.extend_from_slice(ryu2.format(mid_f64).as_bytes());
                                        order_msg.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]");

                                        let text_msg = unsafe { String::from_utf8_unchecked(order_msg.to_vec()) };
                                        let _ = write.send(Message::Text(text_msg.into())).await;
                                        warn!(event = "moonshot_fired", symbol = %symbol,
                                              trigger = trigger as f64 / PRICE_SCALE_I as f64,
                                              price = mid_f64, amount = coin_amount);
                                        notifier.send(format!("🚀 MOONSHOT FIRED! {} @ ${:.2} (trigger ${:.2})",
                                            symbol, mid_f64, trigger as f64 / PRICE_SCALE_I as f64));

                                        // ═══ CROSS-BOT SIGNAL: Alert Hydra via shared mmap CL4 ═══
                                        let drop_bps = ((mid_f64 - (trigger as f64 / PRICE_SCALE_I as f64)) / mid_f64 * 10_000.0) as i64;
                                        sniper_types::l2_command::signal_flash_crash(l2_risk, drop_bps);
                                        warn!(event = "cross_bot_signal", drop_bps = drop_bps,
                                              "🚨 Flash crash signal → Hydra EMERGENCY EXIT");

                                        last_order_ts[idx] = Instant::now();
                                    }
                                }
                            }
                        }

                        // ═══ GHOST ORDERS (existing passive logic) ═══
                        if authed && !paused && last_order_ts[idx].elapsed().as_millis() > min_interval_ms {
                            let mid_price = (bid + ask) / 2;
                            let mid_f64 = mid_price as f64 / PRICE_SCALE_I as f64;

                            let drop_pct = r.m_shot_price_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                            let tp_pct = r.tp_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                            let order_usd = r.order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;

                            if drop_pct > 0.0 && order_usd > 0.0 && mid_f64 > 0.0 {
                                let buy_p = mid_f64 * (1.0 - (drop_pct / 100.0));
                                let sell_p = buy_p * (1.0 + (tp_pct / 100.0));
                                let coin_amount = (order_usd / buy_p).max(0.00015);

                                let ask_f64 = ask as f64 / PRICE_SCALE_I as f64;
                                if buy_p >= ask_f64 { continue; }

                                if let Some(symbol) = idx_to_symbol.get(&idx) {
                                    order_msg.clear();
                                    order_msg.extend_from_slice(b"[0,\"ox_multi\",null,[[\"oc_multi\",{\"symbol\":\"");
                                    order_msg.extend_from_slice(symbol.as_bytes());
                                    order_msg.extend_from_slice(b"\"}],[\"on\",{\"gid\":2000,\"symbol\":\"");
                                    order_msg.extend_from_slice(symbol.as_bytes());
                                    order_msg.extend_from_slice(b"\",\"amount\":\"");
                                    order_msg.extend_from_slice(ryu1.format(coin_amount).as_bytes());
                                    order_msg.extend_from_slice(b"\",\"price\":\"");
                                    order_msg.extend_from_slice(ryu2.format(buy_p).as_bytes());
                                    order_msg.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\"}],[\"on\",{\"gid\":2000,\"symbol\":\"");
                                    order_msg.extend_from_slice(symbol.as_bytes());
                                    order_msg.extend_from_slice(b"\",\"amount\":\"");
                                    order_msg.extend_from_slice(ryu1.format(-coin_amount).as_bytes());
                                    order_msg.extend_from_slice(b"\",\"price\":\"");
                                    order_msg.extend_from_slice(ryu2.format(sell_p).as_bytes());
                                    order_msg.extend_from_slice(b"\",\"type\":\"EXCHANGE LIMIT\"}]]]");

                                    let text_msg = unsafe { String::from_utf8_unchecked(order_msg.to_vec()) };
                                    let _ = write.send(Message::Text(text_msg.into())).await;
                                    e.buy_order_price.store((buy_p * PRICE_SCALE_I as f64) as u64, Ordering::Release);
                                    last_order_ts[idx] = Instant::now();
                                }
                            }
                        }
                    }
                    continue; // Exit ticker fast path
                }

                // ═══ SLOW PATH: Auth/Events ═══
                if bytes.first() == Some(&b'{') {
                    let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();

                    if v["event"] == "auth" {
                        if v["status"] == "OK" {
                            authed = true;
                            info!(event = "authenticated", bot = "moonshot");
                        } else {
                            error!(event = "auth_failed", bot = "moonshot", status = %v["status"], msg = %v["msg"]);
                            notifier.send(format!("❌ AUTH FAILED: {}", v["msg"]));
                        }
                    }

                    // Map chanId → internal pair index
                    if v["event"] == "subscribed" && v["channel"] == "ticker" {
                        if let (Some(chan_id), Some(symbol)) = (v["chanId"].as_i64(), v["symbol"].as_str()) {
                            for (&idx, sym) in idx_to_symbol.iter() {
                                if sym == symbol {
                                    chan_to_idx.insert(chan_id, idx);
                                    info!(event = "pair_subscribed", symbol = symbol, idx = idx, chan_id = chan_id);
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }

        warn!(event = "ws_disconnected", bot = "moonshot");
        notifier.send("⚠️ Moonshot: WebSocket disconnected, reconnecting...".to_string());
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
