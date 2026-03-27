# 🏗️ BEROUN SNIPER v11.0 — Architecture Document

## System Overview

```
                    ┌──────────────────────────────────────────────┐
                    │              TELEGRAM                         │
                    │   /status /macro /ai /sovereign               │
                    └────────────────┬─────────────────────────────┘
                                     │ telebot API
                    ┌────────────────┴─────────────────────────────┐
                    │         L2: GEMINI ORACLE (5 min cycle)       │
                    │  sniper_orchestrator.py → beroun-config CLI   │
                    └────────────────┬─────────────────────────────┘
                                     │ mmap write (risk params)
┌───────────────────┐ ┌──────────────┴──────────────┐ ┌────────────┐
│  MACRO MONITOR    │ │  L1: PYTHON SHIELD (1s loop)│ │   BRAIN    │
│ macro_monitor.py  │ │  l1_shield.py               │ │  brain.rs  │
│                   │ │                              │ │  SQLite    │
│ • Binance WS      │ │  • OBI skew calculation     │ │  sniper.db │
│ • F&G Index       │ │  • Sweep detection          │ │            │
│ • RSS Sentiment   │ │  • Ghost mode control       │ │  • Lessons │
│                   │ │  • Uptime tracking          │ │  • Cycles  │
└───────┬───────────┘ └──────────────┬──────────────┘ └────────────┘
        │ mmap                       │ mmap
        ▼                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    /dev/shm/beroun/                               │
│                                                                   │
│  engine_state.bin (EngineState)  │  risk_state.bin (RiskState)   │
│  ~1900 bytes, lock-free atomics  │  ~256 bytes, lock-free        │
│                                                                   │
│  Micro-Price  │ Orderbook │ Ghost Grid │ AI Registry │ Macro     │
└──────────────────────────┬───────────────────────────────────────┘
                           │ mmap read (Ordering::Relaxed)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                    L0: RUST ENGINE                                │
│                    beroun-core (main.rs)                          │
│                                                                   │
│  ┌─────────────┐ ┌──────────────┐ ┌────────────────────────────┐ │
│  │ DATA THREAD │ │ EXEC THREAD  │ │ HYDRA FIRE (inside exec)   │ │
│  │ WS: book    │ │ WS: trades   │ │                            │ │
│  │ OBI calc    │ │ position mgmt│ │ 1. Micro-Price             │ │
│  │ depth       │ │ wallet sync  │ │ 2. Fair Value (v11.0)      │ │
│  │ ghost check │ │ order track  │ │ 3. OBI Imbalance           │ │
│  └─────────────┘ └──────────────┘ │ 4. Inventory Skew          │ │
│                                    │ 5. Macro Bias (v10.9)      │ │
│  ┌─────────────┐                  │ 6. Anti-Cross Guard        │ │
│  │ DASHBOARD   │                  │ 7. Ghost Transparency      │ │
│  │ :3000 HTMX  │                  │ 8. Sentinel Reposition     │ │
│  └─────────────┘                  └────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

## Data Flow: Order Lifecycle

```
Price Update → Micro-Price → Fair Value → Grid Calc → Anti-Cross → Order Send
                                ↑                         ↑
                         Binance VWAP              Safety Clamp
                         Macro Bias
```

## Sentinel Pipeline (v11.0)

Every fire cycle (~3s), the engine:

1. **Reads** Binance mid-price from mmap (written by macro_monitor.py at ~100 trades/sec)
2. **Computes** Global Fair Value = 60% local + 30% Binance + 10% sentiment
3. **Checks** divergence: if |FV - local| > 0.05%, shifts ghost grid 50% toward FV
4. **Checks** Binance sweep: if detected < 3s ago, forces ghost mode
5. **Applies** Anti-Flicker: fire_interval = max(AI setting, min_order_lifetime)

## Safety Architecture

```
┌─── NON-OVERRIDABLE (Hardcoded) ──────────────────────────────┐
│  • Daily Loss Limit (DLL) circuit breaker                     │
│  • Max position size (capital guard)                          │
│  • Anti-Cross Guard (bid < best_ask, ask > best_bid)         │
│  • Spread inverted → skip cycle                              │
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

## Version History

| Version | Codename | Key Feature |
|---------|----------|-------------|
| v8.0 | Sovereign Intelligence | Three-layer architecture |
| v9.2 | Hybrid Intelligence | L1 Python Shield + OBI |
| v10.0 | Apex Predator | Dynamic grid + analytics |
| v10.4 | Neural Cross | Lesson validation engine |
| v10.5 | Anti-Paralysis | Adaptive sweep thresholds |
| v10.6 | Ghost Shadow | Hidden liquidity (IOC injection) |
| v10.7 | Sovereign AI | Dynamic registry via mmap |
| v10.9 | Omniscient Predator | Macro monitor + Binance sync |
| v11.0 | Sentinel Singularity | Fair Value + Anti-Flicker + Reactive defense |
