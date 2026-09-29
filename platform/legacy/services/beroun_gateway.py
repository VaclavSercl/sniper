#!/usr/bin/env python3
"""BEROUN gateway v2 — risk kernel check + Binance read-only connector.

P007: přidán read-only Binance datový zdroj (research/binance_connector.py).
Obchodní cesta NEEXISTUJE — žádné POST/PUT/DELETE na order endpointy.
Jediný zápis do světa: nic. Gateway pouze čte.

Zprávy přes unix socket /run/beroun-gateway/gateway.sock (JSON):
  {"type": "order", ...}       → risk kernel → REJECTED_L0_NO_VENUE (L0)
  {"type": "account"}          → Binance GET /api/v3/account (signed)
  {"type": "open_orders", "symbol": "..."} → Binance GET openOrders
  {"type": "my_trades", "symbol": "...", "limit": N} → Binance GET myTrades
  {"type": "ticker", "symbol": "..."} → Binance GET ticker/price (public)
  {"type": "exchange_info", "symbol": "..."} → public
  {"type": "status"} → {"gateway": "ok", "mode": <state>, "binance": "ok"/"error"}
"""
import json
import os
import socket
import sys

SOCK = os.environ.get("BEROUN_GATEWAY_SOCK", "/run/beroun-gateway/gateway.sock")
KERNEL = "/run/beroun/risk_kernel.sock"
ENV_FILE = os.environ.get("BEROUN_GATEWAY_ENV", "/etc/beroun/gateway.env")

sys.path.insert(0, "/opt/sniper/current/platform/runtime")
from binance_connector import BinanceReadOnly  # noqa: E402


def kernel_check(order):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(KERNEL)
    s.sendall(json.dumps(order).encode())
    resp = json.loads(s.recv(65536))
    s.close()
    return resp


def read_mode():
    try:
        with open("/opt/beroun/state/mode") as f:
            return f.read().strip()
    except OSError:
        return "UNKNOWN"


# lazy init — pokud env soubor chybí, gateway doruží kernel-only režim
binance = None
binance_err = None
try:
    binance = BinanceReadOnly(env_file=ENV_FILE)
except Exception as e:  # noqa: BLE001 — závěrná inicializace, detail do statusu
    binance_err = str(e)


def handle(msg):
    t = msg.get("type")

    if t == "order":
        resp = kernel_check(msg)
        resp["final"] = "REJECTED_L0_NO_VENUE"  # dokud L0, žádná venue
        return resp

    if t == "status":
        return {
            "gateway": "ok",
            "mode": read_mode(),
            "binance": "ok" if binance else f"error: {binance_err}",
        }

    if binance is None:
        return {"error": f"binance connector unavailable: {binance_err}"}

    if t == "account":
        return binance.account()
    if t == "open_orders":
        return binance.open_orders(msg.get("symbol"))
    if t == "my_trades":
        return binance.my_trades(msg.get("symbol"), int(msg.get("limit", 100)))
    if t == "ticker":
        return binance.ticker_price(msg.get("symbol"))
    if t == "exchange_info":
        return binance.exchange_info(msg.get("symbol"))

    return {"error": f"unknown type: {t}"}


if os.path.exists(SOCK):
    os.unlink(SOCK)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(SOCK)
os.chmod(SOCK, 0o660)
srv.listen(8)
srv.settimeout(1.0)
print(f"gateway listening on {SOCK} (binance: {'ok' if binance else binance_err})", flush=True)

while True:
    try:
        conn, _ = srv.accept()
        with conn:
            data = conn.recv(65536)
            try:
                resp = handle(json.loads(data))
            except Exception as e:  # noqa: BLE001
                resp = {"error": str(e)}
            conn.sendall(json.dumps(resp).encode())
    except socket.timeout:
        pass
