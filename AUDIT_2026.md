# AUDIT_2026: Zavedení Bezpečné Fixed-Point Aritmetiky (L1 Hot-Path)

## Zjištěný stav (Kritický)
V aktuálním repozitáři **neexistuje** žádná definovaná bezpečná (wrapper) struktura pro HFT výpočty. Programátoři přímo konvertují surové typy `u64` a `i64` do `f64` pomocí operátoru `as f64 / PRICE_SCALE`. Toto způsobuje výkonnostní degradaci, riziko zaokrouhlovacích chyb a znemožňuje deterministické testování exekučního jádra.

## Postup zavádění do hot-pathu
Hlavní výzvou je zavedení safe-math mechanismu **bez poklesu výkonu** (tzv. *Zero-Cost Abstraction*). 

1. **Vytvoření modulu `shared/src/math.rs`:** Vytvoříme `struct FixedPrice(i64)` a pomocí maker nebo traitů `#derive` nad ním vystavíme základní operace. 
2. **Definice `#[repr(transparent)]`:** Tento klíčový atribut garantuje kompilátoru (LLVM), že v paměti bude `FixedPrice` interpretováno jako jediný nativní i64 typ, bez jakéhokoliv memory overheadu.
3. **Přetížení operátorů (std::ops):** Naimplementujeme `Add`, `Sub`, `Mul` a `Div` tak, abychom ošetřili overflow/underflow a bezpečný násobič modula (PRICE_SCALE = 1e8).
4. **Refactoring L1 Cortex:** Všechny alokace a operátory počítající spready `(bid - ask) / total` použijí nativní `FixedPrice` abstrakci. Zmizí klíčové slovo `f64`.

## Ukázka implementace (Zero-Cost Abstraction)

```rust
// shared/src/math.rs

use std::ops::{Add, Sub, Mul, Div};

pub const PRICE_SCALE: i64 = 100_000_000;

/// Zero-cost abstraction wrapper pro všechny L0/L1 výpočty.
/// Zajistí absenci f64 a přímou kompilaci na procesorové add/mul registry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
#[repr(transparent)]
pub struct FixedPrice(pub i64);

impl FixedPrice {
    #[inline(always)]
    pub const fn new(scaled_val: i64) -> Self {
        Self(scaled_val)
    }

    #[inline(always)]
    pub fn zero() -> Self {
        Self(0)
    }
}

// Ochrana proti přetečení (Sčítání)
impl Add for FixedPrice {
    type Output = Self;
    #[inline(always)]
    fn add(self, rhs: Self) -> Self::Output {
        // V debug módu panikuje na overflow, v release obaluje.
        // LLVM z toho udělá jedinou instrukci `add`.
        Self(self.0.checked_add(rhs.0).expect("FixedPrice Overflow (Add)"))
    }
}

// Ochrana proti podtečení (Odčítání)
impl Sub for FixedPrice {
    type Output = Self;
    #[inline(always)]
    fn sub(self, rhs: Self) -> Self::Output {
        Self(self.0.checked_sub(rhs.0).expect("FixedPrice Underflow (Sub)"))
    }
}

// HFT Násobení s vyrovnáním měřítka
impl Mul for FixedPrice {
    type Output = Self;
    #[inline(always)]
    fn mul(self, rhs: Self) -> Self::Output {
        // Pokud 1 BTC = 1e8 a kupujeme 1.5 BTC (1.5e8)
        // Vysledek: (1e8 * 1.5e8) / 1e8 = 1.5e8
        // Převedeme do i128 pro absolutní jistotu, že mezisoučet nepřeteče i64 blok.
        let prod = (self.0 as i128) * (rhs.0 as i128);
        Self((prod / (PRICE_SCALE as i128)) as i64)
    }
}

// HFT Dělení s vyrovnáním měřítka
impl Div for FixedPrice {
    type Output = Self;
    #[inline(always)]
    fn div(self, rhs: Self) -> Self::Output {
        assert!(rhs.0 != 0, "FixedPrice Division by Zero");
        let a = (self.0 as i128) * (PRICE_SCALE as i128);
        Self((a / (rhs.0 as i128)) as i64)
    }
}
```

### Nasazení v Hot-Pathu (L1 Cortex Ukázka)
Bez nutnosti použít `f64` dosáhneme naprosto exaktního Spreadu jen pomocí Integer registrů (ALU):
```rust
let bid = FixedPrice::new(50000_000_000000);  // $50k
let ask = FixedPrice::new(50002_000_000000);  // $50,002

// Zero-cost sub & div s ochranou (žádný float)
let spread = ask - bid;                 // 2_000_000000
let profit = (spread / ask) * margin;   // ALU operace
```
