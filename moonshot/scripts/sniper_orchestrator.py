#!/usr/bin/env python3
"""🌙 Moonshot L2 Oracle — AI Pair Selection (sniper_orchestrator.py)
Sniper Armada · Bot #2 · Strategic Layer

Replaces sovereign_ai.rs — same logic, Python implementation for consistency.

Every 66 minutes:
1. Fetches ALL Bitfinex tickers via REST API
2. Builds telemetry: top 50 pairs by volume
3. Sends to Gemini CLI with AI_MOONSHOT_PROMPT.md
4. Writes AI-selected pairs + parameters to mmap (moonshot_risk.bin)

Active Trade Lock: AI cannot rotate a pair with open position.
"""

import os
import sys
import json
import time
import struct
import mmap
import subprocess
import requests
from pathlib import Path

# ═══════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════
PRICE_SCALE = 100_000_000.0
PRICE_SCALE_I = 100_000_000
MAX_PAIRS = 20
AI_CYCLE_SECONDS = 66 * 60  # 66 minutes

MOONSHOT_ENGINE_PATH = "/dev/shm/beroun/moonshot_engine.bin"
MOONSHOT_RISK_PATH = "/dev/shm/beroun/moonshot_risk.bin"

SCRIPT_DIR = Path(__file__).parent.resolve()
BOT_DIR = SCRIPT_DIR.parent
PROMPT_PATH = BOT_DIR / "AI_MOONSHOT_PROMPT.md"

BITFINEX_TICKERS_URL = "https://api-pub.bitfinex.com/v2/tickers?symbols=ALL"

# ═══════════════════════════════════════════════════════════
# mmap helpers
# ═══════════════════════════════════════════════════════════

# PairRiskState layout (10 × u64 = 80 bytes, padded to 128 for alignment)
PAIR_RISK_SIZE = 128  # align(64) → next multiple
RISK_HEADER_SIZE = MAX_PAIRS * PAIR_RISK_SIZE

# Offsets within PairRiskState
OFF_SYMBOL_HASH = 0
OFF_M_SHOT_PRICE_PCT = 8
OFF_M_SHOT_PRICE_MIN_PCT = 16
OFF_M_SHOT_REPLACE_DELAY = 24
OFF_M_SHOT_RAISE_WAIT = 32
OFF_TP_PCT = 40
OFF_SL_PCT = 48
OFF_ORDER_USD = 56

# Global risk offsets (after pairs array)
OFF_GLOBAL_PAUSED = RISK_HEADER_SIZE
OFF_DAILY_LOSS_LIMIT = RISK_HEADER_SIZE + 8
OFF_BTC_VOL_KILL = RISK_HEADER_SIZE + 16
OFF_AI_HEARTBEAT = RISK_HEADER_SIZE + 24

# Engine: PairEngineState offsets for reading positions
PAIR_ENGINE_SIZE = 128
OFF_E_NET_POSITION = 32  # 4th field (after bid, ask, last_trade, latency)


def str_to_symbol_hash(s: str) -> int:
    """Convert up to 8-byte ASCII string to u64 LE hash."""
    b = s.encode('ascii')[:8].ljust(8, b'\x00')
    return struct.unpack('<Q', b)[0]


def symbol_hash_to_str(h: int) -> str:
    """Convert u64 LE hash back to ASCII string."""
    b = struct.pack('<Q', h)
    return b.split(b'\x00')[0].decode('ascii', errors='replace')


def open_mmap(path, size):
    """Open or create mmap file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    os.ftruncate(fd, size)
    return mmap.mmap(fd, size)


def read_u64(mm, offset):
    return struct.unpack_from('<Q', mm, offset)[0]


def read_i64(mm, offset):
    return struct.unpack_from('<q', mm, offset)[0]


def write_u64(mm, offset, value):
    struct.pack_into('<Q', mm, offset, int(value))


def write_i64(mm, offset, value):
    struct.pack_into('<q', mm, offset, int(value))


# ═══════════════════════════════════════════════════════════
# Market Telemetry
# ═══════════════════════════════════════════════════════════

def get_market_telemetry(engine_mm, risk_mm):
    """Fetch Bitfinex tickers and build telemetry for AI."""
    # Current positions (for Active Trade Lock)
    current_positions = []
    for i in range(MAX_PAIRS):
        sym_hash = read_u64(risk_mm, i * PAIR_RISK_SIZE + OFF_SYMBOL_HASH)
        if sym_hash != 0:
            pos = read_i64(engine_mm, i * PAIR_ENGINE_SIZE + OFF_E_NET_POSITION)
            pos_f = pos / PRICE_SCALE_I
            current_positions.append({
                "symbol": symbol_hash_to_str(sym_hash),
                "net_position": pos_f,
            })

    # Fetch all tickers
    market_pairs = []
    btc_1h_change = 0.0

    try:
        resp = requests.get(BITFINEX_TICKERS_URL, timeout=30)
        tickers = resp.json()

        for t in tickers:
            if not isinstance(t, list) or len(t) < 11:
                continue
            sym = t[0]
            if not isinstance(sym, str) or not sym.endswith("USD") or ":" in sym:
                continue

            last_price = t[7] or 0
            change_pct = (t[6] or 0) * 100
            vol = t[8] or 0
            high = t[9] or last_price
            low = t[10] or last_price

            if last_price <= 0:
                continue

            # Tick size estimate
            if last_price > 1000:
                tick_size = 1.0
            elif last_price > 10:
                tick_size = 0.01
            elif last_price > 0.1:
                tick_size = 0.0001
            else:
                tick_size = 0.00001

            price_step_pct = (tick_size / last_price) * 100
            volatility = ((high - low) / last_price) * 100 if last_price > 0 else 0
            spread = (t[3] or last_price) - (t[1] or last_price)

            if sym == "tBTCUSD":
                btc_1h_change = change_pct / 24  # Estimate hourly from daily

            vol_usd = vol * last_price
            if vol_usd > 10000:
                market_pairs.append({
                    "symbol": sym,
                    "24h_change_pct": round(change_pct, 2),
                    "24h_vol_usd": round(vol_usd),
                    "price_step_pct": round(price_step_pct, 3),
                    "volatility_24h_pct": round(volatility, 2),
                    "spread_pct": round((spread / last_price) * 100, 2) if last_price > 0 else 0,
                })
    except Exception as e:
        print(f"❌ Telemetry fetch error: {e}")

    # Sort by volume, take top 50
    market_pairs.sort(key=lambda x: x.get("24h_vol_usd", 0), reverse=True)
    market_pairs = market_pairs[:50]

    # TODO: Read actual wallet balance from auth API
    available_balance = 1000.0

    volatility_index = "HIGH" if abs(btc_1h_change) > 2 else "MEDIUM"

    return json.dumps({
        "wallet_usd_balance": available_balance,
        "btc_1h_change_pct": round(btc_1h_change, 2),
        "market_volatility_index": volatility_index,
        "currently_open_positions": current_positions,
        "top_market_pairs": market_pairs,
    })


# ═══════════════════════════════════════════════════════════
# AI Call
# ═══════════════════════════════════════════════════════════

def call_ai_for_portfolio(engine_mm, risk_mm):
    """Call Gemini via CLI with telemetry + prompt."""
    prompt_content = ""
    if PROMPT_PATH.exists():
        prompt_content = PROMPT_PATH.read_text()
    else:
        prompt_content = "Execute Moonshot Strategy."

    telemetry = get_market_telemetry(engine_mm, risk_mm)
    full_prompt = f"{prompt_content}\n\nINPUT TELEMETRY:\n{telemetry}"

    print("🧠 Moonshot L2: Sending data to Gemini...")
    try:
        result = subprocess.run(
            ["/usr/local/bin/gemini", "-p", full_prompt, "--yolo"],
            capture_output=True, text=True, timeout=300,
            cwd=str(BOT_DIR),
            env={**os.environ, "HOME": os.path.expanduser("~")},
        )
        stdout = result.stdout

        # Extract JSON from output
        lines = stdout.split('\n')
        json_lines = []
        in_json = False
        for line in lines:
            if '{' in line and not in_json:
                in_json = True
                json_lines.append(line[line.index('{'):])
            elif in_json:
                json_lines.append(line)
                if line.strip().startswith('}'):
                    break

        json_str = '\n'.join(json_lines)
        return json.loads(json_str)
    except Exception as e:
        print(f"❌ AI call failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# Apply AI Config
# ═══════════════════════════════════════════════════════════

def apply_ai_config(ai_config, engine_mm, risk_mm):
    """Write AI portfolio to mmap risk state."""
    # Global kill switch
    if ai_config.get("global_kill_switch", False):
        write_u64(risk_mm, OFF_GLOBAL_PAUSED, 1)
        print("🚨 GLOBAL KILL SWITCH ACTIVATED")
        return
    else:
        write_u64(risk_mm, OFF_GLOBAL_PAUSED, 0)

    active_pairs = ai_config.get("active_pairs", [])

    # Find protected indices (open positions)
    protected = set()
    for i in range(MAX_PAIRS):
        pos = read_i64(engine_mm, i * PAIR_ENGINE_SIZE + OFF_E_NET_POSITION)
        if pos != 0:
            protected.add(i)
            print(f"🛡️ Protecting pair at index {i} (open position)")
        else:
            # Clear the slot
            write_u64(risk_mm, i * PAIR_RISK_SIZE + OFF_SYMBOL_HASH, 0)

    # Write new pairs
    next_free = 0
    for p in active_pairs:
        # Find next free slot
        while next_free in protected and next_free < MAX_PAIRS:
            next_free += 1

        if next_free >= MAX_PAIRS:
            print("⚠️ MAX_PAIRS reached, ignoring remaining")
            break

        symbol = p.get("symbol", "")
        if not symbol:
            continue

        sym_hash = str_to_symbol_hash(symbol)

        # Check if symbol already in protected slot
        target_idx = next_free
        is_existing = False
        for pidx in protected:
            if read_u64(risk_mm, pidx * PAIR_RISK_SIZE + OFF_SYMBOL_HASH) == sym_hash:
                target_idx = pidx
                is_existing = True
                break

        base = target_idx * PAIR_RISK_SIZE
        write_u64(risk_mm, base + OFF_SYMBOL_HASH, sym_hash)

        if "order_usd" in p:
            write_u64(risk_mm, base + OFF_ORDER_USD, int(p["order_usd"] * PRICE_SCALE))
        if "m_shot_price_pct" in p:
            write_u64(risk_mm, base + OFF_M_SHOT_PRICE_PCT, int(p["m_shot_price_pct"] * PRICE_SCALE))
        if "m_shot_price_min_pct" in p:
            write_u64(risk_mm, base + OFF_M_SHOT_PRICE_MIN_PCT, int(p["m_shot_price_min_pct"] * PRICE_SCALE))
        if "m_shot_replace_delay_ms" in p:
            write_u64(risk_mm, base + OFF_M_SHOT_REPLACE_DELAY, p["m_shot_replace_delay_ms"])
        if "m_shot_raise_wait_ms" in p:
            write_u64(risk_mm, base + OFF_M_SHOT_RAISE_WAIT, p["m_shot_raise_wait_ms"])
        if "tp_pct" in p:
            write_u64(risk_mm, base + OFF_TP_PCT, int(p["tp_pct"] * PRICE_SCALE))
        if "sl_pct" in p:
            write_u64(risk_mm, base + OFF_SL_PCT, int(p["sl_pct"] * PRICE_SCALE))

        print(f"🎯 Pair {target_idx}: {symbol} | ${p.get('order_usd', 0):.0f} | Drop: {p.get('m_shot_price_pct', 0):.1f}%")

        if not is_existing:
            next_free += 1

    # Update AI heartbeat
    write_u64(risk_mm, OFF_AI_HEARTBEAT, int(time.time() * 1000))
    risk_mm.flush()

    print(f"✅ AI config applied: {len(active_pairs)} pairs")


# ═══════════════════════════════════════════════════════════
# Main Loop
# ═══════════════════════════════════════════════════════════

def main():
    print("═══════════════════════════════════════════")
    print("🌙 MOONSHOT L2 ORACLE — AI Pair Selection")
    print("═══════════════════════════════════════════")

    engine_size = 20 * 128 + 64  # 20 pairs + globals
    risk_size = 20 * 128 + 64

    engine_mm = open_mmap(MOONSHOT_ENGINE_PATH, engine_size)
    risk_mm = open_mmap(MOONSHOT_RISK_PATH, risk_size)

    while True:
        print(f"\n⏳ [{time.strftime('%H:%M:%S')}] Running AI pair selection cycle...")

        ai_config = call_ai_for_portfolio(engine_mm, risk_mm)
        if ai_config:
            print(f"✅ AI reasoning: {ai_config.get('reasoning', 'N/A')}")
            apply_ai_config(ai_config, engine_mm, risk_mm)
        else:
            print("❌ AI failed — activating kill switch for safety")
            write_u64(risk_mm, OFF_GLOBAL_PAUSED, 1)
            risk_mm.flush()

        print(f"💤 Next cycle in {AI_CYCLE_SECONDS // 60} minutes...")
        time.sleep(AI_CYCLE_SECONDS)


if __name__ == "__main__":
    main()
