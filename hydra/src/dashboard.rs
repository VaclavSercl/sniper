/// 👁️ PANOPTICON v1.0 — WebSocket Hyper-Bridge
///
/// Architecture: MMap → Rust (60 FPS poll) → WebSocket → Browser Canvas
///
/// Zero HTML rendering. Zero DOM thrashing. Pure data pipeline.
/// Reads EngineState + RiskState via zero-copy mmap pointer cast,
/// serializes to compact JSON, pushes over axum WebSocket.
///
/// Frontend: Vanilla JS + Canvas 2D (hardware-accelerated gauges).

use memmap2::MmapMut;
use std::fs::OpenOptions;
use std::time::{Duration, Instant};
use std::sync::atomic::Ordering;
use axum::{
    extract::{State, ws::{WebSocketUpgrade, WebSocket, Message}},
    response::{Html, IntoResponse},
    routing::get,
    Router,
};
use tower_http::cors::CorsLayer;
use dotenvy::dotenv;
use anyhow::Result;
use serde::Serialize;

use sniper_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE, PRICE_SCALE_I};

const WS_TICK_MS: u64 = 50; // 20 FPS baseline (sweet spot: fast enough for gauges, light on CPU)

fn init_mmap_ptr<T: Default>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).truncate(false).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

// ═══ PAYLOAD — The only data we send over the wire ═══
#[derive(Serialize)]
struct Payload {
    // Core prices
    ts: u64,
    bid: f64,
    ask: f64,
    mid: f64,
    spread: f64,
    micro: f64,

    // Wallets & equity
    w_usd: f64,
    w_btc: f64,
    equity: f64,

    // Position
    pos: f64,
    pnl: f64,
    fills: u64,

    // Grid
    grid_step: f64,
    grid_size: u64,

    // Latency
    t2t: u64,
    uptime_s: u64,

    // AI Intelligence
    obi: f64,
    l1_conf: f64,
    l1_skew: f64,
    regime: u64,
    toxic: u64,
    freeze: bool,
    paused: bool,
    shadow: bool,
    shadow_pnl: f64,

    // Ghost
    ghost_pct: f64,
    ghost_inj: u64,

    // Macro
    macro_bias: f64,
    fng: u64,

    // Delta Lead
    bnb_mid: f64,
    delta_bps: f64,
    delta_sig: i64,

    // Fee
    maker_fee: f64,
    taker_fee: f64,

    // Kelly / Risk
    max_pos: f64,
    auth_capital: f64,
    daily_loss_limit: f64,

    // Order book (top 5)
    bids: Vec<[f64; 2]>,
    asks: Vec<[f64; 2]>,

    // Sparkline (last price for accumulation on client)
    last_buy: f64,
    last_sell: f64,
}

// ═══ HANDLERS ═══
async fn index_handler() -> Html<&'static str> {
    Html(include_str!("../dashboard.html"))
}

async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<WsState>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_socket(socket, state))
}

async fn handle_socket(mut socket: WebSocket, state: WsState) {
    let scale = PRICE_SCALE;
    let scale_i = PRICE_SCALE_I as f64;
    let engine = state.engine;
    let risk = state.risk;
    let start = state.start_time;

    loop {
        // Read MMap atomically
        let bb = engine.best_bid.load(Ordering::Relaxed) as f64 / scale;
        let ba = engine.best_ask.load(Ordering::Relaxed) as f64 / scale;
        let mid = (bb + ba) / 2.0;
        let pos = engine.net_position.load(Ordering::Relaxed) as f64 / scale;
        let pnl = engine.realized_pnl.load(Ordering::Relaxed) as f64 / scale;
        let w_btc = engine.wallet_btc.load(Ordering::Relaxed) as f64 / scale;
        let w_usd = engine.wallet_usd.load(Ordering::Relaxed) as f64 / scale;
        let micro = engine.micro_price.load(Ordering::Relaxed) as f64 / scale;
        let g_step = risk.grid_step.load(Ordering::Relaxed) as f64 / scale;
        let g_size = risk.grid_size.load(Ordering::Relaxed);
        let max_pos_raw = risk.max_inv_delta.load(Ordering::Relaxed) as f64 / scale;
        let t2t = engine.t2t_micros.load(Ordering::Relaxed);
        let fills = engine.session_fill_count.load(Ordering::Relaxed);

        let obi = engine.l2_imbalance.load(Ordering::Relaxed) as f64 / scale;
        let l1_conf = engine.l1_confidence_score.load(Ordering::Relaxed) as f64 / 10000.0;
        let l1_skew = engine.l1_skew_adjustment.load(Ordering::Relaxed) as f64 / scale;
        let regime = engine.l2_regime_id.load(Ordering::Relaxed);
        let toxic = engine.toxic_flow_hits.load(Ordering::Relaxed);
        let freeze_until = engine.sweep_freeze_until.load(Ordering::Relaxed);
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;
        let freeze = freeze_until > now_ms;
        let paused = risk.paused.load(Ordering::Relaxed) != 0;
        let shadow = engine.is_shadow_mode.load(Ordering::Relaxed) == 1;
        let shadow_pnl_v = engine.shadow_pnl.load(Ordering::Relaxed) as f64 / scale;
        let ghost_pct = engine.ghost_transparency.load(Ordering::Relaxed) as f64 / 10000.0;
        let ghost_inj = engine.ghost_injections.load(Ordering::Relaxed);
        let macro_bias = engine.macro_bias.load(Ordering::Relaxed) as f64 / 10000.0;
        let fng = engine.macro_fear_greed.load(Ordering::Relaxed);
        let bnb_mid = engine.binance_mid_price.load(Ordering::Relaxed) as f64 / scale;
        let delta_bps_raw = engine.delta_lead_raw_bps.load(Ordering::Relaxed) as f64 / 100.0;
        let delta_sig = engine.delta_lead_signal.load(Ordering::Relaxed);
        let maker_f = engine.maker_fee_bps.load(Ordering::Relaxed) as f64 / 10000.0 * 100.0;
        let taker_f = engine.taker_fee_bps.load(Ordering::Relaxed) as f64 / 10000.0 * 100.0;
        let auth_cap = risk.authorized_capital.load(Ordering::Relaxed) as f64 / scale;
        let daily_ll = risk.daily_loss_limit.load(Ordering::Relaxed) as f64 / scale;
        let last_buy = engine.last_buy_price.load(Ordering::Relaxed) as f64 / scale;
        let last_sell = engine.last_sell_price.load(Ordering::Relaxed) as f64 / scale;

        // Order book top 5
        let mut bid_levels = Vec::with_capacity(5);
        let mut ask_levels = Vec::with_capacity(5);
        for i in 0..5 {
            let p = engine.bids[i].price.load(Ordering::Relaxed) as f64 / scale;
            let a = (engine.bids[i].amount.load(Ordering::Relaxed) as f64 / scale).abs();
            if p > 0.0 { bid_levels.push([p, a]); }
            let p = engine.asks[i].price.load(Ordering::Relaxed) as f64 / scale;
            let a = (engine.asks[i].amount.load(Ordering::Relaxed) as f64 / scale).abs();
            if p > 0.0 { ask_levels.push([p, a]); }
        }

        let payload = Payload {
            ts: now_ms,
            bid: bb, ask: ba, mid, spread: ba - bb, micro,
            w_usd, w_btc, equity: w_usd + (w_btc * mid),
            pos, pnl, fills,
            grid_step: g_step, grid_size: g_size,
            t2t, uptime_s: start.elapsed().as_secs(),
            obi, l1_conf, l1_skew, regime,
            toxic, freeze, paused, shadow, shadow_pnl: shadow_pnl_v,
            ghost_pct, ghost_inj,
            macro_bias, fng,
            bnb_mid, delta_bps: delta_bps_raw, delta_sig,
            maker_fee: maker_f, taker_fee: taker_f,
            max_pos: max_pos_raw, auth_capital: auth_cap, daily_loss_limit: daily_ll,
            bids: bid_levels, asks: ask_levels,
            last_buy, last_sell,
        };

        let json = match serde_json::to_string(&payload) {
            Ok(j) => j,
            Err(_) => continue,
        };

        if socket.send(Message::Text(json.into())).await.is_err() {
            break; // Client disconnected
        }

        tokio::time::sleep(Duration::from_millis(WS_TICK_MS)).await;
    }
}

// ═══ SHARED STATE (passed to WS handler) ═══
#[derive(Clone)]
struct WsState {
    engine: &'static EngineState,
    risk: &'static RiskState,
    start_time: Instant,
}

// SAFETY: EngineState/RiskState use only atomics — safe to share across threads
unsafe impl Send for WsState {}
unsafe impl Sync for WsState {}

// ═══ MAIN ═══
#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();

    // Single-instance lock
    let lock_file = std::fs::File::create("/tmp/beroun-dashboard.lock")
        .expect("Failed to create dashboard lock file");
    use fs2::FileExt as Fs2FileExt;
    if lock_file.try_lock_exclusive().is_err() {
        eprintln!("[PANOPTICON] Another instance already running — aborting.");
        std::process::exit(1);
    }
    let _lock_guard = lock_file;

    println!("--- 👁️ PANOPTICON v1.0 (WebSocket Hyper-Bridge) ---");

    // MMap setup — leak to get 'static lifetime (process-lifetime mapping)
    let e_mmap = Box::leak(Box::new(init_mmap_ptr::<EngineState>(ENGINE_STATE_PATH)?));
    let r_mmap = Box::leak(Box::new(init_mmap_ptr::<RiskState>(RISK_STATE_PATH)?));
    let engine: &'static EngineState = unsafe { &*(e_mmap.as_ptr() as *const EngineState) };
    let risk: &'static RiskState = unsafe { &*(r_mmap.as_ptr() as *const RiskState) };

    let state = WsState {
        engine,
        risk,
        start_time: Instant::now(),
    };

    let app = Router::new()
        .route("/", get(index_handler))
        .route("/ws", get(ws_handler))
        .with_state(state)
        .layer(CorsLayer::permissive());

    let listener = {
        let mut bound = None;
        for attempt in 1..=15 {
            let socket = match tokio::net::TcpSocket::new_v4() {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("[PANOPTICON] Socket creation failed: {e}");
                    tokio::time::sleep(Duration::from_secs(3)).await;
                    continue;
                }
            };
            let _ = socket.set_reuseaddr(true);
            #[cfg(unix)]
            { let _ = socket.set_reuseport(true); }
            match socket
                .bind("0.0.0.0:3000".parse().unwrap())
                .and_then(|()| socket.listen(1024))
            {
                Ok(l) => { bound = Some(l); break; }
                Err(e) => {
                    eprintln!("[PANOPTICON] Bind attempt {}/15 failed: {} — retrying in 3s", attempt, e);
                    tokio::time::sleep(Duration::from_secs(3)).await;
                }
            }
        }
        bound.expect("[PANOPTICON] FATAL: Cannot bind :3000")
    };

    println!("[PANOPTICON] Online at http://0.0.0.0:3000 (WebSocket Hyper-Bridge, 20 FPS)");
    axum::serve(listener, app).await?;
    Ok(())
}
