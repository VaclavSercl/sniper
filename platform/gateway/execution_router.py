#!/usr/bin/env python3
"""
BEROUN Unified Multi-Venue Execution Router (Tier 1 Architecture)
Supports Tri-Venue Core:
  1. Binance (Primary Liquidity & Global Benchmark)
  2. Bitfinex (Zero Maker Fee Specialist & Low-Latency EU Gateway)
  3. Hyperliquid (Decentralized L1 Perpetual DEX & Non-Custodial Agent Wallets)

Guarantees BEROUN Constitution §0-§15 Invariants:
  - Fail-closed capability ladder (L0: Shadow / Simulated, L1: Testnet, L2: Live Mikro).
  - Strict read-only / zero-execution safety by default in L0.
  - Zero external dependencies (pure Python 3 standard library).
"""

import dataclasses
import hashlib
import hmac
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

USER_AGENT = "BEROUN-Harness/2.3 (Linux x86_64)"


@dataclasses.dataclass
class OrderRequest:
    venue: str                 # "binance" | "bitfinex" | "hyperliquid"
    symbol: str                # e.g. "BTC", "ETH", "SOL", "HYPE" or "BTCUSDT"
    side: str                  # "buy" | "sell"
    order_type: str            # "limit" | "market"
    price: float               # limit price (USD / USDT)
    quantity: float            # base asset quantity
    post_only: bool = True     # maker order (critical for 0% fee / rebate)
    reduce_only: bool = False  # position reduction (for perps)
    client_order_id: Optional[str] = None
    capability_level: str = "L0"  # "L0" (Shadow), "L1" (Testnet), "L2" (Live Mikro)

    def validate(self) -> None:
        if self.capability_level not in ('L0', 'L1', 'L2'):
            raise ValueError('Unknown execution capability')
        if any(not isinstance(v, str) or not v or len(v) > 80
               for v in (self.venue, self.symbol, self.side, self.order_type)):
            raise ValueError('Invalid order identity')
        if type(self.post_only) is not bool or type(self.reduce_only) is not bool:
            raise ValueError('Order flags must be boolean')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
               not math.isfinite(v) for v in (self.price, self.quantity)):
            raise ValueError('Nonfinite or invalid price/quantity')
        venue_lower = self.venue.lower()
        if venue_lower not in ("binance", "bitfinex", "hyperliquid"):
            raise ValueError(f"Unsupported venue '{self.venue}'. Must be 'binance', 'bitfinex', or 'hyperliquid'.")
        side_lower = self.side.lower()
        if side_lower not in ("buy", "sell"):
            raise ValueError(f"Invalid side '{self.side}'. Must be 'buy' or 'sell'.")
        type_lower = self.order_type.lower()
        if type_lower not in ("limit", "market"):
            raise ValueError(f"Invalid order_type '{self.order_type}'. Must be 'limit' or 'market'.")
        if self.price <= 0 and type_lower == "limit":
            raise ValueError(f"Price must be positive for limit order, got {self.price}")
        if self.quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {self.quantity}")


class BinanceExecutionAdapter:
    """Binance Spot execution adapter (HMAC-SHA256)."""

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.api_key = api_key or os.environ.get("BINANCE_API_KEY")
        secret_str = api_secret or os.environ.get("BINANCE_API_SECRET")
        self.api_secret = secret_str.encode("utf-8") if secret_str else None
        self.testnet_url = "https://testnet.binance.vision"
        self.live_url = "https://api.binance.com"

    def normalize_symbol(self, sym: str) -> str:
        s = sym.upper()
        if s in ("BTC", "ETH", "SOL"):
            return f"{s}USDT"
        return s

    def build_payload(self, req: OrderRequest) -> Dict[str, Any]:
        cid = req.client_order_id or f"beroun_{int(time.time() * 1000)}"
        symbol = self.normalize_symbol(req.symbol)
        side = req.side.upper()
        payload: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT_MAKER" if (req.post_only and req.order_type.lower() == "limit") else req.order_type.upper(),
            "quantity": f"{req.quantity:.6f}".rstrip("0").rstrip("."),
            "newClientOrderId": cid,
        }
        if req.order_type.lower() == "limit":
            payload["price"] = f"{req.price:.2f}"
            if payload["type"] == "LIMIT":
                payload["timeInForce"] = "GTC"
        return payload

    def execute(self, req: OrderRequest) -> Dict[str, Any]:
        req.validate()
        payload = self.build_payload(req)

        # L0 SHADOW ENFORCEMENT: Never send real orders over the wire
        if req.capability_level == "L0":
            return {
                "venue": "binance",
                "mode": "L0_SHADOW",
                "action": "SIMULATED_ORDER_ACCEPTED",
                "client_order_id": payload.get("newClientOrderId"),
                "symbol": payload.get("symbol"),
                "side": payload.get("side"),
                "price": payload.get("price"),
                "quantity": payload.get("quantity"),
                "post_only": req.post_only,
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "status": "ACCEPTED_SIMULATED",
                "final": "REJECTED_L0_NO_VENUE"
            }

        # L1 / L2 Dispatch (requires valid credentials)
        if not self.api_key or not self.api_secret:
            raise ValueError("Binance execution requested in L1/L2 but API credentials are not configured.")

        base_url = self.testnet_url if req.capability_level == "L1" else self.live_url
        params = dict(payload)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urllib.parse.urlencode(params)
        sig = hmac.new(self.api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{base_url}/api/v3/order?{query}&signature={sig}"

        http_req = urllib.request.Request(
            url,
            data=b"",
            headers={"User-Agent": USER_AGENT, "X-MBX-APIKEY": self.api_key},
            method="POST"
        )
        with urllib.request.urlopen(http_req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))


class BitfinexExecutionAdapter:
    """Bitfinex Spot execution adapter (HMAC-SHA384, post-only flag 4096)."""

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.api_key = api_key or os.environ.get("BITFINEX_API_KEY")
        sec_str = api_secret or os.environ.get("BITFINEX_API_SECRET")
        self.api_secret = sec_str.encode("utf-8") if sec_str else None
        self.base_url = "https://api.bitfinex.com/v2"

    def normalize_symbol(self, sym: str) -> str:
        s = sym.upper()
        if not s.startswith("T"):
            if s in ("BTC", "ETH", "SOL"):
                return f"t{s}USD"
            return f"t{s}"
        return s

    def build_payload(self, req: OrderRequest) -> Dict[str, Any]:
        symbol = self.normalize_symbol(req.symbol)
        # In Bitfinex, buy is positive amount, sell is negative amount
        amount = req.quantity if req.side.lower() == "buy" else -req.quantity
        flags = 0
        if req.post_only:
            flags |= 4096  # Post-Only flag
        if req.reduce_only:
            flags |= 512   # Close position flag

        cid = int(time.time() * 1000) % 2147483647
        payload: Dict[str, Any] = {
            "type": "EXCHANGE LIMIT" if req.order_type.lower() == "limit" else "EXCHANGE MARKET",
            "symbol": symbol,
            "amount": f"{amount:.6f}".rstrip("0").rstrip("."),
            "cid": cid,
            "flags": flags
        }
        if req.order_type.lower() == "limit":
            payload["price"] = f"{req.price:.2f}"
        return payload

    def execute(self, req: OrderRequest) -> Dict[str, Any]:
        req.validate()
        payload = self.build_payload(req)

        # L0 SHADOW ENFORCEMENT
        if req.capability_level == "L0":
            return {
                "venue": "bitfinex",
                "mode": "L0_SHADOW",
                "action": "SIMULATED_ORDER_ACCEPTED",
                "cid": payload.get("cid"),
                "symbol": payload.get("symbol"),
                "amount": payload.get("amount"),
                "price": payload.get("price"),
                "flags": payload.get("flags"),
                "post_only": req.post_only,
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "status": "ACCEPTED_SIMULATED",
                "final": "REJECTED_L0_NO_VENUE"
            }

        if not self.api_key or not self.api_secret:
            raise ValueError("Bitfinex execution requested in L1/L2 but API credentials are not configured.")

        nonce = str(int(time.time() * 1000000))
        body = json.dumps(payload)
        sig_path = "/api/v2/auth/w/order/submit"
        msg = f"{sig_path}{nonce}{body}"
        signature = hmac.new(self.api_secret, msg.encode("utf-8"), hashlib.sha384).hexdigest()

        url = f"{self.base_url}/auth/w/order/submit"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "bfx-nonce": nonce,
            "bfx-apikey": self.api_key,
            "bfx-signature": signature
        }

        http_req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(http_req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))


class HyperliquidExecutionAdapter:
    """Legacy L0 diagnostics only; signed testnet operations use hyperliquid_orders.

    A legacy float request has no explicit product, token identity or qualified
    mandate. It can never be promoted to an executable wire payload.
    """

    def __init__(self, agent_address: Optional[str] = None):
        # Keep constructor compatibility without reading credentials in L0.
        self.agent_address = agent_address

    def normalize_symbol(self, sym: str) -> str:
        return sym.upper()

    def build_payload(self, req: OrderRequest) -> Dict[str, Any]:
        coin = self.normalize_symbol(req.symbol)
        req.validate()
        if req.capability_level != 'L0':
            raise ValueError('Legacy Hyperliquid execution disabled; use explicit testnet client')
        asset_id = None
        is_buy = req.side.lower() == "buy"

        # Time-in-force: "Alo" = Add Liquidity Only (Post-Only maker), "Gtc" = Good Til Cancel
        tif = "Alo" if req.post_only else "Gtc"
        order_spec = {
            "a": asset_id,
            "b": is_buy,
            "p": str(req.price),
            "s": str(req.quantity),
            "r": req.reduce_only,
            "t": {"limit": {"tif": tif}}
        }

        action = {
            "type": "order",
            "orders": [order_spec],
            "grouping": "na"
        }

        return {
            "action": action,
            "coin": coin,
            "asset_id": asset_id,
            "wire_ready": False,
            "unresolved": ['product', 'asset_metadata', 'precision', 'account', 'mandate']
        }

    def execute(self, req: OrderRequest) -> Dict[str, Any]:
        req.validate()
        payload = self.build_payload(req)

        # L0 SHADOW ENFORCEMENT
        if req.capability_level == "L0":
            return {
                "venue": "hyperliquid",
                "mode": "L0_SHADOW",
                "action": "SIMULATED_ORDER_ACCEPTED",
                "coin": payload.get("coin"),
                "asset_id": payload.get("asset_id"),
                "wire_ready": False,
                "unresolved": payload['unresolved'],
                "order_spec": payload["action"]["orders"][0],
                "post_only": req.post_only,
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "status": "ACCEPTED_SIMULATED",
                "final": "REJECTED_L0_NO_VENUE"
            }

        raise ValueError('Legacy Hyperliquid execution disabled')


class UnifiedExecutionRouter:
    """Central router managing order execution dispatch across the Tri-Venue Core."""

    def __init__(self, capability_level: str = "L0"):
        if capability_level not in ('L0', 'L1', 'L2'):
            raise ValueError('Unknown execution capability')
        self.capability_level = capability_level
        self.adapters = {
            "binance": BinanceExecutionAdapter(),
            "bitfinex": BitfinexExecutionAdapter(),
            "hyperliquid": HyperliquidExecutionAdapter()
        }

    def route_order(self, req: OrderRequest) -> Dict[str, Any]:
        req.capability_level = self.capability_level
        venue_key = req.venue.lower()
        adapter = self.adapters.get(venue_key)
        if not adapter:
            raise ValueError(f"Unknown venue '{req.venue}'")
        return adapter.execute(req)


if __name__ == "__main__":
    router = UnifiedExecutionRouter(capability_level="L0")
    print("[ROUTER] Testing L0 Shadow order dispatch for Tri-Venue Core...")

    orders = [
        OrderRequest("binance", "BTC", "buy", "limit", 81500.0, 0.005, post_only=True),
        OrderRequest("bitfinex", "BTC", "buy", "limit", 81450.0, 0.005, post_only=True),
        OrderRequest("hyperliquid", "BTC", "buy", "limit", 81550.0, 0.005, post_only=True),
    ]

    for o in orders:
        res = router.route_order(o)
        print(f"\nOrder for {o.venue.upper()}:")
        print(json.dumps(res, indent=2))
