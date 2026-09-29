"""Unit testy BEROUN backtest frameworku — syntetická data, deterministický seed.

Spustit: /home/beroun/.hermes/hermes-agent/venv/bin/python -m unittest discover -s /opt/sniper/current/platform/legacy/research/backtest
"""

import math
import random
import unittest

from cpcv import cpcv_splits, assert_no_lookahead, _embargo_size
from dsr import (norm_cdf, norm_ppf, sharpe_ratio, expected_max_sr,
                 deflated_sharpe_ratio, variance_sr)
from pbo import pbo_cscv
from minbtl import min_backtest_length, minbtl_prescreen


def gauss(rng, mu=0.0, sd=1.0):
    """Normální vzorek (Box-Muller) — stdlib random.gauss je deterministický
    při seedu, ale vlastní implementace je explicitní."""
    return rng.gauss(mu, sd)


def make_returns(n, mu=0.001, sd=0.01, seed=42):
    rng = random.Random(seed)
    return [gauss(rng, mu, sd) for _ in range(n)]


def make_flat_grid(n_strats, T, seed=7):
    """'Plochá mřížka' — N strategií bez skutečné edge (mu=0).
    PBO by mělo jít k 0.5."""
    return [make_returns(T, mu=0.0, sd=0.01, seed=seed + s) for s in range(n_strats)]


class TestCPCV(unittest.TestCase):
    def test_splits_basic(self):
        n = 1000
        splits = cpcv_splits(n, n_groups=8, k_test=2, embargo_pct=0.01)
        # C(8,2) = 28 kombinací
        self.assertEqual(len(splits), 28)

    def test_embargo_at_least_1pct(self):
        n = 1000
        e = _embargo_size(n, 0.01)
        self.assertGreaterEqual(e, 10)  # 1 % z 1000

    def test_no_lookahead(self):
        n = 800
        splits = cpcv_splits(n, n_groups=8, k_test=2, embargo_pct=0.01)
        for s in splits:
            self.assertTrue(assert_no_lookahead(s, horizon=1),
                            f"look-ahead v kombinaci {s['test_groups']}")

    def test_embargo_blocks_adjacent_train(self):
        # žádný train index nesmí být v (max_test, max_test + embargo]
        n = 800
        splits = cpcv_splits(n, n_groups=8, k_test=2, embargo_pct=0.05)
        for s in splits:
            mx = max(s["test"])
            mn = min(s["test"])
            for t in s["train"]:
                self.assertFalse(t in s["test"])
                self.assertFalse(mx < t <= mx + s["embargo"],
                                 "train uvnitř embargo okna po testu")
                self.assertFalse(mn - 1 <= t < mn,
                                 "train v purge okně před testem")

    def test_coverage(self):
        # každý index je v nějakém testu
        n = 400
        splits = cpcv_splits(n, n_groups=8, k_test=2)
        seen = set()
        for s in splits:
            seen.update(s["test"])
        self.assertEqual(seen, set(range(n)))

    def test_no_overlap_train_test(self):
        n = 400
        for s in cpcv_splits(n, n_groups=10, k_test=2):
            self.assertFalse(set(s["train"]) & set(s["test"]))


class TestDSR(unittest.TestCase):
    def test_norm_ppf_roundtrip(self):
        for p in (0.01, 0.05, 0.5, 0.95, 0.99):
            self.assertAlmostEqual(norm_cdf(norm_ppf(p)), p, places=6)

    def test_variance_sr_is_null_variance(self):
        # V[SR_n] = 1/T (T = délka dat), nikdy výběrový rozptyl pozorovaných Sharpů
        self.assertEqual(variance_sr(10), 0.1)

    def test_sr0_grows_with_N(self):
        # při pevné varianci V[SR_n] SR0 roste s N
        self.assertGreater(expected_max_sr(100, 1.0), expected_max_sr(10, 1.0))

    def test_dsr_decreases_with_N(self):
        rets = make_returns(500, mu=0.005, sd=0.01, seed=42)
        sr_prev = None
        for N in (1, 5, 20, 100, 1000):
            res = deflated_sharpe_ratio(rets, N)
            # logování požadovaných hodnot
            self.assertIn("SR0", res)
            self.assertIn("N", res)
            self.assertIn("V_SR_n", res)
            if sr_prev is not None:
                self.assertLess(res["DSR"], sr_prev + 1e-12,
                                f"DSR má klesat s N (N={N})")
            sr_prev = res["DSR"]

    def test_dsr_fields_logged(self):
        rets = make_returns(200, seed=1)
        res = deflated_sharpe_ratio(rets, 50)
        for k in ("DSR", "SR_hat", "SR0", "N", "V_SR_n", "T", "gamma3", "gamma4"):
            self.assertIn(k, res)
        self.assertAlmostEqual(res["V_SR_n"], 1.0 / len(rets))
        self.assertEqual(res["N"], 50)

    def test_dsr_in_unit_interval(self):
        res = deflated_sharpe_ratio(make_returns(300, seed=3), 10)
        self.assertTrue(0.0 <= res["DSR"] <= 1.0)

    def test_dsr_formula_manual(self):
        # ruční výpočet DSR pro kontrolu vzorce
        rets = make_returns(100, mu=0.01, sd=0.02, seed=9)
        res = deflated_sharpe_ratio(rets, 8)
        T = len(rets)
        sr = res["SR_hat"]
        var_sr = 1.0 / T  # V[SR_n] = 1/T za nulové hypotézy
        z1 = norm_ppf(1 - 1 / 8)
        z2 = norm_ppf(1 - 1 / (8 * math.e))
        sr0 = math.sqrt(var_sr) * ((1 - 0.5772) * z1 + 0.5772 * z2)
        self.assertAlmostEqual(res["SR0"], sr0, places=12)
        stat = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(
            1 - res["gamma3"] * sr + ((res["gamma4"] - 1) / 4) * sr ** 2)
        self.assertAlmostEqual(res["DSR"], norm_cdf(stat), places=12)


class TestPBO(unittest.TestCase):
    def test_flat_grid_pbo_near_half(self):
        # plochá mřížka (žádná edge) -> PBO ~ 0.5
        strats = make_flat_grid(n_strats=8, T=1200, seed=7)
        res = pbo_cscv(strats, n_blocks=16)
        self.assertGreater(res["PBO"], 0.30)
        self.assertLess(res["PBO"], 0.70)
        # ještě blíž k 0.5 s více strategiemi
        strats2 = make_flat_grid(n_strats=16, T=1200, seed=11)
        res2 = pbo_cscv(strats2, n_blocks=16)
        self.assertGreater(res2["PBO"], 0.35)
        self.assertLess(res2["PBO"], 0.65)

    def test_skilled_strategy_low_pbo(self):
        # jedna strategie s reálnou edge -> PBO nízké
        rng = random.Random(5)
        strats = make_flat_grid(n_strats=8, T=1200, seed=7)
        strats.append(make_returns(1200, mu=0.02, sd=0.01, seed=99))
        res = pbo_cscv(strats, n_blocks=16)
        self.assertLess(res["PBO"], 0.35)

    def test_n_combos(self):
        # C(16,8) = 12870 kombinací — ověříme počet lambd
        strats = make_flat_grid(n_strats=4, T=320, seed=3)
        res = pbo_cscv(strats, n_blocks=8)
        self.assertEqual(res["n_combos"], math.comb(8, 4))

    def test_block_count_validated(self):
        strats = make_flat_grid(n_strats=2, T=200, seed=1)
        with self.assertRaises(ValueError):
            pbo_cscv(strats, n_blocks=13)  # liché


class TestMinBTL(unittest.TestCase):
    def test_min_length_formula(self):
        sr = 0.1  # per perioda
        N = 100
        z = norm_ppf(1 - 0.05 / 100)
        self.assertAlmostEqual(min_backtest_length(sr, N), (z / sr) ** 2)

    def test_prescreen(self):
        # z(0.9995) ~ 3.29 -> T_min ~ (3.29/0.1)^2 ~ 1082: 2000 prošlo, 100 ne
        ok = minbtl_prescreen(0.1, 100, 2000)
        self.assertTrue(ok["pass"])
        bad = minbtl_prescreen(0.1, 100, 100)
        self.assertFalse(bad["pass"])

    def test_min_length_grows_with_N(self):
        self.assertGreater(min_backtest_length(0.1, 1000),
                           min_backtest_length(0.1, 10))


if __name__ == "__main__":
    unittest.main()
