#!/usr/bin/env python3
"""
🌙 Moonshot Pair Initializer — Seeds risk mmap with optimal flash crash pairs.
Called by L2 Oracle (or manually for bootstrap).

Writes to: /dev/shm/beroun/moonshot_risk.bin

The L2 Oracle will dynamically rotate pairs based on:
  - 24h volatility (higher = better for flash crash catching)
  - Volume (minimum liquidity threshold)
  - Correlation to BTC (lower correlation = more diverse)
  - Recent wick frequency (pairs that "wick" often are prime targets)
"""

import struct
import mmap
import os
import sys
import time

# Moonshot risk mmap layout (must match moonshot_types.rs)
MOONSHOT_RISK_PATH = "/dev/shm/beroun/moonshot_risk.bin"
MOONSHOT_MAX_PAIRS = 20
PRICE_SCALE = 100_000_000.0  # 1e8

# MoonshotPairRisk: 10 fields × 8 bytes = 80 bytes, aligned to 64 → 128 bytes (2 cache lines)
# Actually let's calculate properly: repr(C, align(64))
# Fields: symbol_hash(8) + m_shot_price_pct(8) + m_shot_price_min_pct(8) + 
#   m_shot_replace_delay_ms(8) + m_shot_raise_wait_ms(8) + tp_pct(8) + sl_pct(8) + 
#   order_usd(8) + max_position(8) + fee_bps(8) = 80 bytes → aligned to 128 (2×64)
PAIR_RISK_SIZE = 128  # 2 cache lines per pair (align(64), 80 bytes data)

# Global fields after pairs array: global_paused(8) + daily_loss_limit(8) + 
#   btc_volatility_kill_pct(8) + ai_heartbeat_ms(8) = 32 bytes
GLOBAL_OFFSET = MOONSHOT_MAX_PAIRS * PAIR_RISK_SIZE

def str_to_symbol_hash(s: str) -> int:
    """Match Rust str_to_symbol_hash: up to 8 ASCII bytes as little-endian u64."""
    b = s.encode('ascii')[:8]
    b = b + b'\x00' * (8 - len(b))
    return int.from_bytes(b, 'little')

def write_pair(mm, idx: int, symbol: str, drop_pct: float, tp_pct: float,
               sl_pct: float, order_usd: float, max_pos: float):
    """Write one pair's risk parameters to mmap."""
    base = idx * PAIR_RISK_SIZE
    struct.pack_into('<Q', mm, base + 0,  str_to_symbol_hash(symbol))
    struct.pack_into('<Q', mm, base + 8,  int(drop_pct * PRICE_SCALE))     # m_shot_price_pct
    struct.pack_into('<Q', mm, base + 16, int(0.5 * PRICE_SCALE))          # m_shot_price_min_pct
    struct.pack_into('<Q', mm, base + 24, 500)                             # replace_delay_ms  
    struct.pack_into('<Q', mm, base + 32, 3000)                            # raise_wait_ms
    struct.pack_into('<Q', mm, base + 40, int(tp_pct * PRICE_SCALE))       # tp_pct
    struct.pack_into('<Q', mm, base + 48, int(sl_pct * PRICE_SCALE))       # sl_pct
    struct.pack_into('<Q', mm, base + 56, int(order_usd * PRICE_SCALE))    # order_usd
    struct.pack_into('<q', mm, base + 64, int(max_pos * PRICE_SCALE))      # max_position
    struct.pack_into('<Q', mm, base + 72, 20)                              # fee_bps (legacy, now from GlobalFeeState)

# ═══════════════════════════════════════════════════════════
# PAIR CONFIGURATION — AI will dynamically adjust these
# Initial selection: high-volume pairs optimal for flash crash catching
# ═══════════════════════════════════════════════════════════

# Tier 1: Majors (highest liquidity, tightest spreads)
# Tier 2: Large caps (good volume, moderate spreads)
# Tier 3: Mid caps (higher volatility = bigger wicks)

INITIAL_PAIRS = [
    # symbol,       drop%,  tp%,   sl%,   order_usd,  max_pos
    # ─── TIER 1: BTC + Major Fiat ───
    ("tBTCUSD",      3.0,   1.5,   5.0,   10.0,       0.001),
    ("tBTCUST",      3.0,   1.5,   5.0,   10.0,       0.001),
    ("tBTCEUR",      3.5,   1.5,   5.0,   10.0,       0.001),
    ("tBTCGBP",      3.5,   2.0,   5.0,   10.0,       0.001),
    # ─── TIER 2: ETH (2nd most liquid) ───
    ("tETHUSD",      4.0,   2.0,   6.0,   10.0,       0.01),
    ("tETHUST",      4.0,   2.0,   6.0,   10.0,       0.01),
    ("tETHBTC",      3.0,   1.5,   5.0,   10.0,       0.01),
    ("tETHEUR",      4.0,   2.0,   6.0,   10.0,       0.01),
    # ─── TIER 3: Top Alts (higher vol = bigger wicks) ───
    ("tSOLUSD",      5.0,   3.0,   8.0,   10.0,       0.1),
    ("tXRPUSD",      5.0,   3.0,   8.0,   10.0,       10.0),
    ("tLTCUSD",      5.0,   3.0,   8.0,   10.0,       0.1),
]

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "scanner"
    
    os.makedirs(os.path.dirname(MOONSHOT_RISK_PATH), exist_ok=True)
    
    # Calculate total file size
    file_size = GLOBAL_OFFSET + 64  # pairs + global fields (padded)
    
    fd = os.open(MOONSHOT_RISK_PATH, os.O_RDWR | os.O_CREAT)
    os.ftruncate(fd, file_size)
    mm = mmap.mmap(fd, file_size)
    os.close(fd)
    
    print(f"🌙 Moonshot Pair Initializer — Mode: {mode}")
    print(f"   mmap: {MOONSHOT_RISK_PATH} ({file_size} bytes)")
    print(f"   Pairs: {len(INITIAL_PAIRS)}/{MOONSHOT_MAX_PAIRS}")
    print()
    
    for i, (sym, drop, tp, sl, usd, maxp) in enumerate(INITIAL_PAIRS):
        if mode == "scanner":
            # Scanner mode: $0 order size → no trades
            write_pair(mm, i, sym, drop, tp, sl, 0.0, maxp)
            print(f"   [{i:2d}] {sym:10s}  drop={drop}%  tp={tp}%  order=$0 (SCANNER)")
        else:
            write_pair(mm, i, sym, drop, tp, sl, usd, maxp)
            print(f"   [{i:2d}] {sym:10s}  drop={drop}%  tp={tp}%  order=${usd}")
    
    # Clear remaining slots
    for i in range(len(INITIAL_PAIRS), MOONSHOT_MAX_PAIRS):
        struct.pack_into('<Q', mm, i * PAIR_RISK_SIZE, 0)  # symbol_hash = 0 → inactive
    
    # Global settings
    if mode == "scanner":
        struct.pack_into('<Q', mm, GLOBAL_OFFSET + 0, 1)      # global_paused = 1 (SCANNER)
    else:
        struct.pack_into('<Q', mm, GLOBAL_OFFSET + 0, 0)      # global_paused = 0 (LIVE)
    
    struct.pack_into('<q', mm, GLOBAL_OFFSET + 8, int(50 * PRICE_SCALE))   # daily_loss_limit = $50
    struct.pack_into('<Q', mm, GLOBAL_OFFSET + 16, int(5.0 * PRICE_SCALE)) # btc_vol_kill = 5%
    struct.pack_into('<Q', mm, GLOBAL_OFFSET + 24, int(time.time() * 1000)) # ai_heartbeat
    
    mm.flush()
    mm.close()
    
    print()
    paused = "SCANNER (paused)" if mode == "scanner" else "LIVE"
    print(f"   Status: {paused}")
    print(f"   ✅ Moonshot risk state initialized — restart moonshot-core to apply")

if __name__ == "__main__":
    main()
