#!/usr/bin/env python3
"""BEROUN klines ingest — historické 1m OHLCV z veřejného Binance endpointu.

GET /api/v3/klines je PUBLIC (bez API klíče) — žádné tajemství se netýká.
Ukládá do market_klines (append-only, ON CONFLICT DO NOTHING = idempotentní
opakované spuštění bez duplicit).

Usage:
  python3 fetch_klines.py [symbol] [days]     # default BTCUSDT 30
"""
import json
import subprocess
import sys
import time
import urllib.request

BASE = "https://api.binance.com"
LIMIT_PER_REQ = 1000  # Binance max
LOG = "/var/log/beroun/ingest.log"

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_klines (
    open_time   TIMESTAMPTZ PRIMARY KEY,
    symbol      TEXT NOT NULL,
    open        NUMERIC NOT NULL,
    high        NUMERIC NOT NULL,
    low         NUMERIC NOT NULL,
    close       NUMERIC NOT NULL,
    volume      NUMERIC NOT NULL,
    close_time  TIMESTAMPTZ NOT NULL,
    src         TEXT NOT NULL
);
"""


def psql(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql error: {r.stderr.strip()}")
    return r.stdout


def fetch_klines(symbol, start_ms, end_ms):
    url = (f"{BASE}/api/v3/klines?symbol={symbol}&interval=1m"
           f"&startTime={start_ms}&endTime={end_ms}&limit={LIMIT_PER_REQ}")
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400_000

    # Schema is managed separately; never invent a conflicting primary key.
    total = 0
    cursor = start_ms
    while cursor < now_ms:
        batch = fetch_klines(symbol, cursor, now_ms)
        if not batch:
            break
        for k in batch:
            # Binance kline: [openTime, open, high, low, close, volume, closeTime, ...]
            vals = (f"(to_timestamp({k[0]}/1000.0), "
                    f"'{symbol}', {k[1]}, {k[2]}, {k[3]}, {k[4]}, {k[5]}, "
                    f"to_timestamp({k[6]}/1000.0), "
                    f"'binance_klines_public')")
            psql(f"INSERT INTO market_klines (open_time, symbol, open, high, low, "
                 f"close, volume, close_time, src) VALUES {vals} "
                 f"ON CONFLICT (symbol, src, open_time) DO NOTHING")
        total += len(batch)
        cursor = batch[-1][0] + 60_000
        if len(batch) < LIMIT_PER_REQ:
            break
        time.sleep(0.3)  # zdvořilost k rate limitu

    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} klines {symbol}: "
                f"fetched={total} days={days}\n")
    print(f"klines {symbol}: fetched={total}")


if __name__ == "__main__":
    main()
