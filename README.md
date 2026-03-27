# 🐺 BEROUN SNIPER v11.0 — The Sentinel Singularity

**Autonomous HFT Market-Making Bot** for Bitfinex BTC/USD with cross-exchange intelligence, AI-driven grid management, and real-time macro awareness.

## Architecture

Three-layer lock-free architecture connected via mmap IPC (`/dev/shm/beroun/`):

| Layer | Runtime | Role | Latency |
|-------|---------|------|---------|
| **L0** | Rust (async) | Execution, orderbook, ghost injection | < 1ms |
| **L1** | Python | OBI Shield, sweep detection, ghost mode | ~50ms |
| **L2** | Python + Gemini | Oracle, strategy, parameter tuning | ~5min |
| **Macro** | Python | Binance sync, F&G, news sentiment | real-time |

## Key Features (v11.0)

### Sentinel Defense Engine
- **Global Fair Value**: Weighted composite from local micro-price (60%), Binance VWAP (30%), and macro sentiment (10%)
- **Ghost Reposition**: Automatic shadow grid shift when fair value diverges > 0.05% from local
- **Anti-Flicker Guard**: Minimum order lifetime prevents exchange surveillance flags

### Macro Intelligence
- **Binance Cross-Exchange**: WebSocket aggTrade stream detects large sells (> 1 BTC) and sweep events (> 5 BTC/10s)
- **Fear & Greed Index**: Polled every 5 minutes from Alternative.me
- **News RSS Sentiment**: 4 crypto feeds (CoinTelegraph, CoinDesk, Decrypt, Bitcoin Magazine) with keyword scoring

### Ghost Orders (Shadow Liquidity)
- Only 1 public "tip" order visible per side
- Shadow levels fire as IOC when micro-price enters trigger zone
- Velocity-based anti-toxic rejection prevents adverse fills

### Sovereign AI Control
- **Intent Modes**: `/aggressive`, `/defensive`, `/scout`, `/sovereign` via Telegram
- **Dynamic Registry**: All tactical params (grid, fire interval, ghost trigger) tunable via mmap atomics
- **Safety Guardrails**: Hardcoded capital limits and DLL circuit breakers are non-overridable

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Live engine state + equity + PnL |
| `/macro` | Macro intelligence (bias, F&G, Binance sweep) |
| `/ai` | AI Shield + Ghost + Sovereign state |
| `/aggressive` | Intent: tight grid, fast fire, no ghost |
| `/defensive` | Intent: wide grid, slow fire, ghost ON |
| `/scout` | Intent: shadow mode for data collection |
| `/sovereign` | Intent: full AI autonomy (default) |
| `/validate` | Compare AI decisions vs baseline |
| `/daily` | Force daily PnL report |

## Quick Start

```bash
# Build
cargo build --release

# Install Python dependency
pip install --user websocket-client

# Start all services
./beroun-start.sh

# Or via systemd
sudo systemctl start beroun-sniper.service
```

## File Structure

```
src/
├── main.rs          # L0 Engine: WebSocket, execution, ghost injection, sentinel
├── types.rs         # Shared mmap struct (EngineState, RiskState)
├── brain.rs         # SQLite persistence, lesson engine, nightly analysis
├── dashboard.rs     # HTMX real-time dashboard (port 3000)
scripts/
├── l1_shield.py     # L1: OBI monitoring, sweep detection, ghost mode control
├── sniper_orchestrator.py  # L2: Gemini Oracle, strategic tuning cycles
├── tg_listener.py   # Telegram command interface
├── macro_monitor.py # Binance sync, F&G, news RSS sentiment → mmap
```

## mmap Layout (EngineState)

All fields are lock-free atomics. Key v11.0 additions at end:

| Offset | Type | Field | Description |
|--------|------|-------|-------------|
| 1824 | i64 | macro_bias | -10000..+10000 = -1.0..+1.0 |
| 1832 | u64 | macro_source_ts | Last macro update (epoch ms) |
| 1840 | u64 | binance_sweep_ts | Last BNB large sell (epoch ms) |
| 1848 | u64 | macro_fear_greed | 0..100 |
| 1856 | i64 | binance_mid_price | BNB VWAP × PRICE_SCALE |
| 1864 | i64 | global_fair_value | Weighted FV × PRICE_SCALE |
| 1872 | u64 | ai_min_order_lifetime_ms | Anti-flicker (default 100) |
| 1880 | u64 | sentinel_repositions | Ghost reposition counter |

## Safety

- **DLL Circuit Breaker**: Hardcoded daily loss limit halts trading
- **Capital Guard**: Position size never exceeds max_inv_delta
- **Anti-Cross**: Bid always < best ask, ask always > best bid
- **AI Heartbeat**: Stale AI (>30s) → bias zeroed automatically
- **Anti-Flicker**: Orders stay in book minimum 100ms (configurable)
- **Ghost Velocity**: Fast-moving toxic flow rejected from injection

## License

Private / Proprietary
