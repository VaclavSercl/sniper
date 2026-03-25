use std::time::Duration;
use std::fs::OpenOptions;
use std::sync::atomic::Ordering;
use memmap2::MmapMut;
use anyhow::Result;

use beroun_types::{RiskState, RISK_STATE_PATH, PRICE_SCALE_I};

fn init_mmap_ptr<T: Default>(path: &str) -> Result<MmapMut> {
    let file = OpenOptions::new().read(true).write(true).create(true).open(path)?;
    file.set_len(std::mem::size_of::<T>() as u64)?;
    Ok(unsafe { MmapMut::map_mut(&file)? })
}

fn main() -> Result<()> {
    let r_mmap = init_mmap_ptr::<RiskState>(&RISK_STATE_PATH)?;
    let risk = unsafe { &*(r_mmap.as_ptr() as *const RiskState) };

    loop {
        let paused = risk.paused.load(Ordering::Acquire);
        println!("RISK CONTROL | Paused: {} | Order USD: {:.1} | Grid Step: {:.1}", 
            paused != 0,
            risk.order_usd.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
            risk.grid_step.load(Ordering::Acquire) as f64 / PRICE_SCALE_I as f64,
        );
        std::thread::sleep(Duration::from_secs(5));
    }
}
