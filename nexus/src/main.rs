// 🪐 Nexus L0 Engine — Cross-Exchange Arbitrage Bot
// Sniper Armada · Bot #5 · v1.0.0
//
// Exploits price differences between Bitfinex and Binance.
// When spread > fee_threshold → simultaneous IOC orders on both exchanges.
//
// Architecture:
//   - L0 (this): Rust async, mmap reader, dual-exchange execution
//   - cross_exchange.bin: Written by price_bridge.py (BFX+BNB BBA)
//   - Bitfinex: WebSocket IOC orders (ox_multi, ~10ms)
//   - Binance: REST IOC orders (HMAC-SHA256, ~50-200ms)
//
// Best patterns consolidated from: Hydra (BotEvent notifier),
// Trigon (mmap init, IOC execution, fee math, latency padding),
// Moonshot (reqwest TG client), Grid (L2 Command Matrix)
//
// mmap IPC:
//   - /dev/shm/beroun/cross_exchange.bin (read: BBA + spreads)
//   - /dev/shm/beroun/l2_command.bin (read: latency padding/killswitch)

use std::time::{SystemTime, UNIX_EPOCH, Duration};
use std::io::Write;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use futures_util::StreamExt;
use futures_util::sink::SinkExt;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use dotenvy::dotenv;
use tracing::{info, warn, error, Level};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};
use memmap2::MmapMut;
use anyhow::{Context, Result};
use clap::Parser;

use sniper_types::exchange::cross_types::*;
use sniper_types::exchange::types::*;
use sniper_types::exchange::binance::Binance;
use sniper_types::exchange::bitfinex;
use sniper_types::PRICE_SCALE;

const VERSION: &str = "1.0.0";
const GID_NEXUS: u32 = 5000; // Unique GID for Nexus orders on Bitfinex

// ═══════════════════════════════════════════════════════════
// CLI Arguments
// ═══════════════════════════════════════════════════════════
#[derive(Parser, Debug)]
#[command(name = "nexus-core", version = VERSION, about = "Cross-Exchange Arbitrage Bot")]
struct Args {
    /// Paper trading mode — log signals but don't execute orders
    #[arg(long)]
    paper: bool,

    /// Minimum net profit in bps (after fees + slippage)
    #[arg(long, default_value_t = 5.0)]
    min_profit_bps: f64,

    /// Bitfinex taker fee in bps
    #[arg(long, default_value_t = 10.0)]
    bfx_fee_bps: f64,

    /// Binance taker fee in bps
    #[arg(long, default_value_t = 10.0)]
    bnb_fee_bps: f64,

    /// Slippage buffer in bps
    #[arg(long, default_value_t = 3.0)]
    slippage_bps: f64,

    /// Max trade size in USD
    #[arg(long, default_value_t = 100.0)]
    max_trade_usd: f64,

    /// Cooldown between trades in ms
    #[arg(long, default_value_t = 5000)]
    cooldown_ms: u64,

    /// Scan interval in ms
    #[arg(long, default_value_t = 500)]
    scan_interval_ms: u64,
}
// ═════════════════════════════════════════════════════════════
// Shared modules (v12.0 — unified from shared crate)
// ═════════════════════════════════════════════════════════════
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::open_mmap_readonly;

// ═══════════════════════════════════════════════════════════
// Pair name table (matches price_bridge.py CROSS_EXCHANGE_PAIRS)
// ═══════════════════════════════════════════════════════════
const PAIR_NAMES: [&str; 16] = [
    "BTC", "ETH", "XRP", "SOL", "DOGE",
    "ADA", "AVAX", "LTC", "LINK", "DOT",
    "", "", "", "", "", "",
];

const BFX_SYMBOLS: [&str; 10] = [
    "tBTCUST", "tETHUST", "tXRPUST", "tSOLUST", "tDOGE:UST",
    "tADAUST", "tAVAX:UST", "tLTCUST", "tLINK:UST", "tDOTUST",
];

const BNB_SYMBOLS: [&str; 10] = [
    "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LTCUSDT", "LINKUSDT", "DOTUSDT",
];

// ═══════════════════════════════════════════════════════════
// Spread Calculator (adapted from Trigon calculate_triangle)
// ═══════════════════════════════════════════════════════════
struct ArbSignal {
    pair_idx: usize,
    pair_name: String,
    direction: ArbDirection,
    bfx_price: f64,      // Price on BFX side
    bnb_price: f64,      // Price on BNB side
    gross_bps: f64,       // Raw spread before fees
    net_bps: f64,         // Net profit after fees + slippage
    size_usd: f64,        // Suggested trade size
}

#[derive(Debug, Clone, Copy)]
enum ArbDirection {
    BuyBfxSellBnb,   // Buy on Bitfinex, sell on Binance
    BuyBnbSellBfx,   // Buy on Binance, sell on Bitfinex
}

impl std::fmt::Display for ArbDirection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ArbDirection::BuyBfxSellBnb => write!(f, "BFX→BNB"),
            ArbDirection::BuyBnbSellBfx => write!(f, "BNB→BFX"),
        }
    }
}

fn scan_for_arb(
    cross_state: &CrossExchangeState,
    args: &Args,
    latency_pad_bps: f64,
) -> Option<ArbSignal> {
    let active = cross_state.active_pairs.load(Ordering::Acquire) as usize;
    let paused = cross_state.emergency_pause.load(Ordering::Acquire) != 0;
    if paused || active == 0 { return None; }

    // Check daily loss limit
    let daily_pnl = cross_state.daily_cross_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE;
    let daily_limit = cross_state.daily_loss_limit.load(Ordering::Acquire) as f64 / PRICE_SCALE;
    if daily_pnl < -daily_limit { return None; } // Over daily loss limit

    let total_fee_bps = args.bfx_fee_bps + args.bnb_fee_bps + args.slippage_bps + latency_pad_bps;
    let mut best: Option<ArbSignal> = None;

    for i in 0..active.min(MAX_CROSS_PAIRS) {
        let pair = &cross_state.pairs[i];
        if pair.enabled.load(Ordering::Acquire) == 0 { continue; }

        // Read BBA from both exchanges
        let bfx_bid = pair.bitfinex.bid.load(Ordering::Acquire) as f64 / PRICE_SCALE;
        let bfx_ask = pair.bitfinex.ask.load(Ordering::Acquire) as f64 / PRICE_SCALE;
        let bnb_bid = pair.binance.bid.load(Ordering::Acquire) as f64 / PRICE_SCALE;
        let bnb_ask = pair.binance.ask.load(Ordering::Acquire) as f64 / PRICE_SCALE;

        // Staleness check: both sides must have recent data (< 5s)
        let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        let bfx_age = now_ms.saturating_sub(pair.bitfinex.last_update_ms.load(Ordering::Acquire));
        let bnb_age = now_ms.saturating_sub(pair.binance.last_update_ms.load(Ordering::Acquire));
        if bfx_age > 5000 || bnb_age > 5000 { continue; }

        // Zero price check
        if bfx_bid <= 0.0 || bfx_ask <= 0.0 || bnb_bid <= 0.0 || bnb_ask <= 0.0 { continue; }

        // Direction 1: Buy on BFX (pay ask), Sell on BNB (receive bid)
        let spread_1 = (bnb_bid - bfx_ask) / bfx_ask * 10000.0; // bps
        // Direction 2: Buy on BNB (pay ask), Sell on BFX (receive bid)
        let spread_2 = (bfx_bid - bnb_ask) / bnb_ask * 10000.0; // bps

        let (direction, gross_bps, buy_price, sell_price) = if spread_1 > spread_2 {
            (ArbDirection::BuyBfxSellBnb, spread_1, bfx_ask, bnb_bid)
        } else {
            (ArbDirection::BuyBnbSellBfx, spread_2, bnb_ask, bfx_bid)
        };

        let net_bps = gross_bps - total_fee_bps;
        if net_bps < args.min_profit_bps { continue; }

        // Check exposure limits
        let max_exposure = match direction {
            ArbDirection::BuyBfxSellBnb => {
                cross_state.max_exposure_bitfinex_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE
            }
            ArbDirection::BuyBnbSellBfx => {
                cross_state.max_exposure_binance_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE
            }
        };
        let trade_size = args.max_trade_usd.min(max_exposure);

        let signal = ArbSignal {
            pair_idx: i,
            pair_name: PAIR_NAMES[i].to_string(),
            direction,
            bfx_price: match direction {
                ArbDirection::BuyBfxSellBnb => buy_price,
                ArbDirection::BuyBnbSellBfx => sell_price,
            },
            bnb_price: match direction {
                ArbDirection::BuyBfxSellBnb => sell_price,
                ArbDirection::BuyBnbSellBfx => buy_price,
            },
            gross_bps,
            net_bps,
            size_usd: trade_size,
        };

        // Keep the best signal
        if best.as_ref().map_or(true, |b| signal.net_bps > b.net_bps) {
            best = Some(signal);
        }
    }

    best
}

// ═══════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════
#[tokio::main]
async fn main() -> Result<()> {
    println!("DEBUG: CrossExchangeState size: {}", std::mem::size_of::<CrossExchangeState>());
    println!("DEBUG: active_pairs offset: {}", std::mem::size_of::<[CrossPairState; MAX_CROSS_PAIRS]>());
    dotenv().ok();
    let args = Args::parse();

    tracing_subscriber::registry()
        .with(fmt::layer().with_target(false).json())
        .with(EnvFilter::from_default_env().add_directive(Level::INFO.into()))
        .init();

    let notifier = Arc::new(AsyncNotifier::new("nexus", "🪐"));

    // ═══ SINGLE-INSTANCE LOCK (v12.0 — shared module) ═══
    let _lock_guard = sniper_types::lock::ensure_single_instance("nexus-core")?;

    // Write PID file (for Sentinel crash detection)
    std::fs::write("/tmp/nexus-core.pid", std::process::id().to_string())?;

    info!(event = "system_start", version = VERSION, bot = "nexus",
        strategy = "cross_exchange_arbitrage",
        paper = args.paper,
        min_profit_bps = args.min_profit_bps,
        total_fee_bps = args.bfx_fee_bps + args.bnb_fee_bps + args.slippage_bps);

    let mode = if args.paper { "PAPER" } else { "LIVE" };
    notifier.alert(format!("🪐 Nexus v{} ({}) ONLINE | min_profit={:.1}bps", VERSION, mode, args.min_profit_bps));

    // ═══ Open mmap ═══
    let cross_mmap = open_mmap_readonly(CROSS_EXCHANGE_PATH)
        .context("cross_exchange.bin not found — is price_bridge.py running?")?;
    let cross_state = unsafe { &*(cross_mmap.as_ptr() as *const CrossExchangeState) };

    // L2 Command Matrix (for latency padding — from Trigon)
    let l2cmd_mmap = {
        let path = sniper_types::l2_command::L2_COMMAND_PATH;
        let f = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false)
            .open(path).context("l2_command.bin")?;
        f.set_len(sniper_types::l2_command::L2_COMMAND_FILE_SIZE as u64)?;
        unsafe { MmapMut::map_mut(&f)? }
    };
    let l2cmd = unsafe { &*(l2cmd_mmap.as_ptr() as *const sniper_types::l2_command::L2CommandMatrix) };

    // Binance REST client (for order execution)
    let binance = Binance::new();
    let http_client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()?;

    // Bitfinex credentials (for WebSocket order execution)
    let bfx_key = std::env::var("BITFINEX_API_KEY").context("Missing BITFINEX_API_KEY")?;
    let bfx_secret = std::env::var("BITFINEX_API_SECRET").context("Missing BITFINEX_API_SECRET")?;

    let mut last_exec_ms: u64 = 0;
    let mut total_signals: u64 = 0;
    let mut total_trades: u64 = 0;

    // ═══ MAIN RECONNECT LOOP (from Trigon) ═══
    // Nexus needs BFX WS for order execution (not data — that comes from mmap)
    loop {
        info!(event = "ws_connecting", exchange = "bitfinex");
        let ws_result = connect_async(bitfinex::WS_URL).await;
        let (ws, _): (tokio_tungstenite::WebSocketStream<_>, _) = match ws_result {
            Ok(v) => v,
            Err(e) => {
                warn!(event = "ws_fail", error = %e);
                tokio::time::sleep(Duration::from_secs(30)).await;
                continue;
            }
        };

        let (mut write, mut read) = ws.split();

        // Authenticate on Bitfinex WS (needed for order placement)
        let auth_msg = sniper_types::exchange::bitfinex_auth_message(&bfx_key, &bfx_secret);
        write.send(Message::Text(auth_msg.into())).await?;

        let mut authed = false;
        let mut scan_interval = tokio::time::interval(Duration::from_millis(args.scan_interval_ms));
        let mut last_scan = std::time::Instant::now();

        info!(event = "ws_connected", exchange = "bitfinex", mode = mode);

        // ═══ ARBI SCAN LOOP ═══
        loop {
            tokio::select! {
                // Periodic spread scan (data from mmap, not WS)
                _ = scan_interval.tick() => {
                    if !authed { continue; }

                    // Read latency padding from L2 Command Matrix (from Trigon)
                    let latency_pad = {
                        let (v1, ok1) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
                        let pad = l2cmd.latency_padding_bps.load(Ordering::Relaxed);
                        let kill = l2cmd.latency_killswitch.load(Ordering::Relaxed);
                        let (v2, ok2) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
                        if v1 == v2 && ok1 && ok2 {
                            if kill == 1 { continue; } // Killswitch active
                            pad.max(0) as f64
                        } else { 0.0 }
                    };

                    let now_ms = SystemTime::now().duration_since(UNIX_EPOCH)
                        .unwrap_or_default().as_millis() as u64;

                    // Cooldown check
                    if now_ms - last_exec_ms < args.cooldown_ms { continue; }

                    // Scan for arbitrage opportunity
                    
                    let active = cross_state.active_pairs.load(Ordering::Acquire) as usize;
                    if last_scan.elapsed().as_secs() > 10 && active > 0 {
                        let mut best_gross = -1000.0;
                        let mut best_pair = "";
                        for i in 0..active.min(MAX_CROSS_PAIRS) {
                            let pair = &cross_state.pairs[i];
                            if pair.enabled.load(Ordering::Acquire) == 0 { continue; }
                            let spread_bps = pair.best_spread_bps.load(Ordering::Relaxed) as f64 / 100.0;
                            if spread_bps > best_gross {
                                best_gross = spread_bps;
                                best_pair = PAIR_NAMES[i];
                            }
                        }
                        if best_gross > -1000.0 {
                            println!("🚀 ARB SCORE: {} (best spread {:.1} bps)", best_pair, best_gross);
                            info!(event = "arb_score", pair = best_pair, spread_bps = format!("{:.1}", best_gross));
                        }
                        last_scan = std::time::Instant::now();
                    }

                    if let Some(signal) = scan_for_arb(cross_state, &args, latency_pad) {
                        total_signals += 1;
                        notifier.signal(
                            signal.pair_name.clone(),
                            signal.direction.to_string(),
                            signal.gross_bps,
                        );

                        info!(event = "arb_detected",
                            pair = signal.pair_name,
                            direction = %signal.direction,
                            gross_bps = format!("{:.1}", signal.gross_bps),
                            net_bps = format!("{:.1}", signal.net_bps),
                            size_usd = format!("{:.2}", signal.size_usd),
                            total_signals = total_signals);

                        println!("🚀 ARB SCORE: {} {} (net {:.1} bps)", signal.pair_name, signal.direction, signal.net_bps);

                        if args.paper {
                            // Paper mode — just log
                            info!(event = "paper_trade",
                                pair = signal.pair_name,
                                direction = %signal.direction,
                                bfx_price = format!("{:.2}", signal.bfx_price),
                                bnb_price = format!("{:.2}", signal.bnb_price));
                            last_exec_ms = now_ms;
                            continue;
                        }

                        // ═══ LIVE EXECUTION ═══
                        // Simultaneous IOC orders on both exchanges
                        let qty = if signal.bfx_price > 0.0 {
                            signal.size_usd / signal.bfx_price
                        } else { continue };

                        // BFX side: WebSocket IOC order
                        let (bfx_side, bfx_price) = match signal.direction {
                            ArbDirection::BuyBfxSellBnb => ("BUY", signal.bfx_price),
                            ArbDirection::BuyBnbSellBfx => ("SELL", signal.bfx_price),
                        };
                        let bfx_signed_qty = if bfx_side == "BUY" { qty } else { -qty };
                        let bfx_symbol = BFX_SYMBOLS[signal.pair_idx];

                        let mut order_msg = String::with_capacity(512);
                        let mut buf = ryu::Buffer::new();
                        let mut buf2 = ryu::Buffer::new();
                        order_msg.push_str("[0,\"ox_multi\",null,[[\"on\",{\"gid\":");
                        order_msg.push_str(&GID_NEXUS.to_string());
                        order_msg.push_str(",\"symbol\":\"");
                        order_msg.push_str(bfx_symbol);
                        order_msg.push_str("\",\"amount\":");
                        order_msg.push_str(buf.format(bfx_signed_qty));
                        order_msg.push_str(",\"price\":\"");
                        order_msg.push_str(buf2.format(bfx_price));
                        order_msg.push_str("\",\"type\":\"EXCHANGE IOC\"}]]]");

                        // BNB side: REST IOC order
                        let bnb_side = match signal.direction {
                            ArbDirection::BuyBfxSellBnb => OrderSide::Sell,
                            ArbDirection::BuyBnbSellBfx => OrderSide::Buy,
                        };
                        let bnb_symbol = BNB_SYMBOLS[signal.pair_idx];
                        let bnb_price_scaled = match signal.direction {
                            ArbDirection::BuyBfxSellBnb => (signal.bnb_price * PRICE_SCALE) as i64,
                            ArbDirection::BuyBnbSellBfx => (signal.bnb_price * PRICE_SCALE) as i64,
                        };

                        let bnb_order = OrderRequest {
                            symbol: bnb_symbol.to_string(),
                            side: bnb_side,
                            amount: qty,
                            price: bnb_price_scaled,
                            order_type: OrderType::Ioc,
                            gid: GID_NEXUS,
                            flags: OrderFlags::default(),
                        };

                        // ═══ SIMULTANEOUS EXECUTION (tokio::join!) ═══
                        let bfx_fut = write.send(Message::Text(order_msg.into()));
                        let bnb_signed = binance.new_order(&bnb_order);

                        if let Some(signed) = bnb_signed {
                            let client = http_client.clone();
                            let bnb_fut = async move {
                                let req = match signed.method {
                                    "POST" => client.post(&signed.url),
                                    "DELETE" => client.delete(&signed.url),
                                    _ => client.get(&signed.url),
                                };
                                req.header("X-MBX-APIKEY", &signed.api_key)
                                    .send().await
                            };

                            let (bfx_result, bnb_result) = tokio::join!(bfx_fut, bnb_fut);

                            let bfx_ok = bfx_result.is_ok();
                            let bnb_ok = bnb_result.as_ref().map(|r| r.status().is_success()).unwrap_or(false);

                            if bfx_ok && bnb_ok {
                                total_trades += 1;
                                last_exec_ms = now_ms;
                                notifier.trade(
                                    signal.pair_name, signal.direction.to_string(),
                                    signal.gross_bps, signal.net_bps, signal.size_usd,
                                );
                                info!(event = "arb_executed",
                                    bfx = "OK", bnb = "OK",
                                    trades = total_trades);
                            } else {
                                // Leg risk! One side failed
                                warn!(event = "leg_risk",
                                    bfx_ok = bfx_ok, bnb_ok = bnb_ok,
                                    pair = signal.pair_name);
                                notifier.alert(format!(
                                    "⚠️ LEG RISK! {} | BFX={} BNB={} | Checking fills...",
                                    signal.pair_name,
                                    if bfx_ok { "✅" } else { "❌" },
                                    if bnb_ok { "✅" } else { "❌" }
                                ));
                                last_exec_ms = now_ms;
                            }
                        } else {
                            warn!(event = "bnb_sign_failed", msg = "Binance credentials missing");
                        }
                    }
                }

                // WebSocket messages (auth confirmations, order status)
                msg = read.next() => {
                    match msg {
                        Some(Ok(Message::Text(text))) => {
                            let bytes = text.as_bytes();
                            if bytes.first() == Some(&b'{') {
                                if let Ok(v) = serde_json::from_slice::<serde_json::Value>(bytes) {
                                    if v["event"] == "auth" {
                                        if v["status"] == "OK" {
                                            authed = true;
                                            info!(event = "authenticated", bot = "nexus");
                                        } else {
                                            error!(event = "auth_failed", bot = "nexus", status = %v["status"], msg = %v["msg"]);
                                            notifier.alert(format!("❌ AUTH FAILED: {}", v["msg"]));
                                        }
                                    }
                                }
                            }
                        }
                        Some(Ok(Message::Close(_))) | None => break,
                        _ => {}
                    }
                }
            }
        }

        warn!(event = "ws_disconnected", bot = "nexus");
        notifier.alert("⚠️ Nexus: BFX WebSocket disconnected, reconnecting...".to_string());
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
