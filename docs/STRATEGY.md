# Strategie HFT Bota 2026: OFI a Grid

Tento dokument shrnuje pokročilou obchodní strategii pro vysokofrekvenční obchodování (HFT) páru BTC/USD na burze Bitfinex, optimalizovanou pro prostředí s nulovými poplatky a implementaci v jazyce Rust.

## 1. Hlavní principy a indikátory

Strategie opouští tradiční technickou analýzu (svíčky, indikátory) a zaměřuje se na mikrostrukturu trhu:

*   **Order Flow Imbalance (OFI):** Klíčový rozhodovací mechanismus. Sleduje nerovnováhu mezi nákupními (Bids) a prodejními (Asks) limitními objednávkami v reálném čase. Pokud jedna strana převýší druhou o více než 20 %, algoritmus detekuje směrový tlak.
*   **High-Frequency Grid Market Making:** Neustálé umisťování limitních objednávek těsně kolem středové ceny (mid-price). Strategie profituje ze spreadu a mikropohybů.
*   **Asymetrické vychýlení:** Na základě on-chain dat o akumulaci velryb je grid mírně nakloněn ve prospěch dlouhých (long) pozic.

## 2. Exekuce (Sniper)

Exekuční jádro je navrženo pro maximální rychlost v řádu mikrosekund:

*   **Technologie:** Implementace v Rustu pro minimální latenci (viz architektura v Rust_HFT_Bitcoin_Obchodni_Bot.md).
*   **Pasivní exekuce:** Vstupy i výstupy probíhají výhradně přes pasivní limitní objednávky (Post-Only), což maximalizuje přesnost a využívá 0% poplatky.
*   **Hot-path smyčka:** Čistě matematické výpočty OFI a gridu bez blokujících operací.

## 3. Risk Management (AI Manažer)

Strategie využívá moderní asynchronní architekturu, která odděluje rychlost od hluboké analýzy:

*   **Role "Risk Manager":** Umělá inteligence běžící paralelně s exekučním jádrem. Analyzuje makroekonomické zprávy a tržní sentiment.
*   **Asynchronní komunikace:** AI nepřímo neovlivňuje každou transakci, ale předává strategické pokyny (úprava velikosti pozic, posun spreadu) přes sdílenou paměť (Memory-Mapped Files).
*   **Prevence latence:** Díky asynchronnímu přístupu inference neuronové sítě (trvající milisekundy) nebrzdí exekučního "Snipra" (pracujícího v mikrosekundách).

## Shrnutí výhod
Kombinace nulových poplatků, mikrosekundové latence Rustu a inteligentního AI dozoru vytváří robustní systém schopný profitovat z tržní neefektivity v roce 2026.
