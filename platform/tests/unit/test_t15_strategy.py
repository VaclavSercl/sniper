#!/usr/bin/env python3
"""
Unit tests for Strategy T15 (MiCA Cross-Currency Basis & Triangular Synthetic Carry)
"""

import math
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESEARCH_DIR = REPO_ROOT / "research" / "strategies"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from t15_mica_cross_basis import T15CrossBasisEngine


class TestT15Strategy(unittest.TestCase):
    def setUp(self):
        self.engine = T15CrossBasisEngine(initial_capital=1000.0)

    def test_compute_synthetic_rate_equilibrium(self):
        """In perfect equilibrium without friction, synthetic cross-rate is exactly 1.0."""
        # BTC/USD = 60,000, EUR/USD = 1.08 -> BTC/EUR = 55,555.5555
        btc_usdc = 60000.0
        eur_usdc = 1.08
        btc_eur = btc_usdc / eur_usdc
        rate = self.engine.compute_synthetic_rate(btc_eur, btc_usdc, eur_usdc)
        self.assertAlmostEqual(rate, 1.0, places=5)

    def test_compute_synthetic_rate_dislocation(self):
        """Detects 20 bps dislocation between fiat EUR and USDC crypto price."""
        btc_usdc = 60000.0
        eur_usdc = 1.08
        # BTC/EUR is 20 bps higher than implied
        btc_eur = (btc_usdc / eur_usdc) * 1.0020
        rate = self.engine.compute_synthetic_rate(btc_eur, btc_usdc, eur_usdc)
        self.assertAlmostEqual(rate, 1.0020, places=4)

    def test_ou_parameters_mean_reversion(self):
        """Verifies OU estimation correctly estimates half-life for mean-reverting series."""
        # Simulated OU process
        series = [1.0]
        theta_true = 0.5
        for i in range(100):
            next_val = series[-1] + theta_true * (1.0 - series[-1]) + 0.001 * math.sin(i)
            series.append(next_val)

        theta, mu, half_life = self.engine.estimate_ou_parameters(series)
        self.assertGreater(theta, 0.0)
        self.assertAlmostEqual(mu, 1.0, places=1)
        self.assertLess(half_life, 20.0)

    def test_simulation_run(self):
        """Legacy toy metrics must never qualify execution."""
        bars = [
            {
                "ts": 1700000000 + i * 3600,
                "btc_usdc": 60000.0,
                "btc_eur": (60000.0 / 1.08) * (1.0 + 0.001 * math.sin(i * 0.2)),
                "eur_usdc": 1.08
            }
            for i in range(100)
        ]
        funding = [(1700000000 + i * 3600, 0.0001) for i in range(100)]
        res = self.engine.simulate(bars, funding)

        self.assertEqual(res["verdict"], "UNQUALIFIED_LEGACY_MODEL")
        self.assertGreater(res["funding_earned_usd"], 0.0)
        self.assertFalse(res["live_eligible"])
        self.assertTrue(res["falsification_gates"]["F5_zero_delta_maintained"])


if __name__ == "__main__":
    unittest.main()
