# Open-source HFT monitoring v Rustu (2026)

Tento dokument definuje moderní technologický stack pro nízko-latenční sledování a vizualizaci obchodních botů v jazyce Rust.

## 1. Backend a Telemetrie (Nízko-latenční sběr dat)
*   **GreptimeDB (`GreptimeTeam/greptimedb`)**: Vysoce výkonná time-series databáze psaná v Rustu. Optimalizovaná pro masivní zápis (metriky, tick-data, logy) v reálném čase.
*   **OpenTelemetry Rust (`open-telemetry/opentelemetry-rust`)**: SDK pro generování neblokujících telemetrických dat. Telemetrie musí běžet asynchronně mimo kritickou cestu (hot path).
*   **EngineState Replica (vzor Barter-rs)**: Oddělený "manažer stavu", který udržuje repliku stavu enginu a asynchronně ji publikuje, aby neblokoval obchodní logiku.

## 2. Vizualizace a Real-time Dashboard
*   **Grafana**: Pro technické metriky (CPU, latence sítě, hloubka fronty) s využitím GreptimeDB pluginů.
*   **Custom UI (Next.js 16 + Tailwind v4)**: Pro obchodní data (PnL křivka, vizualizace orderbooku). Moderní stack zajistí plynulé zobrazení WebSocket streamů.
*   **Klíčové metriky k vizualizaci**:
    *   Unrealized PnL (v reálném čase).
    *   Latence „Tick-to-Trade“.
    *   Slippage (rozdíl mezi očekávanou a realizační cenou).

## 3. Doporučené referenční projekty
1.  **Shiva-129/HFT_system**: Propojení Rust backendu s Axum frameworkem pro dashboard.
2.  **barter-rs/barter-rs**: Modulární a robustní trading framework v Rustu.
3.  **marketcalls/openalgo**: SDK pro komunikaci mezi botem a frontendem.

## Analýza pro Beroun Sniper
Prioritou je implementace **EngineState Replica** vzoru pro oddělení exekuce od monitoringu a příprava na integraci **GreptimeDB** pro ukládání historických dat o obchodech.
