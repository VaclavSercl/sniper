use memmap2::MmapMut;
use std::fs::OpenOptions;
use std::time::{Duration, SystemTime, Instant};
use std::sync::{Arc, Mutex, atomic::Ordering};
use axum::{extract::ws::{WebSocket, WebSocketUpgrade, Message as AxumMessage}, response::IntoResponse, routing::get, Router};
use tower_http::cors::CorsLayer;
use dotenvy::dotenv;
use anyhow::Result;

use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE_I};

fn init_mmap_ptr<T: Default>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

#[derive(Debug, Default, serde::Serialize, Clone)]
struct DashboardData {
    // Prices
    best_bid: f64,
    best_ask: f64,
    mid_price: f64,
    spread: f64,
    // Trading
    position: f64,
    inv_skew: f64,
    realized_pnl: f64,
    // Wallets
    wallet_btc: f64,
    wallet_usd: f64,
    // Grid
    grid_base: f64,
    grid_step: f64,
    grid_size: u64,
    // System
    latency_ms: f64,
    t2t_micros: u64,
    uptime_secs: u64,
    timestamp: u64,
    version: String,
    // Advanced
    micro_price: f64,
    current_skew: f64,
    // Order Book depth (top 5 levels for visualization)
    bid_prices: Vec<f64>,
    bid_amounts: Vec<f64>,
    ask_prices: Vec<f64>,
    ask_amounts: Vec<f64>,
}

async fn websocket_stream(mut socket: WebSocket, dashboard: Arc<Mutex<DashboardData>>) {
    let mut interval = tokio::time::interval(Duration::from_millis(200));
    loop {
        interval.tick().await;
        let data = {
            let db = dashboard.lock().expect("Lock failed");
            serde_json::to_string(&*db).unwrap_or_default()
        };
        if socket.send(AxumMessage::Text(data.into())).await.is_err() { break; }
    }
}

async fn ws_handler(ws: WebSocketUpgrade, dashboard: Arc<Mutex<DashboardData>>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| websocket_stream(socket, dashboard))
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();
    println!("--- BEROUN AI MANAGER v6.0.0 ---");
    let dashboard = Arc::new(Mutex::<DashboardData>::default());
    let d_clone = dashboard.clone();
    let start_time = Instant::now();

    tokio::spawn(async move {
        let app = Router::new()
            .route("/", get(|| async { axum::response::Html(include_str!("../dashboard.html")) }))
            .route("/ws", get(move |ws| ws_handler(ws, d_clone)))
            .layer(CorsLayer::permissive());
        let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
        println!("[DASHBOARD] Online at http://0.0.0.0:3000");
        axum::serve(listener, app).await.unwrap();
    });

    let r_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;
    let e_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;

    let engine = unsafe { &*(e_mmap.as_ptr() as *const EngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const RiskState) };
    let scale = PRICE_SCALE_I as f64;

    loop {
        let bb = engine.best_bid.load(Ordering::Acquire) as f64 / scale;
        let ba = engine.best_ask.load(Ordering::Acquire) as f64 / scale;
        let mid = (bb + ba) / 2.0;
        let spread = ba - bb;
        let latency_ns = engine.latency_ns.load(Ordering::Acquire);
        let t2t = engine.t2t_micros.load(Ordering::Acquire);
        let pos = engine.net_position.load(Ordering::Acquire) as f64 / scale;
        let pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / scale;
        let w_btc = engine.wallet_btc.load(Ordering::Acquire) as f64 / scale;
        let w_usd = engine.wallet_usd.load(Ordering::Acquire) as f64 / scale;
        let micro_p = engine.micro_price.load(Ordering::Acquire) as f64 / scale;
        let skew = engine.current_skew.load(Ordering::Acquire) as f64 / scale;

        let g_step = risk.grid_step.load(Ordering::Acquire) as f64 / scale;
        let g_size = risk.grid_size.load(Ordering::Acquire);
        let max_pos = risk.max_inv_delta.load(Ordering::Acquire) as f64 / scale;

        // Calculate inventory skew (mirror of main.rs logic)
        let inv_skew = if max_pos > 0.0 {
            let ratio = (pos / max_pos).clamp(-1.0, 1.0);
            -ratio * g_step * 2.0
        } else { 0.0 };

        // Order book depth (top 5)
        let mut bp = Vec::with_capacity(5);
        let mut ba_v = Vec::with_capacity(5);
        let mut ap = Vec::with_capacity(5);
        let mut aa = Vec::with_capacity(5);
        for i in 0..5 {
            let p = engine.bids[i].price.load(Ordering::Acquire) as f64 / scale;
            let a = engine.bids[i].amount.load(Ordering::Acquire) as f64 / scale;
            if p > 0.0 { bp.push(p); ba_v.push(a.abs()); }
            let p = engine.asks[i].price.load(Ordering::Acquire) as f64 / scale;
            let a = engine.asks[i].amount.load(Ordering::Acquire) as f64 / scale;
            if p > 0.0 { ap.push(p); aa.push(a.abs()); }
        }

        {
            let mut db = dashboard.lock().expect("Lock failed");
            db.best_bid = bb;
            db.best_ask = ba;
            db.mid_price = mid;
            db.spread = spread;
            db.position = pos;
            db.inv_skew = inv_skew;
            db.realized_pnl = pnl;
            db.wallet_btc = w_btc;
            db.wallet_usd = w_usd;
            db.grid_base = mid;
            db.grid_step = g_step;
            db.grid_size = g_size;
            db.latency_ms = latency_ns as f64 / 1_000_000.0;
            db.t2t_micros = t2t;
            db.uptime_secs = start_time.elapsed().as_secs();
            db.timestamp = SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
            db.version = "6.0.0".to_string();
            db.micro_price = micro_p;
            db.current_skew = skew;
            db.bid_prices = bp;
            db.bid_amounts = ba_v;
            db.ask_prices = ap;
            db.ask_amounts = aa;
        }

        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}
