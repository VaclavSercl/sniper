#!/usr/bin/env python3
"""Tests for research/reconcile.py — mock gateway socket + mock DB.

Verifies detection of the 3 diff types: order_missing_on_venue,
fill_missing_in_db, balance_mismatch — plus a clean (ok) case.
"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("reconcile", os.path.join(HERE, "reconcile.py"))
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


# ------------------------------------------------- mock gateway (socket) ---
class MockGateway:
    """Real unix-socket server serving canned JSON responses."""
    def __init__(self, responses):
        self.responses = responses  # list of JSON payloads, one per request
        self.requests = []
        self.path = tempfile.mktemp(suffix=".sock", dir=tempfile.gettempdir())
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(4)

    def start(self):
        def serve():
            while True:
                try:
                    conn, _ = self.srv.accept()
                except OSError:
                    return
                with conn:
                    data = b""
                    while not data.endswith(b"\n"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                    self.requests.append(json.loads(data.decode()))
                    request = self.requests[-1]
                    resp = ({"price": "50000"} if request.get("type") == "ticker"
                            else self.responses[len(self.requests) - 1])
                    conn.sendall((json.dumps(resp) + "\n").encode())
        threading.Thread(target=serve, daemon=True).start()

    def close(self):
        self.srv.close()
        os.unlink(self.path)


def patch_psycopg2_missing(monkey=None):
    """Force the psql fallback path by making 'import psycopg2' fail."""
    class Blocker:
        def find_module(self, name, path=None):
            return self if name == "psycopg2" else None
        def load_module(self, name):
            raise ImportError("psycopg2 blocked in test")
    sys.meta_path.insert(0, Blocker())


# ------------------------------------------------------ mock DB via psql ---
def make_mock_db():
    """Run a real unix-socket-free psql mock: we patch rc._psql_query directly."""
    orders = [
        # open order that IS on venue (no diff expected)
        {"id": "1", "client_order_id": "coid-111", "venue": "binance", "symbol": "BTCUSDT",
         "side": "buy", "type": "limit", "qty": "0.001", "price": "50000", "status": "NEW"},
        # open order NOT on venue -> diff 1
        {"id": "2", "client_order_id": "coid-222", "venue": "binance", "symbol": "BTCUSDT",
         "side": "sell", "type": "limit", "qty": "0.002", "price": "60000", "status": "NEW"},
        # filled order not on venue open list -> fine, not a diff
        {"id": "3", "client_order_id": "coid-333", "venue": "binance", "symbol": "BTCUSDT",
         "side": "buy", "type": "limit", "qty": "0.005", "price": "48000", "status": "FILLED"},
    ]
    fills = [
        {"id": "10", "order_id": "3", "symbol": "BTCUSDT", "side": "buy",
         "venue_fill_id": "998", "qty": "0.005", "price": "48000", "fee": "0.01",
         "fee_currency": "USDT"},
    ]
    return {"orders": orders, "fills": fills, "symbols": {"BTCUSDT"}}


def run_case(name, venue_responses, expected_types):
    db = make_mock_db()
    gw = MockGateway(venue_responses)
    gw.start()
    try:
        client = rc.GatewayClient(sock_path=gw.path, timeout=5)
        venue = rc.fetch_venue_state(client, db["symbols"])
        diffs = rc.reconcile(venue, db)
        types = sorted({d["type"] for d in diffs})
        ok = types == sorted(expected_types)
        print(f"{'PASS' if ok else 'FAIL'} {name}: diffs={types} expected={sorted(expected_types)}")
        return ok
    finally:
        gw.close()


# DB-implied balances (from mock DB): USDT -240.01, BTC +0.005
# Both open orders (coid-111, coid-222) present on venue unless testing for a missing one.
BAL_OK = [{"asset": "USDT", "free": "-240.01", "locked": "0"},
          {"asset": "BTC", "free": "0.005", "locked": "0"}]
ORDERS_OK = [{"clientOrderId": "coid-111", "symbol": "BTCUSDT"},
             {"clientOrderId": "coid-222", "symbol": "BTCUSDT"}]
TRADES_OK = [{"id": "998", "symbol": "BTCUSDT"}]


def test_order_missing_on_venue():
    # venue open orders contain only coid-111 -> coid-222 missing
    return run_case(
        "order_missing_on_venue",
        [dict(balances=BAL_OK), ORDERS_OK[:1], TRADES_OK],
        ["order_missing_on_venue"],
    )


def test_fill_missing_in_db():
    # venue trade 997 not in DB fills; venue balance reflects it -> mismatch too
    return run_case(
        "fill_missing_in_db",
        [
            dict(balances=BAL_OK),
            ORDERS_OK,
            [{"id": "997", "symbol": "BTCUSDT"}, {"id": "998", "symbol": "BTCUSDT"}],
        ],
        # venue balances match DB-implied; only the unrecorded trade is a diff
        ["fill_missing_in_db"],
    )


def test_balance_mismatch():
    # venue USDT off by 10.01 USD vs DB-implied (> tolerance); orders/fills consistent
    return run_case(
        "balance_mismatch",
        [
            {"balances": [{"asset": "USDT", "free": "-230.00", "locked": "0"},
                          {"asset": "BTC", "free": "0.005", "locked": "0"}]},
            ORDERS_OK, TRADES_OK,
        ],
        ["balance_mismatch"],
    )


def test_clean():
    # DB-implied balances match venue exactly; orders/fills consistent
    return run_case(
        "clean_no_diffs",
        [dict(balances=BAL_OK), ORDERS_OK, TRADES_OK],
        [],
    )


def test_balance_within_tolerance():
    # USDT off by exactly 1.0 USD (== tolerance, not >) -> no balance diff
    return run_case(
        "balance_within_tolerance",
        [
            {"balances": [{"asset": "USDT", "free": "-239.01", "locked": "0"},
                          {"asset": "BTC", "free": "0.005", "locked": "0"}]},
            ORDERS_OK, TRADES_OK,
        ],
        ["dust_ignored"],
    )


def test_db_via_psql_mock():
    """Verify the psql fallback path (_db_query -> _psql_query) works end-to-end."""
    orig = rc._psql_query
    def fake_psql(sql):
        if sql.strip().startswith("SELECT id, client_order_id"):
            return [("1", "coid-111", "binance", "BTCUSDT", "buy", "limit",
                     "0.001", "50000", "NEW", "2026-01-01")]
        return [("10", "1", "BTCUSDT", "buy", "998", "0.005", "48000",
                 "0.01", "USDT", "2026-01-01")]
    rc._psql_query = fake_psql
    try:
        st = rc.fetch_db_state()
        ok = (len(st["orders"]) == 1 and len(st["fills"]) == 1
              and st["orders"][0]["client_order_id"] == "coid-111"
              and st["symbols"] == {"BTCUSDT"})
        print(f"{'PASS' if ok else 'FAIL'} db_state_via_psql_mock: {st['symbols']}")
        return ok
    finally:
        rc._psql_query = orig


def main():
    results = [
        test_order_missing_on_venue(),
        test_fill_missing_in_db(),
        test_balance_mismatch(),
        test_clean(),
        test_balance_within_tolerance(),
        test_db_via_psql_mock(),
    ]
    n_pass = sum(results)
    print(f"\n{n_pass}/{len(results)} tests passed")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
