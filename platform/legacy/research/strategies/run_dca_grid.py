#!/usr/bin/env python3
"""DCA + grid simulace (owner dotaz 27.08): start 100 USDT, každý měsíc
+100 USDT vkladu. Porovnání: (a) grid T6 nakupuje/prodává z vkladů,
(b) čisté DCA (kup na open první svíčky měsíce, žádné prodeje).

Grid logika = T6 (ratchet jen nahoru, sell +2 úrovně, bez stop-lossu dolů),
spacing 0.5 %, 10 úrovní, fee 0.075 %/side.
"""
import json
import subprocess
from decimal import Decimal as D

SPACING = D("1.005")
N_LEVELS = 10
FEE = D("0.00075")
EMA_WINDOW = 1440
MONTHLY = D("100")


def psql_rows(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|",
                        "-c", sql], capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l]


def load_klines():
    rows = psql_rows("SELECT extract(epoch from open_time), open, high, low, close "
                     "FROM market_klines WHERE symbol='BTCUSDT' ORDER BY open_time;")
    return [(float(r[0]), D(r[1]), D(r[2]), D(r[3]), D(r[4])) for r in rows]


def month_key(ts):
    import datetime
    d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    return (d.year, d.month)


def sim_grid_dca(klines):
    st = {"center": klines[0][4], "cash": D("100"), "btc": D(0), "holdings": {},
          "buys": [], "ema": klines[0][4], "last_grid": klines[0][0],
          "trades": 0, "realized": D(0), "deposited": D("100"),
          "last_month": month_key(klines[0][0])}

    def build():
        p = st["center"]
        lv = []
        for _ in range(N_LEVELS):
            p = p / SPACING
            lv.append(p)
        st["buys"] = lv

    build()
    alpha = D(2) / (EMA_WINDOW + 1)

    for ts, o, hi, lo, c in klines:
        st["ema"] = alpha * c + (1 - alpha) * st["ema"]

        # měsíční vklad
        mk = month_key(ts)
        if mk != st["last_month"]:
            st["cash"] += MONTHLY
            st["deposited"] += MONTHLY
            st["last_month"] = mk

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

        # denní ratchet jen nahoru
        if ts - st["last_grid"] >= 86400:
            if st["ema"] > st["center"]:
                st["center"] = st["ema"]
                build()
            st["last_grid"] = ts

    lp = klines[-1][4]
    return {
        "deposited_usd": float(st["deposited"]),
        "final_equity_usd": float(st["cash"] + st["btc"] * lp),
        "final_btc": float(st["btc"]),
        "cash_left_usd": float(st["cash"]),
        "trades": st["trades"],
        "realized_grid_profit_usd": float(st["realized"]),
        "pnl_usd": float(st["cash"] + st["btc"] * lp - st["deposited"]),
    }


def sim_pure_dca(klines):
    """Čisté spoření: 100 USDT na open první svíčky každého měsíce (včetně startu)."""
    cash = D("0"); btc = D(0); deposited = D("0")
    last_month = None
    for ts, o, hi, lo, c in klines:
        mk = month_key(ts)
        if mk != last_month:
            cash += MONTHLY
            deposited += MONTHLY
            last_month = mk
        # kup okamžitě (market na open)
        if cash >= D("1"):
            qty = cash / o * (1 - FEE)
            btc += qty
            cash = D("0")
    lp = klines[-1][4]
    return {
        "deposited_usd": float(deposited),
        "final_equity_usd": float(cash + btc * lp),
        "final_btc": float(btc),
        "pnl_usd": float(cash + btc * lp - deposited),
    }


def main():
    klines = load_klines()
    out = {
        "bars": len(klines),
        "start": float(klines[0][4]), "end": float(klines[-1][4]),
        "grid_dca": sim_grid_dca(klines),
        "pure_dca": sim_pure_dca(klines),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
