import numpy as np
import itertools
import time
import logging
import struct
import os

PARAMS_PATH = '/dev/shm/beroun/oracle_params.bin'

def inject_winning_parameters(b_size, z_thresh, vpin_thresh):
    """Atomicky zapíše vítězné parametry do sdílené paměti pro živé Orákulum."""
    log.info("Provádím Kognitivní Injekci do živého Orákula...")
    os.makedirs(os.path.dirname(PARAMS_PATH), exist_ok=True)
    
    with open(PARAMS_PATH, 'wb') as f:
        f.write(struct.pack('<ddd', float(b_size), float(z_thresh), float(vpin_thresh)))
        
    log.info("🟢 PARAMETRY ÚSPĚŠNĚ APLIKOVÁNY! Orákulum nyní používá nová data.")

logging.basicConfig(level=logging.INFO, format='%(asctime)s [meta_tuner] 🧬 %(message)s')
log = logging.getLogger("meta_tuner")

def generate_synthetic_day(ticks=100000):
    """Vygeneruje syntetický den s 3 'Toxickými bouřemi' pro trénink AI."""
    log.info(f"Generování syntetického tick-level datasetu ({ticks} kroků)...")
    prices = np.zeros(ticks)
    volumes = np.abs(np.random.normal(0.1, 0.5, ticks)) # Běžný objem
    spreads = np.abs(np.random.normal(5.0, 1.0, ticks))
    
    current_price = 68000.0
    for i in range(ticks):
        # Injekce umělých bouří na pozicích 20k, 50k a 80k
        if i in [20000, 50000, 80000]:
            volumes[i:i+50] *= 20.0 # Obrovský objemový spike
            spreads[i:i+50] += 25.0 # Spread se trhá
            current_price -= 500.0  # Bleskový propad ceny
            
        prices[i] = current_price
        current_price += np.random.normal(0, 1.5) # Náhodný šum ceny
        
    return prices, volumes, spreads

def run_simulation(prices, volumes, spreads, bucket_size, z_threshold, vpin_threshold):
    """Izolovaná kopie mozku z ml_shield.py pro rychlý backtest."""
    ticks = len(prices)
    storm_signals = np.zeros(ticks)
    
    # VB-VPIN State
    window_size = 50
    buckets = []
    current_vol = 0.0
    current_imbalance = 0.0
    
    # EMA Z-Score State
    ema_val = spreads[0]
    ema_var = 1.0
    alpha = 0.01
    
    for i in range(1, ticks):
        # 1. Update VB-VPIN
        current_vol += volumes[i]
        # Aproximace imbalance z pohybu ceny (když padá, převažují prodeje)
        delta_p = prices[i] - prices[i-1]
        imbalance_direction = 1 if delta_p > 0 else -1
        current_imbalance += volumes[i] * imbalance_direction
        
        if current_vol >= bucket_size:
            buckets.append(abs(current_imbalance))
            if len(buckets) > window_size:
                buckets.pop(0)
            current_vol = 0.0
            current_imbalance = 0.0
            
        vpin = sum(buckets) / (len(buckets) * bucket_size) if len(buckets) > 0 else 0.5
        
        # 2. Update Z-Score
        delta = spreads[i] - ema_val
        ema_val += alpha * delta
        ema_var = (1 - alpha) * ema_var + alpha * (delta ** 2)
        z_score = abs(spreads[i] - ema_val) / max(np.sqrt(ema_var), 0.5)
        
        # 3. Detekce Storm
        if z_score > z_threshold and vpin > vpin_threshold:
            storm_signals[i] = 1
            
    return storm_signals

def evaluate_fitness(storm_signals, prices, forward_window=100, min_price_move=50.0):
    """Hodnotící funkce: Našlo Orákulum bouři včas?"""
    true_positives = 0
    false_positives = 0
    
    storm_indices = np.where(storm_signals == 1)[0]
    # Seskupení po sobě jdoucích signálů do jedné události
    events = []
    for idx in storm_indices:
        if not events or idx > events[-1] + forward_window:
            events.append(idx)
            
    for idx in events:
        if idx + forward_window >= len(prices):
            continue
            
        # Koukáme do budoucnosti, jestli cena opravdu ustřelila
        price_at_signal = prices[idx]
        future_prices = prices[idx : idx + forward_window]
        max_deviation = np.max(np.abs(future_prices - price_at_signal))
        
        if max_deviation >= min_price_move:
            true_positives += 1
        else:
            false_positives += 1
            
    # Fitness vzorec: Každý správný zásah +10 bodů, falešný poplach -2 body
    fitness = (true_positives * 10) - (false_positives * 2)
    return fitness, true_positives, false_positives

def optimize_hyperparameters():
    prices, volumes, spreads = generate_synthetic_day()
    
    log.info("Zahajuji Meta-Evoluci (Grid Search)...")
    # AI si vygeneruje testovací matici
    bucket_sizes = [1.0, 2.0, 5.0, 10.0]
    z_thresholds = [2.5, 3.5, 4.5, 5.5]
    vpin_thresholds = [0.6, 0.75, 0.9]
    
    best_fitness = -9999
    best_params = None
    best_stats = None
    
    combinations = list(itertools.product(bucket_sizes, z_thresholds, vpin_thresholds))
    log.info(f"Testuji {len(combinations)} kognitivních mutací...")
    
    start_time = time.time()
    
    for b_size, z_thresh, vpin_thresh in combinations:
        signals = run_simulation(prices, volumes, spreads, b_size, z_thresh, vpin_thresh)
        fitness, tp, fp = evaluate_fitness(signals, prices)
        
        if fitness > best_fitness:
            best_fitness = fitness
            best_params = (b_size, z_thresh, vpin_thresh)
            best_stats = (tp, fp)
            
    elapsed = time.time() - start_time
    log.info(f"Evoluce dokončena za {elapsed:.2f}s!")
    log.info("🏆 VÍTĚZNÉ PARAMETRY PRO DNEŠNÍ TRH:")
    log.info(f"   Bucket Size: {best_params[0]} BTC")
    log.info(f"   Z-Score Threshold: {best_params[1]}")
    log.info(f"   VPIN Limit: {best_params[2]}")
    log.info(f"   Výkon: {best_stats[0]} Zásahů, {best_stats[1]} Falešných poplachů (Fitness: {best_fitness})")
    
    if best_params:
        inject_winning_parameters(best_params[0], best_params[1], best_params[2])

if __name__ == "__main__":
    optimize_hyperparameters()
