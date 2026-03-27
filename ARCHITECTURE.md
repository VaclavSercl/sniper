# 🏗️ BEROUN SNIPER v10.7 — Architecture

## Execution Model

```
                    ┌──────────────────────────────────────┐
                    │        Bitfinex WebSocket v2         │
                    │    (orderbook + trades + auth)       │
                    └──────────┬───────────────────────────┘
                               │ fastwebsockets (zero-copy)
                    ┌──────────▼───────────────────────────┐
                    │     L0 RUST ENGINE (main.rs)         │
                    │                                      │
                    │  ┌───────────┐  ┌─────────────────┐  │
                    │  │ Orderbook │  │ Ghost Proximity  │  │
                    │  │ Parser    │→ │ Monitor (sub-ms) │  │
                    │  └───────────┘  └────────┬────────┘  │
                    │                          │           │
                    │  ┌───────────────────────▼────────┐  │
                    │  │     Hydra Fire Engine          │  │
                    │  │ Public grid + Shadow ghost      │  │
                    │  │ IOC Flash Injection             │  │
                    │  └───────────────────────┬────────┘  │
                    │                          │           │
                    │  ┌───────────────────────▼────────┐  │
                    │  │     Order TX Channel           │  │
                    │  │  (mpsc → WebSocket write)      │  │
                    │  └────────────────────────────────┘  │
                    └──────────┬───────────────────────────┘
                               │ mmap IPC (lock-free atomics)
              ┌────────────────┼────────────────────────┐
              │                │                        │
   ┌──────────▼─────┐  ┌──────▼────────┐  ┌────────────▼────────┐
   │ L1 SHIELD      │  │ DASHBOARD     │  │ L2 ORACLE           │
   │ (Python, 50ms) │  │ (Rust, SSE)   │  │ (Python, 5min)      │
   │                │  │               │  │                     │
   │ Sweep detect   │  │ HTMX+SSE     │  │ Gemini 2.5 Pro      │
   │ Ghost control  │  │ Real-time UI  │  │ Regime detection    │
   │ Skew bias      │  │ Port 3000     │  │ Grid optimization   │
   │ Anti-paralysis │  │               │  │ Neural Cross        │
   └────────────────┘  └───────────────┘  │ SQLite Brain        │
                                          │ Sovereign Registry  │
                                          └─────────────────────┘
```

## IPC: Shared Memory Layout

```
/dev/shm/beroun/engine_state.bin (EngineState, ~1824 bytes)
├─ Price fields (bid, ask, micro, spread)         offset 0-96
├─ Position & PnL (realized, unrealized)          offset 96-256
├─ OrderBook (25 bids + 25 asks × 24 bytes)       offset 256-1456
├─ Analytics (fills, toxic, OBI)                  offset 1456-1568
├─ AI Intelligence (confidence, regime, shadow)   offset 1568-1664
├─ Anti-Paralysis (l1_uptime_pct)                 offset 1664
├─ Ghost Orders (transparency, mask, prices)      offset 1672-1784
└─ Sovereign Registry (freeze, fire, trigger, intent) offset 1784-1824

/dev/shm/beroun/risk_state.bin (RiskState, ~256 bytes)
├─ paused flag
├─ authorized_capital, daily_loss_limit
├─ grid_step, grid_levels, max_position
└─ cap_floor (escalation)
```

## Data Flow

1. **Book Tick** → L0 updates orderbook atomics → calculates micro-price
2. **Ghost Check** (every tick) → if `|micro - ghost_level| < trigger_zone` → fire IOC
3. **Hydra Fire** (every `ai_fire_interval_ms`) → build public + ghost grid → send orders
4. **L1 Shield** (every 50ms) → read book from mmap → detect sweep → write freeze/ghost/skew
5. **L2 Oracle** (every 5min) → Gemini analysis → write grid/position/registry to mmap
6. **Dashboard** (every 500ms) → read all mmap fields → SSE push to browser

## Safety Architecture

```
┌─ NON-OVERRIDABLE (L0 hardcoded) ─────────────────────┐
│  DLL circuit breaker (daily loss limit auto-pause)    │
│  Capital guard (max authorized capital)               │
│  Anti-cross guard (bid < ask enforcement)             │
│  Fire interval clamp (500ms-10000ms)                 │
│  Freeze clamp (500ms-30000ms)                        │
└───────────────────────────────────────────────────────┘
┌─ AI-TUNABLE (Sovereign Registry) ─────────────────────┐
│  Grid step, position limit                            │
│  Freeze duration, fire interval                       │
│  Ghost trigger zone, transparency                     │
│  Intent mode (aggressive/defensive/scout/sovereign)   │
└───────────────────────────────────────────────────────┘
```
