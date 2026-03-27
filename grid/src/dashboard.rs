// 📐 Grid Dashboard — SSE Real-time Grid Visualizer
// Sniper Armada · Bot #3 · Port :3002

use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use std::time::Duration;
use axum::{Router, response::{Html, sse::{Event, KeepAlive, Sse}}, routing::get};
use tower_http::cors::CorsLayer;
use memmap2::MmapMut;
use anyhow::Result;
use tokio_stream::StreamExt;
use serde_json::json;
use sniper_types::grid_types::*;
use sniper_types::{PRICE_SCALE, PRICE_SCALE_I};

fn init_mmap_read<T>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

async fn sse_handler() -> Sse<impl tokio_stream::Stream<Item = Result<Event, std::convert::Infallible>>> {
    let e_mmap = init_mmap_read::<GridEngineState>(GRID_ENGINE_PATH).unwrap();
    let r_mmap = init_mmap_read::<GridRiskState>(GRID_RISK_PATH).unwrap();
    let engine = unsafe { &*(e_mmap.as_ptr() as *const GridEngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const GridRiskState) };

    let stream = tokio_stream::wrappers::IntervalStream::new(tokio::time::interval(Duration::from_millis(500)))
        .map(move |_| {
            let paused = risk.global_paused.load(Ordering::Acquire) != 0;
            let mid = engine.mid_price.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
            let pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
            let daily = engine.daily_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
            let fills = engine.total_fills.load(Ordering::Acquire);
            let n_buy = engine.active_buy_levels.load(Ordering::Acquire);
            let n_sell = engine.active_sell_levels.load(Ordering::Acquire);
            let spacing = risk.grid_spacing.load(Ordering::Acquire) as f64 / PRICE_SCALE;

            let mut buys = Vec::new();
            for i in 0..n_buy as usize {
                if i >= GRID_MAX_LEVELS { break; }
                let p = engine.buy_levels[i].price.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                let filled = engine.buy_levels[i].filled.load(Ordering::Acquire);
                buys.push(json!({"level": i+1, "price": format!("{:.1}", p), "filled": filled == 1}));
            }
            let mut sells = Vec::new();
            for i in 0..n_sell as usize {
                if i >= GRID_MAX_LEVELS { break; }
                let p = engine.sell_levels[i].price.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
                let filled = engine.sell_levels[i].filled.load(Ordering::Acquire);
                sells.push(json!({"level": i+1, "price": format!("{:.1}", p), "filled": filled == 1}));
            }

            Ok(Event::default().data(json!({
                "paused": paused, "mid_price": format!("{:.1}", mid),
                "spacing": format!("{:.0}", spacing),
                "pnl": format!("{:.2}", pnl), "daily_pnl": format!("{:.2}", daily),
                "fills": fills, "buy_levels": buys, "sell_levels": sells,
            }).to_string()))
        });
    Sse::new(stream).keep_alive(KeepAlive::default())
}

async fn index() -> Html<String> {
    let html_path = std::env::current_exe().ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_default().join("../../grid/dashboard.html");
    Html(std::fs::read_to_string(&html_path).unwrap_or_else(|_| "<h1>Grid Dashboard</h1>".to_string()))
}

#[tokio::main]
async fn main() -> Result<()> {
    let app = Router::new().route("/", get(index)).route("/events", get(sse_handler)).layer(CorsLayer::permissive());
    let listener = tokio::net::TcpListener::bind("0.0.0.0:3002").await?;
    println!("📐 Grid Dashboard listening on :3002");
    axum::serve(listener, app).await?;
    Ok(())
}
