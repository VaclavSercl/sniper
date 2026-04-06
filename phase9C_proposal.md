# 🧠 Taktická Analýza a Exekuční Plán: Fáze 9C (Automated ML Pipeline)

Veliteli, podle tvého rozkazu zahajujeme útok na **Fázi 9C**.
Přeskupil jsem pořadí v GitHub Issues tak, abychom přesně kopírovali tvou strategickou linku: **9C -> 10 -> 9A**.

Skript `architect/nightly_retrain.py` již sice existuje a matematicky počítá nové modely z `market_data.db`, avšak aktuálně je odříznutý od bojové linie – ukládá skóre pouze do `.npz` formátu na disk a Rust boti běží s pevně *hardcodovanými* váhami. Ve Fázi 9C propojíme tento skript s živými boty formou bezztrátové "Hot-Swap" injekce za běhu HFT enginu.

Zde je návrh bleskové exekuce:

## 🗄️ KROK 1: Rust MMap Struktura pro Váhy (L0 vrstva)
Vytvoříme `shared/src/ml_types.rs` s lock-free architekturou:
```rust
#[repr(C, align(64))]
pub struct MlWeightsState {
    pub version: AtomicU64,            // Inkrementováno pythonem po nahrání nového modelu
    pub w_fast: [AtomicU32; 11],       // f32 reprezentovaný jako AtomicU32
    pub w_slow: [AtomicU32; 11],
    pub running_mean: [AtomicU32; 10],
    pub running_var:  [AtomicU32; 10],
}
```
*Poznámka:* Přímý lock-free zápis z Pythonu vyžaduje struktury převedené do atomických typů, Rust L0 pak tyto parametry čte s `Ordering::Acquire`. Soubor bude sídlit v `/dev/shm/beroun/ml_weights.bin`.

## 🐍 KROK 2: Python injektor
V `nightly_retrain.py` po úspěšném natrénování (fáze Shadow Validation Gate) nezapíšeme jen do `.npz`, ale binárně přepíšeme namapovaný `/dev/shm/beroun/ml_weights.bin` vrstvu. Inkrementujeme tím hodnotu `version`, což naslouchející boti použijí jako signál.

## 🦀 KROK 3: Integrace ML Shieldu 
V `ml_shield.rs` (a tam, kde se ML počítá v tick-loop smyčce) přestanou boti používat konstanty. 
Rozšířím boty o pointer do nového `ml_weights` souboru. 
Během horké smyčky (per tick) Rust pouze ověří, zda pointer říká `version > local_version`. Pokud ano, nakopíruje si aktualizované váhy bez zamykání a okamžitě podle nich začne pálit blokace toxickým příkazům.

## 🕒 KROK 4: Scehduler (Automatizace na 02:00)
Vytvoříme stabilní `systemd` timer (nebo `crontab`), který zaručí nekompromisní běh v nejklidnější noční době The Nightly Watch a zápis do logů. 

### ❓ Otázka na tebe:
Aktuální parametry sítě v `nightly_retrain.py` (L2 reg = 0.01, lr = 0.0005) a definice 10 RBA fieldů zůstanou zachovány, měním pouze způsob doručení hot-swap vah do paměti Rustu. Souhlasí? 

*Tento dokument je pouze návrh (Neprogramoval jsem). Čekám na tvé schválení k zahájení KROKU 1.*
