#!/usr/bin/env python3
"""Zápis T7 výsledků do research_trial_results (append-only, unique params)
+ UPDATE result v research_trials (doplnění výsledku k registrovanému trial)."""
import json
import subprocess

RES = json.load(open("/tmp/t7_result.json"))


def psql(sql):
    r = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


GATES = {
    "family": "T7_funding_mr",
    "verdict": "FALSIFIED",
    "gate_dsr": {"required": ">= 0.95", "actual_best": RES["dsr_best"]["dsr"]["DSR"]},
    "gate_pbo": {"required": "<= 0.50", "actual": RES["pbo"]["PBO"]
                 if isinstance(RES["pbo"], dict) else RES["pbo"]},
    "gate_degradation": {"required": "SR_oos/SR_is >= 0.4 AND SR_is > 0",
                         "note": "SR_is < 0 vsude -> zamitnuto bez podilu (§9d)"},
}

n_written = 0
for pj, r in RES["results"].items():
    params = json.loads(pj)
    row = json.dumps({"params": params, "result": r, "gates_summary": {
        "sr": r["sr"], "n_trades": r["n_trades"]}})
    psql("INSERT INTO research_trial_results (params, result) VALUES ("
         f"'{json.dumps(params)}'::jsonb, '{row.replace(chr(39), chr(39)*2)}'::jsonb) "
         "ON CONFLICT (params) DO NOTHING")
    n_written += 1

# doplnit result do research_trials (UPDATE result sloupce registrovanych T7 trialů)
upd = psql("UPDATE research_trials SET result = "
           f"'{json.dumps(GATES).replace(chr(39), chr(39)*2)}'::jsonb "
           "WHERE hypothesis LIKE 'T7 funding-rate%' AND result IS NULL "
           "RETURNING id;")
print(json.dumps({"results_written": n_written,
                  "trials_updated": len(upd.split()) if upd else 0,
                  "verdict": GATES["verdict"],
                  "pbo": GATES["gate_pbo"]["actual"],
                  "dsr_best": GATES["gate_dsr"]["actual_best"]}))
