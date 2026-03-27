#!/usr/bin/env python3
"""📐 Grid L2 Oracle — AI Grid Parameter Tuning
Sniper Armada · Bot #3 · Strategic Layer

Every 30 minutes:
1. Fetches BTC market data (ATR, VWAP, RSI proxy)
2. Calculates optimal grid spacing (ATR/2)
3. Adjusts number of levels based on volatility
4. Writes parameters to mmap (grid_risk.bin)
"""

import os, time, struct, mmap, json, subprocess, requests
from pathlib import Path

PRICE_SCALE = 100_000_000.0
GRID_RISK_PATH = "/dev/shm/beroun/grid_risk.bin"
CYCLE_SECONDS = 30 * 60  # 30 minutes

# Risk state offsets
OFF_SYMBOL = 0; OFF_SPACING = 8; OFF_NUM_BUY = 16; OFF_NUM_SELL = 20
OFF_QTY = 24; OFF_MODE = 32; OFF_GEO_PCT = 40; OFF_TP_MULT = 48
OFF_PAUSED = 56; OFF_DLL = 64; OFF_MAX_LOSSES = 72; OFF_DYN = 76
OFF_ATR_PERIOD = 80; OFF_CENTER = 88; OFF_VOL_KILL = 96; OFF_HEARTBEAT = 104

def open_mmap(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT); os.ftruncate(fd, size)
    return mmap.mmap(fd, size)

def write_u64(mm, off, v): struct.pack_into('<Q', mm, off, int(v))
def write_u32(mm, off, v): struct.pack_into('<I', mm, off, int(v))
def read_u64(mm, off): return struct.unpack_from('<Q', mm, off)[0]

def calculate_atr(candles):
    """Calculate Average True Range from OHLC candles."""
    if len(candles) < 2: return 1200.0
    trs = []
    for i in range(1, len(candles)):
        high, low, close_prev = candles[i][3], candles[i][4], candles[i-1][2]
        tr = max(high - low, abs(high - close_prev), abs(low - close_prev))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 1200.0

def main():
    print("═══════════════════════════════════════════")
    print("📐 GRID L2 ORACLE — AI Grid Parameter Tuner")
    print("═══════════════════════════════════════════")

    risk_mm = open_mmap(GRID_RISK_PATH, 128)  # Simplified size

    while True:
        print(f"\n⏳ [{time.strftime('%H:%M:%S')}] Grid L2 cycle...")
        try:
            # Fetch 1h candles for ATR
            resp = requests.get("https://api-pub.bitfinex.com/v2/candles/trade:1h:tBTCUSD/hist?limit=20", timeout=15)
            candles = resp.json()

            if isinstance(candles, list) and len(candles) > 2:
                atr = calculate_atr(candles)
                optimal_spacing = max(atr / 2, 200)  # ATR/2, min $200

                # Adaptive levels: more levels when low volatility
                if atr > 3000:
                    num_levels = 3  # Wide spacing, fewer levels
                elif atr > 1500:
                    num_levels = 5  # Standard
                else:
                    num_levels = 7  # Tight grid, more levels

                write_u64(risk_mm, OFF_SPACING, int(optimal_spacing * PRICE_SCALE))
                write_u32(risk_mm, OFF_NUM_BUY, num_levels)
                write_u32(risk_mm, OFF_NUM_SELL, num_levels)
                write_u64(risk_mm, OFF_HEARTBEAT, int(time.time() * 1000))
                risk_mm.flush()

                print(f"✅ ATR: ${atr:.0f} → Spacing: ${optimal_spacing:.0f} | Levels: {num_levels}×{num_levels}")
        except Exception as e:
            print(f"❌ Error: {e}")

        time.sleep(CYCLE_SECONDS)

if __name__ == "__main__": main()
