use std::time::Duration;
use anyhow::{Context, Result};
use tracing::{info, warn, error};
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use futures_util::{StreamExt, SinkExt};
use dotenvy::dotenv;

use crate::exchange::venue::VenueAdapter;
use crate::exchange::ExchangeCredentials;

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
}

// ═══════════════════════════════════════════════════════════
// Single-WS Runner (used by Grid, Moonshot, Trigon, Nexus)
// ═══════════════════════════════════════════════════════════

/// Sovereign WebSocket Runner — venue-agnostic via VenueAdapter.
///
/// Monomorphized: `SovereignRunner<GridEngine, BitfinexVenue>` gets
/// fully inlined by the compiler. Zero-cost abstraction.
pub struct SovereignRunner<E: SovereignEngine, V: VenueAdapter> {
    pub engine: E,
    pub venue: V,
    pub name: String,
}

impl<E: SovereignEngine, V: VenueAdapter> SovereignRunner<E, V> {
    pub fn new(engine: E, venue: V, name: &str) -> Self {
        Self { engine, venue, name: name.to_string() }
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

            let mut authed = false;

            while let Some(msg) = read.next().await {
                let msg = match msg {
                    Ok(m) => m,
                    Err(e) => { warn!("WS read err: {}", e); break; }
                };

                out_buf.clear();

                if let Message::Text(text) = msg {
                    let bytes = text.as_bytes();

                    if bytes.first() == Some(&b'{') { 
                        let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();
                        
                        if v["event"] == "auth" {
                            if v["status"] == "OK" {
                                authed = true;
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
                        // Market Data Array (Fast-path)
                        let mut mut_bytes = bytes.to_vec();
                        self.engine.on_market_message(&mut mut_bytes, &mut out_buf);
                    }
                }
                
                self.engine.on_loop(&mut out_buf);
                
                if !out_buf.is_empty() {
                    let text = unsafe { String::from_utf8_unchecked(out_buf.to_vec()) };
                    let _ = write.send(Message::Text(text.into())).await;
                }
            }
            warn!("Sovereign WS read loop ended for {}", self.name);
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
    pub name: String,
}

impl<E: SovereignEngine, V: VenueAdapter> SovereignDualRunner<E, V> {
    pub fn new(engine: E, venue: V, name: &str) -> Self {
        Self { engine, venue, name: name.to_string() }
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

            let mut authed = false;

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
                        if !out_buf.is_empty() {
                            let text = unsafe { String::from_utf8_unchecked(out_buf.to_vec()) };
                            let _ = exec_write.send(Message::Text(text.into())).await;
                        }
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
                                        authed = true;
                                        self.engine.on_auth();
                                    } else {
                                        error!(event = "auth_failed", status = %v["status"], msg = %v["msg"]);
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
                            let _ = exec_write.send(Message::Text(text.into())).await;
                        }
                    }
                    _ = tokio::time::sleep(Duration::from_millis(1)) => {
                        // Background loop iteration
                        out_buf.clear();
                        self.engine.on_loop(&mut out_buf);
                        if !out_buf.is_empty() {
                            let text = unsafe { String::from_utf8_unchecked(out_buf.to_vec()) };
                            let _ = exec_write.send(Message::Text(text.into())).await;
                        }
                    }
                    _ = tokio::signal::ctrl_c() => {
                        info!("Received Ctrl-C, initiating shutdown for {}", self.name);
                        out_buf.clear();
                        self.engine.on_shutdown(&mut out_buf);
                        if !out_buf.is_empty() {
                            let text = unsafe { String::from_utf8_unchecked(out_buf.to_vec()) };
                            let _ = exec_write.send(Message::Text(text.into())).await;
                        }
                        return Ok(());
                    }
                }
            } // end dual stream loop
        }
    }
}
