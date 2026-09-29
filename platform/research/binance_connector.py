#!/usr/bin/env python3
"""
BEROUN Binance Connector — READ-ONLY / L0 Shadow Mode
Research candidate for Tier 1 gateway ingress (§0, §8 L4 cycle).

Binance Architecture:
- Largest global cryptocurrency exchange by volume and order book depth.
- Standard-library HTTP client for Binance REST API (v3).
- Zero external dependencies; fail-closed timeout and error handling.
- HMAC-SHA256 authenticated read-only endpoints (if credentials supplied).

Security:
- Credentials loaded strictly from environment (BINANCE_API_KEY / BINANCE_API_SECRET)
  or optional secure env file. Never hardcoded or logged.
- L0 scope: STRICTLY READ-ONLY. Zero order placement (no POST/PUT/DELETE /api/v3/order).
"""

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

BASE_URL = "https://api.binance.com"
DEFAULT_TIMEOUT_S = 10
USER_AGENT = "BEROUN-Harness/2.3 (Linux x86_64)"


class BinanceReadOnly:
    """
    Standard-library HTTP client for Binance Spot API (v3).
    Supports both public market data and authenticated read-only queries.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        env_file: Optional[str] = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ):
        self.timeout_s = timeout_s
        self.base_url = BASE_URL

        # Resolve credentials if available
        if api_key is None or api_secret is None:
            if env_file and os.path.exists(env_file):
                self._key, self._secret = self._load_env_file(env_file)
            else:
                self._key = os.environ.get("BINANCE_API_KEY")
                secret_str = os.environ.get("BINANCE_API_SECRET")
                self._secret = secret_str.encode("utf-8") if secret_str else None
        else:
            self._key = api_key
            self._secret = api_secret.encode("utf-8") if isinstance(api_secret, str) else api_secret

    @staticmethod
    def _load_env_file(path: str) -> tuple[Optional[str], Optional[bytes]]:
        key = None
        secret = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BINANCE_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("BINANCE_API_SECRET="):
                    sec = line.split("=", 1)[1].strip().strip('"').strip("'")
                    secret = sec.encode("utf-8")
        return key, secret

    def _public_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None}
            if clean_params:
                url += "?" + urllib.parse.urlencode(clean_params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _signed_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if not self._key or not self._secret:
            raise ValueError("Authenticated endpoint requested but Binance API credentials not configured.")
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = 5000
        query = urllib.parse.urlencode(p)
        sig = hmac.new(self._secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{self.base_url}{path}?{query}&signature={sig}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "X-MBX-APIKEY": self._key})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ── Public Market Data ──────────────────────────────────────────────

    def ping(self) -> bool:
        """Returns True if Binance API responds to ping."""
        res = self._public_get("/api/v3/ping")
        return res == {}

    def time(self) -> int:
        """Returns server time in milliseconds."""
        res = self._public_get("/api/v3/time")
        return res.get("serverTime", 0)

    def exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Returns exchange rules and symbol trading filters."""
        params = {"symbol": symbol.upper()} if symbol else None
        return self._public_get("/api/v3/exchangeInfo", params)

    def ticker_price(self, symbol: Optional[str] = None) -> Any:
        """Returns latest price dict {'symbol': ..., 'price': ...} or list of all prices."""
        params = {"symbol": symbol.upper()} if symbol else None
        return self._public_get("/api/v3/ticker/price", params)

    def order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Returns order book depth snapshot (bids and asks)."""
        return self._public_get("/api/v3/depth", {"symbol": symbol.upper(), "limit": limit})

    def klines(self, symbol: str, interval: str = "1h", limit: int = 100) -> List[List[Any]]:
        """Returns candlestick klines data."""
        return self._public_get("/api/v3/klines", {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit
        })

    def stats_24hr(self, symbol: Optional[str] = None) -> Any:
        """Returns 24 hour rolling window price change statistics."""
        params = {"symbol": symbol.upper()} if symbol else None
        return self._public_get("/api/v3/ticker/24hr", params)

    # ── Authenticated Read-Only (Auditing & Balances) ───────────────────

    def account(self) -> Dict[str, Any]:
        """Returns current account information including balances and permissions."""
        return self._signed_get("/api/v3/account")

    def open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all currently open orders."""
        params = {"symbol": symbol.upper()} if symbol else None
        return self._signed_get("/api/v3/openOrders", params)

    def my_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns historical trade fills for auditing/reconciliation."""
        return self._signed_get("/api/v3/myTrades", {"symbol": symbol.upper(), "limit": limit})


def generate_venue_health_report() -> Dict[str, Any]:
    """Generates sanitized health check report for Binance integration."""
    client = BinanceReadOnly()
    t0 = time.time()
    t_btc = client.ticker_price("BTCUSDT")
    t_eth = client.ticker_price("ETHUSDT")
    t_sol = client.ticker_price("SOLUSDT")
    latency_ms = round((time.time() - t0) * 1000, 2)

    stats = client.stats_24hr("BTCUSDT")

    return {
        "venue": "binance",
        "role": "Primary Liquidity & Benchmark",
        "endpoint": BASE_URL,
        "latency_ms": latency_ms,
        "sample_mids": {
            "BTCUSDT": float(t_btc["price"]) if "price" in t_btc else None,
            "ETHUSDT": float(t_eth["price"]) if "price" in t_eth else None,
            "SOLUSDT": float(t_sol["price"]) if "price" in t_sol else None,
        },
        "stats_24hr": {
            "symbol": "BTCUSDT",
            "priceChangePercent": stats.get("priceChangePercent"),
            "highPrice": stats.get("highPrice"),
            "lowPrice": stats.get("lowPrice"),
            "volume": stats.get("volume"),
            "quoteVolume": stats.get("quoteVolume"),
        },
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "HEALTHY_READ_ONLY"
    }


if __name__ == "__main__":
    report = generate_venue_health_report()
    print(json.dumps(report, indent=2))
