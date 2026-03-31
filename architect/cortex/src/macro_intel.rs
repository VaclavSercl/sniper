// ═══════════════════════════════════════════════════════════
// 🌍 SOVEREIGN CORTEX — Macro Intelligence Module
// Phase 3: Binance WebSocket, Fear & Greed, News RSS
//
// Writes SHARED macro data to ALL bot engine_state.bin files.
// Currently: Hydra only. Future: fan-out to all bots.
// ═══════════════════════════════════════════════════════════

use sniper_types::{EngineState, PRICE_SCALE};
use std::collections::{HashSet, VecDeque};
use std::sync::atomic::Ordering;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

// ── Binance thresholds ──
const BINANCE_LARGE_SELL_BTC: f64 = 1.0;
const BINANCE_SWEEP_WINDOW_S: f64 = 10.0;
const BINANCE_SWEEP_VOLUME_BTC: f64 = 5.0;
const BINANCE_WS_URL: &str = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade";

// ── Fear & Greed ──
const FEAR_GREED_URL: &str = "https://api.alternative.me/fng/?limit=1";
const FEAR_GREED_INTERVAL_S: u64 = 300;

// ── News RSS ──
const NEWS_INTERVAL_S: u64 = 180;
const RSS_FEEDS: &[&str] = &[
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
];

const BULLISH_KEYWORDS: &[&str] = &[
    "bull", "rally", "surge", "breakout", "ath", "all-time high",
    "adoption", "approval", "etf approved", "institutional buy",
    "rate cut", "dovish", "bullish", "upgrade", "accumulation",
];

const BEARISH_KEYWORDS: &[&str] = &[
    "bear", "crash", "dump", "hack", "exploit", "ban",
    "regulation", "sec", "lawsuit", "rate hike", "hawkish",
    "liquidation", "capitulation", "sell-off", "downgrade",
    "bankruptcy", "insolvency", "default",
];

fn epoch_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64
}

/// Safe string truncation that respects UTF-8 char boundaries.
fn safe_truncate(s: &str, max_bytes: usize) -> &str {
    if s.len() <= max_bytes { return s; }
    let mut end = max_bytes;
    while end > 0 && !s.is_char_boundary(end) { end -= 1; }
    &s[..end]
}

// ═══ MODULE 1: BINANCE CROSS-EXCHANGE WEBSOCKET ═══

/// Run Binance BTC/USDT aggTrade WebSocket (blocking — run in dedicated thread).
pub async fn run_binance_ws(engine: &EngineState) {
    println!("  🌐 [MACRO] Binance BTC/USDT WebSocket starting...");

    loop {
        match run_binance_ws_inner(engine).await {
            Ok(()) => println!("  ⚠️ [MACRO] Binance WS closed, reconnecting in 5s..."),
            Err(e) => println!("  ❌ [MACRO] Binance WS error: {e}, reconnecting in 5s..."),
        }
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}

async fn run_binance_ws_inner(engine: &EngineState) -> anyhow::Result<()> {
    use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
    use futures_util::StreamExt;

    let (mut socket, _response) = connect_async(BINANCE_WS_URL).await?;
    println!("  🟢 [MACRO] Binance BTC/USDT aggTrade connected");

    let mut sell_window: VecDeque<(f64, f64)> = VecDeque::new(); // (timestamp, volume)
    let mut price_buffer: VecDeque<(f64, f64)> = VecDeque::with_capacity(100);

    while let Some(msg_res) = socket.next().await {
        let msg = msg_res?;
        if let Message::Text(text) = msg {
            if let Ok(trade) = serde_json::from_str::<serde_json::Value>(&text) {
                let price: f64 = trade["p"].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
                let qty: f64 = trade["q"].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
                let is_sell = trade["m"].as_bool().unwrap_or(false); // maker=buyer → sell aggression

                // Track Binance mid-price (VWAP)
                price_buffer.push_back((price, qty));
                if price_buffer.len() > 100 { price_buffer.pop_front(); }
                if price_buffer.len() >= 5 {
                    let total_vol: f64 = price_buffer.iter().map(|(_, v)| v).sum();
                    if total_vol > 0.0 {
                        let vwap: f64 = price_buffer.iter().map(|(p, v)| p * v).sum::<f64>() / total_vol;
                        engine.binance_mid_price.store((vwap * PRICE_SCALE) as i64, Ordering::Release);
                    }
                }

                if is_sell {
                    let now = SystemTime::now().duration_since(UNIX_EPOCH)
                        .unwrap_or_default().as_secs_f64();
                    sell_window.push_back((now, qty));

                    // Prune old entries
                    while let Some(&(ts, _)) = sell_window.front() {
                        if ts < now - BINANCE_SWEEP_WINDOW_S { sell_window.pop_front(); }
                        else { break; }
                    }

                    // Single large sell
                    if qty >= BINANCE_LARGE_SELL_BTC {
                        engine.binance_sweep_ts.store(epoch_ms(), Ordering::Release);
                        println!("  🔴 [MACRO] BINANCE LARGE SELL: {qty:.3} BTC @ ${price:.0}");
                    }

                    // Aggregate window
                    let window_vol: f64 = sell_window.iter().map(|(_, v)| v).sum();
                    if window_vol >= BINANCE_SWEEP_VOLUME_BTC {
                        engine.binance_sweep_ts.store(epoch_ms(), Ordering::Release);
                        println!("  🔴 [MACRO] BINANCE SWEEP: {window_vol:.1} BTC in {BINANCE_SWEEP_WINDOW_S}s");
                        sell_window.clear();
                    }
                }
            }
        }
    }
    Ok(())
}

// ═══ MODULE 2: FEAR & GREED INDEX ═══

/// Fetch Fear & Greed Index every 5 minutes (blocking — run in dedicated thread).
pub async fn run_fear_greed(engine: &EngineState) {
    println!("  📊 [MACRO] Fear & Greed monitor starting ({}s interval)...", FEAR_GREED_INTERVAL_S);

    loop {
        match fetch_fear_greed(engine).await {
            Ok(val) => println!("  📊 [MACRO] Fear & Greed: {val}"),
            Err(e) => println!("  ⚠️ [MACRO] F&G fetch error: {e}"),
        }
        tokio::time::sleep(Duration::from_secs(FEAR_GREED_INTERVAL_S)).await;
    }
}

async fn fetch_fear_greed(engine: &EngineState) -> anyhow::Result<u64> {
    let client = reqwest::Client::new();
    let body: String = client.get(FEAR_GREED_URL)
        .header("User-Agent", "SniperCortex/13.0")
        .send()
        .await?
        .text()
        .await?;

    let data: serde_json::Value = serde_json::from_str(&body)?;
    let value = data["data"][0]["value"]
        .as_str()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(50);

    engine.macro_fear_greed.store(value, Ordering::Release);
    engine.macro_source_ts.store(epoch_ms(), Ordering::Release);

    Ok(value)
}

// ═══ MODULE 3: NEWS RSS SENTIMENT ═══

/// Scan crypto RSS feeds every 3 minutes (blocking — run in dedicated thread).
pub async fn run_news_sentiment(engine: &EngineState) {
    println!("  📰 [MACRO] News RSS sentiment starting ({}s interval)...", NEWS_INTERVAL_S);

    let mut seen_titles: HashSet<String> = HashSet::new();

    loop {
        match scan_rss_feeds(engine, &mut seen_titles).await {
            Ok(n) => if n > 0 { println!("  📰 [MACRO] Scored {n} new articles"); },
            Err(e) => println!("  ⚠️ [MACRO] RSS scan error: {e}"),
        }

        // Cap seen_titles
        if seen_titles.len() > 1000 {
            let keep: Vec<String> = seen_titles.iter().take(500).cloned().collect();
            seen_titles = keep.into_iter().collect();
        }

        tokio::time::sleep(Duration::from_secs(NEWS_INTERVAL_S)).await;
    }
}

async fn scan_rss_feeds(engine: &EngineState, seen: &mut HashSet<String>) -> anyhow::Result<usize> {
    let mut scores: Vec<f64> = Vec::new();
    let client = reqwest::Client::new();

    for &feed_url in RSS_FEEDS {
        let body = match client.get(feed_url)
            .header("User-Agent", "SniperCortex/13.0")
            .send()
            .await
        {
            Ok(resp) => match resp.text().await {
                Ok(s) => s,
                Err(_) => continue,
            },
            Err(_) => continue,
        };

        // Simple XML parsing for RSS <item><title>...</title><description>...</description></item>
        for item_block in body.split("<item>").skip(1).take(10) {
            let title = extract_tag(item_block, "title");
            let desc = extract_tag(item_block, "description");

            let key = safe_truncate(&title, 80);
            if seen.contains(key) { continue; }
            seen.insert(key.to_string());

            let combined = format!("{title} {desc}");
            let score = score_text(&combined);
            if score.abs() > 0.01 {
                scores.push(score);
                if score.abs() > 0.5 {
                    let dir = if score > 0.0 { "🟢 BULL" } else { "🔴 BEAR" };
                    println!("  📰 {dir} ({score:+.2}): {}", safe_truncate(&title, 60));
                }
            }
        }
    }

    let scored_count = scores.len();

    if !scores.is_empty() {
        let avg_score = scores.iter().sum::<f64>() / scores.len() as f64;
        // EMA: 70% old, 30% new
        let old_bias = engine.macro_bias.load(Ordering::Relaxed) as f64 / 10000.0;
        let new_bias = (old_bias * 0.7 + avg_score * 0.3).clamp(-1.0, 1.0);
        engine.macro_bias.store((new_bias * 10000.0) as i64, Ordering::Release);
        engine.macro_source_ts.store(epoch_ms(), Ordering::Release);
    }

    Ok(scored_count)
}

fn score_text(text: &str) -> f64 {
    let lower = text.to_lowercase();
    let bull = BULLISH_KEYWORDS.iter().filter(|&&kw| lower.contains(kw)).count();
    let bear = BEARISH_KEYWORDS.iter().filter(|&&kw| lower.contains(kw)).count();
    let total = bull + bear;
    if total == 0 { return 0.0; }
    (bull as f64 - bear as f64) / total as f64
}

fn extract_tag(xml: &str, tag: &str) -> String {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    if let Some(start) = xml.find(&open) {
        let content_start = start + open.len();
        if let Some(end) = xml[content_start..].find(&close) {
            let content = &xml[content_start..content_start + end];
            // Strip CDATA
            let content = content.strip_prefix("<![CDATA[").unwrap_or(content);
            let content = content.strip_suffix("]]>").unwrap_or(content);
            return content.to_string();
        }
    }
    String::new()
}
