use std::time::Duration;
use anyhow::{Context, Result};
use tracing::{info, warn, error};
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use futures_util::{StreamExt, SinkExt};
use dotenvy::dotenv;

use crate::exchange::venue::VenueAdapter;
use crate::exchange::ExchangeCredentials;

use crate::EngineState;

/// Fleet-wide Bitfinex notification parser.
/// Intercepts [0, "n", [...]] messages and logs rejections/successes.
/// Returns true if the message was a notification (so caller can skip further parsing).
#[inline]
fn parse_bitfinex_notification(bytes: &[u8]) -> bool {
    // Quick check: must be [0,"n", pattern (auth channel notification)
    if bytes.len() < 10 || bytes[0] != b'[' { return false; }
    // Check for channel 0 and "n" type
    if !(bytes[1] == b'0' && bytes[2] == b',') { return false; }
    // Find message type marker after channel
    let needle = b"\"n\"";
    let found = bytes[3..12.min(bytes.len())].windows(3).any(|w| w == needle);
    if !found { return false; }
    // Parse with serde_json for the notification payload
    if let Ok(v) = serde_json::from_slice::<serde_json::Value>(bytes) {
        if let Some(arr) = v.get(2).and_then(|a| a.as_array()) {
            let status = arr.get(6).and_then(|s| s.as_str()).unwrap_or("");
            let msg = arr.get(7).and_then(|s| s.as_str()).unwrap_or("");
            if status.contains("ERROR") {
                error!(event = "bitfinex_reject", status = status, msg = msg);
            } else if !msg.is_empty() {
                info!(event = "bitfinex_notification", status = status, msg = msg);
            }
        }
    }
    true
}
use crate::math::FixedPrice;
use crate::fee_types::GlobalFeeMatrix;
use std::sync::atomic::Ordering;

/// Sjednocený kontext bez jakýchkoliv alokací (předává se botovi v každém cyklu)
// 1. Zavedeme generické typy E (Engine) a R (Risk)
pub struct BotContext<'a, E, R> {
    pub engine: &'a E,
    pub risk: Option<&'a R>,
    pub fee_matrix: &'a GlobalFeeMatrix,
    pub toxic_storm_active: bool,
    pub current_time_ms: u64,
}

// 2. Metody pro poplatky zůstávají stejné (sahají do fee_matrix)
impl<'a, E, R> BotContext<'a, E, R> {
    #[inline(always)]
    pub fn taker_fee_bps(&self, venue_id: usize) -> FixedPrice {
        let bps = self.fee_matrix.venues[venue_id].taker_fee_bps.load(Ordering::Relaxed);
        FixedPrice::new(bps as i64 * (crate::PRICE_SCALE_I / 100))
    }

    #[inline(always)]
    pub fn maker_fee_bps(&self, venue_id: usize) -> FixedPrice {
        let bps = self.fee_matrix.venues[venue_id].maker_fee_bps.load(Ordering::Relaxed);
        FixedPrice::new(bps as i64 * (crate::PRICE_SCALE_I / 100))
    }
}

// 3. Memory Bootloader (vrací raw pointery, které si Runner sestaví do kontextu)
pub struct SovereignMemory<E, R> {
    pub engine_ptr: *const E,
    pub risk_ptr: Option<*const R>,
    pub fee_matrix_ptr: *const GlobalFeeMatrix,
    pub l2_ptr: *mut crate::l2_command::L2SharedState,
    pub toxic_storm_ptr: *const u8,
    // (Zde si framework drží memmap2::Mmap instance, aby nebyly zahozeny)
    _engine_mmap: memmap2::MmapMut,
    _risk_mmap: Option<memmap2::MmapMut>,
    _fee_mmap: memmap2::MmapMut,
    _l2_mmap: memmap2::MmapMut,
    _storm_mmap: memmap2::Mmap,
}

impl<E: Default, R: Default> SovereignMemory<E, R> {
    pub fn boot(engine_path: &str, risk_path: Option<&str>) -> anyhow::Result<Self> {
        let engine_mmap = crate::mmap_utils::init_mmap::<E>(engine_path)?;
        let engine_ptr = engine_mmap.as_ptr() as *const E;

        let (risk_mmap, risk_ptr) = if let Some(path) = risk_path {
            let rm = crate::mmap_utils::init_mmap::<R>(path)?;
            let rp = rm.as_ptr() as *const R;
            (Some(rm), Some(rp))
        } else {
            (None, None)
        };

        let fee_mmap = crate::mmap_utils::init_mmap::<GlobalFeeMatrix>(crate::fee_types::FEE_MATRIX_PATH)?;
        let fee_matrix_ptr = fee_mmap.as_ptr() as *const GlobalFeeMatrix;

        let l2_mmap = crate::mmap_utils::init_mmap::<crate::l2_command::L2SharedState>(crate::l2_command::L2_COMMAND_PATH)?;
        let l2_ptr = l2_mmap.as_ptr() as *mut crate::l2_command::L2SharedState;
        
        let storm_path = "/dev/shm/sniper/toxic_storm.bin";
        if !std::path::Path::new(storm_path).exists() { let _ = std::fs::write(storm_path, [0u8]); }
        let toxic_storm_mmap = crate::mmap_utils::open_mmap_readonly(storm_path)?;
        let toxic_storm_ptr = toxic_storm_mmap.as_ptr();

        Ok(Self {
            engine_ptr,
            risk_ptr,
            fee_matrix_ptr,
            l2_ptr,
            toxic_storm_ptr,
            _engine_mmap: engine_mmap,
            _risk_mmap: risk_mmap,
            _fee_mmap: fee_mmap,
            _l2_mmap: l2_mmap,
            _storm_mmap: toxic_storm_mmap,
        })
    }
}

/// Zastřešující Trait pro všechny L0 Sovereign Boty.
pub trait SovereignEngine {
    /// Očekávané WebSocket subskripce po ověření
    fn subscriptions(&mut self) -> Vec<String>;
    
    /// Spuštěno před navázáním síťového spojení (mmap init atd.)
    fn on_start(&mut self) -> Result<()>;
    
    /// Spuštěno po úspěšném Auth OK
    fn on_auth(&mut self);
    
    /// Rychlá smyčka pro čistý socket stream z burzy (např. Ticker, Book)
    fn on_market_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut);
    
    /// Pomalá smyčka pro systémové JSON události (info, conf, err)
    fn on_system_event(&mut self, value: &serde_json::Value, out_buf: &mut bytes::BytesMut);
    
    /// Autonomní HFT scan-smyčka, spuštěna na každém průchodu tokio event loopu
    fn on_loop(&mut self, out_buf: &mut bytes::BytesMut);
    
    /// Spuštěno před ukončením programu (SIGINT/SIGTERM) pro clean-up
    fn on_shutdown(&mut self, _out_buf: &mut bytes::BytesMut) {}
    
    /// Vrací TRUE, pokud je bot přesunut do Shadow režimu (generuje intent, ale neodešle ho na burzu)
    fn is_shadow(&self) -> bool { false }
    
    /// Vrací nejlepší public Bid a Ask pro simulaci pesimistického plnění
    fn best_bid_ask(&self) -> (f64, f64) { (0.0, 0.0) }
    
    /// Pridani virtualniho zisku/ztraty pro Shadow Mode
    fn add_virtual_pnl(&self, _amount: i64) {}
}

// ═══════════════════════════════════════════════════════════
// Shadow Execution Macro
// ═══════════════════════════════════════════════════════════
macro_rules! drain_out_buf {
    ($runner:expr, $write:expr, $out_buf:expr) => {
        if !$out_buf.is_empty() {
            let text = unsafe { String::from_utf8_unchecked($out_buf.to_vec()) };
            if text == "RECONNECT" {
                tracing::warn!(event = "reconnection_triggered", bot = ?$runner.name);
                break;
            }
            if $runner.engine.is_shadow() {
                tracing::info!(event = "shadow_order_gen", payload = %text);
                if text.contains("\"oc\"") || text.contains("cancel") || text.contains("ox_multi") {
                    $runner.shadow_queue.clear();
                }
                
                if text.contains("\"EXCHANGE LIMIT\"") || text.contains("\"EXCHANGE IOC\"") {
                    let mut search_idx = 0;
                    while let Some(rel_a) = text[search_idx..].find("\"amount\":") {
                        let a = search_idx + rel_a + 9;
                        let s_a = if text.as_bytes()[a] == b'"' { a+1 } else { a };
                        let mut amount: f64 = 0.0;
                        if let Some(e) = text[s_a..].find(|c| c == '"' || c == ',' || c == '}') {
                            amount = text[s_a..s_a+e].parse().unwrap_or(0.0);
                        }
                        
                        let mut price: f64 = 0.0;
                        if let Some(rel_p) = text[a..].find("\"price\":") {
                            let p = a + rel_p + 8;
                            let s_p = if text.as_bytes()[p] == b'"' { p+1 } else { p };
                            if let Some(e) = text[s_p..].find(|c| c == '"' || c == ',' || c == '}') {
                                price = text[s_p..s_p+e].parse().unwrap_or(0.0);
                                search_idx = s_p + e; // advance search
                            } else {
                                search_idx = s_p;
                            }
                        } else {
                            break;
                        }
                        
                        if amount != 0.0 && price != 0.0 {
                            let is_buy = amount > 0.0;
                            $runner.shadow_queue.push(ShadowIntent {
                                side: is_buy, amount: amount.abs(), price, bot: $runner.name,
                                ts: std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_micros() as u64
                            });
                            tracing::info!(event = "shadow_intent_created", bot = ?$runner.name, side = ?if is_buy { "buy" } else { "sell" }, amount = amount.abs(), price = price);
                        }
                    }
                }
            } else {
                use futures_util::SinkExt;
                tracing::info!(event = "order_send", payload = %text);
                let _ = $write.send(Message::Text(text.into())).await;
            }
            $out_buf.clear();
        }
    }
}

// ═══════════════════════════════════════════════════════════
// Single-WS Runner (used by Grid, Moonshot, Trigon, Nexus)
// ═══════════════════════════════════════════════════════════

/// Sovereign WebSocket Runner — venue-agnostic via VenueAdapter.
///
/// Monomorphized: `SovereignRunner<GridEngine, BitfinexVenue>` gets
/// fully inlined by the compiler. Zero-cost abstraction.
#[derive(Debug, Clone)]
pub struct ShadowIntent {
    pub side: bool, // true = BUY, false = SELL
    pub amount: f64,
    pub price: f64,
    pub bot: &'static str,
    pub ts: u64,
}

pub struct SovereignRunner<E: SovereignEngine, V: VenueAdapter> {
    pub engine: E,
    pub venue: V,
    pub name: &'static str,
    pub shadow_queue: Vec<ShadowIntent>,
}

impl<E: SovereignEngine, V: VenueAdapter> SovereignRunner<E, V> {
    pub fn new(engine: E, venue: V, name: &'static str) -> Self {
        Self { engine, venue, name, shadow_queue: Vec::new() }
    }

    pub async fn run(&mut self) -> Result<()> {
        dotenv().ok();
        
        let creds = ExchangeCredentials::from_env(self.venue.env_prefix())
            .context(format!("Missing {}_API_KEY / {}_API_SECRET", 
                self.venue.env_prefix(), self.venue.env_prefix()))?;

        self.engine.on_start()?;
        
        let mut out_buf = bytes::BytesMut::with_capacity(2048);

        loop {
            let ws_result = connect_async(self.venue.ws_url()).await;
            let (ws, _) = match ws_result {
                Ok(v) => v,
                Err(e) => { 
                    warn!(event = "ws_fail", venue = self.venue.name(), error = %e); 
                    tokio::time::sleep(Duration::from_secs(5)).await; 
                    continue; 
                }
            };

            info!("Connected to {} WebSocket for {}", self.venue.name(), self.name);

            let (mut write, mut read) = ws.split();

            // Auth via VenueAdapter
            if let Some(auth_msg) = self.venue.auth_message(&creds) {
                if let Err(e) = write.send(Message::Text(auth_msg.into())).await {
                    error!("Auth send failed: {}", e);
                    continue;
                }
            }

            let mut _authed = false;

            loop {
                tokio::select! {
                    msg_res = read.next() => {
                        let msg = match msg_res {
                            Some(Ok(m)) => m,
                            Some(Err(e)) => { warn!("WS read err: {}", e); break; }
                            None => { warn!("WS disconnected"); break; }
                        };

                        out_buf.clear();

                        if let Message::Text(text) = msg {
                            let bytes = text.as_bytes();

                            if bytes.first() == Some(&b'{') { 
                                let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();
                                
                                if v["event"] == "auth" {
                                    if v["status"] == "OK" {
                                        _authed = true;
                                        self.engine.on_auth();
                                        
                                        let subs = self.engine.subscriptions();
                                        for sub in subs {
                                            let _ = write.send(Message::Text(sub.into())).await;
                                        }
                                    } else {
                                        error!(event = "auth_failed", status = %v["status"], msg = %v["msg"]);
                                    }
                                } else {
                                    self.engine.on_system_event(&v, &mut out_buf);
                                }
                            } else if bytes.first() == Some(&b'[') {
                                // Fleet-wide notification interception
                                if !parse_bitfinex_notification(bytes) {
                                    // Market Data Array (Fast-path)
                                    let mut mut_bytes = bytes.to_vec();
                                    self.engine.on_market_message(&mut mut_bytes, &mut out_buf);
                                }
                            }
                        }
                        
                        self.engine.on_loop(&mut out_buf);
                        drain_out_buf!(self, write, &mut out_buf);
                    }
                    _ = tokio::time::sleep(Duration::from_millis(1)) => {
                        out_buf.clear();
                        self.engine.on_loop(&mut out_buf);
                        drain_out_buf!(self, write, &mut out_buf);
                    }
                }
                self.process_shadow_matches();
            }
            warn!("Sovereign WS read loop ended for {}", self.name);
        }
    }

    /// Odbaví pesimistické stínové exekuce podle aktuálního Orderbooku
    fn process_shadow_matches(&mut self) {
        if !self.engine.is_shadow() || self.shadow_queue.is_empty() { return; }
        let (bid, ask) = self.engine.best_bid_ask();
        if bid <= 0.0 || ask <= 0.0 { return; }
        
        let mut i = 0;
        while i < self.shadow_queue.len() {
            let order = &self.shadow_queue[i];
            let is_buy = order.side;
            let is_filled = if is_buy { ask <= order.price } else { bid >= order.price };
            if is_filled {
                let fill_price = if is_buy { ask } else { bid };
                let payload = format!(
                    r#"{{"event":"shadow_fill","bot":"{}","symbol":"{}","side":"{}","qty":{},"price":{},"fee":0,"trade_id":"shadow_{}","ts":{}}}"#,
                    order.bot, crate::types::TRADING_SYMBOL, if is_buy { "buy" } else { "sell" }, order.amount, fill_price,
                    order.ts, std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis()
                );
                if let Ok(udp) = std::net::UdpSocket::bind("127.0.0.1:0") {
                    let _ = udp.send_to(payload.as_bytes(), "127.0.0.1:8888");
                }
                self.engine.add_virtual_pnl((order.amount * 5.0 * 100_000_000.0) as i64); // Simulate 5 points profit per fill
                self.shadow_queue.swap_remove(i);
            } else {
                i += 1;
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════
// Dual-WS Runner (used by Hydra — MDATA + EXEC separation)
// ═══════════════════════════════════════════════════════════

/// Sovereign Dual-WebSocket Runner — MDATA + EXEC channels.
/// Used by Hydra for maximum throughput: market data on one WS,
/// order execution on another.
pub struct SovereignDualRunner<E: SovereignEngine, V: VenueAdapter> {
    pub engine: E,
    pub venue: V,
    pub name: &'static str,
    pub shadow_queue: Vec<ShadowIntent>,
}

impl<E: SovereignEngine, V: VenueAdapter> SovereignDualRunner<E, V> {
    pub fn new(engine: E, venue: V, name: &'static str) -> Self {
        Self { engine, venue, name, shadow_queue: Vec::new() }
    }

    pub async fn run(&mut self) -> Result<()> {
        dotenv().ok();
        
        let creds = ExchangeCredentials::from_env(self.venue.env_prefix())
            .context(format!("Missing {}_API_KEY / {}_API_SECRET",
                self.venue.env_prefix(), self.venue.env_prefix()))?;

        self.engine.on_start()?;
        
        let mut out_buf = bytes::BytesMut::with_capacity(4096);

        loop {
            info!("Connecting Dual {} WebSockets for {}", self.venue.name(), self.name);
            let ws_mdata_res = connect_async(self.venue.ws_url()).await;
            let (ws_mdata, _) = match ws_mdata_res {
                Ok(v) => v, Err(e) => { warn!(event = "ws_mdata_fail", error = %e); tokio::time::sleep(Duration::from_secs(5)).await; continue; }
            };
            
            let ws_exec_res = connect_async(self.venue.ws_url()).await;
            let (ws_exec, _) = match ws_exec_res {
                Ok(v) => v, Err(e) => { warn!(event = "ws_exec_fail", error = %e); tokio::time::sleep(Duration::from_secs(5)).await; continue; }
            };

            let (mut mdata_write, mut mdata_read) = ws_mdata.split();
            let (mut exec_write, mut exec_read) = ws_exec.split();

            // Setup MDATA (No auth, just pure speed)
            let subs = self.engine.subscriptions();
            for sub in subs {
                let _ = mdata_write.send(Message::Text(sub.into())).await;
            }

            // Setup EXEC — auth via VenueAdapter
            let _ = exec_write.send(Message::Text(r#"{"event":"conf","flags":131072}"#.into())).await;
            if let Some(auth_msg) = self.venue.auth_message(&creds) {
                if let Err(e) = exec_write.send(Message::Text(auth_msg.into())).await {
                    error!("Auth send failed: {}", e);
                    continue;
                }
            }

            let mut _authed = false;

            // Dual polling loop
            loop {
                tokio::select! {
                    msg = mdata_read.next() => {
                        let msg = match msg {
                            Some(Ok(m)) => m,
                            _ => { warn!("MDATA WS disconnect"); break; }
                        };
                        out_buf.clear();
                        if let Message::Text(text) = msg {
                            let mut bytes = text.as_bytes().to_vec();
                            if bytes.first() == Some(&b'{') {
                                let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap_or_default();
                                self.engine.on_system_event(&v, &mut out_buf);
                            } else if bytes.first() == Some(&b'[') {
                                self.engine.on_market_message(&mut bytes, &mut out_buf);
                            }
                        }
                        drain_out_buf!(self, exec_write, &mut out_buf);
                    }
                    msg = exec_read.next() => {
                        let msg = match msg {
                            Some(Ok(m)) => m,
                            _ => { warn!("EXEC WS disconnect"); break; }
                        };
                        out_buf.clear();
                        if let Message::Text(text) = msg {
                            let bytes = text.as_bytes();
                            if bytes.first() == Some(&b'{') { 
                                let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();
                                if v["event"] == "auth" {
                                    if v["status"] == "OK" {
                                        _authed = true;
                                        self.engine.on_auth();
                                    } else {
                                        error!(event = "auth_failed", status = %v["status"], msg = %v["msg"]);
                                    }
                                } else {
                                    self.engine.on_system_event(&v, &mut out_buf);
                                }
                            } else if bytes.first() == Some(&b'[') {
                                if !parse_bitfinex_notification(bytes) {
                                    let mut mut_bytes = bytes.to_vec();
                                    self.engine.on_market_message(&mut mut_bytes, &mut out_buf);
                                }
                            }
                        }
                        drain_out_buf!(self, exec_write, &mut out_buf);
                    }
                    _ = tokio::signal::ctrl_c() => {
                        info!("Received Ctrl-C, initiating shutdown for {}", self.name);
                        out_buf.clear();
                        self.engine.on_shutdown(&mut out_buf);
                        drain_out_buf!(self, exec_write, &mut out_buf);
                        return Ok(());
                    }
                }
                // on_loop runs EVERY iteration — engine's internal fire_interval throttle controls frequency
                out_buf.clear();
                self.engine.on_loop(&mut out_buf);
                drain_out_buf!(self, exec_write, &mut out_buf);
                self.process_shadow_matches();
            } // end dual stream loop
        }
    }

    /// Odbaví pesimistické stínové exekuce podle aktuálního Orderbooku
    fn process_shadow_matches(&mut self) {
        if !self.engine.is_shadow() || self.shadow_queue.is_empty() { return; }
        let (bid, ask) = self.engine.best_bid_ask();
        if bid <= 0.0 || ask <= 0.0 { return; }
        
        tracing::info!(event = "process_shadow_matches", shadow_q_len = self.shadow_queue.len(), bid = bid, ask = ask);

        let mut i = 0;
        while i < self.shadow_queue.len() {
            let order = &self.shadow_queue[i];
            let is_buy = order.side;
            let is_filled = if is_buy { ask <= order.price } else { bid >= order.price };
            if is_filled {
                let fill_price = if is_buy { ask } else { bid };
                let payload = format!(
                    r#"{{"event":"shadow_fill","bot":"{}","symbol":"{}","side":"{}","qty":{},"price":{},"fee":0,"trade_id":"shadow_{}","ts":{}}}"#,
                    order.bot, crate::types::TRADING_SYMBOL, if is_buy { "buy" } else { "sell" }, order.amount, fill_price,
                    order.ts, std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis()
                );
                if let Ok(udp) = std::net::UdpSocket::bind("127.0.0.1:0") {
                    let _ = udp.send_to(payload.as_bytes(), "127.0.0.1:8888");
                }
                self.shadow_queue.swap_remove(i);
            } else {
                i += 1;
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════
// Cross-Venue Runner (for multi-exchange arbitrage)
// ═══════════════════════════════════════════════════════════

/// Extended engine trait for cross-venue bots (e.g., Nexus).
/// Receives data from two venues simultaneously.
pub trait CrossVenueEngine: SovereignEngine {
    /// Subscriptions for the secondary venue
    fn secondary_subscriptions(&mut self) -> Vec<String>;

    /// Handle market data from the secondary venue
    fn on_secondary_message(&mut self, payload: &mut [u8], out_buf: &mut bytes::BytesMut);
}

/// Cross-Venue Runner — connects to TWO exchanges simultaneously.
/// Primary venue: handles auth + order execution (out_buf goes here).
/// Secondary venue: market data only (price reference for arb signals).
///
/// Usage: `SovereignCrossVenueRunner::new(engine, BitfinexVenue::new(), BinanceVenue::new(), "Nexus")`
pub struct SovereignCrossVenueRunner<E: CrossVenueEngine, V1: VenueAdapter, V2: VenueAdapter> {
    pub engine: E,
    pub primary: V1,
    pub secondary: V2,
    pub name: &'static str,
}

impl<E: CrossVenueEngine, V1: VenueAdapter, V2: VenueAdapter> SovereignCrossVenueRunner<E, V1, V2> {
    pub fn new(engine: E, primary: V1, secondary: V2, name: &'static str) -> Self {
        Self { engine, primary, secondary, name }
    }

    pub async fn run(&mut self) -> Result<()> {
        dotenv().ok();

        let primary_creds = ExchangeCredentials::from_env(self.primary.env_prefix())
            .context(format!("Missing {}_API_KEY", self.primary.env_prefix()))?;

        self.engine.on_start()?;

        let mut out_buf = bytes::BytesMut::with_capacity(4096);

        loop {
            info!("Connecting Cross-Venue: {} (primary={}, secondary={})",
                self.name, self.primary.name(), self.secondary.name());

            // Connect primary venue (auth + execution)
            let ws_primary_res = connect_async(self.primary.ws_url()).await;
            let (ws_primary, _) = match ws_primary_res {
                Ok(v) => v, Err(e) => {
                    warn!(event = "primary_ws_fail", venue = self.primary.name(), error = %e);
                    tokio::time::sleep(Duration::from_secs(5)).await;
                    continue;
                }
            };


            let (mut primary_write, mut primary_read) = ws_primary.split();

            // Auth on primary
            if let Some(auth_msg) = self.primary.auth_message(&primary_creds) {
                if let Err(e) = primary_write.send(Message::Text(auth_msg.into())).await {
                    error!("Primary auth send failed: {}", e);
                    continue;
                }
            }

            // Build secondary venue URL with subscriptions baked in.
            // For Binance: combined streams via URL path (btcusdt@bookTicker/ethusdt@bookTicker).
            // For other venues: subscriptions sent via WS message after connect.
            let sec_subs = self.engine.secondary_subscriptions();
            let sec_url = if !sec_subs.is_empty() {
                // Extract stream names from subscribe JSON params
                let stream_names: Vec<String> = sec_subs.iter()
                    .filter_map(|s| {
                        serde_json::from_str::<serde_json::Value>(s).ok()
                            .and_then(|v| v.get("params")?.as_array().map(|a|
                                a.iter().filter_map(|p| p.as_str().map(String::from))
                                    .collect::<Vec<_>>().join("/")
                            ))
                    })
                    .collect();
                if stream_names.is_empty() {
                    self.secondary.ws_url().to_string()
                } else {
                    format!("{}/{}", self.secondary.ws_url(), stream_names.join("/"))
                }
            } else {
                self.secondary.ws_url().to_string()
            };

            let ws_sec_res = connect_async(&sec_url).await;
            let (ws_sec, _) = match ws_sec_res {
                Ok(v) => v, Err(e) => {
                    warn!(event = "secondary_ws_fail", error = %e);
                    tokio::time::sleep(Duration::from_secs(5)).await;
                    continue;
                }
            };
            let (_sec_write, mut sec_read) = ws_sec.split();

            let mut _authed = false;

            // Dual-venue polling loop
            loop {
                tokio::select! {
                    // Primary venue (Bitfinex): auth + market data + execution
                    msg = primary_read.next() => {
                        let msg = match msg {
                            Some(Ok(m)) => m,
                            _ => { warn!("{} primary WS disconnect", self.primary.name()); break; }
                        };
                        out_buf.clear();
                        if let Message::Text(text) = msg {
                            let bytes = text.as_bytes();
                            if bytes.first() == Some(&b'{') {
                                let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();
                                if v["event"] == "auth" {
                                    if v["status"] == "OK" {
                                        _authed = true;
                                        self.engine.on_auth();
                                        let subs = self.engine.subscriptions();
                                        for sub in subs {
                                            let _ = primary_write.send(Message::Text(sub.into())).await;
                                        }
                                    } else {
                                        error!(event = "auth_failed", venue = self.primary.name());
                                    }
                                } else {
                                    self.engine.on_system_event(&v, &mut out_buf);
                                }
                            } else if bytes.first() == Some(&b'[') {
                                let mut mut_bytes = bytes.to_vec();
                                self.engine.on_market_message(&mut mut_bytes, &mut out_buf);
                            }
                        }
                        if !out_buf.is_empty() {
                            let text = unsafe { String::from_utf8_unchecked(out_buf.to_vec()) };
                            let _ = primary_write.send(Message::Text(text.into())).await;
                        }
                    }
                    // Secondary venue (Binance): market data only
                    msg = sec_read.next() => {
                        let msg = match msg {
                            Some(Ok(m)) => m,
                            _ => { warn!("{} secondary WS disconnect", self.secondary.name()); break; }
                        };
                        if let Message::Text(text) = msg {
                            let mut bytes = text.as_bytes().to_vec();
                            out_buf.clear();
                            self.engine.on_secondary_message(&mut bytes, &mut out_buf);
                            // Secondary data may trigger primary venue orders
                            if !out_buf.is_empty() {
                                let text = unsafe { String::from_utf8_unchecked(out_buf.to_vec()) };
                                let _ = primary_write.send(Message::Text(text.into())).await;
                            }
                        }
                    }
                    _ = tokio::signal::ctrl_c() => {
                        info!("Received Ctrl-C, shutting down {}", self.name);
                        out_buf.clear();
                        self.engine.on_shutdown(&mut out_buf);
                        if !out_buf.is_empty() {
                            let text = unsafe { String::from_utf8_unchecked(out_buf.to_vec()) };
                            let _ = primary_write.send(Message::Text(text.into())).await;
                        }
                        return Ok(());
                    }
                }
                // on_loop runs after every WS message — engine's fire_interval throttle controls frequency
                out_buf.clear();
                self.engine.on_loop(&mut out_buf);
                if !out_buf.is_empty() {
                    let text = unsafe { String::from_utf8_unchecked(out_buf.to_vec()) };
                    let _ = primary_write.send(Message::Text(text.into())).await;
                }
            }
        }
    }
}
