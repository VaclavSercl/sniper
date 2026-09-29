#!/usr/bin/env python3
import json, os, socket
SOCK = os.environ.get("BEROUN_GATEWAY_SOCK", "/run/beroun-gateway/gateway.sock")
KERNEL = "/run/beroun/risk_kernel.sock"

def kernel_check(order):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(KERNEL)
    s.sendall(json.dumps(order).encode())
    resp = json.loads(s.recv(65536))
    s.close()
    return resp

if os.path.exists(SOCK):
    os.unlink(SOCK)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(SOCK); os.chmod(SOCK, 0o660); srv.listen(8); srv.settimeout(1.0)
print(f"gateway listening on {SOCK}", flush=True)
while True:
    try:
        conn, _ = srv.accept()
        with conn:
            data = conn.recv(65536)
            try:
                order = json.loads(data)
                resp = kernel_check(order)
                resp["final"] = "REJECTED_L0_NO_VENUE"
            except Exception as e:
                resp = {"final": "FAILED", "error": str(e)}
            conn.sendall(json.dumps(resp).encode())
    except socket.timeout:
        pass
