# 🏗️ SNIPER ARMADA v11.1 — Architecture Document

## System Overview

```
                  ┌─────────────────────────────────────┐
                  │         TELEGRAM C2                   │
                  │  /status /delta /moonshot /mpairs     │
                  └────────────┬────────────────────────┘
                               │ telebot API
┌──────────────────────────────┴──────────────────────────────────────┐
│                    SNIPER WORKSPACE (Cargo)                          │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  shared/ (sniper_types crate)                                   │ │
│  │  EngineState │ RiskState │ MoonshotState │ PRICE_SCALE          │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                               │                                      │
│       ┌───────────────────────┴───────────────────┐                  │
│       ▼                                           ▼                  │
│  ┌────────────────────────┐    ┌────────────────────────┐           │
│  │  hydra/ (Bot #1)       │    │  moonshot/ (Bot #2)    │           │
│  │  BTC-USD Delta Lead    │    │  Multi-Symbol Spike    │           │
│  │  CPU Core 0            │    │  CPU Core 1            │           │
│  │                        │    │                        │           │
│  │  L0: Rust Engine       │    │  L0: Rust Engine       │           │
│  │  L1: Python Shield     │    │  L1: Python Shield     │           │
│  │  L2: Gemini Oracle     │    │  L2: Gemini Oracle     │           │
│  │  Dashboard :3000       │    │  Dashboard :3001       │           │
│  │  Brain (SQLite)        │    │  Brain (SQLite)        │           │
│  │  Config CLI            │    │  Config CLI            │           │
│  └──────────┬─────────────┘    └──────────┬─────────────┘           │
│             │                              │                         │
│             ▼                              ▼                         │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │            /dev/shm/beroun/                                    │   │
│  │  engine_state.bin (Hydra)   │ moonshot_engine.bin (Moonshot)  │   │
│  │  risk_state.bin             │ moonshot_risk.bin                │   │
│  │  Lock-free atomics · Zero-copy · <1μs access                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                               │                                      │
│       ┌───────────────────────┴───────────────────┐                  │
│       ▼                                           ▼                  │
│  ┌────────────────────────┐    ┌────────────────────────┐           │
│  │  trigon/ (Bot #3)      │    │  architect/            │  PLANNED  │
│  │  Triangular Arb        │    │  sniper_architect.py   │           │
│  │  CPU Core 1 (shared)   │    │  Capital allocation    │           │
│  └────────────────────────┘    └────────────────────────┘           │
└──────────────────────────────────────────────────────────────────────┘
```

## Bot Template (Standard Structure)

Every bot in the Armada follows this identical layout:

```
bot/
├── Cargo.toml               ← depends on sniper-shared
├── src/
│   ├── main.rs              ← L0 Engine (Rust, hot loop)
│   ├── dashboard.rs         ← SSE Dashboard server
│   ├── brain.rs             ← SQLite analytics/history
│   └── config_cli.rs        ← Live config via mmap
├── scripts/
│   ├── l1_shield.py         ← L1 Tactical AI (Python)
│   ├── sniper_orchestrator.py ← L2 Strategic Oracle
│   ├── tg_listener.py       ← Telegram C2
│   └── macro_monitor.py     ← Cross-venue data feed
├── dashboard.html           ← Dashboard frontend
└── bot-start.sh             ← Launch (taskset + env)
```

## Hydra Data Flow: Order Lifecycle

```
Binance WS → Delta Lead → Fair Value → Grid Calc → Fee Guard → Anti-Cross → Order Send
                ↑             ↑            ↑           ↑
           BNB mid-price   Macro Bias   AI L2 Oracle  Fee Sentinel
```

## Moonshot Data Flow: Spike Capture

```
Bitfinex WS → Ticker → Price Update → Ghost BUY Check → Order Replace
                                              ↑              ↑
                                         AI Portfolio    Replace Delay
                                      (sniper_orchestrator)  (l1_shield)
```

## Safety Architecture

```
┌─── NON-OVERRIDABLE (Hardcoded) ──────────────────────────────┐
│  • Daily Loss Limit (DLL) circuit breaker                     │
│  • Max position size (capital guard)                          │
│  • Anti-Cross Guard (bid < best_ask, ask > best_bid)         │
│  • Fee Sentinel: spread < 2× fees → skip                     │
│  • AI Heartbeat > 30s stale → zero bias (Hydra)              │
│  • AI Heartbeat > 120min → safe mode (Moonshot)              │
│  • BTC Volatility Kill > 4%/h → pause (Moonshot)            │
│  • Active Trade Lock: AI cannot rotate open positions         │
│  • Panic shutdown on WebSocket failure                        │
└──────────────────────────────────────────────────────────────┘

┌─── AI-TUNABLE (via mmap registry) ───────────────────────────┐
│  Hydra: grid step, fire interval, ghost trigger zone          │
│  Moonshot: drop%, TP%, pair selection, replace delay          │
└──────────────────────────────────────────────────────────────┘
```

## Hardware Profile (Beroun Server)

| Resource | Spec | Allocation |
|----------|------|------------|
| CPU | i5-6400 (4 cores, no HT) | Core 0: Hydra, Core 1: Moonshot, Core 2-3: OS + AI |
| RAM | 16 GB | ~3 GB used, 12 GB available |
| GPU | GTX 1060 6GB | NVIDIA MPS for shared AI inference |
| SHM | 7.6 GB tmpfs | mmap IPC between all processes |

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v11.1 | Delta Lead + Moonshot | Cross-venue arb + Multi-symbol flash crash bot |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker + Reactive defense |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v10.6 | Ghost Shadow | Hidden liquidity (IOC injection) |
| v10.0 | Apex Predator | Dynamic grid + analytics |
| v8.0 | Sovereign Intelligence | Three-layer architecture |
