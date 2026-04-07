use axum::{
    extract::ws::{WebSocketUpgrade, WebSocket, Message},
    response::{Html, IntoResponse},
    routing::get,
    Router,
};
use tower_http::cors::CorsLayer;
use serde::Serialize;
use serde_json::Value;
use std::time::{Duration, Instant};
use std::sync::atomic::Ordering;
use std::path::Path;
use std::process::Command;
use std::sync::{Arc, RwLock, LazyLock};
use futures_util::{StreamExt, SinkExt};

#[derive(Clone, Serialize, Default)]
pub struct ServerHealth {
    pub cpu_load: String,
    pub ram_pct: f64,
    pub gpu_temp: i32,
    pub gpu_load: i32,
    pub vram_mb: i32,
    pub disk_root_pct: f64,
    pub disk_shm_pct: f64,
}

static SERVER_HEALTH: LazyLock<Arc<RwLock<ServerHealth>>> = LazyLock::new(|| {
    Arc::new(RwLock::new(ServerHealth::default()))
});

pub fn spawn_hardware_monitor() {
    tokio::spawn(async move {
        loop {
            let mut new_state = ServerHealth::default();

            if let Ok(load) = std::fs::read_to_string("/proc/loadavg") {
                new_state.cpu_load = load.split_whitespace().next().unwrap_or("0.0").to_string();
            }

            if let Ok(meminfo) = std::fs::read_to_string("/proc/meminfo") {
                let mut total = 0.0;
                let mut avail = 0.0;
                for line in meminfo.lines() {
                    if line.starts_with("MemTotal:") {
                        total = line.split_whitespace().nth(1).unwrap_or("0").parse().unwrap_or(0.0);
                    }
                    if line.starts_with("MemAvailable:") {
                        avail = line.split_whitespace().nth(1).unwrap_or("0").parse().unwrap_or(0.0);
                    }
                }
                if total > 0.0 {
                    new_state.ram_pct = ((total - avail) / total) * 100.0;
                }
            }

            if let Ok(output) = Command::new("nvidia-smi")
                .args(&["--query-gpu=temperature.gpu,memory.used,utilization.gpu", "--format=csv,noheader,nounits"])
                .output()
            {
                let stdout = String::from_utf8_lossy(&output.stdout);
                let vals: Vec<&str> = stdout.trim().split(", ").collect();
                if vals.len() >= 3 {
                    new_state.gpu_temp = vals[0].parse().unwrap_or(0);
                    new_state.vram_mb = vals[1].parse().unwrap_or(0);
                    new_state.gpu_load = vals[2].parse().unwrap_or(0);
                }
            }

            if let Ok(output) = Command::new("df").args(&["-P", "/", "/dev/shm"]).output() {
                let stdout = String::from_utf8_lossy(&output.stdout);
                for line in stdout.lines().skip(1) {
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    if parts.len() >= 6 {
                        let pct = parts[4].trim_end_matches('%').parse().unwrap_or(0.0);
                        if parts[5] == "/" {
                            new_state.disk_root_pct = pct;
                        } else if parts[5] == "/dev/shm" {
                            new_state.disk_shm_pct = pct;
                        }
                    }
                }
            }

            if let Ok(mut lock) = SERVER_HEALTH.write() {
                *lock = new_state;
            }

            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    });
}

use sniper_types::{
    EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE,
};
use sniper_types::moonshot_types::{MoonshotEngineState, MOONSHOT_ENGINE_PATH, MOONSHOT_RISK_PATH, MoonshotRiskState};
use sniper_types::grid_types::{GridEngineState, GRID_ENGINE_PATH, GRID_RISK_PATH, GridRiskState};
use sniper_types::trigon_types::{TrigonEngineState, TRIGON_ENGINE_PATH, TRIGON_RISK_PATH, TrigonRiskState};
use sniper_types::exchange::cross_types::{CrossExchangeState, CROSS_EXCHANGE_PATH};
use sniper_types::mmap_utils::{open_mmap_readonly, open_mmap_readwrite};

pub trait BotSnapshot {
    fn name(&self) -> &'static str;
    fn mode(&self) -> String; // "LIVE", "PAPER", "PAUSED", "OFFLINE"
    fn pnl(&self) -> f64;
    fn position(&self) -> f64;
    fn detail(&self) -> Value;
}

// ═══════════════════════════════════════════════════════════
// MMap Loader Helper
// ═══════════════════════════════════════════════════════════
fn load_mmap<T>(path: &str) -> Option<&'static T> {
    if !Path::new(path).exists() {
        return None;
    }
    match open_mmap_readonly(path) {
        Ok(mmap) => {
            let leaked = Box::leak(Box::new(mmap));
            Some(unsafe { &*(leaked.as_ptr() as *const T) })
        }
        Err(_) => None,
    }
}

fn load_mmap_mut<T>(path: &str) -> Option<&'static T> {
    if !Path::new(path).exists() {
        return None;
    }
    match open_mmap_readwrite(path) {
        Ok(mmap) => {
            let leaked = Box::leak(Box::new(mmap));
            Some(unsafe { &*(leaked.as_mut_ptr() as *const T) })
        }
        Err(_) => None,
    }
}

pub struct MutTies {
    pub hydra: Option<&'static RiskState>,
    pub moonshot: Option<&'static MoonshotRiskState>,
    pub grid: Option<&'static GridRiskState>,
    pub trigon: Option<&'static TrigonRiskState>,
    pub nexus: Option<&'static CrossExchangeState>,
}
unsafe impl Send for MutTies {}
unsafe impl Sync for MutTies {}

static ACTIVE_MUT_TIES: LazyLock<Arc<MutTies>> = LazyLock::new(|| {
    Arc::new(MutTies {
        hydra: load_mmap_mut(RISK_STATE_PATH),
        moonshot: load_mmap_mut(MOONSHOT_RISK_PATH),
        grid: load_mmap_mut(GRID_RISK_PATH),
        trigon: load_mmap_mut(TRIGON_RISK_PATH),
        nexus: load_mmap_mut(CROSS_EXCHANGE_PATH),
    })
});

static GLOBAL_KELLY_WEIGHTS: LazyLock<Arc<RwLock<std::collections::HashMap<String, f64>>>> = LazyLock::new(|| {
    let mut m = std::collections::HashMap::new();
    m.insert("Nexus".to_string(), 0.25);
    m.insert("Trigon".to_string(), 0.25);
    m.insert("Hydra".to_string(), 0.20);
    m.insert("Moonshot".to_string(), 0.10);
    m.insert("Grid".to_string(), 0.10);
    Arc::new(RwLock::new(m))
});

// ═══════════════════════════════════════════════════════════
// HYDRA PROBE
// ═══════════════════════════════════════════════════════════
struct HydraProbe {
    engine: Option<&'static EngineState>,
    risk: Option<&'static RiskState>,
}
impl BotSnapshot for HydraProbe {
    fn name(&self) -> &'static str { "Hydra" }
    fn mode(&self) -> String {
        let (Some(_e), Some(r)) = (self.engine, self.risk) else { return "OFFLINE".to_string() };
        if r.paused.load(Ordering::Relaxed) != 0 { "PAUSED".to_string() } else { "LIVE".to_string() }
    }
    fn pnl(&self) -> f64 {
        self.engine.map(|e| {
            let r = e.realized_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            let v = e.virtual_realized_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            r + v
        }).unwrap_or(0.0)
    }
    fn position(&self) -> f64 {
        self.engine.map(|e| e.net_position.load(Ordering::Relaxed) as f64 / PRICE_SCALE).unwrap_or(0.0)
    }
    fn detail(&self) -> Value {
        if let Some(e) = self.engine {
            let obi = e.l2_imbalance.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            
            let mut levels = Vec::new();
            for i in 0..5.min(e.asks.len()) {
                let p = e.asks[i].price.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
                let a = e.asks[i].amount.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
                if p > 0.0 { levels.push(serde_json::json!({"price": p, "qty": a.abs(), "side": "sell"})); }
            }
            for i in 0..5.min(e.bids.len()) {
                let p = e.bids[i].price.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
                let a = e.bids[i].amount.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
                if p > 0.0 { levels.push(serde_json::json!({"price": p, "qty": a.abs(), "side": "buy"})); }
            }

            serde_json::json!({
                "obi": obi,
                "grid_levels": levels
            })
        } else {
            serde_json::json!({"obi": 0.0, "grid_levels": []})
        }
    }
}

// ═══════════════════════════════════════════════════════════
// MOONSHOT PROBE
// ═══════════════════════════════════════════════════════════
struct MoonshotProbe {
    engine: Option<&'static MoonshotEngineState>,
    risk: Option<&'static MoonshotRiskState>,
}
impl BotSnapshot for MoonshotProbe {
    fn name(&self) -> &'static str { "Moonshot" }
    fn mode(&self) -> String {
        let (Some(_e), Some(r)) = (self.engine, self.risk) else { return "OFFLINE".to_string() };
        if r.global_paused.load(Ordering::Relaxed) != 0 { "PAUSED".to_string() } else { "LIVE".to_string() }
    }
    fn pnl(&self) -> f64 {
        self.engine.map(|e| {
            let r = e.daily_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            let v = e.virtual_realized_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            r + v
        }).unwrap_or(0.0)
    }
    fn position(&self) -> f64 { 0.0 } // Aggregate position isn't meaningful here
    fn detail(&self) -> Value {
        if let Some(e) = self.engine {
            let pairs: Vec<_> = e.pairs.iter().enumerate().map(|(i, p)| {
                let active = p.active.load(Ordering::Relaxed);
                let pnl = p.realized_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
                serde_json::json!({
                    "id": i + 1,
                    "pnl": pnl,
                    "status": if active == 1 { "active" } else { "inactive" }
                })
            }).collect();
            serde_json::json!({ "pairs": pairs })
        } else {
            serde_json::json!({ "pairs": [] })
        }
    }
}

// ═══════════════════════════════════════════════════════════
// GRID PROBE
// ═══════════════════════════════════════════════════════════
struct GridProbe {
    engine: Option<&'static GridEngineState>,
    risk: Option<&'static GridRiskState>,
}
impl BotSnapshot for GridProbe {
    fn name(&self) -> &'static str { "Grid" }
    fn mode(&self) -> String {
        let (Some(_e), Some(r)) = (self.engine, self.risk) else { return "OFFLINE".to_string() };
        if r.global_paused.load(Ordering::Relaxed) != 0 { "PAUSED".to_string() } else { "LIVE".to_string() }
    }
    fn pnl(&self) -> f64 {
        self.engine.map(|e| {
            let r = e.realized_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            let v = e.virtual_realized_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            r + v
        }).unwrap_or(0.0)
    }
    fn position(&self) -> f64 {
        self.engine.map(|e| e.net_position.load(Ordering::Relaxed) as f64 / PRICE_SCALE).unwrap_or(0.0)
    }
    fn detail(&self) -> Value {
        if let Some(e) = self.engine {
            let buy_levels: Vec<f64> = e.buy_levels.iter()
                .map(|l| l.price.load(Ordering::Relaxed) as f64 / PRICE_SCALE).filter(|&p| p > 0.0).collect();
            let sell_levels: Vec<f64> = e.sell_levels.iter()
                .map(|l| l.price.load(Ordering::Relaxed) as f64 / PRICE_SCALE).filter(|&p| p > 0.0).collect();
            
            serde_json::json!({
                "buy_levels": buy_levels,
                "sell_levels": sell_levels
            })
        } else {
            serde_json::json!({"buy_levels": [], "sell_levels": []})
        }
    }
}

// ═══════════════════════════════════════════════════════════
// TRIGON PROBE
// ═══════════════════════════════════════════════════════════
struct TrigonProbe {
    engine: Option<&'static TrigonEngineState>,
    risk: Option<&'static TrigonRiskState>,
}
impl BotSnapshot for TrigonProbe {
    fn name(&self) -> &'static str { "Trigon" }
    fn mode(&self) -> String {
        let (Some(_e), Some(r)) = (self.engine, self.risk) else { return "OFFLINE".to_string() };
        if r.global_paused.load(Ordering::Relaxed) != 0 { "PAUSED".to_string() } else { "LIVE".to_string() }
    }
    fn pnl(&self) -> f64 {
        self.engine.map(|e| {
            let r = e.total_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            let v = e.virtual_realized_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            r + v
        }).unwrap_or(0.0)
    }
    fn position(&self) -> f64 { 0.0 }
    fn detail(&self) -> Value {
        if let Some(e) = self.engine {
            let triangles: Vec<_> = e.triangles.iter().enumerate().map(|(i, t)| {
                // assume profit_bps is bps * 10 or similar; let's divide by 100.0 to get direct format visually.
                let bps = t.profit_bps.load(Ordering::Relaxed) as f64 / 100.0;
                serde_json::json!({
                    "id": i + 1,
                    "profit_bps": bps
                })
            }).collect();
            serde_json::json!({ "triangles": triangles })
        } else {
            serde_json::json!({ "triangles": [] })
        }
    }
}

// ═══════════════════════════════════════════════════════════
// NEXUS PROBE (Cross Exchange Bridge)
// ═══════════════════════════════════════════════════════════
struct NexusProbe {
    cross: Option<&'static CrossExchangeState>,
}
impl BotSnapshot for NexusProbe {
    fn name(&self) -> &'static str { "Nexus" }
    fn mode(&self) -> String {
        let Some(c) = self.cross else { return "OFFLINE".to_string() };
        if c.emergency_pause.load(Ordering::Relaxed) != 0 { "PAUSED".to_string() }
        else if c.paper_mode.load(Ordering::Relaxed) != 0 { "PAPER".to_string() }
        else { "LIVE".to_string() }
    }
    fn pnl(&self) -> f64 {
        self.cross.map(|c| {
            let r = c.daily_cross_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            let v = c.virtual_realized_pnl.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            r + v
        }).unwrap_or(0.0)
    }
    fn position(&self) -> f64 { 0.0 }
    fn detail(&self) -> Value {
        if let Some(c) = self.cross {
            let p = &c.pairs[0];
            let bfx_bid = p.bitfinex.bid.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            let bfx_ask = p.bitfinex.ask.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            let bnb_bid = p.binance.bid.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            let bnb_ask = p.binance.ask.load(Ordering::Relaxed) as f64 / PRICE_SCALE;
            
            let bfx_mid = if bfx_bid > 0.0 && bfx_ask > 0.0 { (bfx_bid + bfx_ask) / 2.0 } else { 0.0 };
            let bnb_mid = if bnb_bid > 0.0 && bnb_ask > 0.0 { (bnb_bid + bnb_ask) / 2.0 } else { 0.0 };
            
            serde_json::json!({
                "bfx_mid": bfx_mid,
                "bnb_mid": bnb_mid,
                "spread": bnb_mid - bfx_mid
            })
        } else {
            serde_json::json!({ "bfx_mid": 0.0, "bnb_mid": 0.0, "spread": 0.0 })
        }
    }
}

// ═══════════════════════════════════════════════════════════
// HIVE MIND CORE
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
    hardware: ServerHealth,
}

async fn index_handler() -> Html<&'static str> {
    Html(include_str!("../dashboard.html"))
}

async fn ws_handler(ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(handle_socket)
}

async fn handle_socket(socket: WebSocket) {
    let (mut sender, mut receiver) = socket.split();

    let h_engine = load_mmap::<EngineState>(ENGINE_STATE_PATH);
    let h_risk = load_mmap::<RiskState>(RISK_STATE_PATH);
    let hydra = HydraProbe { engine: h_engine, risk: h_risk };

    let m_engine = load_mmap::<MoonshotEngineState>(MOONSHOT_ENGINE_PATH);
    let m_risk = load_mmap::<MoonshotRiskState>(MOONSHOT_RISK_PATH);
    let moonshot = MoonshotProbe { engine: m_engine, risk: m_risk };

    let g_engine = load_mmap::<GridEngineState>(GRID_ENGINE_PATH);
    let g_risk = load_mmap::<GridRiskState>(GRID_RISK_PATH);
    let grid = GridProbe { engine: g_engine, risk: g_risk };

    let t_engine = load_mmap::<TrigonEngineState>(TRIGON_ENGINE_PATH);
    let t_risk = load_mmap::<TrigonRiskState>(TRIGON_RISK_PATH);
    let trigon = TrigonProbe { engine: t_engine, risk: t_risk };

    let n_cross = load_mmap::<CrossExchangeState>(CROSS_EXCHANGE_PATH);
    let nexus = NexusProbe { cross: n_cross };

    let probes: Vec<Box<dyn BotSnapshot + Send + Sync>> = vec![
        Box::new(hydra),
        Box::new(moonshot),
        Box::new(grid),
        Box::new(trigon),
        Box::new(nexus),
    ];

    let armada_state = load_mmap::<sniper_types::armada_types::ArmadaState>("/dev/shm/beroun/armada_state.bin");

    loop {
        tokio::select! {
            cmd_opt = receiver.next() => {
                match cmd_opt {
                    Some(Ok(Message::Text(text))) => {
                        let parsed: Value = match serde_json::from_str(&text) {
                            Ok(v) => v,
                            Err(_) => { tracing::warn!("Invalid JSON command ignored"); continue; }
                        };
                        
                        let cmd = parsed["cmd"].as_str()
                            .or_else(|| parsed["action"].as_str())
                            .unwrap_or("");
                        let target = parsed["bot"].as_str().unwrap_or("");
                        
                        if cmd == "GLOBAL_KILL" {
                            if let Some(r) = ACTIVE_MUT_TIES.hydra { r.paused.store(1, Ordering::Release); }
                            if let Some(r) = ACTIVE_MUT_TIES.moonshot { r.global_paused.store(1, Ordering::Release); }
                            if let Some(r) = ACTIVE_MUT_TIES.grid { r.global_paused.store(1, Ordering::Release); }
                            if let Some(r) = ACTIVE_MUT_TIES.trigon { r.global_paused.store(1, Ordering::Release); }
                            if let Some(r) = ACTIVE_MUT_TIES.nexus { r.emergency_pause.store(1, Ordering::Release); }
                            tracing::warn!("☢️ GLOBAL KILL INITIATED FRONTEND!");
                            continue;
                        }
                        
                        if cmd == "KELLY_UPDATE" {
                            let val = parsed["val"].as_f64().unwrap_or(0.0);
                            let updates = parsed["updates"].as_object();
                            if let Ok(mut lock) = GLOBAL_KELLY_WEIGHTS.write() {
                                if let Some(upds) = updates {
                                    for (k, v) in upds {
                                        if let Some(w) = v.as_f64() { lock.insert(k.to_string(), w); }
                                    }
                                } else {
                                    lock.insert(target.to_string(), val);
                                }
                            }
                            continue;
                        }
                        
                        let is_pause = if cmd == "FORCE_STOP" { 1 } else { 0 };
                        
                        match target {
                            "Hydra" => if let Some(r) = ACTIVE_MUT_TIES.hydra { r.paused.store(is_pause, Ordering::Release); },
                            "Moonshot" => if let Some(r) = ACTIVE_MUT_TIES.moonshot { r.global_paused.store(is_pause, Ordering::Release); },
                            "Grid" => if let Some(r) = ACTIVE_MUT_TIES.grid { r.global_paused.store(is_pause, Ordering::Release); },
                            "Trigon" => if let Some(r) = ACTIVE_MUT_TIES.trigon { r.global_paused.store(is_pause, Ordering::Release); },
                            "Nexus" => if let Some(r) = ACTIVE_MUT_TIES.nexus { r.emergency_pause.store(is_pause as u32, Ordering::Release); },
                            _ => {}
                        }
                        tracing::info!("Tactical Execution: {} on {}", cmd, target);
                    }
                    Some(Ok(_)) => {},
                    Some(Err(e)) => { tracing::warn!("WS err: {}", e); break; }
                    None => { break; }
                }
            }
            _ = tokio::time::sleep(Duration::from_millis(50)) => {
                let global_capital_limit = std::fs::read_to_string("/home/wwwenda/sniper/state/armada_state.json")
                    .ok()
                    .and_then(|data| serde_json::from_str::<Value>(&data).ok())
                    .and_then(|v| v["global_capital_limit"].as_f64())
                    .unwrap_or(10000.0);
                
                let mut bots_data = Vec::new();
                let mut total_pnl = 0.0;
                
                for (i, probe) in probes.iter().enumerate() {
                    let name = probe.name();
                    let kelly_limit = armada_state.map(|a| a.authorized_capital[i].load(Ordering::Relaxed) as f64 / PRICE_SCALE).unwrap_or(2000.0);
                    
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
                
                let hw_stats = SERVER_HEALTH.read().unwrap().clone();
                let state = PanopticonState {
                    global_capital: global_capital_limit,
                    total_equity: 15420.0 + total_pnl,
                    armada_pnl: total_pnl,
                    bots: bots_data,
                    hardware: hw_stats,
                };
                
                if let Ok(json) = serde_json::to_string(&state) {
                    if sender.send(Message::Text(json.into())).await.is_err() {
                        break;
                    }
                }
            }
        }
    }
}

#[tokio::main]
async fn main() {
    dotenvy::dotenv().ok();
    spawn_hardware_monitor();
    println!("--- 👁️ SOVEREIGN PANOPTICON v2.0 (LIVE KINETIC MMap STREAM) ---");
    let app = Router::new()
        .route("/", get(index_handler))
        .route("/ws", get(ws_handler))
        .layer(CorsLayer::permissive());

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
    println!("[PANOPTICON] Online at http://0.0.0.0:3000");
    axum::serve(listener, app).await.unwrap();
}
