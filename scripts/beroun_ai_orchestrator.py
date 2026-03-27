#!/usr/bin/env python3
"""
🧠 BEROUN AI ORCHESTRATOR v10.1 — Cross-Layer Intelligence Bridge
═══════════════════════════════════════════════════════════════════
The Sovereign Oracle: Connects L1 (tactical) with L2 (strategic).
Runs every 5 minutes, analyzes both layers, decides and acts.

Architecture:
  L1_STATE → reads /dev/shm/beroun/l1_state.json (from L1 Shield)
  ENGINE   → reads /dev/shm/beroun/engine_state.bin (from L0 Rust)
  GEMINI   → consults Gemini 3.1 Pro for macro-strategic decisions
  OUTPUT   → writes parameters via beroun-config + Telegram reports

Prompt Design: Chain-of-Analysis (CoA) + Systemic Anchoring
═══════════════════════════════════════════════════════════════════
"""

import json
import time
import subprocess
import os
import sys
import mmap
import struct
import logging
import requests
from datetime import datetime, timedelta, timezone

# ── CONFIG ──────────────────────────────────────────────────
CET = timezone(timedelta(hours=1))
LOG_DIR = "/home/wwwenda/hft-sniper/logs"
CONFIG_BIN = "/home/wwwenda/hft-sniper/target/release/beroun-config"
L1_STATE_JSON = "/dev/shm/beroun/l1_state.json"
ENGINE_MMAP = "/dev/shm/beroun/engine_state.bin"
ALERTS_LOG = f"{LOG_DIR}/alerts.log"
ORACLE_CYCLE_SEC = 300  # 5 minutes
PRICE_SCALE = 1e8

# Mmap offsets for v10.1 AI fields
OFF_L1_CONFIDENCE = 1600
OFF_L2_REGIME = 1608
OFF_SHADOW_MODE = 1616
OFF_SHADOW_PNL = 1624
OFF_L2_ACTION_MS = 1648
OFF_LEARNING_TRIG = 1656

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [L2-ORACLE] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f'{LOG_DIR}/oracle.log')
    ]
)
log = logging.getLogger('L2')

# ── TELEGRAM ────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_telegram(text, buttons=None):
    """Send formatted Telegram message with optional inline keyboard."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured, skipping.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    if buttons:
        payload["reply_markup"] = json.dumps({
            "inline_keyboard": [buttons]
        })

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log.error(f"Telegram send failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram error: {e}")


def read_u64_mmap(mm, offset):
    return struct.unpack_from('<Q', mm, offset)[0]

def read_i64_mmap(mm, offset):
    return struct.unpack_from('<q', mm, offset)[0]

def write_u64_mmap(mm, offset, val):
    struct.pack_into('<Q', mm, offset, int(val))


# ── DATA COLLECTION ─────────────────────────────────────────
def collect_bot_state():
    """Collect current bot state from beroun-config."""
    try:
        result = subprocess.run(
            [CONFIG_BIN, "export-json"],
            capture_output=True, text=True, timeout=5
        )
        return json.loads(result.stdout.strip())
    except Exception as e:
        log.error(f"Config export failed: {e}")
        return {}


def collect_l1_state():
    """Read L1 Shield state from shared JSON."""
    try:
        with open(L1_STATE_JSON, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def collect_market_intel():
    """Fetch external market data: Fear & Greed, RSS headlines."""
    intel = {"fear_greed": "unavailable", "headlines": []}

    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        d = r.json()
        fg = d['data'][0]
        intel["fear_greed"] = f"{fg['value']} ({fg['value_classification']})"
    except Exception:
        pass

    try:
        import xml.etree.ElementTree as ET
        r = requests.get("https://www.coindesk.com/arc/outboundfeeds/rss/", timeout=8)
        root = ET.fromstring(r.content)
        for item in root.iter('item'):
            title_el = item.find('title')
            if title_el is not None and title_el.text:
                intel["headlines"].append(title_el.text)
                if len(intel["headlines"]) >= 5:
                    break
    except Exception:
        pass

    return intel


# ── STRATEGIC AI LOGIC ──────────────────────────────────────
def analyze_regime(bot_state, l1_state):
    """Determine market regime from L1 data + analytics."""
    analytics = bot_state.get("analytics", {})
    toxic = analytics.get("toxic_flow_hits", 0)
    fills = analytics.get("session_fills", 0)
    obi = l1_state.get("obi", 0)
    confidence = l1_state.get("confidence", 0.5)

    # Regime classification
    if toxic > fills * 0.3 and fills > 10:
        return 3, "CHAOS"  # High toxicity relative to fills
    elif abs(obi) > 0.6:
        return 1, "TRENDING"  # Strong directional pressure
    else:
        return 2, "RANGING"  # Normal market


def generate_l2_prompt(bot_state, l1_state, market_intel):
    """Generate the Sovereign Oracle prompt for Gemini."""
    analytics = bot_state.get("analytics", {})
    risk = bot_state.get("risk_params", {})
    price = bot_state.get("price", {})
    pnl = bot_state.get("pnl", {})
    equity = bot_state.get("equity", {})

    prompt = f"""You are the L2 Sovereign Oracle for Beroun Sniper v10.1 HFT bot.
Your role: Strategic commander of a high-frequency BTC/USD market maker.

═══ SYSTEM ARCHITECTURE ═══
L0 = Rust HFT Engine (µs execution, Hydra Grid, atomic mmap IPC)
L1 = Python Tactical Shield (50ms cycle, OBI skewing, sweep detection, ADAPTIVE learning)
L2 = YOU (Strategic Oracle, 5min cycle, macro analysis, parameter control)

═══ LIVE ENGINE STATE ═══
BTC Price: ${price.get('micro_price', 0):.2f}
Total Equity: ${equity.get('total_usd', 0):.2f}
Net Position: {bot_state.get('position', {}).get('net_btc', 0):.6f} BTC
Realized PnL: ${pnl.get('realized_usd', 0):.4f}
Total PnL: ${pnl.get('total_usd', 0):.4f}

═══ RISK PARAMETERS (current) ═══
Grid Step: ${risk.get('grid_step_usd', 5.0):.2f}
Grid Levels: {risk.get('grid_levels', 3)}
Authorized Capital: ${risk.get('authorized_capital_usd', 400):.2f}
Daily Loss Limit: -${risk.get('daily_loss_limit_usd', 20):.2f}
Paused: {risk.get('paused', False)}

═══ TRADE ANALYTICS (session) ═══
Fills: {analytics.get('session_fills', 0)}
Buy Volume: {analytics.get('session_buy_volume_btc', 0):.6f} BTC
Sell Volume: {analytics.get('session_sell_volume_btc', 0):.6f} BTC
Net Spread Capture: ${analytics.get('net_spread_capture_per_btc', 0):.2f}/BTC
Toxic Flow Hits: {analytics.get('toxic_flow_hits', 0)}

═══ L1 SHIELD TELEMETRY ═══
OBI (Order Book Imbalance): {l1_state.get('obi', 0):.4f}
L1 Confidence: {l1_state.get('confidence', 0.5):.2f}
Sweep Threshold (adaptive): {l1_state.get('sweep_threshold', 0.70):.3f}
False Positive Rate: {l1_state.get('false_positive_rate', 0):.2%}
Success Rate: {l1_state.get('success_rate', 0.5):.2%}
Total Sweeps Detected: {l1_state.get('total_sweeps', 0)}

═══ MARKET INTELLIGENCE ═══
Fear & Greed: {market_intel.get('fear_greed', 'N/A')}
Headlines: {'; '.join(market_intel.get('headlines', ['none'])[:3])}

═══ YOUR MISSION (Chain-of-Analysis) ═══
STEP 1: Classify market regime (TRENDING / RANGING / CHAOS)
STEP 2: Evaluate L1 performance (is sweep detection too aggressive or too passive?)
STEP 3: Recommend grid_step and max_position based on regime + toxicity
STEP 4: If net_spread_capture < 0, this is an EMERGENCY — explain why and recommend action
STEP 5: Provide a 2-sentence strategic insight for the human operator

RESPOND WITH EXACTLY THIS JSON (no markdown, no explanation before/after):
{{"regime": "TRENDING|RANGING|CHAOS", "grid_step": 5.0, "max_position": 0.005, "risk_level": "low|medium|high", "l1_advice": "keep|increase|decrease", "reasoning": "brief 1-2 sentence explanation", "strategic_insight": "Insight for the operator"}}"""

    return prompt


def consult_gemini(prompt):
    """Call Gemini CLI and parse response."""
    try:
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=120
        )
        raw = result.stdout.strip()

        # Extract JSON from response
        import re
        match = re.search(r'\{[^{}]*"regime"[^{}]*\}', raw)
        if match:
            return json.loads(match.group())
        else:
            log.warning(f"No JSON in Gemini response: {raw[:200]}")
            return None
    except subprocess.TimeoutExpired:
        log.error("Gemini timeout (120s)")
        return None
    except Exception as e:
        log.error(f"Gemini error: {e}")
        return None


# ── EXECUTION ───────────────────────────────────────────────
def apply_decision(decision, mm):
    """Apply L2 decision via beroun-config + mmap."""
    if not decision:
        return

    # Apply grid change
    grid = max(2.0, min(50.0, float(decision.get("grid_step", 5.0))))
    subprocess.run([CONFIG_BIN, "set-grid", str(grid)],
                   capture_output=True, timeout=5)

    # Apply max position
    pos = max(0.001, min(0.05, float(decision.get("max_position", 0.005))))
    subprocess.run([CONFIG_BIN, "set-max-inv", str(pos)],
                   capture_output=True, timeout=5)

    # Write regime to mmap
    regime_map = {"TRENDING": 1, "RANGING": 2, "CHAOS": 3}
    regime_id = regime_map.get(decision.get("regime", ""), 0)
    write_u64_mmap(mm, OFF_L2_REGIME, regime_id)

    # Update L2 action timestamp
    write_u64_mmap(mm, OFF_L2_ACTION_MS, int(time.time() * 1000))

    # If L1 advice is to increase sensitivity, trigger learning
    l1_advice = decision.get("l1_advice", "keep")
    if l1_advice != "keep":
        write_u64_mmap(mm, OFF_LEARNING_TRIG, 1)
        log.info(f"🧠 L2→L1: Triggered learning cycle (advice: {l1_advice})")

    log.info(f"✅ Applied: Grid=${grid:.1f} MaxPos={pos:.4f} "
             f"Regime={decision.get('regime')} L1={l1_advice}")


def generate_apex_report(bot_state, l1_state, decision, market_intel):
    """Generate premium Telegram report."""
    analytics = bot_state.get("analytics", {})
    pnl = bot_state.get("pnl", {})
    equity = bot_state.get("equity", {})
    price = bot_state.get("price", {})
    risk = bot_state.get("risk_params", {})

    toxic = analytics.get("toxic_flow_hits", 0)
    fills = analytics.get("session_fills", 0)

    # Dynamic health icon
    if toxic < 5 and fills > 0:
        health = "🟢"
    elif toxic < 20:
        health = "🟡"
    else:
        health = "🔴"

    regime = decision.get("regime", "UNKNOWN") if decision else "UNKNOWN"
    regime_icon = {"TRENDING": "📈", "RANGING": "↔️", "CHAOS": "🌪️"}.get(regime, "❓")

    reasoning = decision.get("reasoning", "No analysis") if decision else "Gemini unavailable"
    insight = decision.get("strategic_insight", "") if decision else ""

    timestamp = datetime.now(CET).strftime("%H:%M")

    report = f"""{health} *APEX PREDATOR v10.1 | STATUS* `{timestamp}`
━━━━━━━━━━━━━━━━━━━━━

{regime_icon} *Režim:* `{regime}`
💎 *Equity:* `${equity.get('total_usd', 0):.2f}`
💰 *PnL:* `${pnl.get('total_usd', 0):.4f}`
💲 *BTC:* `${price.get('micro_price', 0):.2f}`

📊 *STRATEGIE (L2)*
`Grid:     ${decision.get('grid_step', 0):.1f}` → aplikováno
`MaxPos:   {decision.get('max_position', 0):.4f} BTC`
`Risk:     {decision.get('risk_level', '?')}`

🛡️ *TAKTIKA (L1)*
`Toxic:    {toxic} hits`
`Conf:     {l1_state.get('confidence', 0):.0%}`
`FP Rate:  {l1_state.get('false_positive_rate', 0):.0%}`
`Sweeps:   {l1_state.get('total_sweeps', 0)}`

📈 *OBCHODY*
`Fills:    {fills}`
`Capture:  ${analytics.get('net_spread_capture_per_btc', 0):.2f}/BTC`

😱 *F&G:* `{market_intel.get('fear_greed', 'N/A')}`

🧠 _{reasoning}_
💡 _{insight}_"""

    return report


# ── SHADOW MODE MONITOR ─────────────────────────────────────
def check_shadow_recovery(mm, bot_state):
    """Check if Shadow Mode should recommend going live."""
    shadow = read_u64_mmap(mm, OFF_SHADOW_MODE)
    if shadow != 1:
        return None

    shadow_pnl = read_i64_mmap(mm, OFF_SHADOW_PNL) / PRICE_SCALE
    toxic = bot_state.get("analytics", {}).get("toxic_flow_hits", 0)

    if shadow_pnl > 0.001 and toxic < 3:
        return {
            "status": "RECOVERY_READY",
            "shadow_pnl": shadow_pnl,
            "message": (f"📈 *Shadow Mode Report*\n\n"
                       f"Simulovaný zisk: `{shadow_pnl:.6f}` BTC\n"
                       f"Toxic hits: `{toxic}`\n"
                       f"Trh se uklidnil.\n\n"
                       f"_Chceš obnovit live trading?_"),
        }
    return None


# ── MAIN LOOP ───────────────────────────────────────────────
def main():
    log.info("═══ BEROUN AI ORCHESTRATOR v10.1 (Sovereign Oracle) STARTING ═══")
    log.info(f"  Cycle: {ORACLE_CYCLE_SEC}s | L1 bridge: {L1_STATE_JSON}")
    log.info(f"  Engine: {ENGINE_MMAP}")

    if not os.path.exists(ENGINE_MMAP):
        log.error(f"Engine mmap not found: {ENGINE_MMAP}")
        sys.exit(1)

    fd = os.open(ENGINE_MMAP, os.O_RDWR)
    mm = mmap.mmap(fd, 0, access=mmap.ACCESS_WRITE)

    cycle = 0

    while True:
        try:
            cycle += 1
            log.info(f"═══ ORACLE CYCLE #{cycle} ═══")

            # 1. Collect all data
            bot_state = collect_bot_state()
            l1_state = collect_l1_state()
            market_intel = collect_market_intel()

            # 2. Local regime analysis (fast fallback)
            regime_id, regime_name = analyze_regime(bot_state, l1_state)
            log.info(f"  Local regime: {regime_name} (id={regime_id})")

            # 3. Consult L2 Oracle (Gemini)
            prompt = generate_l2_prompt(bot_state, l1_state, market_intel)
            decision = consult_gemini(prompt)

            if not decision:
                # Fallback: use local analysis
                analytics = bot_state.get("analytics", {})
                current_grid = bot_state.get("risk_params", {}).get("grid_step_usd", 5.0)
                decision = {
                    "regime": regime_name,
                    "grid_step": current_grid,
                    "max_position": 0.005,
                    "risk_level": "medium",
                    "l1_advice": "keep",
                    "reasoning": "Gemini unavailable, using local analysis.",
                    "strategic_insight": "System running on auto-pilot.",
                }

            # 4. Apply decision
            apply_decision(decision, mm)

            # 5. Generate & send report
            report = generate_apex_report(bot_state, l1_state, decision, market_intel)
            send_telegram(report)

            # 6. Log alert for historical analysis
            alert_entry = {
                "timestamp": datetime.now(CET).isoformat(),
                "cycle": cycle,
                "regime": decision.get("regime"),
                "grid": decision.get("grid_step"),
                "risk": decision.get("risk_level"),
                "l1_confidence": l1_state.get("confidence", 0),
                "toxic": bot_state.get("analytics", {}).get("toxic_flow_hits", 0),
                "reasoning": decision.get("reasoning", ""),
            }
            try:
                with open(ALERTS_LOG, 'a') as f:
                    f.write(json.dumps(alert_entry) + "\n")
            except Exception:
                pass

            # 7. Check Shadow Mode recovery
            recovery = check_shadow_recovery(mm, bot_state)
            if recovery:
                send_telegram(
                    recovery["message"],
                    buttons=[
                        {"text": "🚀 GO LIVE", "callback_data": "go_live"},
                        {"text": "🌑 STAY SHADOW", "callback_data": "stay_shadow"},
                    ]
                )

            log.info(f"═══ ORACLE CYCLE #{cycle} COMPLETE ═══")

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"Oracle error: {e}")

        time.sleep(ORACLE_CYCLE_SEC)

    mm.close()
    os.close(fd)
    log.info("═══ AI ORCHESTRATOR STOPPED ═══")


if __name__ == "__main__":
    main()
