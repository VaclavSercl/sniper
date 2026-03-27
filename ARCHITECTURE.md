# 🏗️ SNIPER ARMADA v11.1 — Architecture Document

## System Overview

```
                  ┌─────────────────────────────────────┐
                  │         TELEGRAM C2                   │
                  │  /status /delta /fees /sovereign      │
                  └────────────┬────────────────────────┘
                               │ telebot API
┌──────────────────────────────┴──────────────────────────────────────┐
│                    SNIPER WORKSPACE (Cargo)                          │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  shared/ (sniper_types crate)                                   │ │
│  │  EngineState │ RiskState │ PRICE_SCALE │ OrderBookLevel         │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                               │                                      │
│               ┌───────────────┴───────────────┐                      │
│               ▼                               ▼                      │
│  ┌────────────────────────┐    ┌────────────────────────┐           │
│  │  hydra/ (Bot #1)       │    │  trigon/ (Bot #2)      │  PLANNED  │
│  │  BTC-USD Delta Lead    │    │  Triangular Arb        │           │
│  │  CPU Core 0            │    │  CPU Core 1            │           │
│  │                        │    │                        │           │
│  │  L0: Rust Engine       │    │  L0: Rust Engine       │           │
│  │  L1: Python Shield     │    │  L1: Python Shield     │           │
│  │  L2: Gemini Oracle     │    │  L2: Oracle            │           │
│  │  Dashboard :3000       │    │  Dashboard :3001       │           │
│  └──────────┬─────────────┘    └──────────┬─────────────┘           │
│             │                              │                         │
│             ▼                              ▼                         │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │            /dev/shm/beroun/                                    │   │
│  │  engine_state.bin (Hydra) │ trigon_state.bin (Trigon)         │   │
│  │  risk_state.bin           │ market_data.bin (shared MDF)      │   │
│  │  Lock-free atomics · Zero-copy · <1μs access                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                               │                                      │
│               ┌───────────────┴───────────────┐                      │
│               ▼                               ▼                      │
│  ┌────────────────────────┐    ┌────────────────────────┐           │
│  │  architect/            │    │  Master Dashboard      │  PLANNED  │
│  │  sniper_architect.py   │    │  Aggregated Multi-Bot  │           │
│  │  Capital allocation    │    │  Total Equity + DD     │           │
│  │  Health watchdog       │    │                        │           │
│  └────────────────────────┘    └────────────────────────┘           │
└──────────────────────────────────────────────────────────────────────┘
```

## Hydra Data Flow: Order Lifecycle

```
Binance WS → Delta Lead → Fair Value → Grid Calc → Fee Guard → Anti-Cross → Order Send
                ↑             ↑            ↑           ↑
           BNB mid-price   Macro Bias   AI L2 Oracle  Fee Sentinel
```

## Delta Lead Pipeline (v11.1)

Every fire cycle (~3s), the Hydra engine:

1. **Reads** Binance mid-price from mmap (written by macro_monitor.py)
2. **Computes** Delta: Binance_mid - Bitfinex_mid in basis points
3. **Evaluates** Signal: accumulated delta > threshold → directional bias
4. **Repositions** Grid center (50%) toward expected convergence when delta > 3 bps
5. **Dashboard** shows BUY/SELL grid lines + BNB mid as SVG overlay

## Fee Sentinel Pipeline (v11.3)

Hourly autonomous cycle:

1. **Queries** Bitfinex `/auth/r/summary` for current maker/taker fees
2. **Writes** fee values to mmap atomics (maker_fee_bps, taker_fee_bps)
3. **Guards** L0 execution: if spread < 2× round-trip fees → skip cycle + increment fee_kills
4. **Alerts** Telegram if fee structure changes from last known state

## Safety Architecture

```
┌─── NON-OVERRIDABLE (Hardcoded) ──────────────────────────────┐
│  • Daily Loss Limit (DLL) circuit breaker                     │
│  • Max position size (capital guard)                          │
│  • Anti-Cross Guard (bid < best_ask, ask > best_bid)         │
│  • Fee Sentinel: spread < 2× fees → skip                     │
│  • AI Heartbeat > 30s stale → zero bias                      │
│  • Panic shutdown on WebSocket failure                        │
└──────────────────────────────────────────────────────────────┘

┌─── AI-TUNABLE (via mmap registry) ───────────────────────────┐
│  • Grid step, fire interval, ghost trigger zone               │
│  • Sweep freeze duration, intent mode                         │
│  • Min order lifetime (anti-flicker)                          │
│  • Ghost transparency (% of grid visible)                     │
└──────────────────────────────────────────────────────────────┘
```

## Hardware Profile (Beroun Server)

| Resource | Spec | Allocation |
|----------|------|------------|
| CPU | i5-6400 (4 cores, no HT) | Core 0: Hydra, Core 1: Trigon, Core 2-3: OS + AI |
| RAM | 16 GB | ~3 GB used, 12 GB available |
| GPU | GTX 1060 6GB | NVIDIA MPS for shared AI inference |
| SHM | 7.6 GB tmpfs | mmap IPC between all processes |

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v11.1 | Delta Lead | Cross-venue arbitrage + Workspace migration |
| v11.3 | Fee Sentinel | Autonomous fee monitoring + profitability guard |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker + Reactive defense |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v10.7 | Sovereign AI | Dynamic registry via mmap |
| v10.6 | Ghost Shadow | Hidden liquidity (IOC injection) |
| v10.0 | Apex Predator | Dynamic grid + analytics |
| v8.0 | Sovereign Intelligence | Three-layer architecture |
