# Beroun Sniper Development Rules

## Tech Stack
- **Language:** Rust (Edition 2024)
- **Architecture:** Zero-copy, Allocation-free hot path, Mmap IPC
- **OS:** Linux (Ubuntu 22.04+)

## Coding Standards (HFT Specific)
1. **No Heap Allocations:** Never use `Box`, `Vec`, or `String` in the main execution loop (`main.rs`).
2. **Atomic Consistency:** Use `AtomicU64/I64` with `Acquire/Release` ordering for all shared state.
3. **Fixed-Point Math:** All prices must use `PRICE_SCALE = 1e8`. No `f64` in risk calculations.
4. **Cache Alignment:** All IPC structures must use `#[repr(C, align(64))]` to prevent false sharing.
5. **No Unwraps:** Always use safe matching or `anyhow` context.

## Build & Sync Commands
- **Build Release:** `cargo build --release`
- **Optimize (PGO):** `bash optimize.sh`
- **Run Sniper:** `./target/release/beroun-core`
- **Sync GitHub:** `git add . && git commit -m "feat: sync latest changes" && git push`
