# Analyze_Moonshot.md

<thinking>
**Architektura & Problém Slippage u Moonshota:**
Moonshot byl navržen jako snajpr, který pálí gigantické příkazy (např. celý margin) jako IOC za `mid_price` nebo mírně pod/nad, když se aktivuje Toxic Storm.
Zjištěný fatální defekt (Logical Flaw - Zero Book Depth):
Moonshot momentálně odebírá kanál `ticker`. Vidí pouze Top Bid a Top Ask bez objemů! Pokud pošle objednávku za $200k na cenu, kde je Volume jen za $5k, IOC zbytek objednávky okamžitě zabije (zruší). Ztrácíme ziskový potenciál a nezapojíme kapitál. 

Pro Surgical Refactor musím implementovat "Slippage Matrix", tedy agregaci hloubky knihy do weighted average ceny, ale bez paměťových alokací na hot-pathu:
1. Moonshot musí změnit subscription channel z `ticker` na `book`.
2. Aplikovat Zero-Allocation orderbook update engine (stejný mechanismus jako lokálně udržuje Hydra), pouze stack-alokovaná pole.
3. Kdykoli dojde k triggeru (Toxic Storm / Flash Crash), Moonshot sestoupí For-cyklem do vektorových hladin (Bid for Sell, Ask for Buy), odkousne si z každé hladiny žádaný USD objem a spočte **WAP (Weighted Average Price)**.
4. Pokud je Slippage (rozdíl mezi WAP a Mid-price) vetší než max tolerovaný práh (např. stanovený v algoritmu), zmenší úměrně celkový posílaný kapitál.

Vzhledem k tomu, že Hydra už udržuje Orderbook L0, druhou (extenzivnější) variantou je vystavit Orderbook do MMap a číst ho v Moonshotu. Ale pro zachování maximální nezávislosti Moonshota na pádech jiných deamonů je robustnější přidat Zero-Cost Book parser přímo do Moonshot WS smyčky.
</thinking>

### 1. BUG HUNTING & SECURITY:
- **Blind Fire do Prázdné Knihy:** Ticker stream poskytuje 0 bajtů o hloubce trhu. Moonshot posílá příkazy naslepo, čímž riskuje buď obří partial fill rejecty, nebo katastrofický skluz při Market exekuci.
- **No Limit Clamp:** `coin_amount` při výstřelu není limitován maximální ztrátou ze zborcení (Slippage max_bps), což při thin orderbookech vede k devastujícím fillům na spodku crash wicks.

### 2. ZOMBIE CODE PURGE (Zero-Tolerance):
- Odstraněn zakomentovaný kód: 
  ```rust
  // if storm_byte == 1 {
  //     return;
  // }
  ```
  Zbytečný šum uvnitř ultra-hot WS parse loopu. Bude popraveno.

### 3. LAYER-SPECIFIC OPTIMIZATION:
- **Zero-Allocation Slippage Matrix:** Před samotnou stavbou `write_standalone_ioc` stringu pro Bitfinex provedeme rychlý průchod stackovým O-book polem. Budeme saturovat USD poptávku napříč hladinami po zlomcích sekundy, počítat přesnou "Weighted Average Price" (v `FixedPrice` abstrakci) a jakmile narazíme do hranice (např. -30 bps slippage max), exekuci zařízneme na objemu, který se do limitu vešel. Výpočet stojí řádově 20-50 CPU cyklů.

### 4. SURGICAL CODE REFACTOR:
Upozornění pro velení: Abych mohl provést chirurgický řez pro Slippage Matrix v Moonshotu, potřebuji **přístup k modulu `hydra/src/book.rs`**, který obsahuje L0 Zero-Copy Orderbook parser, abych jej mohl zduplikovat (nebo sdílet přes `sniper_types`) do vrstvy Moonshotu místo dosavadního hloupého Ticker-parseru.

Prosím velitele o přeložení/přístup ke kódu `hydra/src/book.rs` pro zahájení asimilace letálních orderbook funkcí.
