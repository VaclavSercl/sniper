"""Offline tests of the real T15 entrypoint; no database or network calls."""
import copy
import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "strategies"))
import paper_t15_daemon as daemon

HOUR = 3600000
START = 1750003200000  # An exact hour, deliberately unrelated to live data.
START -= START % HOUR


def quotes(now):
    pairs = [("bitfinex_candles", "tBTCEUR", 50000),
             ("bitfinex_candles", "tEURUSD", 2),
             ("bitfinex_candles", "tUDCUSD", 1),
             ("hyperliquid_candles", "BTC-PERP", 100000)]
    close = (now // 60000) * 60000 - 1
    return [dict(src=src, symbol=sym, close=str(price), close_ms=close) for src, sym, price in pairs]


def event(hour=1, rate="0.0001", price="100000"):
    return dict(venue="hyperliquid", symbol="BTC", settled_ms=START + hour * HOUR,
                interval_ms=HOUR, rate=rate, oracle_price=price,
                evidence_sha256="a" * 64)


class ExistingEntrypointRegressions(unittest.TestCase):
    def test_construction_does_not_write_schema(self):
        with patch.object(daemon, "psql") as query:
            daemon.T15PaperDaemon()
        query.assert_not_called()

    def test_tick_never_implicitly_initializes(self):
        obj = object.__new__(daemon.T15PaperDaemon)
        with patch.object(obj, "load_state", return_value=None), \
             patch.object(obj, "init_state", side_effect=AssertionError("implicit init")), \
             patch.object(daemon, "psql", side_effect=AssertionError("unexpected database call")):
            with self.assertRaisesRegex(RuntimeError, "explicit|initialized"):
                obj.tick()

    def test_corrupt_state_is_not_missing_state(self):
        with patch.object(daemon, "psql", return_value="not-json"):
            with self.assertRaisesRegex(RuntimeError, "Corrupt"):
                daemon.T15PaperDaemon().load_state()

    def test_compare_and_swap_rejects_lost_update(self):
        with patch.object(daemon, "psql", return_value="") as query:
            with self.assertRaisesRegex(RuntimeError, "Concurrent"):
                daemon.T15PaperDaemon().save_state({"revision": 2}, expected={"revision": 1})
        self.assertIn("AND state=", query.call_args.args[0])

    def test_failed_tick_never_saves(self):
        a = importlib.import_module("t15_paper_accounting")
        state = a.new_state(quotes(START), START)
        obj = daemon.T15PaperDaemon(clock=lambda: START + 600000)
        with patch.object(obj, "load_state", return_value=state), \
             patch.object(obj, "load_market", return_value=[]), \
             patch.object(obj, "load_funding", return_value=[]), \
             patch.object(obj, "save_state") as save:
            with self.assertRaises(RuntimeError):
                obj.tick()
        save.assert_not_called()

    def test_psql_errors_and_timeouts_are_visible(self):
        import subprocess
        from types import SimpleNamespace
        with patch.object(daemon.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(RuntimeError):
                daemon.psql("SELECT 1", fetch=True)
        with patch.object(daemon.subprocess, "run", side_effect=subprocess.TimeoutExpired("psql", 20)):
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                daemon.psql("SELECT 1", fetch=True)

    def test_status_masks_stale_gates_and_preserves_legacy(self):
        a = importlib.import_module("t15_paper_accounting")
        state = a.new_state(quotes(START), START)
        state["falsification_gates"]["F1_max_drawdown_under_10pct"] = "PASS"
        obj = daemon.T15PaperDaemon(clock=lambda: START + 600000)
        with patch.object(obj, "load_state", return_value=state), patch.object(daemon, "psql") as query:
            text = obj.format_status()
        self.assertIn("STALE", text)
        self.assertNotIn("[PASS]", text)
        query.assert_not_called()
        with patch.object(obj, "load_state", side_effect=[None, {"equity": 1016}]):
            self.assertIn("UNVALIDATED", obj.format_status())


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.a = importlib.import_module("t15_paper_accounting")
        self.state = self.a.new_state(quotes(START), START, 1000)

    def evaluate(self, events=None, now=None, state=None, rows=None):
        now = START + HOUR + 600000 if now is None else now
        return self.a.evaluate_tick(state or self.state, quotes(now) if rows is None else rows,
                                    [event()] if events is None else events, now)

    def test_funding_once_after_restart(self):
        state = self.evaluate()
        for _ in range(30):
            state = self.evaluate(state=json.loads(json.dumps(state)))
        self.assertAlmostEqual(float(state["funding_usdc"]), .05)
        self.assertEqual(len(state["funding_events"]), 1)

    def test_negative_funding_and_settlement_price(self):
        state = self.evaluate(events=[event(rate="-0.001", price="90000")])
        self.assertAlmostEqual(float(state["funding_usdc"]), -.45)
        self.assertEqual(state["falsification_gates"]["F3_positive_funding_yield"], "FAIL")

    def test_catchup_all_events(self):
        state = self.evaluate(events=[event(3), event(1), event(2)], now=START+3*HOUR+600000)
        self.assertAlmostEqual(float(state["funding_usdc"]), .15)

    def test_duplicate_same_event(self):
        self.assertAlmostEqual(float(self.evaluate(events=[event(), event()])["funding_usdc"]), .05)

    def test_revised_event_blocks(self):
        state = self.evaluate()
        with self.assertRaises(self.a.DataBlocked):
            self.evaluate(state=state, events=[event(rate="0.0002")])

    def test_funding_gap_blocks_without_mutation(self):
        original = copy.deepcopy(self.state)
        with self.assertRaises(self.a.DataBlocked):
            self.evaluate(events=[event(2)], now=START+2*HOUR+600000)
        self.assertEqual(original, self.state)

    def test_missing_stale_future_nan_quotes(self):
        now = START + HOUR + 600000
        for rows in ([], quotes(now)[:-1], quotes(now-600000)):
            with self.subTest(rows=rows), self.assertRaises(self.a.DataBlocked):
                self.evaluate(rows=rows)
        for value in ("NaN", "Infinity", "0", "-1"):
            rows = quotes(now)
            rows[0]["close"] = value
            with self.subTest(value=value), self.assertRaises(self.a.DataBlocked):
                self.evaluate(rows=rows)
        rows = quotes(now)
        rows[0]["close_ms"] = now + 1
        with self.assertRaises(self.a.DataBlocked):
            self.evaluate(rows=rows)

    def test_unsynchronized_quotes(self):
        now = START + HOUR + 600000
        rows = quotes(now)
        rows[0]["close_ms"] -= 120000
        with self.assertRaises(self.a.DataBlocked):
            self.evaluate(rows=rows)

    def test_wrong_venue_and_funding_snapshot_rejected(self):
        e = event()
        e["venue"] = "binance"
        with self.assertRaises(self.a.DataBlocked):
            self.evaluate(events=[e])
        e = event()
        del e["oracle_price"]
        e["mark_price"] = "100000"
        with self.assertRaises(self.a.DataBlocked):
            self.evaluate(events=[e])

    def test_no_funding_due_yet_is_not_profit_or_pass(self):
        state = self.evaluate(events=[], now=START+600000)
        self.assertEqual(float(state["funding_usdc"]), 0)
        self.assertEqual(state["falsification_gates"]["F3_positive_funding_yield"], "UNKNOWN")

    def test_no_arbitrary_profit_rebate_or_qualification(self):
        now = START + HOUR + 600000
        rows = quotes(now)
        rows[0]["close"] = "50030"
        state = self.evaluate(rows=rows)
        self.assertEqual(state["total_trades"], 0)
        self.assertEqual(state["accumulated_arb_profit_usd"], 0)
        self.assertEqual(state["accumulated_rebates_usd"], 0)
        self.assertEqual(state["qualification"], "BLOCKED")
        for gate in ("F2_sharpe_above_1_0", "F4_half_life_under_72h", "F7_execution_verified"):
            self.assertEqual(state["falsification_gates"][gate], "UNKNOWN")

    def test_legacy_state_not_adopted(self):
        old = copy.deepcopy(self.state)
        del old["schema_version"]
        with self.assertRaises(self.a.DataBlocked):
            self.evaluate(state=old)

    def test_late_missing_persisted_event_blocks(self):
        state = self.evaluate()
        with self.assertRaises(self.a.DataBlocked):
            self.evaluate(state=state, events=[])

    def test_daily_and_hourly_samples_not_tick_count(self):
        state = self.evaluate()
        first = copy.deepcopy(state)
        for _ in range(30):
            state = self.evaluate(state=state)
        self.assertEqual(state["hourly_samples"], first["hourly_samples"])
        self.assertEqual(state["daily_samples"], first["daily_samples"])

    def test_future_settlement_blocks(self):
        with self.assertRaises(self.a.DataBlocked):
            self.evaluate(events=[event(2)])


if __name__ == "__main__":
    unittest.main()
