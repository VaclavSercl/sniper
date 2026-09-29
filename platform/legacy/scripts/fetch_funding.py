#!/usr/bin/env python3
"""BEROUN funding-rate ingest — T7 rodina.

GET /fapi/v1/fundingRate je PUBLIC (bez klíče). Ukládá do market_funding
(append-only, ON CONFLICT DO NOTHING). Funding BTCUSDT every 8h → ~1095 ročně.

Usage: python3 fetch_funding.py [symbol] [days]
"""
import json
import subprocess
import sys
import time
import urllib.request

BASE = "https://fapi.binance.com"
LIMIT = 1000
LOG = "/var/log/beroun/ingest.log"

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_funding (
    funding_time TIMESTAMPTZ PRIMARY KEY,
    symbol       TEXT NOT NULL,
    rate         NUMERIC NOT NULL,
    mark_price   NUMERIC,
    src          TEXT NOT NULL
);
"""


def psql(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout


def fetch(symbol, start_ms, end_ms):
    url = (f"{BASE}/fapi/v1/fundingRate?symbol={symbol}"
           f"&startTime={start_ms}&endTime={end_ms}&limit={LIMIT}")
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 365
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400_000

    psql(SCHEMA)
    total = 0
    cursor = start_ms
    while cursor < now_ms:
        batch = fetch(symbol, cursor, now_ms)
        if not batch:
            break
        for f in batch:
            vals = (f"(to_timestamp({f['fundingTime']}/1000.0), '{symbol}', "
                    f"{f['fundingRate']}, "
                    f"{f.get('markPrice') or 'NULL'}, 'binance_funding_public')")
            psql(f"INSERT INTO market_funding (funding_time, symbol, rate, "
                 f"mark_price, src) VALUES {vals} ON CONFLICT (funding_time) DO NOTHING")
        total += len(batch)
        cursor = batch[-1]["fundingTime"] + 1
        if len(batch) < LIMIT:
            break
        time.sleep(0.3)

    with open(LOG, "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} funding {symbol}: "
                 f"fetched={total} days={days}\n")
    print(f"funding {symbol}: fetched={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
