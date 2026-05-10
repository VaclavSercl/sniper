use std::fs::OpenOptions;

fn main() {
    let path = sniper_types::ENGINE_STATE_PATH;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(path);

    if let Ok(f) = file {
        let mut mmap = unsafe { memmap2::MmapMut::map_mut(&f).unwrap() };
        let state = unsafe { &*mmap.as_ptr().cast::<sniper_types::EngineState>() };
        
        let old = state.high_water_mark_usd.load(std::sync::atomic::Ordering::Relaxed);
        state.high_water_mark_usd.store(0, std::sync::atomic::Ordering::Relaxed);
        
        println!("Hydra HWM reset from {} to 0", old);
    } else {
        println!("Could not open engine state");
    }
}
