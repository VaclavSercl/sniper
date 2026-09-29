#!/usr/bin/env python3
"""
SynthBit Universal Development Harness · Tri-Venue Opportunity Scanner Tests
Verifies:
  1. Cash & Carry / Basis calculation logic.
  2. Triangular FX dislocation mathematics.
  3. Stablecoin parity calculation and deviation metrics.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESEARCH_DIR = REPO_ROOT / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

import tri_venue_opportunity_scanner as scanner


class TestOpportunityScanner(unittest.TestCase):
    @patch("tri_venue_opportunity_scanner.get_latest_prices")
    @patch("tri_venue_opportunity_scanner.get_latest_funding_rates")
    def test_basis_arbitrage_math(self, mock_funding, mock_prices):
        mock_prices.return_value = {
            "tBTCUSD": 80000.0,
            "BTC-PERP": 80100.0,
            "tEURUSD": 1.15,
            "tBTCEUR": 69500.0,
            "BTCEUR": 69550.0,
            "tUSTUSD": 0.9995,
            "tUDCUSD": 1.0001,
            "USDCUSDT": 1.0005,
            "FDUSDUSDT": 0.9990
        }
        mock_funding.return_value = {
            "BTC": {
                "hourly_rate": 0.0000125,
                "annual_apr_pct": 10.95,
                "mark_price": 80100.0
            }
        }

        data = scanner.scan_opportunities()
        basis = data["basis_arbitrage"]["BTC"]
        # Basis spread: (80100 - 80000) = +100.0 USD (+0.125%)
        self.assertEqual(basis["basis_spread_usd"], 100.0)
        self.assertEqual(basis["basis_spread_pct"], 0.125)
        self.assertEqual(basis["funding_annual_apr_pct"], 10.95)
        self.assertEqual(basis["verdict"], "ATTRACTIVE_POSITIVE_CARRY")

        # Triangular FX: synthetic = 80000 / 1.15 = 69565.22
        t = data["triangular_fx"]["BTC_EUR_USD"]
        self.assertAlmostEqual(t["synthetic_btc_eur"], 69565.22, places=1)
        self.assertGreater(t["dislocation_eur"], 0)

        # Stables
        st = data["stablecoin_parities"]
        self.assertEqual(st["usdt_usd_bitfinex"]["deviation_bps"], -5.0)
        self.assertEqual(st["usdc_usd_bitfinex"]["deviation_bps"], 1.0)
        self.assertEqual(st["usdc_usdt_binance"]["premium_bps"], 5.0)
        self.assertEqual(st["fdusd_usdt_binance"]["discount_bps"], -10.0)


if __name__ == "__main__":
    unittest.main()
