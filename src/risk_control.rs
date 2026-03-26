use std::time::Duration;
use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use memmap2::MmapMut;
use anyhow::Result;

use beroun_types::{EngineState, RiskState, ENGINE_STATE_PATH, RISK_STATE_PATH, PRICE_SCALE_I};

fn init_mmap_ptr<T: Default>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

#[tokio::main]
async fn main() -> Result<()> {
    let r_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;
    let e_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;
    
    let risk = unsafe { &*(r_mmap.as_ptr() as *const RiskState) };
    let engine = unsafe { &*(e_mmap.as_ptr() as *const EngineState) };

    println!("\x1B[2J\x1B[H"); // Vyčistit obrazovku
    
    loop {
        let paused = risk.paused.load(Ordering::Acquire) != 0;
        let order_usd = risk.order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let grid_step = risk.grid_step.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let bias = risk.bias_offset.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;

        let price = engine.best_bid.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let pos = engine.net_position.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let w_usd = engine.wallet_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let w_btc = engine.wallet_btc.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;

        println!("\x1B[H"); // Skočit na začátek (refresh bez blikání)
        println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
        println!(" 🐺 BEROUN SNIPER v5.1 - RISK CONTROL CENTER ");
        println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
        println!(" STATUS:   {}", if paused { "\x1B[31mPAUSED\x1B[0m " } else { "\x1B[32mACTIVE\x1B[0m " });
        println!(" PRICE:    \x1B[36m{:.2} USD\x1B[0m", price);
        println!(" POSITION: {:.5} BTC", pos);
        println!(" PnL:      \x1B[33m{:.2} USD\x1B[0m", pnl);
        println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
        println!(" WALLET USD: {:.2} USD", w_usd);
        println!(" WALLET BTC: {:.5} BTC", w_btc);
        println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
        println!(" GRID STEP: {:.1} USD | ORDER: {:.1} USD | BIAS: {:.1}", grid_step, order_usd, bias);
        println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
        println!(" (Stiskněte Ctrl+C pro ukončení náhledu)");

        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}
