#!/usr/bin/env python3
"""Registrace 4ročních re-test trialů (§9a) — GRID-20 + DCA-family.
Nové data_hash (4y dataset), nový kontext: multi-regime test."""
import hashlib
import json
import subprocess

GRID20 = "grid20_multiregime_4y"
DCAFAM = "dca_family_multiregime_4y"

HYP_GRID = (
    "GRID-20 multi-regime 4y (BTCUSDT 1m, 2022-08..2026-08): re-test 20 grid "
    "variant dle literatury (DGT arXiv 2506.11921, Quantpedia, GTSbot, Jia 2022) "
    "napříč režimy (bear 2022/23, bull 2023/24, range 2024, bear 2025/26). "
    "Hypotéza: grid rodina má kladný edge v range/bull režimech, záporný v "
    "trendovém bearu; existuje konfigurace kladná napříč režimy. Protistrana: "
    "mean-reversion retail flow. Falzifikace: žádná varianta nemá kladný "
    "sats-vs-B&H v >2 ze 4 let."
)
HYP_DCA = (
    "DCA-family multi-regime 4y (BTCUSDT, měsíční vklady 100 USDT): re-test "
    "11 DCA variant (V0-V10) napříč režimy. Hypotéza: V8 (band 80/20) "
    "konzistentně >= plain DCA (V0) ve všech režimech. Falzifikace: V8 "
    "horší než V0 v >1 z 4 let."
)


def psql(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def main():
    row = psql("SELECT count(*) || '|' || min(open_time)::text || '|' || "
               "max(open_time)::text || '|' || sum(close)::text FROM market_klines "
               "WHERE symbol='BTCUSDT';")
    dh = hashlib.sha256(row.encode()).hexdigest()
    for hyp, fam in ((HYP_GRID, GRID20), (HYP_DCA, DCAFAM)):
        existing = psql(f"SELECT count(*) FROM research_trials WHERE "
                        f"hypothesis LIKE '%{fam}%' AND data_hash='{dh}';")
        if int(existing) > 0:
            print(json.dumps({"family": fam, "already": int(existing)}))
            continue
        psql("INSERT INTO research_trials (hypothesis, params, code_hash, "
             "data_hash, data_as_of) VALUES ("
             f"'{hyp.replace(chr(39), chr(39)*2)}', "
             f"'{{\"family\": \"{fam}\"}}'::jsonb, "
             f"'{hashlib.sha256(fam.encode()).hexdigest()}', '{dh}', CURRENT_DATE);")
        print(json.dumps({"family": fam, "registered": 1, "data_hash": dh}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
