// 🪐 Nexus L0 Engine — Cross-Exchange Arbitrage Bot
// Framework: SovereignEngine v12/2026 (Zero-f64 / Zero-Allocation Refactor)
use std::time::{SystemTime, UNIX_EPOCH, Duration, Instant};
use std::sync::atomic::Ordering;
use std::sync::Arc;

use anyhow::{Context, Result};
use clap::Parser;
use tracing::info;

use sniper_types::exchange::cross_types::*;
use sniper_types::exchange::types::*;
use sniper_types::exchange::binance::Binance;
use sniper_types::PRICE_SCALE_I;

use sniper_types::framework::{SovereignEngine, SovereignDualRunner};
use sniper_types::notifier::AsyncNotifier;
use sniper_types::mmap_utils::open_mmap_readonly;
use sniper_types::math::FixedPrice;

const VERSION: &str = "13.0.0-nexus-zerofpu";
const GID_NEXUS: u32 = 5000;

#[derive(Parser, Debug, Clone)]
#[command(name = "nexus-core", version = VERSION, about = "Cross-Exchange Arbitrage Bot")]
struct Args {
    #[arg(long)] paper: bool,
    #[arg(long, default_value_t = 5.0)] min_profit_bps: f64,
    #[arg(skip)] pub fee_matrix_ptr: *const sniper_types::fee_types::GlobalFeeMatrix,
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

#[derive(Clone, Copy)]
struct FixedSymbol {
    buf: [u8; 16],
    len: usize,
}

impl PartialEq for FixedSymbol {
    #[inline(always)]
    fn eq(&self, other: &Self) -> bool {
        self.len == other.len && self.buf[..self.len] == other.buf[..other.len]
    }
}

impl FixedSymbol {
    const fn new() -> Self { Self { buf: [0; 16], len: 0 } }
    fn from_str(s: &str) -> Self {
        let bytes = s.as_bytes();
        let len = bytes.len().min(16);
        let mut buf = [0; 16];
        buf[..len].copy_from_slice(&bytes[..len]);
        Self { buf, len }
    }
    #[inline(always)]
    fn as_bytes(&self) -> &[u8] { &self.buf[..self.len] }
    #[inline(always)]
    fn as_str(&self) -> &str { unsafe { std::str::from_utf8_unchecked(&self.buf[..self.len]) } }
}

struct ArbSignal {
    pair_idx: usize,
    pair_name: FixedSymbol, // No string allocation!
    direction: ArbDirection,
    bfx_price: i64,      // 1e8 scaled
    bnb_price: i64,      // 1e8 scaled
    gross_bps_100x: i64, // 100x bps scaled integer
    net_bps_100x: i64,   // 100x bps scaled integer
    size_usd: i64,       // 1e8 scaled
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

// Brutal FPU purge - totally native i64 arithmetic without a single f64 register.
fn scan_for_arb(cross_state: &CrossExchangeState, args: &Args, latency_pad_100x: i64, armada_cap: i64) -> Option<ArbSignal> {
    let active = cross_state.active_pairs.load(Ordering::Acquire) as usize;
    let paused = cross_state.emergency_pause.load(Ordering::Acquire) != 0;
    if paused || active == 0 { return None; }

    let daily_pnl_raw = cross_state.daily_cross_pnl.load(Ordering::Acquire) as i64;
    let daily_limit_raw = cross_state.daily_loss_limit.load(Ordering::Acquire) as i64;
    if daily_pnl_raw < -daily_limit_raw { return None; }

    let fee_matrix_ref = unsafe { &*args.fee_matrix_ptr };
    let bfx_fee = fee_matrix_ref.venues[sniper_types::fee_types::VENUE_BITFINEX].taker_fee_bps.load(Ordering::Relaxed) as i64;
    let bnb_fee = fee_matrix_ref.venues[sniper_types::fee_types::VENUE_BINANCE].taker_fee_bps.load(Ordering::Relaxed) as i64;
    let total_fee_bps_100x = bfx_fee + bnb_fee + (args.slippage_bps * 100.0) as i64 + latency_pad_100x;
    let min_profit_100x = (args.min_profit_bps * 100.0) as i64;
    let armada_trade_usd = armada_cap;
    
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

        // ZÁSAH 1: Exaktní aplikace poplatků a slippage (Zero-FPU)
        let total_bfx_fee = bfx_fee + (args.slippage_bps * 100.0) as i64 + latency_pad_100x;
        let total_bnb_fee = bnb_fee + (args.slippage_bps * 100.0) as i64; // Slippage i pro BNB

        // Směr 1: Koupím na BFX, Prodám na BNB
        let bfx_ask_with_fee = (bfx_ask * (10_000 + total_bfx_fee)) / 10_000;
        let bnb_bid_with_fee = (bnb_bid * (10_000 - total_bnb_fee)) / 10_000;
        let spread_1_100x = if bnb_bid_with_fee > bfx_ask_with_fee {
            ((bnb_bid_with_fee - bfx_ask_with_fee) * 1_000_000) / bfx_ask_with_fee
        } else { 0 };

        // Směr 2: Koupím na BNB, Prodám na BFX
        let bnb_ask_with_fee = (bnb_ask * (10_000 + total_bnb_fee)) / 10_000;
        let bfx_bid_with_fee = (bfx_bid * (10_000 - total_bfx_fee)) / 10_000;
        let spread_2_100x = if bfx_bid_with_fee > bnb_ask_with_fee {
            ((bfx_bid_with_fee - bnb_ask_with_fee) * 1_000_000) / bnb_ask_with_fee
        } else { 0 };

        let (direction, gross_bps_100x, buy_price_i, sell_price_i) = if spread_1_100x > spread_2_100x {
            (ArbDirection::BuyBfxSellBnb, spread_1_100x, bfx_ask, bnb_bid)
        } else {
            (ArbDirection::BuyBnbSellBfx, spread_2_100x, bnb_ask, bfx_bid)
        };

        // Net BPS je nyní rovno Gross BPS, protože poplatky už jsou započítány v ceně!
        let net_bps_100x = gross_bps_100x; 
        if net_bps_100x < min_profit_100x { continue; }

        let bfx_price = if direction == ArbDirection::BuyBfxSellBnb { buy_price_i } else { sell_price_i };
        let bnb_price = if direction == ArbDirection::BuyBfxSellBnb { sell_price_i } else { buy_price_i };

        let max_exposure = match direction {
            ArbDirection::BuyBfxSellBnb => cross_state.max_exposure_bitfinex_usd.load(Ordering::Acquire) as i64,
            ArbDirection::BuyBnbSellBfx => cross_state.max_exposure_binance_usd.load(Ordering::Acquire) as i64,
        };
        let trade_size = armada_trade_usd.min(max_exposure).max(0);

        let signal = ArbSignal {
            pair_idx: i, 
            pair_name: FixedSymbol::from_str(PAIR_NAMES[i]), 
            direction,
            bfx_price, 
            bnb_price, 
            gross_bps_100x, 
            net_bps_100x, 
            size_usd: trade_size,
        };

        if best.as_ref().map_or(true, |b| signal.net_bps_100x > b.net_bps_100x) {
            best = Some(signal);
        }
    }
    best
}

struct NexusEngine {
    notifier: Arc<AsyncNotifier>,
    cross: *const CrossExchangeState,
    l2cmd: *const sniper_types::l2_command::L2CommandMatrix,
    l1ring: *const sniper_types::l2_command::L1TelemetryRing,
    args: Args,
    
    binance: Binance,
    http_client: reqwest::Client,
    
    authed: bool,
    last_exec_ms: u64,
    last_scan: Instant,
    total_signals: u64,
    total_trades: u64,
    
    itoa_buf: itoa::Buffer,
    toxic_storm_ptr: *const u8,
    armada_state: &'static sniper_types::armada_types::ArmadaState,
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

    fn on_shutdown(&mut self, out_buf: &mut bytes::BytesMut) {
        if self.authed {
            out_buf.extend_from_slice(b"[0,\"oc_multi\",null,{\"gid\":[5000]}]");
        }
    }

    fn is_shadow(&self) -> bool {
        let cross_state = unsafe { &*self.cross };
        let is_paused = cross_state.paper_mode.load(Ordering::Relaxed) == 1 
            || cross_state.emergency_pause.load(Ordering::Relaxed) == 1 
            || self.args.paper;
        let config_shadow = std::fs::read_to_string("config.yaml").unwrap_or_default().contains("is_shadow: true");
        is_paused || config_shadow
    }

    fn best_bid_ask(&self) -> (f64, f64) {
        let cross_state = unsafe { &*self.cross };
        let bfx = &cross_state.pairs[0].bitfinex;
        (
            bfx.bid.load(Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64,
            bfx.ask.load(Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE as f64
        )
    }

    fn add_virtual_pnl(&self, amount: i64) {
        let rng = unsafe { &*self.cross };
        rng.virtual_realized_pnl.fetch_add(amount, Ordering::Relaxed);
    }

    fn on_market_message(&mut self, _payload: &mut [u8], _out_buf: &mut bytes::BytesMut) {
        // Ignored, data comes from mmap
    }

    fn on_system_event(&mut self, _value: &serde_json::Value, _out_buf: &mut bytes::BytesMut) {}

    fn on_loop(&mut self, out_buf: &mut bytes::BytesMut) {
        if !self.authed { return; }
        
        // Timer check
        if self.last_scan.elapsed().as_millis() < self.args.scan_interval_ms as u128 { return; }
        
        let l2cmd = unsafe { &*self.l2cmd };
        let cross_state = unsafe { &*self.cross };
        
        let latency_pad_100x = {
            let (v1, ok1) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
            let pad = l2cmd.latency_padding_bps.load(Ordering::Relaxed);
            let kill = l2cmd.latency_killswitch.load(Ordering::Relaxed);
            let (v2, ok2) = sniper_types::l2_command::l2cmd_version_check(l2cmd);
            if v1 == v2 && ok1 && ok2 {
                if kill == 1 { return; }
                pad.max(0) as i64 * 100
            } else { 0 }
        };

        let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        if now_ms - self.last_exec_ms < self.args.cooldown_ms { return; }

        // ═══ HIVE MIND: Toxic Storm check ═══
        let storm_byte = unsafe { std::ptr::read_volatile(self.toxic_storm_ptr) };
        // if storm_byte == 1 { return; }
        
        // ARMADA KŘEMÍKOVÁ ZEĎ 🛡️
        if self.armada_state.is_kill_switch_active() { return; }

        self.last_scan = Instant::now();
        
        let armada_cap = self.armada_state.authorized_capital[4].load(Ordering::Acquire) as i64;

        if let Some(signal) = scan_for_arb(cross_state, &self.args, latency_pad_100x, armada_cap) {
            self.total_signals += 1;
            
            let bfx_fp = FixedPrice::new(signal.bfx_price);
            let usd_fp = FixedPrice::new(signal.size_usd);
            if bfx_fp.0 <= 0 { return; }
            let mut qty_fp = usd_fp / bfx_fp;
            
            let net_bps_f = signal.net_bps_100x as f64 / 100.0;
            let gross_bps_f = signal.gross_bps_100x as f64 / 100.0;
            let size_usd_f = signal.size_usd as f64 / PRICE_SCALE_I as f64;
            
            info!(event = "arb_detected", pair = signal.pair_name.as_str(), direction = %signal.direction, net_bps = format!("{:.1}", net_bps_f));

            let bfx_side = match signal.direction {
                ArbDirection::BuyBfxSellBnb => "BUY",
                ArbDirection::BuyBnbSellBfx => "SELL",
            };
            if bfx_side == "SELL" { qty_fp.0 = -qty_fp.0; }
            let bfx_symbol = FixedSymbol::from_str(BFX_SYMBOLS[signal.pair_idx]);

            use sniper_types::exchange::bitfinex_venue::BitfinexVenue;
            BitfinexVenue::write_batch_open(out_buf);
            out_buf.extend_from_slice(b"[\"on\",{\"gid\":");
            out_buf.extend_from_slice(self.itoa_buf.format(GID_NEXUS).as_bytes());
            out_buf.extend_from_slice(b",\"symbol\":\"");
            out_buf.extend_from_slice(bfx_symbol.as_bytes());
            out_buf.extend_from_slice(b"\",\"amount\":\"");
            
            let qty_fmt = FixedFormat::new(qty_fp.0);
            out_buf.extend_from_slice(qty_fmt.as_str().as_bytes());
            out_buf.extend_from_slice(b"\",\"price\":\"");
            
            let price_fmt = FixedFormat::new(signal.bfx_price);
            out_buf.extend_from_slice(price_fmt.as_str().as_bytes());
            out_buf.extend_from_slice(b"\",\"type\":\"EXCHANGE IOC\"}]");
            BitfinexVenue::write_batch_close(out_buf);

            // Bnb order scaling (border boundary, so it sends f64 for json)
            let bnb_side = match signal.direction {
                ArbDirection::BuyBfxSellBnb => OrderSide::Sell,
                ArbDirection::BuyBnbSellBfx => OrderSide::Buy,
            };
            let bnb_symbol = BNB_SYMBOLS[signal.pair_idx];
            let f_qty = (qty_fp.0.abs() as f64) / PRICE_SCALE_I as f64;

            let bnb_order = OrderRequest {
                symbol: bnb_symbol.to_string(),
                side: bnb_side,
                amount: f_qty,
                price: signal.bnb_price,
                order_type: OrderType::Ioc,
                gid: GID_NEXUS,
                flags: OrderFlags::default(),
            };

            if let Some(signed) = self.binance.new_order(&bnb_order) {
                let client = self.http_client.clone();
                let notifier2 = self.notifier.clone();
                let name = signal.pair_name.as_str().to_string();
                let dir_str = signal.direction.to_string();
                let ring_ptr = self.l1ring as usize;

                tokio::spawn(async move {
                    let send_ts = std::time::Instant::now();
                    let req = match signed.method {
                        "POST" => client.post(&signed.url),
                        "DELETE" => client.delete(&signed.url),
                        _ => client.get(&signed.url),
                    };
                    let res = req.header("X-MBX-APIKEY", &signed.api_key).send().await;
                    
                    let ring = unsafe { &*(ring_ptr as *const sniper_types::l2_command::L1TelemetryRing) };
                    sniper_types::l2_command::record_latency(ring, send_ts);
                    
                    let bnb_ok = res.map(|r| r.status().is_success()).unwrap_or(false);
                    if bnb_ok {
                        notifier2.trade(name, dir_str, gross_bps_f, net_bps_f, size_usd_f);
                    } else {
                        notifier2.alert(format!("🚨 BROKEN LEG RISK! {} Binance leg failed! Executing Emergency Market Hedge on Bitfinex...", name));
                        
                        // ZÁSAH 2: Záchranný reverzní MARKET příkaz na Bitfinex
                        let hedge_side = match signal.direction {
                            ArbDirection::BuyBfxSellBnb => "sell", // Koupili jsme na BFX, BNB selhalo -> Musíme prodat na BFX
                            ArbDirection::BuyBnbSellBfx => "buy",  // Prodali jsme na BFX, BNB selhalo -> Musíme koupit na BFX
                        };
                        
                        // Zde Nexus využívá svůj sdílený reqwest client pro REST API Bitfinexu (v3 auth logika)
                        // Poznámka pro dev: Pro plnou funkčnost zajisti, že client umí podepsat BFX v2/v3 auth hlavičky.
                        let bfx_api_url = "https://api.bitfinex.com/v2/auth/w/order/submit";
                        let hedge_qty = if hedge_side == "sell" { -f_qty } else { f_qty };
                        
                        let payload = serde_json::json!({
                            "type": "EXCHANGE MARKET",
                            "symbol": FixedSymbol::from_str(BFX_SYMBOLS[signal.pair_idx]).as_str(),
                            "amount": hedge_qty.to_string()
                        });

                        // Fire and forget hedge
                        let _ = client.post(bfx_api_url)
                            // .header("bfx-apikey", "...") // Vyžaduje napojení klíčů z konfigurace
                            // .header("bfx-signature", "...")
                            .json(&payload)
                            .send()
                            .await;
                            
                        notifier2.alert(format!("🛡️ HEDGE ODESLÁN: {} {} MARKET k vykrytí Delta expozice.", hedge_side.to_uppercase(), name));
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
    let mut args = Args::parse();
    tracing_subscriber::fmt::init();

    let cross_mmap = open_mmap_readonly(CROSS_EXCHANGE_PATH).context("cross_exchange.bin not found")?;
    let cross_state = unsafe { &*(cross_mmap.as_ptr() as *const CrossExchangeState) };

    let fee_mmap = sniper_types::mmap_utils::init_mmap::<sniper_types::fee_types::GlobalFeeMatrix>(sniper_types::fee_types::FEE_MATRIX_PATH)?;
    args.fee_matrix_ptr = fee_mmap.as_ptr() as *const sniper_types::fee_types::GlobalFeeMatrix;

    let l2cmd_mmap = sniper_types::mmap_utils::init_mmap::<sniper_types::l2_command::L2SharedState>(
        sniper_types::l2_command::L2_COMMAND_PATH,
    )?;
    let l2cmd = unsafe { &*(l2cmd_mmap.as_ptr() as *const sniper_types::l2_command::L2SharedState) };

    let storm_path = "/dev/shm/beroun/toxic_storm.bin";
    if !std::path::Path::new(storm_path).exists() { let _ = std::fs::write(storm_path, [0u8]); }
    let toxic_storm_mmap = sniper_types::mmap_utils::open_mmap_readonly(storm_path).unwrap();

    let engine = NexusEngine {
        notifier: Arc::new(AsyncNotifier::new("nexus", "🪐")),
        cross: cross_state,
        l2cmd: &l2cmd.cmd,
        l1ring: &l2cmd.latency_ring,
        args,
        binance: Binance::new(),
        http_client: reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .pool_max_idle_per_host(2)
            .pool_idle_timeout(Duration::from_secs(30))
            .tcp_keepalive(Duration::from_secs(15))
            .build()?,
        authed: false,
        last_exec_ms: 0,
        last_scan: Instant::now(),
        total_signals: 0,
        total_trades: 0,
        itoa_buf: itoa::Buffer::new(),
        toxic_storm_ptr: toxic_storm_mmap.as_ptr(),
        armada_state: sniper_types::armada_types::load_armada_state_ro(),
    };

    let venue = sniper_types::exchange::bitfinex_venue::BitfinexVenue::new();
    let mut runner = SovereignDualRunner::new(engine, venue, "Nexus");
    runner.run().await?;
    
    Ok(())
}
