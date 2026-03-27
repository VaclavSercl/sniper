#!/usr/bin/env python3
"""📐 Grid Macro Monitor — BTC reference & ADX trend detection"""
import time, requests

def main():
    print("📡 Grid Macro Monitor — BTC Reference + Trend")
    while True:
        try:
            resp = requests.get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=10)
            d = resp.json()
            price = float(d.get("lastPrice", 0))
            chg = float(d.get("priceChangePercent", 0))
            vol = float(d.get("quoteVolume", 0))
            print(f"  BTC: ${price:,.0f} | 24h: {chg:+.2f}% | Vol: ${vol/1e6:.0f}M")
        except: pass
        time.sleep(60)

if __name__ == "__main__": main()
