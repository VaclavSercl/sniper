#!/usr/bin/env python3
"""📐 Grid L1 Shield — BTC Volatility Guard
Sniper Armada · Bot #3
Monitors BTC volatility and pauses grid during extreme moves.
"""
import os, time, struct, mmap, requests

PRICE_SCALE = 100_000_000.0
GRID_RISK_PATH = "/dev/shm/beroun/grid_risk.bin"
OFF_PAUSED = 56; OFF_VOL_KILL = 96

def open_mmap(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT); os.ftruncate(fd, size)
    return mmap.mmap(fd, size)
def read_u64(mm, off): return struct.unpack_from('<Q', mm, off)[0]
def write_u64(mm, off, v): struct.pack_into('<Q', mm, off, int(v))

def main():
    print("🛡️ Grid L1 Shield — Volatility Guard")
    risk_mm = open_mmap(GRID_RISK_PATH, 128)
    while True:
        try:
            resp = requests.get("https://api-pub.bitfinex.com/v2/ticker/tBTCUSD", timeout=10)
            data = resp.json()
            if isinstance(data, list) and len(data) >= 10:
                daily_pct = abs((data[5] or 0) * 100)
                hourly_est = daily_pct / 24
                kill_raw = read_u64(risk_mm, OFF_VOL_KILL)
                kill_pct = kill_raw / PRICE_SCALE if kill_raw > 0 else 4.0
                if hourly_est > kill_pct:
                    cur = read_u64(risk_mm, OFF_PAUSED)
                    if cur == 0:
                        write_u64(risk_mm, OFF_PAUSED, 1); risk_mm.flush()
                        print(f"🚨 BTC vol kill! {hourly_est:.2f}% > {kill_pct:.1f}%")
        except: pass
        time.sleep(5)

if __name__ == "__main__": main()
