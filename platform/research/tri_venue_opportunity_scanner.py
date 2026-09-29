#!/usr/bin/env python3
"""
BEROUN Tri-Venue Opportunity & Cross-Market Dislocation Engine
Analyzes live market data ingested from Binance, Bitfinex, and Hyperliquid.
Detects mathematically verifiable edges:
  1. Cash & Carry Basis Arbitrage (Bitfinex Spot 0% fee + Hyperliquid Perp funding & rebate)
  2. Cross-Currency Triangular Dislocation (Direct BTC/EUR vs Synthetic BTC/USD via EUR/USD)
  3. Stablecoin Peg Spread & Mean Reversion (USDC vs USDT vs USD fiat parities)

Zero external dependencies. Strictly read-only analysis of PostgreSQL data.
"""

import json
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional


def psql_query(sql: str) -> List[Dict[str, str]]:
    cmd = ["psql", "-U", "beroun", "-d", "beroun", "-t", "-A", "-F", "|", "-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and ("Peer authentication failed" in r.stderr or "FATAL" in r.stderr):
        cmd = ["sudo", "-u", "beroun", "psql", "-d", "beroun", "-t", "-A", "-F", "|", "-c", sql]
        r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql error: {r.stderr.strip()}")

    lines = [line.strip() for line in r.stdout.strip().split("\n") if line.strip()]
    return lines


def get_latest_prices() -> Dict[str, float]:
    """Retrieves the freshest tick for each symbol from PostgreSQL market_ticks."""
    sql = """
    SELECT symbol, price
    FROM (
        SELECT symbol, price, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY ts DESC) as rn
        FROM market_ticks
        WHERE ts > now() - interval '30 minutes'
    ) sub
    WHERE rn = 1;
    """
    lines = psql_query(sql)
    prices = {}
    for line in lines:
        parts = line.split("|")
        if len(parts) >= 2:
            try:
                prices[parts[0]] = float(parts[1])
            except ValueError:
                pass
    return prices


def get_latest_funding_rates() -> Dict[str, Dict[str, float]]:
    """Retrieves newest funding rates from market_funding."""
    sql = """
    SELECT symbol, rate, mark_price
    FROM (
        SELECT symbol, rate, mark_price, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY funding_time DESC) as rn
        FROM market_funding
        WHERE funding_time > now() - interval '24 hours'
    ) sub
    WHERE rn = 1;
    """
    lines = psql_query(sql)
    rates = {}
    for line in lines:
        parts = line.split("|")
        if len(parts) >= 2:
            try:
                coin = parts[0].replace("-PERP", "")
                hourly_rate = float(parts[1])
                mark_px = float(parts[2]) if len(parts) > 2 and parts[2] != "" else 0.0
                rates[coin] = {
                    "hourly_rate": hourly_rate,
                    "annual_apr_pct": round(hourly_rate * 24 * 365 * 100, 2),
                    "mark_price": mark_px
                }
            except ValueError:
                pass
    return rates


def scan_opportunities() -> Dict[str, Any]:
    prices = get_latest_prices()
    funding = get_latest_funding_rates()

    results: Dict[str, Any] = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "basis_arbitrage": {},
        "triangular_fx": {},
        "stablecoin_parities": {}
    }

    # 1. Delta-Neutral Basis Arbitrage (Bitfinex Spot 0% Maker vs Hyperliquid Perp)
    btc_spot_bfx = prices.get("tBTCUSD") or prices.get("tBTCUST")
    btc_perp_hl = prices.get("BTC-PERP")
    btc_fund = funding.get("BTC", {})

    if btc_spot_bfx and btc_perp_hl:
        basis_spread_usd = btc_perp_hl - btc_spot_bfx
        basis_spread_pct = (basis_spread_usd / btc_spot_bfx) * 100
        annual_funding_apr = btc_fund.get("annual_apr_pct", 10.95)
        # Combined net APY: funding yield + basis spread capture (annualized assuming 30d convergence)
        total_carry_apr = annual_funding_apr + (basis_spread_pct * 12)

        results["basis_arbitrage"]["BTC"] = {
            "spot_venue": "Bitfinex",
            "spot_price": btc_spot_bfx,
            "perp_venue": "Hyperliquid",
            "perp_price": btc_perp_hl,
            "basis_spread_usd": round(basis_spread_usd, 2),
            "basis_spread_pct": round(basis_spread_pct, 4),
            "funding_annual_apr_pct": annual_funding_apr,
            "net_carry_apr_pct": round(total_carry_apr, 2),
            "maker_cost": "0.00% (Bitfinex) + -0.02% rebate (Hyperliquid) = NET REBATE (+0.02%)",
            "verdict": "ATTRACTIVE_POSITIVE_CARRY" if total_carry_apr > 5.0 else "NEUTRAL"
        }

    # 2. Triangular FX Dislocation (Direct BTC/EUR vs Synthetic via EUR/USD)
    # Synthetic BTC/EUR = BTC_USD / EUR_USD
    btc_usd = prices.get("tBTCUSD") or prices.get("BTCUSDT")
    eur_usd = prices.get("tEURUSD")
    direct_btc_eur_bin = prices.get("BTCEUR")
    direct_btc_eur_bfx = prices.get("tBTCEUR")

    if btc_usd and eur_usd and direct_btc_eur_bfx:
        synthetic_btc_eur = btc_usd / eur_usd
        diff_eur = synthetic_btc_eur - direct_btc_eur_bfx
        diff_pct = (diff_eur / direct_btc_eur_bfx) * 100

        results["triangular_fx"]["BTC_EUR_USD"] = {
            "btc_usd": btc_usd,
            "eur_usd": eur_usd,
            "synthetic_btc_eur": round(synthetic_btc_eur, 2),
            "direct_btc_eur_bitfinex": direct_btc_eur_bfx,
            "direct_btc_eur_binance": direct_btc_eur_bin,
            "dislocation_eur": round(diff_eur, 2),
            "dislocation_pct": round(diff_pct, 4),
            "arbitrage_edge_bps": round(diff_pct * 100, 2),
            "opportunity": "BUY_DIRECT_SELL_SYNTHETIC" if diff_eur > 0 else "BUY_SYNTHETIC_SELL_DIRECT"
        }

    # 3. Stablecoin Parities & Spreads
    ust_usd = prices.get("tUSTUSD")  # USDT on Bitfinex
    udc_usd = prices.get("tUDCUSD")  # USDC on Bitfinex
    usdc_usdt = prices.get("USDCUSDT")  # Binance
    fdusd_usdt = prices.get("FDUSDUSDT")  # Binance

    results["stablecoin_parities"] = {
        "usdt_usd_bitfinex": {
            "price": ust_usd,
            "deviation_bps": round((ust_usd - 1.0) * 10000, 2) if ust_usd else None
        },
        "usdc_usd_bitfinex": {
            "price": udc_usd,
            "deviation_bps": round((udc_usd - 1.0) * 10000, 2) if udc_usd else None
        },
        "usdc_usdt_binance": {
            "price": usdc_usdt,
            "premium_bps": round((usdc_usdt - 1.0) * 10000, 2) if usdc_usdt else None
        },
        "fdusd_usdt_binance": {
            "price": fdusd_usdt,
            "discount_bps": round((fdusd_usdt - 1.0) * 10000, 2) if fdusd_usdt else None
        }
    }

    return results


def print_dashboard(data: Dict[str, Any]) -> None:
    print("=" * 80)
    print("   BEROUN TRI-VENUE OPPORTUNITY & ARBITRAGE SCANNER")
    print(f"   Evaluated at: {data['timestamp_utc']} | Source: PostgreSQL Authoritative State")
    print("=" * 80)

    # 1. Basis Arbitrage
    print("\n[1] DELTA-NEUTRAL BASIS & FUNDING CARRY (Bitfinex Spot 0% + Hyperliquid Perp)")
    print("-" * 80)
    for asset, b in data.get("basis_arbitrage", {}).items():
        print(f"Asset: {asset}")
        print(f"  - Spot ({b['spot_venue']})  : ${b['spot_price']:,.2f}")
        print(f"  - Perp ({b['perp_venue']}): ${b['perp_price']:,.2f} [Spread: {b['basis_spread_usd']:+,.2f} USD | {b['basis_spread_pct']:+,.3f}%]")
        print(f"  - Funding APR          : {b['funding_annual_apr_pct']:.2f}% p.a. (Hyperliquid hourly yield)")
        print(f"  - Net Estimated Carry  : {b['net_carry_apr_pct']:.2f}% p.a. (Zero maker cost + rebate)")
        print(f"  - Strategy Rating      : {b['verdict']}")

    # 2. Triangular FX
    print("\n[2] TRIANGULAR FOREX & CRYPTO CONVERSION DISLOCATION")
    print("-" * 80)
    t = data.get("triangular_fx", {}).get("BTC_EUR_USD", {})
    if t:
        print(f"BTC/USD: ${t['btc_usd']:,.2f} | EUR/USD: {t['eur_usd']:.4f}")
        print(f"  - Synthetic BTC/EUR    : €{t['synthetic_btc_eur']:,.2f} (via USD conversion)")
        print(f"  - Direct BTC/EUR (BFX) : €{t['direct_btc_eur_bitfinex']:,.2f}")
        print(f"  - Direct BTC/EUR (BIN) : €{t['direct_btc_eur_binance']:,.2f}")
        print(f"  - Cross Dislocation    : {t['dislocation_eur']:+,.2f} EUR ({t['dislocation_pct']:+,.3f}% | {t['arbitrage_edge_bps']:+,.1f} bps)")
        print(f"  - Edge Action          : {t['opportunity']}")

    # 3. Stablecoins
    print("\n[3] STABLECOIN PEG STATUS & DEVIATIONS")
    print("-" * 80)
    sp = data.get("stablecoin_parities", {})
    for k, v in sp.items():
        p = v.get("price")
        dev = v.get("deviation_bps") if "deviation_bps" in v else v.get("premium_bps", v.get("discount_bps"))
        unit = "bps"
        print(f"  - {k:<22}: {p:<8} [Offset: {dev:+.1f} {unit}]")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    opps = scan_opportunities()
    if "--json" in sys.argv:
        print(json.dumps(opps, indent=2))
    else:
        print_dashboard(opps)
