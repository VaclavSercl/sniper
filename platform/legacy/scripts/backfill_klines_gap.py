#!/usr/bin/env python3
"""Robustní backfill 1m klines pro chybějící období (2022-11-06 .. 2025-08-26).
Ukládá průběžně, loguje každých 50 requestů, přežije restart (pokračuje od
posledního cursoru zapsaného do /tmp/backfill_cursor).
"""
import json
import subprocess
import time
import urllib.request

BASE = "https://api.binance.com"
START_MS = 1667683200000   # 2022-11-06 00:00 UTC
END_MS = 1756166400000     # 2025-08-26 00:00 UTC
CURSOR_FILE = "/tmp/backfill_cursor"
LOG = "/var/log/beroun/ingest.log"


def psql(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout


def fetch(start_ms, end_ms):
    url = (f"{BASE}/api/v3/klines?symbol=BTCUSDT&interval=1m"
           f"&startTime={start_ms}&endTime={end_ms}&limit=1000")
    req = urllib.request.Request(url, headers={"User-Agent": "BEROUN-research/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    try:
        cursor = int(open(CURSOR_FILE).read().strip())
    except (OSError, ValueError):
        cursor = START_MS
    total = 0
    req = 0
    errors = 0
    while cursor < END_MS:
        for attempt in range(5):
            try:
                batch = fetch(cursor, END_MS)
                break
            except Exception as e:
                errors += 1
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        if not batch:
            print(f"empty batch at {cursor}, stopping")
            break
        values = []
        for k in batch:
            values.append(
                f"(to_timestamp({k[0]/1000.0}), 'BTCUSDT', {k[1]}, {k[2]}, "
                f"{k[3]}, {k[4]}, {k[5]}, to_timestamp({k[6]/1000.0}), "
                f"'binance_klines_public')")
        # insert po 100 radcich (argument list limit)
        for i in range(0, len(values), 100):
            psql("INSERT INTO market_klines (open_time, symbol, open, high, low, "
                 "close, volume, close_time, src) VALUES " + ",".join(values[i:i+100]) +
                 " ON CONFLICT (open_time) DO NOTHING")
        total += len(batch)
        req += 1
        cursor = batch[-1][0] + 60_000
        open(CURSOR_FILE, "w").write(str(cursor))
        if req % 50 == 0:
            print(f"progress: req={req} bars={total} cursor={cursor} "
                  f"({(cursor-START_MS)/(END_MS-START_MS)*100:.1f}%)", flush=True)
        time.sleep(0.15)
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} backfill gap done: "
                f"bars={total} req={req} errors={errors}\n")
    print(f"DONE bars={total} req={req} errors={errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
