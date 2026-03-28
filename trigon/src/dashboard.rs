// 🔺 Trigon Dashboard — SSE Real-time Triangle Monitor
// Sniper Armada · Bot #4 · Port :3003

use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use std::time::Duration;
use std::convert::Infallible;
use axum::{Router, response::{Html, sse::{Event, Sse}}, routing::get};
use tokio_stream::StreamExt;
use tower_http::cors::CorsLayer;
use memmap2::MmapMut;
use anyhow::Result;

use sniper_types::trigon_types::*;
use sniper_types::moonshot_types::symbol_hash_to_str;
use sniper_types::{PRICE_SCALE_I};

fn init_mmap_read<T>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

#[tokio::main]
async fn main() -> Result<()> {
    let e_mmap = init_mmap_read::<TrigonEngineState>(TRIGON_ENGINE_PATH)?;
    let r_mmap = init_mmap_read::<TrigonRiskState>(TRIGON_RISK_PATH)?;

    let engine: &'static TrigonEngineState = unsafe {
        &*(e_mmap.as_ptr() as *const TrigonEngineState)
    };
    let risk: &'static TrigonRiskState = unsafe {
        &*(r_mmap.as_ptr() as *const TrigonRiskState)
    };

    // Leak mmaps to get 'static lifetime
    std::mem::forget(e_mmap);
    std::mem::forget(r_mmap);

    let app = Router::new()
        .route("/", get(|| async {
            let exe = std::env::current_exe().ok()
                .and_then(|p| p.parent().map(|d| d.to_path_buf()))
                .unwrap_or_default();
            let html_path = exe.join("../../trigon/dashboard.html");
            Html(std::fs::read_to_string(html_path).unwrap_or_else(|_|
                "<h1>🔺 Trigon Dashboard</h1><p>dashboard.html not found</p>".to_string()
            ))
        }))
        .route("/events", get(move || async move {
            let stream = tokio_stream::wrappers::IntervalStream::new(
                tokio::time::interval(Duration::from_millis(500))
            )
            .map(move |_| -> Result<Event, Infallible> {
                let mut triangles = Vec::new();
                for t in 0..TRIGON_MAX_TRIANGLES {
                    let et = &engine.triangles[t];
                    if et.active.load(Ordering::Acquire) == 0 {
                        // Check if any leg has data
                        let has_data = (0..TRIGON_LEGS).any(|l|
                            et.legs[l].best_bid.load(Ordering::Acquire) != 0
                        );
                        if !has_data { continue; }
                    }

                    let mut legs = Vec::new();
                    for l in 0..TRIGON_LEGS {
                        let leg = &et.legs[l];
                        let sym = symbol_hash_to_str(risk.triangles[t].leg_symbols[l].load(Ordering::Acquire));
                        legs.push(serde_json::json!({
                            "symbol": sym,
                            "bid": leg.best_bid.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                            "ask": leg.best_ask.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                            "dir": risk.triangles[t].leg_directions[l].load(Ordering::Acquire),
                        }));
                    }

                    triangles.push(serde_json::json!({
                        "idx": t,
                        "legs": legs,
                        "rate": et.implied_rate.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                        "profit_bps": et.profit_bps.load(Ordering::Acquire) as f64 / 100.0,
                        "executions": et.executions.load(Ordering::Acquire),
                        "pnl": et.total_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                        "executing": et.executing.load(Ordering::Acquire),
                    }));
                }

                let data = serde_json::json!({
                    "triangles": triangles,
                    "total_arbs": engine.total_arbs.load(Ordering::Acquire),
                    "total_pnl": engine.total_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                    "daily_pnl": engine.daily_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
                    "best_profit_bps": engine.best_profit_bps.load(Ordering::Acquire) as f64 / 100.0,
                    "scan_latency_ns": engine.scan_latency_ns.load(Ordering::Acquire),
                    "heartbeat_ms": engine.heartbeat_ms.load(Ordering::Acquire),
                    "paused": risk.global_paused.load(Ordering::Acquire) != 0,
                });

                Ok(Event::default().data(data.to_string()))
            });

            Sse::new(stream)
        }))
        .layer(CorsLayer::permissive());

    let addr = "0.0.0.0:3003";
    println!("🔺 Trigon Dashboard running on http://{}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
