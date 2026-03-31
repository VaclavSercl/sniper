// ═══════════════════════════════════════════════════════════
// 🌍 SOVEREIGN CORTEX — Macro Intelligence Module
// Phase 3: Binance WebSocket, Fear & Greed, News RSS
//
// Writes SHARED macro data to ALL bot engine_state.bin files.
// FixedPrice refactor: NO f64 keywords allowed!
// ═══════════════════════════════════════════════════════════

use sniper_types::{EngineState, PRICE_SCALE_I};
use sniper_types::math::FixedPrice;
use std::collections::{HashSet, VecDeque};
use std::sync::atomic::Ordering;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

// ── Binance thresholds ──
const BINANCE_LARGE_SELL_BTC: i64 = 100_000_000; // 1.0 * PRICE_SCALE
const BINANCE_SWEEP_WINDOW_S: u64 = 10;
const BINANCE_SWEEP_VOLUME_BTC: i64 = 500_000_000; // 5.0 * PRICE_SCALE
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

fn epoch_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

fn safe_truncate(s: &str, max_bytes: usize) -> &str {
    if s.len() <= max_bytes { return s; }
    let mut end = max_bytes;
    while end > 0 && !s.is_char_boundary(end) { end -= 1; }
    &s[..end]
}

/// Helper to parse string directly into FixedPrice without floats.
fn parse_fixed(s: &str) -> FixedPrice {
    let mut parts = s.split('.');
    let int_part: i64 = parts.next().unwrap_or("0").parse().unwrap_or(0);
    let mut frac_part: i64 = 0;
    if let Some(f) = parts.next() {
        let f = &f[..std::cmp::min(f.len(), 8)];
        let padded = format!("{:0<8}", f);
        frac_part = padded.parse().unwrap_or(0);
    }
    FixedPrice::new(int_part * PRICE_SCALE_I + frac_part)
}

// ═══ MODULE 1: BINANCE CROSS-EXCHANGE WEBSOCKET ═══

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

    let mut sell_window: VecDeque<(u64, FixedPrice)> = VecDeque::new(); // (timestamp, volume)
    let mut price_buffer: VecDeque<(FixedPrice, FixedPrice)> = VecDeque::with_capacity(100);

    while let Some(msg_res) = socket.next().await {
        let msg = msg_res?;
        if let Message::Text(text) = msg {
            if let Ok(trade) = serde_json::from_str::<serde_json::Value>(&text) {
                let price_str = trade["p"].as_str().unwrap_or("0");
                let qty_str = trade["q"].as_str().unwrap_or("0");
                
                let price = parse_fixed(price_str);
                let qty = parse_fixed(qty_str);
                let is_sell = trade["m"].as_bool().unwrap_or(false);

                // Track Binance mid-price (VWAP)
                price_buffer.push_back((price, qty));
                if price_buffer.len() > 100 { price_buffer.pop_front(); }
                if price_buffer.len() >= 5 {
                    let total_vol = price_buffer.iter().fold(FixedPrice::zero(), |acc, (_, v)| acc + *v);
                    if total_vol > FixedPrice::zero() {
                        let weighted_sum = price_buffer.iter().fold(FixedPrice::zero(), |acc, (p, v)| acc + (*p * *v));
                        let vwap = weighted_sum / total_vol;
                        engine.binance_mid_price.store(vwap.0, Ordering::Release);
                    }
                }

                if is_sell {
                    let now = epoch_secs();
                    sell_window.push_back((now, qty));

                    // Prune old entries
                    while let Some(&(ts, _)) = sell_window.front() {
                        if ts < now.saturating_sub(BINANCE_SWEEP_WINDOW_S) { sell_window.pop_front(); }
                        else { break; }
                    }

                    // Single large sell
                    if qty >= FixedPrice::new(BINANCE_LARGE_SELL_BTC) {
                        engine.binance_sweep_ts.store(epoch_ms(), Ordering::Release);
                        let qty_int = qty.0 / PRICE_SCALE_I;
                        let price_int = price.0 / PRICE_SCALE_I;
                        println!("  🔴 [MACRO] BINANCE LARGE SELL: {} BTC @ ${}", qty_int, price_int);
                    }

                    // Aggregate window
                    let window_vol = sell_window.iter().fold(FixedPrice::zero(), |acc, (_, v)| acc + *v);
                    if window_vol >= FixedPrice::new(BINANCE_SWEEP_VOLUME_BTC) {
                        engine.binance_sweep_ts.store(epoch_ms(), Ordering::Release);
                        let w_int = window_vol.0 / PRICE_SCALE_I;
                        println!("  🔴 [MACRO] BINANCE SWEEP: {} BTC in {}s", w_int, BINANCE_SWEEP_WINDOW_S);
                        sell_window.clear();
                    }
                }
            }
        }
    }
    Ok(())
}

// ═══ MODULE 2: FEAR & GREED INDEX ═══

pub async fn run_fear_greed(engine: &EngineState) {
    println!("  �� [MACRO] Fear & Greed monitor starting ({}s interval)...", FEAR_GREED_INTERVAL_S);

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

pub async fn run_news_sentiment(engine: &EngineState) {
    println!("  📰 [MACRO] News RSS sentiment starting ({}s interval)...", NEWS_INTERVAL_S);

    let mut seen_titles: HashSet<String> = HashSet::new();

    loop {
        match scan_rss_feeds(engine, &mut seen_titles).await {
            Ok(n) => if n > 0 { println!("  📰 [MACRO] Scored {n} new articles"); },
            Err(e) => println!("  ⚠️ [MACRO] RSS scan error: {e}"),
        }

        if seen_titles.len() > 1000 {
            let keep: Vec<String> = seen_titles.iter().take(500).cloned().collect();
            seen_titles = keep.into_iter().collect();
        }

        tokio::time::sleep(Duration::from_secs(NEWS_INTERVAL_S)).await;
    }
}

async fn scan_rss_feeds(engine: &EngineState, seen: &mut HashSet<String>) -> anyhow::Result<usize> {
    let mut scores: Vec<FixedPrice> = Vec::new();
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

        for item_block in body.split("<item>").skip(1).take(10) {
            let title = extract_tag(item_block, "title");
            let desc = extract_tag(item_block, "description");

            let key = safe_truncate(&title, 80);
            if seen.contains(key) { continue; }
            seen.insert(key.to_string());

            let combined = format!("{title} {desc}");
            let score = score_text(&combined);
            if score.0.abs() > 1_000_000 { // 0.01 * PRICE_SCALE
                scores.push(score);
                if score.0.abs() > 50_000_000 { // 0.5 * PRICE_SCALE
                    let dir = if score > FixedPrice::zero() { "🟢 BULL" } else { "🔴 BEAR" };
                    println!("  📰 {dir} ({}): {}", score.0 / 1_000_000, safe_truncate(&title, 60)); // pseudofloat print
                }
            }
        }
    }

    let scored_count = scores.len();

    if !scores.is_empty() {
        let avg_score = scores.iter().fold(FixedPrice::zero(), |acc, x| acc + *x) / FixedPrice::new((scores.len() as i64) * PRICE_SCALE_I);
        // EMA: 70% old, 30% new
        let old_bias = FixedPrice::new(engine.macro_bias.load(Ordering::Relaxed) * (PRICE_SCALE_I / 10000));
        let new_bias = std::cmp::max(std::cmp::min(old_bias * FixedPrice::new(70_000_000) + avg_score * FixedPrice::new(30_000_000), FixedPrice::new(PRICE_SCALE_I)), FixedPrice::new(-PRICE_SCALE_I));
        engine.macro_bias.store(new_bias.0 / (PRICE_SCALE_I / 10000), Ordering::Release);
        engine.macro_source_ts.store(epoch_ms(), Ordering::Release);
    }

    Ok(scored_count)
}

fn score_text(text: &str) -> FixedPrice {
    let lower = text.to_lowercase();
    let bull = BULLISH_KEYWORDS.iter().filter(|&&kw| lower.contains(kw)).count();
    let bear = BEARISH_KEYWORDS.iter().filter(|&&kw| lower.contains(kw)).count();
    let total = bull + bear;
    if total == 0 { return FixedPrice::zero(); }
    
    // (bull - bear) / total * PRICE_SCALE
    let numerator = (bull as i64 - bear as i64) * PRICE_SCALE_I;
    FixedPrice::new(numerator / total as i64)
}

fn extract_tag(xml: &str, tag: &str) -> String {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    if let Some(start) = xml.find(&open) {
        let content_start = start + open.len();
        if let Some(end) = xml[content_start..].find(&close) {
            let content = &xml[content_start..content_start + end];
            let content = content.strip_prefix("<![CDATA[").unwrap_or(content);
            let content = content.strip_suffix("]]>").unwrap_or(content);
            return content.to_string();
        }
    }
    String::new()
}
