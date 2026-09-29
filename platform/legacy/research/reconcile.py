#!/usr/bin/env python3
"""BEROUN reconcile (§10) — oneshot: compare gateway (venue) state vs local DB.

Gateway: unix socket /run/beroun-gateway/gateway.sock, newline-delimited JSON:
    {"type":"account"} | {"type":"open_orders","symbol":S} | {"type":"my_trades","symbol":S}
DB: PostgreSQL beroun/beroun (peer auth), tables orders, fills.
    psycopg2 if importable, else psql subprocess fallback.

Output: JSON report {"ok":bool,"diffs":[...]} on stdout.
Diffs: order_missing_on_venue, fill_missing_in_db, balance_mismatch.
Balance tolerance: reconcile_tolerance_usd = 1.0 (USD-pegged assets).
Mismatch > tolerance -> ALERT line appended to /var/log/beroun/reconcile.log, exit 1.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

GATEWAY_SOCK = "/run/beroun-gateway/gateway.sock"
LOG_PATH = "/var/log/beroun/reconcile.log"
RECONCILE_TOLERANCE_USD = 1.0  # reconcile_tolerance_usd

DB_NAME = "beroun"
DB_USER = "beroun"

# Assets treated as USD-pegged for the tolerance rule.
USD_ASSETS = {"USDT", "USDC", "USD", "FDUSD", "USD1", "BUSD", "USDS", "TUSD", "USDP"}
BASELINE_TABLE = "reconcile_baseline"  # venue balances at BEROUN epoch (pre-history dust)

OPEN_STATUSES = {"NEW", "PARTIALLY_FILLED"}  # venue-side "open" order statuses


# ---------------------------------------------------------------- gateway ---
class GatewayClient:
    def __init__(self, sock_path=GATEWAY_SOCK, timeout=10.0):
        self.sock_path = sock_path
        self.timeout = timeout

    def request(self, payload):
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.sock_path)
            s.sendall((json.dumps(payload) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            data = json.loads(buf.decode() or "{}")
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"gateway error for {payload!r}: {data['error']}")
            return data
        finally:
            s.close()

    def account(self):
        return self.request({"type": "account"})

    def open_orders(self, symbol):
        r = self.request({"type": "open_orders", "symbol": symbol})
        return r if isinstance(r, list) else r.get("orders", r)

    def my_trades(self, symbol):
        r = self.request({"type": "my_trades", "symbol": symbol})
        return r if isinstance(r, list) else r.get("trades", r)


# --------------------------------------------------------------------- db ---
def _psql_query(sql):
    """Run a query via psql (peer auth), return list of row tuples."""
    p = subprocess.run(
        ["psql", "-U", DB_USER, "-d", DB_NAME, "-A", "-t", "-F", "\x1f", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if p.returncode != 0:
        raise RuntimeError(f"psql failed: {p.stderr.strip()}")
    rows = []
    for line in p.stdout.splitlines():
        if not line:
            continue
        rows.append(tuple(line.split("\x1f")))
    return rows


def _db_query(sql, params=None):
    """Query DB via psycopg2 if available, else psql. Returns list of tuples."""
    try:
        import psycopg2  # noqa
    except ImportError:
        return _psql_query(sql)

    conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return [tuple(str(v) for v in row) for row in cur.fetchall()]
    finally:
        conn.close()


def fetch_db_state(symbols=None):
    """Return {'orders': [...], 'fills': [...], 'symbols': set} from the DB."""
    orders = []
    for r in _db_query(
        "SELECT id, client_order_id, venue, symbol, side, type, qty, price, status, "
        "created_at FROM orders WHERE venue = 'binance'"
    ):
        orders.append({
            "id": r[0], "client_order_id": r[1], "venue": r[2], "symbol": r[3],
            "side": r[4], "type": r[5], "qty": r[6], "price": r[7],
            "status": r[8], "created_at": r[9],
        })
    fills = []
    for r in _db_query(
        "SELECT f.id, f.order_id, o.symbol, o.side, f.venue_fill_id, f.qty, "
        "f.price, f.fee, f.fee_currency, f.filled_at "
        "FROM fills f JOIN orders o ON o.id = f.order_id WHERE o.venue = 'binance'"
    ):
        fills.append({
            "id": r[0], "order_id": r[1], "symbol": r[2], "side": r[3],
            "venue_fill_id": r[4], "qty": r[5], "price": r[6], "fee": r[7],
            "fee_currency": r[8], "filled_at": r[9],
        })
    if symbols is None:
        symbols = {o["symbol"] for o in orders}
    return {"orders": orders, "fills": fills, "symbols": symbols}


# -------------------------------------------------------------- reconcile ---
def compute_expected_balances(db_state):
    """Expected venue balances (quote+base) implied by recorded fills in DB."""
    from decimal import Decimal
    bal = {}

    def add(asset, amt):
        bal[asset] = bal.get(asset, Decimal("0")) + amt

    for f in db_state["fills"]:
        base, quote = _split_symbol(f["symbol"])
        qty = Decimal(f["qty"] or "0")
        price = Decimal(f["price"] or "0")
        fee = Decimal(f["fee"] or "0")
        if f["side"] == "buy":
            add(base, qty)
            add(quote, -(qty * price))
        else:
            add(base, -qty)
            add(quote, qty * price)
        if f.get("fee_currency"):
            add(f["fee_currency"], -fee)
    return {k: v for k, v in bal.items()}


def _split_symbol(symbol):
    for q in ("USDT", "USDC", "FDUSD", "USD1", "BUSD", "BTC", "ETH", "BNB", "USD"):
        if symbol.endswith(q) and len(symbol) > len(q):
            return symbol[: -len(q)], q
    return symbol, ""


def fetch_baseline():
    """Pre-history venue balances (dust etc.) captured once into reconcile_baseline."""
    try:
        rows = _db_query(f"SELECT asset, qty FROM {BASELINE_TABLE}")
        return {r[0]: r[1] for r in rows}
    except RuntimeError:
        return {}


def reconcile(venue_state, db_state, tolerance_usd=RECONCILE_TOLERANCE_USD,
              baseline=None):
    """Pure diff function.

    venue_state: {"balances": {asset: Decimal}, "open_orders": [...],
                  "trades": [...]}  (trades: list of venue trade dicts with 'id')
    db_state:    output of fetch_db_state()
    """
    from decimal import Decimal
    diffs = []

    # --- 1. order in DB but not on venue -------------------------------
    venue_open_ids = {str(o.get("clientOrderId") or o.get("client_order_id") or "")
                      for o in venue_state.get("open_orders", [])}
    for o in db_state["orders"]:
        if o["status"].upper() in OPEN_STATUSES and o["client_order_id"] not in venue_open_ids:
            diffs.append({
                "type": "order_missing_on_venue",
                "detail": {
                    "client_order_id": o["client_order_id"],
                    "symbol": o["symbol"], "side": o["side"],
                    "qty": o["qty"], "status": o["status"],
                },
            })

    # --- 2. fill on venue but not in DB --------------------------------
    db_fill_ids = {str(f["venue_fill_id"]) for f in db_state["fills"] if f["venue_fill_id"]}
    for t in venue_state.get("trades", []):
        tid = str(t.get("id") or t.get("tradeId") or "")
        if tid and tid not in db_fill_ids:
            diffs.append({
                "type": "fill_missing_in_db",
                "detail": {
                    "venue_fill_id": tid,
                    "symbol": t.get("symbol"),
                    "qty": t.get("qty"), "price": t.get("price"),
                },
            })

    # --- 3. balance mismatch (expected = baseline + DB fills history) ----
    # P010 (owner approval 27.08.2026): tolerance reconcile_tolerance_usd se
    # aplikuje na USD hodnotu rozdílu U VŠECH assetů (kurz přes price_map),
    # ne jen USD-stable. Diff pod prahem → dust_ignored (report, ne alert).
    expected = compute_expected_balances(db_state)
    from decimal import Decimal as _D
    baseline = baseline or {}
    price_map = venue_state.get("usd_prices", {})  # asset -> USD price (náhradní ticker)
    for asset in sorted(set(expected) | set(venue_state.get("balances", {}))
                        | set(baseline)):
        exp = expected.get(asset, _D("0")) + _D(str(baseline.get(asset, "0")))
        act = venue_state.get("balances", {}).get(asset, Decimal("0"))
        diff = abs(exp - act)
        if asset in USD_ASSETS:
            diff_usd = diff
        else:
            px = _D(str(price_map.get(asset, 0)))
            diff_usd = diff * px
        tol = Decimal(str(tolerance_usd))
        if diff_usd > tol:
            diffs.append({
                "type": "balance_mismatch",
                "detail": {
                    "asset": asset, "db_expected": str(exp),
                    "venue_actual": str(act), "diff": str(diff),
                    "diff_usd": str(diff_usd),
                    "tolerance": str(tol),
                },
            })
        elif diff_usd > 0:
            diffs.append({
                "type": "dust_ignored",
                "detail": {
                    "asset": asset, "diff": str(diff),
                    "diff_usd": str(diff_usd), "tolerance": str(tol),
                },
            })
    return diffs


# ------------------------------------------------------------------ main ---
def fetch_venue_state(gw, symbols):
    account = gw.account()
    balances = {}
    from decimal import Decimal
    for b in account.get("balances", []):
        total = Decimal(b.get("free", "0")) + Decimal(b.get("locked", "0"))
        balances[b["asset"]] = total
    open_orders, trades = [], []
    for sym in symbols:
        open_orders.extend(gw.open_orders(sym))
        trades.extend(gw.my_trades(sym))
    # P010: USD kurzy pro neUSD assety (tolerance na USD hodnotu diffu)
    # OPTIMALIZACE: jen assety s nenulovym diffem (account ma stovky
    # nulovych balances — ticker dotazy jen tam, kde je co porovnavat)
    usd_prices = {}
    for asset in list(balances):
        if asset in USD_ASSETS or asset == "USD" or balances[asset] == 0:
            continue
        try:
            t = gw.request({"type": "ticker", "symbol": f"{asset}USDT"})
            px = t.get("price")
            if px:
                usd_prices[asset] = px
        except Exception:  # noqa: BLE001 — kurz nedostupny = diff zustava v jednotkach assetu
            pass
    return {"balances": balances, "open_orders": open_orders,
            "trades": trades, "usd_prices": usd_prices}


def write_alert(lines):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with open(LOG_PATH, "a") as fh:
        for ln in lines:
            fh.write(f"ALERT {ts} {ln}\n")


def main():
    try:
        gw = GatewayClient()
        db_state = fetch_db_state()
        venue_state = fetch_venue_state(gw, db_state["symbols"])
        diffs = reconcile(venue_state, db_state, baseline=fetch_baseline())
    except Exception as e:  # noqa
        report = {"ok": False, "diffs": [{"type": "reconcile_error", "detail": {"error": str(e)}}]}
        print(json.dumps(report))
        try:
            write_alert([f"reconcile_error {e}"])
        except OSError:
            pass
        return 1

    report = {"ok": not any(d["type"] != "dust_ignored" for d in diffs),
              "diffs": diffs}
    print(json.dumps(report, default=str))
    hard = [d for d in diffs if d["type"] != "dust_ignored"]
    if hard:
        try:
            write_alert([f"{d['type']} {json.dumps(d['detail'], default=str)}"
                         for d in hard])
        except OSError:
            pass  # log dir not writable: report already on stdout
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
