# 🐺 Beroun Sniper v10.0 — Architecture

Vysokofrekvenční market-making bot pro Bitfinex BTC/USD.
Rust 2024, zero-copy architektura, sub-millisecond tick-to-trade, třívrstvý AI imunitní systém.

## Třívrstvá Architektura (L0 / L1 / L2)

```
┌─────────────────────────────────────────────────────────────┐
│  L0 REFLEX — Rust (< 1ms tick-to-trade)                     │
│  CPU Core 1, pinned, current_thread Tokio runtime           │
│  ├─ Task 1: Market Data WS (simd_json, 25-level book)      │
│  ├─ Task 2: Execution WS (order send/recv, trade tracking)  │
│  └─ Task 3: HFT Loop (Hydra Grid, micro-price, skew)       │
├─────────────────────────────────────────────────────────────┤
│  IPC (mmap, lock-free atomics, cache-line aligned)          │
│  ├─ engine_state.bin  ← L0 writes, L1 reads                │
│  └─ risk_state.bin    ← L1 writes, L0 reads                │
├─────────────────────────────────────────────────────────────┤
│  L1 TACTICAL SHIELD — Python (1s cycle)                     │
│  l1_shield.py: OBI skewing, micro-skew, sweep detection     │
│  Reads OBI/mid from engine_state → writes bias to risk_state│
├─────────────────────────────────────────────────────────────┤
│  L2 STRATEGIC ORACLE — Gemini 3.1 Pro (26h cycle)           │
│  oracle_brain.sh: RSS + Fear&Greed + macro → beroun-config  │
└─────────────────────────────────────────────────────────────┘
```

## L0: Rust HFT Engine (main.rs)

### Dual WebSocket Architecture

**Problém:** Jeden WS pro data i ordery → **TCP Head-of-Line blocking**.
Při tržním volume přijímáš stovky book updatů, a tvůj order čeká ve frontě na TCP ACK.

**Řešení:**
| WS | Účel | Auth | Subscribe |
|----|-------|------|-----------|
| Market Data | Příjem order booku | ❌ Ne | ✅ book tBTCUSD |
| Execution | Ordery + notifikace | ✅ Ano | ❌ Ne |

Order string se formátuje na hot path a posílá přes `unbounded_channel` — **nanosekunda**, ne milisekunda čekání na TCP.

### Task Architecture

```mermaid
graph TB
    subgraph "CPU Core 1 (pinned, current_thread runtime)"
        subgraph "Task 3: HFT Loop (inline, highest priority)"
            WS_PUB[Market Data WS<br/>TCP_NODELAY<br/>15s watchdog] --> PARSE[simd_json<br/>zero-copy parse]
            PARSE --> BOOK[Order Book<br/>25 bids + 25 asks]
            BOOK --> BBA[Update BBA]
            BBA --> SNIPER["Sniper Logic<br/>Micro-Price + Inv Skew + Hydra Grid"]
        end
        subgraph "Task 1: Exec Writer (spawned)"
            WRITER[Order Writer<br/>unbounded_channel recv]
        end
        subgraph "Task 2: Exec Reader (spawned)"
            READER[Notification Reader<br/>15s watchdog] --> TE[te: trade_executed]
            READER --> WU[wu/ws: wallet_update]
            READER --> N[n: bitfinex_notification]
        end
    end

    subgraph "Shared Memory (mmap)"
        ES[(EngineState<br/>cache-line aligned)]
        RS[(RiskState<br/>cache-line aligned)]
    end

    subgraph "Control Flow"
        ERR_CH{{err_tx/err_rx<br/>error channel}}
        ORD_CH{{order_tx/order_rx<br/>unbounded MPSC}}
        SIGTERM[SIGTERM/SIGINT] --> SHUTDOWN[graceful_shutdown<br/>cancel_all → 500ms flush]
    end

    SNIPER -->|order string| ORD_CH
    ORD_CH --> WRITER
    WRITER -->|WS send| WS_PRIV[Execution WS<br/>TCP_NODELAY]
    WS_PRIV --> READER

    BOOK --> ES
    READER --> ES
    RS --> SNIPER

    READER -->|socket error| ERR_CH
    WRITER -->|write error| ERR_CH
    ERR_CH -->|watchdog trigger| WS_PUB
```

### Trading Intelligence

#### Micro-Price (Volume-Weighted Mid)
```
micro = (bid_price × ask_vol + ask_price × bid_vol) / total_vol
```
Na rozdíl od `(bid+ask)/2`, micro-price predikuje směr z volume imbalance.
Velký bid volume → cena se posune k asku (předpovídá růst) → bot se posune dřív než trh.

#### L2 Order Book Imbalance (OBI)
```
OBI = (Σ bid_vol[0..10] - Σ ask_vol[0..10]) / (Σ bid_vol[0..10] + Σ ask_vol[0..10])
```
Rozsah: -1.0 (čistý prodejní tlak) → +1.0 (čistý nákupní tlak).
Bot čte hloubku 10 hladin order booku a vidí "zdi" — velké objemy, které drží cenu.

#### Hydra Multi-Level Grid (v9.0+)
```
Level 1: base_grid × 1.0    (tight, high fill rate)
Level 2: base_grid × 1.618  (Fibonacci, medium)
Level 3: base_grid × 2.618  (wide, capture volatility)
Level 4: base_grid × 4.236  (ultra-wide, rare fills)
Level 5: base_grid × 6.854  (extreme, black swan capture)
```

#### Adaptive Grid (v9.5+)
```
fill_balance = buys_filled / (buys_filled + sells_filled)
grid_mult = lerp(0.7, 1.5, abs(fill_balance - 0.5) × 2)
dynamic_grid = base_grid × grid_mult × volatility_factor
```

#### Inventory Skew (Řízení zásob)
```
skew = -(position / max_position) × 2 × grid
```
| Pozice | Ratio | Skew | Efekt |
|--------|-------|------|-------|
| 0 BTC | 0.0 | $0 | Symetrický market making |
| +50% max | +0.5 | -1×grid | Brzdí nákupy, zlevňuje prodeje |
| +100% max | +1.0 | -2×grid | Maximální obranný posun |
| -50% max | -0.5 | +1×grid | Brzdí prodeje, zlevňuje nákupy |

#### Volatility Engine (Dynamický Grid)
Background task (500ms polling): sbírá mid-price do 60-slot ring bufferu (30s okno).
```
dynamic_grid = base($2) + 15% × price_range(30s)
clamped to [$2, $50]
```

#### Liquidity Hole Detection (v9.5+)
```
depth_score = Σ ask_vol[0..5]  (top 5 levels)
depth_ma = 60-sample moving average
if depth_score < depth_ma × 0.3 → grid ×3 (protective widening)
```

## L1: Tactical Shield (l1_shield.py)

Python sidecar proces čtoucí engine_state přes mmap a zapisující bias do risk_state.

### Funkce
| Funkce | Input | Output | Cyklus |
|--------|-------|--------|--------|
| **OBI Skewing** | OBI z engine_state (-1.0 to +1.0) | bias_offset do risk_state | 1s |
| **Micro-Skew** | Mid price + OBI směr | Posun bidů dolů při sell pressure | 1s |
| **Sweep Detection** | Volume spike detection | Toxic flag (grid widening) | 1s |

### Metriky (live)
```
OBI=-0.651  Skew=$-0.59  Mid=$68,470  Toxic=0  Cycle=1200
```
- OBI: -0.651 = silný sell pressure → posouvá bidy dolů
- Micro-skew: $-0.59 = chrání před padajícím nožem
- Toxic=0: žádné sweep detekce (clean market)

### mmap IPC Layout
```
L1 reads:  engine_state.bin → best_bid, best_ask, bids[25], asks[25], OBI
L1 writes: risk_state.bin   → bias_offset (AtomicI64, PRICE_SCALE=1e8)
```

## L2: Strategic Oracle (oracle_brain.sh)

### Pipeline
```
main.rs (hourly) → runtime/state.json
                        ↓
oracle_brain.sh  → beroun-config export-json + RSS + Fear/Greed
                        ↓
                   gemini-cli → {new_grid, max_position, risk_level, reasoning}
                        ↓
                   beroun-config set-grid + set-max-inv (with 3× safety)
                        ↓
                   risk_state.bin (mmap) → Sniper reads immediately
```

## Memory Layout (mmap IPC)

### EngineState — Cache-line izolovaný

```
Offset  Zone         Fields                          Access Pattern
──────  ──────────   ──────────────────────────────   ──────────────────
0x000   HEARTBEAT    latency_ns + 56B pad            Background task, 1/s
0x040   HOT          best_bid, best_ask              Main loop, 100s/s
0x050   HOT          bids[25] (25×24B = 600B)        Main loop (sort)
0x2C8   HOT          asks[25] (25×24B = 600B)        Main loop (sort)
0x540   PAD          64B cache line boundary          ─── isolation ───
0x580   COLD         net_position, realized_pnl       Infrequent writes
0x590   COLD         wallet_btc, wallet_usd           On "wu" message
0x5A0   COLD         checksum, last_buy/sell_price    On sniper_fire
```

### OrderBookLevel — Compact `repr(C)` (24 bytes)

```
Field    Type        Size   Offset
───────  ─────────   ────   ──────
price    AtomicU64   8B     0x00
amount   AtomicI64   8B     0x08
count    AtomicU64   8B     0x10
```

## Watchdog Systems

### In-Process Watchdog

| Mechanismus | Timeout | Reakce |
|---|---|---|
| Market Data read | 15s | `should_reconnect = true` |
| Exec Reader read | 15s | `err_tx.send("exec_socket_timeout")` |
| Exec Writer send fail | okamžitě | `err_tx.send("writer_socket_error")` |
| `err_rx.recv()` v HFT loop | biased select | `should_reconnect = true` |

### External Watchdog (watchdog.sh)
- Checks mmap heartbeat timestamp
- Restarts engine if heartbeat > 60s stale
- Runs as systemd timer or cron

### Reconnect Cleanup
```
1. writer_handle.abort()    — zabije zombie writer
2. reader_handle.abort()    — zabije zombie reader
3. Zero order book          — sniper čeká na snapshot
4. Zero last_buy/sell_price — sniper provede fresh fire
5. Sleep 3s                  — dá Bitfinexu čas
6. Outer loop: reconnect    — nové oba WS
```

### Graceful Shutdown (SIGTERM/SIGINT)
```
1. Odchytí signal (ctrl_c / sigterm.recv)
2. Pošle cancel_all přes order_tx → writer → Bitfinex
3. Sleep 500ms (TCP flush)
4. return Ok(()) — čistý exit
```

## Hot Path Optimizations

| Optimalizace | Detaily | Dopad |
|---|---|---|
| **Dual WebSocket** | Odd. data/ordery → no TCP HoL blocking | Eliminuje order queue delay |
| **unbounded_channel** | Nanosekundové předání orderu | Zero lock contention |
| **simd_json** | SIMD JSON parser, in-place mutation | ~10× vs serde_json |
| **into_data().to_vec()** | Consume Message, no double-buffer | Eliminuje 1 memcpy |
| **write_bfx** | Stack buffer [u8;32] pro checksum | 0 heap alloc/level |
| **format! orders** | Direct string vs json! Value tree | ~5× faster |
| **TCP_NODELAY** | Oba WS sockety bez Nagle | Instant packet send |
| **CPU pinning** | Core 1, current_thread runtime | Zero cache migration |
| **biased select!** | Shutdown/watchdog checked first | Guaranteed responsiveness |
| **mmap IPC** | Lock-free atomic reads/writes | Zero syscall overhead |

## Bezpečnostní opravy

| Problém | Řešení |
|---|---|
| UB: `&mut EngineState` aliasing | Raw `*mut EngineState` pointer |
| Float precision drift | `.round()` před `as u64/i64` |
| i64→u64 overflow | `i64` aritmetika s `.max(0)` |
| Orphan orders on shutdown | `cancel_all` → 500ms flush |
| Half-open connections | 15s timeout watchdog |
| Zombie tasks on reconnect | `JoinHandle::abort()` |

## Order Tracking

### Životní cyklus objednávky
```
os (snapshot)  → načte existující ordery po připojení (state recovery)
on (new)       → bot poslal nový order → uloží ID do active_buy/sell_id
ou (update)    → order se částečně fillnul → aktualizuje ID
oc (cancel)    → order zrušen → compare_exchange vymaže ID
te (executed)  → obchod proveden → aktualizuje net_position
```

### Chirurgický cancel (vs. cancel all)
```rust
// PŘED (v6.0): ruší VŠECHNY objednávky na účtu
["oc_multi", {"all": 1}]

// PO (v6.1): ruší POUZE naše trackované objednávky
["oc_multi", {"id": [234102741323, 234102741324]}]
```

## Konfigurace

### `.env` (povinné)

```env
BITFINEX_API_KEY=...
BITFINEX_API_SECRET=...
TELEGRAM_BOT_TOKEN=...     # optional
TELEGRAM_CHAT_ID=...       # optional
```

### RiskState defaults (v `types.rs`)

| Parametr | Default | Popis |
|---|---|---|
| grid_step | $2-50 (dynamic) | Volatility Engine dynamicky řídí |
| grid_size | 2 | Počet párů objednávek |
| order_usd | $50 | Velikost objednávky v USD |
| max_inv_delta | 0.005 BTC | Max inventory delta (pro skew) |
| bias_offset | 0 | Directional bias (L1 Shield writes) |

## Adresářová struktura

```
/home/wwwenda/hft-sniper/
├── src/
│   ├── main.rs              # L0 HFT engine (Dual WS, Hydra Grid, Adaptive Grid)
│   ├── types.rs             # EngineState, RiskState, OrderBookLevel (mmap layout)
│   ├── config_cli.rs        # beroun-config CLI (mmap parameter modifier)
│   ├── dashboard.rs         # Dashboard HTTP server (:3000)
│   ├── dump_offsets.rs      # Debug: mmap struct offset validator
│   └── risk_control.rs     # Risk control binary
├── scripts/
│   ├── l1_shield.py         # L1 Tactical Shield (OBI→skew, sweep detection)
│   ├── tg_listener.py       # Telegram C2 interface
│   ├── analytics.py         # Trade Analytics Engine (Sharpe, win-rate)
│   └── oracle_brain.sh      # L2 Gemini Oracle scheduler
├── docs/
│   ├── AI_INFRASTRUCTURE.md # AI stack (LM Studio, Gemini, L1 Shield)
│   ├── HFT_AUDIT_2026.md   # Architecture audit & standards
│   ├── MONITORING.md        # Observability & metrics stack
│   ├── RESEARCH.md          # HFT research notes
│   └── STRATEGY.md          # Trading strategy documentation
├── runtime/
│   ├── engine_state.bin     # mmap shared state (auto-generated)
│   └── risk_state.bin       # mmap risk params (auto-generated)
├── logs/                    # Runtime logs (gitignored)
├── systemd/                 # systemd unit files
├── beroun-start.sh          # Orchestrator startup script
├── watchdog.sh              # mmap heartbeat monitor
├── optimize.sh              # PGO optimization script
├── dashboard.html           # Web dashboard UI (glassmorphism, uPlot)
├── beroun-sniper.service    # systemd service definition
├── Cargo.toml               # Rust dependencies
└── .env                     # API keys (gitignored)
```

## Operační příkazy

```bash
# Status
systemctl status beroun-sniper

# Live logy
journalctl -u beroun-sniper -f

# Restart
systemctl restart beroun-sniper

# Stop (triggers graceful shutdown → cancel_all)
systemctl stop beroun-sniper

# L1 Shield status
ps aux | grep l1_shield

# Sledování obchodů
journalctl -u beroun-sniper -f | grep -E "sniper_fire|trade_aggregated"

# Sledování watchdogu
journalctl -u beroun-sniper -f | grep -E "watchdog|reconnect|shutdown"
```

## Telegram Command & Control

```
📱 /status    → beroun-config export-json → live mmap snapshot
📱 /report    → Denní PnL, equity, obchody
📱 /analytics → Sharpe, win-rate, hourly heatmap
📱 /analyze   → gemini -p → Gemini 3.1 Pro ad-hoc analysis
📱 /grid N    → beroun-config set-grid (3× safety layers)
📱 /pause     → beroun-config pause true → instant stop
📱 /oracle    → oracle_brain.sh → force 26h cycle
📱 /close CONFIRM → Emergency REST API market close
📱 /cautious  → 15-min defensive mode
📱 free-text  → gemini -p → CIO advisor
```

## Známé Limitace

1. **`into_data().to_vec()`** — tungstenite 0.26 vrací `Bytes`, true zero-copy by vyžadoval `fastwebsockets`
2. **Sell ordery** mohou selhat bez BTC balance na exchange walletce
3. **L1 Shield** závisí na správných mmap offsetech — `dump_offsets` tool ověřuje kompatibilitu
4. **Single pair** — multi-pair runtime připraven (TRADING_SYMBOL), ale zatím běží pouze BTC/USD

Dokumentace: [docs/AI_INFRASTRUCTURE.md](docs/AI_INFRASTRUCTURE.md) | [docs/STRATEGY.md](docs/STRATEGY.md) | [docs/MONITORING.md](docs/MONITORING.md)
