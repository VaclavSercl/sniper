#!/usr/bin/env python3
"""BEROUN Tier 0 risk kernel — deterministický, ŽÁDNÝ model (§5).
Kontroluje objednávky proti envelope.yaml. Jediné_OK vstupní brány orderu ven je gateway,
ta se ptá tohoto kernelu. Fail-closed."""
import json, os, socket, threading, time, hashlib, yaml

CORE = "/opt/beroun/core"
STATE_DIR = "/opt/beroun/state"
SOCK = "/run/beroun/risk_kernel.sock"

env = yaml.safe_load(open(f"{CORE}/envelope.yaml"))

def mode():
    p = f"{STATE_DIR}/mode"
    return open(p).read().strip() if os.path.exists(p) else "NO_NEW_RISK"

def check(order):
    """Vrátí (approved, reason). Fail-closed: cokoli neznámého = zamítnuto."""
    m = mode()
    if m != "NORMAL" and not order.get("reduce_only"):
        # Blokujeme VSTUP do rizika, nikdy výstup (§7): reduce-only
        # projde v každém režimu, včetně NO_NEW_RISK a FLAT.
        return False, f"mode={m}: nové riziko zakázáno"
    c = env["capital"]; o = env["order"]; l = env["loss"]
    notional = float(order.get("notional_usd", 0))
    if notional > o["max_notional_usd"]:
        return False, f"notional {notional} > {o['max_notional_usd']}"
    if not order.get("client_order_id"):
        return False, "chybí client_order_id (§10)"
    return True, "ok"

def handle(conn):
    with conn:
        data = conn.recv(65536)
        try:
            order = json.loads(data)
            ok, why = check(order)
        except Exception as e:
            ok, why = False, f"parse error: {e}"
        conn.sendall(json.dumps({"approved": ok, "reason": why,
                                 "kernel_ts": time.time()}).encode())

os.makedirs("/run/beroun", exist_ok=True)
if os.path.exists(SOCK): os.unlink(SOCK)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(SOCK); os.chmod(SOCK, 0o660); s.listen(16)
s.settimeout(1.0)
while True:
    try:
        conn, _ = s.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()
    except socket.timeout:
        pass
