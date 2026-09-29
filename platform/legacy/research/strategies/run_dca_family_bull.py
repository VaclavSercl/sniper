#!/usr/bin/env python3
"""A-family: 10 variant okamžitého DCA + hold — test na loňském (bear) roce.

Všechny varianty sdílejí: měsíční vklad 100 USDT ( varianta 2 a 9 mění
velikost dle pravidel), nákup pouze spot BTCUSDT, fee 0.075 %/side,
žádný short, žádný stop-loss. Benchmark = A (plain immediate DCA).
Report: final_btc (sats — primární metrika dle owner numéraire),
final_equity_usd, pnl_usd, trades.
"""
import json
import subprocess
from decimal import Decimal as D
import math

FEE = D("0.00075")
MONTHLY = D("100")


def psql_rows(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|",
                        "-c", sql], capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l]


def load_daily():
    """Denní OHLC agregované z 1m (open první, close poslední, high/low max/min)."""
    rows = psql_rows(
        "SELECT date_trunc('day', open_time), min(low), max(high), "
        "(array_agg(open ORDER BY open_time))[1], (array_agg(close ORDER BY open_time DESC))[1] "
        "FROM market_klines WHERE symbol='BTCUSDT' AND open_time >= '2026-07-01' "
        "GROUP BY 1 ORDER BY 1;")
    return [(r[0][:10], D(r[2]), D(r[1]), D(r[3]), D(r[4])) for r in rows]  # date,high,low,open,close


class Sim:
    """Společný běžec: denní data, month rollover volá on_deposit(s, price)."""
    def __init__(self, daily):
        self.daily = daily
        self.btc = D(0); self.cash = D(0); self.deposited = D(0)
        self.trades = 0; self.realized = D(0)
        self.state = {}

    def buy(self, usd, price):
        if usd >= D("1"):
            self.btc += usd / price * (1 - FEE)
            self.cash -= usd
            self.trades += 1

    def run(self, on_deposit=None, on_day=None):
        last_month = None
        for date, hi, lo, o, c in self.daily:
            m = date[:7]
            new_month = m != last_month
            if new_month:
                last_month = m
            if on_day:
                on_day(self, date, hi, lo, o, c, new_month)
            elif new_month:
                self.cash += MONTHLY
                self.deposited += MONTHLY
                self.buy(MONTHLY, o)
        lp = self.daily[-1][4]
        return self.finish(lp)

    def finish(self, lp):
        eq = self.cash + self.btc * lp
        return {"final_btc": float(self.btc), "cash": float(self.cash),
                "final_equity_usd": float(eq),
                "pnl_usd": float(eq - self.deposited),
                "deposited": float(self.deposited), "trades": self.trades}


# ---- V1 A-boost: dobuy polovinu dalšího vkladu při poklesu >5 % od posledního nákupu
def v1(sim, date, hi, lo, o, c, new_month):
    st = sim.state
    if new_month:
        sim.cash += MONTHLY; sim.deposited += MONTHLY
        sim.buy(MONTHLY, o)
        st["last_buy_price"] = o; st["boosted"] = False
    elif not st.get("boosted") and o < st.get("last_buy_price", o) * D("0.95"):
        # předstih: půjčíme si z budoucího vkladu (započítáno do deposited)
        sim.cash += MONTHLY / 2; sim.deposited += MONTHLY / 2
        sim.buy(MONTHLY / 2, o)
        st["boosted"] = True
        st["last_buy_price"] = o

# ---- V2 Dip-2x: start s rezervou 1 vkladu; při poklesu >10 % od 30d max dvojnásobek
def v2(sim, date, hi, lo, o, c, new_month):
    st = sim.state.setdefault("st", {"max30": None, "hist": []})
    st["hist"].append(o)
    if len(st["hist"]) > 30:
        st["hist"].pop(0)
    st["max30"] = max(st["hist"])
    if new_month:
        amt = MONTHLY
        if o < st["max30"] * D("0.90") and sim.cash >= MONTHLY:
            amt = MONTHLY * 2
        sim.cash += MONTHLY; sim.deposited += MONTHLY  # vždy jen 100 vklad
        sim.buy(amt, o)

# ---- V3 Vol-weighted: vklad * (med_vol / vol30) — klidný měsíc = větší nákup
def v3(sim, date, hi, lo, o, c, new_month):
    st = sim.state.setdefault("st", {"rets": []})
    st["rets"].append((c / o))
    if len(st["rets"]) > 30:
        st["rets"].pop(0)
    if new_month:
        if len(st["rets"]) >= 10:
            vols = [abs(r - 1) for r in st["rets"]]
            vols.sort()
            med = vols[len(vols) // 2]
            cur = sum(vols[-5:]) / 5
            w = min(D(2), max(D("0.5"), D(str(med / cur)) if cur > 0 else D(1)))
        else:
            w = D(1)
        amt = MONTHLY * w
        sim.cash += MONTHLY; sim.deposited += MONTHLY
        # nevyužitá část zůstává v cash jen do příštího měsíce, pak se dokupí
        sim.buy(min(amt, sim.cash), o)

# ---- V4 RSI filter: odlož nákup max 5 dní, dokud RSI14(denní) > 35 → kup při <35
def v4(sim, date, hi, lo, o, c, new_month):
    st = sim.state.setdefault("st", {"gains": [], "wait": 0, "pending": D(0)})
    chg = c / sim.state.get("_prev_c", c) - 1 if sim.state.get("_prev_c") else D(0)
    sim.state["_prev_c"] = c
    st["gains"].append(chg)
    if len(st["gains"]) > 14:
        st["gains"].pop(0)
    if new_month:
        sim.cash += MONTHLY; sim.deposited += MONTHLY
        st["pending"] = MONTHLY; st["wait"] = 0
    if st["pending"] > 0:
        if len(st["gains"]) == 14:
            gains = [g for g in st["gains"] if g > 0]
            losses = [-g for g in st["gains"] if g < 0]
            ag = sum(gains) / 14 if gains else D(0)
            al = sum(losses) / 14 if losses else D("0.000001")
            rs = ag / al
            rsi = 100 - 100 / (1 + rs)
        else:
            rsi = 50
        st["wait"] += 1
        if rsi < 35 or st["wait"] >= 5:
            sim.buy(st["pending"], o)
            st["pending"] = D(0)

# ---- V5 Trend-skip: cena < 200d MA → půlka vkladu; druhá půlka při close > MA
def v5(sim, date, hi, lo, o, c, new_month):
    st = sim.state.setdefault("st", {"closes": [], "pending": D(0)})
    st["closes"].append(c)
    ma200 = (sum(st["closes"][-200:]) / min(len(st["closes"]), 200)
             if st["closes"] else c)
    if new_month:
        sim.cash += MONTHLY; sim.deposited += MONTHLY
        if c < ma200:
            sim.buy(MONTHLY / 2, o)
            st["pending"] += MONTHLY / 2
        else:
            sim.buy(sim.cash, o)
    elif st["pending"] > 0 and c > ma200:
        sim.buy(st["pending"], o)
        st["pending"] = D(0)

# ---- V6 Weekend effect: měsíční vklad nakupuje na open nejlepšího dne
#      (proxy: nakupuje na open nejnižšího dne prvních 7 dní měsíce)
def v6(sim, date, hi, lo, o, c, new_month):
    st = sim.state.setdefault("st", {"pending": D(0), "days_left": 0, "best": None})
    if new_month:
        sim.cash += MONTHLY; sim.deposited += MONTHLY
        st["pending"] = MONTHLY; st["days_left"] = 7; st["best"] = None
    if st["pending"] > 0:
        st["days_left"] -= 1
        if st["best"] is None or o < st["best"][1]:
            st["best"] = (date, o)
        if st["days_left"] <= 0:
            sim.buy(st["pending"], st["best"][1])
            st["pending"] = D(0)

# ---- V7 VWAP-month: rozdělí vklad na 30 denních nákupů, ale jen dny s open
#      pod kumulativním měsíčním VWAP; poslední den měsíce dokupí zbytek
def v7(sim, date, hi, lo, o, c, new_month):
    st = sim.state.setdefault("st", {"vwap_num": D(0), "vol": D(0)})
    if new_month:
        st["vwap_num"] = D(0); st["vol"] = D(0)
    vwap = st["vwap_num"] / st["vol"] if st["vol"] > 0 else o
    if new_month:
        sim.cash += MONTHLY; sim.deposited += MONTHLY
    daily_slice = sim.cash / 30 if sim.cash > 0 else D(0)
    if o <= vwap:
        sim.buy(min(daily_slice * 2, sim.cash), o)
    # poslední den měsíce: dokup zbytek (detekce přes next day month change)
    st["_next_day_flush"] = True
    # flush dělá následující měsíc? jednoduše: flush na konci run() v main

# ---- V8 80/20 pásmo: vždy min. 80 % equity v BTC; přebytky nad 85 % se
#      neprodávají (akumulace!), ale pod 75 % se dokupuje z cash rezervy
#      (rezerva = 20 % každého vkladu se drží, dokud pásmo neklesne)
def v8(sim, date, hi, lo, o, c, new_month):
    st = sim.state.setdefault("st", {})
    if new_month:
        sim.cash += MONTHLY; sim.deposited += MONTHLY
        core = MONTHLY * D("0.8")
        sim.buy(core, o)
        sim.cash_reserve = getattr(sim, "cash_reserve", D(0)) + MONTHLY * D("0.2")
    eq = sim.cash + getattr(sim, "cash_reserve", D(0)) + sim.btc * c
    pct_btc = sim.btc * c / eq if eq > 0 else D(1)
    if pct_btc < D("0.75") and getattr(sim, "cash_reserve", D(0)) >= D("1"):
        sim.cash += sim.cash_reserve
        sim.cash_reserve = D(0)
        sim.buy(sim.cash, o)

# ---- V9 Accelerating DCA: vklad roste o 10 %, dokud měsíc měsíci klesá;
#      při růstu zamrzne na základních 100
def v9(sim, date, hi, lo, o, c, new_month):
    st = sim.state.setdefault("st", {"last_close": None, "mult": D(1)})
    if new_month:
        amt = MONTHLY * st["mult"]
        sim.cash += amt; sim.deposited += amt
        sim.buy(amt, o)
        if st["last_close"] is not None and c < st["last_close"]:
            st["mult"] = min(st["mult"] * D("1.1"), D(3))
        else:
            st["mult"] = D(1)
    st["last_close"] = c

# ---- V10 Drawdown ladder: 50 % vkladu hned; 50 % ve třech dílech po −5/−10/−15 %
#       pod cenou vkladu (limitky na denním low)
def v10(sim, date, hi, lo, o, c, new_month):
    st = sim.state.setdefault("st", {"ladder": []})
    if new_month:
        sim.cash += MONTHLY; sim.deposited += MONTHLY
        sim.buy(MONTHLY / 2, o)
        ref = o
        st["ladder"] = [
            {"price": ref * D("0.95"), "amt": MONTHLY * D("0.1667")},
            {"price": ref * D("0.90"), "amt": MONTHLY * D("0.1667")},
            {"price": ref * D("0.85"), "amt": MONTHLY * D("0.1666")},
        ]
    for rung in st["ladder"]:
        if rung["amt"] > 0 and lo <= rung["price"]:
            sim.buy(min(rung["amt"], sim.cash), rung["price"])
            rung["amt"] = D(0)


VARIANTS = {
    "V0_A_baseline": None,
    "V1_A_boost_dip5": v1,
    "V2_dip2x": v2,
    "V3_vol_weighted": v3,
    "V4_rsi35_delay": v4,
    "V5_trend200ma": v5,
    "V6_weekend_low7": v6,
    "V7_vwap_month": v7,
    "V8_band_80_20": v8,
    "V9_accelerating": v9,
    "V10_drawdown_ladder": v10,
}


def main():
    daily = load_daily()
    out = {"days": len(daily), "start": float(daily[0][4]),
           "end": float(daily[-1][4]), "variants": {}}
    for name, fn in VARIANTS.items():
        sim = Sim(daily)
        out["variants"][name] = sim.run(on_day=fn)
    # V7 flush: dodělat — spustíme znovu s flushem na konci každého měsíce
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
