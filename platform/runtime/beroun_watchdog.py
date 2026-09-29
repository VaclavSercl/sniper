#!/usr/bin/env python3
"""BEROUN watchdog — liveness token (§7-L3). Jiné UID než agent.
Expirace → REDUCE_ONLY (fail-closed, ne FLAT). Obnovení tokenu → návrat
pouze na NO_NEW_RISK (nikdy na NORMAL — nové riziko povoluje jen owner)."""
import os, time
STATE = "/opt/beroun/state"
LOG = "/var/log/beroun/watchdog.log"
LATCH = f"{STATE}/liveness_latch"
TTL = 72 * 3600

def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}\n")

def cur():
    p = f"{STATE}/mode"
    return open(p).read().strip() if os.path.exists(p) else "NO_NEW_RISK"

def set_mode(m):
    with open(f"{STATE}/mode", "w") as f:
        f.write(m)

while True:
    tok = f"{STATE}/liveness_token"
    try:
        age = time.time() - os.path.getmtime(tok)
        if age > TTL:
            if cur() not in ("REDUCE_ONLY", "GRACEFUL_EXIT", "FLAT"):
                set_mode("REDUCE_ONLY")
                open(LATCH, "w").write(str(int(time.time())))
                log(f"LIVENESS EXPIRED (age={age:.0f}s) -> REDUCE_ONLY")
        else:
            # token čerstvý: pokud watchdog dříve eskaloval, vrať na NO_NEW_RISK
            if os.path.exists(LATCH):
                set_mode("NO_NEW_RISK")
                os.unlink(LATCH)
                log(f"LIVENESS RESTORED (age={age:.0f}s) -> NO_NEW_RISK (nové riziko zůstává zakázané)")
    except FileNotFoundError:
        pass  # token nevydán — default NO_NEW_RISK platí
    time.sleep(30)
