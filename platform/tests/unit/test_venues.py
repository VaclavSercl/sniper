#!/usr/bin/env python3
"""
SynthBit Universal Development Harness · Tri-Venue Connector Test Battery
Verifies read-only venue connectors (Binance, Bitfinex, Hyperliquid) and Tri-Venue Monitor.
Strictly zero external dependencies (standard library unittest only).
"""

import hashlib
import hmac
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESEARCH_DIR = REPO_ROOT / "research"
sys.path.insert(0, str(RESEARCH_DIR))

import binance_connector
import bitfinex_connector
import hyperliquid_connector
import tri_venue_monitor


class TestBinanceConnector(unittest.TestCase):
    def test_signature_calculation(self):
        client = binance_connector.BinanceReadOnly(
            api_key="test_key",
            api_secret="test_secret"
        )
        # Test HMAC-SHA256 signature
        query = "symbol=BTCUSDT&timestamp=1700000000000&recvWindow=5000"
        expected_sig = hmac.new(b"test_secret", query.encode("utf-8"), hashlib.sha256).hexdigest()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"balances": []}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            with patch("time.time", return_value=1700000000.0):
                res = client._signed_get("/api/v3/account", {"symbol": "BTCUSDT"})
                self.assertEqual(res, {"balances": []})

                call_req = mock_urlopen.call_args[0][0]
                self.assertIn(f"signature={expected_sig}", call_req.full_url)
                self.assertEqual(call_req.headers.get("X-mbx-apikey"), "test_key")

    def test_signed_without_credentials_raises(self):
        client = binance_connector.BinanceReadOnly(api_key=None, api_secret=None)
        with self.assertRaises(ValueError):
            client.account()


class TestBitfinexConnector(unittest.TestCase):
    def test_governor_window(self):
        gov = bitfinex_connector.SlidingWindowGovernor(max_calls=3, window_seconds=1.0)
        gov.acquire()
        gov.acquire()
        gov.acquire()
        self.assertEqual(len(gov.calls), 3)

    def test_ticker_parser(self):
        client = bitfinex_connector.BitfinexReadOnly()
        # Sample Bitfinex v2 ticker array: [BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_RELATIVE, LAST_PRICE, VOLUME, HIGH, LOW]
        fixture = [81474.0, 1.5, 81478.0, 2.0, 500.0, 0.006, 81476.0, 800.0, 81900.0, 80700.0]

        with patch.object(client, "_public_get", return_value=fixture):
            res = client.ticker("tBTCUSD")
            self.assertEqual(res["bid"], 81474.0)
            self.assertEqual(res["ask"], 81478.0)
            self.assertEqual(res["mid"], 81476.0)
            self.assertEqual(res["symbol"], "tBTCUSD")

    def test_signed_signature_and_headers(self):
        client = bitfinex_connector.BitfinexReadOnly(
            api_key="bfx_key",
            api_secret="bfx_secret"
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'[{"type": "exchange", "currency": "BTC", "balance": 1.0}]'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            with patch("time.time", return_value=1700000000.123456):
                nonce = str(int(1700000000.123456 * 1000000))
                sig_payload = f"/api/v2/auth/r/wallets{nonce}{{}}"
                expected_sig = hmac.new(b"bfx_secret", sig_payload.encode("utf-8"), hashlib.sha384).hexdigest()

                res = client.wallets()
                self.assertEqual(len(res), 1)

                call_req = mock_urlopen.call_args[0][0]
                self.assertEqual(call_req.headers.get("Bfx-apikey"), "bfx_key")
                self.assertEqual(call_req.headers.get("Bfx-nonce"), nonce)
                self.assertEqual(call_req.headers.get("Bfx-signature"), expected_sig)

    def test_signed_without_credentials_raises(self):
        client = bitfinex_connector.BitfinexReadOnly(api_key=None, api_secret=None)
        with self.assertRaises(ValueError):
            client.wallets()


class TestHyperliquidConnector(unittest.TestCase):
    def test_url_selection(self):
        c_main = hyperliquid_connector.HyperliquidReadOnly(testnet=False)
        self.assertEqual(c_main.info_url, hyperliquid_connector.MAINNET_INFO_URL)

        c_test = hyperliquid_connector.HyperliquidReadOnly(testnet=True)
        self.assertEqual(c_test.info_url, hyperliquid_connector.TESTNET_INFO_URL)

    def test_post_info_payload(self):
        client = hyperliquid_connector.HyperliquidReadOnly()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"BTC": "81500.0", "ETH": "2650.0"}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            mids = client.all_mids()
            self.assertEqual(mids.get("BTC"), "81500.0")

            call_req = mock_urlopen.call_args[0][0]
            self.assertEqual(call_req.data, b'{"type": "allMids"}')
            self.assertEqual(call_req.headers.get("Content-type"), "application/json")


class TestTriVenueMonitor(unittest.TestCase):
    @patch("hyperliquid_connector.HyperliquidReadOnly.meta_and_asset_contexts")
    @patch("hyperliquid_connector.HyperliquidReadOnly.all_mids")
    @patch("bitfinex_connector.BitfinexReadOnly.tickers")
    @patch("binance_connector.BinanceReadOnly.ticker_price")
    def test_snapshot_calculations(self, mock_b_price, mock_bfx_tickers, mock_hl_mids, mock_hl_meta):
        mock_b_price.side_effect = lambda sym: {
            "BTCUSDT": {"symbol": "BTCUSDT", "price": "80000.00"},
            "ETHUSDT": {"symbol": "ETHUSDT", "price": "2500.00"},
            "SOLUSDT": {"symbol": "SOLUSDT", "price": "100.00"},
        }.get(sym, {})

        mock_bfx_tickers.return_value = {
            "tBTCUSD": {"mid": 79900.0},
            "tETHUSD": {"mid": 2505.0},
            "tSOLUSD": {"mid": 99.5},
        }

        mock_hl_mids.return_value = {
            "BTC": "80100.0",
            "ETH": "2502.0",
            "SOL": "100.2",
            "HYPE": "90.0",
        }

        mock_hl_meta.return_value = (
            {"universe": [{"name": "BTC"}, {"name": "ETH"}, {"name": "SOL"}, {"name": "HYPE"}]},
            [
                {"funding": "0.00001", "openInterest": "1000"},
                {"funding": "0.00001", "openInterest": "2000"},
                {"funding": "0.00001", "openInterest": "3000"},
                {"funding": "0.00002", "openInterest": "4000"},
            ]
        )

        snap = tri_venue_monitor.collect_tri_venue_snapshot()
        self.assertIn("venues", snap)
        self.assertIn("comparisons", snap)
        self.assertIn("funding_rates", snap)

        # BTC spread check: BFX (79900) vs BIN (80000) -> diff = -100 (-0.125%)
        btc_comp = snap["comparisons"]["BTC"]
        self.assertEqual(btc_comp["spread_bitfinex_vs_binance"]["diff_usd"], -100.0)
        self.assertEqual(btc_comp["spread_bitfinex_vs_binance"]["diff_pct"], -0.125)

        # BTC spread check: HL (80100) vs BIN (80000) -> diff = +100 (+0.125%)
        self.assertEqual(btc_comp["spread_hyperliquid_vs_binance"]["diff_usd"], 100.0)
        self.assertEqual(btc_comp["spread_hyperliquid_vs_binance"]["diff_pct"], 0.125)

        # HYPE funding rate check: hourly 0.00002 -> annual 0.00002 * 24 * 365 * 100 = 17.52%
        hype_funding = snap["funding_rates"]["HYPE"]
        self.assertEqual(hype_funding["annualized_pct"], 17.52)


if __name__ == "__main__":
    unittest.main()
