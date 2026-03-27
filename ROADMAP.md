# 🗺️ Beroun Sniper — Roadmap

## ✅ Phase 1: v9.2 "Accountant" (DONE — 27.3.2026)

- [x] #11 Emergency Market Close (`/close CONFIRM` via Bitfinex REST API)
- [x] #12 Total Equity Tracking (`wallet_usd + btc × mid_price`)
- [x] #13 Telegram Daily Report (auto 08:00 CET + `/report`)

## ✅ Phase 2: v9.5 "Sentinel" (DONE — 27.3.2026)

- [x] #14 Liquidity Hole Detection (L2 depth MA, grid ×3 on holes)
- [x] #15 Macro-Event Awareness (`/cautious` mode, 15 min auto-restore)
- [x] #17 Adaptive Grid Spacing (fill-rate balance → grid multiplier 0.7-1.5×)

## ✅ Phase 3: v10.0 "Apex Predator" (DONE — 27.3.2026)

- [x] #19 Dynamic Fee Optimizer (monthly_volume_usd tracking, fee tier estimation)
- [x] #22 Trade Analytics Engine (Sharpe, win-rate, hourly heatmap, max drawdown)
- [x] #23 Multi-Pair Foundation (TRADING_SYMBOL abstraction, 0 hardcoded pairs)

---

## 🔜 Phase 4: v11.0 "Colossus" (Planned)

### 4.1 Dashboard Upgrade
**Priority:** High | **Effort:** 3-4h

Dashboard na `http://localhost:3000` modernizovat:
- [ ] Total Equity widget s live grafy (Chart.js)
- [ ] Hourly PnL heatmap vizualizace (barevná mřížka 24h)
- [ ] Fill-rate balance indikátor
- [ ] Adaptive grid stav (grid_mult, fill balance)
- [ ] Liquidity depth monitor (L2 vizualizace)
- [ ] Volume tracker + fee tier progress bar

### 4.2 PGO Profilování
**Priority:** Medium | **Effort:** 2-3h

Profile-Guided Optimization pro maximální latenci:
- [ ] Instrumentační build: `RUSTFLAGS="-Cprofile-generate=/tmp/pgo" cargo build --release`
- [ ] Sběr profilu při reálném obchodování (30 min minimálně)
- [ ] Optimalizovaný rebuild: `RUSTFLAGS="-Cprofile-use=/tmp/pgo" cargo build --release`
- [ ] Benchmark: T2T latence před/po (cíl: <5µs)
- [ ] Integrace do `optimize.sh` skriptu

### 4.3 Multi-Pair Runtime
**Priority:** Medium | **Effort:** 8-10h

Plná podpora více párů současně:
- [ ] `HydraPair` trait s konfigurací per pair
- [ ] Duplikace EngineState per pair (mmap: `engine_state_{symbol}.bin`)
- [ ] Shared Capital Guard napříč páry
- [ ] Independent grid/levels per pair
- [ ] Kandidátní páry: ETH:USD, SOL:USD
- [ ] Risk aggregace: celkové exposure

### 4.4 Backtest Framework
**Priority:** High | **Effort:** 5-6h

Replay engine pro simulaci strategie na historických datech:
- [ ] Log parser (analytics.py základ je hotový)
- [ ] Orderbook replay z trading logů
- [ ] Simulated matching engine
- [ ] Parameter sweep: grid_step, levels, thresholds
- [ ] Output: Sharpe, max DD, equity curve
- [ ] CLI: `python3 scripts/backtest.py --date 2026-03-27 --grid 3.5 --levels 3`

### 4.5 Automated Strategy Tuning
**Priority:** Medium | **Effort:** 4-5h

Data-driven optimalizace parametrů:
- [ ] Sbírání víkendových dat z `/analytics`
- [ ] Bayesian optimization grid_step a levels
- [ ] Hourly heatmap → automatické pause v nejhorších hodinách
- [ ] Fill-rate feedback loop → adaptivní base_grid
- [ ] Oracle doporučení na základě 7-day rolling analytics
- [ ] Safety: max ±20% change per tuning cycle

---

## 📊 Metrics to Track (Weekend Monitoring)

Po nasazení v10.0, sledovat přes víkend:
1. `adaptive_grid` logy — jak se grid_mult mění
2. `liquidity_hole` / `liquidity_recovered` eventy
3. Sharpe Ratio trend (denní `/analytics`)
4. Reconnect frequency (cíl: 0)
5. Total Equity trend (denní `/report` v 08:00)
