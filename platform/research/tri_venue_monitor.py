#!/usr/bin/env python3
"""
BEROUN Tri-Venue Market Dislocation & Infrastructure Monitor
Compares market conditions across the Tri-Venue Core:
1. Binance (Primary Liquidity & Price Benchmark)
2. Bitfinex (Zero Maker Fee Specialist & Low-Latency EU Gateway)
3. Hyperliquid (Decentralized L1 Perpetual DEX & Non-Custodial Agent Wallets)

Zero external dependencies (pure Python 3 standard library).
Strictly READ-ONLY / L0 Shadow Mode compliant (§0, §8, §13).
"""

import json
import sys
import time
from typing import Any, Dict, Optional

from binance_connector import BinanceReadOnly
from bitfinex_connector import BitfinexReadOnly
from hyperliquid_connector import HyperliquidReadOnly


def collect_tri_venue_snapshot() -> Dict[str, Any]:
    """Polls Binance, Bitfinex, and Hyperliquid to construct a cross-venue pricing snapshot."""
    binance = BinanceReadOnly()
    bitfinex = BitfinexReadOnly()
    hyperliquid = HyperliquidReadOnly()

    snapshot_ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    results: Dict[str, Any] = {
        "timestamp": snapshot_ts,
        "venues": {},
        "comparisons": {},
        "funding_rates": {},
    }

    # 1. Binance Poll
    t0 = time.time()
    b_btc = binance.ticker_price("BTCUSDT")
    b_eth = binance.ticker_price("ETHUSDT")
    b_sol = binance.ticker_price("SOLUSDT")
    binance_lat = round((time.time() - t0) * 1000, 1)

    p_binance = {
        "BTC": float(b_btc["price"]) if "price" in b_btc else None,
        "ETH": float(b_eth["price"]) if "price" in b_eth else None,
        "SOL": float(b_sol["price"]) if "price" in b_sol else None,
    }
    results["venues"]["binance"] = {
        "name": "Binance",
        "type": "CEX (Spot / Benchmark)",
        "latency_ms": binance_lat,
        "maker_fee_pct": 0.02,  # VIP/BNB standard
        "taker_fee_pct": 0.05,
        "prices": p_binance,
    }

    # 2. Bitfinex Poll
    t0 = time.time()
    bfx_batch = bitfinex.tickers(["tBTCUSD", "tETHUSD", "tSOLUSD"])
    bitfinex_lat = round((time.time() - t0) * 1000, 1)

    p_bitfinex = {
        "BTC": bfx_batch.get("tBTCUSD", {}).get("mid"),
        "ETH": bfx_batch.get("tETHUSD", {}).get("mid"),
        "SOL": bfx_batch.get("tSOLUSD", {}).get("mid"),
    }
    results["venues"]["bitfinex"] = {
        "name": "Bitfinex",
        "type": "CEX (0% Maker Fee)",
        "latency_ms": bitfinex_lat,
        "maker_fee_pct": 0.00,  # Zero maker fee
        "taker_fee_pct": 0.20,
        "prices": p_bitfinex,
    }

    # 3. Hyperliquid Poll
    t0 = time.time()
    hl_mids = hyperliquid.all_mids()
    meta, ctxs = hyperliquid.meta_and_asset_contexts()
    hl_lat = round((time.time() - t0) * 1000, 1)

    p_hyperliquid = {
        "BTC": float(hl_mids["BTC"]) if "BTC" in hl_mids else None,
        "ETH": float(hl_mids["ETH"]) if "ETH" in hl_mids else None,
        "SOL": float(hl_mids["SOL"]) if "SOL" in hl_mids else None,
        "HYPE": float(hl_mids["HYPE"]) if "HYPE" in hl_mids else None,
    }
    results["venues"]["hyperliquid"] = {
        "name": "Hyperliquid",
        "type": "DEX L1 (Non-Custodial / Perp)",
        "latency_ms": hl_lat,
        "maker_fee_pct": -0.02,  # Maker rebate / sub-zero
        "taker_fee_pct": 0.035,
        "prices": p_hyperliquid,
    }

    # Extract funding rates from Hyperliquid asset contexts
    universe = meta.get("universe", [])
    for idx, asset in enumerate(universe):
        coin = asset.get("name")
        if coin in ("BTC", "ETH", "SOL", "HYPE") and idx < len(ctxs):
            ctx = ctxs[idx]
            raw_funding = float(ctx.get("funding", 0.0))
            # Hyperliquid funding is hourly: 1h * 24 * 365 = Annualized %
            annualized_pct = round(raw_funding * 24 * 365 * 100, 2)
            results["funding_rates"][coin] = {
                "hourly_rate": raw_funding,
                "annualized_pct": annualized_pct,
                "open_interest": ctx.get("openInterest"),
            }

    # 4. Cross-Venue Dislocation Calculations
    for coin in ["BTC", "ETH", "SOL"]:
        pb = p_binance.get(coin)
        pbfx = p_bitfinex.get(coin)
        phl = p_hyperliquid.get(coin)

        if pb and pbfx and phl:
            # Binance vs Bitfinex
            diff_bfx_bin = pbfx - pb
            diff_bfx_bin_pct = (diff_bfx_bin / pb) * 100

            # Hyperliquid vs Binance
            diff_hl_bin = phl - pb
            diff_hl_bin_pct = (diff_hl_bin / pb) * 100

            # Hyperliquid vs Bitfinex
            diff_hl_bfx = phl - pbfx
            diff_hl_bfx_pct = (diff_hl_bfx / pbfx) * 100

            results["comparisons"][coin] = {
                "binance_price": pb,
                "bitfinex_price": pbfx,
                "hyperliquid_price": phl,
                "spread_bitfinex_vs_binance": {
                    "diff_usd": round(diff_bfx_bin, 2),
                    "diff_pct": round(diff_bfx_bin_pct, 4),
                },
                "spread_hyperliquid_vs_binance": {
                    "diff_usd": round(diff_hl_bin, 2),
                    "diff_pct": round(diff_hl_bin_pct, 4),
                },
                "spread_hyperliquid_vs_bitfinex": {
                    "diff_usd": round(diff_hl_bfx, 2),
                    "diff_pct": round(diff_hl_bfx_pct, 4),
                },
            }

    return results


def print_formatted_dashboard(data: Dict[str, Any]) -> None:
    """Prints a beautiful ASCII table dashboard for console review."""
    print("=" * 80)
    print("   BEROUN TRI-VENUE MARKET DISLOCATION & INFRASTRUCTURE DASHBOARD")
    print(f"   Snapshot: {data['timestamp']} | Status: L0 SHADOW READ-ONLY")
    print("=" * 80)

    print("\n[1] VENUE INFRASTRUCTURE & LATENCY AUDIT")
    print("-" * 80)
    print(f"{'Venue':<14} | {'Architecture':<22} | {'Latency':<10} | {'Maker Fee':<10} | {'Taker Fee'}")
    print("-" * 80)
    for key, v in data["venues"].items():
        maker_str = f"{v['maker_fee_pct']:+.2f}%" if v["maker_fee_pct"] <= 0 else f"{v['maker_fee_pct']:.2f}%"
        taker_str = f"{v['taker_fee_pct']:.2f}%"
        print(f"{v['name']:<14} | {v['type']:<22} | {v['latency_ms']:>6.1f} ms  | {maker_str:>8}   | {taker_str:>8}")

    print("\n[2] ASSET PRICING & CROSS-VENUE DISLOCATIONS")
    print("-" * 80)
    for coin, c in data["comparisons"].items():
        print(f"\n>> ASSET: {coin}")
        print(f"   - Binance (Benchmark) : ${c['binance_price']:>11.2f}")
        print(f"   - Bitfinex (0% Maker) : ${c['bitfinex_price']:>11.2f} "
              f" [Spread: {c['spread_bitfinex_vs_binance']['diff_usd']:+7.2f} USD | {c['spread_bitfinex_vs_binance']['diff_pct']:+6.3f}%]")
        print(f"   - Hyperliquid (DEX)   : ${c['hyperliquid_price']:>11.2f} "
              f" [Spread: {c['spread_hyperliquid_vs_binance']['diff_usd']:+7.2f} USD | {c['spread_hyperliquid_vs_binance']['diff_pct']:+6.3f}%]")

    print("\n[3] HYPERLIQUID PERPETUAL FUNDING YIELDS (CASH & CARRY OPPORTUNITY)")
    print("-" * 80)
    print(f"{'Asset':<8} | {'1h Funding Rate':<18} | {'Annualized APR %':<18} | {'Open Interest'}")
    print("-" * 80)
    for coin, f in data["funding_rates"].items():
        print(f"{coin:<8} | {f['hourly_rate']:>15.7f}  | {f['annualized_pct']:>15.2f}% | {str(f['open_interest']):>15}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    snapshot = collect_tri_venue_snapshot()
    if "--json" in sys.argv:
        print(json.dumps(snapshot, indent=2))
    else:
        print_formatted_dashboard(snapshot)
