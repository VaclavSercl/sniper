#!/usr/bin/env python3
"""Registrace T7 trialů do research_trials (§9a). Append-only INSERT."""
import hashlib
import json
import subprocess
import sys

GRID = [
    {"z_window": z, "entry_z": e, "hold_h": h}
    for z in (60, 90, 120)
    for e in (-1.0, -1.5, -2.0)
    for h in (4, 8, 24)
]

HYPOTHESIS = (
    "T7 funding-rate mean reversion (BTCUSDT spot, 8h funding filtr): funding "
    "z-score (rolling, okno W) pod prahem E signalizuje short crowding na "
    "perpetuálech; po takovém fixu následuje krátkodobá mean-reversion ceny. "
    "Long-only spot, entry open 1m svíčky po fixu, exit po H hodinách, funding "
    "cash-flow dle strany (long dostává při rate<0). Protistrana: crowding "
    "perp traderů platících extrémní funding, jejich unwind vytváří mechanický "
    "protitlak; edge nezmizí, dokud funding zůstává placen perp crowdem "
    "(strukturální, ne informační). Falzifikace: SR_oos/SR_is < 0.4, "
    "DSR < 0.95, PBO > 0.50 pri N=27 trialech rodiny."
)


def psql(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def main():
    # data hash: funding + klines fingerprint
    row = psql(
        "SELECT (SELECT count(*) FROM market_funding WHERE symbol='BTCUSDT') || '|' || "
        "(SELECT coalesce(sum(rate),0)::text FROM market_funding WHERE symbol='BTCUSDT') || '|' || "
        "(SELECT count(*) FROM market_klines WHERE symbol='BTCUSDT') || '|' || "
        "(SELECT sum(close)::text FROM market_klines WHERE symbol='BTCUSDT');")
    dh = hashlib.sha256(row.encode()).hexdigest()

    h = hashlib.sha256()
    for path in ("strategies/t7_funding_mr.py", "strategies/run_t7.py"):
        h.update(open(f"/opt/sniper/current/platform/legacy/research/{path}", "rb").read())
    ch = h.hexdigest()

    # idempotence: stejné trials (stejný code+data hash) neregistruj znovu
    existing = psql(f"SELECT count(*) FROM research_trials WHERE code_hash='{ch}' AND data_hash='{dh}';")
    if int(existing) > 0:
        print(json.dumps({"already_registered": int(existing)}))
        return 0

    for params in GRID:
        psql("INSERT INTO research_trials (hypothesis, params, code_hash, "
             f"data_hash, data_as_of) VALUES ("
             f"'{HYPOTHESIS.replace(chr(39), chr(39)*2)}', "
             f"'{json.dumps(params)}'::jsonb, '{ch}', '{dh}', CURRENT_DATE);")
    n = psql("SELECT count(*) FROM research_trials;")
    print(json.dumps({"registered": len(GRID), "total_trials": n,
                      "data_hash": dh, "code_hash": ch, "fingerprint": row}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
