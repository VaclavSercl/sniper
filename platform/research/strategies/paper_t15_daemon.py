#!/usr/bin/env python3
"""T15 research paper accounting, schema v2. Never submits exchange orders.

Explicit init creates a SEPARATE epoch; tick never initializes or migrates legacy
results. status is read-only. Requires settled-event evidence, not rate polling.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

STRATEGIES_DIR = Path(__file__).resolve().parent
if str(STRATEGIES_DIR) not in sys.path:
    sys.path.insert(0, str(STRATEGIES_DIR))
from t15_paper_accounting import DataBlocked, INSTRUMENTS, evaluate_tick, new_state, validate_state

STRATEGY_ID = "t15_cross_basis_v2"
LEGACY_ID = "t15_cross_basis"
STRATEGY_NAME = "T15_mica_cross_basis"
TARGET_PAPER_DAYS = 30
TARGET_PAPER_TRADES = 100


def psql(sql, fetch=False):
    """Use caller's DB role; never silently escalate with sudo.

    ON_ERROR_STOP is essential. SQL on stdin avoids state in process arguments.
    """
    try:
        result = subprocess.run(
            ["psql", "-X", "-w", "-q", "-v", "ON_ERROR_STOP=1", "-d", "beroun", "-t", "-A"],
            input="SET standard_conforming_strings=on; SET statement_timeout='15s'; " + sql,
            capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DataBlocked("Database execution unavailable or outcome uncertain; reload before retry") from exc
    if result.returncode:
        raise DataBlocked("Database operation failed; state was not confirmed")
    return result.stdout.strip() if fetch else None


def literal(value):
    return "'" + json.dumps(value, allow_nan=False, sort_keys=True).replace("'", "''") + "'::jsonb"


class T15PaperDaemon:
    def __init__(self, clock=None):
        self.clock = clock or (lambda: time.time_ns() // 1000000)

    def ensure_schema(self):
        """Only invoked by explicit init; never from construction/status/tick."""
        psql("""
        BEGIN;
        CREATE TABLE IF NOT EXISTS paper_arbitrage_state (
            id TEXT PRIMARY KEY, strategy TEXT NOT NULL, state JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
        CREATE TABLE IF NOT EXISTS t15_settled_funding (
            venue TEXT NOT NULL, symbol TEXT NOT NULL, settled_at TIMESTAMPTZ NOT NULL,
            interval_ms BIGINT NOT NULL CHECK (interval_ms = 3600000),
            rate NUMERIC NOT NULL, oracle_price NUMERIC NOT NULL CHECK (oracle_price > 0),
            evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
            PRIMARY KEY (venue, symbol, settled_at));
        COMMIT;
        """)

    def load_state(self, legacy=False):
        strategy_id = LEGACY_ID if legacy else STRATEGY_ID
        raw = psql(f"BEGIN READ ONLY; SELECT state FROM paper_arbitrage_state WHERE id='{strategy_id}'; COMMIT;", fetch=True)
        if not raw:
            return None
        try:
            state = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise DataBlocked("Corrupt persisted state; refusing automatic reset") from exc
        if not isinstance(state, dict):
            raise DataBlocked("Persisted state must be an object")
        return state

    def save_state(self, state, expected=None):
        payload = literal(state)
        if expected is None:
            sql = f"""INSERT INTO paper_arbitrage_state (id,strategy,state)
                VALUES ('{STRATEGY_ID}','{STRATEGY_NAME}',{payload})
                ON CONFLICT (id) DO NOTHING RETURNING id;"""
        else:
            sql = f"""UPDATE paper_arbitrage_state SET state={payload}, updated_at=now()
                WHERE id='{STRATEGY_ID}' AND state={literal(expected)} RETURNING id;"""
        if psql(sql, fetch=True) != STRATEGY_ID:
            raise DataBlocked("Concurrent state change or existing epoch; reload, do not overwrite")

    def load_market(self, now_ms):
        pairs = ",".join(f"('{src}','{symbol}')" for src, symbol in INSTRUMENTS)
        raw = psql(f"""BEGIN READ ONLY;
            SELECT coalesce(json_agg(q), '[]'::json) FROM (
                SELECT DISTINCT ON (src,symbol) src,symbol,close::text,
                       (extract(epoch from close_time)*1000)::bigint AS close_ms
                FROM market_klines WHERE (src,symbol) IN ({pairs})
                AND close_time <= to_timestamp({int(now_ms)}/1000.0)
                ORDER BY src,symbol,close_time DESC
            ) q; COMMIT;""", fetch=True)
        return self.parse_rows(raw)

    @staticmethod
    def parse_rows(raw):
        try:
            rows = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise DataBlocked("Malformed market evidence") from exc
        if not isinstance(rows, list):
            raise DataBlocked("Market evidence must be an array")
        return rows

    def load_funding(self, state, now_ms):
        # market_funding contains live context polls, NOT settled oracle evidence.
        start = int(state["started_ms"])
        raw = psql(f"""BEGIN READ ONLY;
            SELECT coalesce(json_agg(q), '[]'::json) FROM (
                SELECT venue,symbol,(extract(epoch from settled_at)*1000)::bigint AS settled_ms,
                    interval_ms,rate::text,oracle_price::text,evidence_sha256
                FROM t15_settled_funding WHERE venue='hyperliquid' AND symbol='BTC'
                    AND settled_at > to_timestamp({start}/1000.0)
                    AND settled_at <= to_timestamp({int(now_ms)}/1000.0)
                ORDER BY settled_at LIMIT 5001
            ) q; COMMIT;""", fetch=True)
        return self.parse_rows(raw)

    def init_state(self, initial_capital=1000):
        now = self.clock()
        state = new_state(self.load_market(now), now, initial_capital)
        self.ensure_schema()
        self.save_state(state)
        return state

    def tick(self):
        state = self.load_state()
        if state is None:
            raise DataBlocked("New v2 epoch is not initialized; explicit init required; legacy preserved")
        validate_state(state)
        now = self.clock()
        updated = evaluate_tick(state, self.load_market(now), self.load_funding(state, now), now)
        self.save_state(updated, expected=state)
        return updated

    def format_status(self):
        state = self.load_state()
        if state is None:
            legacy = self.load_state(legacy=True)
            return ("BLOCKED: v2 epoch not initialized. " +
                    ("Legacy T15 results exist and are UNVALIDATED; preserved unchanged." if legacy else "No prior state."))
        validate_state(state)
        now = self.clock()
        stale = now - state["last_tick_ms"] > 180000
        freshness = "STALE" if stale else "RECENT"
        age_days = (now - state["started_ms"]) / 86400000
        lines = ["T15 v2 — RESEARCH_UNQUALIFIED; no verified executions",
                 f"Qualification: BLOCKED ({state['qualification_reason']}); data: {freshness}",
                 f"Elapsed: {age_days:.2f} days / required {TARGET_PAPER_DAYS}",
                 "Hypothetical equity (not realized profit): " + str(state["current_equity_usd"]),
                 "Settled funding model USDC: " + state["funding_usdc"],
                 f"Verified executions: 0 / required {TARGET_PAPER_TRADES}"]
        for name, verdict in state["falsification_gates"].items():
            lines.append(f"[{('UNKNOWN' if stale else verdict)}] {name}")
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "tick", "status"], nargs="?", default="status")
    parser.add_argument("--capital", type=float, default=1000)
    args = parser.parse_args()
    runner = T15PaperDaemon()
    try:
        if args.action == "init":
            runner.init_state(args.capital)
        elif args.action == "tick":
            runner.tick()
        print(runner.format_status())
        return 0
    except (DataBlocked, ValueError, KeyError) as exc:
        print(f"T15 BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
