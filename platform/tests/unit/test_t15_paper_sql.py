"""Opt-in PostgreSQL integration against an explicitly verified private fixture.

No probing or subprocess execution at import. Never targets production.
"""
import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "strategies"))
import paper_t15_daemon as d
from test_t15_paper_accounting import START, HOUR, quotes, event
from t15_paper_accounting import new_state, DataBlocked


class SQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("T15_TEST_PG_ROOT"):
            # The full gate requires integration, rather than silently skipping it.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from postgres_fixture import FixtureClient, OwnedPostgres, clean_environment
            FixtureClient.from_environment().verify_identity()
            owner = OwnedPostgres()
            cls.addClassCleanup(owner.stop)
            client = owner.start()
            client.sql("CREATE DATABASE beroun;")
            env = clean_environment(owner.root)
            env.update(PGHOST=str(owner.root / 'socket'), PGPORT='55439', PGUSER='synthbit_test')
            patcher = patch.dict(os.environ, env, clear=True)
            patcher.start()
            cls.addClassCleanup(patcher.stop)
            root = owner.root
            expected_role = 'synthbit_test'
        else:
            root = Path(os.environ["T15_TEST_PG_ROOT"])
            if (root.is_symlink() or not root.name.startswith("t15-validation-") or
                    root.parent != Path("/tmp") or root.stat().st_uid != os.getuid() or
                    stat.S_IMODE(root.stat().st_mode) != 0o700 or
                    os.environ.get("PGHOST") != str(root / "socket") or
                    os.environ.get("PGPORT") != "55493" or os.environ.get("PGUSER") != "t15_test"):
                raise RuntimeError("Unverified private fixture; refusing connection")
            expected_role = 't15_test'
        observed = json.loads(d.psql("SELECT json_build_object('data',current_setting('data_directory'),"
            "'listen',current_setting('listen_addresses'),'role',current_user,'database',current_database());", fetch=True))
        if observed != {"data": str(root / "data"), "listen": "", "role": expected_role, "database": "beroun"}:
            raise RuntimeError("Fixture identity mismatch")
        cls.runner = d.T15PaperDaemon(clock=lambda: START + HOUR + 600000)
        cls.runner.ensure_schema()
        d.psql("""CREATE TABLE market_klines (src TEXT,symbol TEXT,close NUMERIC,close_time TIMESTAMPTZ);
            INSERT INTO paper_arbitrage_state VALUES ('t15_cross_basis','legacy','{"preserved":true}',now());""")

    def setUp(self):
        # Exact fixture row only; production cannot pass setUpClass identity checks.
        d.psql(f"DELETE FROM paper_arbitrage_state WHERE id='{d.STRATEGY_ID}'; DELETE FROM t15_settled_funding; DELETE FROM market_klines;")
        self.initial = new_state(quotes(START), START)
        self.runner.save_state(self.initial)
        self.saved = self.runner.load_state()

    def test_restart_and_stale_writer(self):
        first = dict(self.saved, revision=1)
        self.runner.save_state(first, expected=self.saved)
        other = d.T15PaperDaemon()
        self.assertEqual(other.load_state()["revision"], 1)
        with self.assertRaises(DataBlocked):
            other.save_state(dict(self.saved, revision=2), expected=self.saved)
        self.assertEqual(other.load_state()["revision"], 1)
        self.assertEqual(other.load_state(legacy=True), {"preserved": True})

    def test_reinitialization_never_overwrites(self):
        with self.assertRaises(DataBlocked):
            self.runner.save_state(dict(self.saved, revision=10))
        self.assertEqual(self.runner.load_state(), self.saved)

    def test_invalid_sql_does_not_look_like_empty_data(self):
        with self.assertRaises(DataBlocked):
            d.psql("SELECT * FROM table_that_does_not_exist;", fetch=True)

    def test_real_tick_with_event_replay(self):
        now = START + HOUR + 600000
        for row in quotes(now):
            d.psql(f"INSERT INTO market_klines VALUES ('{row['src']}','{row['symbol']}',{row['close']},to_timestamp({row['close_ms']}/1000.0));")
        e = event()
        d.psql(f"INSERT INTO t15_settled_funding VALUES ('hyperliquid','BTC',to_timestamp({e['settled_ms']}/1000.0),3600000,0.0001,100000,'{'a'*64}');")
        self.runner.tick()
        for _ in range(3):
            self.runner.tick()
        state = d.T15PaperDaemon().load_state()
        self.assertEqual(float(state["funding_usdc"]), .05)
        self.assertEqual(len(state["funding_events"]), 1)
        self.assertEqual(state["qualification"], "BLOCKED")

    def test_readonly_status(self):
        before = d.psql("SELECT xmin::text FROM paper_arbitrage_state ORDER BY id;", fetch=True)
        self.runner.format_status()
        after = d.psql("SELECT xmin::text FROM paper_arbitrage_state ORDER BY id;", fetch=True)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
