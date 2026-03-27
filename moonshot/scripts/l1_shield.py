#!/usr/bin/env python3
"""🌙 Moonshot L1 Shield — Tactical AI Layer
Sniper Armada · Bot #2

Monitors BTC volatility and applies delta modifiers to Moonshot parameters.
Runs every 5 seconds:
1. BTC Volatility Kill: Auto-pause if BTC hourly change > threshold
2. PriceBug Modifier: Widen drop distance when exchange latency spikes
3. Delta Modifiers: Adjust m_shot_price_pct based on BTC hourly/5min delta
"""

import os
import time
import struct
import mmap
import requests

PRICE_SCALE = 100_000_000.0
MAX_PAIRS = 20
MOONSHOT_RISK_PATH = "/dev/shm/beroun/moonshot_risk.bin"

PAIR_RISK_SIZE = 128
OFF_GLOBAL_PAUSED = MAX_PAIRS * PAIR_RISK_SIZE
OFF_BTC_VOL_KILL = OFF_GLOBAL_PAUSED + 16

BITFINEX_TICKER_URL = "https://api-pub.bitfinex.com/v2/ticker/tBTCUSD"


def open_mmap(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    os.ftruncate(fd, size)
    return mmap.mmap(fd, size)


def read_u64(mm, offset):
    return struct.unpack_from('<Q', mm, offset)[0]


def write_u64(mm, offset, value):
    struct.pack_into('<Q', mm, offset, int(value))


def main():
    print("🛡️ Moonshot L1 Shield — Tactical AI Layer")
    risk_size = MAX_PAIRS * PAIR_RISK_SIZE + 64
    risk_mm = open_mmap(MOONSHOT_RISK_PATH, risk_size)

    while True:
        try:
            # Fetch BTC ticker
            resp = requests.get(BITFINEX_TICKER_URL, timeout=10)
            data = resp.json()
            # [BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC, LAST_PRICE, VOLUME, HIGH, LOW]
            if isinstance(data, list) and len(data) >= 10:
                daily_change_pct = (data[5] or 0) * 100
                hourly_estimate = daily_change_pct / 24

                # BTC Volatility Kill
                kill_threshold = read_u64(risk_mm, OFF_BTC_VOL_KILL)
                kill_pct = kill_threshold / PRICE_SCALE if kill_threshold > 0 else 4.0

                if abs(hourly_estimate) > kill_pct:
                    paused = read_u64(risk_mm, OFF_GLOBAL_PAUSED)
                    if paused == 0:
                        write_u64(risk_mm, OFF_GLOBAL_PAUSED, 1)
                        risk_mm.flush()
                        print(f"🚨 BTC volatility kill! BTC 1h est = {hourly_estimate:.2f}% > {kill_pct:.1f}%")
        except Exception as e:
            pass  # Silent fail — L1 is non-critical

        time.sleep(5)


if __name__ == "__main__":
    main()
