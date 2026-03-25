# Monitorování obchodního bota v roce 2026

Tento dokument definuje architekturu pro sledování vysokofrekvenčních (HFT) obchodních systémů v jazyce Rust pro rok 2026.

## 1. Backend a Telemetrie (Nízko-latenční sběr dat)
*   **Asynchronní přístup:** Monitoring nesmí být na „kritické cestě“. Telemetrie musí probíhat neblokujícím způsobem, aby nezpovalovala hlavní obchodní vlákno.
*   **Standardy:** Použití knihovny `tracing` v kombinaci s protokolem **OpenTelemetry**. Zajišťuje strukturované JSON logy a trasování napříč systémem.
*   **Ukládání dat:** GreptimeDB (nativně v Rustu) nebo datové pipeliny jako Vector.
*   **Klíčové metriky:** Měření „tail“ latence (p99) a jitteru (kolísání zpoždění).

## 2. Vizualizace a Dashboardy
*   **Grafana:** Ideální pro systémovou observabilitu v reálném čase (PnL, spready, zátěž HW).
*   **Custom UI:** Next.js 16, React 19 a shadcn/ui. Propojení přes WebSockets pro okamžité aktualizace.

## 3. Bezpečnostní mechanismy (Watchdog a Alerting)
*   **Izolovaný Watchdog:** Samostatný proces, který bota hlídá a v případě pádu jej do 30 sekund automaticky restartuje.
*   **Okamžité notifikace:** Integrace s Telegramem pro každou exekuci a e-mail pro kritické chyby.
*   **Anomální detekce:**
    *   Expozice přesahující 15 % kapitálu.
    *   Příliš široký spread.
    *   Absence obchodů během vysoké volatility.

## 4. Analytika a Journaling
*   **Hodnocení rizika:** Sledování Calmar Ratio (roční výnos / max. drawdown).
*   **Hloubková analýza:** Integrace s TradesViz v2.0 pro AI analýzu obchodních vzorců.

## Shrnutí implementace
Systém bude využívat `tracing` pro sběr dat, samostatný `watchdog` pro stabilitu a Telegram pro real-time upozornění.
