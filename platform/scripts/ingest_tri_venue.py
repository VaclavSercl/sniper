#!/usr/bin/env python3
"""
BEROUN Tri-Venue Market Ingest Pipeline (L0 Shadow Mode)
Continuously downloads and ingests market data focused on:
  - BTC (Bitcoin across all denominations)
  - Dolar (USD, USDT, USDC, FDUSD)
  - Euro (EUR, EUR/USD, EUR/USDT, EUR/USDC)
  - Stablecoin peg & cross-currency spreads

Venues:
  1. Binance (BTCUSDT, BTCUSDC, BTCEUR, EURUSDT, EURUSDC, USDCUSDT, FDUSDUSDT)
  2. Bitfinex (tBTCUSD, tBTCEUR, tBTCUST, tEURUST, tUSTUSD, tUDCUSD)
  3. Hyperliquid (BTC-PERP, ETH-PERP, SOL-PERP, HYPE-PERP + 1h Funding Rates)

Stores records into PostgreSQL:
  - market_ticks(ts, symbol, price, src)
  - market_funding(funding_time, symbol, rate, mark_price, src)

Standard library only. Robust error isolation per venue.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure research module imports work
REPO_ROOT = Path(__file__).resolve().parent.parent
RESEARCH_DIR = REPO_ROOT / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from binance_connector import BinanceReadOnly
from bitfinex_connector import BitfinexReadOnly
from hyperliquid_connector import HyperliquidReadOnly

LOG_FILE = os.environ.get("BEROUN_INGEST_LOG", "/var/log/beroun/ingest.log")

BINANCE_TARGETS = [
    "BTCUSDT", "BTCUSDC", "BTCEUR",
    "EURUSDT", "EURUSDC",
    "USDCUSDT", "FDUSDUSDT"
]

BITFINEX_TARGETS = [
    "tBTCUSD", "tBTCEUR", "tBTCUST",
    "tEURUST",
    "tUSTUSD", "tUDCUSD"
]

HYPERLIQUID_TARGETS = ["BTC", "HYPE", "ETH", "SOL"]

# Absent from the official exchange pair inventory checked 2026-09-29.
# Explicitly disclose this missing input; never substitute a synthetic FX price.
UNAVAILABLE_MARKETS = {"bitfinex": ["tEURUSD"]}


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} [TRI-INGEST] {msg}\n"
    if os.path.exists(os.path.dirname(LOG_FILE)):
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
    print(line.strip(), flush=True)


def psql(sql: str) -> str:
    """Run under the configured service identity; never escalate on failure."""
    cmd = ["psql", "-X", "-w", "-v", "ON_ERROR_STOP=1", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise RuntimeError("Database write failed")
    return r.stdout


class TriVenueIngest:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.errors = []
        self.binance = BinanceReadOnly()
        self.bitfinex = BitfinexReadOnly()
        self.hyperliquid = HyperliquidReadOnly()

    def failure(self, source, exc):
        self.errors.append({"source": source, "error": type(exc).__name__})
        log(f"{source} failed: {type(exc).__name__}")

    def fetch_binance(self) -> List[Dict[str, Any]]:
        ticks = []
        for sym in BINANCE_TARGETS:
            try:
                data = self.binance.ticker_price(sym)
                if "price" in data and data.get("symbol") == sym and not isinstance(data['price'], bool):
                    ticks.append({
                        "symbol": sym,
                        "price": float(data["price"]),
                        "src": "binance_ticker"
                    })
                else:
                    raise ValueError("Missing ticker price")
            except Exception as e:
                self.failure(f"Binance:{sym}", e)
        return ticks

    def fetch_bitfinex(self) -> List[Dict[str, Any]]:
        ticks = []
        try:
            batch = self.bitfinex.tickers(BITFINEX_TARGETS)
            if set(batch) - set(BITFINEX_TARGETS):
                raise ValueError("Unexpected Bitfinex symbol")
            for sym in BITFINEX_TARGETS:
                data = batch.get(sym, {})
                if "mid" in data and not isinstance(data['mid'], bool) and data["mid"] > 0:
                    ticks.append({
                        "symbol": sym,
                        "price": data["mid"],
                        "src": "bitfinex_ticker"
                    })
        except Exception as e:
            self.failure("Bitfinex", e)
        return ticks

    def fetch_hyperliquid(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        ticks = []
        fundings = []
        try:
            mids = self.hyperliquid.all_mids()
            for coin in HYPERLIQUID_TARGETS:
                if coin in mids and not isinstance(mids[coin], bool):
                    ticks.append({
                        "symbol": f"{coin}-PERP",
                        "price": float(mids[coin]),
                        "src": "hyperliquid_mid"
                    })

            # Fetch funding & mark prices
            meta, ctxs = self.hyperliquid.meta_and_asset_contexts()
            universe = meta.get("universe", [])
            for idx, asset in enumerate(universe):
                coin = asset.get("name")
                if coin in HYPERLIQUID_TARGETS and idx < len(ctxs):
                    ctx = ctxs[idx]
                    if isinstance(ctx['funding'], bool) or isinstance(ctx['markPx'], bool):
                        raise ValueError("Boolean funding value")
                    funding_rate = float(ctx["funding"])
                    mark_px = float(ctx["markPx"])
                    fundings.append({
                        "symbol": f"{coin}-PERP",
                        "rate": funding_rate,
                        "mark_price": mark_px,
                        "src": "hyperliquid_funding"
                    })
        except Exception as e:
            self.failure("Hyperliquid", e)
        return ticks, fundings

    def ingest_cycle(self) -> Dict[str, Any]:
        self.errors = []
        all_ticks: List[Dict[str, Any]] = []
        all_fundings: List[Dict[str, Any]] = []

        # 1. Binance
        b_ticks = self.fetch_binance()
        all_ticks.extend(b_ticks)

        # 2. Bitfinex
        bfx_ticks = self.fetch_bitfinex()
        all_ticks.extend(bfx_ticks)

        # 3. Hyperliquid
        hl_ticks, hl_fundings = self.fetch_hyperliquid()
        all_ticks.extend(hl_ticks)
        all_fundings.extend(hl_fundings)

        # Coverage failures remain visible even when an endpoint returns an empty
        # successful response. Never silently drop a configured market.
        for label, rows, targets in (("Binance", b_ticks, BINANCE_TARGETS),
                                    ("Bitfinex", bfx_ticks, BITFINEX_TARGETS),
                                    ("Hyperliquid ticks", hl_ticks, [c+'-PERP' for c in HYPERLIQUID_TARGETS]),
                                    ("Hyperliquid funding", hl_fundings, [c+'-PERP' for c in HYPERLIQUID_TARGETS])):
            missing = set(targets) - {row['symbol'] for row in rows}
            if missing or len({row['symbol'] for row in rows}) != len(rows):
                self.failure(label, ValueError("Missing configured symbols"))
        valid_ticks = []
        for row in all_ticks:
            try:
                if not math.isfinite(row['price']) or row['price'] <= 0:
                    raise ValueError("Invalid price")
                valid_ticks.append(row)
            except (ValueError, TypeError) as exc:
                self.failure("Tick validation", exc)
        all_ticks = valid_ticks
        valid_funding = []
        for row in all_fundings:
            try:
                if not math.isfinite(row['rate']) or not math.isfinite(row['mark_price']) or row['mark_price'] <= 0:
                    raise ValueError("Invalid funding snapshot")
                valid_funding.append(row)
            except (ValueError, TypeError) as exc:
                self.failure("Funding validation", exc)
        all_fundings = valid_funding

        # Persistence
        ticks_written = 0
        funding_written = 0

        if self.dry_run:
            log(f"[DRY-RUN] Collected {len(all_ticks)} ticks and {len(all_fundings)} funding records.")
            return {
                "dry_run": True,
                "ticks_count": len(all_ticks),
                "funding_count": len(all_fundings),
                "ticks": all_ticks,
                "funding": all_fundings,
                "errors": self.errors,
                "funding_kind": "SNAPSHOT_NOT_SETTLEMENT",
                "unavailable_markets": UNAVAILABLE_MARKETS,
            }

        # Insert ticks into DB
        for t in all_ticks:
            try:
                psql(f"INSERT INTO market_ticks (symbol, price, src) "
                     f"VALUES ('{t['symbol']}', {t['price']}, '{t['src']}')")
                ticks_written += 1
            except Exception as e:
                self.failure("Tick insert", e)

        # Insert funding rates into DB
        for f in all_fundings:
            try:
                mark_str = f"{f['mark_price']}" if f['mark_price'] is not None else "NULL"
                psql(f"INSERT INTO market_funding (funding_time, symbol, rate, mark_price, src) "
                     f"VALUES (now(), '{f['symbol']}', {f['rate']}, {mark_str}, '{f['src']}') "
                     f"ON CONFLICT (symbol, src, funding_time) DO NOTHING")
                funding_written += 1
            except Exception as e:
                self.failure("Funding insert", e)

        log(f"Cycle complete: ticks={ticks_written} funding={funding_written} (Binance={len(b_ticks)}, Bitfinex={len(bfx_ticks)}, Hyperliquid={len(hl_ticks)})")
        return {
            "ticks_written": ticks_written,
            "funding_written": funding_written,
            "total_collected": len(all_ticks),
            "ticks": all_ticks,
            "funding": all_fundings,
            "errors": self.errors,
            "funding_kind": "SNAPSHOT_NOT_SETTLEMENT",
            "unavailable_markets": UNAVAILABLE_MARKETS,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="BEROUN Tri-Venue Market Ingest (BTC, USD, EUR, Stables)")
    parser.add_argument("--dry-run", action="store_true", help="Print collected data without inserting to DB")
    parser.add_argument("--daemon", action="store_true", help="Run continuously every N seconds")
    parser.add_argument("--interval", type=int, default=60, help="Interval in seconds for daemon mode")
    args = parser.parse_args()

    ingest = TriVenueIngest(dry_run=args.dry_run)

    if args.daemon:
        log(f"Starting tri-venue ingest daemon (interval={args.interval}s, dry_run={args.dry_run})...")
        while True:
            try:
                ingest.ingest_cycle()
            except Exception as e:
                log(f"Unhandled exception in ingest cycle: {e}")
            time.sleep(args.interval)
    else:
        res = ingest.ingest_cycle()
        if args.dry_run:
            print(json.dumps(res, indent=2))
        return 1 if res['errors'] else 0


if __name__ == "__main__":
    sys.exit(main())
