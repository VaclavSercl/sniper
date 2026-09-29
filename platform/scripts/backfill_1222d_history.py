#!/usr/bin/env python3
"""
BEROUN 1222-Day Historical Backfill Engine (§9b, §10)
Downloads and backfills continuous 1-minute OHLCV candles and funding rates
from public exchange APIs up to 1222 days into the past.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [backfill_1222d] %(message)s"
)
logger = logging.getLogger("backfill_1222d")

RETENTION_DAYS = 1222
BINANCE_API_LIMIT = 1000


def psql(sql: str) -> str:
    cmd = ["sudo", "-u", "beroun", "psql", "-d", "beroun", "-t", "-A", "-c", sql]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error("PSQL failed: %s", res.stderr.strip())
        raise RuntimeError(res.stderr.strip())
    return res.stdout.strip()


def backfill_binance_funding(symbols: List[str], days: int = RETENTION_DAYS):
    """Backfills 8h funding rates for Binance Perpetual Futures to 'days' into the past."""
    logger.info("=== Backfilling Binance Futures Funding Rates (%d days) ===", days)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400_000

    for sym in symbols:
        cursor = start_ms
        total = 0
        while cursor < now_ms:
            url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={cursor}&limit={BINANCE_API_LIMIT}"
            req = urllib.request.Request(url, headers={"User-Agent": "BEROUN/2.5-Backfill"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            if not data:
                break

            val_rows = []
            for f in data:
                mp = str(float(f["markPrice"])) if f.get("markPrice") else "NULL"
                val_rows.append(
                    f"(to_timestamp({f['fundingTime']}/1000.0), '{sym}', 'binance_funding_public', {f['fundingRate']}, {mp})"
                )

            sql = f"""
            INSERT INTO market_funding (funding_time, symbol, src, rate, mark_price)
            VALUES {','.join(val_rows)}
            ON CONFLICT (symbol, src, funding_time) DO UPDATE SET rate = EXCLUDED.rate;
            """
            psql(sql)
            total += len(data)
            cursor = data[-1]["fundingTime"] + 1
            time.sleep(0.08)

        logger.info("Successfully backfilled %d funding records for %s", total, sym)


def backfill_binance_klines(symbol: str, days: int = RETENTION_DAYS, delay: float = 0.15):
    """Backfills 1-minute OHLCV candles from Binance public API up to 'days' in past."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400_000
    total_expected_minutes = days * 1440

    logger.info("=== Starting Binance 1m Kline Backfill for %s (%d days = ~%d bars) ===",
                symbol, days, total_expected_minutes)

    cursor = start_ms
    saved_total = 0
    t0 = time.time()

    while cursor < now_ms:
        end_batch = min(cursor + BINANCE_API_LIMIT * 60_000, now_ms)
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m"
               f"&startTime={cursor}&endTime={end_batch}&limit={BINANCE_API_LIMIT}")

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "BEROUN/2.5-Backfill"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_bars = json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("API error on batch cursor %d: %s. Retrying in 2s...", cursor, e)
            time.sleep(2.0)
            continue

        if not raw_bars:
            cursor = end_batch + 60_000
            continue

        val_rows = []
        for r in raw_bars:
            o_ts = datetime.fromtimestamp(r[0] / 1000.0, tz=timezone.utc).isoformat()
            c_ts = datetime.fromtimestamp(r[6] / 1000.0, tz=timezone.utc).isoformat()
            val_rows.append(
                f"('{o_ts}', '{symbol}', 'binance_klines', {r[1]}, {r[2]}, {r[3]}, {r[4]}, "
                f"{r[5]}, {r[7]}, {r[8]}, '{c_ts}')"
            )

        sql = f"""
        INSERT INTO market_klines (
            open_time, symbol, src, open, high, low, close, volume, quote_volume, trades_count, close_time
        ) VALUES
        {','.join(val_rows)}
        ON CONFLICT (symbol, src, open_time) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
            volume = EXCLUDED.volume, quote_volume = EXCLUDED.quote_volume, trades_count = EXCLUDED.trades_count;
        """
        psql(sql)
        saved_total += len(raw_bars)

        # Progress reporting
        pct = (cursor - start_ms) / (now_ms - start_ms) * 100.0
        elapsed = time.time() - t0
        speed = saved_total / max(1.0, elapsed)
        eta_sec = (total_expected_minutes - saved_total) / max(1.0, speed)

        if saved_total % 10000 < BINANCE_API_LIMIT:
            logger.info("Progress [%s]: %.1f%% | Saved %d / ~%d bars | Speed: %.0f bars/s | ETA: %.1f min",
                        symbol, pct, saved_total, total_expected_minutes, speed, eta_sec / 60.0)

        cursor = raw_bars[-1][0] + 60_000
        time.sleep(delay)

    logger.info("Backfill complete for %s: %d bars in %.1f minutes", symbol, saved_total, (time.time() - t0) / 60.0)


def main():
    parser = argparse.ArgumentParser(description="BEROUN 1222-Day Backfill Engine")
    parser.add_argument("--symbol", default="ETHUSDT", help="Symbol to backfill (e.g. ETHUSDT, SOLUSDT, EURUSDT)")
    parser.add_argument("--days", type=int, default=RETENTION_DAYS, help="Number of days to backfill (default 1222)")
    parser.add_argument("--funding-only", action="store_true", help="Only backfill funding rates")
    args = parser.parse_args()

    if args.funding_only:
        backfill_binance_funding(["BTCUSDT", "ETHUSDT", "SOLUSDT"], days=args.days)
        return

    backfill_binance_klines(symbol=args.symbol, days=args.days)


if __name__ == "__main__":
    main()
