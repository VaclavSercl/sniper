# 🏗️ SNIPER ARMADA v15.1 — Architecture Document

## System Overview: "Separated Hemispheres"

```
                   ┌─────────────────────────────────────────────┐
                   │          📱 TELEGRAM COMMANDER v15.0        │
                   │  /hydra /moonshot /grid /gpu /pnl /panic    │
                   │  Natural Language via Gemini AI              │
                   └──────────────┬──────────────────────────────┘
                                  │ telebot polling
┌─────────────────────────────────┴──────────────────────────────────────┐
│                    CONTROL PLANE (Python Commander)                      │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │ 📱 Telegram   │  │ 🧠 L2 Oracle  │  │ 🏛️ Dashboard  │                  │
│  │ Commander     │  │ Gemini 5min  │  │ SSE :3004    │                  │
│  │ rpt hourly    │  │ silent tune  │  │ 141 LOC      │                  │
│  └──────────────┘  └──────────────┘  └──────────────┘                  │
│                          │                                               │
│                    UDS Bridge                                            │
│           /tmp/cortex.sock (JSON-line, bidirectional)                    │
│           /tmp/commander_events.sock (Sentinel push)                     │
│                          │                                               │
├──────────────────────────┴───────────────────────────────────────────────┤
│                     DATA PLANE (Rust Cortex)                              │
│           100% network-isolated from Telegram/Internet                    │
│                                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ L1 Loop  │  │ GPU      │  │ Macro    │  │ Sentinel │               │
│  │ 50ms OBI │  │ Phi-3.5  │  │ F&G/BNB  │  │ Alerts   │               │
│  │ 451 LOC  │  │ 480 LOC  │  │ 262 LOC  │  │ 412 LOC  │               │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘               │
│       └──────────────┴─────────────┴──────────────┘                     │
│                    UDS Server (321 LOC)                                   │
│                    Memory Reader (336 LOC)                                │
│                          │                                               │
├──────────────────────────┴───────────────────────────────────────────────┤
│                  SNIPER ARMADA WORKSPACE (Cargo 2024)                     │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  shared/ (sniper_types crate)                                       │ │
│  │  EngineState │ MoonshotState │ GridState │ TrigonState │ PnlState  │ │
│  │  math.rs (PRICE_SCALE 1e8)   │  logging.rs (tracing)              │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│       ┌───────────┬───────────┬───────────┬───────────┐                 │
│       ▼           ▼           ▼           ▼           ▼                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │  hydra   │ │ moonshot │ │  grid    │ │ trigon   │ │  mdf     │    │
│  │  Bot #1  │ │  Bot #2  │ │  Bot #3  │ │  Bot #4  │ │  Feed    │    │
│  │  Core 0  │ │  Core 1  │ │  Core 2  │ │  Core 3  │ │          │    │
│  │  :3000   │ │  :3001   │ │  :3002   │ │  :3003   │ │          │    │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘    │
│       └────────────┴────────────┴─────────────┴─────────────┘          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │           /dev/shm/beroun/ (Lock-free mmap IPC, <1µs)              │ │
│  │  engine_state │ moonshot_engine │ grid_engine │ trigon_engine       │ │
│  │  risk_state   │ pnl_state.bin   │ mdf.bin                         │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

## Component Matrix

| Component | Language | LOC | Role | Port | CPU |
|-----------|----------|-----|------|------|-----|
| **hydra-core** | Rust | 1,434 | BTC-USD Delta Lead HFT | :3000 | Core 0 |
| **hydra-brain** | Rust | 1,099 | SQLite analytics | — | Core 0 |
| **hydra-dashboard** | Rust | 816 | SSE Dashboard | :3000 | Core 3 |
| **moonshot-core** | Rust | 344 | Multi-Symbol Flash Crash | :3001 | Core 1 |
| **grid-core** | Rust | 334 | Dynamic Multi-Level Grid | :3002 | Core 2 |
| **trigon-core** | Rust | 363 | Triangular Arbitrage | :3003 | Core 3 |
| **sovereign-cortex** | Rust | 2,448 | L1 + GPU + Macro + Sentinel + UDS | — | Core 3 |
| **shared (sniper_types)** | Rust | 929 | Type definitions + math + logging | — | — |
| **tg_commander** | Python | ~900 | Telegram C2 (Slash + NL + Dashboard) | — | Core 3 |
| **l2_oracle** | Python | ~1100 | Strategic Oracle (Gemini, reports hourly) | — | Core 3 |
| **cortex_client** | Python | 184 | UDS client library | — | — |
| **dashboard_server** | Python | 141 | Master Dashboard SSE server | :3004 | Core 3 |
| **pnl_daemon** | Python | 466 | FIFO PnL Engine + mmap writer | — | Core 3 |

**Total:** ~7,767 Rust LOC + ~2,122 Python LOC = **~9,889 LOC**

## GPU Telemetry Pipeline

```
L1 Loop (50ms) → mpsc channel → GPU Thread
                                     │
                          ┌──────────┴──────────┐
                          │ Phi-3.5 Inference    │
                          │ localhost:1234       │
                          │ Actions: SKEW_BID,   │
                          │ SKEW_ASK, PAUSE, HOLD│
                          └──────────┬──────────┘
                                     │
                          Ring Buffer (10,000 slots)
                          ~50ns per record, zero I/O
                                     │
                          Evaluator (every 60s)
                          Pre/post snapshot comparison
                                     │
                          GpuStats (LazyLock<Mutex>)
                          Win rates, toxic rates, PnL
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
              GET_GPU_STATS    /gpu Telegram    L2 Oracle Feed
              (UDS command)    (on-demand)      (every 5 min, silent)
                                                     │
                                              SET_L1_TUNING
                                              skew_max, obi_thr
                                              inference_interval
                                                     │
                                              TG Report (hourly only)
```

## IPC Map

| Path | Type | Protocol | Written By | Read By |
|------|------|----------|-----------|---------|
| `/dev/shm/beroun/engine_state.bin` | mmap | Atomic loads | hydra-core | cortex, dashboard |
| `/dev/shm/beroun/risk_state.bin` | mmap | Atomic stores | cortex (L1) | hydra-core |
| `/dev/shm/beroun/moonshot_engine.bin` | mmap | Atomic | moonshot-core | cortex |
| `/dev/shm/beroun/grid_engine.bin` | mmap | Atomic | grid-core | cortex |
| `/dev/shm/beroun/trigon_engine.bin` | mmap | Atomic | trigon-core | cortex |
| `/dev/shm/beroun/pnl_state.bin` | mmap | Atomic | pnl_daemon | dashboards |
| `/dev/shm/beroun/mdf.bin` | mmap | Atomic | mdf | pnl_daemon, trigon |
| `/tmp/cortex.sock` | UDS | JSON-line | cortex | commander, l2_oracle |
| `/tmp/commander_events.sock` | UDS | JSON push | cortex (sentinel) | commander |

## UDS Command Protocol

| Command | Direction | Payload |
|---------|-----------|---------|
| `PING` | → Cortex | — |
| `GET_SNAPSHOT` | → Cortex | returns all bot states + metadata |
| `GET_GPU_STATS` | → Cortex | returns ring buffer telemetry + L1 tuning |
| `SET_GRID` | → Cortex | `{value: float}` — grid step |
| `SET_MAXPOS` | → Cortex | `{value: float}` — max position |
| `SET_REGIME` | → Cortex | `{regime: "BULLISH_TREND"}` |
| `SET_L1_TUNING` | → Cortex | `{skew_max_usd, obi_threshold, inference_interval_ms}` |
| Push Alert | Cortex → | `{type: "mmap_stale", bot: "hydra", age_s: 45}` |

## Safety Architecture

```
┌─── NON-OVERRIDABLE (Hardcoded in Rust) ──────────────────────┐
│  • Daily Loss Limit (DLL) circuit breaker                     │
│  • Max position size (capital guard)                          │
│  • Anti-Cross Guard (bid < best_ask, ask > best_bid)         │
│  • Fee Sentinel: spread < 2× fees → skip cycle              │
│  • L1 Tuning clamps: skew [0.5-5.0], OBI [0.0-0.8]         │
│  • GPU inference interval clamp: [500-10000ms]               │
│  • Instance Lock: fs2 exclusive file lock (no dual-trade)    │
│  • Telegram PANIC: /panic → stops ALL bots                   │
│  • L1 tuning resets to defaults on restart (no persistence)  │
└──────────────────────────────────────────────────────────────┘
```

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v15.0 | Sovereign HFT | Consolidated AI reporting (hourly/daily/weekly/monthly), lmstudio.service dep, unified watchdog |
| v14.0 | Separated Hemispheres | Cortex/Commander split, GPU telemetry, L2 feedback loop, Master Dashboard SSE |
| v13.0 | Sovereign Intelligence | Zero-Debt Audit, unified standard |
| v12.0 | Armada PnL | FIFO PnL Engine + Telegram Commander v2.0 |
| v11.2 | Full Armada | 4-bot workspace + MDF |
| v10.0 | Apex Predator | Dynamic grid + analytics |
