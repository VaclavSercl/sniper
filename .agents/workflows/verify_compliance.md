---
description: Jak kontrolovat a validovat nasazení nového bota do Armada infrastruktury
---

# 🤖 AI Workflow: Kontrola compliance nového bota

Při programování a zařazování nového botího jádra (např. `twap-core` nebo `flash-arb`) do systému Sniper Armada, musí budoucí AI agent (např. Antigravity) absolutně garantovat splnění **7 pilířů suverenity**, aby se předešlo narušení produkce.

Zde je přesný postup (Workflow), který AI musí automaticky iterovat dříve, než bota prohlásí za "Připraveného k nasazení":

## 1. Architektura Rizika (The Routing Integrity)
- [ ] V souboru `architect/orchestration.py` je bot obsažen pod klíčem ve slovníku `BOTS`.
- [ ] Bot má explicitně přiřazenu `type: RiskClass.[TYP]`. Bez tohoto by SBP selhal.
- [ ] Typ odpovídá jeho funkci (např. nemá `POSITIONAL` pokud nedrží směrové riziko).

## 2. Hardwarová exkluzivita (Ports & CPU)
- [ ] Port pro lokální API bota nesmí kolidovat. (Hydra=3000, Moon=3001, Grid=3002, Trigon=3003, Nexus=3004). Nový bot dostává `3005` atd.
- [ ] Identifikovat CPU Core a ověřit distribuci `cpu` v orchestraci. Aby se zabránilo thread-contention (křížení na hot-cache L1 linkách), doporučuje se mapovat 1 bot = 1 Core.

## 3. Mapování paměti (Safe Boot Risk Offset)
- [ ] Bot používá `mmap` zero-copy IPC pro příjem PAUSED stavu.
- [ ] Rust struktura pro Risk (např. `twap_risk.bin`) je vyrovnána přes `#[repr(C, align(64))]`.
- [ ] V souboru `architect/safe_boot.py` v proměnné `BOT_RISK_MAP` je vložen záznam s přesným **bytovým offsetem** proměnné `paused: u64`. (Zásadní: Jinak SBP bota nezastaví!)

## 4. Watchdog Resilience
- [ ] V souboru `watchdog.sh` sekce `for BOT_NAME in moonshot grid trigon nexus...` byla aktualizována, aby sledovala i binárku nového bota.

## 5. Deployment Build Stream
- [ ] V souboru `deploy_armada.sh` je nový bot uveden pod `cargo build --release --bin [novejménobota]-core`.
- [ ] Kreslí PGO (Profile-Guided Optimization) instrumentaci.

## 6. Rust "Unsafe" Zero-Alloc pravidla
- [ ] Zdrojový kód bota v `src/main.rs` neobsahuje žádné explicitní `unwrap()` bez checku.
- [ ] Komunikace burzy (parse zpráv) prochází čistě přes `simd_json::prelude` bez realokování dat stringů na haldu (heap).

## 7. Master Dashboard UI (Sovereign View)
- [ ] Soubor `master_dashboard.html` obsahuje vytvořenou záložku/kartu navázanou na stream z `dashboard_server.py`.

Pokud během vývoje agent narazí na to, že tyto parametry chybí, nesmí bota spustit živě.

---

# 🏦 AI Workflow: Integrace nové Crypto Burzy (Exchange)

Pokud agent přidává do ekosystému integraci na zbrusu novou burzu (např. Bybit, OKX, Kraken), musí ověřit a dodržet tyto pilíře kompliance, jinak se vystavuje arbitrážním chybám v datové fúzi:

## 1. Symbol Normalization (Klíčové pro Arbitráž a Risk)
- [ ] Všechny tickery z nové burzy (např. `BTCUSD`, `btc-usdt`, `XBTUSD`) musí být mapovány na jednotný vnitřní standard `sniper_types` (např. `BTC/USD`), aby nedošlo k duplicitním orderbookům ve sdílené paměti.

## 2. API & Env Blueprint
- [ ] V souboru `.env.example` jsou přidány proměnné pro novou burzu (např. `BYBIT_API_KEY`, `BYBIT_API_SECRET`).
- [ ] Zkontrolováno, že `.env` loader načítá tyto klíče bezpečně z hostitele a nesdílí je v žádných lozích (`bot-core.log`).

## 3. Fee & Rate Limit Monitor
- [ ] Burza je zaregistrována v `architect/fee_monitor.py` včetně hardcoded "worst-case" defaultů pro taker/maker fees (aby kalkulace nepočítala s nulovými poplatky během startovního `HTTP 500` downtime).
- [ ] Algoritmus zohledňuje Rate Limity burzy pro REST objednávky.

## 4. Unifikovaný WebSocket Framework (Rust)
- [ ] Parsery nové burzy v implementaci `sniper_types` se vážou na traity L1 Engine `SovereignEngine` a nepoužívají nezávislé proprietární modely. Tím je zachován plynulý průchod nulovou alokací.
- [ ] Datové typy Orderbooku a Trade ticks konvertují `f64` (float) do interního atomického fix-point integer mechanismu (`PRICE_SCALE = 1e8`). Obchodování na Floatech je zakázáno!

## 5. Robustní Ping/Pong Keepalive
- [ ] Je implementován mechanismus odpovědí na ping rámce specifické pro danou burzu, aby se WebSocket odpojoval jen při výpadku a chytil disconnect pomocí `async_alert`.
