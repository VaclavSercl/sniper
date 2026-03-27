#!/usr/bin/env python3
"""📐 Grid Telegram C2 — /grid status, /grid kill"""
import os, struct, mmap

PRICE_SCALE_I = 100_000_000; PRICE_SCALE = 100_000_000.0
GRID_ENGINE_PATH = "/dev/shm/beroun/grid_engine.bin"
GRID_RISK_PATH = "/dev/shm/beroun/grid_risk.bin"

def open_mm(path, size):
    if not os.path.exists(path): return None
    fd = os.open(path, os.O_RDWR); return mmap.mmap(fd, size)
def read_u64(mm, o): return struct.unpack_from('<Q', mm, o)[0]
def read_i64(mm, o): return struct.unpack_from('<q', mm, o)[0]

def main():
    print("📐 Grid Telegram C2 — Standalone Test")
    # Simplified test
    e = open_mm(GRID_ENGINE_PATH, 4096)
    r = open_mm(GRID_RISK_PATH, 128)
    if not e or not r: print("⚠️ mmap not found"); return
    # Read globals from engine (after buy/sell level arrays)
    MID_OFF = 20 * 64 * 2  # 20 levels × 64 bytes × 2 sides
    mid = read_u64(e, MID_OFF) / PRICE_SCALE_I
    pnl = read_i64(e, MID_OFF + 24) / PRICE_SCALE_I
    paused = read_u64(r, 56)
    print(f"Mid: ${mid:,.0f} | PnL: ${pnl:.2f} | Kill: {'🔴' if paused else '🟢'}")

if __name__ == "__main__": main()
