#!/usr/bin/env python3
"""BEROUN ingest — placeholder market data feed. V L0 bez zdroje (§8).
Drží mód. INVARIANT (§7): mód smí zpřísnit, nikdy uvolnit — watchdogova
eskalace (REDUCE_ONLY) musí přetrvat i během ingestu. Uvolnění módu
(z NO_NEW_RISK na NORMAL) dělá výhradně owner přes §13."""
import os, time
STATE = "/opt/beroun/state"
STRICTER = {"NORMAL": 0, "NO_NEW_RISK": 1, "REDUCE_ONLY": 2, "GRACEFUL_EXIT": 3, "FLAT": 4}

def cur():
    p = f"{STATE}/mode"
    return open(p).read().strip() if os.path.exists(p) else "NO_NEW_RISK"

def set_mode(m):
    with open(f"{STATE}/mode", "w") as f:
        f.write(m)

# L0: feed neexistuje → systém má zůstat v NO_NEW_RISK.
# Zapisujeme jen tehdy, když by to bylo zpřísnění (nikdy nepřebijeme eskalaci).
set_mode("NO_NEW_RISK")
while True:
    time.sleep(5)
    if cur() == "NORMAL":          # jediný režim, který smíme zpřísnit
        set_mode("NO_NEW_RISK")
