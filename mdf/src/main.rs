// 🔄 MDF — Shared Market Data Feed Process
// Sniper Armada · Phase 7 · v1.0.0
//
// Single WebSocket connection to Bitfinex that broadcasts market data
// to all bots via mmap. Eliminates duplicate WS connections.
//
// Architecture:
//   1 WS connection → mmap → N bots read atomically
//   Instead of 4 bots × N symbols = 4N connections,
//   we have 1 connection serving all bots.
//
// mmap layout: /dev/shm/beroun/mdf.bin
//   - Array of MdfSymbol[MAX_MDF_SYMBOLS]
//   - Each symbol: bid, ask, last_trade, volume, timestamp

use std::time::{SystemTime, UNIX_EPOCH, Duration};
use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use std::collections::HashMap;

use futures_util::{StreamExt, SinkExt};
use serde_json::json;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use dotenvy::dotenv;
use tracing::{info, warn, Level};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};
use memmap2::MmapMut;
use anyhow::Result;

use sniper_types::PRICE_SCALE_I;

const BITFINEX_WS_URL: &str = "wss://api.bitfinex.com/ws/2";
const MDF_PATH: &str = "/dev/shm/beroun/mdf.bin";
const MAX_MDF_SYMBOLS: usize = 64;

// ═══════════════════════════════════════════════════════════
// MDF Symbol State (per symbol, cache-aligned)
// ═══════════════════════════════════════════════════════════
#[repr(C, align(64))]
struct MdfSymbol {
    symbol_hash: std::sync::atomic::AtomicU64,
    best_bid: std::sync::atomic::AtomicU64,
    best_ask: std::sync::atomic::AtomicU64,
    last_trade: std::sync::atomic::AtomicU64,
    volume_24h: std::sync::atomic::AtomicU64,
    timestamp_ms: std::sync::atomic::AtomicU64,
    tick_count: std::sync::atomic::AtomicU64,
    _padding: [u8; 8],
}

#[repr(C, align(64))]
struct MdfState {
    symbols: [MdfSymbol; MAX_MDF_SYMBOLS],
    heartbeat_ms: std::sync::atomic::AtomicU64,
    active_symbols: std::sync::atomic::AtomicU32,
    total_ticks: std::sync::atomic::AtomicU64,
}

impl Default for MdfState {
    fn default() -> Self {
        unsafe { std::mem::zeroed() }
    }
}

fn init_mmap() -> Result<MmapMut> {
    let dir = std::path::Path::new(MDF_PATH).parent().unwrap();
    std::fs::create_dir_all(dir)?;
    let file = OpenOptions::new().read(true).write(true).create(true).open(MDF_PATH)?;
    file.set_len(std::mem::size_of::<MdfState>() as u64)?;
    let mut mmap = unsafe { MmapMut::map_mut(&file)? };
    if mmap.iter().all(|&b| b == 0) {
        let default_val = MdfState::default();
        let ptr = &default_val as *const MdfState as *const u8;
        let slice = unsafe { std::slice::from_raw_parts(ptr, std::mem::size_of::<MdfState>()) };
        mmap.copy_from_slice(slice);
    }
    Ok(mmap)
}

fn str_to_hash(s: &str) -> u64 {
    let mut bytes = [0u8; 8];
    let len = s.len().min(8);
    bytes[..len].copy_from_slice(&s.as_bytes()[..len]);
    u64::from_le_bytes(bytes)
}

// Fast ticker parser (battle-tested, with bounds checks)
fn fast_parse_ticker(data: &[u8]) -> Option<(i64, i64, i64)> {
    if data.len() < 10 || data[0] != b'[' { return None; }
    let mut i = 1;
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() { return None; }
    let chan_id = std::str::from_utf8(&data[1..i]).ok()?.parse::<i64>().ok()?;
    if i + 2 < data.len() && data[i+1] == b'"' && data[i+2] == b'h' { return None; }
    while i < data.len() && data[i] != b'[' { i += 1; }
    if i >= data.len() { return None; }
    i += 1;
    let start = i;
    while i < data.len() && data[i] != b',' { i += 1; }
    if i >= data.len() || start >= i { return None; }
    let bid = (std::str::from_utf8(&data[start..i]).ok()?.parse::<f64>().ok()? * PRICE_SCALE_I as f64) as i64;
    i += 1;
    if i >= data.len() { return None; }
    while i < data.len() && data[i] != b',' { i += 1; }
    i += 1;
    if i >= data.len() { return None; }
    let start = i;
    while i < data.len() && data[i] != b',' && data[i] != b']' { i += 1; }
    if start >= i { return None; }
    let ask = (std::str::from_utf8(&data[start..i]).ok()?.parse::<f64>().ok()? * PRICE_SCALE_I as f64) as i64;
    Some((chan_id, bid, ask))
}

// ═══════════════════════════════════════════════════════════
// Default symbols to subscribe (union of all bots' needs)
// ═══════════════════════════════════════════════════════════
const DEFAULT_SYMBOLS: &[&str] = &[
    "tBTCUSD",  // Hydra + Grid
    "tETHUSD", "tETHBTC",  // Trigon
    "tLTCUSD", "tLTCBTC",  // Trigon
    "tXRPUSD", "tXRPBTC",  // Trigon
    "tSOLUSD", "tSOLBTC", "tSOLETH",  // Trigon + Moonshot
    "tDOGEUSD", "tADAUSD", "tAVAXUSD", "tDOTUSD",  // Moonshot candidates
    "tMATICUSD", "tLINKUSD", "tUNIUSD", "tATOMUSD",
];

#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();
    tracing_subscriber::registry()
        .with(fmt::layer().with_target(false).json())
        .with(EnvFilter::from_default_env().add_directive(Level::INFO.into()))
        .init();

    let mdf_mmap = init_mmap()?;
    let state = unsafe { &*(mdf_mmap.as_ptr() as *const MdfState) };

    info!(event = "mdf_start", version = "1.0.0", symbols = DEFAULT_SYMBOLS.len());

    // ═══ RECONNECT LOOP ═══
    loop {
        let ws_result = connect_async(BITFINEX_WS_URL).await;
        let (ws, _) = match ws_result {
            Ok(v) => v,
            Err(e) => { warn!(event = "ws_fail", error = %e); tokio::time::sleep(Duration::from_secs(60)).await; continue; }
        };

        let (mut _write, mut read) = ws.split();

        // Subscribe to all symbols
        let mut chan_to_idx: HashMap<i64, usize> = HashMap::new();
        let mut symbol_count = 0usize;

        for &sym in DEFAULT_SYMBOLS {
            _write.send(Message::Text(
                json!({"event":"subscribe","channel":"ticker","symbol":sym}).to_string().into()
            )).await?;

            // Pre-assign symbol hash
            let hash = str_to_hash(sym);
            state.symbols[symbol_count].symbol_hash.store(hash, Ordering::Release);
            symbol_count += 1;
        }
        state.active_symbols.store(symbol_count as u32, Ordering::Release);

        info!(event = "subscribed_all", count = symbol_count);

        // ═══ MESSAGE LOOP ═══
        while let Some(msg) = read.next().await {
            let msg = match msg { Ok(m) => m, Err(_) => break };

            let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
            state.heartbeat_ms.store(now_ms, Ordering::Release);

            if let Message::Text(text) = msg {
                let bytes = text.as_bytes();

                if let Some((chan, bid, ask)) = fast_parse_ticker(bytes) {
                    if let Some(&idx) = chan_to_idx.get(&chan) {
                        let s = &state.symbols[idx];
                        s.best_bid.store(bid as u64, Ordering::Release);
                        s.best_ask.store(ask as u64, Ordering::Release);
                        s.last_trade.store(((bid + ask) / 2) as u64, Ordering::Release);
                        s.timestamp_ms.store(now_ms, Ordering::Release);
                        s.tick_count.fetch_add(1, Ordering::Relaxed);
                        state.total_ticks.fetch_add(1, Ordering::Relaxed);
                    }
                    continue;
                }

                // Map chanId → index
                if bytes.first() == Some(&b'{') {
                    let v: serde_json::Value = serde_json::from_slice(bytes).unwrap_or_default();
                    if v["event"] == "subscribed" && v["channel"] == "ticker" {
                        if let (Some(cid), Some(sym)) = (v["chanId"].as_i64(), v["symbol"].as_str()) {
                            let hash = str_to_hash(sym);
                            for i in 0..symbol_count {
                                if state.symbols[i].symbol_hash.load(Ordering::Acquire) == hash {
                                    chan_to_idx.insert(cid, i);
                                    info!(event = "mapped", symbol = sym, idx = i, chan_id = cid);
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }

        warn!(event = "ws_disconnected", bot = "mdf");
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
