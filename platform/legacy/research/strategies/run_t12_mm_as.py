#!/usr/bin/env python3
"""T12 backtest: Avellaneda-Stoikov market making na Bitfinex ticích.

Data: bitfinex_ticks (tBTCUSD, ~72h). Model: A-S s GLFT-style zjednodušením.
- reservation price: r = mid - q * gamma * sigma^2 * (T-t)
- quotes: bid = r - delta/2, ask = r + delta/2, delta z A/k intenzity
- fill model: limitka na bid se naplní, když přijme SELL tick <= bid
  (taker prodává nám); ask když BUY tick >= ask (klasický konzervativní
  model bez queue — optimistic bias viz poznámka)
- latence: fill potvrzen s 10ms zpožděním (naše měřená RTT), v tomto
  okně kotujeme staré
- inventory limit: |q| <= Q_max, pak kótujeme jen druhou stranu
- fee: maker 0 % (Bitfinex dle ownera), taker fallback 0.1 %
Metriky: P&L, počet fillů, spread capture, inventory DD, adverse selection
(vícenásobnost: avg pohyb mid proti nám 1s po fillu).
"""
import json
import subprocess
from decimal import Decimal as D

GAMMA = D("0.1")          # risk aversion
Q_MAX = 3                 # max pozic (0.01 BTC each -> 0.03 BTC)
LOT = D("0.01")
LAT_MS = 10               # naše round-trip latence (ms)
FEE = D("0.0")            # maker 0 %


def psql_rows(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|",
                        "-c", sql], capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l]


def load_ticks():
    rows = psql_rows("SELECT extract(epoch from mts)*1000, amount, price "
                     "FROM bitfinex_ticks ORDER BY mts, id;")
    return [(int(float(r[0])), D(r[1]), D(r[2])) for r in rows]


def main():
    ticks = load_ticks()
    if len(ticks) < 100:
        print(json.dumps({"error": "not enough ticks", "n": len(ticks)}))
        return 1

    # mid odhad z last trade ceny (spread book nemáme — proxy)
    # volatility: std of 1-min returns
    import math
    by_min = {}
    for ts, amt, px in ticks:
        by_min[ts // 60000] = float(px)
    keys = sorted(by_min)
    rets = [(by_min[keys[i+1]] / by_min[keys[i]] - 1)
            for i in range(len(keys) - 1)]
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    sigma_min = math.sqrt(var)
    sigma_tick = sigma_min / math.sqrt(60)   # per-minute -> per-tick approx

    cash = D(0); q = D(0)          # q v BTC
    fills = 0; pnl_series = []
    bid = ask = None
    pending = []                    # (ts_effective, side, price, qty)
    adverse = []                    # pohyb mid proti nám po 1 s
    last_px = ticks[0][2]
    spread_cap_usd = D("20")        # guard: nesekotovat absurdni spread

    for ts, amt, px in ticks:
        # aplikuj pending filly (latence)
        while pending and pending[0][0] <= ts:
            _, side, fpx, qty = pending.pop(0)
            if side == "buy":
                cash -= fpx * qty; q += qty
            else:
                cash += fpx * qty; q -= qty
            fills += 1
            # adverse selection: mid za 1 s
            adverse.append((ts, side, float(px)))

        # 1) fill test proti PREDCHOZIM kotacim (bid/ask z minuleho ticku)
        if amt < 0 and bid is not None and px <= bid:
            pending.append((ts + LAT_MS, "buy", bid, LOT))
        if amt > 0 and ask is not None and px >= ask:
            pending.append((ts + LAT_MS, "sell", ask, LOT))

        # 2) requote: A-S reservation price z aktualniho mid
        # (kotace se vystavuji KA Z D Y — i po fillu, pokud inventory limit dovoli)
        mid = px
        sig = D(str(sigma_min))   # per-minute sigma (A-S horizont ~ minuta)
        r_res = mid - q * GAMMA * sig * sig * mid
        # half-spread: kalibrace na realny BFX BTCUSD spread (~2-10 USD);
        # sigma-min * konstanta; floor 2 USD (2x tick), cap 20 USD
        half = max(sig * mid * D("0.08"), D("2"))
        half = min(half, spread_cap_usd / 2)
        nb = r_res - half
        na = r_res + half
        # inventory limit: prekrocen -> nekotovat danou stranu
        if abs(q) >= Q_MAX * LOT:
            if q > 0:
                nb = None            # nekupovat vic
            else:
                na = None
        bid = nb
        ask = na

        last_px = px
        pnl_series.append((ts, float(cash + q * px)))

    # adverse selection metrica: mid za 1 s po fillu vs fill cenou
    tick_map = [(ts, float(px)) for ts, amt, px in ticks]
    adv_loss = []
    import bisect
    ts_list = [t[0] for t in tick_map]
    for ts, side, fpx in adverse:
        i = bisect.bisect_left(ts_list, ts + 1000)
        if i < len(tick_map):
            px1 = tick_map[i][1]
            if side == "buy":
                adv_loss.append(px1 - fpx)     # kupovali jsme, cena sla dal nahoru = sme pomali
            else:
                adv_loss.append(fpx - px1)
    final_pnl = cash + q * last_px
    out = {
        "ticks": len(ticks),
        "period_h": (ticks[-1][0] - ticks[0][0]) / 3600000,
        "fills": fills,
        "final_pnl_usd": float(final_pnl),
        "final_inventory_btc": float(q),
        "avg_adverse_selection_usd": (sum(adv_loss) / len(adv_loss)) if adv_loss else None,
        "sigma_per_min_pct": sigma_min * 100,
        "note": "fill model bez queue simulace — OPTIMISTICKY (skutecny MM ma horsi filly)",
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
