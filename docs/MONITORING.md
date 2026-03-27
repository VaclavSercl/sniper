# Monitoring & Observability — Beroun Sniper v10.0

Architektura monitoringu pro HFT systém. Kombinace nízko-latenční telemetrie, real-time dashboardů a alertingu.

## Stack

| Vrstva | Nástroj | Účel |
|--------|---------|------|
| Telemetrie | `tracing` + JSON | Strukturované logy → journald |
| Dashboard (Web) | Custom HTML + WebSocket | Live metriky na `:3000` |
| L1 Shield | `l1_shield.py` (Python) | Real-time OBI/Skew monitoring via mmap |
| Alerting | Telegram Bot API | BotEvent: Alert (okamžitý) + Trade (hodinový report) |
| Analytics | `analytics.py` (Python) | Sharpe, win-rate, PnL heatmap |
| Watchdog (internal) | In-process (15s timeout) | Auto-reconnect při výpadku WS |
| Watchdog (external) | `watchdog.sh` (mmap heartbeat) | Restart engine if stale |
| Process Mgmt | systemd | `Restart=always`, high priority |

## Klíčové metriky

| Metrika | Zdroj | Dashboard | L1 Shield |
|---------|-------|-----------|-----------|
| Tick-to-Trade (µs) | `t2t_micros` v mmap | ✅ Web | ❌ |
| Mid-Price | `mid_price` v mmap | ✅ Web | ✅ |
| L2 OBI | `l2_imbalance` v mmap | ✅ Web | ✅ |
| Inventory Skew | `current_skew` v mmap | ✅ Web | ❌ |
| Best Bid/Ask | `best_bid/ask` v mmap | ✅ Web | ✅ |
| Net Position | `net_position` v mmap | ✅ Web | ❌ |
| Realized PnL | `realized_pnl` v mmap | ✅ Web | ❌ |
| AI Bias Offset | `bias_offset` v risk_state | ✅ Web | ✅ (writes) |
| Sweep Toxic Flag | L1 Shield detection | ❌ | ✅ |
| Total Equity | Computed (wallet + btc×mid) | ✅ Telegram | ❌ |

## Telegram Notifications

### BotEvent Enum
```rust
pub enum BotEvent {
    Alert(String),            // Okamžité odeslání (startup, shutdown, reconnect)
    Trade { amount, price },  // Agregováno do hodinového reportu
}
```

| Událost | Typ | Chování |
|---------|-----|--------|
| Startup | Alert | Okamžitě: `*Beroun Sniper v10.0 ONLINE*` |
| Trade | Trade | Agreguje se: buys/sells/volume/poslední cena |
| Hodinový report | Timer | Každou hodinu: počet obchodů, objem, posl. cena |
| Denní report | Timer | 08:00 CET: equity, PnL, obchody |
| Reconnect | Alert | Okamžitě s důvodem |
| Emergency Close | Alert | Okamžitě: market close confirmation |
| Shutdown | Alert | Okamžitě: cancel_all + flush |

## Analytics Engine (analytics.py)

| Metrika | Popis |
|---------|-------|
| Sharpe Ratio | Risk-adjusted returns (hourly window) |
| Win Rate | % profitable trades |
| Hourly PnL Heatmap | 24h color-coded performance grid |
| Max Drawdown | Largest peak-to-trough loss |
| Volume Tracking | 30-day accumulation for fee tiers |

## Budoucí rozšíření

### GreptimeDB (time-series databáze v Rustu)
- Ukládání historických tick dat a PnL
- Grafana integrace pro dlouhodobou analýzu

### OpenTelemetry
- Neblokující SDK pro trace/metrics export
- Asynchronní pipeline mimo hot path

## Referenční projekty

| Projekt | Relevance |
|---------|-----------|
| `barter-rs/barter-rs` | EngineState replica pattern (inspirace pro mmap IPC) |
| `Shiva-129/HFT_system` | Axum + Chart.js dashboard |
| `GreptimeTeam/greptimedb` | Time-series DB v Rustu |
