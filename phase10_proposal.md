# 🎯 Taktická Analýza a Exekuční Plán: Fáze 10 (Armada Orchestrator)

Veliteli, zanalyzoval jsem tvé GitHub Issues, zejména nejnovější epic **Issue #87: [Phase 10] Armada Orchestrator: Autonomous Capital Re-allocation & Fractional Kelly Matrix**. 

S úspěšným dokončením **Fázev 9C (Kinetic Command Bridge)** máme nyní v Panopticonu vybudované plně obousměrné asynchronní spojení a atomické zámky v reálném čase. Jsme tedy strategicky a technologicky absolutně připraveni předat řízení autonomnímu mozku.

Zde je můj návrh postupného nasazení (krok za krokem), abychom při přechodu z fixního na dynamický kapitál neohrozili živé běžící boty:

## 🗄️ KROK 1: MMap Infrastruktura (Střední mozek)
Než napíšeme logiku Orchestratoru, musíme připravit paměťovou dálnici.
1. V `shared/src` vytvořit `armada_types.rs` se strukturou `ArmadaState` `#[repr(C, align(64))]`.
   - Bude obsahovat `total_equity`, `global_var`, `global_kill_switch` a PnL stream všech botů.
2. Rozšíříme stávající Risk struktury (`RiskState`, `MoonshotRiskState`, atd.) o nové atomické proměnné:
   - `authorized_capital: AtomicU64`
   - `kelly_weight: AtomicU64` (Scaled to fixed point)

## 🧮 KROK 2: Matematické Jádro (Fractional Kelly)
Vytvoříme novou knihovnu (nebo task v `cortex` démonovi) pro běh na 10 Hz:
1. Bude číst `PnL` historii za posledních X minut a počítat Win-Rate a Risk-Reward ratio každé strategie.
2. Algoritmus implementuje klasický Kellyho vzorec `K = W - [(1 - W) / R]`, který ale bude uškrcený tzv. "Fractional" konstantou pro eliminaci tail-risků (např. pád burzy).
3. **Zámek korelací (Covariance Penalty):** Jednoduchá detekce "Jsou Hydra i Moonshot ve stejné long pozici na stejný pár?". Pokud ano, systém plošně sníží povolený limit oběma, aby omezil kumulované riziko expozice.

## 🤖 KROK 3: Integrace na Boty (Odříznutí YAML)
Upravíme logiku botů (`hydra-core`, `moonshot-core` atd). 
Aktuálně načítají svůj obchodní balík staticky. Odstraníme proměnné tvrdých limitů. 
Boti se před každým vložením objednávky "zeptají" ve sdíleném MMap pointeru: 
`let authorized = risk.authorized_capital.load(Ordering::Acquire);`
Zabrání to jakémukoliv přečerpání nad limit.

## 🧠 KROK 4: SBP / L2 Oracle Napojení 
Sovereign Boot Protocol (SBP) a Gemini Agent dostanou schopnost do paměti zasahovat. 
Například zpráva o zvednutí úrokových sazeb FEDem srazí "Váhu" agresivních směrových modelů na 0.0, zatímco na maximum zvýší alokaci u market-making botů a arbitrážím.

### ❓ Otázka na tebe před startem exekuce:
Chceš, aby byl `Armada Orchestrator` samostatná Rust binárka (`armada-core`), která poběží odděleně jako démon na OS, **NEBO** ho mám začlenit jako vlákno existujícího `sovereign-cortex` (L2 Oracle)? Samostatná binárka by zamezila pádu mozku v případě kolapsu sítě, ale Cortex už má přístup ke zprávám Gemini.

*Čekám na tvé příkazy. Žádný další kód zatím nebyl modifikován.*
