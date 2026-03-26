# Monitoring & Observability — Beroun Sniper

Architektura monitoringu pro HFT systém. Kombinace nízko-latenční telemetrie, real-time dashboardů a alertingu.

## Stack

| Vrstva | Nástroj | Účel |
|--------|---------|------|
| Telemetrie | `tracing` + JSON | Strukturované logy → journald |
| Dashboard (Web) | Custom HTML + WebSocket | Live metriky na `:3000` |
| Dashboard (TUI) | `beroun-monitor` (ANSI) | SSH terminál, 5 FPS, mmap reader |
| Alerting | Telegram Bot API | BotEvent: Alert (okamžitý) + Trade (hodinový report) |
| Watchdog | In-process (15s timeout) | Auto-reconnect při výpadku WS |
| Process Mgmt | systemd (user) | `Restart=always`, linger |

## Klíčové metriky

| Metrika | Zdroj | Dashboard |
|---------|-------|-----------|
| Tick-to-Trade (µs) | `t2t_micros` v mmap | ✅ Web + TUI |
| Micro-Price | `micro_price` v mmap | ✅ Web + TUI |
| L2 OBI | `l2_imbalance` v mmap | ✅ TUI |
| Inventory Skew | `current_skew` v mmap | ✅ Web + TUI |
| Dynamic Order Size | `current_order_usd` v mmap | ✅ TUI |
| Active Order IDs | `active_buy/sell_id` v mmap | ✅ TUI |
| Net Position | `net_position` v mmap | ✅ Web + TUI |
| Realized PnL | `realized_pnl` v mmap | ✅ Web + TUI |

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
