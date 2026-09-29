#!/usr/bin/env python3
"""BEROUN Binance connector — READ-ONLY část (research/ verze, kandidát do gateway).

Rozsah (vědomě úzký):
  - GET /api/v3/account        (balances) — ověření klíče a sanity
  - GET /api/v3/exchangeInfo   (symboly, filtry)
  - GET /api/v3/ticker/price   (referenční ceny pro price band check)
  - GET /api/v3/openOrders     (reconcile)
  - GET /api/v3/myTrades       (reconcile fills)

ŽÁDNÉ obchodní endpointy. Trading path se implementuje až po read-only
verifikaci v provozu a po §13.

Bezpečnost:
  - Klíč se čte EXKLUZIVNĚ z env proměnných (BINANCE_API_KEY /
    BINANCE_API_SECRET), nikdy ze souboru, argumentu nebo konstanty.
  - Podpora ENV_FILE (cesta k /etc/beroun/gateway.env) — soubor se parsuje
    a NIPLY se nikdy nevypisuje. Výpisy obsahují jen maskované hodnoty.
  - HMAC-SHA256 podpis dle Binance spec (query string, X-MBX-APIKEY header).
  - Timeout 10 s, žádné retry u signature (idempotence řeší volající).

Usage (gateway design):
  from binance_connector import BinanceReadOnly
  c = BinanceReadOnly(env_file="/etc/beroun/gateway.env")
  account = c.account()   # dict: {"canTrade": ..., "balances": [...], ...}

L0 kompatibilita: connector nikdy nic neposílá (žádné POST/PUT/DELETE).
"""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

BASE_URL = "https://api.binance.com"
TIMEOUT_S = 10


class BinanceReadOnly:
    def __init__(self, api_key=None, api_secret=None, env_file=None):
        # Priorita: explicitní argumenty > env proměnné > env soubor.
        # env soubor se čte JEN zde, hodnoty nikdy neopustí instanci.
        if api_key is None or api_secret is None:
            if env_file:
                key, secret = self._load_env_file(env_file)
            else:
                key = os.environ.get("BINANCE_API_KEY")
                secret = os.environ.get("BINANCE_API_SECRET")
        else:
            key, secret = api_key, api_secret

        if not key or not secret:
            raise ValueError("API credentials not available (env/env_file)")
        self._key = key
        self._secret = secret.encode()

    @staticmethod
    def _load_env_file(path):
        key = secret = None
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BINANCE_API_KEY="):
                    key = line.split("=", 1)[1]
                elif line.startswith("BINANCE_API_SECRET="):
                    secret = line.split("=", 1)[1]
        return key, secret

    def _signed_request(self, path, params=None):
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urllib.parse.urlencode(params)
        signature = hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()
        url = f"{BASE_URL}{path}?{query}&signature={signature}"
        req = urllib.request.Request(url, headers={"X-MBX-APIKEY": self._key})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())

    def _public_request(self, path, params=None):
        url = f"{BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())

    # ── public read-only ────────────────────────────────────────────────
    def exchange_info(self, symbol=None):
        return self._public_request("/api/v3/exchangeInfo", {"symbol": symbol} if symbol else None)

    def ticker_price(self, symbol=None):
        return self._public_request("/api/v3/ticker/price", {"symbol": symbol} if symbol else None)

    # ── signed read-only (klíč) ─────────────────────────────────────────
    def account(self):
        return self._signed_request("/api/v3/account")

    def open_orders(self, symbol=None):
        return self._signed_request("/api/v3/openOrders", {"symbol": symbol} if symbol else None)

    def my_trades(self, symbol, limit=100):
        return self._signed_request("/api/v3/myTrades", {"symbol": symbol, "limit": limit})


def verify_key_report(env_file="/etc/beroun/gateway.env"):
    """Sanity report klíče pro §13 deploy: canTrade, canWithdraw (očekáváme
    False), canTrade stav, počet nenulových balances. Nic citlivého."""
    c = BinanceReadOnly(env_file=env_file)
    acct = c.account()
    nonzero = [
        {"asset": b["asset"], "free": b["free"], "locked": b["locked"]}
        for b in acct.get("balances", [])
        if float(b["free"]) > 0 or float(b["locked"]) > 0
    ]
    return {
        "canTrade": acct.get("canTrade"),
        "canWithdraw": acct.get("canWithdraw"),
        "canDeposit": acct.get("canDeposit"),
        "accountType": acct.get("accountType"),
        "permissions": acct.get("permissions", []),
        "nonzero_balances": nonzero,
        "ts": acct.get("updateTime"),
    }


if __name__ == "__main__":
    # Test: načti env soubor a vypiš report (bez citlivých hodnot).
    report = verify_key_report()
    print(json.dumps(report, indent=2))
