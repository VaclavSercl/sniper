#!/usr/bin/env python3
"""
🐺 BEROUN SNIPER v10.9 — Macro Intelligence Monitor
"The Eyes" — External market awareness for Omniscient Predator.

Modules:
  1. Binance Cross-Exchange: WebSocket aggTrade stream for BTC/USDT
     → Detects large sells (>1 BTC) → writes binance_sweep_ts to mmap
  2. Fear & Greed Index: CoinGecko alternative.market data
     → Writes macro_fear_greed (0-100) to mmap every 5 min
  3. News RSS Sentiment: Scans crypto RSS feeds for keywords
     → Writes macro_bias (-1.0 to +1.0) to mmap

All data flows to mmap → L0 Engine reacts in <1ms.
"""

import os
import sys
import json
import time
import struct
import mmap
import signal
import logging
import threading
import re
from datetime import datetime, timezone
from collections import deque

# ── CONFIG ──────────────────────────────────────────────────
ENGINE_MMAP = "/dev/shm/beroun/engine_state.bin"
PRICE_SCALE = 1e8

# Mmap offsets (v10.9)
OFF_AI_REGISTRY_VER = 1816
OFF_MACRO_BIAS = 1824        # i64: -10000..+10000
OFF_MACRO_SOURCE_TS = 1832   # u64: epoch ms
OFF_BINANCE_SWEEP_TS = 1840  # u64: epoch ms
OFF_MACRO_FEAR_GREED = 1848  # u64: 0..100

# Binance thresholds
BINANCE_LARGE_SELL_BTC = 1.0    # Alert if single trade > 1 BTC sell
BINANCE_SWEEP_WINDOW_S = 10    # Rolling window for aggregate detection
BINANCE_SWEEP_VOLUME_BTC = 5.0 # Alert if >5 BTC sold in 10s window

# News sentiment keywords
BULLISH_KEYWORDS = [
    "bull", "rally", "surge", "breakout", "ath", "all-time high",
    "adoption", "approval", "etf approved", "institutional buy",
    "rate cut", "dovish", "bullish", "upgrade", "accumulation",
]
BEARISH_KEYWORDS = [
    "bear", "crash", "dump", "hack", "exploit", "ban",
    "regulation", "sec", "lawsuit", "rate hike", "hawkish",
    "liquidation", "capitulation", "sell-off", "downgrade",
    "bankruptcy", "insolvency", "default",
]

# RSS feeds (crypto news)
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
]

# Fear & Greed API
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
FEAR_GREED_INTERVAL_S = 300  # 5 minutes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MACRO] %(message)s")
log = logging.getLogger("macro-monitor")

running = True

def signal_handler(sig, frame):
    global running
    running = False
    log.info("Shutting down Macro Monitor...")

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ── MMAP HELPERS ────────────────────────────────────────────
def open_mmap():
    """Open engine mmap for read/write."""
    fd = os.open(ENGINE_MMAP, os.O_RDWR)
    mm = mmap.mmap(fd, 0, access=mmap.ACCESS_WRITE)
    return fd, mm

def write_i64(mm, offset, val):
    struct.pack_into('<q', mm, offset, val)

def write_u64(mm, offset, val):
    struct.pack_into('<Q', mm, offset, val)

def read_u64(mm, offset):
    return struct.unpack_from('<Q', mm, offset)[0]

def now_ms():
    return int(time.time() * 1000)


# ── MODULE 1: BINANCE CROSS-EXCHANGE ───────────────────────
def binance_monitor(mm):
    """Monitor Binance BTC/USDT aggTrade stream for large sells."""
    import websocket

    sell_window = deque()  # (timestamp, volume)
    url = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"

    def on_message(ws, message):
        if not running:
            ws.close()
            return
        try:
            d = json.loads(message)
            price = float(d["p"])
            qty = float(d["q"])
            is_buyer = d["m"]  # True = seller is maker = sell aggression

            if is_buyer:  # Sell aggression
                now = time.time()
                sell_window.append((now, qty))

                # Prune old entries
                while sell_window and sell_window[0][0] < now - BINANCE_SWEEP_WINDOW_S:
                    sell_window.popleft()

                # Check single large sell
                if qty >= BINANCE_LARGE_SELL_BTC:
                    write_u64(mm, OFF_BINANCE_SWEEP_TS, now_ms())
                    log.warning(f"🔴 BINANCE LARGE SELL: {qty:.3f} BTC @ ${price:.0f}")

                # Check aggregate window
                window_vol = sum(v for _, v in sell_window)
                if window_vol >= BINANCE_SWEEP_VOLUME_BTC:
                    write_u64(mm, OFF_BINANCE_SWEEP_TS, now_ms())
                    log.warning(f"🔴 BINANCE SWEEP: {window_vol:.1f} BTC sold in {BINANCE_SWEEP_WINDOW_S}s window")
                    sell_window.clear()  # Reset after alert

        except Exception as e:
            log.debug(f"Binance parse error: {e}")

    def on_error(ws, error):
        log.error(f"Binance WS error: {error}")

    def on_close(ws, code, msg):
        log.info(f"Binance WS closed: {code} {msg}")

    def on_open(ws):
        log.info("🟢 Binance BTC/USDT aggTrade stream connected")

    while running:
        try:
            ws = websocket.WebSocketApp(
                url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log.error(f"Binance reconnect in 5s: {e}")
        if running:
            time.sleep(5)


# ── MODULE 2: FEAR & GREED INDEX ───────────────────────────
def fear_greed_monitor(mm):
    """Fetch Fear & Greed Index every 5 minutes."""
    import urllib.request

    while running:
        try:
            req = urllib.request.Request(FEAR_GREED_URL, headers={"User-Agent": "BerounSniper/10.9"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                fng = data.get("data", [{}])[0]
                value = int(fng.get("value", 50))
                classification = fng.get("value_classification", "Neutral")
                write_u64(mm, OFF_MACRO_FEAR_GREED, value)
                write_u64(mm, OFF_MACRO_SOURCE_TS, now_ms())
                log.info(f"📊 Fear & Greed: {value} ({classification})")
        except Exception as e:
            log.warning(f"Fear & Greed fetch failed: {e}")

        # Sleep in 10s chunks for clean shutdown
        for _ in range(FEAR_GREED_INTERVAL_S // 10):
            if not running:
                return
            time.sleep(10)


# ── MODULE 3: NEWS RSS SENTIMENT ───────────────────────────
def score_text(text):
    """Simple keyword sentiment scoring. Returns -1.0 to +1.0."""
    text_lower = text.lower()
    bull_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bear_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
    total = bull_count + bear_count
    if total == 0:
        return 0.0
    return (bull_count - bear_count) / total


def news_sentiment_monitor(mm):
    """Scan RSS feeds for sentiment keywords every 3 minutes."""
    import urllib.request
    import xml.etree.ElementTree as ET

    seen_titles = set()
    NEWS_INTERVAL_S = 180  # 3 minutes

    while running:
        scores = []
        for feed_url in RSS_FEEDS:
            try:
                req = urllib.request.Request(feed_url, headers={"User-Agent": "BerounSniper/10.9"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read()
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    continue

                # RSS 2.0 format
                items = root.findall(".//item")
                for item in items[:10]:  # Last 10 articles per feed
                    title = item.findtext("title", "")
                    desc = item.findtext("description", "")
                    key = title[:80]

                    if key in seen_titles:
                        continue
                    seen_titles.add(key)

                    score = score_text(f"{title} {desc}")
                    if abs(score) > 0.01:
                        scores.append(score)
                        if abs(score) > 0.5:
                            direction = "🟢 BULL" if score > 0 else "🔴 BEAR"
                            log.info(f"📰 {direction} ({score:+.2f}): {title[:80]}")

            except Exception as e:
                log.debug(f"RSS fetch error ({feed_url[:30]}...): {e}")

        # Compute aggregate bias
        if scores:
            avg_score = sum(scores) / len(scores)
            # EMA with existing bias (70% old, 30% new)
            try:
                fd_r = os.open(ENGINE_MMAP, os.O_RDONLY)
                mm_r = mmap.mmap(fd_r, 0, access=mmap.ACCESS_READ)
                old_bias = struct.unpack_from('<q', mm_r, OFF_MACRO_BIAS)[0] / 10000.0
                mm_r.close()
                os.close(fd_r)
            except Exception:
                old_bias = 0.0

            new_bias = old_bias * 0.7 + avg_score * 0.3
            new_bias = max(-1.0, min(1.0, new_bias))

            write_i64(mm, OFF_MACRO_BIAS, int(new_bias * 10000))
            write_u64(mm, OFF_MACRO_SOURCE_TS, now_ms())
            log.info(f"📊 Macro Bias: {new_bias:+.4f} (from {len(scores)} scored articles)")

        # Cap seen_titles at 1000
        if len(seen_titles) > 1000:
            seen_titles = set(list(seen_titles)[-500:])

        for _ in range(NEWS_INTERVAL_S // 10):
            if not running:
                return
            time.sleep(10)


# ── MAIN ────────────────────────────────────────────────────
def main():
    log.info("🐺 SNIPER v10.9 — Macro Intelligence Monitor starting...")

    # Wait for mmap
    for i in range(30):
        if os.path.exists(ENGINE_MMAP):
            break
        log.info(f"Waiting for mmap ({i+1}/30)...")
        time.sleep(1)
    else:
        log.error(f"mmap not found: {ENGINE_MMAP}")
        sys.exit(1)

    fd, mm = open_mmap()
    log.info(f"  mmap: {ENGINE_MMAP}")

    # Check websocket-client is available
    try:
        import websocket
        has_ws = True
        log.info("  Binance monitor: ENABLED (websocket-client found)")
    except ImportError:
        has_ws = False
        log.warning("  Binance monitor: DISABLED (pip install websocket-client)")

    threads = []

    # Module 1: Binance Cross-Exchange
    if has_ws:
        t = threading.Thread(target=binance_monitor, args=(mm,), daemon=True, name="binance")
        t.start()
        threads.append(t)

    # Module 2: Fear & Greed Index
    t = threading.Thread(target=fear_greed_monitor, args=(mm,), daemon=True, name="fear-greed")
    t.start()
    threads.append(t)

    # Module 3: News RSS Sentiment
    t = threading.Thread(target=news_sentiment_monitor, args=(mm,), daemon=True, name="news-rss")
    t.start()
    threads.append(t)

    log.info("═══ MACRO INTELLIGENCE v10.9 ACTIVE ═══")

    # Main loop — status every 60s
    while running:
        try:
            bias = struct.unpack_from('<q', mm, OFF_MACRO_BIAS)[0] / 10000.0
            fng = read_u64(mm, OFF_MACRO_FEAR_GREED)
            last_bnb = read_u64(mm, OFF_BINANCE_SWEEP_TS)
            age_bnb = (now_ms() - last_bnb) / 1000 if last_bnb > 0 else -1

            log.info(f"MACRO status: bias={bias:+.4f} F&G={fng} "
                     f"BNB_sweep={'NONE' if age_bnb < 0 else f'{age_bnb:.0f}s ago'}")
        except Exception as e:
            log.error(f"Status read error: {e}")

        for _ in range(6):  # 60s in 10s chunks
            if not running:
                break
            time.sleep(10)

    mm.close()
    os.close(fd)
    log.info("═══ MACRO INTELLIGENCE v10.9 STOPPED ═══")


if __name__ == "__main__":
    main()
