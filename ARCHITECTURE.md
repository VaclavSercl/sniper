# 🐺 Beroun Sniper v8.0 — HFT Trading Bot

Vysokofrekvenční obchodní bot pro Bitfinex BTC/USD. Rust 2024, zero-copy architektura, sub-millisecond tick-to-trade, třívrstvý AI imunitní systém.

## Architektura v8.0 — Tri-Layer AI + Telegram Command & Control

```mermaid
graph TB
    subgraph "CPU Core 1 (pinned, current_thread runtime)"
        subgraph "Task 3: HFT Loop (inline, highest priority)"
            WS_PUB[Market Data WS<br/>TCP_NODELAY<br/>15s watchdog] --> PARSE[simd_json<br/>zero-copy parse]
            PARSE --> BOOK[Order Book<br/>25 bids + 25 asks]
            BOOK --> BBA[Update BBA]
            BBA --> SNIPER["Sniper Logic<br/>Micro-Price + Inv Skew + Dynamic Grid"]
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

## Proč Dual WebSocket?

**Problém:** Jeden WS pro data i ordery → **TCP Head-of-Line blocking**.
Při markentím volume přijímáš stovky book updatů, a tvůj order čeká ve frontě na TCP ACK.

**Řešení:**
| WS | Účel | Auth | Subscribe |
|----|-------|------|-----------|
| Market Data | Příjem order booku | ❌ Ne | ✅ book tBTCUSD |
| Execution | Ordery + notifikace | ✅ Ano | ❌ Ne |

Order string se formátuje na hot path a posílá přes `unbounded_channel` — **nanosekunda**, ne milisekunda čekání na TCP.

## Trading Intelligence (v6.2)

### Micro-Price (Volume-Weighted Mid)
```
micro = (bid_price × ask_vol + ask_price × bid_vol) / total_vol
```
Na rozdíl od hloupého `(bid+ask)/2`, micro-price predikuje směr z volume imbalance.
Velký bid volume → cena se posune k asku (předpovídá růst) → bot se posune dřív než trh.

### L2 Order Book Imbalance (OBI) — NOVÉ v6.2
```
OBI = (Σ bid_vol[0..10] - Σ ask_vol[0..10]) / (Σ bid_vol[0..10] + Σ ask_vol[0..10])
```
Rozsah: -1.0 (čistý prodejní tlak) → +1.0 (čistý nákupní tlak).
Bot čte hloubku 10 hladin order booku a vidí "zdi" — velké objemy, které drží cenu.

### Dynamic Sizing (Konvergence signálů) — NOVÉ v6.2
| OBI | Micro-Price bias | Signály | Sizing |
|-----|-----------------|---------|--------|
| > +0.2 | kladný (bid tlak) | ✅ Shodné | **1.5× base** (přitlačí) |
| < -0.2 | záporný (ask tlak) | ✅ Shodné | **1.5× base** (přitlačí) |
| > +0.1 | záporný | ❌ Protichůdné | **0.7× base** (opatrný) |
| < -0.1 | kladný | ❌ Protichůdné | **0.7× base** (opatrný) |
| jinak | jakýkoli | Neutrální | **1.0× base** |

Clamp: `[0.5× base, 2.0× base]`

### Inventory Skew (Řízení zásob)
```
skew = -(position / max_position) × 2 × grid
```
| Pozice | Ratio | Skew | Efekt |
|--------|-------|------|-------|
| 0 BTC | 0.0 | $0 | Symetrický market making |
| +50% max | +0.5 | -1×grid | Brzdí nákupy, zlevňuje prodeje |
| +100% max | +1.0 | -2×grid | Maximální obranný posun |
| -50% max | -0.5 | +1×grid | Brzdí prodeje, zlevňuje nákupy |

### Volatility Engine (Dynamický Grid)
Background task (500ms polling): sbírá mid-price do 60-slot ring bufferu (30s okno).
```
dynamic_grid = base($2) + 15% × price_range(30s)
clamped to [$2, $50]
```
- Nízká volatilita → tight spread ($2-3) → více obchodů
- Vysoká volatilita → wide spread ($5-50) → ochrana před adverse selection
- Loguje `volatility_spike` při range > $50

## Watchdog

### In-Process (v5.3, nové)

| Mechanismus | Timeout | Reakce |
|---|---|---|
| Market Data read | 15s | `should_reconnect = true` |
| Exec Reader read | 15s | `err_tx.send("exec_socket_timeout")` |
| Exec Writer send fail | okamžitě | `err_tx.send("writer_socket_error")` |
| `err_rx.recv()` v HFT loop | biased select | `should_reconnect = true` |

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

## Adresářová struktura

```
/home/wwwenda/hft-sniper/
├── src/
│   ├── main.rs              # Core: async_main, 3 tasks, watchdog, shutdown
│   ├── types.rs              # EngineState, RiskState, OrderBookLevel
│   ├── dashboard.rs          # Dashboard WS server (:3000)
│   ├── monitor.rs            # TUI dashboard (ANSI, 5 FPS, mmap reader)
│   └── sovereign_ai.rs       # AI risk module (separate binary)
├── runtime/
│   ├── engine_state.bin      # mmap shared state (auto-generated)
│   └── risk_state.bin        # mmap risk params (auto-generated)
├── logs/
│   └── alerts.log            # Telegram + file alerts
├── dashboard.html            # Web dashboard (glassmorphism, uPlot)
├── .env                      # BITFINEX_API_KEY, BITFINEX_API_SECRET
├── Cargo.toml
└── ARCHITECTURE.md           # This file
```

## Telegram Notifikace (v6.0)

### BotEvent Enum
```rust
pub enum BotEvent {
    Alert(String),            // Okamžité odeslání (startup, shutdown, reconnect)
    Trade { amount, price },  // Agregováno do hodinového reportu
}
```

| Událost | Typ | Chování |
|---------|-----|--------|
| Startup | Alert | Okamžitě: `*Beroun Sniper v6.0 ONLINE*` |
| Trade | Trade | Agreguje se: buys/sells/volume/poslední cena |
| Hodinový report | Timer | Každou hodinu: počet obchodů, objem, posl. cena |
| Reconnect | Alert | Okamžitě s důvodem |
| Shutdown | Alert | Okamžitě: cancel_all + flush |

### Hodinový Report (ukázka)
```
🐺 📊 *Hodinový Report*
📈 Obchodů: `20` (12 nákup / 8 prodej)
💰 Objem: `0.01440` BTC
💲 Posl. cena: `$69,752.50`
```

## TUI Monitor (`cargo run --release --bin beroun-monitor`)
ANSI terminálový dashboard, 5 FPS, čte mmap přímo.
```
══════════════════════════════════════════════════════════════════════
 🐺 BEROUN SNIPER v6.0          🟢 RUNNING
══════════════════════════════════════════════════════════════════════
 ┌─ TRH: tBTCUSD ───────────┐  ┌─ BOT METRIKY ───────────┐
 │ Best Ask:    $ 69,640.00  │  │ T2T Latence:    42 µs │
 │ Micro-Price: $ 69,639.50  │  │ Dyn Grid:    $ 4.25  │
 │ Mid-Price:   $ 69,639.00  │  │ Inv Skew:    $+1.73  │
 │ Best Bid:    $ 69,638.00  │  │ PnL:         $+2.15  │
 │ Spread:      $ 2.00       │  │                      │
 └────────────────────────────┘  └──────────────────────┘
```

## Operační příkazy

```bash
# Status
systemctl --user status beroun-sniper

# Live logy
journalctl --user -u beroun-sniper -f

# Restart
systemctl --user restart beroun-sniper

# Stop (triggers graceful shutdown → cancel_all)
systemctl --user stop beroun-sniper

# Sledování obchodů
journalctl --user -u beroun-sniper -f | jq 'select(.fields.event == "sniper_fire" or .fields.event == "trade_aggregated")'

# TUI Monitor (druhé SSH okno)
cargo run --release --bin beroun-monitor

# Sledování watchdogu
journalctl --user -u beroun-sniper -f | jq 'select(.fields.event | startswith("watchdog") or startswith("reconnect") or startswith("shutdown"))'

# Sledování chyb
journalctl --user -u beroun-sniper -f | jq 'select(.fields.event | test("error|timeout|closed"))'
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

## Optimalizace na Hot Path

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
| **15s watchdog** | timeout() na obou WS | Detekce half-open |
| **abort() handles** | Zombie task prevention | Zero memory leaks |
| **Book zeroing** | Reconnect → clean slate | No stale data trading |
| **Micro-Price** | Volume-weighted mid | ~1-2 tick prediction edge |
| **Inventory Skew** | Position-based bias | Prevents inventory blowup |
| **Volatility Engine** | Dynamic grid from 30s window | Adaptive spread width |

## Bezpečnostní opravy

| Problém | Řešení |
|---|---|
| UB: `&mut EngineState` aliasing | Raw `*mut EngineState` pointer |
| Float precision drift | `.round()` před `as u64/i64` |
| i64→u64 overflow | `i64` aritmetika s `.max(0)` |
| Orphan orders on shutdown | `cancel_all` → 500ms flush |
| Half-open connections | 15s timeout watchdog |
| Zombie tasks on reconnect | `JoinHandle::abort()` |

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
| bias_offset | 0 | Directional bias (signed) |

## Order Tracking (v6.1)

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

### Graceful Shutdown
- Čte `active_buy_id` / `active_sell_id` z mmap
- Posílá cílený cancel
- Fallback na `cancel_all` pokud nejsou žádné trackované ID

## Lokální AI Node (v8.0)

### Stack
| Komponenta | Hodnota |
|-----------|--------|
| Runtime | LM Studio v0.4.7 (llmster headless) |
| Model | Phi-3.5-mini-instruct (3.8B, Q4_K_S) |
| GPU | GTX 1060 6GB (VRAM: ~3.7 GB model + ~2.3 GB KV cache) |
| API | `localhost:1234` (OpenAI-compatible) |
| Sampling | Greedy: temp=0, top_p=0.1, max_tokens=5 |
| Systemd | `beroun-ai.service` (Restart=always, CPU core 2) |

### AI Safety Systems (v8.0)
| System | Trigger | Akce |
|--------|---------|------|
| **Heartbeat Fuse** | `ai_heartbeat_ms` > 30s stale | Bias zeroed, pure grid |
| **Thermal Guard** | GPU ≥ 82°C | Cycle 5s → 10s |
| **Sanity Clamp** | beroun-config writes | Grid $1-$200, ±50%/update |
| **Alpha Tracking** | Every trade execution | Measures AI $ contribution |

### Třívrstvý AI Pipeline
```
L0 (µs)  main.rs         — Rust zero-copy, simd_json, mmap
L1 (5s)  sovereign_ai.rs — LM Studio GPU, OBI→bias, heartbeat
L2 (26h) oracle_brain.sh — Gemini 3.1 Pro, RSS→grid, beroun-config
```

### Telegram Command & Control (v8.0)
```
📱 /status  → beroun-config export-json → live mmap snapshot
📱 /analyze → gemini -p → Gemini 3.1 Pro ad-hoc analysis
📱 /grid N  → beroun-config set-grid (3× safety layers)
📱 /pause   → beroun-config pause true → instant stop
📱 /oracle  → oracle_brain.sh → force 26h cycle
📱 free-text → gemini -p → CIO advisor
```

### Oracle Pipeline (L2)
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

Dokumentace: [docs/AI_INFRASTRUCTURE.md](docs/AI_INFRASTRUCTURE.md)

## Známé Limitace

1. **`into_data().to_vec()`** — tungstenite 0.26 vrací `Bytes`, true zero-copy by vyžadoval `fastwebsockets`
2. **Sell ordery** mohou selhat bez BTC balance na exchange walletce
3. **AI inference** závisí na LM Studio dostupnosti — heartbeat fuse ochrání při výpadku

