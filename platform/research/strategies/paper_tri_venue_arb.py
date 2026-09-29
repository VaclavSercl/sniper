#!/usr/bin/env python3
"""
BEROUN Multi-Venue Paper Engine: T13 (Basis Carry) & T14 (Triangular FX)
Executes L0 Shadow paper simulation for both cross-venue arbitrage strategies.
Safe, non-destructive, and isolated from paper_grid.py.

Usage:
  paper_tri_venue_arb.py init     # Initializes starting paper capital ($1,000 / €1,000)
  paper_tri_venue_arb.py tick     # Reads freshest DB ticks, evaluates trades, records PnL
  paper_tri_venue_arb.py status   # Prints current simulated portfolio state
"""

import json
import os
import subprocess
import sys
import time
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATEWAY_DIR = REPO_ROOT / "gateway"
STRATEGIES_DIR = REPO_ROOT / "research" / "strategies"
for p in (GATEWAY_DIR, STRATEGIES_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from execution_router import UnifiedExecutionRouter
from t13_basis_carry import T13BasisCarryEngine
from t14_triangular_fx import T14TriangularFXEngine

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_arbitrage_state (
    id          TEXT PRIMARY KEY,
    strategy    TEXT NOT NULL,
    state       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def psql(sql: str, fetch: bool = False) -> Optional[str]:
    cmd = ["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and ("Peer authentication failed" in r.stderr or "FATAL" in r.stderr):
        cmd = ["sudo", "-u", "beroun", "psql", "-d", "beroun", "-t", "-A", "-c", sql]
        r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql error: {r.stderr.strip()}")
    return r.stdout.strip() if fetch else None


class PaperArbitrageManager:
    def __init__(self):
        self.t13_engine = T13BasisCarryEngine(initial_capital=1000.0)
        self.t14_engine = T14TriangularFXEngine(capital_eur=1000.0)
        self.router = UnifiedExecutionRouter(capability_level="L0")
        self.ensure_schema()

    def ensure_schema(self) -> None:
        psql(SCHEMA)

    def load_state(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        raw = psql(f"SELECT state FROM paper_arbitrage_state WHERE id = '{strategy_id}';", fetch=True)
        if raw:
            return json.loads(raw)
        return None

    def save_state(self, strategy_id: str, strategy_name: str, state: Dict[str, Any]) -> None:
        state_json = json.dumps(state).replace("'", "''")
        psql(
            f"INSERT INTO paper_arbitrage_state (id, strategy, state, updated_at) "
            f"VALUES ('{strategy_id}', '{strategy_name}', '{state_json}', now()) "
            f"ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, updated_at = now();"
        )

    def init_states(self) -> None:
        # T13 State
        t13_init = {
            "strategy": "T13_basis_carry",
            "capital_usd": 1000.0,
            "equity_usd": 1000.0,
            "in_position": False,
            "spot_btc": 0.0,
            "spot_entry_price": 0.0,
            "perp_short_btc": 0.0,
            "perp_entry_price": 0.0,
            "collateral_usdc": 1000.0,
            "accumulated_funding_usd": 0.0,
            "accumulated_rebates_usd": 0.0,
            "total_trades": 0,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        self.save_state("t13_carry", "T13_basis_carry", t13_init)

        # T14 State
        t14_init = {
            "strategy": "T14_triangular_fx",
            "capital_eur": 1000.0,
            "equity_eur": 1000.0,
            "total_trades": 0,
            "captured_bps_total": 0.0,
            "realized_profit_eur": 0.0,
            "in_triangle_position": False,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        self.save_state("t14_triangle", "T14_triangular_fx", t14_init)
        print("[PAPER ARB] Initialized clean paper states for T13 ($1,000) and T14 (€1,000).")

    def tick(self) -> Dict[str, Any]:
        t13 = self.load_state("t13_carry")
        t14 = self.load_state("t14_triangle")
        if not t13 or not t14:
            self.init_states()
            t13 = self.load_state("t13_carry")
            t14 = self.load_state("t14_triangle")

        # 1. Fetch latest ticks
        ticks_raw = psql(
            "SELECT symbol, price FROM ("
            "  SELECT symbol, price, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY ts DESC) as rn "
            "  FROM market_ticks WHERE ts > now() - interval '30 minutes'"
            ") s WHERE rn = 1;",
            fetch=True
        )
        ticks = dict(line.split("|") for line in ticks_raw.splitlines() if "|" in line)

        # 2. Fetch latest funding
        fund_raw = psql(
            "SELECT rate FROM market_funding WHERE symbol LIKE '%BTC%' ORDER BY funding_time DESC LIMIT 1;",
            fetch=True
        )
        latest_hourly_funding = float(fund_raw) if fund_raw else 0.0000125
        annual_funding_apr = latest_hourly_funding * 24 * 365 * 100.0

        spot_btc_price = float(ticks.get("tBTCUSD", 80443.0))
        perp_btc_price = float(ticks.get("BTC-PERP", 80479.5))

        # --- Evaluate T13 (Basis Carry) ---
        eval_t13 = self.t13_engine.evaluate_entry(spot_btc_price, perp_btc_price, annual_funding_apr)
        if not t13["in_position"] and eval_t13["action"] == "ENTER_CARRY":
            spot_ord, perp_ord = self.t13_engine.generate_orders(spot_btc_price, perp_btc_price, t13["capital_usd"])
            res_spot = self.router.route_order(spot_ord)
            res_perp = self.router.route_order(perp_ord)

            qty = spot_ord.quantity
            t13["in_position"] = True
            t13["spot_btc"] = qty
            t13["spot_entry_price"] = spot_btc_price
            t13["perp_short_btc"] = qty
            t13["perp_entry_price"] = perp_btc_price
            t13["collateral_usdc"] = t13["capital_usd"] * 0.50
            # Maker entry rebate on Hyperliquid
            rebate = (qty * perp_btc_price) * 0.0002
            t13["accumulated_rebates_usd"] += rebate
            t13["total_trades"] += 1

        if t13["in_position"]:
            # Accrue hourly funding payment
            f_payment = t13["perp_short_btc"] * perp_btc_price * latest_hourly_funding
            t13["accumulated_funding_usd"] += f_payment

            # Portfolio Mark to Market
            spot_val = t13["spot_btc"] * spot_btc_price
            perp_pnl = t13["perp_short_btc"] * (t13["perp_entry_price"] - perp_btc_price)
            t13["equity_usd"] = spot_val + t13["collateral_usdc"] + perp_pnl + t13["accumulated_funding_usd"] + t13["accumulated_rebates_usd"]

        self.save_state("t13_carry", "T13_basis_carry", t13)

        # --- Evaluate T14 (Triangular FX) ---
        btc_usd = float(ticks.get("tBTCUSD", 80443.0))
        eur_usd = float(ticks.get("tEURUSD", 1.1484))
        direct_btc_eur = float(ticks.get("tBTCEUR", 69972.5))

        opp = self.t14_engine.calculate_dislocation(btc_usd, eur_usd, direct_btc_eur)
        if opp.actionable and not t14["in_triangle_position"]:
            orders = self.t14_engine.generate_triangle_orders(opp, notional_eur=500.0)
            for o in orders:
                self.router.route_order(o)

            # Simulated post-only capture of dislocation
            captured_bps = abs(opp.dislocation_bps) * 0.80 # assume 80% capture on mean-reversion
            profit_eur = 500.0 * (captured_bps / 10000.0)
            t14["total_trades"] += 1
            t14["captured_bps_total"] += captured_bps
            t14["realized_profit_eur"] += profit_eur
            t14["equity_eur"] = t14["capital_eur"] + t14["realized_profit_eur"]

        self.save_state("t14_triangle", "T14_triangular_fx", t14)

        return {
            "t13_carry": {
                "equity_usd": round(t13["equity_usd"], 2),
                "in_position": t13["in_position"],
                "accumulated_funding_usd": round(t13["accumulated_funding_usd"], 4),
                "total_trades": t13["total_trades"]
            },
            "t14_triangle": {
                "equity_eur": round(t14["equity_eur"], 2),
                "realized_profit_eur": round(t14["realized_profit_eur"], 4),
                "captured_bps_total": round(t14["captured_bps_total"], 2),
                "total_trades": t14["total_trades"]
            }
        }


def main():
    mgr = PaperArbitrageManager()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "init":
        mgr.init_states()
    elif cmd == "tick":
        res = mgr.tick()
        print(json.dumps(res, indent=2))
    elif cmd == "status":
        t13 = mgr.load_state("t13_carry")
        t14 = mgr.load_state("t14_triangle")
        print("=" * 80)
        print("   BEROUN MULTI-VENUE ARBITRAGE PAPER ENGINE STATUS (L0 SHADOW)")
        print("=" * 80)
        print("\n[T13 BASIS & FUNDING CARRY]:")
        print(json.dumps(t13, indent=2) if t13 else "Not initialized (run init)")
        print("\n[T14 TRIANGULAR FX DISLOCATION]:")
        print(json.dumps(t14, indent=2) if t14 else "Not initialized (run init)")
        print("=" * 80)
    else:
        print(f"Unknown command '{cmd}'. Use: init, tick, status")


if __name__ == "__main__":
    main()
