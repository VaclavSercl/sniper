#!/usr/bin/env python3
"""
BEROUN Bitfinex Connector — READ-ONLY / L0 Shadow Mode
Research candidate for Tier 1 gateway ingress (§0, §8 L4 cycle).

Bitfinex Architecture:
- Zero Maker Fee trading environment (0% maker fee on selected pairs/tiers).
- Lowest European network latency from Prague: ~2.2 ms RTT ping, ~30-50 ms REST.
- Standard-library HTTP client for Bitfinex REST API (v2).
- Zero external dependencies; fail-closed timeout and error handling.
- HMAC-SHA384 authenticated read-only endpoints (if credentials supplied).

Rate Limit Guard:
- Bitfinex enforces strict IP limits: 10–90 req/min for public REST.
- Order limit per account: 1 000 orders / 5 min (~3.3 orders/sec).
- Built-in SlidingWindowGovernor enforces safe rate dispatch and fail-closed backoff.

Security:
- Credentials loaded strictly from environment (BITFINEX_API_KEY / BITFINEX_API_SECRET)
  or optional secure env file. Never hardcoded or logged.
- L0 scope: STRICTLY READ-ONLY. Zero order placement (no POST /v2/auth/w/order/submit).
"""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

PUBLIC_BASE_URL = "https://api-pub.bitfinex.com/v2"
AUTH_BASE_URL = "https://api.bitfinex.com/v2"
DEFAULT_TIMEOUT_S = 10
USER_AGENT = "BEROUN-Harness/2.3 (Linux x86_64)"


class BitfinexRateLimitError(Exception):
    """Raised when Bitfinex rate limit is approached or violated."""
    pass


class SlidingWindowGovernor:
    """
    Client-side rate limit governor to prevent IP-level bans (ERR_RATE_LIMIT).
    Enforces a strict maximum requests per time window.
    """

    def __init__(self, max_calls: int = 60, window_seconds: float = 60.0):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.calls: List[float] = []

    def acquire(self) -> None:
        now = time.time()
        # Discard calls older than the sliding window
        self.calls = [t for t in self.calls if now - t < self.window_seconds]
        if len(self.calls) >= self.max_calls:
            oldest = self.calls[0]
            sleep_needed = self.window_seconds - (now - oldest)
            if sleep_needed > 0:
                time.sleep(sleep_needed)
        self.calls.append(time.time())


class BitfinexReadOnly:
    """
    Standard-library HTTP client for Bitfinex API v2.
    Supports public market data and authenticated read-only audit endpoints.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        env_file: Optional[str] = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        max_req_per_min: int = 60,
    ):
        self.timeout_s = timeout_s
        self.governor = SlidingWindowGovernor(max_calls=max_req_per_min, window_seconds=60.0)

        # Resolve credentials
        if api_key is None or api_secret is None:
            if env_file and os.path.exists(env_file):
                self._key, self._secret = self._load_env_file(env_file)
            else:
                self._key = os.environ.get("BITFINEX_API_KEY")
                sec_str = os.environ.get("BITFINEX_API_SECRET")
                self._secret = sec_str.encode("utf-8") if sec_str else None
        else:
            self._key = api_key
            self._secret = api_secret.encode("utf-8") if isinstance(api_secret, str) else api_secret

    @staticmethod
    def _load_env_file(path: str) -> Tuple[Optional[str], Optional[bytes]]:
        key = None
        secret = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BITFINEX_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("BITFINEX_API_SECRET="):
                    sec = line.split("=", 1)[1].strip().strip('"').strip("'")
                    secret = sec.encode("utf-8")
        return key, secret

    def _public_get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self.governor.acquire()
        url = f"{PUBLIC_BASE_URL}{endpoint}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list) and len(data) >= 2 and data[0] == "error":
                    raise RuntimeError(f"Bitfinex API Error: {data}")
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise BitfinexRateLimitError(f"Bitfinex 429 Too Many Requests on {url}") from e
            raise

    def _auth_post(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        if not self._key or not self._secret:
            raise ValueError("Authenticated endpoint requested but Bitfinex API credentials not configured.")
        self.governor.acquire()

        # Bitfinex v2 auth requires microsecond nonce or strictly increasing millisecond nonce
        nonce = str(int(time.time() * 1000000))
        body = json.dumps(payload or {})
        # Signature format: /api/v2/{path}{nonce}{body}
        sig_path = f"/api/v2/{path.lstrip('/')}"
        msg = f"{sig_path}{nonce}{body}"
        signature = hmac.new(self._secret, msg.encode("utf-8"), hashlib.sha384).hexdigest()

        url = f"{AUTH_BASE_URL}/{path.lstrip('/')}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "bfx-nonce": nonce,
            "bfx-apikey": self._key,
            "bfx-signature": signature,
        }

        req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list) and len(data) >= 2 and data[0] == "error":
                    raise RuntimeError(f"Bitfinex Auth Error: {data}")
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise BitfinexRateLimitError(f"Bitfinex 429 Rate Limit on auth endpoint {url}") from e
            raise

    # ── Public Endpoints ────────────────────────────────────────────────

    def platform_status(self) -> bool:
        """Returns True if Bitfinex matching engine and platform is operational ([1])."""
        res = self._public_get("/platform/status")
        return res == [1]

    @staticmethod
    def _to_float(val: Any, default: float = 0.0) -> float:
        return float(val) if val is not None else default

    def ticker(self, symbol: str = "tBTCUSD") -> Dict[str, Any]:
        """
        Returns parsed ticker dict for a symbol.
        Bitfinex format: [BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_RELATIVE, LAST_PRICE, VOLUME, HIGH, LOW]
        """
        sym = symbol if symbol.startswith("t") else f"t{symbol}"
        raw = self._public_get(f"/ticker/{sym}")
        if not isinstance(raw, list) or len(raw) < 10:
            raise ValueError(f"Unexpected ticker response: {raw}")

        bid = self._to_float(raw[0])
        ask = self._to_float(raw[2])
        return {
            "symbol": sym,
            "bid": bid,
            "bid_size": self._to_float(raw[1]),
            "ask": ask,
            "ask_size": self._to_float(raw[3]),
            "daily_change": self._to_float(raw[4]),
            "daily_change_relative": self._to_float(raw[5]),
            "last_price": self._to_float(raw[6]),
            "volume": self._to_float(raw[7]),
            "high": self._to_float(raw[8]),
            "low": self._to_float(raw[9]),
            "mid": round((bid + ask) / 2.0, 4) if (bid or ask) else 0.0
        }

    def tickers(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Returns multiple tickers in a single batch HTTP call."""
        formatted = [s if s.startswith("t") else f"t{s}" for s in symbols]
        raw = self._public_get("/tickers", {"symbols": ",".join(formatted)})
        results = {}
        for item in raw:
            if isinstance(item, list) and len(item) >= 11:
                sym = item[0]
                bid = self._to_float(item[1])
                ask = self._to_float(item[3])
                results[sym] = {
                    "symbol": sym,
                    "bid": bid,
                    "bid_size": self._to_float(item[2]),
                    "ask": ask,
                    "ask_size": self._to_float(item[4]),
                    "daily_change": self._to_float(item[5]),
                    "daily_change_relative": self._to_float(item[6]),
                    "last_price": self._to_float(item[7]),
                    "volume": self._to_float(item[8]),
                    "high": self._to_float(item[9]),
                    "low": self._to_float(item[10]),
                    "mid": round((bid + ask) / 2.0, 4) if (bid or ask) else 0.0
                }
        return results

    def order_book(self, symbol: str = "tBTCUSD", precision: str = "P0", length: int = 25) -> Dict[str, Any]:
        """
        Returns parsed order book {bids: [{'price': ..., 'count': ..., 'amount': ...}], asks: [...]}.
        Valid lengths: 1, 25, 100.
        """
        sym = symbol if symbol.startswith("t") else f"t{symbol}"
        raw = self._public_get(f"/book/{sym}/{precision}", {"len": length})
        bids = []
        asks = []
        for entry in raw:
            price, count, amount = float(entry[0]), int(entry[1]), float(entry[2])
            if count > 0:
                if amount > 0:
                    bids.append({"price": price, "count": count, "amount": amount})
                else:
                    asks.append({"price": price, "count": count, "amount": abs(amount)})
        return {"symbol": sym, "bids": bids, "asks": asks}

    def trades(self, symbol: str = "tBTCUSD", limit: int = 50) -> List[Dict[str, Any]]:
        """Returns recent public execution trades."""
        sym = symbol if symbol.startswith("t") else f"t{symbol}"
        raw = self._public_get(f"/trades/{sym}/hist", {"limit": limit})
        trades = []
        for t in raw:
            # [ID, MTS, AMOUNT, PRICE]
            trades.append({
                "id": t[0],
                "timestamp_ms": t[1],
                "amount": abs(float(t[2])),
                "side": "buy" if float(t[2]) > 0 else "sell",
                "price": float(t[3])
            })
        return trades

    def candles(self, symbol: str = "tBTCUSD", timeframe: str = "1h", limit: int = 50) -> List[Dict[str, Any]]:
        """Returns candlestick bars [MTS, OPEN, CLOSE, HIGH, LOW, VOLUME]."""
        sym = symbol if symbol.startswith("t") else f"t{symbol}"
        raw = self._public_get(f"/candles/trade:{timeframe}:{sym}/hist", {"limit": limit})
        candles = []
        for c in raw:
            candles.append({
                "timestamp_ms": c[0],
                "open": float(c[1]),
                "close": float(c[2]),
                "high": float(c[3]),
                "low": float(c[4]),
                "volume": float(c[5])
            })
        return candles

    # ── Authenticated Read-Only (Auditing & Balances) ───────────────────

    def wallets(self) -> List[Dict[str, Any]]:
        """Returns wallet balances (exchange, margin, funding)."""
        return self._auth_post("auth/r/wallets")

    def active_orders(self) -> List[Dict[str, Any]]:
        """Returns active resting orders."""
        return self._auth_post("auth/r/orders")

    def positions(self) -> List[Dict[str, Any]]:
        """Returns active margin positions."""
        return self._auth_post("auth/r/positions")


def generate_venue_health_report() -> Dict[str, Any]:
    """Generates sanitized health check report for Bitfinex integration."""
    client = BitfinexReadOnly()
    t0 = time.time()
    operational = client.platform_status()
    batch = client.tickers(["tBTCUSD", "tETHUSD", "tSOLUSD"])
    latency_ms = round((time.time() - t0) * 1000, 2)

    sample_mids = {sym: data["mid"] for sym, data in batch.items()}
    btc_meta = batch.get("tBTCUSD", {})

    return {
        "venue": "bitfinex",
        "role": "Zero Maker Fee Specialist & Low-Latency EU Gateway",
        "endpoint": PUBLIC_BASE_URL,
        "operational": operational,
        "latency_ms": latency_ms,
        "sample_mids": sample_mids,
        "btc_stats": {
            "bid": btc_meta.get("bid"),
            "ask": btc_meta.get("ask"),
            "spread": round(btc_meta.get("ask", 0) - btc_meta.get("bid", 0), 2),
            "daily_change_pct": round(btc_meta.get("daily_change_relative", 0) * 100, 3),
            "volume_24h": btc_meta.get("volume"),
        },
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "HEALTHY_READ_ONLY" if operational else "DEGRADED"
    }


if __name__ == "__main__":
    report = generate_venue_health_report()
    print(json.dumps(report, indent=2))
