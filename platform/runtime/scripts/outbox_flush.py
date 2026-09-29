#!/usr/bin/env python3
"""Outbox delivery (§14): doručuje undelivered reports.
Po ÚSPĚŠNÉM doručení denního reportu posílá GET ping na
${HEALTHCHECKS_PING_URL} (env z /etc/beroun/outbox.env, pokud existuje).
Ping je VEDLEJŠÍ: jeho selhání nikdy neznamená selhání reportu.
Not configured = legitimní L0 stav, jen log."""
import json, os, subprocess, time, urllib.request

ENV_FILE = "/etc/beroun/outbox.env"
LOG = "/var/log/beroun/outbox.log"

def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}\n")

# načtení env (volitelné)
ping_url = None
try:
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith("HEALTHCHECKS_PING_URL="):
                ping_url = line.strip().split("=", 1)[1] or None
except FileNotFoundError:
    pass

# doručení reportů
q = "SELECT id, kind FROM reports WHERE delivered_at IS NULL ORDER BY id;"
out = subprocess.run(["psql","-U","beroun","-d","beroun","-t","-A","-F","|","-c",q],
                     capture_output=True, text=True)
delivered_daily = False
for row in [l for l in out.stdout.strip().split("\n") if l]:
    rid, kind = row.split("|", 1)
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} delivered report id={rid} kind={kind} (L0 log channel)\n")
    subprocess.run(["psql","-U","beroun","-d","beroun","-t","-A","-c",
                    f"UPDATE reports SET delivered_at=now() WHERE id={rid};"],
                   capture_output=True)
    if kind == "daily":
        delivered_daily = True

# healthcheck ping — jen po denním reportu
if delivered_daily:
    if ping_url:
        try:
            req = urllib.request.Request(ping_url, method="GET")
            urllib.request.urlopen(req, timeout=10).read()
            log(f"healthcheck ping: OK")
        except Exception as e:
            # retry 2x (celkem 3 pokusy, timeout 10 s každý)
            ok = False
            for _ in range(2):
                time.sleep(3)
                try:
                    urllib.request.urlopen(urllib.request.Request(ping_url, method="GET"), timeout=10).read()
                    ok = True; break
                except Exception:
                    pass
            log(f"healthcheck ping: FAILED after retries ({e})")
    else:
        log("healthcheck ping: not configured")
print("outbox flush done")
