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
        self.assertIn("BEROUN RANNÍ EXECUTIVE REPORT", report)
        self.assertIn("STAV SYSTÉMU", report)
        self.assertIn("Celkový kapitál", report)
        self.assertIn("sats", report)
        self.assertIn("T13: Basis & Funding Carry", report)
        self.assertIn("T14: Triangular FX Dislocation", report)
        self.assertIn("Monitor stability stablecoinů", report)
        self.assertIn("AI TRŽNÍ SYNTÉZA", report)
        self.assertIn("RIZIKOVÝ PERIMETR & INVARIANTY (§14)", report)
        self.assertIn("PŮVOD DAT (§14) & GLOBÁLNÍ AI HIERARCHIE", report)
        self.assertIn("Claude Code (--yolo)", report)
        self.assertIn("[AGY CLI]", report)
        self.assertIn(narrative, report)

    @patch("generate_rich_report.run_query")
    def test_save_report_to_outbox(self, mock_run_query):
        mock_run_query.return_value = "42\nINSERT 0 1"
        report_id = reporter.save_report_to_outbox("Sample report text", {"key": "val"})
        self.assertEqual(report_id, 42)
        mock_run_query.assert_called_once()
        self.assertIn("INSERT INTO reports", mock_run_query.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
