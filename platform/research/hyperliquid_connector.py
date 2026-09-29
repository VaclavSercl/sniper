#!/usr/bin/env python3
"""
BEROUN Hyperliquid (HYPE) Connector — READ-ONLY / L0 Shadow Mode
Research candidate for new DEX venue integration (§0, §8 L4 venue cycle).

Hyperliquid Architecture:
- High-performance decentralized L1 perpetual & spot exchange (HYPE ecosystem).
- REST API (POST to /info) with sub-10ms network latency to European edge.
- Non-custodial Agent Wallets: API keys are scoped ephemeral keys with
  ZERO withdrawal capabilities (100% compliant with BEROUN Constitution §1 & §3).

Endpoints:
- Mainnet: https://api.hyperliquid.xyz/info
- Testnet: https://api.hyperliquid-testnet.xyz/info
- WebSocket: wss://api.hyperliquid.xyz/ws

Scope in L0:
- GET all_mids, ticker, meta, asset_contexts, l2_book (Market data ingest)
- GET clearinghouse_state, open_orders, user_fills (Reconcile / audit)
- NO trading path (POST /exchange) until §9 validation and §13 owner approval.
"""

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

MAINNET_INFO_URL = "https://api.hyperliquid.xyz/info"
TESTNET_INFO_URL = "https://api.hyperliquid-testnet.xyz/info"
DEFAULT_TIMEOUT_S = 10


class HyperliquidReadOnly:
    """
    Standard-library HTTP client for Hyperliquid Info API.
    Zero external dependencies; fail-closed timeout and error handling.
    """

    def __init__(self, testnet: bool = False, timeout_s: int = DEFAULT_TIMEOUT_S):
        self.info_url = TESTNET_INFO_URL if testnet else MAINNET_INFO_URL
        self.timeout_s = timeout_s

    def _post_info(self, payload: Dict[str, Any]) -> Any:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.info_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "BEROUN-Harness/2.3 (Linux x86_64)"
            }
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            raw = resp.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('Public response too large')
            return json.loads(raw.decode("utf-8"))

    # ── Market Data Endpoints (Public) ──────────────────────────────────

    def all_mids(self) -> Dict[str, str]:
        """Returns map of coin/symbol to mid price string."""
        return self._post_info({"type": "allMids"})

    def ticker(self, symbol: str = "BTC") -> Optional[float]:
        """Returns current mid price float for symbol (e.g. 'BTC', 'HYPE', 'ETH')."""
        mids = self.all_mids()
        val = mids.get(symbol.upper())
        return float(val) if val is not None else None

    def meta(self) -> Dict[str, Any]:
        """Returns perpetual market metadata (universe of assets, max leverage, tick size)."""
        return self._post_info({"type": "meta"})

    def meta_and_asset_contexts(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Returns (meta, asset_contexts) containing:
        - universe parameters (max leverage, size decimals)
        - mark price, funding rate, open interest, 24h volume
        """
        raw = self._post_info({"type": "metaAndAssetCtxs"})
        return raw[0], raw[1]

    def spot_meta(self) -> Dict[str, Any]:
        """Returns spot tokens, pairs, and asset identifiers."""
        return self._post_info({"type": "spotMeta"})

    def l2_book(self, symbol: str, n_levels: int = 5) -> Dict[str, Any]:
        """Returns Level 2 order book snapshot with bids and asks."""
        return self._post_info({"type": "l2Book", "coin": symbol.upper(), "nSigFigs": n_levels})

    # ── Account & Audit Endpoints (Read-Only / Address lookup) ──────────

    def clearinghouse_state(self, wallet_address: str) -> Dict[str, Any]:
        """
        Returns margin summary, equity, and open positions for a given Ethereum address.
        """
        return self._post_info({"type": "clearinghouseState", "user": wallet_address.lower()})

    def open_orders(self, wallet_address: str) -> List[Dict[str, Any]]:
        """Returns active open resting orders for a given wallet address."""
        return self._post_info({"type": "openOrders", "user": wallet_address.lower()})

    def user_fills(self, wallet_address: str) -> List[Dict[str, Any]]:
        """Returns recent trade fills for reconciliation audit."""
        return self._post_info({"type": "userFills", "user": wallet_address.lower()})


def generate_venue_health_report(testnet: bool = False) -> Dict[str, Any]:
    """Generates sanitized health check report for Hyperliquid integration."""
    client = HyperliquidReadOnly(testnet=testnet)
    t0 = time.time()
    mids = client.all_mids()
    latency_ms = round((time.time() - t0) * 1000, 2)

    sample_symbols = ["BTC", "ETH", "SOL", "HYPE"]
    sample_prices = {s: float(mids[s]) for s in sample_symbols if s in mids}

    # Asset context sample for BTC & HYPE
    meta, ctxs = client.meta_and_asset_contexts()
    universe = meta.get("universe", [])
    funding_info = {}
    for i, u in enumerate(universe):
        name = u.get("name")
        if name in ("BTC", "HYPE"):
            ctx = ctxs[i] if i < len(ctxs) else {}
            funding_info[name] = {
                "max_leverage": u.get("maxLeverage"),
                "mark_price": ctx.get("markPx"),
                "funding_rate": ctx.get("funding"),
                "open_interest": ctx.get("openInterest")
            }

    return {
        "venue": "hyperliquid",
        "network": "testnet" if testnet else "mainnet",
        "endpoint": client.info_url,
        "latency_ms": latency_ms,
        "total_active_markets": len(mids),
        "sample_mids": sample_prices,
        "asset_metrics": funding_info,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "HEALTHY_READ_ONLY"
    }


if __name__ == "__main__":
    report = generate_venue_health_report(testnet=False)
    print(json.dumps(report, indent=2))
