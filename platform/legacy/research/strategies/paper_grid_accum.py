#!/usr/bin/env python3
"""BEROUN paper grid ACCUM engine (owner mandate 27.08: akumulovat BTC, sats jako
numéraire). L0 — venue=paper, žádné reálné obchody.

Mandát (owner): jedeme-li dolů a nakupujeme BTC, je to výhoda — trailing stop
na inventář NEexistuje (prodávat nízko = realizovat sats ztrátu). Selly NEEXISTUJÍ
(čistá akumulace, ne grid trading).

Parametry:
- kapitál: 1000 USDT (paper)
- geometrická buy mřížka, spacing 0.5 %, 10 úrovní pod trhem
- center ratchet: jen nahoru (EMA 1440), přepočet 1×/24 h
- VÝSTUP NENÍ — nakoupené BTC zůstávají (accumulation)
- metrika: nasbírané sats vs buy-and-hold benchmark (1000 USDT @ start cena)

Stav: paper_state_accum (jsonb, id=1).
Usage: paper_grid_accum.py init | tick | status
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal as D

SPACING_PCT = D("0.5")
N_LEVELS = 10
CAPITAL = D("1000")
FEE = D("0.001")            # taker (accum engine nakupuje market/limit bez
                            # prodejní protiváhy — konzervativně taker)
EMA_WINDOW = 1440
TABLE = "paper_state_accum"

SCHEMA = (f"CREATE TABLE IF NOT EXISTS {TABLE} (id int PRIMARY KEY CHECK (id=1), "
          "engine text NOT NULL, state jsonb NOT NULL, "
          "updated_at timestamptz NOT NULL DEFAULT now());")


def psql(sql, fetch=False):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip() if fetch else None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_state():
    raw = psql(f"select state from {TABLE} where id=1", fetch=True)
    return json.loads(raw) if raw else None


def save_state(st):
    st["updated_at"] = now_iso()
    psql(f"update {TABLE} set state='{json.dumps(st)}', updated_at=now() where id=1")


def build_levels(st):
    m = D(1) + SPACING_PCT / 100
    p = D(st["center"])
    lv = []
    for _ in range(N_LEVELS):
        p = p / m
        lv.append(str(p))
    st["buys"] = lv


def fetch_klines(since_ts=None):
    q = ("SELECT extract(epoch from open_time), open, high, low, close "
         "FROM market_klines WHERE symbol='BTCUSDT'")
    if since_ts:
        q += f" AND open_time > '{since_ts}'"
    q += " ORDER BY open_time"
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|",
                        "-c", q], capture_output=True, text=True, check=True)
    out = []
    for line in r.stdout.strip().splitlines():
        ts, o, h, lo, c = line.split("|")
        out.append((float(ts), D(o), D(h), D(lo), D(c)))
    return out


def record_fill(st, qty, price, ts):
    coid = f"paper-accum-{int(ts)}-buy"
    psql(f"insert into orders (client_order_id, venue, symbol, side, type, qty, "
         f"price, status) values ('{coid}', 'paper', 'BTCUSDT', 'buy', 'limit', "
         f"{qty}, {price}, 'FILLED') on conflict (client_order_id) do nothing")
    oid = psql(f"select id from orders where client_order_id='{coid}'", fetch=True)
    fee = qty * FEE / (1 - FEE)  # fee v BTC (Binance: fee odebírá z base)
    psql(f"insert into fills (order_id, venue_fill_id, qty, price, fee, "
         f"fee_currency, filled_at) values ({oid}, '{coid}', {qty}, {price}, "
         f"{fee}, 'BTC', to_timestamp({ts}))")


def init_state(price):
    psql(SCHEMA)
    ts_row = psql("SELECT extract(epoch from max(open_time)) FROM market_klines "
                  "WHERE symbol='BTCUSDT'", fetch=True)
    st = {
        "engine": "paper_grid_accum_v1",
        "center": str(price),
        "cash": str(CAPITAL),
        "btc": "0",
        "fees_btc": "0",
        "start_price": str(price),       # benchmark buy-and-hold
        "buys_total": 0,
        "last_grid_ts": float(ts_row) if ts_row else None,
        "last_bar_ts": float(ts_row) if ts_row else None,  # historii NEPŘEHRÁVAT
        "last_price": str(price),
        "started_at": now_iso(),
    }
    build_levels(st)
    psql(f"insert into {TABLE} (id, engine, state) values (1, '{st['engine']}', "
         f"'{json.dumps(st)}') on conflict (id) do nothing")
    return st


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
        print(json.dumps({"ok": True, "note": "no new bars",
                          "price": st["last_price"]}))
        return 0

    ema = D(st.get("ema") or st["center"])
    alpha = D(2) / (EMA_WINDOW + 1)

    for ts, o, hi, lo, c in bars:
        ema = alpha * c + (1 - alpha) * ema

        # buys — každá úroveň maximálně jednou (fill → smazána z nabídky)
        for idx, lvl_s in enumerate(st["buys"]):
            lvl = D(lvl_s)
            if lo <= lvl:
                budget = D(st["cash"]) / (N_LEVELS - len(st.get("filled", [])))
                budget = min(budget, D(st["cash"]))
                if budget < D("1"):
                    continue
                qty = budget / lvl * (1 - FEE)
                record_fill(st, qty, lvl, ts)
                st["cash"] = str(D(st["cash"]) - budget)
                st["btc"] = str(D(st["btc"]) + qty)
                st["fees_btc"] = str(D(st["fees_btc"]) + budget / lvl * FEE / (1 - FEE))
                st["buys_total"] = st.get("buys_total", 0) + 1
                st.setdefault("filled", []).append(
                    {"level": lvl_s, "qty": str(qty), "ts": ts})
                # odeber naplněnou úroveň z nabídky (ne kupovat znovu stejnou)
                st["buys"] = [b for b in st["buys"] if b != lvl_s]

        st["last_price"] = str(c)
        st["last_bar_ts"] = ts

        # denní ratchet (jen nahoru) — doplní buy úrovně směrem nahoru
        last_grid = st.get("last_grid_ts") or 0
        if ts - last_grid >= 86400:
            if ema > D(st["center"]):
                st["center"] = str(ema)
                build_levels(st)
                # znovu-vykrýt jen úrovně nad aktuální nabídkou
                have = set(st["buys"])
                for f in st.get("filled", []):
                    have.discard(f["level"])
                st["buys"] = sorted(have, key=D, reverse=True)[:N_LEVELS]
            st["last_grid_ts"] = ts

    st["ema"] = str(ema)
    save_state(st)

    # metriky: sats vs buy-and-hold
    btc = D(st["btc"])
    sp = D(st["start_price"])
    lp = D(st["last_price"])
    bh_btc = CAPITAL / sp * (1 - FEE)
    out = {
        "ok": True, "price": st["last_price"], "cash": st["cash"],
        "btc_accum": str(btc), "buys_total": st["buys_total"],
        "fees_btc": st["fees_btc"],
        "usd_equity": str(D(st["cash"]) + btc * lp),
        "bench_buyhold_btc": str(bh_btc),
        "sats_vs_buyhold_pct": str((btc / bh_btc - 1) * 100) if bh_btc > 0 else None,
        "bars_processed": len(bars),
    }
    print(json.dumps(out))
    return 0


def status():
    st = load_state()
    print(json.dumps(st, indent=2, default=str))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "init":
        price = fetch_klines()[-1][4]
        print(json.dumps(init_state(price), default=str))
    elif cmd == "tick":
        sys.exit(tick())
    else:
        status()
