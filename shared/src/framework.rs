use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use anyhow::{Context, Result};
use tracing::{info, warn, error};
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use futures_util::{StreamExt, SinkExt};
use serde_json::json;
use dotenvy::dotenv;

use crate::exchange::bitfinex;

/// Zastřešující Trait pro všechny L0 Sovereign Boty.
pub trait SovereignEngine {
    /// Očekávané WebSocket subskripce po ověření
    fn subscriptions(&mut self) -> Vec<String>;
    
    /// Spuštěno před navázáním síťového spojení (mmap init atd.)
    fn on_start(&mut self) -> Result<()>;
    
    /// Spuštěno po úspěšném Bitfinex Auth OK
    fn on_auth(&mut self);
    
    /// Rychlá smyčka pro čistý socket stream z burzy (např. Ticker, Book)
    fn on_market_message(&mut self, payload: &[u8], out_buf: &mut bytes::BytesMut);
    
    /// Pomalá smyčka pro systémové JSON události (info, conf, err)
    fn on_system_event(&mut self, value: &serde_json::Value, out_buf: &mut bytes::BytesMut);
    
    /// Autonomní HFT scan-smyčka, spuštěna na každém průchodu tokio event loopu
    fn on_loop(&mut self, out_buf: &mut bytes::BytesMut);
}

/// Sjednocený Sovereign WebSocket Runner
pub struct SovereignRunner<E: SovereignEngine> {
    pub engine: E,
    pub name: String,
}

impl<E: SovereignEngine> SovereignRunner<E> {
    pub fn new(engine: E, name: &str) -> Self {
        Self { engine, name: name.to_string() }
    }

    pub async fn run(&mut self) -> Result<()> {
        dotenv().ok();
        
        let key = std::env::var("BITFINEX_API_KEY").context("BITFINEX_API_KEY")?;
        let sec = std::env::var("BITFINEX_API_SECRET").context("BITFINEX_API_SECRET")?;

        self.engine.on_start()?;
        
        let mut out_buf = bytes::BytesMut::with_capacity(2048);

        loop {
            let ws_result = connect_async(bitfinex::WS_URL).await;
            let (ws, _) = match ws_result {
                Ok(v) => v,
                Err(e) => { 
                    warn!(event = "ws_fail", error = %e); 
                    tokio::time::sleep(Duration::from_secs(5)).await; 
                    continue; 
                }
            };

            info!("Connected to Sovereign WebSocket for {}", self.name);

            let (mut write, mut read) = ws.split();

            // Auth
            let auth_msg = crate::exchange::bitfinex_auth_message(&key, &sec);
            if let Err(e) = write.send(Message::Text(auth_msg.into())).await {
                error!("Auth send failed: {}", e);
                continue;
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
                        self.engine.on_market_message(bytes, &mut out_buf);
                    }
                }
                
                self.engine.on_loop(&mut out_buf);

                if !out_buf.is_empty() {
                    let s = unsafe { String::from_utf8_unchecked(out_buf.to_vec()) };
                    if let Err(e) = write.send(Message::Text(s.into())).await {
                        error!("WS write payload err: {}", e);
                        break;
                    }
                }
            }

            warn!("Sovereign WebSocket disconnected. Reconnecting in 5s...");
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    }
}
