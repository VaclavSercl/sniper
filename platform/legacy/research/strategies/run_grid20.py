#!/usr/bin/env python3
"""GRID-20: 20 grid strategii podle literatury — test na BTCUSDT 1m datech.

Literatura:
- Chen/Chen/Jang (arXiv:2506.11921): E[grid]=0 pro finite grid; DGT reset
- Quantpedia primer: spacing = f(volatilita), range trhy
- arXiv:2211.12839: flexible grid, equal-ratio, adaptivni parametry
- Jia 2022 (EUDL): asymetricke gridy pro BTC
- GTSbot (MDPI 2019): quadratic funkcni adaptace

Spolecne: geometric grid, fee 0.075 %/side, MTM equity, benchmark buy&hold.
Perioda: 2025-08-27 .. 2026-08-27 (kompletni bear rok) — doplnime po rocich.
Metriky: final_btc (sats), final_equity_usd, max_dd, trades, grid_profit.
"""
import json
import subprocess
from decimal import Decimal as D

FEE = D("0.00075")
CAPITAL = D("1000")


def psql_rows(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|",
                        "-c", sql], capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l]


def load_daily():
    rows = psql_rows(
        "SELECT extract(epoch from open_time)::text, high, low, open, close "
        "FROM market_klines WHERE symbol='BTCUSDT' AND open_time >= '2025-08-27' "
        "ORDER BY open_time;")
    return [(r[0], D(r[1]), D(r[2]), D(r[3]), D(r[4])) for r in rows]


class GridSim:
    """Geometricky grid; center/spacing/levels konfigurovatelne; reset dle modu."""

    def __init__(self, daily, spacing=D("0.005"), n_up=5, n_down=5,
                 rebalance_mode="none", reset_mode="stop", trail=None,
                 asym=D(1), vol_adaptive=False, martingale=False,
                 dgt_wallet=False, initial_split="half"):
        self.daily = daily
        self.sp = spacing
        self.n_up, self.n_down = n_up, n_down
        self.rebalance_mode = rebalance_mode
        self.reset_mode = reset_mode   # stop | reset | dgt
        self.trail = trail
        self.asym = asym
        self.vol_adaptive = vol_adaptive
        self.martingale = martingale
        self.dgt_wallet = dgt_wallet
        self.initial_split = initial_split

    def run(self):
        st = {"cash": CAPITAL, "btc": D(0), "wallet": D(0),  # wallet = DGT arbitrage profit
              "trades": 0, "grid_profit": D(0), "center": None,
              "levels": {}, "peak": None, "invested": D(0)}
        max_dd = D(0); eq_peak = D(0)
        first = True
        for date, hi, lo, o, c in self.daily:
            if first:
                self._start_grid(st, o)
                first = False

            # vol-adaptive spacing (denni update)
            if self.vol_adaptive and st["center"]:
                st["vol"] = abs(c / o - 1)
                sp = max(D("0.003"), min(D("0.02"), st["vol"] * 2))
                if abs(sp - self.sp) > D("0.002"):
                    self.sp = sp

            # trail peak update
            if st["peak"] is None or c > st["peak"]:
                st["peak"] = c

            # martingale sizing multiplikator podle ztraty od center
            mult = D(1)
            if self.martingale and st["center"]:
                dd = (st["center"] - c) / st["center"]
                if dd > 0:
                    mult = min(D(3), 1 + dd * 2)

            # buys
            for lvl, qty_pending in list(st["levels"].items()):
                if qty_pending["side"] == "buy" and lo <= D(lvl):
                    cost = min(st["cash"], qty_pending["amt"] * mult)
                    if cost >= D("1"):
                        qty = cost / D(lvl) * (1 - FEE)
                        st["cash"] -= cost
                        st["btc"] += qty
                        qty_pending["filled"] = True
                        st["trades"] += 1
                        st["levels"][lvl] = {"side": "sell",
                                             "amt": cost,
                                             "qty": qty,
                                             "entry": D(lvl)}

            # sells (target = entry * (1+sp)^2)
            for lvl, q in list(st["levels"].items()):
                if q["side"] == "sell" and hi >= D(lvl) * (1 + self.sp) ** 2:
                    proceeds = q["qty"] * D(lvl) * (1 + self.sp) ** 2 * (1 - FEE)
                    st["cash"] += proceeds
                    st["btc"] -= q["qty"]
                    profit = proceeds - q["amt"]
                    st["grid_profit"] += profit
                    if self.dgt_wallet:
                        st["wallet"] += profit  # jen tracking; equity = cash+btc (bez wallet)
                    del st["levels"][lvl]
                    st["trades"] += 1

            # trailing stop (jen ziskove)
            if self.trail and st["btc"] > 0:
                trail_lvl = st["peak"] * (1 - self.trail / 100)
                if c <= trail_lvl:
                    avg_entry = st.get("avg_entry")
                    if avg_entry and avg_entry < c:
                        proceeds = st["btc"] * c * (1 - FEE)
                        st["cash"] += proceeds
                        st["grid_profit"] += proceeds - st["invested"]
                        st["btc"] = D(0)
                        st["levels"] = {}
                        st["peak"] = c
                        st["trades"] += 1

            # reset / stop pri prorazeni limitu
            if st["center"]:
                upper = st["center"] * (1 + self.sp) ** self.n_up
                lower = st["center"] * (1 - self.sp) ** self.n_down
                if c > upper or c < lower:
                    if self.reset_mode == "stop":
                        pass  # grid zastavi (levels vyckavaji navzdy)
                    elif self.reset_mode in ("reset", "dgt"):
                        self._start_grid(st, c)

            # rebalance center (mode: ema | drift | none)
            if st["center"] and self.rebalance_mode != "none":
                if self.rebalance_mode == "ema":
                    st["ema"] = st.get("ema") or c
                    alpha = D(2) / (D(14) + 1)  # 14d EMA
                    st["ema"] = alpha * c + (1 - alpha) * st["ema"]
                    if abs(st["ema"] - st["center"]) / st["center"] > self.sp * 3:
                        self._start_grid(st, st["ema"])
                elif self.rebalance_mode == "drift":
                    if abs(c - st["center"]) / st["center"] > self.sp * 5:
                        self._start_grid(st, c)

            # avg_entry tracking
            if st["btc"] > 0:
                st["invested"] = st.get("invested", D(0))

            eq = st["cash"] + st["btc"] * c
            if eq > eq_peak:
                eq_peak = eq
            dd = (eq_peak - eq) / eq_peak if eq_peak > 0 else D(0)
            if dd > max_dd:
                max_dd = dd

        lp = self.daily[-1][4]
        eq_f = st["cash"] + st["btc"] * lp
        bh = CAPITAL / self.daily[0][4] * (1 - FEE)
        return {
            "final_btc": float(st["btc"]),
            "final_equity_usd": float(eq_f),
            "grid_profit_usd": float(st["grid_profit"]),
            "max_dd_pct": float(max_dd * 100),
            "trades": st["trades"],
            "sats_vs_buyhold_pct": float((st["btc"] / bh - 1) * 100),
        }

    def _start_grid(self, st, price):
        st["center"] = price
        st["levels"] = {}
        # initial split: half = 50/50 spot/cash; allcash = ceka na buy
        if self.initial_split == "half":
            buy_amt = st["cash"] / 2
            qty = buy_amt / price * (1 - FEE)
            st["cash"] -= buy_amt
            st["btc"] += qty
            st["trades"] += 1
            st["avg_entry"] = price
            st["invested"] = buy_amt
        for i in range(1, self.n_down + 1):
            lvl = price * (1 - self.sp) ** i
            st["levels"][str(lvl)] = {"side": "buy",
                                      "amt": st["cash"] / self.n_down / self.asym}
        for i in range(1, self.n_up + 1):
            lvl = price * (1 + self.sp) ** i
            st["levels"][str(lvl)] = {"side": "sell",
                                      "amt": st["cash"] / self.n_up,
                                      "qty": st["btc"] / self.n_up,
                                      "entry": price}


def main():
    daily = load_daily()
    variants = {
        # klasika dle Quantpedia: staticky grid, stop pri prorazeni
        "G01_static_05_5x5": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="stop"),
        "G02_static_10_10x10": GridSim(daily, spacing=D("0.01"), n_up=10, n_down=10, reset_mode="stop"),
        "G03_static_20_5x5": GridSim(daily, spacing=D("0.02"), n_up=5, n_down=5, reset_mode="stop"),
        "G04_static_05_10x10": GridSim(daily, spacing=D("0.005"), n_up=10, n_down=10, reset_mode="stop"),
        # DGT (arXiv 2506.11921): reset gridu pri prorazeni limitu, wallet
        "G05_DGT_reset": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="reset"),
        "G06_DGT_wallet": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="dgt", dgt_wallet=True),
        "G07_DGT_10": GridSim(daily, spacing=D("0.01"), n_up=10, n_down=10, reset_mode="reset"),
        # vol-adaptive (Quantpedia: spacing = f(vol))
        "G08_vol_adaptive": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="reset", vol_adaptive=True),
        # asymetricke (Jia 2022:BTC nahoře hodně, dole málo)
        "G09_asym_20up_2down": GridSim(daily, spacing=D("0.01"), n_up=20, n_down=2, reset_mode="reset"),
        "G10_asym_2up_20down": GridSim(daily, spacing=D("0.01"), n_up=2, n_down=20, reset_mode="reset"),
        # rebalance center (EMA / drift)
        "G11_rebalance_ema": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="stop", rebalance_mode="ema"),
        "G12_rebalance_drift": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="stop", rebalance_mode="drift"),
        # trailing stop
        "G13_trail10": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="reset", trail=D("10")),
        "G14_trail20": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="reset", trail=D("20")),
        # martingale sizing (rizikove, dle Quantpedia martingale nature)
        "G15_martingale": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="reset", martingale=True),
        # siroke gridy (pokryti -40 %)
        "G16_wide_25x25_2pct": GridSim(daily, spacing=D("0.02"), n_up=25, n_down=25, reset_mode="reset"),
        "G17_wide_10x10_1pct_deep": GridSim(daily, spacing=D("0.01"), n_up=10, n_down=10, reset_mode="stop", initial_split="allcash"),
        # GTSbot-style: quadratic levels (nerovnomerne rozlozeni)
        "G18_gtsbot_quadratic": GridSim(daily, spacing=D("0.005"), n_up=8, n_down=8, reset_mode="reset", asym=D("0.5")),
        # flexible grid (arXiv 2211.12839): ema rebalance + vol adaptive + reset
        "G19_flexible": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="reset", vol_adaptive=True, rebalance_mode="ema"),
        # all-cash start (ceka na pokles)
        "G20_allcash_start": GridSim(daily, spacing=D("0.005"), n_up=5, n_down=5, reset_mode="reset", initial_split="allcash"),
    }
    out = {"days": len(daily), "start": float(daily[0][4]), "end": float(daily[-1][4]),
           "variants": {}}
    for name, g in variants.items():
        try:
            out["variants"][name] = g.run()
        except Exception as e:
            out["variants"][name] = {"error": str(e)}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
