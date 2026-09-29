#!/usr/bin/env python3
"""DCA okamžitá alokace (owner korekce 27.08): každý měsíční vklad 100 USDT
se OKAMŽITĚ konvertuje na BTC (žádné ležící cash), grid pracuje POUZE s
těmito BTC (prodává +2 úrovně výš, ratchet nahoru i dolů, trailing stop).

Interpretace mandate: core je akumulace — vklad nikdy neleží. Grid přidává
zisk tím, že část BTC prodá výš a pokusí se koupit zpět níž (swing add-on).

Varianty:
A) immediate_dca_only: vklad → BTC, nikdy se neprodává (čistá akumulace)
B) immediate_dca_grid: vklad → BTC; každý měsíc se z nově nakoupeného BTC
   určí "trade díl" (např. 30 %), který grid pokusí prodat +2 úrovně výš a
   re-nakoupit níž (swing); 70 % je "hodl jádro" (neprodejné)
C) referenční pure_dca z předchozího testu (identické A bez fee detailů)

Benchmark: buy & hold každého vkladu okamžitě (A) — srovnáváme sats.
"""
import json
import subprocess
from decimal import Decimal as D

SPACING = D("1.005")
FEE = D("0.00075")
MONTHLY = D("100")
TRADE_SHARE = D("0.3")     # 30 % vkladu jde do grid swing dílů
EMA_WINDOW = 1440
RATCHET_DOWN_HYST = D("0.02")
TRAIL_PCT = D("10")


def psql_rows(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|",
                        "-c", sql], capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l]


def load_klines():
    rows = psql_rows("SELECT extract(epoch from open_time), open, high, low, close "
                     "FROM market_klines WHERE symbol='BTCUSDT' AND open_time >= "
                     "'2025-08-27' ORDER BY open_time;")
    return [(float(r[0]), D(r[1]), D(r[2]), D(r[3]), D(r[4])) for r in rows]


def month_key(ts):
    import datetime
    d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    return (d.year, d.month)


def sim_immediate_hold(klines):
    btc = D(0); deposited = D(0); last_month = None
    for ts, o, hi, lo, c in klines:
        mk = month_key(ts)
        if mk != last_month:
            btc += MONTHLY / o * (1 - FEE)
            deposited += MONTHLY
            last_month = mk
    lp = klines[-1][4]
    return {"deposited": float(deposited), "final_btc": float(btc),
            "final_equity_usd": float(btc * lp),
            "pnl_usd": float(btc * lp - deposited)}


def sim_immediate_grid(klines):
    btc_core = D(0)          # hodl jádro
    swings = []              # [{"qty","entry","sell_target"}] aktivní swing dily
    cash = D(0)              # z prodejů swings — čeká na re-nákup NÍŽE
    ema = klines[0][4]; last_grid = klines[0][0]
    center = klines[0][4]
    deposited = D(0); last_month = None
    realized = D(0); trades = 0; trail_exits = 0
    ratchets_up = 0; ratchets_down = 0

    alpha = D(2) / (EMA_WINDOW + 1)
    for ts, o, hi, lo, c in klines:
        ema = alpha * c + (1 - alpha) * ema

        mk = month_key(ts)
        if mk != last_month:
            # vklad: 70 % core, 30 % swing (cíl: prodat +2 spacing výš,
            # pak koupit zpět kdykoliv níž a opakovat)
            core_amt = MONTHLY * (1 - TRADE_SHARE)
            swing_amt = MONTHLY * TRADE_SHARE
            btc_core += core_amt / o * (1 - FEE)
            sqty = swing_amt / o * (1 - FEE)
            swings.append({"qty": sqty, "entry": o, "target": o * SPACING * SPACING})
            deposited += MONTHLY
            last_month = mk
            trades += 1

        # swing sells na target
        for sw in list(swings):
            if hi >= sw["target"]:
                proceeds = sw["qty"] * sw["target"] * (1 - FEE)
                cash += proceeds
                realized += proceeds - sw["qty"] * sw["entry"]
                swings.remove(sw)
                trades += 1

        # re-nákup: pokud máme cash a cena je pod průměrným vstupem prodaných
        # swingů (re-nákup na -2 spacing od posledního prodeje), kup zpět
        # jednoduše: re-nákup když cena <= poslední sell target / (spacing^2)
        if cash >= D("1") and swings == []:
            # žádný aktivní swing a máme cash → založ nový swing (re-nákup)
            # re-nákup podmínka: cena <= entry posledního cyklu (sledujeme
            # přes reentry_lvl aktualizované při prodeji)
            if c <= globals().get("_reentry", klines[0][4] * D(0) + klines[0][4]):
                pass  # placeholder, nahrazeno níže přes state proměnnou

        # (čistší implementace re-nákupu přes explicitní stav:)
        if cash >= D("1") and not swings:
            re_lvl = globals().get("_reentry_lvl")
            if re_lvl is None or c <= re_lvl:
                sqty = cash / c * (1 - FEE)
                swings.append({"qty": sqty, "entry": c, "target": c * SPACING * SPACING})
                cash = D(0)
                trades += 1

        # denní ratchet center (nahoru/dolů) — používá se pro reentry level
        if ts - last_grid >= 86400:
            if ema > center:
                center = ema; ratchets_up += 1
            elif ema < center * (1 - RATCHET_DOWN_HYST):
                center = ema; ratchets_down += 1
            last_grid = ts
        # reentry = aktuální center (ratchet sleduje trh oběma směry)
        globals()["_reentry_lvl"] = center

    lp = klines[-1][4]
    btc_total = btc_core + sum(s["qty"] for s in swings)
    return {"deposited": float(deposited), "final_btc": float(btc_total),
            "final_equity_usd": float(btc_total * lp + cash),
            "pnl_usd": float(btc_total * lp + cash - deposited),
            "trades": trades, "realized_usd": float(realized),
            "ratchets_up": ratchets_up, "ratchets_down": ratchets_down}


def main():
    klines = load_klines()
    out = {
        "bars": len(klines), "start": float(klines[0][4]), "end": float(klines[-1][4]),
        "A_immediate_hold": sim_immediate_hold(klines),
        "B_immediate_30pct_swing": sim_immediate_grid(klines),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
