use std::fs::OpenOptions;

fn main() {
    let path = sniper_types::ENGINE_STATE_PATH;
    let file = OpenOptions::new().read(true).open(path).expect("no file");
    let mmap = unsafe { memmap2::MmapOptions::new().map(&file).unwrap() };
    let state = unsafe { &*mmap.as_ptr().cast::<sniper_types::EngineState>() };
    
    let w_usd = state.wallet_usd.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE;
    let w_btc = state.wallet_btc.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE;
    let b_bid = state.best_bid.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE;
    let hwm = state.high_water_mark_usd.load(std::sync::atomic::Ordering::Relaxed) as f64 / sniper_types::PRICE_SCALE;
    
    println!("Wallet USD: {}", w_usd);
    println!("Wallet BTC: {}", w_btc);
    println!("Best Bid: {}", b_bid);
    println!("Total Wallet: {}", w_usd + (w_btc * b_bid));
    println!("HWM: {}", hwm);
}
