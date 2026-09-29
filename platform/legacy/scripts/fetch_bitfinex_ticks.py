#!/usr/bin/env python3
"""BEROUN Bitfinex tick ingest — veřejné trades (tBTCUSD) z REST v2.

GET /v2/trades/tBTCUSD/hist je PUBLIC. Ukládá do bitfinex_ticks (append-only).
Usage: python3 fetch_bitfinex_ticks.py [hours]   # default 72
"""
import json
import subprocess
import sys
import time
import urllib.request

BASE = "https://api-pub.bitfinex.com"
LOG = "/var/log/beroun/ingest.log"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bitfinex_ticks (
    id BIGINT PRIMARY KEY,
    mts TIMESTAMPTZ NOT NULL,
    amount NUMERIC NOT NULL,     -- + buy / - sell (taker side)
    price NUMERIC NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bfx_mts ON bitfinex_ticks (mts);
"""


def psql(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout


def fetch(end_mts, limit=10000):
    url = f"{BASE}/v2/trades/tBTCUSD/hist?limit={limit}&end={end_mts}&sort=-1"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) BEROUN-research/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 72
    psql(SCHEMA)
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - hours * 3600_000
    end_mts = now_ms
    total = 0
    while end_mts > cutoff:
        batch = fetch(end_mts)
        if not batch:
            break
        for t in batch:  # [id, mts, amount, price]
            if t[1] < cutoff:
                continue
            psql(f"INSERT INTO bitfinex_ticks (id, mts, amount, price) "
                 f"VALUES ({t[0]}, to_timestamp({t[1]}/1000.0), {t[2]}, {t[3]}) "
                 f"ON CONFLICT (id) DO NOTHING")
        total += len(batch)
        oldest = min(t[1] for t in batch)
        if oldest >= end_mts or len(batch) < 2:
            break
        end_mts = oldest
        time.sleep(0.5)
    n = psql("SELECT count(*) FROM bitfinex_ticks;").strip()
    print(json.dumps({"fetched": total, "in_db": n}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
