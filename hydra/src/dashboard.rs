use axum::{
    extract::ws::{WebSocketUpgrade, WebSocket, Message},
    response::{Html, IntoResponse},
    routing::get,
    Router,
};
use tower_http::cors::CorsLayer;
use serde::Serialize;
use serde_json::Value;
use std::time::Duration;

pub trait BotSnapshot {
    fn name(&self) -> &'static str;
    fn mode(&self) -> String; // "LIVE", "PAPER", "PAUSED"
    fn pnl(&self) -> f64;
    fn position(&self) -> f64;
    fn detail(&self) -> Value;
}

// ═══════════════════════════════════════════════════════════
// DUMMY PROBES 
// Note: In Phase 9C, these will read the actual MMap structs
// ═══════════════════════════════════════════════════════════

struct HydraProbe;
impl BotSnapshot for HydraProbe {
    fn name(&self) -> &'static str { "Hydra" }
    fn mode(&self) -> String { "LIVE".to_string() }
    fn pnl(&self) -> f64 { 150.25 }
    fn position(&self) -> f64 { 0.5 }
    fn detail(&self) -> Value {
        serde_json::json!({
            "obi": rand::random::<f64>() * 2.0 - 1.0, // Kinetic OBI simulation
            "grid_levels": [
                {"price": 65000.0, "qty": 0.1, "side": "sell"},
                {"price": 64000.0, "qty": 0.1, "side": "buy"}
            ]
        })
    }
}

struct MoonshotProbe;
impl BotSnapshot for MoonshotProbe {
    fn name(&self) -> &'static str { "Moonshot" }
    fn mode(&self) -> String { "LIVE".to_string() }
    fn pnl(&self) -> f64 { 42.10 }
    fn position(&self) -> f64 { 0.0 }
    fn detail(&self) -> Value {
        let pairs: Vec<_> = (0..20).map(|i| {
            serde_json::json!({
                "id": i, 
                "pnl": (i as f64 - 10.0) + (rand::random::<f64>() * 5.0), 
                "status": if i % 3 == 0 || i % 5 == 0 { "active" } else { "inactive" }
            })
        }).collect();
        serde_json::json!({ "pairs": pairs })
    }
}

struct GridProbe;
impl BotSnapshot for GridProbe {
    fn name(&self) -> &'static str { "Grid" }
    fn mode(&self) -> String { "PAPER".to_string() }
    fn pnl(&self) -> f64 { -12.50 }
    fn position(&self) -> f64 { 0.25 }
    fn detail(&self) -> Value {
        let mid = 64500.0 + (rand::random::<f64>() * 100.0 - 50.0);
        serde_json::json!({
            "buy_levels": [(mid-100.0).trunc(), (mid-200.0).trunc(), (mid-300.0).trunc()],
            "sell_levels": [(mid+100.0).trunc(), (mid+200.0).trunc(), (mid+300.0).trunc()]
        })
    }
}

struct TrigonProbe;
impl BotSnapshot for TrigonProbe {
    fn name(&self) -> &'static str { "Trigon" }
    fn mode(&self) -> String { "PAUSED".to_string() }
    fn pnl(&self) -> f64 { 0.0 }
    fn position(&self) -> f64 { 0.0 }
    fn detail(&self) -> Value {
        let triangles: Vec<_> = (0..24).map(|i| {
            serde_json::json!({
                "id": i, 
                "profit_bps": (i as f64 - 12.0) * 2.0 + (rand::random::<f64>() * 4.0 - 2.0)
            })
        }).collect();
        serde_json::json!({ "triangles": triangles })
    }
}

struct NexusProbe;
impl BotSnapshot for NexusProbe {
    fn name(&self) -> &'static str { "Nexus" }
    fn mode(&self) -> String { "PAPER".to_string() }
    fn pnl(&self) -> f64 { 8.4 }
    fn position(&self) -> f64 { 0.0 }
    fn detail(&self) -> Value {
        let bfx = 64500.0 + (rand::random::<f64>() * 10.0);
        let bnb = 64510.0 + (rand::random::<f64>() * 10.0);
        serde_json::json!({
            "bfx_mid": bfx,
            "bnb_mid": bnb,
            "spread": bnb - bfx
        })
    }
}

// ═══════════════════════════════════════════════════════════

#[derive(Serialize)]
struct BotData {
    name: &'static str,
    mode: String,
    pnl: f64,
    position: f64,
    kelly_limit: f64,
    detail: Value,
}

#[derive(Serialize)]
struct PanopticonState {
    global_capital: f64,
    total_equity: f64,
    armada_pnl: f64,
    bots: Vec<BotData>,
}

async fn index_handler() -> Html<&'static str> {
    Html(include_str!("../dashboard.html"))
}

async fn ws_handler(ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(handle_socket)
}

async fn handle_socket(mut socket: WebSocket) {
    let probes: Vec<Box<dyn BotSnapshot + Send + Sync>> = vec![
        Box::new(HydraProbe),
        Box::new(MoonshotProbe),
        Box::new(GridProbe),
        Box::new(TrigonProbe),
        Box::new(NexusProbe),
    ];
    
    // Fractional Kelly Weights
    let weights = vec![
        ("Nexus", 0.25),
        ("Trigon", 0.25),
        ("Hydra", 0.20),
        ("Moonshot", 0.10),
        ("Grid", 0.10),
    ];

    loop {
        let global_capital_limit = std::fs::read_to_string("/home/wwwenda/sniper/state/armada_state.json")
            .ok()
            .and_then(|data| serde_json::from_str::<Value>(&data).ok())
            .and_then(|v| v["global_capital_limit"].as_f64())
            .unwrap_or(10000.0);
        
        let mut bots_data = Vec::new();
        let mut total_pnl = 0.0;
        
        for probe in &probes {
            let name = probe.name();
            let weight = weights.iter().find(|(n, _)| *n == name).map(|(_, w)| *w).unwrap_or(0.0);
            let kelly_limit = global_capital_limit * weight;
            let pnl = probe.pnl();
            total_pnl += pnl;
            
            bots_data.push(BotData {
                name,
                mode: probe.mode(),
                pnl,
                position: probe.position(),
                kelly_limit,
                detail: probe.detail(),
            });
        }
        
        let state = PanopticonState {
            global_capital: global_capital_limit,
            total_equity: 15420.0 + total_pnl,
            armada_pnl: total_pnl,
            bots: bots_data,
        };
        
        if let Ok(json) = serde_json::to_string(&state) {
            if socket.send(Message::Text(json.into())).await.is_err() {
                break;
            }
        }
        
        tokio::time::sleep(Duration::from_millis(50)).await; // 20 FPS
    }
}

#[tokio::main]
async fn main() {
    dotenvy::dotenv().ok();
    println!("--- 👁️ SOVEREIGN PANOPTICON v2.0 (Hive Mind Multiplexer) ---");
    let app = Router::new()
        .route("/", get(index_handler))
        .route("/ws", get(ws_handler))
        .layer(CorsLayer::permissive());

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
    println!("[PANOPTICON] Online at http://0.0.0.0:3000");
    axum::serve(listener, app).await.unwrap();
}
