# Contributing — Beroun Sniper v10.0

## Tech Stack
- **Language:** Rust (Edition 2024) + Python 3 (L1 Shield, Telegram, Analytics)
- **Architecture:** Zero-copy, Allocation-free hot path, Mmap IPC, Tri-Layer AI
- **OS:** Linux (Ubuntu 22.04+)

## Coding Standards (HFT Specific)

1. **No Heap Allocations:** Never use `Box`, `Vec`, or `String` in the HFT loop (`main.rs` Task 3). Use stack buffers and `format!` only for order strings sent via channel.
2. **Atomic Consistency:** Use `AtomicU64/I64` with `SeqCst` for mmap writes, `Acquire/Release` for reads.
3. **Fixed-Point Math:** All prices use `PRICE_SCALE = 1e8`. No `f64` in risk calculations on hot path.
4. **Cache Alignment:** All IPC structures use `#[repr(C, align(64))]`. Padding must maintain 64-byte cache line boundaries.
5. **No Unwraps:** Always use safe matching or `anyhow` context. `unwrap()` is forbidden.
6. **Order Tracking:** Every order sent must be tracked via `active_buy_id`/`active_sell_id`. Never use `oc_multi {"all": 1}` on hot path.
7. **mmap Offsets:** When modifying `EngineState` or `RiskState` in `types.rs`, run `dump_offsets` to verify Python L1 Shield compatibility.

## File Structure

```
src/
├── main.rs          # L0 HFT engine (Dual WS, Hydra Grid, watchdog, shutdown)
├── types.rs         # EngineState, RiskState, OrderBookLevel (mmap layout)
├── config_cli.rs    # beroun-config CLI (mmap parameter modifier)
├── dashboard.rs     # Web dashboard HTTP server (:3000)
├── dump_offsets.rs  # Debug: prints mmap struct offsets for L1 Shield
└── risk_control.rs  # Risk control binary

scripts/
├── l1_shield.py     # L1 Tactical Shield (OBI→skew via mmap)
├── tg_listener.py   # Telegram C2 interface
├── analytics.py     # Trade Analytics Engine (Sharpe, win-rate)
└── oracle_brain.sh  # L2 Gemini Oracle (26h macro cycle)
```

## Build & Deploy

```bash
# Build Rust binaries
cargo build --release

# Deploy via systemd
sudo systemctl restart beroun-sniper

# Or manual start
./beroun-start.sh
```

## Testing mmap Changes

When modifying `types.rs` struct layouts:

```bash
# 1. Verify offsets match between Rust and Python
cargo run --release --bin dump-offsets

# 2. Update l1_shield.py offsets if needed
# 3. Test L1 Shield reads correctly
python3 scripts/l1_shield.py
# Expected: OBI, Skew, Mid, Toxic values should be sane
```

## Commit Convention

```
feat: v10.0 Apex Predator — Trade Analytics + Fee Optimizer
fix(L1): L1 Shield opravený — OBI/Skew/Sweep protection
refactor: extract Hydra Grid into separate module
ops: update systemd service for L1 Shield
docs: update ARCHITECTURE.md for v10.0
```

Use `fixes #N` in commit body to auto-close GitHub Issues.

## Python Scripts

Python scripts in `scripts/` follow these standards:
- **mmap IPC only** — no network calls to L0 engine
- **struct.unpack** — must match Rust `#[repr(C)]` layout exactly
- **Graceful degradation** — if mmap read fails, zero bias (safe default)
- **Logging** — use structured logging for all state changes
