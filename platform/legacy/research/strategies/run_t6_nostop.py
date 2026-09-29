#!/usr/bin/env python3
"""T6 backtest: grid ratchet BEZ stop-lossu (owner mandát 27.08).

Varianta grid_ratchet_trail_v1 s odstraněným trailing stopem na inventář:
- buy mřížka 10 úrovní po 0,5 % pod center
- každý buy se prodává 2 úrovně výš (+~1 %)
- center ratchet jen nahoru (EMA 1440), 1×/24 h
- propad pod celou mřížku = 100 % BTC, ČEKÁ SE (žádná likvidace)
- propad pod mřížku: mřížka se NEpřestavuje dolů (žádný ratchet dolů)

Usage: run_t6_nostop.py            # celý rok 1m dat
"""
import json
import subprocess
import sys
from decimal import Decimal as D

SPACING = D("1.005")
N_LEVELS = 10
CAPITAL = D("1000")
FEE = D("0.00075")     # maker per side
EMA_WINDOW = 1440


def psql_rows(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|",
                        "-c", sql], capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l]


def load_klines():
    rows = psql_rows("SELECT extract(epoch from open_time), open, high, low, close "
                     "FROM market_klines WHERE symbol='BTCUSDT' ORDER BY open_time;")
    return [(float(r[0]), D(r[1]), D(r[2]), D(r[3]), D(r[4])) for r in rows]


def sim(klines):
    st = {"center": klines[0][4], "cash": CAPITAL, "btc": D(0),
          "holdings": {}, "buys": [], "peak": klines[0][4], "ema": klines[0][4],
          "last_grid": klines[0][0], "trades": 0, "realized": D(0),
          "start_price": klines[0][4]}

    def build():
        p = st["center"]
        lv = []
        for _ in range(N_LEVELS):
            p = p / SPACING
            lv.append(p)
        st["buys"] = lv

    build()
    alpha = D(2) / (EMA_WINDOW + 1)

    # tracking
    eq_series = []          # (ts, equity, pct_btc) denní vzorek
    max_dd = D(0)
    eq_peak = D(0)
    days_full_btc = 0
    full_btc_last = False
    min_price_below_grid = None
    below_grid = False

    for ts, o, hi, lo, c in klines:
        st["ema"] = alpha * c + (1 - alpha) * st["ema"]

        # buys
        for i, lvl in enumerate(st["buys"]):
            if str(i) in st["holdings"]:
                continue
            if lo <= lvl:
                cost = st["cash"] / (N_LEVELS - len(st["holdings"]))
                if cost < D("1") or st["cash"] < cost:
                    continue
                qty = cost / lvl * (1 - FEE)
                st["cash"] -= cost
                st["btc"] += qty
                st["holdings"][str(i)] = {"qty": qty, "entry": lvl}
                st["trades"] += 1

        # sells 2 úrovně výš
        for i in list(st["holdings"]):
            hd = st["holdings"][i]
            sp = hd["entry"] * SPACING * SPACING
            if hi >= sp:
                qty = hd["qty"]
                proceeds = qty * sp * (1 - FEE)
                st["cash"] += proceeds
                st["btc"] -= qty
                st["realized"] += proceeds - qty * hd["entry"]
                del st["holdings"][i]
                st["trades"] += 1

        # ratchet denně (jen nahoru), přestaví jen pokud nemáme inventář na úrovni
        if ts - st["last_grid"] >= 86400:
            if st["ema"] > st["center"]:
                st["center"] = st["ema"]
                build()
            st["last_grid"] = ts

        # tracking (denní vzorkování stačí: nový den detekujeme přes ts)
        eq = st["cash"] + st["btc"] * c
        if eq > eq_peak:
            eq_peak = eq
        dd = (eq_peak - eq) / eq_peak if eq_peak > 0 else D(0)
        if dd > max_dd:
            max_dd = dd
        pct_btc = (st["btc"] * c / eq) if eq > 0 else D(0)
        eq_series.append((ts, float(eq), float(pct_btc)))
        full = pct_btc > D("0.999")
        if full and not full_btc_last:
            days_full_btc += 1
        full_btc_last = full
        # pod mřížkou?
        if st["btc"] * c / eq > D("0.999") and lo < st["buys"][-1] if st["buys"] else False:
            below_grid = True

    lp = klines[-1][4]
    eq_final = st["cash"] + st["btc"] * lp
    bh = CAPITAL / st["start_price"] * (1 - FEE)
    return {
        "final_equity_usd": float(eq_final),
        "final_btc": float(st["btc"]),
        "cash_left_usd": float(st["cash"]),
        "trades": st["trades"],
        "realized_usd": float(st["realized"]),
        "buyhold_btc": float(bh),
        "sats_vs_buyhold_pct": float((st["btc"] / bh - 1) * 100) if bh > 0 else None,
        "max_drawdown_pct": float(max_dd * 100),
        "days_entered_full_btc": days_full_btc,
        "start_price": float(st["start_price"]),
        "end_price": float(lp),
    }


def main():
    klines = load_klines()
    res = sim(klines)
    res["bars"] = len(klines)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
