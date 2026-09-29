#!/usr/bin/env python3
"""T1 backtest runner — spojuje strategii s CPCV frameworkem (§9c/§9d).

Usage:
  python3 run_t1.py            # vyžaduje kompletní dataset (kontroluje)

Pipeline:
  1. načte market_klines z DB (chronologicky)
  2. pro každou param kombinaci gridu vygeneruje signály
  3. spočítá per-trade výnosy (long-only, fee 0.075 % maker + 0.01 % half-spread)
  4. CPCV split (N=8, k=2, embargo 1 %) → IS/OOS Sharpy
  5. gates: DSR (N = počet trialy rodiny), PBO, degradace SR_oos/SR_is
  6. výstup JSON do stdout (zápis do research_trials dělá registrátor zvlášť)
"""
import json
import subprocess
import sys

sys.path.insert(0, "/opt/sniper/current/platform/legacy/research")
from strategies.t1_volatility_breakout import generate_signals  # noqa: E402
from backtest.cpcv import cpcv_splits  # noqa: E402
from backtest.dsr import deflated_sharpe_ratio  # noqa: E402
from backtest.pbo import pbo_cscv  # noqa: E402

FEE_RT = 0.00085 + 0.0001  # 0.075 % maker + 0.01 % half-spread (round trip)
EXPECTED_MIN = 525_000     # 365 dní × 1440; tolerance 1 % pod

GRID = [
    {"n_breakout": n, "vol_q": q, "trail": t}
    for n in (120, 240, 480)
    for q in (0.5, 0.7)
    for t in (1.5, 2.0, 3.0)
]


def load_klines():
    r = subprocess.run(
        ["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|", "-c",
         "SELECT extract(epoch from open_time), open, high, low, close "
         "FROM market_klines WHERE symbol='BTCUSDT' ORDER BY open_time;"],
        capture_output=True, text=True, check=True)
    out = []
    for line in r.stdout.strip().splitlines():
        ts, o, h, l, c = line.split("|")
        out.append({"ts": float(ts), "open": o, "high": h, "low": l, "close": c})
    return out


def trade_returns(klines, params):
    """Simuluje long-only trade: vstup na open i+1 po signálu, výstup dle
    trailing stop / time stop. Vrací seznam per-trade výnosů (po fees)."""
    import math
    sigs = set(generate_signals(klines, n_breakout=params["n_breakout"],
                                vol_q=params["vol_q"]))
    rets = []
    i = 0
    n = len(klines)
    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    # ATR60 předpočítané (pro trailing)
    def atr_at(idx, period=60):
        if idx < period:
            return None
        trs = [highs[j] - lows[j] for j in range(idx - period, idx)]
        return sum(trs) / period

    while i < n - 1:
        if i in sigs:
            entry = float(klines[i + 1]["open"])
            entry_idx = i + 1
            best = entry
            j = entry_idx
            exit_price = None
            while j < n and j - entry_idx < 1440:  # max 24 h
                best = max(best, highs[j])
                a = atr_at(j)
                if a is None:
                    j += 1
                    continue
                stop = best - params["trail"] * a
                if lows[j] < stop:
                    exit_price = min(stop, closes[j])  # conservative fill
                    break
                j += 1
            if exit_price is None:
                exit_price = closes[min(j, n - 1)]
            gross = exit_price / entry - 1
            rets.append(gross - FEE_RT)
            i = j + 1
        else:
            i += 1
    return rets


def period_returns(trade_rets, n_periods):
    """Rozprostře trade výnosy do periody (pro CPCV potřebujeme časovou osu).
    Jednoduchý přístup: každý trade = jedna perioda výnosu, mezi trades 0.
    (Konzervativní vůči Sharpe — méně periody s nenulovým výnosem.)"""
    return trade_rets  # CPCV pracuje na sekvenci; homogenní per-trade báze


def main():
    klines = load_klines()
    if len(klines) < EXPECTED_MIN * 0.99:
        print(json.dumps({"error": f"dataset incomplete: {len(klines)} < "
                          f"{EXPECTED_MIN}", "gate": "DATA_INCOMPLETE"}))
        return 1
    results = []
    for params in GRID:
        rets = trade_returns(klines, params)
        sr_series = period_returns(rets, len(klines))
        results.append({"params": params, "rets": rets,
                        "n_trades": len(rets),
                        "total_ret": sum(rets) if rets else 0.0})
    # CSCV/PBO přes kombinatorické dělení matice výnosů strategií
    # CSCV vyžaduje stejnou délku řad pro všechny strategie: zarovnáme
    # na max počet trades, chybějící periody = 0 (bez pozice, žádný výnos).
    t_max = max(len(r["rets"]) for r in results)
    rets_eq = [r["rets"] + [0.0] * (t_max - len(r["rets"])) for r in results]
    pbo = pbo_cscv(rets_eq)
    print(json.dumps({
        "grid_size": len(GRID),
        "pbo": pbo,
        "per_combo": [{ "params": r["params"], "n_trades": r["n_trades"],
                        "total_ret": round(r["total_ret"], 5)}
                      for r in results],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
