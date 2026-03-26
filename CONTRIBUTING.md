# Contributing — Beroun Sniper

## Tech Stack
- **Language:** Rust (Edition 2024)
- **Architecture:** Zero-copy, Allocation-free hot path, Mmap IPC
- **OS:** Linux (Ubuntu 22.04+)

## Coding Standards (HFT Specific)

1. **No Heap Allocations:** Never use `Box`, `Vec`, or `String` in the HFT loop (`main.rs` Task 3). Use stack buffers and `format!` only for order strings sent via channel.
2. **Atomic Consistency:** Use `AtomicU64/I64` with `SeqCst` for mmap writes, `Acquire/Release` for reads.
3. **Fixed-Point Math:** All prices use `PRICE_SCALE = 1e8`. No `f64` in risk calculations on hot path.
4. **Cache Alignment:** All IPC structures use `#[repr(C, align(64))]`. Padding must maintain 64-byte cache line boundaries.
5. **No Unwraps:** Always use safe matching or `anyhow` context. `unwrap()` is forbidden.
6. **Order Tracking:** Every order sent must be tracked via `active_buy_id`/`active_sell_id`. Never use `oc_multi {"all": 1}` on hot path.

## File Structure

```
src/
├── main.rs          # HFT engine (3 tasks + watchdog)
├── types.rs         # EngineState, RiskState (mmap layout)
├── dashboard.rs     # Web dashboard server (:3000)
├── monitor.rs       # TUI dashboard (ANSI, mmap reader)
└── sovereign_ai.rs  # AI risk module
```

## Build & Deploy

```bash
cargo build --release                        # Optimized build
systemctl --user restart beroun-sniper       # Deploy HFT engine
systemctl --user restart beroun-dashboard    # Deploy dashboard
```

## Commit Convention

```
feat: v6.2 Intelligence — L2 OBI + Dynamic Sizing
fix: correct padding in EngineState mmap layout
refactor: rename beroun-ai → beroun-dashboard
ops: add systemd service for dashboard
docs: update ARCHITECTURE.md for v6.2
```

Use `fixes #N` in commit body to auto-close GitHub Issues.
