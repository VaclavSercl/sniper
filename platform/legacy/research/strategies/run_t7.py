#!/usr/bin/env python3
"""T7 backtest runner — funding-rate mean reversion, §9c/§9d gates.

Pipeline (stejná kostra jako run_t1.py):
  1. market_funding (8h) + market_klines (1m) z DB
  2. grid parametrů: z_window, entry_z, holding_h (hodin)
  3. per-trade výnosy: long spot, fee 0.085 % RT + half-spread 0.01 %,
     funding cash-flow dle strany (long dostává při rate<0, platí při rate>0)
  4. CPCV (N=8, k=2, embargo 1 %) → IS/OOS Sharpy
  5. gates: DSR (N=18), PBO CSCV, degradace SR_oos/SR_is >= 0.4
"""
import json
import subprocess
import sys

sys.path.insert(0, "/opt/sniper/current/platform/legacy/research")
from strategies.t7_funding_mr import generate_signals  # noqa: E402
from backtest.cpcv import cpcv_splits  # noqa: E402
from backtest.dsr import deflated_sharpe_ratio  # noqa: E402
from backtest.pbo import pbo_cscv  # noqa: E402

FEE_RT = 0.00085 + 0.0001   # maker RT + half-spread (konzervativně; T7 bude
                             # entry na funding fixu = likvidní okamžik)
GRID = [
    {"z_window": z, "entry_z": e, "hold_h": h}
    for z in (60, 90, 120)
    for e in (-1.0, -1.5, -2.0)
    for h in (4, 8, 24)
]


def psql_rows(sql):
    r = subprocess.run(
        ["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l]


def load_data():
    fund = [(int(float(r[0])), float(r[1])) for r in psql_rows(
        "SELECT extract(epoch from funding_time), rate FROM market_funding "
        "WHERE symbol='BTCUSDT' ORDER BY funding_time;")]
    kl = psql_rows(
        "SELECT extract(epoch from open_time), open, high, low, close "
        "FROM market_klines WHERE symbol='BTCUSDT' ORDER BY open_time;")
    klines = [{"ts": float(r[0]), "open": float(r[1]), "high": float(r[2]),
               "low": float(r[3]), "close": float(r[4])} for r in kl]
    return fund, klines


def trade_returns(fund, klines, params):
    """Long-only trades: entry open následující svíčky po signálu, exit po
    hold_h hodinách; funding cash-flow přes všechny fixy v holding období."""
    sigs = generate_signals(fund, None, z_window=params["z_window"],
                            entry_z=params["entry_z"])
    ts2idx = {k["ts"]: i for i, k in enumerate(klines)}
    fund_map = {ts: rate for ts, rate in fund}
    hold_s = params["hold_h"] * 3600
    rets = []
    used_until = -1
    for ts, _side in sigs:
        # první 1m bar s ts >= signál ts
        i = None
        # bisect-like: klines jsou chronologické; signál ts je vždy na 8h hraně
        lo = max(used_until + 1, 0)
        for j in range(lo, len(klines)):
            if klines[j]["ts"] >= ts:
                i = j
                break
        if i is None or i + 1 >= len(klines):
            continue
        entry_i = i + 1
        exit_i = min(entry_i + params["hold_h"] * 60, len(klines) - 1)
        if exit_i <= entry_i:
            continue
        entry = klines[entry_i]["open"]
        exit_ = klines[entry_i + params["hold_h"] * 60]["close"] \
            if entry_i + params["hold_h"] * 60 < len(klines) else klines[-1]["close"]
        price_ret = (exit_ - entry) / entry
        # funding cash-flow v holding okně (long dostává rate<0, platí rate>0)
        fund_cf = 0.0
        for fts, rate in fund:
            if klines[entry_i]["ts"] <= fts < klines[exit_i]["ts"]:
                fund_cf += -rate  # long: negativní rate = příjem
        used_until = exit_i
        rets.append(price_ret + fund_cf - FEE_RT)
    return rets


def sharpe(rets):
    n = len(rets)
    if n < 2:
        return 0.0
    mu = sum(rets) / n
    var = sum((r - mu) ** 2 for r in rets) / (n - 1)
    sd = var ** 0.5
    return mu / sd if sd > 0 else 0.0


def main():
    fund, klines = load_data()
    n_trials = len(GRID)
    results = {}
    all_rets = {}
    for p in GRID:
        rets = trade_returns(fund, klines, p)
        all_rets[json.dumps(p)] = rets
        results[json.dumps(p)] = {
            "n_trades": len(rets),
            "mean": sum(rets) / len(rets) if rets else 0,
            "sr": sharpe(rets),
        }

    # CPCV na concatenated per-trade returns? T1 používal cpcv_splits na
    # return series; zde per-trade série (nerovnoměrné) → použijeme stejný
    # přístup: split série per-trade returns do N skupin
    import math
    N_GROUPS, K_TEST, EMBARGO = 8, 2, 0.01
    fam_results = {}
    for pj, rets in all_rets.items():
        if len(rets) < 20:
            fam_results[pj] = {"sr_is": 0.0, "sr_oos": 0.0, "n": len(rets)}
            continue
        # jednoduchý CPCV proxy: rozdělíme na N skupin, IS = complement k=2 OOS
        n = len(rets)
        g = max(1, n // N_GROUPS)
        groups = [rets[i:i + g] for i in range(0, n, g)]
        srs_is, srs_oos = [], []
        for a in range(len(groups) - 1):
            for b in range(a + 1, len(groups)):
                oos = groups[a] + groups[b]
                is_ = [x for gi, grp in enumerate(groups) if gi not in (a, b)
                       for x in grp]
                srs_oos.append(sharpe(oos))
                srs_is.append(sharpe(is_))
        fam_results[pj] = {
            "sr_is": sum(srs_is) / len(srs_is) if srs_is else 0.0,
            "sr_oos": sum(srs_oos) / len(srs_oos) if srs_oos else 0.0,
            "n": len(rets),
        }

    # PBO CSCV: matice výkonu across kombinací
    # (T1 framework pbo_cscv očekává matici M: rows=trials, cols=returns)
    keys = list(all_rets.keys())
    maxn = max(len(all_rets[k]) for k in keys)
    if maxn >= 20:
        M = []
        for k in keys:
            r = all_rets[k]
            M.append(r + [0.0] * (maxn - len(r)))
        pbo = pbo_cscv(M)
    else:
        pbo = None

    # DSR na nejlepším kandidátovi
    keys = list(all_rets.keys())
    best_i = max(range(len(keys)), key=lambda i: results[keys[i]]["sr"])
    best_key = keys[best_i]
    best_rets = all_rets[best_key]
    dsr = None
    if len(best_rets) >= 20:
        dsr = deflated_sharpe_ratio(best_rets, n_trials=n_trials)

    out = {
        "family": "T7_funding_mr",
        "n_trials": n_trials,
        "grid": GRID,
        "results": results,
        "cpcv": fam_results,
        "pbo": pbo,
        "dsr_best": {"params": best_key, "dsr": dsr,
                     "sr": results[best_key]["sr"],
                     "n_trades": results[best_key]["n_trades"]},
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
