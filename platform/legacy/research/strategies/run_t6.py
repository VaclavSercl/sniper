#!/usr/bin/env python3
"""T6 grid trading runner — bez CPCV (grid nemá IS/OOS selekci parametrů stejným
způsobem; falzifikace je benchmarková: grid vs HODL v USD i BTC ekvivalentu).

Pipeline: načte klines, pustí grid přes kombinace parametrů, report JSON.
Verdikt rodiny: fail pokud >2/3 kombinací prohraje s HODL v BTC ekvivalentu
 bitcoin-standard metrika) i USD.
"""
import json
import subprocess
import sys

sys.path.insert(0, "/opt/sniper/current/platform/legacy/research")
from strategies.t6_grid import run_grid  # noqa: E402

EXPECTED_MIN = 525_000

GRID = [
    {"spacing_pct": s, "n_levels": n, "geometric": g, "rolling": r,
     "stop_loss_pct": sl}
    for s in (0.5, 1.0, 2.0)
    for n in (10, 25, 50)
    for g in (False, True)
    for r in (False, True)
    for sl in (None, 20.0)
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
        out.append({"ts": ts, "open": o, "high": h, "low": l, "close": c})
    return out


def main():
    klines = load_klines()
    if len(klines) < EXPECTED_MIN:
        print(json.dumps({"error": f"dataset incomplete: {len(klines)}"}))
        return 1

    results = []
    for p in GRID:
        r = run_grid(klines, **p)
        r["params"] = p
        r["beats_hodl_usd"] = r["total_ret_usd"] > r["hodl_ret"]
        r["beats_hodl_btc"] = r["grid_btc_equiv"] > r["hodl_btc"]
        results.append(r)
        print(f"done {p} -> ret_usd={r['total_ret_usd']:.3f} "
              f"vs hodl {r['hodl_ret']:.3f}", file=sys.stderr)

    n = len(results)
    beats_usd = sum(r["beats_hodl_usd"] for r in results)
    beats_btc = sum(r["beats_hodl_btc"] for r in results)

    out = {
        "trial": "T6_grid",
        "n_combinations": n,
        "hodl_ret": results[0]["hodl_ret"],
        "beats_hodl_usd": beats_usd,
        "beats_hodl_btc": beats_btc,
        "best_usd": max(results, key=lambda r: r["total_ret_usd"]),
        "best_btc": max(results, key=lambda r: r["grid_btc_equiv"]),
        "worst_usd": min(results, key=lambda r: r["total_ret_usd"]),
        "results": results,
    }
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
