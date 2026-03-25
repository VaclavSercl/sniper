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

fn main() -> Result<()> {
    let e_mmap = init_mmap_ptr::<EngineState>(&ENGINE_STATE_PATH)?;
    let r_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;

    let engine = unsafe { &*(e_mmap.as_ptr() as *const EngineState) };
    let risk = unsafe { &*(r_mmap.as_ptr() as *const RiskState) };

    loop {
        let pos = engine.net_position.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        let price = engine.best_bid.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;
        
        let mut final_bias = 0.0;
        if pos > 0.001 { final_bias = -1.5; }
        else if pos < -0.001 { final_bias = 1.5; }

        let bias_scaled = (final_bias * PRICE_SCALE_I as f64) as i64;
        risk.bias_offset.store(bias_scaled, Ordering::Release);

        let lat = engine.latency_ns.load(Ordering::Acquire);
        let pnl = engine.realized_pnl.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64;

        if price > 0.0 {
            println!("SOVEREIGN AI | Price: {:.2} | Pos: {:.4} | Bias: {:.1} | Lat: {:.2}ms | PnL: {:.2}", 
                price, pos, final_bias, lat as f64 / 1_000_000.0, pnl
            );
        }

        std::thread::sleep(Duration::from_millis(500));
    }
}
