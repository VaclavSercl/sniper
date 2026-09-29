#!/usr/bin/env python3
"""
SynthBit Universal Development Harness · Rich Executive Daily Report Tests (§14)
Verifies:
  1. Progress bar rendering logic.
  2. Multi-currency and Satoshi benchmark PnL conversion.
  3. Report formatting and section integrity.
  4. Outbox SQL payload structure.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_rich_report as reporter


class TestRichReport(unittest.TestCase):
    def test_format_progress_bar(self):
        self.assertEqual(reporter.format_progress_bar(0.0), "[□□□□□□□□□□]")
        self.assertEqual(reporter.format_progress_bar(0.2), "[■■□□□□□□□□]")
        self.assertEqual(reporter.format_progress_bar(0.5), "[■■■■■□□□□□]")
        self.assertEqual(reporter.format_progress_bar(1.0), "[■■■■■■■■■■]")

    def test_build_full_report_structure(self):
        mock_data = {
            "mode": "L0",
            "ladder_level": "L0",
            "core_hash": "7f921e5d0ea5",
            "ticks": {
                "tBTCUSD": 80000.0,
                "tEURUSD": 1.1500,
                "tBTCEUR": 69565.0,
                "tUSTUSD": 0.9995,
                "tUDCUSD": 1.0002,
                "USDCUSDT": 1.0004,
            },
            "funding": {
                "BTC-PERP": {
                    "hourly_rate": 0.0000125,
                    "annual_apr_pct": 10.95,
                    "mark_price": 80100.0,
                }
            },
            "strategies": {
                "t13_carry": {
                    "equity_usd": 1005.50,
                    "capital_usd": 1000.0,
                    "accumulated_funding_usd": 4.50,
                    "accumulated_rebates_usd": 1.00,
                    "in_position": True,
                    "spot_btc": 0.0062,
                    "perp_short_btc": 0.0062,
                },
                "t14_triangle": {
                    "equity_eur": 1002.30,
                    "capital_eur": 1000.0,
                    "realized_profit_eur": 2.30,
                    "captured_bps_total": 23.0,
                    "total_trades": 3,
                },
            },
            "orders_rejected": 0,
            "invariant_violations": 0,
        }

        narrative = "Trh je v mírném contangu s pozitivním carry výnosem."
        winning_tier = "AGY CLI"

        report = reporter.build_full_report(mock_data, narrative, winning_tier)

        # Check required sections
        self.assertIn("SNIPER — PROVOZNÍ REPORT / BEROUN", report)
        self.assertIn("STAV SYSTÉMU", report)
        self.assertIn("Skutečný burzovní kapitál a PnL: NEOVĚŘENO", report)
        self.assertIn("T13: Basis & Funding Carry", report)
        self.assertIn("T14: Triangular FX Dislocation", report)
        self.assertIn("T15: MiCA Cross-Basis Carry — stará evidence ZNEPLATNĚNA", report)
        self.assertIn("Reconcile: NEOVĚŘENO", report)
        self.assertNotIn("MATCHED_IN_TOLERANCE", report)
        self.assertNotIn("Všechny testy OK", report)
        self.assertNotIn("0% poplatek", report)
        self.assertIn("AGY CLI", report)
        self.assertIn(narrative, report)

    def test_legacy_t15_does_not_become_live_qualification(self):
        report = reporter.build_full_report({'strategies': {'t15_cross_basis': {
            'current_equity_usd': 999999, 'total_trades': 99999,
            'started_at': '2020-01-01T00:00:00+00:00'}}}, '', 'DETERMINISTIC')
        self.assertNotIn('999999', report)
        self.assertNotIn('99999', report)
        self.assertNotIn('/30', report)
        self.assertIn('ZNEPLATNĚNA', report)

    def test_commentary_does_not_launch_agents(self):
        with patch.object(reporter.cascade_runner, 'execute_cascade', side_effect=AssertionError('agent')):
            text, source = reporter.generate_ai_narrative({})
        self.assertEqual(source, 'DETERMINISTIC')
        self.assertIn('samostatné ověření', text)

    @patch("generate_rich_report.run_query")
    def test_save_report_to_outbox(self, mock_run_query):
        mock_run_query.return_value = "42\nINSERT 0 1"
        report_id = reporter.save_report_to_outbox("Sample report text", {"key": "val"})
        self.assertEqual(report_id, 42)
        mock_run_query.assert_called_once()
        self.assertIn("INSERT INTO reports", mock_run_query.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
