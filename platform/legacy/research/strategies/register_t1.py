#!/usr/bin/env python3
"""Registrace trialů T1 do research_trials (§9a) — volá se až je dataset
kompletní (≥99 % 525 600 svíček). Append-only: INSERT bez UPDATE zadání.
Výsledky dopisuje zpětně až run_t1.py (UPDATE result sloupce).
"""
import hashlib
import json
import subprocess
import sys

sys.path.insert(0, "/opt/sniper/current/platform/legacy/research")

GRID = [
    {"n_breakout": n, "vol_q": q, "trail": t}
    for n in (120, 240, 480)
    for q in (0.5, 0.7)
    for t in (1.5, 2.0, 3.0)
]

HYPOTHESIS = (
    "T1 volatility breakout (BTCUSDT 1m, long-only spot): po kompresi volatility "
    "nad kvantilem ATR60 následuje expanze; close > max(high N min)*(1+0.1%). "
    "Protistrana: mean-reversion tradeři a dispoziční efekt retailu (drží "
    "losery proti novému trendu). Edge nezmizí, dokud retail flow s "
    "dispozičním chováním převládá; falzifikace: SR_oos/SR_is < 0.4 nebo "
    "DSR < 0.95 při N=18 trialech rodiny."
)


def psql(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def data_hash():
    """Hash datasetu: count + min/max open_time + suma cen (levý, ale
    deterministický fingerprint obsahu)."""
    row = psql("SELECT count(*), min(open_time)::text, max(open_time)::text, "
               "sum(close)::text FROM market_klines WHERE symbol='BTCUSDT';")
    h = hashlib.sha256(row.encode()).hexdigest()
    return h, row


def code_hash():
    h = hashlib.sha256()
    for path in ("strategies/t1_volatility_breakout.py",
                 "strategies/run_t1.py",
                 "backtest/cpcv.py", "backtest/dsr.py", "backtest/pbo.py"):
        h.update(open(f"/opt/sniper/current/platform/legacy/research/{path}", "rb").read())
    return h.hexdigest()


def main():
    count = int(psql("SELECT count(*) FROM market_klines WHERE symbol='BTCUSDT';"))
    if count < 525_000 * 0.99:
        print(json.dumps({"error": "dataset incomplete", "count": count}))
        return 1
    dh, fingerprint = data_hash()
    ch = code_hash()
    existing = int(psql("SELECT count(*) FROM research_trials;"))
    if existing > 0:
        print(json.dumps({"error": "trials already registered", "existing": existing}))
        return 1
    for params in GRID:
        psql("INSERT INTO research_trials (hypothesis, params, code_hash, "
             "data_hash, data_as_of) VALUES ("
             f"'{HYPOTHESIS.replace(chr(39), chr(39)*2)}', "
             f"'{json.dumps(params)}'::jsonb, '{ch}', '{dh}', CURRENT_DATE);")
    n = psql("SELECT count(*) FROM research_trials;")
    print(json.dumps({"registered": n, "data_hash": dh, "code_hash": ch,
                      "fingerprint": fingerprint}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
