# Monitorovací nástroje a inspirace pro HFT (2026)

Tento dokument shrnuje doporučené open-source nástroje a referenční projekty pro stavbu monitorovacího stacku HFT botů v jazyce Rust.

## 1. Sběr dat a Backend (Low-Latency)
*   **GreptimeDB (`GreptimeTeam/greptimedb`)**: Vysoce výkonná time-series databáze psaná v Rustu. Sjednocuje metriky, logy a trace. Ideální pro obrovské objemy dat z HFT.
*   **OpenTelemetry Rust (`open-telemetry/opentelemetry-rust`)**: Oficiální SDK pro generování neblokujících telemetrických dat.

## 2. Vizualizace a Frontend
*   **Grafana & Greptime**: Standard pro real-time dashboardy s přímou podporou pro GreptimeDB.
*   **CryptoPulse (`adrianhajdin/coinpulse`)**: Referenční projekt pro custom dashboardy využívající **Next.js 16** a **TailwindCSS v4** pro vizualizaci bez lagu.

## 3. Referenční HFT Projekty
*   **HFT_system (`Shiva-129/HFT_system`)**: Využívá **Axum** a **Chart.js** pro vizualizaci PnL, pozic a logů v reálném čase.
*   **OpenAlgo (`marketcalls/openalgo`)**: Ekosystém s **React 19** UI a Rust SDK pro čistou vizualizaci strategií.
*   **Barter-rs (`barter-rs/barter-rs`)**: Obsahuje **EngineState replica manager** – klíčový koncept pro bezpečné, asynchronní zpracování dat z runtime pro UI nebo notifikace (např. Telegram).

## Analýza pro Beroun Sniper
Klíčovým vylepšením pro náš systém je koncept **EngineState replica manager** z projektu Barter-rs. Umožňuje Sniperovi posílat kopii svého stavu do "repliky", kterou pak AI a UI mohou číst bez rizika, že by Sniper musel čekat na zámky (locks).
