#!/usr/bin/env python3
"""BEROUN funding-rate ingest — T7 rodina.

GET /fapi/v1/fundingRate je PUBLIC (bez klíče). Ukládá do market_funding
(append-only, ON CONFLICT DO NOTHING). Funding BTCUSDT every 8h → ~1095 ročně.

Usage: python3 fetch_funding.py [symbol] [days]
"""
import json
import math
import re
import subprocess
import sys
import time
import urllib.request

BASE = "https://fapi.binance.com"
LIMIT = 1000
LOG = "/var/log/beroun/ingest.log"

def psql(sql):
    r = subprocess.run(["psql", "-X", "-w", "-v", "ON_ERROR_STOP=1", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise RuntimeError("Database write failed")
    return r.stdout


def fetch(symbol, start_ms, end_ms):
    url = (f"{BASE}/fapi/v1/fundingRate?symbol={symbol}"
           f"&startTime={start_ms}&endTime={end_ms}&limit={LIMIT}")
    with urllib.request.urlopen(url, timeout=15) as r:
        raw = r.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Funding response too large")
        return json.loads(raw.decode())


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 365
    if not re.fullmatch(r"[A-Z0-9]{2,24}", symbol) or not 1 <= days <= 3650:
        raise ValueError("Invalid symbol or history window")
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400_000

    # Schema is a separately reviewed migration, never an ingestion side effect.
    total = 0
    cursor = start_ms
    while cursor < now_ms:
        batch = fetch(symbol, cursor, now_ms)
        if not isinstance(batch, list):
            raise ValueError("Invalid funding response")
        if not batch:
            break
        previous = cursor - 1
        for f in batch:
            stamp = f['fundingTime']
            if type(stamp) is not int or not previous < stamp <= now_ms or stamp < cursor or f.get('symbol') != symbol:
                raise ValueError("Invalid funding identity or chronology")
            if isinstance(f['fundingRate'], bool) or isinstance(f.get('markPrice'), bool):
                raise ValueError("Boolean funding value")
            rate = float(f['fundingRate'])
            mark = float(f['markPrice']) if f.get('markPrice') not in (None, '') else None
            if not math.isfinite(rate) or (mark is not None and (not math.isfinite(mark) or mark <= 0)):
                raise ValueError("Invalid funding value")
            previous = stamp
            vals = (f"(to_timestamp({stamp}/1000.0), '{symbol}', "
                    f"{rate}, {mark if mark is not None else 'NULL'}, 'binance_funding_public')")
            psql(f"INSERT INTO market_funding (funding_time, symbol, rate, "
                 f"mark_price, src) VALUES {vals} ON CONFLICT (symbol, src, funding_time) DO NOTHING")
        total += len(batch)
        cursor = batch[-1]["fundingTime"] + 1
        if len(batch) < LIMIT:
            break
        time.sleep(0.3)

    if not total:
        raise ValueError("No funding observations in requested window")
    with open(LOG, "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} funding {symbol}: "
                 f"fetched={total} days={days}\n")
    print(f"funding {symbol}: fetched={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
