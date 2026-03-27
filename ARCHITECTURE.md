# 🏗️ SNIPER ARMADA v11.2 — Architecture Document

## System Overview

```
                  ┌─────────────────────────────────────────┐
                  │            TELEGRAM C2                    │
                  │  /status /delta /moonshot /mpairs /grid   │
                  └────────────┬─────────────────────────────┘
                               │ telebot API
┌──────────────────────────────┴──────────────────────────────────────────┐
│                     SNIPER WORKSPACE (Cargo)                            │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  shared/ (sniper_types crate)                                       │ │
│  │  EngineState │ MoonshotState │ GridState │ PRICE_SCALE              │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                               │                                          │
│       ┌───────────────────────┼───────────────────────┐                  │
│       ▼                       ▼                       ▼                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │  hydra/ (Bot #1) │  │ moonshot/ (#2)  │  │  grid/ (Bot #3) │         │
│  │  BTC-USD Delta   │  │ Multi-Symbol    │  │  Dynamic Grid   │         │
│  │  CPU Core 0      │  │ CPU Core 1      │  │  CPU Core 2     │         │
│  │                   │  │                 │  │                 │         │
│  │ L0: Rust Engine  │  │ L0: Rust Engine │  │ L0: Rust Engine │         │
│  │ L1: Python Shield│  │ L1: Python      │  │ L1: Python      │         │
│  │ L2: Gemini Oracle│  │ L2: AI Pairs    │  │ L2: ATR Tuner   │         │
│  │ Dashboard :3000  │  │ Dashboard :3001 │  │ Dashboard :3002 │         │
│  │ Brain (SQLite)   │  │ Brain (SQLite)  │  │ Brain (SQLite)  │         │
│  └──────┬───────────┘  └──────┬──────────┘  └──────┬──────────┘         │
│         │                      │                     │                    │
│         ▼                      ▼                     ▼                    │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                /dev/shm/beroun/                                      │ │
│  │  engine_state.bin  │ moonshot_engine.bin │ grid_engine.bin           │ │
│  │  risk_state.bin    │ moonshot_risk.bin   │ grid_risk.bin             │ │
│  │  Lock-free atomics · Zero-copy · <1μs access                        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                               │                                          │
│                               ▼                                          │
│                  ┌────────────────────────┐                              │
│                  │  architect/ (PLANNED)   │                              │
│                  │  Capital allocator      │                              │
│                  │  Cross-bot correlation  │                              │
│                  └────────────────────────┘                              │
└──────────────────────────────────────────────────────────────────────────┘
```

## Bot Template (Standard Structure)

Every bot follows this identical layout:

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
```

## Moonshot Data Flow: Spike Capture
```
Bitfinex WS → Ticker → Price Update → Ghost BUY Check → Order Replace
                                              ↑
                                      AI Portfolio (L2)
```

## Grid Data Flow: Multi-Level Management
```
Bitfinex WS → Ticker → Mid Price → Grid Recalculate → Cancel All → Place N Levels
                                          ↑                              ↑
                                    ATR/2 Dynamic (L2)          Arithmetic / Geometric
                                                               ↓
                                                       BUY fills → place SELL
                                                       SELL fills → place BUY
```

## Grid Strategy Details

| Parameter | Default | AI-Tunable | Description |
|-----------|---------|------------|-------------|
| grid_spacing | $1,200 | ✅ | Distance between levels |
| num_levels | 5+5 | ✅ | Number of BUY + SELL levels |
| grid_mode | arithmetic | ✅ | Fixed $ vs fixed % spacing |
| geometric_step | 1.5% | ✅ | Percentage step for geometric mode |
| dynamic_spacing | ATR/2 | ✅ | AI calculates optimal spacing |
| order_qty | 0.0005 BTC | ✅ | Per-level order size |
| max_consecutive_losses | 3 | ❌ | Halt after N losses |
| btc_vol_kill | 4%/h | ✅ | Volatility auto-pause |

## Safety Architecture

```
┌─── NON-OVERRIDABLE (Hardcoded) ──────────────────────────────┐
│  • Daily Loss Limit (DLL) circuit breaker                     │
│  • Max position size (capital guard)                          │
│  • Anti-Cross Guard (bid < best_ask, ask > best_bid)         │
│  • Fee Sentinel: spread < 2× fees → skip (Hydra)             │
│  • AI Heartbeat > 30s → zero bias (Hydra)                     │
│  • AI Heartbeat > 120min → safe mode (Moonshot)              │
│  • BTC Volatility Kill > 4%/h (Moonshot, Grid)              │
│  • Active Trade Lock (Moonshot)                               │
│  • Consecutive Loss Halt: 3 losses → 24h pause (Grid)       │
│  • Panic shutdown on WebSocket failure                        │
└──────────────────────────────────────────────────────────────┘
```

## Hardware Profile (Beroun Server)

| Resource | Spec | Allocation |
|----------|------|------------|
| CPU | i5-6400 (4 cores, no HT) | Core 0: Hydra, Core 1: Moonshot, Core 2: Grid, Core 3: OS + AI |
| RAM | 16 GB | ~4 GB used (3 bots), 12 GB available |
| GPU | GTX 1060 6GB | NVIDIA MPS for shared AI inference |
| SHM | 7.6 GB tmpfs | 6 mmap files for IPC |

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v11.2 | Full Armada | 3-bot workspace (Hydra + Moonshot + Grid) |
| v11.1 | Delta Lead + Moonshot | Cross-venue arb + flash crash |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v10.0 | Apex Predator | Dynamic grid + analytics |
