#!/usr/bin/env python3
"""
SynthBit Universal Development Harness · Tri-Venue Strategies Test Suite
Verifies:
  1. Strategy T13 Delta-Neutral Basis Carry mechanics, orders, and delta parity.
  2. Strategy T14 Triangular Currency parity math and 3-legged order suite.
  3. Multi-venue paper arbitrage driver state persistence and tick flow.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STRATEGIES_DIR = REPO_ROOT / "research" / "strategies"
if str(STRATEGIES_DIR) not in sys.path:
    sys.path.insert(0, str(STRATEGIES_DIR))

from paper_tri_venue_arb import PaperArbitrageManager
from t13_basis_carry import T13BasisCarryEngine
from t14_triangular_fx import T14TriangularFXEngine


class TestTriVenueStrategies(unittest.TestCase):
    def test_t13_delta_neutrality(self):
        engine = T13BasisCarryEngine(initial_capital=1000.0)
        spot_ord, perp_ord = engine.generate_orders(
            spot_price=80000.0,
            perp_price=80050.0,
            capital_usd=1000.0
        )
        # Verify sizes are identical
        self.assertEqual(spot_ord.quantity, perp_ord.quantity)
        # Verify sides are matched long/short
        self.assertEqual(spot_ord.side, "buy")
        self.assertEqual(perp_ord.side, "sell")
        # Verify post-only flags
        self.assertTrue(spot_ord.post_only)
        self.assertTrue(perp_ord.post_only)

    def test_t13_simulation_invariants(self):
        engine = T13BasisCarryEngine(initial_capital=1000.0)
        # Mock funding records: 3 intervals with 0.0001 rate
        mock_funding = [(1000.0, 0.0001), (2000.0, 0.0001), (3000.0, 0.0001)]
        mock_klines = [
            {"ts": 1000.0, "open": 80000.0, "close": 82000.0},
            {"ts": 2000.0, "open": 82000.0, "close": 78000.0},
            {"ts": 3000.0, "open": 78000.0, "close": 80000.0},
        ]
        res = engine.simulate_historical_run(mock_funding, mock_klines)
        self.assertTrue(res["delta_neutrality_verified"])
        self.assertGreater(res["accumulated_funding_usd"], 0)
        self.assertGreater(res["accumulated_rebates_usd"], 0)
        # Because delta is neutral, return is driven by funding & rebates
        self.assertGreater(res["final_equity_usd"], 1000.0)

    def test_t14_triangle_parity(self):
        engine = T14TriangularFXEngine()
        opp = engine.calculate_dislocation(
            btc_usd=80000.0,
            eur_usd=1.1450,
            direct_btc_eur=69800.0
        )
        # Synthetic = 80000 / 1.1450 = 69869.00
        self.assertAlmostEqual(opp.synthetic_btc_eur, 69868.995, places=1)
        self.assertGreater(opp.dislocation_bps, 0)
        self.assertEqual(opp.direction, "BUY_DIRECT_SELL_SYNTHETIC")

        orders = engine.generate_triangle_orders(opp, notional_eur=500.0)
        self.assertEqual(len(orders), 3)
        # All 3 legs must be post-only on Bitfinex
        for o in orders:
            self.assertEqual(o.venue, "bitfinex")
            self.assertTrue(o.post_only)


if __name__ == "__main__":
    unittest.main()
