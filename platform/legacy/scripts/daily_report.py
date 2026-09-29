#!/usr/bin/env python3
"""BEROUN daily report — čísla generuje kód z ledgeru/DB, ne LLM (§4-4, §14).
V L0: DB je prázdná, report hlásí nuly + hash CORE + ladder + mód."""
import hashlib, json, subprocess, time

CORE = "/opt/beroun/core"
def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

report = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "core_hash": sha(f"{CORE}/BEROUN_MASTER_v2_1.md"),
    "envelope_hash": sha(f"{CORE}/envelope.yaml"),
    "mode": open("/opt/beroun/state/mode").read().strip(),
    "ladder_level": "L0",
    "db_counts": {},
    "orders_rejected": 0,
    "invariant_violations": 0,
    "reconcile": "N/A_L0_NO_VENUES",
    "origin": "CORE hash: file read; DB counts: psql count; ostatní: [NEOVĚŘENO] bez venue",
}
# DB počty — z DB, ne z paměti
try:
    out = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-c",
        "SELECT 'orders', count(*) FROM orders UNION ALL SELECT 'fills', count(*) FROM fills UNION ALL SELECT 'ledger', count(*) FROM ledger;"],
        capture_output=True, text=True, timeout=10)
    if out.returncode == 0:
        for line in out.stdout.strip().split("\n"):
            k, v = line.split("|")
            report["db_counts"][k] = v
except Exception as e:
    report["db_counts"] = {"error": str(e)}

# zápis do outbox (reports tabulka, §14 — report v jedné transakci s daty)
payload = json.dumps(report, ensure_ascii=False)
sql = "INSERT INTO reports (kind, payload) VALUES ('daily', %s);"
out = subprocess.run(["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-v",
                      "ON_ERROR_STOP=1", "-c",
                      f"INSERT INTO reports (kind, payload) VALUES ('daily', '{payload.replace(chr(39), chr(39)*2)}');"],
                     capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "PGPASSWORD": ""})
print(payload)
