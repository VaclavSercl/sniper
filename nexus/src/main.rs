// 🪐 Nexus L0 Engine — Cross-Exchange Arbitrage Bot
// Framework: SovereignEngine
use std::time::{SystemTime, UNIX_EPOCH, Duration, Instant};
use std::sync::atomic::Ordering;
use std::sync::Arc;

use anyhow::{Context, Result};
use clap::Parser;
use tracing::{info, warn, error};

use sniper_types::exchange::cross_types::*;
use sniper_types::exchange::types::*;
use sniper_types::exchange::binance::Binance;
use sniper_types::PRICE_SCALE;

use sniper_types::framework::{SovereignEngine, SovereignRunner};
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::open_mmap_readonly;

const VERSION: &str = "1.1.0";
const GID_NEXUS: u32 = 5000;

#[derive(Parser, Debug, Clone)]
#[command(name = "nexus-core", version = VERSION, about = "Cross-Exchange Arbitrage Bot")]
struct Args {
    #[arg(long)] paper: bool,
    #[arg(long, default_value_t = 5.0)] min_profit_bps: f64,
    #[arg(long, default_value_t = 10.0)] bfx_fee_bps: f64,
    #[arg(long, default_value_t = 10.0)] bnb_fee_bps: f64,
    #[arg(long, default_value_t = 3.0)] slippage_bps: f64,
    #[arg(long, default_value_t = 100.0)] max_trade_usd: f64,
    #[arg(long, default_value_t = 5000)] cooldown_ms: u64,
    #[arg(long, default_value_t = 500)] scan_interval_ms: u64,
}

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

struct ArbSignal {
    pair_idx: usize,
    pair_name: String,
    direction: ArbDirection,
    bfx_price: f64,
    bnb_price: f64,
    gross_bps: f64,
    net_bps: f64,
    size_usd: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum ArbDirection {
    BuyBfxSellBnb,
    BuyBnbSellBfx,
}
impl std::fmt::Display for ArbDirection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ArbDirection::BuyBfxSellBnb => write!(f, "BFX→BNB"),
            ArbDirection::BuyBnbSellBfx => write!(f, "BNB→BFX"),
        }
    }
}

fn scan_for_arb(cross_state: &CrossExchangeState, args: &Args, latency_pad_bps: f64) -> Option<ArbSignal> {
    let active = cross_state.active_pairs.load(Ordering::Acquire) as usize;
    let paused = cross_state.emergency_pause.load(Ordering::Acquire) != 0;
    if paused || active == 0 { return None; }

    let daily_pnl = cross_state.daily_cross_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE;
    let daily_limit = cross_state.daily_loss_limit.load(Ordering::Acquire) as f64 / PRICE_SCALE;
    if daily_pnl < -daily_limit { return None; }

    let total_fee_bps = ((args.bfx_fee_bps + args.bnb_fee_bps + args.slippage_bps + latency_pad_bps) * 100.0) as i64;
    let min_profit_100 = (args.min_profit_bps * 100.0) as i64;
    
    let mut best: Option<ArbSignal> = None;

    for i in 0..active.min(MAX_CROSS_PAIRS) {
        let pair = &cross_state.pairs[i];
        if pair.enabled.load(Ordering::Relaxed) == 0 { continue; }

        let bfx_bid = pair.bitfinex.bid.load(Ordering::Relaxed) as i64;
        let bfx_ask = pair.bitfinex.ask.load(Ordering::Relaxed) as i64;
        let bnb_bid = pair.binance.bid.load(Ordering::Relaxed) as i64;
        let bnb_ask = pair.binance.ask.load(Ordering::Relaxed) as i64;

        let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        let bfx_age = now_ms.saturating_sub(pair.bitfinex.last_update_ms.load(Ordering::Relaxed));
        let bnb_age = now_ms.saturating_sub(pair.binance.last_update_ms.load(Ordering::Relaxed));
        if bfx_age > 5000 || bnb_age > 5000 { continue; }

        if bfx_bid <= 0 || bfx_ask <= 0 || bnb_bid <= 0 || bnb_ask <= 0 { continue; }

        let spread_1 = ((bnb_bid - bfx_ask) * 1_000_000) / bfx_ask; 
        let spread_2 = ((bfx_bid - bnb_ask) * 1_000_000) / bnb_ask; 

        let (direction, gross_bps_100, buy_price_i, sell_price_i) = if spread_1 > spread_2 {
            (ArbDirection::BuyBfxSellBnb, spread_1, bfx_ask, bnb_bid)
        } else {
            (ArbDirection::BuyBnbSellBfx, spread_2, bnb_ask, bfx_bid)
        };

        let net_bps_100 = gross_bps_100 - total_fee_bps;
        if net_bps_100 < min_profit_100 { continue; }

        let gross_bps = gross_bps_100 as f64 / 100.0;
        let net_bps = net_bps_100 as f64 / 100.0;
        let buy_price = buy_price_i as f64 / PRICE_SCALE;
        let sell_price = sell_price_i as f64 / PRICE_SCALE;

        let max_exposure = match direction {
            ArbDirection::BuyBfxSellBnb => cross_state.max_exposure_bitfinex_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE,
            ArbDirection::BuyBnbSellBfx => cross_state.max_exposure_binance_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE,
        };
        let trade_size = args.max_trade_usd.min(max_exposure);

        let signal = ArbSignal {
            pair_idx: i, pair_name: PAIR_NAMES[i].to_string(), direction,
            bfx_price: if direction == ArbDirection::BuyBfxSellBnb { buy_price } else { sell_price },
            bnb_price: if direction == ArbDirection::BuyBfxSellBnb { sell_price } else { buy_price },
            gross_bps, net_bps, size_usd: trade_size,
        };

        if best.as_ref().map_or(true, |b| signal.net_bps > b.net_bps) {
            best = Some(signal);
        }
    }
    best
}

struct NexusEngine {
    notifier: Arc<AsyncNotifier>,
    cross: *const CrossExchangeState,
    l2cmd: *const sniper_types::l2_command::L2CommandMatrix,
    args: Args,
    
    binance: Binance,
    http_client: reqwest::Client,
    
    authed: bool,
    last_exec_ms: u64,
    last_scan: Instant,
    total_signals: u64,
    total_trades: u64,
    
    itoa_buf: itoa::Buffer,
    ryu1: ryu::Buffer,
    ryu2: ryu::Buffer,
}

unsafe impl Send for NexusEngine {}
unsafe impl Sync for NexusEngine {}

impl SovereignEngine for NexusEngine {
    fn subscriptions(&mut self) -> Vec<String> {
        vec![] // Nexus only executes trades on Bitfinex Websocket, data is from shared mem
    }

    fn on_start(&mut self) -> Result<()> {
        let _lock_guard = sniper_types::lock::ensure_single_instance("nexus-core")?;
        std::fs::write("/tmp/nexus-core.pid", std::process::id().to_string())?;
        
        let mode = if self.args.paper { "PAPER" } else { "LIVE" };
        self.notifier.alert(format!("🪐 Nexus v{} ({}) ONLINE | min_profit={:.1}bps", VERSION, mode, self.args.min_profit_bps));
        Ok(())
    }

    fn on_auth(&mut self) {
        self.authed = true;
        info!(event = "authenticated", bot = "nexus");
    }

    fn on_market_message(&mut self, _payload: &[u8], _out_buf: &mut bytes::BytesMut) {
        // Ignored, data comes from mmap
    }

    fn on_system_event(&mut self, _value: &serde_json::Value, _out_buf: &mut bytes::BytesMut) {}

    fn on_loop(&mut self, out_buf: &mut bytes::BytesMut) {
        if !self.authed { return; }
        
        // Timer check
        if self.last_scan.elapsed().as_millis() < self.args.scan_interval_ms as u128 { return; }
        
        let l2cmd = unsafe { &*self.l2cmd };
        let cross_state = unsafe { &*self.cross };
        
        let latency_pad = {
            let (v1, ok1) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
            let pad = l2cmd.latency_padding_bps.load(Ordering::Relaxed);
            let kill = l2cmd.latency_killswitch.load(Ordering::Relaxed);
            let (v2, ok2) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
            if v1 == v2 && ok1 && ok2 {
                if kill == 1 { return; }
                pad.max(0) as f64
            } else { 0.0 }
        };

        let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        if now_ms - self.last_exec_ms < self.args.cooldown_ms { return; }

        self.last_scan = Instant::now();

        if let Some(signal) = scan_for_arb(cross_state, &self.args, latency_pad) {
            self.total_signals += 1;
            info!(event = "arb_detected", pair = signal.pair_name, direction = %signal.direction, net_bps = format!("{:.1}", signal.net_bps));

            let is_paper = cross_state.paper_mode.load(Ordering::Relaxed) == 1 || self.args.paper;
            if is_paper {
                self.last_exec_ms = now_ms;
                return;
            }

            let qty = if signal.bfx_price > 0.0 { signal.size_usd / signal.bfx_price } else { return };

            let (bfx_side, bfx_price) = match signal.direction {
                ArbDirection::BuyBfxSellBnb => ("BUY", signal.bfx_price),
                ArbDirection::BuyBnbSellBfx => ("SELL", signal.bfx_price),
            };
            let bfx_signed_qty = if bfx_side == "BUY" { qty } else { -qty };
            let bfx_symbol = BFX_SYMBOLS[signal.pair_idx];

            out_buf.extend_from_slice(b"[0,\"ox_multi\",null,[[\"on\",{\"gid\":");
            out_buf.extend_from_slice(self.itoa_buf.format(GID_NEXUS).as_bytes());
            out_buf.extend_from_slice(b",\"symbol\":\"");
            out_buf.extend_from_slice(bfx_symbol.as_bytes());
            out_buf.extend_from_slice(b"\",\"amount\":\"");
            out_buf.extend_from_slice(self.ryu1.format(bfx_signed_qty).as_bytes());
            out_buf.extend_from_slice(b"\",\"price\":\"");
            out_buf.extend_from_slice(self.ryu2.format(bfx_price).as_bytes());
            out_buf.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]]]");

            let bnb_side = match signal.direction {
                ArbDirection::BuyBfxSellBnb => OrderSide::Sell,
                ArbDirection::BuyBnbSellBfx => OrderSide::Buy,
            };
            let bnb_symbol = BNB_SYMBOLS[signal.pair_idx];
            let bnb_price_scaled = (signal.bnb_price * PRICE_SCALE) as i64;

            let bnb_order = OrderRequest {
                symbol: bnb_symbol.to_string(),
                side: bnb_side,
                amount: qty,
                price: bnb_price_scaled,
                order_type: OrderType::Ioc,
                gid: GID_NEXUS,
                flags: OrderFlags::default(),
            };

            if let Some(signed) = self.binance.new_order(&bnb_order) {
                let client = self.http_client.clone();
                let notifier2 = self.notifier.clone();
                let name = signal.pair_name.clone();
                let dir_str = signal.direction.to_string();
                let gross_bps = signal.gross_bps;
                let net_bps = signal.net_bps;
                let size_usd = signal.size_usd;

                tokio::spawn(async move {
                    let req = match signed.method {
                        "POST" => client.post(&signed.url),
                        "DELETE" => client.delete(&signed.url),
                        _ => client.get(&signed.url),
                    };
                    let res = req.header("X-MBX-APIKEY", &signed.api_key).send().await;
                    let bnb_ok = res.map(|r| r.status().is_success()).unwrap_or(false);
                    if bnb_ok {
                        notifier2.trade(name, dir_str, gross_bps, net_bps, size_usd);
                    } else {
                        notifier2.alert(format!("⚠️ LEG RISK! {} Binance leg failed!", name));
                    }
                });
            }

            self.total_trades += 1;
            self.last_exec_ms = now_ms;
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    tracing_subscriber::fmt::init();

    let cross_mmap = open_mmap_readonly(CROSS_EXCHANGE_PATH).context("cross_exchange.bin not found")?;
    let cross_state = unsafe { &*(cross_mmap.as_ptr() as *const CrossExchangeState) };

    let l2cmd_mmap = sniper_types::mmap_utils::init_mmap::<sniper_types::l2_command::L2SharedState>(
        sniper_types::l2_command::L2_COMMAND_PATH,
    )?;
    let l2cmd = unsafe { &*(l2cmd_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };

    let engine = NexusEngine {
        notifier: Arc::new(AsyncNotifier::new("nexus", "🪐")),
        cross: cross_state,
        l2cmd: &l2cmd.cmd,
        args,
        binance: Binance::new(),
        http_client: reqwest::Client::builder().timeout(Duration::from_secs(5)).build()?,
        authed: false,
        last_exec_ms: 0,
        last_scan: Instant::now(),
        total_signals: 0,
        total_trades: 0,
        itoa_buf: itoa::Buffer::new(),
        ryu1: ryu::Buffer::new(),
        ryu2: ryu::Buffer::new(),
    };

    let mut runner = SovereignRunner::new(engine, "Nexus");
    runner.run().await?;
    
    Ok(())
}
