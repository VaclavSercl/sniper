#!/usr/bin/env python3
"""
SynthBit Universal Development Harness · Multi-Venue Execution & Ingest Test Suite
Verifies:
  1. Unified execution router for Binance, Bitfinex, Hyperliquid.
  2. Fail-closed L0 shadow enforcement across all venues.
  3. Wire payload formatting (flags, post-only, Alo, Gtc).
  4. Tri-venue ingest pipeline and dry-run outputs.
  5. Tri-venue klines normalization.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATEWAY_DIR = REPO_ROOT / "gateway"
SCRIPTS_DIR = REPO_ROOT / "scripts"
RESEARCH_DIR = REPO_ROOT / "research"

for p in (GATEWAY_DIR, SCRIPTS_DIR, RESEARCH_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import execution_router
import fetch_tri_venue_klines
import ingest_tri_venue


class TestExecutionRouter(unittest.TestCase):
    def setUp(self):
        self.router = execution_router.UnifiedExecutionRouter(capability_level="L0")

    def test_order_validation(self):
        # Invalid venue
        req = execution_router.OrderRequest("kraken", "BTC", "buy", "limit", 80000.0, 1.0)
        with self.assertRaises(ValueError):
            req.validate()

        # Invalid side
        req = execution_router.OrderRequest("binance", "BTC", "hold", "limit", 80000.0, 1.0)
        with self.assertRaises(ValueError):
            req.validate()

        # Invalid quantity
        req = execution_router.OrderRequest("binance", "BTC", "buy", "limit", 80000.0, -1.0)
        with self.assertRaises(ValueError):
            req.validate()

    def test_binance_l0_shadow(self):
        req = execution_router.OrderRequest("binance", "BTC", "buy", "limit", 81500.0, 0.01, post_only=True)
        res = self.router.route_order(req)
        self.assertEqual(res["venue"], "binance")
        self.assertEqual(res["mode"], "L0_SHADOW")
        self.assertEqual(res["final"], "REJECTED_L0_NO_VENUE")
        self.assertEqual(res["symbol"], "BTCUSDT")

    def test_bitfinex_l0_shadow(self):
        req = execution_router.OrderRequest("bitfinex", "BTC", "buy", "limit", 81450.0, 0.01, post_only=True)
        res = self.router.route_order(req)
        self.assertEqual(res["venue"], "bitfinex")
        self.assertEqual(res["mode"], "L0_SHADOW")
        self.assertEqual(res["final"], "REJECTED_L0_NO_VENUE")
        self.assertEqual(res["symbol"], "tBTCUSD")
        self.assertEqual(res["flags"], 4096)  # Post-Only flag verified

    def test_hyperliquid_l0_shadow(self):
        req = execution_router.OrderRequest("hyperliquid", "BTC", "buy", "limit", 81550.0, 0.01, post_only=True)
        res = self.router.route_order(req)
        self.assertEqual(res["venue"], "hyperliquid")
        self.assertEqual(res["mode"], "L0_SHADOW")
        self.assertEqual(res["final"], "REJECTED_L0_NO_VENUE")
        self.assertEqual(res["coin"], "BTC")
        self.assertEqual(res["order_spec"]["t"]["limit"]["tif"], "Alo")  # Add Liquidity Only verified


class TestTriVenueIngest(unittest.TestCase):
    @patch("binance_connector.BinanceReadOnly.ticker_price")
    @patch("bitfinex_connector.BitfinexReadOnly.tickers")
    @patch("hyperliquid_connector.HyperliquidReadOnly.all_mids")
    @patch("hyperliquid_connector.HyperliquidReadOnly.meta_and_asset_contexts")
    def test_ingest_cycle_dry_run(self, mock_hl_meta, mock_hl_mids, mock_bfx_tickers, mock_bin_price):
        mock_bin_price.return_value = {"symbol": "BTCUSDT", "price": "81500.00"}
        mock_bfx_tickers.return_value = {"tBTCUSD": {"mid": 81480.0}}
        mock_hl_mids.return_value = {"BTC": "81520.0"}
        mock_hl_meta.return_value = (
            {"universe": [{"name": "BTC"}]},
            [{"funding": "0.0000125", "markPx": "81520.0"}]
        )

        ingest = ingest_tri_venue.TriVenueIngest(dry_run=True)
        res = ingest.ingest_cycle()

        self.assertTrue(res["dry_run"])
        self.assertGreater(res["ticks_count"], 0)
        self.assertGreater(res["funding_count"], 0)

        # Verify sources
        sources = {t["src"] for t in res["ticks"]}
        self.assertIn("binance_ticker", sources)
        self.assertIn("bitfinex_ticker", sources)
        self.assertIn("hyperliquid_mid", sources)


class TestTriVenueKlines(unittest.TestCase):
    @patch("binance_connector.BinanceReadOnly.klines")
    @patch("bitfinex_connector.BitfinexReadOnly.candles")
    @patch("hyperliquid_connector.HyperliquidReadOnly._post_info")
    def test_klines_normalization(self, mock_hl_post, mock_bfx_candles, mock_bin_klines):
        mock_bin_klines.return_value = [
            [1700000000000, "80000", "81000", "79500", "80500", "150.5", 1700003599999]
        ]
        mock_bfx_candles.return_value = [
            {"timestamp_ms": 1700000000000, "open": 80000.0, "high": 81000.0, "low": 79500.0, "close": 80500.0, "volume": 120.0}
        ]
        mock_hl_post.return_value = [
            {"t": 1700000000000, "T": 1700003599999, "s": "BTC", "i": "1h", "o": "80000", "c": "80500", "h": "81000", "l": "79500", "v": "130.0", "n": 500}
        ]

        fetcher = fetch_tri_venue_klines.TriVenueKlinesFetcher(dry_run=True)
        bk = fetcher.fetch_binance("BTC", limit=1)
        bfxk = fetcher.fetch_bitfinex("BTC", limit=1)
        hlk = fetcher.fetch_hyperliquid("BTC", limit=1)

        self.assertEqual(len(bk), 1)
        self.assertEqual(bk[0]["close"], 80500.0)
        self.assertEqual(len(bfxk), 1)
        self.assertEqual(bfxk[0]["close"], 80500.0)
        self.assertEqual(len(hlk), 1)
        self.assertEqual(hlk[0]["close"], 80500.0)


if __name__ == "__main__":
    unittest.main()
