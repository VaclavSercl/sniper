use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use std::time::Duration;
use memmap2::MmapOptions;
use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE};

const ESC: char = 27 as char;

fn main() -> anyhow::Result<()> {
    let eng_file = OpenOptions::new().read(true).open(&*ENGINE_STATE_PATH)?;
    let eng_mmap = unsafe { MmapOptions::new().map(&eng_file)? };
    let engine = unsafe { &*(eng_mmap.as_ptr() as *const EngineState) };

    let risk_file = OpenOptions::new().read(true).open(&*RISK_STATE_PATH)?;
    let risk_mmap = unsafe { MmapOptions::new().map(&risk_file)? };
    let risk = unsafe { &*(risk_mmap.as_ptr() as *const RiskState) };

    // Hide cursor + clear
    print!("{ESC}[2J{ESC}[?25l");

    // Restore cursor on exit
    ctrlc::handle(|| {
        print!("{ESC}[?25h\n");
        std::process::exit(0);
    });

    let scale = PRICE_SCALE;

    loop {
        let best_bid = engine.best_bid.load(Ordering::Acquire) as f64 / scale;
        let best_ask = engine.best_ask.load(Ordering::Acquire) as f64 / scale;
        let spread = best_ask - best_bid;
        let mid = (best_bid + best_ask) / 2.0;

        let micro_p = engine.micro_price.load(Ordering::Acquire) as f64 / scale;
        let skew = engine.current_skew.load(Ordering::Acquire) as f64 / scale;
        let t2t = engine.t2t_micros.load(Ordering::Acquire);

        let net_pos = engine.net_position.load(Ordering::Acquire) as f64 / scale;
        let btc_w = engine.wallet_btc.load(Ordering::Acquire) as f64 / scale;
        let usd_w = engine.wallet_usd.load(Ordering::Acquire) as f64 / scale;
        let pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / scale;

        let dyn_grid = risk.grid_step.load(Ordering::Acquire) as f64 / scale;
        let paused = risk.paused.load(Ordering::Acquire) != 0;

        let last_buy = engine.last_buy_price.load(Ordering::Acquire) as f64 / scale;
        let last_sell = engine.last_sell_price.load(Ordering::Acquire) as f64 / scale;

        // ANSI colors
        let g = "\x1b[32m"; // green
        let r = "\x1b[31m"; // red
        let y = "\x1b[33m"; // yellow
        let c = "\x1b[36m"; // cyan
        let m = "\x1b[35m"; // magenta
        let w = "\x1b[37;1m"; // white bold
        let d = "\x1b[90m"; // dim
        let x = "\x1b[0m"; // reset

        let status = if paused { format!("{y}⏸  PAUSED{x}") } else { format!("{g}🟢 RUNNING{x}") };
        let skew_c = if skew < 0.0 { r } else if skew > 0.0 { g } else { d };
        let pos_c = if net_pos < 0.0 { r } else if net_pos > 0.0 { g } else { d };
        let pnl_c = if pnl < 0.0 { r } else if pnl > 0.0 { g } else { d };

        // Move cursor home
        print!("{ESC}[1;1H");

        let line = format!("{d}══════════════════════════════════════════════════════════════════════{x}");
        println!("{line}");
        println!(" {w}🐺 BEROUN SNIPER{x} {c}v6.0{x}          {status}          {d}CPU Core 1{x}");
        println!("{line}");
        println!();
        println!(" {d}┌─ TRH: tBTCUSD ─────────────────┐  ┌─ BOT METRIKY ────────────────┐{x}");
        println!(" {d}│{x} {r}Best Ask:    ${x} {w}{:<14.2}{x} {d}│  │{x} {c}T2T Latence:{x}  {w}{:>6}{x} µs      {d}│{x}", best_ask, t2t);
        let obi = engine.l2_imbalance.load(Ordering::Acquire) as f64 / scale;
        let cur_usd = engine.current_order_usd.load(Ordering::Acquire) as f64 / scale;
        let obi_c = if obi > 0.1 { g } else if obi < -0.1 { r } else { d };

        let ai_bias = engine.current_ai_bias.load(Ordering::Acquire) as f64 / scale;
        let ai_c = if ai_bias > 0.0 { g } else if ai_bias < 0.0 { r } else { d };

        // AI Heartbeat check
        let ai_hb = engine.ai_heartbeat_ms.load(Ordering::Acquire);
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH).unwrap_or_default()
            .as_millis() as u64;
        let hb_age_s = if ai_hb > 0 { now_ms.saturating_sub(ai_hb) / 1000 } else { 999 };
        let (hb_status, hb_c) = if hb_age_s < 10 { ("LIVE", g) } else if hb_age_s < 30 { ("SLOW", y) } else { ("DEAD", r) };

        println!(" {d}│{x} {m}Micro-Price: ${x} {w}{:<14.2}{x} {d}│  │{x} {c}Dyn Grid:   ${x} {w}{:<10.2}{x}     {d}│{x}", micro_p, dyn_grid);
        println!(" {d}│{x} {d}Mid-Price:   ${x} {w}{:<14.2}{x} {d}│  │{x} {m}Inv Skew:   ${x} {skew_c}{:<+10.2}{x}     {d}│{x}", mid, skew);
        println!(" {d}│{x} {g}Best Bid:    ${x} {w}{:<14.2}{x} {d}│  │{x} {obi_c}L2 OBI:      {x} {obi_c}{:<+10.3}{x}     {d}│{x}", best_bid, obi);
        println!(" {d}│{x} {y}Spread:      ${x} {w}{:<14.2}{x} {d}│  │{x} {c}Order:    $ {x} {w}{:<10.2}{x}     {d}│{x}", spread, cur_usd);
        println!(" {d}│{x}                                {d}│  │{x} {ai_c}AI Bias:  $ {x} {ai_c}{:<+10.2}{x}     {d}│{x}", ai_bias);
        println!(" {d}│{x}                                {d}│  │{x} {hb_c}AI Heart:   {x} {hb_c}{:<4}{x} {d}({hb_age_s}s ago) {x}{d}│{x}", hb_status);
        println!(" {d}└────────────────────────────────┘  └──────────────────────────────┘{x}");
        println!();
        let n_buys = engine.active_buy_ids.iter().filter(|s| s.load(Ordering::Acquire) > 0).count();
        let n_sells = engine.active_sell_ids.iter().filter(|s| s.load(Ordering::Acquire) > 0).count();
        let grid_size = risk.grid_size.load(Ordering::Acquire);

        println!(" {d}┌─ HYDRA GRID v9.0 ──────────────┐  ┌─ PORTFOLIO ───────────────────┐{x}");
        println!(" {d}│{x} {r}Prodej (Ask): ${x} {w}{:<12.2}{x}  {d}│  │{x} Net Pozice:  {pos_c}{:>+10.5}{x} BTC  {d}│{x}", last_sell, net_pos);
        println!(" {d}│{x}    {d}└─ Active: {g}B×{}{x} {r}S×{}{x} / {:<2}{d}│  │{x} BTC Wallet:  {w}{:>10.5}{x} BTC  {d}│{x}", n_buys, n_sells, grid_size, btc_w);
        println!(" {d}│{x} {g}Nákup  (Bid): ${x} {w}{:<12.2}{x}  {d}│  │{x} USD Wallet:  {w}${:>10.2}{x}      {d}│{x}", last_buy, usd_w);
        println!(" {d}│{x}    {d}└─ Levels: {:<12}{x}  {d}│  │{x} {pnl_c}PnL:         ${x} {pnl_c}{:<+10.2}{x}     {d}│{x}",
                 format!("{}/{}", n_buys + n_sells, grid_size * 2), pnl);
        println!(" {d}└────────────────────────────────┘  └──────────────────────────────┘{x}");
        println!("{line}");

        // ── PnL TRACKER (v7.0) ──
        let aep = engine.average_entry_price.load(Ordering::Acquire) as f64 / scale;
        let r_pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / scale;
        let u_pnl = if aep > 0.0 && net_pos.abs() > 1e-8 { (micro_p - aep) * net_pos } else { 0.0 };
        let total_pnl = r_pnl + u_pnl;
        let total_c = if total_pnl > 0.0 { g } else if total_pnl < 0.0 { r } else { d };
        let r_c = if r_pnl > 0.0 { g } else if r_pnl < 0.0 { r } else { d };
        let u_c = if u_pnl > 0.0 { g } else if u_pnl < 0.0 { r } else { d };

        let alpha = engine.ai_alpha_usd.load(Ordering::Acquire) as f64 / scale;
        let alpha_c = if alpha > 0.0 { g } else if alpha < 0.0 { r } else { d };

        println!(" {d}┌─ FINANČNÍ VÝSLEDEK ────────────────────────────────────────────────┐{x}");
        println!(" {d}│{x} {r_c}Realized PnL:   ${x} {r_c}{:<+10.2}{x}  {d}│{x}  {u_c}Unrealized PnL: ${x} {u_c}{:<+10.2}{x}  {d}│{x}  {total_c}TOTAL: ${x} {total_c}{:<+10.2}{x} {d}│{x}", r_pnl, u_pnl, total_pnl);
        println!(" {d}│{x} {d}Avg Entry:      ${x} {w}{:<10.2}{x}  {d}│{x}  {alpha_c}AI Alpha:       ${x} {alpha_c}{:<+10.4}{x}  {d}│{x}              {d}│{x}", aep, alpha);
        println!(" {d}└────────────────────────────────────────────────────────────────────┘{x}");

        // ── CAPITAL GUARD (v9.0) ──
        let auth_cap = risk.authorized_capital.load(Ordering::Acquire) as f64 / scale;
        let dll = risk.daily_loss_limit.load(Ordering::Acquire) as f64 / scale;
        let pos_val = net_pos.abs() * micro_p;
        let cap_used_pct = if auth_cap > 0.0 { (pos_val / auth_cap * 100.0).min(100.0) } else { 0.0 };
        let dll_pct = if dll > 0.0 && r_pnl < 0.0 { (r_pnl.abs() / dll * 100.0).min(100.0) } else { 0.0 };
        let dll_c = if dll_pct > 80.0 { r } else if dll_pct > 50.0 { y } else { g };
        let cap_bar: String = (0..20).map(|i| if (i as f64) < cap_used_pct / 5.0 { '█' } else { '░' }).collect();

        println!(" {d}┌─ CAPITAL GUARD ─────────────────────────────────────────────────────┐{x}");
        println!(" {d}│{x} {c}Auth Capital: ${x} {w}{:<10.2}{x}  {d}│{x}  {c}In Use: ${x} {w}{:<8.2}{x} ({cap_used_pct:.0}%) {y}{cap_bar}{x} {d}│{x}", auth_cap, pos_val);
        println!(" {d}│{x} {dll_c}Daily PnL:   ${x} {dll_c}{:<+10.2}{x}  {d}│{x}  {dll_c}Loss Limit: -${x} {dll_c}{:<8.2}{x} ({dll_pct:.0}% used)     {d}│{x}", r_pnl, dll);
        println!(" {d}└────────────────────────────────────────────────────────────────────┘{x}");
        println!("{line}");
        println!(" {d}Press Ctrl+C to exit{x}");

        use std::io::Write;
        let _ = std::io::stdout().flush();
        std::thread::sleep(Duration::from_millis(200));
    }
}

mod ctrlc {
    use std::sync::atomic::{AtomicBool, Ordering};
    static HANDLED: AtomicBool = AtomicBool::new(false);

    pub fn handle(f: impl Fn() + Send + 'static) {
        if HANDLED.swap(true, Ordering::SeqCst) { return; }
        std::thread::spawn(move || {
            let (tx, rx) = std::sync::mpsc::channel();
            let _ = signal_hook::iterator::Signals::new(&[signal_hook::consts::SIGINT, signal_hook::consts::SIGTERM])
                .map(|mut signals| {
                    for _ in signals.forever() { let _ = tx.send(()); break; }
                });
            let _ = rx.recv();
            f();
        });
    }
}
