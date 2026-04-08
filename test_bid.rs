use std::sync::atomic::Ordering;
fn main() {
    let f = std::fs::OpenOptions::new().read(true).open("/dev/shm/sniper/engine_state.bin").unwrap();
    let mmap = unsafe { memmap2::MmapOptions::new().map(&f).unwrap() };
    let bytes: [u8; 8] = mmap[64..72].try_into().unwrap();
    let bid = u64::from_le_bytes(bytes);
    println!("RUST BEST BID: {}", bid);
}
