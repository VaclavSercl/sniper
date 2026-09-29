#!/usr/bin/env python3
"""
SynthBit Universal Development Harness · Autonomous Cascade Runner Tests
Verifies:
  1. Cascade tier order: Codex -> Claude -> AGY -> Hermes.
  2. Fallback on quota exhaustion / rate limit / missing auth.
  3. Badge formatting and metadata generation.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import autonomous_cascade_runner as runner


class TestAutonomousCascadeRunner(unittest.TestCase):
    @patch("autonomous_cascade_runner.run_tier1_codex")
    def test_tier1_codex_success(self, mock_tier1):
        mock_tier1.return_value = (True, "Codex Response", "Codex executed successfully")

        resp, tier, meta = runner.execute_cascade("Test Prompt")
        self.assertEqual(tier, "Codex CLI")
        self.assertEqual(resp, "Codex Response")
        self.assertEqual(meta["winning_tier"], "Codex CLI")
        self.assertEqual(len(meta["tiers_attempted"]), 1)

    @patch("autonomous_cascade_runner.run_tier1_codex")
    @patch("autonomous_cascade_runner.run_tier2_claude")
    def test_tier2_claude_fallback_when_codex_fails(self, mock_tier2, mock_tier1):
        mock_tier1.return_value = (False, "", "Codex error: Usage limit or quota exceeded")
        mock_tier2.return_value = (True, "Claude Response", "Claude executed successfully")

        resp, tier, meta = runner.execute_cascade("Test Prompt")
        self.assertEqual(tier, "Claude Code CLI")
        self.assertEqual(resp, "Claude Response")
        self.assertEqual(meta["winning_tier"], "Claude Code CLI")
        self.assertEqual(len(meta["tiers_attempted"]), 2)
        self.assertEqual(meta["tiers_attempted"][0]["tier"], "Codex CLI")
        self.assertFalse(meta["tiers_attempted"][0]["success"])
        self.assertEqual(meta["tiers_attempted"][1]["tier"], "Claude Code CLI")
        self.assertTrue(meta["tiers_attempted"][1]["success"])

    @patch("autonomous_cascade_runner.run_tier1_codex")
    @patch("autonomous_cascade_runner.run_tier2_claude")
    @patch("autonomous_cascade_runner.run_tier3_agy")
    def test_tier3_agy_fallback_when_codex_and_claude_fail(self, mock_tier3, mock_tier2, mock_tier1):
        mock_tier1.return_value = (False, "", "Codex error: Usage limit or quota exceeded")
        mock_tier2.return_value = (False, "", "Claude error: Missing login / auth or rate limit")
        mock_tier3.return_value = (True, "AGY Response", "AGY executed successfully")

        resp, tier, meta = runner.execute_cascade("Test Prompt", show_tier_badge=True)
        self.assertEqual(tier, "AGY CLI")
        self.assertTrue(resp.startswith("[AGY CLI]\n"))
        self.assertIn("AGY Response", resp)
        self.assertEqual(len(meta["tiers_attempted"]), 3)

    @patch("autonomous_cascade_runner.run_tier1_codex")
    @patch("autonomous_cascade_runner.run_tier2_claude")
    @patch("autonomous_cascade_runner.run_tier3_agy")
    @patch("autonomous_cascade_runner.run_tier4_hermes")
    def test_tier4_hermes_safety_net(self, mock_tier4, mock_tier3, mock_tier2, mock_tier1):
        mock_tier1.return_value = (False, "", "Codex error: Usage limit or quota exceeded")
        mock_tier2.return_value = (False, "", "Claude error: Missing login / auth or rate limit")
        mock_tier3.return_value = (False, "", "AGY error: Rate limit / API error")
        mock_tier4.return_value = (True, "Hermes Response", "Hermes executed successfully")

        resp, tier, meta = runner.execute_cascade("Test Prompt")
        self.assertEqual(tier, "Hermes Agent")
        self.assertEqual(resp, "Hermes Response")
        self.assertEqual(len(meta["tiers_attempted"]), 4)

    @patch("autonomous_cascade_runner.run_tier1_codex")
    @patch("autonomous_cascade_runner.run_tier2_claude")
    @patch("autonomous_cascade_runner.run_tier3_agy")
    @patch("autonomous_cascade_runner.run_tier4_hermes")
    def test_all_tiers_failed_returns_diagnostic_summary(self, mock_tier4, mock_tier3, mock_tier2, mock_tier1):
        mock_tier1.return_value = (False, "", "Codex error: Usage limit")
        mock_tier2.return_value = (False, "", "Claude error: Not logged in")
        mock_tier3.return_value = (False, "", "AGY error: Rate limit")
        mock_tier4.return_value = (False, "", "Hermes error: Timeout")

        resp, tier, meta = runner.execute_cascade("Test Prompt")
        self.assertEqual(tier, "NONE")
        self.assertIn("Všechny 4 stupně AI kaskády selhaly", resp)
        self.assertIn("Codex error", resp)
        self.assertIn("Claude error", resp)
        self.assertIn("AGY error", resp)
        self.assertIn("Hermes error", resp)


if __name__ == "__main__":
    unittest.main()
