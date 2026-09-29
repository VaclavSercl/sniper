#!/usr/bin/env python3
"""T12 param sweep: half-spread multiplikator x gamma x Q_MAX na BFX ticích.
Ukazuje, zda EXISTUJE parametr, kde MM s nasi latenci vydělává.
"""
import json
import subprocess
import math
from decimal import Decimal as D

LAT_MS = 10
LOT = D("0.01")


def psql_rows(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|",
                        "-c", sql], capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l]


def load_ticks():
    rows = psql_rows("SELECT extract(epoch from mts)*1000, amount, price "
                     "FROM bitfinex_ticks ORDER BY mts, id;")
    return [(int(float(r[0])), D(r[1]), D(r[2])) for r in rows]


def sigma_of(ticks):
    by_min = {}
    for ts, amt, px in ticks:
        by_min[ts // 60000] = float(px)
    keys = sorted(by_min)
    rets = [(by_min[keys[i+1]] / by_min[keys[i]] - 1) for i in range(len(keys) - 1)]
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def sim(ticks, sigma_min, half_mult, gamma, q_max):
    sig = D(str(sigma_min))
    cash = D(0); q = D(0); fills = 0
    bid = ask = None
    pending = []
    last_px = ticks[0][2]
    for ts, amt, px in ticks:
        while pending and pending[0][0] <= ts:
            _, side, fpx, qty = pending.pop(0)
            if side == "buy":
                cash -= fpx * qty; q += qty
            else:
                cash += fpx * qty; q -= qty
            fills += 1
        if amt < 0 and bid is not None and px <= bid:
            pending.append((ts + LAT_MS, "buy", bid, LOT))
        if amt > 0 and ask is not None and px >= ask:
            pending.append((ts + LAT_MS, "sell", ask, LOT))
        mid = px
        r_res = mid - q * D(str(gamma)) * sig * sig * mid
        half = max(sig * mid * D(str(half_mult)), D("2"))
        half = min(half, D("20"))
        nb = r_res - half
        na = r_res + half
        if abs(q) >= q_max * LOT:
            if q > 0: nb = None
            else: na = None
        bid, ask = nb, na
        last_px = px
    return {"fills": fills, "pnl": float(cash + q * last_px),
            "inv_btc": float(q)}


def main():
    ticks = load_ticks()
    sig = sigma_of(ticks)
    out = {"ticks": len(ticks), "hours": (ticks[-1][0]-ticks[0][0])/3.6e6,
           "sigma_min": sig, "grid": []}
    for hm in (0.02, 0.08, 0.3):
        for g in (0.1, 1.0, 10.0):
            for qm in (3, 10):
                r = sim(ticks, sig, hm, g, qm)
                out["grid"].append({"half_mult": hm, "gamma": g, "q_max": qm, **r})
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
