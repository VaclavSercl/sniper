#!/usr/bin/env python3
"""BEROUN paper grid engine (§9e precursor, L0 — žádné reálné obchody).

Parametry (owner, 27.08.2026):
- kapitál: 1000 USDT (paper)
- geometrická mřížka, spacing 0.5 %, 10 úrovní pod trhem
- center ratchet: posouvá se JEN nahoru (EMA, pokud EMA > center)
- žádný fixní stop-loss; TRAILING STOP na inventář: pokud close klesne pod
  (peak_price_since_entry * (1 - trail_pct)), celý inventář se prodá a grid
  pokračuje s novým centrem. Trail 10 %.
- přepočet mřížky: 1× / 24 h (nebo když center ratchet posune)
- celý kapitál vždy v mřížce (každý buy level = cash / n_levels)

Stav: paper_state tabulka (jsonb). Filly: orders/fills s venue='paper'.
Cena: poslední kline z market_klines (ingest 1m běží).

Usage:
  paper_grid.py tick     # zpracuje klines od posledního ticku, zapíše filly
  paper_grid.py status   # vypíše stav JSON
  paper_grid.py init     # inicializuje stav (pokud prázdný)
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal as D

SPACING_PCT = D("0.5")      # %
N_LEVELS = 10
TRAIL_PCT = D("10")         # % trailing stop na inventář
CAPITAL = D("1000")         # paper USDT
FEE = D("0.00075")          # maker per side (paper simulace)
VENUE = "paper"
EMA_WINDOW = 1440           # 1 den pro center ratchet

STATE_FILE_SQL = "select state from paper_state where id=1"


def psql(sql, fetch=False):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip() if fetch else None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- state ---
def load_state():
    raw = psql(STATE_FILE_SQL, fetch=True)
    if not raw:
        return None
    return json.loads(raw)


def save_state(st):
    st["updated_at"] = now_iso()
    psql(f"update paper_state set state='{json.dumps(st)}', updated_at=now() where id=1")


def init_state(price):
    ts_row = psql("SELECT extract(epoch from max(open_time)) FROM market_klines "
                  "WHERE symbol='BTCUSDT'", fetch=True)
    st = {
        "engine": "grid_ratchet_trail_v1",
        "center": str(price),
        "cash": str(CAPITAL),
        "btc": "0",
        "holdings": {},           # level_index -> {qty, entry}
        "peak_price": str(price),  # pro trailing stop
        "last_grid_ts": float(ts_row) if ts_row else None,
        "last_bar_ts": float(ts_row) if ts_row else None,  # historii NEPŘEHRÁVAT
        "last_price": str(price),
        "trades_total": 0,
        "realized_pnl": "0",
        "started_at": now_iso(),
    }
    build_levels(st)
    psql(f"insert into paper_state (id, engine, state) values (1, '{st['engine']}', "
         f"'{json.dumps(st)}') on conflict (id) do nothing")
    return st


def build_levels(st):
    center = D(st["center"])
    m = D(1) + SPACING_PCT / 100
    lv = []
    p = center
    for _ in range(N_LEVELS):
        p = p / m
        lv.append(str(p))
    st["buys"] = lv
    st["sells"] = {}  # level_index -> sell price (2 úrovně výš)


def sell_price(buy_lvl):
    m = D(1) + SPACING_PCT / 100
    return buy_lvl * m * m


# ----------------------------------------------------------------- tick ---
def fetch_klines(since_ts=None):
    q = ("SELECT extract(epoch from open_time), open, high, low, close "
         "FROM market_klines WHERE symbol='BTCUSDT'")
    if since_ts:
        q += f" AND open_time > '{since_ts}'"
    q += " ORDER BY open_time"
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|", "-c", q],
                       capture_output=True, text=True, check=True)
    out = []
    for line in r.stdout.strip().splitlines():
        ts, o, h, l, c = line.split("|")
        out.append((float(ts), D(o), D(h), D(l), D(c)))
    return out


def record_fill(st, side, qty, price, ts):
    """Zápis do orders/fills jako paper (append-only)."""
    coid = f"paper-grid-{int(ts)}-{side}"
    psql(f"insert into orders (client_order_id, venue, symbol, side, type, qty, price, status) "
         f"values ('{coid}', '{VENUE}', 'BTCUSDT', '{side}', 'limit', {qty}, {price}, 'FILLED') "
         f"on conflict (client_order_id) do nothing")
    oid = psql(f"select id from orders where client_order_id='{coid}'", fetch=True)
    fee = qty * FEE
    psql(f"insert into fills (order_id, venue_fill_id, qty, price, fee, fee_currency, filled_at) "
         f"values ({oid}, '{coid}', {qty}, {price}, {fee}, 'USDT', "
         f"to_timestamp({ts}))")


def tick():
    st = load_state()
    if st is None:
        print(json.dumps({"error": "no state; run init first"}))
        return 1

    since = None
    if st.get("last_bar_ts"):
        since = datetime.fromtimestamp(st["last_bar_ts"], tz=timezone.utc).isoformat()
    bars = fetch_klines(since)
    if not bars:
        print(json.dumps({"ok": True, "note": "no new bars", "price": st["last_price"]}))
        return 0

    ema = D(st.get("ema") or st["center"])
    alpha = D(2) / (EMA_WINDOW + 1)

    for ts, o, hi, lo, c in bars:
        # EMA update
        ema = alpha * c + (1 - alpha) * ema

        # peak pro trailing
        if c > D(st["peak_price"]):
            st["peak_price"] = str(c)

        # trailing stop: close pod peak*(1-trail) a máme inventář -> prodat vše
        trail_lvl = D(st["peak_price"]) * (1 - TRAIL_PCT / 100)
        if D(st["btc"]) > 0 and c <= trail_lvl:
            qty = D(st["btc"])
            proceeds = qty * c * (1 - FEE)
            record_fill(st, "sell", qty, c, ts)
            st["cash"] = str(D(st["cash"]) + proceeds)
            st["btc"] = "0"
            st["holdings"] = {}
            st["trades_total"] += 1
            st["peak_price"] = str(c)  # reset peak
            # grid reset na aktuální cenu (ratchet jen nahoru neplatí po stop-out)
            if c > D(st["center"]):
                st["center"] = str(c)
                build_levels(st)

        # buys
        for idx, lvl_s in enumerate(st["buys"]):
            if str(idx) in st["holdings"]:
                continue
            lvl = D(lvl_s)
            if lo <= lvl:
                cost = D(st["cash"]) / N_LEVELS
                if cost < D("1") or D(st["cash"]) < cost:
                    continue
                qty = cost / lvl * (1 - FEE)
                record_fill(st, "buy", qty, lvl, ts)
                st["cash"] = str(D(st["cash"]) - cost)
                st["btc"] = str(D(st["btc"]) + qty)
                st["holdings"][str(idx)] = {"qty": str(qty), "entry": lvl_s}
                st["trades_total"] += 1

        # sells (2 úrovně výš)
        for idx in list(st["holdings"]):
            hd = st["holdings"][idx]
            sp = sell_price(D(hd["entry"]))
            if hi >= sp:
                qty = D(hd["qty"])
                proceeds = qty * sp * (1 - FEE)
                record_fill(st, "sell", qty, sp, ts)
                st["cash"] = str(D(st["cash"]) + proceeds)
                st["btc"] = str(D(st["btc"]) - qty)
                realized = proceeds - qty * D(hd["entry"])
                st["realized_pnl"] = str(D(st["realized_pnl"]) + realized)
                del st["holdings"][idx]
                st["trades_total"] += 1

        st["last_price"] = str(c)
        st["last_bar_ts"] = ts

        # denní přepočet / ratchet (jen nahoru)
        last_grid = st.get("last_grid_ts") or 0
        if ts - last_grid >= 86400:
            if ema > D(st["center"]):
                st["center"] = str(ema)
                build_levels(st)
                st["last_grid_ts"] = ts
            else:
                st["last_grid_ts"] = ts  # pokus o rebuild i tak (počítá se den)

    st["ema"] = str(ema)
    save_state(st)
    eq = D(st["cash"]) + D(st["btc"]) * D(st["last_price"])
    print(json.dumps({
        "ok": True, "price": st["last_price"], "cash": st["cash"],
        "btc": st["btc"], "equity_usdt": str(eq),
        "pnl_pct": str((eq / CAPITAL - 1) * 100),
        "trades_total": st["trades_total"], "bars_processed": len(bars),
    }))
    return 0


def status():
    st = load_state()
    print(json.dumps(st, indent=2, default=str))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "init":
        price = fetch_klines()[-1][4]  # poslední close
        print(json.dumps(init_state(price), default=str))
    elif cmd == "tick":
        sys.exit(tick())
    else:
        status()
