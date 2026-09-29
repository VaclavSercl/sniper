#!/usr/bin/env python3
"""T6b backtest: grid s oboustranným ratchetem center (nahoru I dolů),
trailing stop POUZE nahoru (prodává zisky), žádný stop-loss dolů.

Mandát ownera 27.08: mřížka normálně funguje (nákup+prodej), ale propad
pod celou mřížku = 100 % BTC a čekání; navíc mřížka se smí přestavovat
DOLŮ (ratchet dolů), aby nakupovala nový cenový režim.

Parametry gridu: spacing 0,5 %, 10 úrovní, sell +2 úrovně, EMA 1440.
Ratchet dolů: EMA < center * (1 - reentry_hysteresis) → center = EMA
(hystereze zabraňuje whipsaw přestavování sem a tam).

Varianta T6b-plus navíc: trailing stop nahoru — pokud close > peak cen
holdings a poklesne o trail_pct pod peak EQUITY scaled... Zjednodušení dle
mandátu: trailing stop = prodej inventáře při poklesu pod (peak_price *
(1-trail)) POUZE pokud holdings jsou v plusu (vstupní cena < akt. cena)?
NE — owner: stop jen nahoru při zisku. Implementace: trailing stop se
aktivuje jen tehdy, když průměrný vstup holdings < center (tj. nakoupili
jsme níž a jeli nahoru); při poklesu o trail% z peak prodáme ZISK (část
inventáře odpovídající holdings) a pokračujeme.

Pro čistotu testu: dva režimy v jednom běhu — 'grid_only' (T6b) a
'grid_trail' (T6b+trailing nahoru), report obou.
"""
import json
import subprocess
import sys
from decimal import Decimal as D

SPACING = D("1.005")
N_LEVELS = 10
CAPITAL = D("1000")
FEE = D("0.00075")
EMA_WINDOW = 1440
RATCHET_DOWN_HYST = D("0.02")   # EMA musí být 2 % pod center, jinak whipsaw
TRAIL_PCT = D("10")             # trailing stop nahoru (jen na ziskových pozicích)


def psql_rows(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|",
                        "-c", sql], capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l]


def load_klines():
    rows = psql_rows("SELECT extract(epoch from open_time), open, high, low, close "
                     "FROM market_klines WHERE symbol='BTCUSDT' ORDER BY open_time;")
    return [(float(r[0]), D(r[1]), D(r[2]), D(r[3]), D(r[4])) for r in rows]


def sim(klines, use_trailing_up):
    st = {"center": klines[0][4], "cash": CAPITAL, "btc": D(0),
          "holdings": {}, "buys": [], "peak": klines[0][4], "ema": klines[0][4],
          "last_grid": klines[0][0], "trades": 0, "realized": D(0),
          "start_price": klines[0][4], "ratchets_up": 0, "ratchets_down": 0,
          "trail_exits": 0}

    def build():
        p = st["center"]
        lv = []
        for _ in range(N_LEVELS):
            p = p / SPACING
            lv.append(p)
        st["buys"] = lv

    build()
    alpha = D(2) / (EMA_WINDOW + 1)
    max_dd = D(0); eq_peak = D(0)

    for ts, o, hi, lo, c in klines:
        st["ema"] = alpha * c + (1 - alpha) * st["ema"]

        # buys
        for i, lvl in enumerate(st["buys"]):
            if str(i) in st["holdings"]:
                continue
            if lo <= lvl:
                free = N_LEVELS - len(st["holdings"])
                if free <= 0:
                    continue
                cost = st["cash"] / free
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

        # trailing stop NAHORU (jen ziskové: avg entry < cena, jsme nad mřížkou)
        if use_trailing_up and st["holdings"]:
            if c > st["peak"]:
                st["peak"] = c
            avg_entry = sum(h["qty"] * h["entry"] for h in st["holdings"].values()) / \
                sum(h["qty"] for h in st["holdings"].values())
            trail_lvl = st["peak"] * (1 - TRAIL_PCT / 100)
            if c <= trail_lvl and avg_entry < c:
                qty = st["btc"]
                proceeds = qty * c * (1 - FEE)
                st["cash"] += proceeds
                st["realized"] += proceeds - sum(
                    h["qty"] * h["entry"] for h in st["holdings"].values())
                st["btc"] = D(0)
                st["holdings"] = {}
                st["trades"] += 1
                st["trail_exits"] += 1
                st["peak"] = c
        else:
            if c > st["peak"]:
                st["peak"] = c

        # denní ratchet NAHORU i DOLŮ (s hysterezí)
        if ts - st["last_grid"] >= 86400:
            if st["ema"] > st["center"]:
                st["center"] = st["ema"]
                build()
                st["ratchets_up"] += 1
            elif st["ema"] < st["center"] * (1 - RATCHET_DOWN_HYST):
                st["center"] = st["ema"]
                build()
                st["ratchets_down"] += 1
            st["last_grid"] = ts

        eq = st["cash"] + st["btc"] * c
        if eq > eq_peak:
            eq_peak = eq
        dd = (eq_peak - eq) / eq_peak if eq_peak > 0 else D(0)
        if dd > max_dd:
            max_dd = dd

    lp = klines[-1][4]
    eq_final = st["cash"] + st["btc"] * lp
    bh = CAPITAL / st["start_price"] * (1 - FEE)
    return {
        "final_equity_usd": float(eq_final),
        "final_btc": float(st["btc"]),
        "cash_left_usd": float(st["cash"]),
        "trades": st["trades"],
        "realized_usd": float(st["realized"]),
        "ratchets_up": st["ratchets_up"],
        "ratchets_down": st["ratchets_down"],
        "trail_exits": st["trail_exits"],
        "buyhold_btc": float(bh),
        "sats_vs_buyhold_pct": float((st["btc"] / bh - 1) * 100) if bh > 0 else None,
        "max_drawdown_pct": float(max_dd * 100),
        "start_price": float(st["start_price"]),
        "end_price": float(lp),
    }


def main():
    klines = load_klines()
    out = {
        "bars": len(klines),
        "T6b_grid_only_no_stop": sim(klines, use_trailing_up=False),
        "T6b_grid_trailing_up": sim(klines, use_trailing_up=True),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
