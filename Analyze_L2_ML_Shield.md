# Analyze_L2_ML_Shield.md

## 1. Bug Hunting & Security
- **Memory Corruption Threat (Hardcoded Mmap Offsets)**: Modul `ml_shield.py` naivně hardkóduje pozice paměti do Rust structu (`OFF_L1_SKEW_CALC = 1584`). Pokud se v Rust `types.rs` změní zarovnání paměti (`#[repr(C, align(64))]`) nebo se přidá jediné `bool` pole, Python script zapíše float predikce do nesprávného místa paměti L0 exekučního jádra, čímž ho může permanentně zkorumpovat a vyvolat nekryté tržní objednávky. Offsety musí být sdíleny dynamicky přes vygenerovaný bind soubor (`l2_rust_offsets.py`).
- **Memory Leak a Alokace v Hot-loopu**: Ve funkci `FeatureExtractor.extract` se volá konstrukce `np.array(list(self.obi_history))` z deque. Převod cyklické fronty (deque) na list a následně na NumPy array vytvoří v Pythonu každých 50 milisekund novou alokaci. Garbage Collector se brzy zahltí.

## 2. Odstranění Zombie kódů
Tento skript jasně uvádí: `Model: Online-learning linear model + EMA ensemble (no GPU needed)`. Přesto obsahuje funkci `get_gpu_temp()`, která každých 30 vteřin zavolá synchronní `subprocess.run(['nvidia-smi', ...])`, zablokuje smyčku výpočtu a na základě **teploty GPU** vyřadí CPU NumPy model z provozu (`throttled = True`). 
Toto je kritický zombie kód z minulé éry vývoje (kdy L1 běžel na GPU), který musí být nemilosrdně smazán, jelikož throttling CPU analytiky kvůli GPU teplotě nedává smysl a ohrožuje běh systému.

## 3. HFT Optimalizace podle vrstvy (L2 Python)
- **Ring Buffer místo Deque**: Pro historii OBI, mid-price a spreadů musí FeatureExtractor používat pre-alokovaný `numpy.ndarray` s pointerem (modulo indexováním).
- **Zrušení GIL blokování pro SMI**: Odstranění synchronního spouštění `nvidia-smi`.
- **In-place operace numpy**: Namísto `diff = features - self.running_mean` (což alokuje nové pole), použijte `np.subtract` s `out=` argumentem, čímž plně využijete cache procesoru bez GC spikeů.

## 4. Refaktorovaný kód (Návrh pro L2 / ML Shield)
```python
import numpy as np
# 1. Optimalizace: Použití vygenerovaných offsetů z Rustu
from l2_rust_offsets import OFF_L1_SKEW, OFF_L1_CONF

class FeatureExtractor:
    def __init__(self, window_size=200):
        # 2. Optimalizace: Zero-allocation Ring Buffers
        self.window_size = window_size
        self.obi_history = np.zeros(window_size, dtype=np.float64)
        self.mid_history = np.zeros(window_size, dtype=np.float64)
        self.ptr = 0
        
    def extract(self, mm):
        # ... extrakce ...
        self.obi_history[self.ptr] = obi
        self.mid_history[self.ptr] = mid
        
        # O(1) in-place ema nebo simple moving averages přes views
        
        self.ptr = (self.ptr + 1) % self.window_size

def run_inference():
    # ...
    # 3. Odstranění zombie throttlingu -> Nvidia-SMI kompletně smazáno!
    while True:
        cycle_start = time.monotonic()
        features = extractor.extract(mm)
        if features is not None:
             # Fast numpy-native prediction
             prediction = model.predict(features)
             # Přímý zápis na ověřené pointery
             struct.pack_into('<q', mm, OFF_L1_SKEW, int(prediction * 100_000_000))
        # ...
```
