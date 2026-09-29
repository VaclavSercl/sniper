#!/usr/bin/env python3
"""
BEROUN Tri-Venue Klines / Candles Ingest & Backfill
Downloads OHLCV candlestick data from public endpoints for:
  - BTC (Bitcoin in USDT, USDC, EUR, USD)
  - Dolar & Stablecoins (USDT, USDC, FDUSD)
  - Euro (EUR/USD, EUR/USDT, EUR/USDC)

Venues:
  1. Binance: GET /api/v3/klines
  2. Bitfinex: GET /v2/candles/trade:{timeframe}:{symbol}/hist
  3. Hyperliquid: POST /info {type: candleSnapshot}

Standard library only. All public endpoints (zero credentials required).
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
RESEARCH_DIR = REPO_ROOT / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from binance_connector import BinanceReadOnly
from bitfinex_connector import BitfinexReadOnly
from hyperliquid_connector import HyperliquidReadOnly

SCHEMA = """
CREATE TABLE IF NOT EXISTS tri_venue_klines (
    open_time   TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    open        NUMERIC NOT NULL,
    high        NUMERIC NOT NULL,
    low         NUMERIC NOT NULL,
    close       NUMERIC NOT NULL,
    volume      NUMERIC NOT NULL,
    close_time  TIMESTAMPTZ NOT NULL,
    src         TEXT NOT NULL,
    PRIMARY KEY (symbol, open_time, src)
);
CREATE INDEX IF NOT EXISTS idx_tri_klines_sym_time ON tri_venue_klines (symbol, open_time);
"""

BINANCE_PAIRS = [
    "BTCUSDT", "BTCUSDC", "BTCEUR",
    "EURUSDT", "EURUSDC",
    "USDCUSDT", "FDUSDUSDT"
]

BITFINEX_PAIRS = [
    "tBTCUSD", "tBTCEUR", "tBTCUST",
    "tEURUSD", "tEURUST",
    "tUSTUSD", "tUDCUSD"
]

HYPERLIQUID_PAIRS = ["BTC", "HYPE", "ETH", "SOL"]


def psql(sql: str) -> str:
    """Executes SQL via psql, falling back to sudo -u beroun if peer auth requires it."""
    cmd = ["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        if "Peer authentication failed" in r.stderr or "FATAL" in r.stderr:
            cmd = ["sudo", "-u", "beroun", "psql", "-d", "beroun", "-t", "-A", "-c", sql]
            r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql error: {r.stderr.strip()}")
    return r.stdout


class TriVenueKlinesFetcher:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.binance = BinanceReadOnly()
        self.bitfinex = BitfinexReadOnly()
        self.hyperliquid = HyperliquidReadOnly()

    def ensure_schema(self) -> None:
        if not self.dry_run:
            try:
                psql(SCHEMA)
            except Exception as e:
                print(f"Warning: could not bootstrap schema: {e}", file=sys.stderr)

    def fetch_binance(self, symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 50) -> List[Dict[str, Any]]:
        sym = symbol.upper()
        raw = self.binance.klines(sym, interval=interval, limit=limit)
        results = []
        for k in raw:
            # [openTime, open, high, low, close, volume, closeTime, ...]
            results.append({
                "venue": "binance",
                "symbol": sym,
                "open_time_ms": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time_ms": k[6],
                "src": "binance_klines"
            })
        return results

    def fetch_bitfinex(self, symbol: str = "tBTCUSD", timeframe: str = "1h", limit: int = 50) -> List[Dict[str, Any]]:
        sym = symbol if symbol.startswith("t") else f"t{symbol.upper()}"
        raw = self.bitfinex.candles(sym, timeframe=timeframe, limit=limit)
        results = []
        for c in raw:
            results.append({
                "venue": "bitfinex",
                "symbol": sym,
                "open_time_ms": c["timestamp_ms"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
                "close_time_ms": c["timestamp_ms"] + 3600000 - 1,
                "src": "bitfinex_candles"
            })
        return results

    def fetch_hyperliquid(self, symbol: str = "BTC", interval: str = "1h", limit: int = 50) -> List[Dict[str, Any]]:
        coin = symbol.upper().replace("-PERP", "").replace("USDT", "").replace("USD", "")
        now_ms = int(time.time() * 1000)
        span_ms = limit * 3600_000
        start_ms = now_ms - span_ms

        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": now_ms
            }
        }
        raw = self.hyperliquid._post_info(payload)
        results = []
        for item in raw:
            # item has: t, T, s, i, o, c, h, l, v, n
            results.append({
                "venue": "hyperliquid",
                "symbol": f"{coin}-PERP",
                "open_time_ms": item["t"],
                "open": float(item["o"]),
                "high": float(item["h"]),
                "low": float(item["l"]),
                "close": float(item["c"]),
                "volume": float(item["v"]),
                "close_time_ms": item["T"],
                "src": "hyperliquid_candles"
            })
        return results

    def save_batch_to_db(self, candles: List[Dict[str, Any]]) -> int:
        if self.dry_run or not candles:
            return 0
        written = 0
        for c in candles:
            try:
                psql(
                    f"INSERT INTO tri_venue_klines (open_time, symbol, open, high, low, close, volume, close_time, src) "
                    f"VALUES (to_timestamp({c['open_time_ms']}/1000.0), '{c['symbol']}', {c['open']}, {c['high']}, "
                    f"{c['low']}, {c['close']}, {c['volume']}, to_timestamp({c['close_time_ms']}/1000.0), '{c['src']}') "
                    f"ON CONFLICT (symbol, open_time, src) DO NOTHING"
                )
                written += 1
            except Exception as e:
                print(f"Error saving candle for {c['symbol']}: {e}", file=sys.stderr)
        return written

    def backfill_all_stables(self, limit: int = 24) -> Dict[str, Any]:
        """Backfills OHLCV candles for all BTC, USD, EUR, and stablecoin markets across the venues."""
        self.ensure_schema()
        stats = {"binance": 0, "bitfinex": 0, "hyperliquid": 0}

        print(f"[KLINES] Backfilling {limit} 1h candles for BTC, USD, EUR, and stablecoin pairs across Tri-Venue Core...")

        # 1. Binance
        for pair in BINANCE_PAIRS:
            try:
                candles = self.fetch_binance(pair, limit=limit)
                saved = self.save_batch_to_db(candles)
                stats["binance"] += saved
                print(f"  Binance: {pair:<10} fetched={len(candles)} saved={saved}")
                time.sleep(0.1)
            except Exception as e:
                print(f"  Binance {pair} failed: {e}", file=sys.stderr)

        # 2. Bitfinex
        for pair in BITFINEX_PAIRS:
            try:
                candles = self.fetch_bitfinex(pair, limit=limit)
                saved = self.save_batch_to_db(candles)
                stats["bitfinex"] += saved
                print(f"  Bitfinex: {pair:<10} fetched={len(candles)} saved={saved}")
                time.sleep(0.2)
            except Exception as e:
                print(f"  Bitfinex {pair} failed: {e}", file=sys.stderr)

        # 3. Hyperliquid
        for coin in HYPERLIQUID_PAIRS:
            try:
                candles = self.fetch_hyperliquid(coin, limit=limit)
                saved = self.save_batch_to_db(candles)
                stats["hyperliquid"] += saved
                print(f"  Hyperliquid: {coin:<10} fetched={len(candles)} saved={saved}")
                time.sleep(0.1)
            except Exception as e:
                print(f"  Hyperliquid {coin} failed: {e}", file=sys.stderr)

        total = sum(stats.values())
        print(f"[KLINES COMPLETE] Total candles saved to PostgreSQL: {total}")
        return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Tri-Venue Klines / Candles (BTC, USD, EUR, Stables)")
    parser.add_argument("--symbol", default="BTC", help="Canonical symbol (e.g. BTC, ETH, SOL, HYPE)")
    parser.add_argument("--venue", choices=["all", "binance", "bitfinex", "hyperliquid"], default="all")
    parser.add_argument("--limit", type=int, default=10, help="Number of candles to fetch")
    parser.add_argument("--save-db", action="store_true", help="Save candles to PostgreSQL tri_venue_klines")
    parser.add_argument("--all-stables", action="store_true", help="Backfill full universe of BTC, USD, EUR, and stablecoin pairs")
    args = parser.parse_args()

    fetcher = TriVenueKlinesFetcher(dry_run=not args.save_db)

    if args.all_stables:
        fetcher.backfill_all_stables(limit=args.limit)
        return 0

    out = {}
    if args.venue in ("all", "binance"):
        sym = f"{args.symbol.upper()}USDT" if not args.symbol.upper().endswith("USDT") else args.symbol.upper()
        candles = fetcher.fetch_binance(sym, limit=args.limit)
        if args.save_db:
            fetcher.save_batch_to_db(candles)
        out["binance"] = candles

    if args.venue in ("all", "bitfinex"):
        sym = f"t{args.symbol.upper()}USD" if not args.symbol.upper().startswith("T") else args.symbol.upper()
        candles = fetcher.fetch_bitfinex(sym, limit=args.limit)
        if args.save_db:
            fetcher.save_batch_to_db(candles)
        out["bitfinex"] = candles

    if args.venue in ("all", "hyperliquid"):
        candles = fetcher.fetch_hyperliquid(args.symbol, limit=args.limit)
        if args.save_db:
            fetcher.save_batch_to_db(candles)
        out["hyperliquid"] = candles

    summary = {
        venue: {
            "count": len(candles),
            "latest_close": candles[0]["close"] if candles else None,
            "sample": candles[0] if candles else None
        }
        for venue, candles in out.items()
    }

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
