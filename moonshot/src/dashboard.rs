// 🌙 Moonshot Dashboard — SSE Real-time Multi-Pair Monitor
// Sniper Armada · Bot #2 · Port :3001
//
// Reads moonshot_engine.bin + moonshot_risk.bin via mmap
// Streams 20-pair grid updates via Server-Sent Events (SSE)

use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use std::time::Duration;

use axum::{Router, response::{Html, sse::{Event, KeepAlive, Sse}}, routing::get};
use tower_http::cors::CorsLayer;
use memmap2::MmapMut;
use anyhow::Result;
use tokio_stream::StreamExt;
use serde_json::json;

use sniper_types::moonshot_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};

fn init_mmap_read<T>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

async fn sse_handler() -> Sse<impl tokio_stream::Stream<Item = Result<Event, std::convert::Infallible>>> {
    let e_mmap = init_mmap_read::<MoonshotEngineState>(MOONSHOT_ENGINE_PATH).unwrap();
    let r_mmap = init_mmap_read::<MoonshotRiskState>(MOONSHOT_RISK_PATH).unwrap();
    let engine = unsafe { &*(e_mmap.as_ptr() as *const MoonshotEngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const MoonshotRiskState) };

    let stream = tokio_stream::wrappers::IntervalStream::new(tokio::time::interval(Duration::from_millis(500)))
        .map(move |_| {
            let paused = risk.global_paused.load(Ordering::Acquire) != 0;
            let daily_pnl = engine.daily_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
            let total_fills = engine.total_fills.load(Ordering::Acquire);

            let mut pairs = Vec::new();
            let mut active_count = 0u32;
            let mut total_pnl = 0.0f64;

            for i in 0..MOONSHOT_MAX_PAIRS {
                let sym_hash = risk.pairs[i].symbol_hash.load(Ordering::Acquire);
                let active = engine.pairs[i].active.load(Ordering::Acquire);

                if sym_hash != 0 && active == 1 {
                    active_count += 1;
                    let symbol = symbol_hash_to_str(sym_hash);
                    let bid = engine.pairs[i].best_bid.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                    let ask = engine.pairs[i].best_ask.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                    let pos = engine.pairs[i].net_position.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                    let pnl = engine.pairs[i].realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                    let fills = engine.pairs[i].fill_count.load(Ordering::Acquire);
                    let drop_pct = risk.pairs[i].m_shot_price_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                    let tp_pct = risk.pairs[i].tp_pct.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                    let order_usd = risk.pairs[i].order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE;
                    let buy_level = engine.pairs[i].buy_order_price.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                    let latency = engine.pairs[i].latency_ns.load(Ordering::Acquire);

                    total_pnl += pnl;

                    pairs.push(json!({
                        "idx": i,
                        "symbol": symbol,
                        "bid": format!("{:.4}", bid),
                        "ask": format!("{:.4}", ask),
                        "position": format!("{:.6}", pos),
                        "pnl": format!("{:.2}", pnl),
                        "fills": fills,
                        "drop_pct": format!("{:.2}", drop_pct),
                        "tp_pct": format!("{:.2}", tp_pct),
                        "order_usd": format!("{:.0}", order_usd),
                        "buy_level": format!("{:.4}", buy_level),
                        "latency_us": latency / 1000,
                    }));
                }
            }

            let data = json!({
                "paused": paused,
                "active_pairs": active_count,
                "total_pnl": format!("{:.2}", total_pnl),
                "daily_pnl": format!("{:.2}", daily_pnl),
                "total_fills": total_fills,
                "pairs": pairs,
            });

            Ok(Event::default().data(data.to_string()))
        });

    Sse::new(stream).keep_alive(KeepAlive::default())
}

async fn index() -> Html<String> {
    let html_path = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_default()
        .join("../../moonshot/dashboard.html");

    let content = std::fs::read_to_string(&html_path)
        .unwrap_or_else(|_| "<h1>Moonshot Dashboard</h1><p>dashboard.html not found</p>".to_string());
    Html(content)
}

#[tokio::main]
async fn main() -> Result<()> {
    let app = Router::new()
        .route("/", get(index))
        .route("/events", get(sse_handler))
        .layer(CorsLayer::permissive());

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3001").await?;
    println!("🌙 Moonshot Dashboard listening on :3001");
    axum::serve(listener, app).await?;
    Ok(())
}
