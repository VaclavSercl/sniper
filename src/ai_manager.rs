use memmap2::MmapMut;
use std::fs::OpenOptions;
use std::time::{Duration, SystemTime};
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
    mid_price: f64, latency_ms: f64, position: f64, realized_pnl: f64, timestamp: u64,
    grid_base: f64, grid_step: f64, grid_size: u64,
}

async fn websocket_stream(mut socket: WebSocket, dashboard: Arc<Mutex<DashboardData>>) {
    let mut interval = tokio::time::interval(Duration::from_millis(200));
    loop {
        interval.tick().await;
        let data = { let db = dashboard.lock().expect("Lock failed"); serde_json::to_string(&*db).unwrap_or_default() };
        if socket.send(AxumMessage::Text(data.into())).await.is_err() { break; }
    }
}

async fn ws_handler(ws: WebSocketUpgrade, dashboard: Arc<Mutex<DashboardData>>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| websocket_stream(socket, dashboard))
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();
    println!("--- BEROUN AI MANAGER v5.1.0 (EXTREME) ---");
    let dashboard = Arc::new(Mutex::<DashboardData>::default());
    let d_clone = dashboard.clone();
    
    tokio::spawn(async move {
        let app = Router::new()
            .route("/", get(|| async { axum::response::Html(include_str!("../dashboard.html")) }))
            .route("/ws", get(move |ws| ws_handler(ws, d_clone)))
            .layer(CorsLayer::permissive());
        let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
        axum::serve(listener, app).await.unwrap();
    });

    let r_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;
    let e_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;
    
    let engine = unsafe { &*(e_mmap.as_ptr() as *const EngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const RiskState) };

    loop {
        let price = engine.best_bid.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let latency_ns = engine.latency_ns.load(Ordering::Acquire);
        let pos = engine.net_position.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        
        let g_step = risk.grid_step.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let g_size = risk.grid_size.load(Ordering::Acquire);

        {
            let mut db = dashboard.lock().expect("Lock failed");
            db.mid_price = price; db.latency_ms = latency_ns as f64 / 1_000_000.0;
            db.position = pos; db.realized_pnl = pnl;
            db.grid_base = price; db.grid_step = g_step; db.grid_size = g_size;
            db.timestamp = SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
        }

        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}
