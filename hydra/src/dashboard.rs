use memmap2::MmapMut;
use std::fs::OpenOptions;
use std::time::{Duration, SystemTime, Instant};
use std::sync::{Arc, Mutex, atomic::Ordering};
use std::collections::VecDeque;
use std::convert::Infallible;
use axum::{
    extract::State,
    response::{Html, sse::{Event, Sse, KeepAlive}},
    routing::get,
    Router,
};
use tower_http::cors::CorsLayer;
use dotenvy::dotenv;
use anyhow::Result;

use sniper_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE_I};

const MAX_HISTORY: usize = 200;
const MAX_EVENTS: usize = 150;

fn init_mmap_ptr<T: Default>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).truncate(false).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

// ═══ STATE ═══
#[derive(Debug, Clone)]
struct LogEvent {
    time: String,
    text: String,
    class: String,
}

#[derive(Debug, Clone)]
struct DashboardState {
    // Prices
    best_bid: f64,
    best_ask: f64,
    mid_price: f64,
    spread: f64,
    micro_price: f64,
    // Trading
    position: f64,
    inv_skew: f64,
    realized_pnl: f64,
    // Wallets
    wallet_btc: f64,
    wallet_usd: f64,
    total_equity: f64,
    // Grid
    grid_step: f64,
    grid_size: u64,
    // System
    t2t_micros: u64,
    uptime_secs: u64,
    // AI Intelligence
    l1_confidence: f64,
    l2_regime_id: u64,
    is_shadow_mode: bool,
    shadow_pnl: f64,
    toxic_flow_hits: u64,
    sweep_freeze_active: bool,
    l1_skew_usd: f64,
    l2_imbalance: f64,
    l1_false_positive_rate: f64,
    l1_sweep_success_rate: f64,
    session_fills: u64,
    paused: bool,
    l1_uptime_pct: f64,  // v10.5: Trading uptime %
    // v10.6 Ghost Orders
    ghost_transparency: f64,
    ghost_injections: u64,
    ghost_velocity_rejects: u64,
    ghost_active_mask: u64,
    // v10.9 Macro Intelligence
    macro_bias: f64,
    macro_fear_greed: u64,
    macro_bnb_sweep_ago: i64,  // -1 = never, else seconds ago
    // v11.1 Delta Lead
    binance_mid: f64,
    delta_lead_bps: f64,
    delta_signal: i64,
    delta_repositions: u64,
    sentinel_repositions: u64,
    // v11.3 Fee Sentinel
    maker_fee_pct: f64,
    taker_fee_pct: f64,
    fee_kills: u64,
    // Grid overlay
    last_buy_price: f64,
    last_sell_price: f64,
    // Order Book
    bid_prices: Vec<f64>,
    bid_amounts: Vec<f64>,
    ask_prices: Vec<f64>,
    ask_amounts: Vec<f64>,
    // History (for SVG sparklines)
    price_history: VecDeque<f64>,
    pnl_history: VecDeque<f64>,
    // Event log
    events: VecDeque<LogEvent>,
    prev_fills: u64,
    prev_freeze: bool,
}

impl Default for DashboardState {
    fn default() -> Self {
        Self {
            best_bid: 0.0, best_ask: 0.0, mid_price: 0.0, spread: 0.0, micro_price: 0.0,
            position: 0.0, inv_skew: 0.0, realized_pnl: 0.0,
            wallet_btc: 0.0, wallet_usd: 0.0, total_equity: 0.0,
            grid_step: 0.0, grid_size: 0,
            t2t_micros: 0, uptime_secs: 0,
            l1_confidence: 0.0, l2_regime_id: 0, is_shadow_mode: false,
            shadow_pnl: 0.0, toxic_flow_hits: 0, sweep_freeze_active: false,
            l1_skew_usd: 0.0, l2_imbalance: 0.0,
            l1_false_positive_rate: 0.0, l1_sweep_success_rate: 0.0,
            session_fills: 0, paused: false, l1_uptime_pct: 1.0,
            ghost_transparency: 1.0, ghost_injections: 0,
            ghost_velocity_rejects: 0, ghost_active_mask: 0,
            macro_bias: 0.0, macro_fear_greed: 50, macro_bnb_sweep_ago: -1,
            binance_mid: 0.0, delta_lead_bps: 0.0, delta_signal: 0,
            delta_repositions: 0, sentinel_repositions: 0,
            maker_fee_pct: 0.0, taker_fee_pct: 0.0, fee_kills: 0,
            last_buy_price: 0.0, last_sell_price: 0.0,
            bid_prices: vec![], bid_amounts: vec![],
            ask_prices: vec![], ask_amounts: vec![],
            price_history: VecDeque::with_capacity(MAX_HISTORY),
            pnl_history: VecDeque::with_capacity(MAX_HISTORY),
            events: VecDeque::with_capacity(MAX_EVENTS),
            prev_fills: 0, prev_freeze: false,
        }
    }
}

impl DashboardState {
    fn add_event(&mut self, text: &str, class: &str) {
        let now = chrono::Local::now();
        self.events.push_front(LogEvent {
            time: now.format("%H:%M:%S").to_string(),
            text: text.to_string(),
            class: class.to_string(),
        });
        if self.events.len() > MAX_EVENTS {
            self.events.pop_back();
        }
    }
}

type SharedState = Arc<Mutex<DashboardState>>;

// ═══ SVG SPARKLINE GENERATOR ═══
fn render_sparkline(data: &VecDeque<f64>, w: f64, h: f64, stroke: &str, gid: &str) -> String {
    let hex_gray = "#4a5568";
    if data.len() < 3 {
        return format!(
            "<svg viewBox=\"0 0 {} {}\" width=\"100%\" style=\"aspect-ratio:{}/{}\">\
            <text x=\"50%\" y=\"50%\" text-anchor=\"middle\" fill=\"{}\" font-size=\"11\" font-family=\"Inter\">Collecting data...</text></svg>",
            w, h, w as i32, h as i32, hex_gray
        );
    }
    let min = data.iter().cloned().fold(f64::INFINITY, f64::min);
    let max = data.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let range = (max - min).max(0.01);
    let m = 4.0;
    let pw = w - 2.0 * m;
    let ph = h - 2.0 * m;
    let n = data.len() as f64 - 1.0;

    let points: String = data.iter().enumerate().map(|(i, &v)| {
        let x = m + (i as f64 / n) * pw;
        let y = m + ph - ((v - min) / range) * ph;
        format!("{:.1},{:.1}", x, y)
    }).collect::<Vec<_>>().join(" ");

    let first_x = m;
    let last_x = m + pw;

    // Grid lines
    let grid_color = "#1a2035";
    let mut grid = String::new();
    for i in 1..4 {
        let gy = m + (i as f64 / 4.0) * ph;
        grid.push_str(&format!(
            "<line x1=\"{}\" y1=\"{:.1}\" x2=\"{}\" y2=\"{:.1}\" stroke=\"{}\" stroke-width=\"0.5\"/>",
            m, gy, m + pw, gy, grid_color
        ));
    }

    // Last value label
    let last_val = data.back().unwrap_or(&0.0);
    let val_label = if last_val.abs() > 100.0 {
        format!("${:.0}", last_val)
    } else {
        format!("${:.4}", last_val)
    };

    format!(
        r#"<svg viewBox="0 0 {} {}" width="100%" style="aspect-ratio:{}/{}" xmlns="http://www.w3.org/2000/svg">
<defs><linearGradient id="{}" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="{}" stop-opacity="0.2"/><stop offset="100%" stop-color="{}" stop-opacity="0"/>
</linearGradient></defs>
{}
<polygon points="{:.1},{:.1} {} {:.1},{:.1}" fill="url(#{})"/>
<polyline points="{}" fill="none" stroke="{}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
<text x="{}" y="{}" text-anchor="end" fill="{}" font-size="9" font-family="JetBrains Mono">{}</text>
</svg>"#,
        w, h, w as i32, h as i32,
        gid, stroke, stroke,
        grid,
        first_x, h, points, last_x, h, gid,
        points, stroke,
        w - 2.0, m + 10.0, stroke, val_label
    )
}

/// Price sparkline with grid overlay: buy level (green), sell level (red), Binance mid (cyan)
fn render_price_with_grid(data: &VecDeque<f64>, w: f64, h: f64, buy: f64, sell: f64, bnb: f64) -> String {
    if data.len() < 3 {
        return render_sparkline(data, w, h, "#e2e8f0", "gp");
    }
    let min = data.iter().cloned().fold(f64::INFINITY, f64::min);
    let max = data.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let range = (max - min).max(0.01);
    let m = 4.0;
    let pw = w - 2.0 * m;
    let ph = h - 2.0 * m;

    // Helper: price → Y coordinate
    let price_to_y = |p: f64| -> f64 {
        if p <= 0.0 || p < min - range || p > max + range { return -100.0; }
        m + ph - ((p - min) / range) * ph
    };

    let mut overlay = String::new();

    // Buy grid line (green dashed)
    if buy > 0.0 {
        let y = price_to_y(buy);
        if y > 0.0 && y < h {
            overlay.push_str(&format!(
                "<line x1=\"{x1}\" y1=\"{y:.1}\" x2=\"{x2}\" y2=\"{y:.1}\" stroke=\"#10b981\" stroke-width=\"1\" stroke-dasharray=\"4,3\" opacity=\"0.7\"/>\
                 <text x=\"{tx}\" y=\"{ty:.1}\" fill=\"#10b981\" font-size=\"7\" font-family=\"JetBrains Mono\" opacity=\"0.8\">BUY ${p:.0}</text>",
                x1 = m, y = y, x2 = m + pw, tx = m + 2.0, ty = y - 2.0, p = buy
            ));
        }
    }

    // Sell grid line (red dashed)
    if sell > 0.0 {
        let y = price_to_y(sell);
        if y > 0.0 && y < h {
            overlay.push_str(&format!(
                "<line x1=\"{x1}\" y1=\"{y:.1}\" x2=\"{x2}\" y2=\"{y:.1}\" stroke=\"#ef4444\" stroke-width=\"1\" stroke-dasharray=\"4,3\" opacity=\"0.7\"/>\
                 <text x=\"{tx}\" y=\"{ty:.1}\" fill=\"#ef4444\" font-size=\"7\" font-family=\"JetBrains Mono\" text-anchor=\"end\" opacity=\"0.8\">SELL ${p:.0}</text>",
                x1 = m, y = y, x2 = m + pw, tx = m + pw - 2.0, ty = y - 2.0, p = sell
            ));
        }
    }

    // Binance mid line (cyan dotted)
    if bnb > 0.0 {
        let y = price_to_y(bnb);
        if y > 0.0 && y < h {
            overlay.push_str(&format!(
                "<line x1=\"{x1}\" y1=\"{y:.1}\" x2=\"{x2}\" y2=\"{y:.1}\" stroke=\"#06b6d4\" stroke-width=\"0.8\" stroke-dasharray=\"2,2\" opacity=\"0.5\"/>\
                 <text x=\"{tx}\" y=\"{ty:.1}\" fill=\"#06b6d4\" font-size=\"6\" font-family=\"JetBrains Mono\" text-anchor=\"middle\" opacity=\"0.6\">BNB</text>",
                x1 = m, y = y, x2 = m + pw, tx = w / 2.0, ty = y + 8.0
            ));
        }
    }

    // Base sparkline + overlay
    let base = render_sparkline(data, w, h, "#e2e8f0", "gp");
    // Insert overlay before closing </svg>
    base.replace("</svg>", &format!("{}</svg>", overlay))
}

// ═══ HTML FRAGMENT RENDERERS ═══

fn fmt_usd(v: f64) -> String {
    if v >= 0.0 { format!("+${:.2}", v) } else { format!("-${:.2}", v.abs()) }
}
fn fmt_price(v: f64) -> String { format!("${:.2}", v) }
fn fmt_pct(v: f64) -> String { format!("{:.0}%", v * 100.0) }

fn render_regime_badge(id: u64) -> &'static str {
    match id {
        1 => r#"<span class="regime trending">📈 TRENDING</span>"#,
        2 => r#"<span class="regime ranging">↔️ RANGING</span>"#,
        3 => r#"<span class="regime chaos">🌪️ CHAOS</span>"#,
        _ => r#"<span class="regime unknown">⏳ INIT</span>"#,
    }
}

fn render_obi_bar(obi: f64) -> String {
    let pct = (obi.abs() * 50.0).min(50.0);
    let (style, cls) = if obi > 0.0 {
        (format!("left:50%;width:{pct:.1}%"), "bid-pressure")
    } else {
        (format!("right:50%;width:{pct:.1}%"), "ask-pressure")
    };
    let color = if obi > 0.3 { "var(--green)" } else if obi < -0.3 { "var(--red)" } else { "var(--text-muted)" };
    format!(
        r#"<div class="obi-container">
<div class="obi-center"></div>
<div class="obi-fill {cls}" style="{style}"></div>
<span class="obi-l">BIDS</span><span class="obi-r">ASKS</span>
</div>
<div class="obi-val" style="color:{color}">{obi:+.4}</div>"#
    )
}

fn render_conf_bar(conf: f64) -> String {
    let pct = (conf * 100.0).min(100.0);
    let cls = if conf > 0.7 { "high" } else if conf > 0.4 { "med" } else { "low" };
    let color = if conf > 0.7 { "var(--red)" } else if conf > 0.4 { "var(--yellow)" } else { "var(--green)" };
    format!(
        r#"<div class="bar-row"><span class="bar-label">L1 CONFIDENCE</span><span class="bar-val" style="color:{color}">{pct:.0}%</span></div>
<div class="conf-track"><div class="conf-fill {cls}" style="width:{pct:.1}%"></div></div>"#
    )
}

fn render_orderbook(db: &DashboardState) -> String {
    let mut html = String::new();
    let max_vol = db.bid_amounts.iter().chain(db.ask_amounts.iter())
        .filter(|v| **v > 0.0).cloned().fold(0.001_f64, f64::max);

    // Asks (reversed, high to low)
    let asks: Vec<_> = db.ask_prices.iter().zip(db.ask_amounts.iter()).collect();
    for (p, a) in asks.iter().rev() {
        let pct = (*a / max_vol * 100.0) as u32;
        html.push_str(&format!(
            r#"<tr class="ask-row"><td class="price">{:.2}</td><td>{:.5}</td><td class="depth"><div class="bar ask" style="width:{}%"></div>{}%</td></tr>"#,
            p, a, pct, pct
        ));
    }
    html.push_str(&format!(
        r#"<tr class="spread-row"><td colspan="3">▲ SPREAD ${:.1} ▼</td></tr>"#,
        db.spread
    ));
    for (p, a) in db.bid_prices.iter().zip(db.bid_amounts.iter()) {
        let pct = (a / max_vol * 100.0) as u32;
        html.push_str(&format!(
            r#"<tr class="bid-row"><td class="price">{:.2}</td><td>{:.5}</td><td class="depth"><div class="bar bid" style="width:{}%"></div>{}%</td></tr>"#,
            p, a, pct, pct
        ));
    }
    html
}

fn render_events(events: &VecDeque<LogEvent>) -> String {
    events.iter().take(40).map(|e| {
        format!(r#"<div class="log-entry {}"><span class="log-time">{}</span>{}</div>"#,
            e.class, e.time, e.text)
    }).collect::<Vec<_>>().join("")
}

fn render_metric(label: &str, value: &str, cls: &str) -> String {
    format!(r#"<div class="mc {}"><div class="ml">{}</div><div class="mv {}">{}</div></div>"#,
        cls, label, cls, value)
}

// ═══ MAIN RENDER — GENERATES ENTIRE DASHBOARD BODY ═══
fn render_dashboard(db: &DashboardState) -> String {
    let shadow_class = if db.is_shadow_mode { " shadow-mode" } else { "" };
    let freeze_html = if db.sweep_freeze_active {
        r#"<div class="freeze-ind">🚨 SWEEP FREEZE</div>"#
    } else { "" };

    let ghost_html = if db.ghost_transparency < 0.99 {
        r#"<span class="ghost-badge">👻 GHOST</span>"#
    } else { "" };

    let shadow_banner = if db.is_shadow_mode {
        r#"<div class="shadow-banner">🌑 SHADOW MODE — SIMULACE BEZ RIZIKA 🌑</div>"#
    } else { "" };

    let paused_html = if db.paused {
        r#"<span class="paused-badge">⏸ PAUSED</span>"#
    } else { "" };

    let pnl_cls = if db.realized_pnl >= 0.0 { "pos" } else { "neg" };
    let pnl_str = if db.realized_pnl >= 0.0 {
        format!("+${:.4}", db.realized_pnl)
    } else {
        format!("-${:.4}", db.realized_pnl.abs())
    };

    let t2t_cls = if db.t2t_micros > 500 { "neg" } else if db.t2t_micros > 100 { "yellow" } else { "pos" };
    let t2t_str = if db.t2t_micros > 0 { format!("{} µs", db.t2t_micros) } else { "---".to_string() };

    let h = db.uptime_secs / 3600;
    let m = (db.uptime_secs % 3600) / 60;
    let s = db.uptime_secs % 60;
    let uptime_str = format!("{:02}:{:02}:{:02}", h, m, s);

    let price_svg = render_price_with_grid(&db.price_history, 600.0, 200.0, db.last_buy_price, db.last_sell_price, db.binance_mid);
    let pnl_svg = render_sparkline(&db.pnl_history, 500.0, 140.0,
        if db.is_shadow_mode { "#a855f7" } else { "#10b981" },
        "gpnl"
    );

    let pos_str = format!("{:+.5}", db.position);
    let skew_str = fmt_usd(db.inv_skew);
    let macro_val_str = format!("{:+.2}", db.macro_bias);
    let bnb_sweep_str = if db.macro_bnb_sweep_ago < 0 { "—".to_string() } else { format!("{}s", db.macro_bnb_sweep_ago) };
    let shadow_pnl_str = if db.is_shadow_mode {
        format!(r#"<div class="ai-sep"></div><div class="bar-row"><span class="bar-label">SHADOW PnL</span><span class="bar-val purple">${:.6}</span></div>"#, db.shadow_pnl)
    } else { String::new() };

    format!(r##"<div class="root{shadow_class}">
{freeze_html}
{shadow_banner}

<header class="hdr">
<div class="hdr-l">
<div class="logo"><span class="wolf">🐺</span><span class="b">BEROUN</span><span class="s"> SNIPER</span></div>
<span class="ver">v11.1</span>
{regime}
{ghost_html}
{paused_html}
</div>
<div class="hdr-r">
<div class="live-dot"></div>
<span class="mono-s">LIVE</span>
<span class="mono-s">{uptime_str}</span>
</div>
</header>

<div class="row6">
<div class="equity-card" style="grid-column:span 2">
<div class="ml">💎 TOTAL EQUITY</div>
<div class="eq-val">${eq:.2}</div>
<div class="eq-sub">${usd:.2} USD + {btc:.5} ₿</div>
</div>
{m_bid}{m_ask}{m_micro}{m_spread}
</div>

<div class="row6">
{m_pos}{m_pnl}{m_grid}{m_t2t}{m_fills}{m_skew}
</div>

<div class="layout3">
<div style="display:flex;flex-direction:column;gap:0.75rem">
<div class="ai-panel">
<div class="ai-title">🧠 AI INTELLIGENCE</div>
{conf_bar}
<div class="ai-sep"></div>
<div class="ai-label">ORDER BOOK IMBALANCE</div>
{obi_bar}
<div class="ai-sep"></div>
<div class="ai-grid">
<div class="ai-stat"><div class="ai-sl">Toxic</div><div class="ai-sv neg">{toxic}</div></div>
<div class="ai-stat"><div class="ai-sl">FP Rate</div><div class="ai-sv yellow">{fp}</div></div>
<div class="ai-stat"><div class="ai-sl">Success</div><div class="ai-sv pos">{success}</div></div>
<div class="ai-stat"><div class="ai-sl">L1 Skew</div><div class="ai-sv">{l1_skew}</div></div>
<div class="ai-stat"><div class="ai-sl">Uptime</div><div class="ai-sv {uptime_cls}">{uptime}</div></div>
<div class="ai-stat"><div class="ai-sl">👻 Ghost</div><div class="ai-sv {ghost_cls}">{ghost_pct}</div></div>
<div class="ai-stat"><div class="ai-sl">Injections</div><div class="ai-sv purple">{ghost_inj}</div></div>
<div class="ai-stat"><div class="ai-sl">Vel.Reject</div><div class="ai-sv yellow">{ghost_rej}</div></div>
<div class="ai-stat"><div class="ai-sl">🌍 Macro</div><div class="ai-sv {macro_cls}">{macro_val}</div></div>
<div class="ai-stat"><div class="ai-sl">F&G</div><div class="ai-sv">{fng}</div></div>
<div class="ai-stat"><div class="ai-sl">BNB Sweep</div><div class="ai-sv {bnb_cls}">{bnb_sweep}</div></div>
</div>
{shadow_pnl_block}
</div>

<div class="ai-panel" style="border-image:linear-gradient(180deg,var(--cyan),var(--green)) 1;border-left:3px solid">
<div class="ai-title">⚡ DELTA LEAD v11.1</div>
<div class="ai-grid" style="grid-template-columns:1fr 1fr 1fr">
<div class="ai-stat"><div class="ai-sl">Binance</div><div class="ai-sv">{bnb_mid_str}</div></div>
<div class="ai-stat"><div class="ai-sl">Delta</div><div class="ai-sv {delta_cls}">{delta_str}</div></div>
<div class="ai-stat"><div class="ai-sl">Signal</div><div class="ai-sv {delta_cls}">{delta_dir}</div></div>
<div class="ai-stat"><div class="ai-sl">Δ Repos</div><div class="ai-sv cyan">{delta_repos}</div></div>
<div class="ai-stat"><div class="ai-sl">Sentinel</div><div class="ai-sv cyan">{sentinel_repos}</div></div>
<div class="ai-stat"><div class="ai-sl">Fee</div><div class="ai-sv {fee_cls}">{fee_str}</div></div>
</div>
</div>
</div>

<div class="chart-box">
<div class="chart-hdr">PRICE + GRID OVERLAY</div>
{price_svg}
</div>

<div class="chart-box">
<div class="chart-hdr">SYSTEM EVENTS <span class="ev-count">{evcount}</span></div>
<div class="log-scroll">{events}</div>
</div>
</div>

<div class="layout2">
<div class="chart-box">
<div class="chart-hdr">ORDER BOOK (TOP 5)</div>
<table class="book"><thead><tr><th>Price</th><th>Amount</th><th style="width:40%">Depth</th></tr></thead>
<tbody>{orderbook}</tbody></table>
</div>
<div class="chart-box">
<div class="chart-hdr">PnL CURVE {pnl_mode}</div>
{pnl_svg}
</div>
</div>
</div>"##,
        shadow_class = shadow_class,
        freeze_html = freeze_html,
        shadow_banner = shadow_banner,
        regime = render_regime_badge(db.l2_regime_id),
        paused_html = paused_html,
        uptime_str = uptime_str,
        eq = db.total_equity,
        usd = db.wallet_usd,
        btc = db.wallet_btc,
        m_bid = render_metric("Best Bid", &fmt_price(db.best_bid), "pos"),
        m_ask = render_metric("Best Ask", &fmt_price(db.best_ask), "neg"),
        m_micro = render_metric("Micro-Price", &fmt_price(db.micro_price), "purple"),
        m_spread = render_metric("Spread", &format!("${:.1}", db.spread), "yellow"),
        m_pos = render_metric("Position (BTC)", &pos_str, "blue"),
        m_pnl = render_metric("Realized PnL", &pnl_str, pnl_cls),
        m_grid = render_metric("Dynamic Grid", &format!("${:.2}", db.grid_step), "cyan"),
        m_t2t = render_metric("T2T Latence", &t2t_str, t2t_cls),
        m_fills = render_metric("Session Fills", &db.session_fills.to_string(), ""),
        m_skew = render_metric("Inv. Skew", &skew_str, "purple"),
        conf_bar = render_conf_bar(db.l1_confidence),
        obi_bar = render_obi_bar(db.l2_imbalance),
        toxic = db.toxic_flow_hits,
        fp = fmt_pct(db.l1_false_positive_rate),
        success = fmt_pct(db.l1_sweep_success_rate),
        l1_skew = fmt_usd(db.l1_skew_usd),
        uptime = fmt_pct(db.l1_uptime_pct),
        uptime_cls = if db.l1_uptime_pct < 0.3 { "neg" } else if db.l1_uptime_pct < 0.7 { "yellow" } else { "pos" },
        ghost_pct = fmt_pct(db.ghost_transparency),
        ghost_cls = if db.ghost_transparency < 0.5 { "purple" } else { "pos" },
        ghost_inj = db.ghost_injections,
        ghost_rej = db.ghost_velocity_rejects,
        ghost_html = ghost_html,
        macro_val = macro_val_str,
        macro_cls = if db.macro_bias < -0.3 { "neg" } else if db.macro_bias > 0.3 { "pos" } else { "" },
        fng = db.macro_fear_greed,
        bnb_sweep = bnb_sweep_str,
        bnb_cls = if db.macro_bnb_sweep_ago >= 0 && db.macro_bnb_sweep_ago < 10 { "neg" } else { "" },
        shadow_pnl_block = shadow_pnl_str,
        // v11.1 Delta Lead
        bnb_mid_str = if db.binance_mid > 0.0 { format!("${:.0}", db.binance_mid) } else { "---".to_string() },
        delta_str = format!("{:+.1} bps", db.delta_lead_bps),
        delta_cls = if db.delta_lead_bps > 0.5 { "pos" } else if db.delta_lead_bps < -0.5 { "neg" } else { "" },
        delta_dir = if db.delta_signal > 100 { "🟢 BULL" } else if db.delta_signal < -100 { "🔴 BEAR" } else { "⚪ FLAT" },
        delta_repos = db.delta_repositions,
        sentinel_repos = db.sentinel_repositions,
        // v11.3 Fee Sentinel
        fee_str = if db.maker_fee_pct == 0.0 && db.taker_fee_pct == 0.0 {
            "FREE".to_string()
        } else {
            format!("M{:.2}% T{:.2}%", db.maker_fee_pct, db.taker_fee_pct)
        },
        fee_cls = if db.maker_fee_pct == 0.0 && db.taker_fee_pct == 0.0 { "pos" } else { "neg" },
        price_svg = price_svg,
        evcount = db.events.len(),
        events = render_events(&db.events),
        orderbook = render_orderbook(db),
        pnl_svg = pnl_svg,
        pnl_mode = if db.is_shadow_mode { "(SHADOW)" } else { "(LIVE)" },
    )
}

// ═══ HANDLERS ═══
async fn index_handler() -> Html<&'static str> {
    Html(include_str!("../dashboard.html"))
}

async fn sse_handler(
    State(state): State<SharedState>,
) -> Sse<impl futures_util::Stream<Item = std::result::Result<Event, Infallible>>> {
    let stream = futures_util::stream::unfold(state, |state| async {
        tokio::time::sleep(Duration::from_millis(300)).await;
        let html = {
            let db = state.lock().expect("Lock failed");
            render_dashboard(&db)
        };
        let event = Event::default()
            .event("dashboard")
            .data(html);
        Some((Ok::<_, Infallible>(event), state))
    });
    Sse::new(stream).keep_alive(
        KeepAlive::new().interval(Duration::from_secs(15))
    )
}

// ═══ MAIN ═══
#[tokio::main]
async fn main() -> Result<()> {
    dotenv().ok();

    // ═══ SINGLE-INSTANCE LOCK ═══
    let lock_file = std::fs::File::create("/tmp/beroun-dashboard.lock")
        .expect("Failed to create dashboard lock file");
    use fs2::FileExt as Fs2FileExt;
    if lock_file.try_lock_exclusive().is_err() {
        eprintln!("[DASHBOARD] Another instance already running — aborting.");
        std::process::exit(1);
    }
    let _lock_guard = lock_file;

    println!("--- BEROUN DASHBOARD v11.1 (HTMX+SSE) ---");

    let state: SharedState = Arc::new(Mutex::new(DashboardState::default()));
    let state_for_server = state.clone();
    let start_time = Instant::now();

    // Spawn HTTP server
    tokio::spawn(async move {
        let app = Router::new()
            .route("/", get(index_handler))
            .route("/sse", get(sse_handler))
            .with_state(state_for_server)
            .layer(CorsLayer::permissive());

        let listener = {
            let mut bound = None;
            for attempt in 1..=15 {
                // SO_REUSEADDR+SO_REUSEPORT for graceful restart (old socket may linger)
                // Double-start is prevented by flock guard above, not by socket binding
                let socket = match tokio::net::TcpSocket::new_v4() {
                    Ok(s) => s,
                    Err(e) => {
                        eprintln!("[DASHBOARD] Socket creation failed: {e}");
                        tokio::time::sleep(Duration::from_secs(3)).await;
                        continue;
                    }
                };
                let _ = socket.set_reuseaddr(true);
                #[cfg(unix)]
                {
                    let _ = socket.set_reuseport(true);
                }
                match socket.bind("0.0.0.0:3000".parse().unwrap_or_else(|_| std::net::SocketAddr::from(([0,0,0,0], 3000))))
                    .and_then(|()| socket.listen(1024))
                {
                    Ok(l) => { bound = Some(l); break; }
                    Err(e) => {
                        eprintln!("[DASHBOARD] Bind attempt {}/15 failed: {} — retrying in 3s", attempt, e);
                        tokio::time::sleep(Duration::from_secs(3)).await;
                    }
                }
            }
            match bound {
                Some(l) => l,
                None => { eprintln!("[DASHBOARD] FATAL: Cannot bind :3000"); return; }
            }
        };
        println!("[DASHBOARD] Online at http://0.0.0.0:3000 (HTMX+SSE, zero JS)");
        if let Err(e) = axum::serve(listener, app).await {
            eprintln!("[DASHBOARD] Server error: {}", e);
        }
    });

    // mmap setup
    let r_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;
    let e_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;
    let engine = unsafe { &*(e_mmap.as_ptr() as *const EngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const RiskState) };
    let scale = PRICE_SCALE_I as f64;

    // Add startup event
    {
        let mut db = state.lock().expect("Lock failed");
        db.add_event("Dashboard v10.1 started (HTMX+SSE)", "trade");
    }

    let mut tick = 0u64;

    loop {
        let bb = engine.best_bid.load(Ordering::Acquire) as f64 / scale;
        let ba = engine.best_ask.load(Ordering::Acquire) as f64 / scale;
        let mid = (bb + ba) / 2.0;
        let spread = ba - bb;
        let pos = engine.net_position.load(Ordering::Acquire) as f64 / scale;
        let pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / scale;
        let w_btc = engine.wallet_btc.load(Ordering::Acquire) as f64 / scale;
        let w_usd = engine.wallet_usd.load(Ordering::Acquire) as f64 / scale;
        let micro_p = engine.micro_price.load(Ordering::Acquire) as f64 / scale;
        let g_step = risk.grid_step.load(Ordering::Acquire) as f64 / scale;
        let g_size = risk.grid_size.load(Ordering::Acquire);
        let max_pos = risk.max_inv_delta.load(Ordering::Acquire) as f64 / scale;
        let t2t = engine.t2t_micros.load(Ordering::Acquire);
        let fills = engine.session_fill_count.load(Ordering::Acquire);

        let inv_skew = if max_pos > 0.0 {
            let ratio = (pos / max_pos).clamp(-1.0, 1.0);
            -ratio * g_step * 2.0
        } else { 0.0 };

        // Order book
        let mut bp = Vec::with_capacity(5);
        let mut bv = Vec::with_capacity(5);
        let mut ap = Vec::with_capacity(5);
        let mut av = Vec::with_capacity(5);
        for i in 0..5 {
            let p = engine.bids[i].price.load(Ordering::Acquire) as f64 / scale;
            let a = engine.bids[i].amount.load(Ordering::Acquire) as f64 / scale;
            if p > 0.0 { bp.push(p); bv.push(a.abs()); }
            let p = engine.asks[i].price.load(Ordering::Acquire) as f64 / scale;
            let a = engine.asks[i].amount.load(Ordering::Acquire) as f64 / scale;
            if p > 0.0 { ap.push(p); av.push(a.abs()); }
        }

        // AI fields
        let l1_conf = engine.l1_confidence_score.load(Ordering::Acquire) as f64 / 10000.0;
        let l2_regime = engine.l2_regime_id.load(Ordering::Acquire);
        let shadow = engine.is_shadow_mode.load(Ordering::Acquire) == 1;
        let shadow_pnl = engine.shadow_pnl.load(Ordering::Acquire) as f64 / scale;
        let toxic = engine.toxic_flow_hits.load(Ordering::Acquire);
        let freeze_until = engine.sweep_freeze_until.load(Ordering::Acquire);
        let now_ms = SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        let freeze_active = freeze_until > now_ms;
        let l1_skew = engine.l1_skew_adjustment.load(Ordering::Acquire) as f64 / scale;
        let obi = engine.l2_imbalance.load(Ordering::Acquire) as f64 / scale;
        let fp_rate = engine.l1_false_positive_rate.load(Ordering::Acquire) as f64 / 10000.0;
        let success_rate = engine.l1_sweep_success_rate.load(Ordering::Acquire) as f64 / 10000.0;
        let paused = risk.paused.load(Ordering::Acquire) != 0;
        let l1_uptime = engine.l1_uptime_pct.load(Ordering::Acquire) as f64 / 10000.0;
        let ghost_trans = engine.ghost_transparency.load(Ordering::Acquire) as f64 / 10000.0;
        let ghost_inj = engine.ghost_injections.load(Ordering::Acquire);
        let ghost_rej = engine.ghost_velocity_rejects.load(Ordering::Acquire);
        let ghost_mask = engine.ghost_active_mask.load(Ordering::Acquire);
        let macro_bias_raw = engine.macro_bias.load(Ordering::Acquire) as f64 / 10000.0;
        let macro_fng = engine.macro_fear_greed.load(Ordering::Acquire);
        let bnb_ts = engine.binance_sweep_ts.load(Ordering::Acquire);
        let now_ms_d = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis() as u64;
        let bnb_ago = if bnb_ts > 0 { ((now_ms_d - bnb_ts) / 1000) as i64 } else { -1 };
        // v11.1 Delta Lead
        let bnb_mid = engine.binance_mid_price.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;
        let delta_bps = engine.delta_lead_raw_bps.load(Ordering::Acquire) as f64 / 100.0;
        let delta_sig = engine.delta_lead_signal.load(Ordering::Acquire);
        let delta_repos = engine.delta_repositions.load(Ordering::Acquire);
        let sentinel_repos = engine.sentinel_repositions.load(Ordering::Acquire);
        // v11.3 Fee Sentinel
        let maker_f = engine.maker_fee_bps.load(Ordering::Acquire) as f64 / 10000.0 * 100.0;
        let taker_f = engine.taker_fee_bps.load(Ordering::Acquire) as f64 / 10000.0 * 100.0;
        let f_kills = engine.fee_kills.load(Ordering::Acquire);

        {
            let mut db = state.lock().expect("Lock failed");
            db.best_bid = bb;
            db.best_ask = ba;
            db.mid_price = mid;
            db.spread = spread;
            db.micro_price = micro_p;
            db.position = pos;
            db.inv_skew = inv_skew;
            db.realized_pnl = pnl;
            db.wallet_btc = w_btc;
            db.wallet_usd = w_usd;
            db.total_equity = w_usd + (w_btc * mid);
            db.grid_step = g_step;
            db.grid_size = g_size;
            db.t2t_micros = t2t;
            db.uptime_secs = start_time.elapsed().as_secs();
            db.l1_confidence = l1_conf;
            db.l2_regime_id = l2_regime;
            db.is_shadow_mode = shadow;
            db.shadow_pnl = shadow_pnl;
            db.toxic_flow_hits = toxic;
            db.sweep_freeze_active = freeze_active;
            db.l1_skew_usd = l1_skew;
            db.l2_imbalance = obi;
            db.l1_false_positive_rate = fp_rate;
            db.l1_sweep_success_rate = success_rate;
            db.session_fills = fills;
            db.paused = paused;
            db.l1_uptime_pct = l1_uptime;
            db.ghost_transparency = ghost_trans;
            db.ghost_injections = ghost_inj;
            db.ghost_velocity_rejects = ghost_rej;
            db.ghost_active_mask = ghost_mask;
            db.macro_bias = macro_bias_raw;
            db.macro_fear_greed = macro_fng;
            db.macro_bnb_sweep_ago = bnb_ago;
            db.binance_mid = bnb_mid;
            db.delta_lead_bps = delta_bps;
            db.delta_signal = delta_sig;
            db.delta_repositions = delta_repos;
            db.sentinel_repositions = sentinel_repos;
            db.maker_fee_pct = maker_f;
            db.taker_fee_pct = taker_f;
            db.fee_kills = f_kills;
            db.last_buy_price = engine.last_buy_price.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;
            db.last_sell_price = engine.last_sell_price.load(Ordering::Acquire) as f64 / sniper_types::PRICE_SCALE;
            db.bid_prices = bp;
            db.bid_amounts = bv;
            db.ask_prices = ap;
            db.ask_amounts = av;

            // Update sparkline history (every 300ms = ~3 points/sec)
            if tick.is_multiple_of(6) && mid > 0.0 {
                db.price_history.push_back(mid);
                if db.price_history.len() > MAX_HISTORY { db.price_history.pop_front(); }
                db.pnl_history.push_back(pnl);
                if db.pnl_history.len() > MAX_HISTORY { db.pnl_history.pop_front(); }
            }

            // Detect events
            if fills > db.prev_fills && db.prev_fills > 0 {
                db.add_event(&format!("⚡ Trade #{} executed", fills), "trade");
            }
            db.prev_fills = fills;

            if freeze_active && !db.prev_freeze {
                db.add_event("🚨 Sweep detected! Freeze active.", "sweep");
            }
            if !freeze_active && db.prev_freeze {
                db.add_event("✅ Sweep freeze expired.", "trade");
            }
            db.prev_freeze = freeze_active;
        }

        tick += 1;
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}
