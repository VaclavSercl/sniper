#!/usr/bin/env python3
"""BEROUN ingest market data — L0 shadow pipeline (P008).

Oneshot skript pro timer: stáhne přes gateway (read-only) aktuální ticky
a zapíše do PostgreSQL. Bez psycopg2 — používá psql subprocess (stejný
vzor jako outbox_flush.py). Nikdy nic neposílá na burzu.

DB schéma (idempotentní):
  market_ticks(ts TIMESTAMPTZ, symbol TEXT, price NUMERIC, src TEXT)
    — bez PRIMARY KEY na ts: ticky přicházejí rychleji než 1/s, now()
      může kolidovat; duplicita se řeší deduplikací při čtení.
"""
import json
import os
import socket
import subprocess
import time

SOCK = os.environ.get("BEROUN_GATEWAY_SOCK", "/run/beroun-gateway/gateway.sock")
SYMBOLS = [s.strip() for s in os.environ.get("BEROUN_SYMBOLS", "BTCUSDT").split(",")]
LOG = "/var/log/beroun/ingest.log"

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_ticks (
    ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol  TEXT NOT NULL,
    price   NUMERIC NOT NULL,
    src     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_ticks_symbol_ts
    ON market_ticks (symbol, ts);
"""


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}\n")


def psql(sql):
    r = subprocess.run(
        ["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql error: {r.stderr.strip()}")
    return r.stdout


def gw(msg):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(json.dumps(msg).encode())
    r = json.loads(s.recv(65536).decode())
    s.close()
    return r


def main():
    psql(SCHEMA)
    written = 0
    errors = []
    for sym in SYMBOLS:
        try:
            t = gw({"type": "ticker", "symbol": sym})
            price = t.get("price")
            if price:
                # single-row insert per symbol; psql params via quote
                safe = str(price).replace("'", "''")
                psql(f"INSERT INTO market_ticks (symbol, price, src) "
                     f"VALUES ('{sym}', {safe}, 'binance_ticker')")
                written += 1
            else:
                errors.append(f"{sym}: no price in response")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{sym}: {e}")

    if errors:
        log(f"ingest partial: written={written} errors={errors}")
        print(f"ingest partial: written={written} errors={errors}")
        return 1
    log(f"ingest ok: ticks={written}")
    print(f"ingest ok: ticks={written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
