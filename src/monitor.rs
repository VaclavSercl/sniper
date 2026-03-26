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
        println!(" {d}│{x} {m}Micro-Price: ${x} {w}{:<14.2}{x} {d}│  │{x} {c}Dyn Grid:   ${x} {w}{:<10.2}{x}     {d}│{x}", micro_p, dyn_grid);
        println!(" {d}│{x} {d}Mid-Price:   ${x} {w}{:<14.2}{x} {d}│  │{x} {m}Inv Skew:   ${x} {skew_c}{:<+10.2}{x}     {d}│{x}", mid, skew);
        println!(" {d}│{x} {g}Best Bid:    ${x} {w}{:<14.2}{x} {d}│  │{x} {pnl_c}PnL:        ${x} {pnl_c}{:<+10.2}{x}     {d}│{x}", best_bid, pnl);
        println!(" {d}│{x} {y}Spread:      ${x} {w}{:<14.2}{x} {d}│  │{x}                              {d}│{x}", spread);
        println!(" {d}└────────────────────────────────┘  └──────────────────────────────┘{x}");
        println!();
        println!(" {d}┌─ AKTIVNÍ GRID ─────────────────┐  ┌─ PORTFOLIO ───────────────────┐{x}");
        println!(" {d}│{x} {r}Prodej (Ask): ${x} {w}{:<12.2}{x}  {d}│  │{x} Net Pozice:  {pos_c}{:>+10.5}{x} BTC  {d}│{x}", last_sell, net_pos);
        println!(" {d}│{x} {g}Nákup  (Bid): ${x} {w}{:<12.2}{x}  {d}│  │{x} BTC Wallet:  {w}{:>10.5}{x} BTC  {d}│{x}", last_buy, btc_w);
        println!(" {d}│{x} {y}Odstup:       ${x} {w}{:<12.2}{x}  {d}│  │{x} USD Wallet:  {w}${:>10.2}{x}      {d}│{x}", if last_sell > 0.0 && last_buy > 0.0 { last_sell - last_buy } else { 0.0 }, usd_w);
        println!(" {d}└────────────────────────────────┘  └──────────────────────────────┘{x}");
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
