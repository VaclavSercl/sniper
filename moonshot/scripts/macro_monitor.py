#!/usr/bin/env python3
"""🌙 Moonshot Macro Monitor — Cross-venue BTC Reference
Sniper Armada · Bot #2

Monitors BTC mid-price from Binance as reference benchmark.
Writes BTC reference data that l1_shield.py and orchestrator use.
"""

import time
import requests

BINANCE_BTC_URL = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"


def main():
    print("📡 Moonshot Macro Monitor — BTC Reference Feed")

    while True:
        try:
            resp = requests.get(BINANCE_BTC_URL, timeout=10)
            data = resp.json()
            price = float(data.get("lastPrice", 0))
            change_pct = float(data.get("priceChangePercent", 0))
            volume = float(data.get("quoteVolume", 0))

            # Log every 60 seconds
            print(f"  BTC: ${price:,.0f} | 24h: {change_pct:+.2f}% | Vol: ${volume/1e6:.0f}M")
        except Exception:
            pass

        time.sleep(60)


if __name__ == "__main__":
    main()
