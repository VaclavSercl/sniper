#!/usr/bin/env python3
"""🌙 Moonshot Telegram C2 — Remote Control Interface
Sniper Armada · Bot #2

Commands:
  /moonshot    - Quick status (active pairs, PnL, kill switch)
  /mpairs      - List all active pairs with parameters
  /mkill       - Toggle global kill switch
  /mstatus     - Detailed status with positions
"""

import os
import sys
import struct
import mmap
import time

PRICE_SCALE = 100_000_000.0
PRICE_SCALE_I = 100_000_000
MAX_PAIRS = 20

MOONSHOT_ENGINE_PATH = "/dev/shm/beroun/moonshot_engine.bin"
MOONSHOT_RISK_PATH = "/dev/shm/beroun/moonshot_risk.bin"

PAIR_ENGINE_SIZE = 128
PAIR_RISK_SIZE = 128

# Engine offsets
OFF_E_BID = 0
OFF_E_ASK = 8
OFF_E_LAST = 16
OFF_E_LATENCY = 24
OFF_E_NET_POS = 32
OFF_E_PNL = 40
OFF_E_FILLS = 64
OFF_E_ACTIVE = 56  # AtomicU32

# Risk offsets
OFF_R_SYMBOL = 0
OFF_R_DROP = 8
OFF_R_TP = 40
OFF_R_SL = 48
OFF_R_ORDER = 56

OFF_GLOBAL_PAUSED = MAX_PAIRS * PAIR_RISK_SIZE

# Global engine offsets (after pairs)
OFF_E_GLOBAL_PNL = MAX_PAIRS * PAIR_ENGINE_SIZE + 24


def open_mmap_read(path, size):
    if not os.path.exists(path):
        return None
    fd = os.open(path, os.O_RDWR)
    return mmap.mmap(fd, size)


def read_u64(mm, off):
    return struct.unpack_from('<Q', mm, off)[0]

def read_i64(mm, off):
    return struct.unpack_from('<q', mm, off)[0]

def read_u32(mm, off):
    return struct.unpack_from('<I', mm, off)[0]

def write_u64(mm, off, val):
    struct.pack_into('<Q', mm, off, int(val))


def symbol_hash_to_str(h):
    b = struct.pack('<Q', h)
    return b.split(b'\x00')[0].decode('ascii', errors='replace')


def main():
    """Standalone test — in production this runs inside telebot framework."""
    print("🌙 Moonshot Telegram C2 — Standalone Test")

    engine_size = MAX_PAIRS * PAIR_ENGINE_SIZE + 64
    risk_size = MAX_PAIRS * PAIR_RISK_SIZE + 64

    e_mm = open_mmap_read(MOONSHOT_ENGINE_PATH, engine_size)
    r_mm = open_mmap_read(MOONSHOT_RISK_PATH, risk_size)

    if not e_mm or not r_mm:
        print("⚠️ mmap files not found — moonshot not running")
        return

    paused = read_u64(r_mm, OFF_GLOBAL_PAUSED)
    daily_pnl = read_i64(e_mm, OFF_E_GLOBAL_PNL) / PRICE_SCALE_I

    print(f"Kill Switch: {'🔴 ACTIVE' if paused else '🟢 OFF'}")
    print(f"Daily PnL: ${daily_pnl:.2f}")
    print()

    active = 0
    for i in range(MAX_PAIRS):
        sym_hash = read_u64(r_mm, i * PAIR_RISK_SIZE + OFF_R_SYMBOL)
        if sym_hash == 0:
            continue

        symbol = symbol_hash_to_str(sym_hash)
        drop = read_u64(r_mm, i * PAIR_RISK_SIZE + OFF_R_DROP) / PRICE_SCALE
        tp = read_u64(r_mm, i * PAIR_RISK_SIZE + OFF_R_TP) / PRICE_SCALE
        order = read_u64(r_mm, i * PAIR_RISK_SIZE + OFF_R_ORDER) / PRICE_SCALE
        pos = read_i64(e_mm, i * PAIR_ENGINE_SIZE + OFF_E_NET_POS) / PRICE_SCALE_I
        pnl = read_i64(e_mm, i * PAIR_ENGINE_SIZE + OFF_E_PNL) / PRICE_SCALE_I
        fills = read_u64(e_mm, i * PAIR_ENGINE_SIZE + OFF_E_FILLS)

        active += 1
        print(f"  [{i:2d}] {symbol:8s} | ${order:6.0f} | Drop: {drop:5.2f}% | TP: {tp:4.2f}% | Pos: {pos:+.4f} | PnL: ${pnl:+.2f} | Fills: {fills}")

    print(f"\nActive pairs: {active}/{MAX_PAIRS}")


if __name__ == "__main__":
    main()
