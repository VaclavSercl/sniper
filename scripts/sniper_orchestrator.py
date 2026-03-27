#!/usr/bin/env python3
"""
🧠 BEROUN AI ORCHESTRATOR v10.1.1 — Sovereign Oracle with Memory
═══════════════════════════════════════════════════════════════════
The Sovereign Oracle: Connects L1 (tactical) with L2 (strategic).
Runs every 5 minutes, analyzes both layers, decides and acts.

v10.1.1 UPGRADES:
  - OracleMemory: Cross-cycle persistent memory (12 cycles = 1 hour)
  - Tactical Alert Engine: 5 automated rules (inventory, spread, latency, FP, streak)
  - Auto-Escalation: Performance-based aggressivity tuning
  - Enhanced Gemini Prompt: Historical context from memory

Architecture:
  L1_STATE → reads /dev/shm/beroun/l1_state.json (from L1 Shield)
  ENGINE   → reads /dev/shm/beroun/engine_state.bin (from L0 Rust)
  GEMINI   → consults Gemini 3.1 Pro for macro-strategic decisions
  MEMORY   → persists to logs/oracle_memory.json (12-cycle rolling window)
  OUTPUT   → writes parameters via beroun-config + Telegram reports
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
from pathlib import Path

# ── CONFIG ──────────────────────────────────────────────────
CET = timezone(timedelta(hours=1))
LOG_DIR = "/home/wwwenda/hft-sniper/logs"
CONFIG_BIN = "/home/wwwenda/hft-sniper/target/release/beroun-config"
BRAIN_BIN = "/home/wwwenda/hft-sniper/target/release/beroun-brain"
L1_STATE_JSON = "/dev/shm/beroun/l1_state.json"
ENGINE_MMAP = "/dev/shm/beroun/engine_state.bin"
ALERTS_LOG = f"{LOG_DIR}/alerts.log"
MEMORY_FILE = f"{LOG_DIR}/oracle_memory.json"
ORACLE_CYCLE_SEC = 300  # 5 minutes
PRICE_SCALE = 1e8
MAX_MEMORY_CYCLES = 12  # 1 hour of history

# Mmap offsets for v10.1 AI fields
OFF_BEST_BID = 0
OFF_BEST_ASK = 8
OFF_NET_POS = 32
OFF_REALIZED_PNL = 40
OFF_T2T_MICROS = 72
OFF_L1_CONFIDENCE = 1600
OFF_L2_REGIME = 1608
OFF_SHADOW_MODE = 1616
OFF_SHADOW_PNL = 1624
OFF_L1_FP_RATE = 1632
OFF_L1_SUCCESS_RATE = 1640
OFF_L2_ACTION_MS = 1648
OFF_LEARNING_TRIG = 1656

# Legacy v8.0 fields (must keep alive for beroun-config ai_alive check)
OFF_AI_HEARTBEAT = 1480  # ai_heartbeat_ms in EngineState (verified from repr(C) layout)

# Auto-escalation limits
MAX_POSITION_FLOOR = 0.001
MAX_POSITION_CEIL = 0.010
ESCALATION_UP_PCT = 0.10    # +10%
ESCALATION_DOWN_PCT = 0.20  # -20%

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


# ═══════════════════════════════════════════════════════════
# ORACLE MEMORY — Cross-Cycle Persistent State
# ═══════════════════════════════════════════════════════════
class OracleMemory:
    """Maintains rolling window of Oracle cycle snapshots for trend analysis."""

    def __init__(self, filepath=MEMORY_FILE, max_cycles=MAX_MEMORY_CYCLES):
        self.filepath = filepath
        self.max_cycles = max_cycles
        self.snapshots = []
        self.load()

    def load(self):
        """Load memory from disk."""
        try:
            if os.path.exists(self.filepath):
                with open(self.filepath, 'r') as f:
                    data = json.load(f)
                    self.snapshots = data.get("snapshots", [])[-self.max_cycles:]
                log.info(f"  Memory loaded: {len(self.snapshots)} cycles")
        except Exception as e:
            log.warning(f"Memory load failed: {e}")
            self.snapshots = []

    def save(self):
        """Persist memory to disk."""
        try:
            Path(os.path.dirname(self.filepath)).mkdir(parents=True, exist_ok=True)
            with open(self.filepath, 'w') as f:
                json.dump({"snapshots": self.snapshots[-self.max_cycles:]}, f, indent=2)
        except Exception as e:
            log.error(f"Memory save failed: {e}")

    def record(self, snapshot: dict):
        """Add a cycle snapshot to memory."""
        snapshot["timestamp"] = datetime.now(CET).isoformat()
        self.snapshots.append(snapshot)
        if len(self.snapshots) > self.max_cycles:
            self.snapshots = self.snapshots[-self.max_cycles:]
        self.save()

    def get_trend(self, field: str, window: int = 3) -> str:
        """Analyze trend of a numeric field over last N cycles."""
        if len(self.snapshots) < window:
            return "insufficient_data"
        values = [s.get(field, 0) for s in self.snapshots[-window:]]
        if all(values[i] <= values[i+1] for i in range(len(values)-1)):
            return "rising"
        if all(values[i] >= values[i+1] for i in range(len(values)-1)):
            return "falling"
        return "stable"

    def count_consecutive(self, field: str, condition) -> int:
        """Count how many consecutive recent cycles satisfy a condition."""
        count = 0
        for s in reversed(self.snapshots):
            if condition(s.get(field, 0)):
                count += 1
            else:
                break
        return count

    def get_history_summary(self, n: int = 6) -> str:
        """Generate human-readable history summary for Gemini prompt."""
        if not self.snapshots:
            return "No history available (first cycle)."
        lines = []
        recent = self.snapshots[-n:]
        for i, s in enumerate(reversed(recent)):
            age = (i + 1) * 5  # minutes ago
            lines.append(
                f"  -{age}min: Regime={s.get('regime','?')} "
                f"Grid=${s.get('grid_step',0):.1f} "
                f"PnL=${s.get('pnl',0):.4f} "
                f"Pos={s.get('position',0):.5f}BTC "
                f"Conf={s.get('confidence',0):.0%} "
                f"Toxic={s.get('toxic',0)}"
            )

        # Add trend analysis
        pnl_trend = self.get_trend("pnl")
        pos_trend = self.get_trend("position")
        conf_trend = self.get_trend("confidence")

        lines.append(f"  TRENDS: PnL={pnl_trend} Position={pos_trend} Confidence={conf_trend}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# TACTICAL ALERT ENGINE — 5 Automated Rules
# ═══════════════════════════════════════════════════════════
class TacticalAlertEngine:
    """Generates tactical alerts based on threshold rules + memory trends."""

    def __init__(self, memory: OracleMemory):
        self.memory = memory
        self.alerts_fired = set()  # prevent spamming same alert

    def check_all(self, state: dict) -> list:
        """Run all tactical checks and return list of alert messages."""
        alerts = []

        # Rule 1: Inventory Drift (position growing for 3+ cycles)
        pos_drift = self.memory.count_consecutive("position",
            lambda p: abs(p) > 0.004)
        if pos_drift >= 3 and "inv_drift" not in self.alerts_fired:
            pos = state.get("position", 0)
            direction = "SHORT" if pos < 0 else "LONG"
            alerts.append({
                "level": "🔴",
                "title": "INVENTORY DRIFT",
                "text": (f"⚠️ *Inventory Drift Alert*\n\n"
                         f"Pozice `{pos:.5f} BTC` ({direction}) "
                         f"překračuje 0.004 BTC již *{pos_drift} cyklů* ({pos_drift*5} min).\n"
                         f"L2 Oracle doporučuje zvážit manuální rebalancing."),
            })
            self.alerts_fired.add("inv_drift")
        elif pos_drift < 2:
            self.alerts_fired.discard("inv_drift")

        # Rule 2: Spread Explosion (spread > grid * 1.5)
        spread = state.get("spread", 0)
        grid = state.get("grid_step", 10)
        if spread > grid * 1.5 and spread > 0 and "spread_exp" not in self.alerts_fired:
            alerts.append({
                "level": "🟡",
                "title": "SPREAD EXPLOSION",
                "text": (f"⚡ *Spread Alert*\n\n"
                         f"Spread `${spread:.1f}` překračuje grid `${grid:.1f}` × 1.5.\n"
                         f"Riziko sweepů se zvyšuje."),
            })
            self.alerts_fired.add("spread_exp")
        elif spread <= grid * 1.2:
            self.alerts_fired.discard("spread_exp")

        # Rule 3: Latency Spike (T2T > 200µs)
        t2t = state.get("t2t_micros", 0)
        if t2t > 200 and "lat_spike" not in self.alerts_fired:
            alerts.append({
                "level": "🔴",
                "title": "LATENCY SPIKE",
                "text": (f"🐌 *Latency Alert*\n\n"
                         f"T2T latence `{t2t} µs` překračuje bezpečný limit 200µs.\n"
                         f"Prověř systémové prostředky a dashboard SSE."),
            })
            self.alerts_fired.add("lat_spike")
        elif t2t < 100:
            self.alerts_fired.discard("lat_spike")

        # Rule 4: L1 False Positive Drift (FP > 30% for 2+ cycles)
        fp_drift = self.memory.count_consecutive("fp_rate",
            lambda fp: fp > 0.30)
        if fp_drift >= 2 and "fp_drift" not in self.alerts_fired:
            fp = state.get("fp_rate", 0)
            alerts.append({
                "level": "🟡",
                "title": "L1 FALSE POSITIVE DRIFT",
                "text": (f"🧠 *L1 Shield Alert*\n\n"
                         f"False Positive Rate `{fp:.0%}` je vysoká "
                         f"již *{fp_drift} cyklů*.\n"
                         f"Sweep threshold je pravděpodobně příliš nízký."),
            })
            self.alerts_fired.add("fp_drift")
        elif fp_drift < 1:
            self.alerts_fired.discard("fp_drift")

        # Rule 5: Performance Streak (success 100% for 6+ cycles = 30min)
        perf_streak = self.memory.count_consecutive("success_rate",
            lambda sr: sr >= 0.99)
        if perf_streak >= 6 and "perf_streak" not in self.alerts_fired:
            alerts.append({
                "level": "🟢",
                "title": "PERFORMANCE STREAK",
                "text": (f"🏆 *Výkonnostní Streak*\n\n"
                         f"L1 Success Rate = `100%` po dobu `{perf_streak * 5} minut`.\n"
                         f"Systém je v perfektní kondici.\n"
                         f"Auto-escalation zvažuje zvýšení agresivity."),
            })
            self.alerts_fired.add("perf_streak")
        elif perf_streak < 4:
            self.alerts_fired.discard("perf_streak")

        return alerts


# ═══════════════════════════════════════════════════════════
# AUTO-ESCALATION — Performance-Based Aggressivity Tuning
# ═══════════════════════════════════════════════════════════
class AutoEscalation:
    """Automatically adjusts max_position based on sustained performance."""

    def __init__(self, memory: OracleMemory):
        self.memory = memory

    def evaluate(self, current_max_pos: float, state: dict) -> dict:
        """Evaluate whether to escalate or de-escalate aggressivity.
        Returns dict with action, new_max_pos, and reason."""

        result = {"action": "hold", "new_max_pos": current_max_pos, "reason": ""}

        # DE-ESCALATE: FP rate > 40% OR PnL falling 3+ cycles
        fp_high = self.memory.count_consecutive("fp_rate", lambda fp: fp > 0.40) >= 2
        pnl_falling = self.memory.get_trend("pnl", 3) == "falling"

        if fp_high:
            new_pos = max(MAX_POSITION_FLOOR,
                          current_max_pos * (1 - ESCALATION_DOWN_PCT))
            result = {
                "action": "de-escalate",
                "new_max_pos": round(new_pos, 6),
                "reason": f"FP rate > 40% for 2+ cycles → max_pos {current_max_pos:.4f} → {new_pos:.4f} (-20%)"
            }
            return result

        if pnl_falling and len(self.memory.snapshots) >= 3:
            new_pos = max(MAX_POSITION_FLOOR,
                          current_max_pos * (1 - ESCALATION_DOWN_PCT / 2))
            result = {
                "action": "de-escalate",
                "new_max_pos": round(new_pos, 6),
                "reason": f"PnL declining 3+ cycles → max_pos {current_max_pos:.4f} → {new_pos:.4f} (-10%)"
            }
            return result

        # ESCALATE: Success = 100% for 6+ cycles AND PnL positive or stable
        success_streak = self.memory.count_consecutive("success_rate", lambda sr: sr >= 0.99)
        pnl_ok = self.memory.get_trend("pnl", 3) != "falling"
        current_pnl = state.get("pnl", 0)

        if success_streak >= 6 and pnl_ok and current_pnl >= -1.0:
            new_pos = min(MAX_POSITION_CEIL,
                          current_max_pos * (1 + ESCALATION_UP_PCT))
            if new_pos > current_max_pos * 1.01:  # Only if meaningful change
                result = {
                    "action": "escalate",
                    "new_max_pos": round(new_pos, 6),
                    "reason": (f"Success 100% for {success_streak * 5}min + PnL stable "
                               f"→ max_pos {current_max_pos:.4f} → {new_pos:.4f} (+10%)")
                }

        return result


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


def collect_mmap_state(mm):
    """Read critical values directly from mmap for tactical alerts."""
    try:
        bb = read_u64_mmap(mm, OFF_BEST_BID) / PRICE_SCALE
        ba = read_u64_mmap(mm, OFF_BEST_ASK) / PRICE_SCALE
        return {
            "spread": ba - bb,
            "position": read_i64_mmap(mm, OFF_NET_POS) / PRICE_SCALE,
            "pnl": read_i64_mmap(mm, OFF_REALIZED_PNL) / PRICE_SCALE,
            "t2t_micros": read_u64_mmap(mm, OFF_T2T_MICROS),
            "confidence": read_u64_mmap(mm, OFF_L1_CONFIDENCE) / 10000.0,
            "fp_rate": read_u64_mmap(mm, OFF_L1_FP_RATE) / 10000.0,
            "success_rate": read_u64_mmap(mm, OFF_L1_SUCCESS_RATE) / 10000.0,
        }
    except Exception as e:
        log.error(f"Mmap read failed: {e}")
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

    if toxic > fills * 0.3 and fills > 10:
        return 3, "CHAOS"
    elif abs(obi) > 0.6:
        return 1, "TRENDING"
    else:
        return 2, "RANGING"


def generate_l2_prompt(bot_state, l1_state, market_intel, memory: OracleMemory, brain_context: str = ""):
    """Generate the Sovereign Oracle prompt with historical memory context."""
    analytics = bot_state.get("analytics", {})
    risk = bot_state.get("risk_params", {})
    price = bot_state.get("price", {})
    pnl = bot_state.get("pnl", {})
    equity = bot_state.get("equity", {})

    # Memory context: prefer Sniper Brain (permanent), fallback to rolling memory
    history_summary = brain_context if brain_context else memory.get_history_summary(6)

    prompt = f"""You are SNIPER, the L2 Sovereign Oracle for Beroun Sniper v10.2 HFT bot.
Your role: Strategic commander of a high-frequency BTC/USD market maker.
You have PERMANENT MEMORY — you remember every decision you've ever made and their outcomes.

═══ SYSTEM ARCHITECTURE ═══
L0 = Rust HFT Engine (µs execution, Hydra Grid, atomic mmap IPC)
L1 = Python Tactical Shield (50ms cycle, OBI skewing, sweep detection, ADAPTIVE learning)
L2 = YOU — SNIPER (Strategic Oracle, 5min cycle, macro analysis, parameter control, PERMANENT MEMORY)

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
Max Position: {risk.get('max_position_btc', 0.005):.6f} BTC
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

═══ SNIPER PERMANENT MEMORY ═══
{history_summary}

═══ MARKET INTELLIGENCE ═══
Fear & Greed: {market_intel.get('fear_greed', 'N/A')}
Headlines: {'; '.join(market_intel.get('headlines', ['none'])[:3])}

═══ YOUR MISSION (Chain-of-Analysis) ═══
STEP 1: Review the ORACLE MEMORY trends. Is the situation improving or degrading?
STEP 2: Classify market regime (TRENDING / RANGING / CHAOS)
STEP 3: Evaluate L1 performance (is sweep detection too aggressive or too passive?)
STEP 4: Recommend grid_step and max_position based on regime + toxicity + trends
STEP 5: If net_spread_capture < 0, this is an EMERGENCY — explain why
STEP 6: Provide a SPECIFIC, ACTIONABLE tactical recommendation for the next 30 minutes.
         Examples: "Watch for inventory drift above 0.005 BTC", "Spread is tightening, 
         consider reducing grid to capture more", "High toxicity — stay defensive"

RESPOND WITH EXACTLY THIS JSON (no markdown, no explanation before/after):
{{"regime": "TRENDING|RANGING|CHAOS", "grid_step": 5.0, "max_position": 0.005, "risk_level": "low|medium|high", "l1_advice": "keep|increase|decrease", "reasoning": "brief 1-2 sentence analysis referencing memory trends", "tactical_recommendation": "Specific actionable advice for next 30min", "strategic_insight": "High-level insight for the operator"}}"""

    return prompt


def consult_gemini(prompt):
    """Call Gemini CLI and parse response."""
    try:
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=120
        )
        raw = result.stdout.strip()

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
def apply_decision(decision, mm, escalation_result=None):
    """Apply L2 decision via beroun-config + mmap."""
    if not decision:
        return

    # Apply grid change
    grid = max(2.0, min(50.0, float(decision.get("grid_step", 5.0))))
    subprocess.run([CONFIG_BIN, "set-grid", str(grid)],
                   capture_output=True, timeout=5)

    # Apply max position (use escalation override if available)
    if escalation_result and escalation_result["action"] != "hold":
        pos = escalation_result["new_max_pos"]
        log.info(f"🎚️ Auto-Escalation: {escalation_result['reason']}")
    else:
        pos = max(MAX_POSITION_FLOOR, min(MAX_POSITION_CEIL,
                  float(decision.get("max_position", 0.005))))

    subprocess.run([CONFIG_BIN, "set-max-inv", str(pos)],
                   capture_output=True, timeout=5)

    # Write regime to mmap
    regime_map = {"TRENDING": 1, "RANGING": 2, "CHAOS": 3}
    regime_id = regime_map.get(decision.get("regime", ""), 0)
    write_u64_mmap(mm, OFF_L2_REGIME, regime_id)

    # Update L2 action timestamp
    now_ms = int(time.time() * 1000)
    write_u64_mmap(mm, OFF_L2_ACTION_MS, now_ms)

    # Update legacy ai_heartbeat so beroun-config reports ai_alive=true
    write_u64_mmap(mm, OFF_AI_HEARTBEAT, now_ms)

    # L1 learning trigger
    l1_advice = decision.get("l1_advice", "keep")
    if l1_advice != "keep":
        write_u64_mmap(mm, OFF_LEARNING_TRIG, 1)
        log.info(f"🧠 L2→L1: Triggered learning cycle (advice: {l1_advice})")

    log.info(f"✅ Applied: Grid=${grid:.1f} MaxPos={pos:.4f} "
             f"Regime={decision.get('regime')} L1={l1_advice}")


def generate_apex_report(bot_state, l1_state, decision, market_intel,
                          escalation_result=None, tactical_alerts=None):
    """Generate premium Telegram report with tactical recommendations."""
    analytics = bot_state.get("analytics", {})
    pnl = bot_state.get("pnl", {})
    equity = bot_state.get("equity", {})
    price = bot_state.get("price", {})

    toxic = analytics.get("toxic_flow_hits", 0)
    fills = analytics.get("session_fills", 0)

    health = "🟢" if toxic < 5 and fills > 0 else "🟡" if toxic < 20 else "🔴"

    regime = decision.get("regime", "UNKNOWN") if decision else "UNKNOWN"
    regime_icon = {"TRENDING": "📈", "RANGING": "↔️", "CHAOS": "🌪️"}.get(regime, "❓")

    reasoning = decision.get("reasoning", "No analysis") if decision else "Gemini unavailable"
    insight = decision.get("strategic_insight", "") if decision else ""
    tactical = decision.get("tactical_recommendation", "") if decision else ""

    timestamp = datetime.now(CET).strftime("%H:%M")

    report = f"""{health} *APEX PREDATOR v10.1.1 | STATUS* `{timestamp}`
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
`Success:  {l1_state.get('success_rate', 0.5):.0%}`

📈 *OBCHODY*
`Fills:    {fills}`
`Capture:  ${analytics.get('net_spread_capture_per_btc', 0):.2f}/BTC`

😱 *F&G:* `{market_intel.get('fear_greed', 'N/A')}`"""

    # Add escalation info
    if escalation_result and escalation_result["action"] != "hold":
        esc_icon = "📈" if escalation_result["action"] == "escalate" else "📉"
        report += f"\n\n{esc_icon} *AUTO-ESCALATION*\n_{escalation_result['reason']}_"

    # Add tactical recommendation
    if tactical:
        report += f"\n\n🎯 *TAKTICKÉ DOPORUČENÍ*\n_{tactical}_"

    # Add strategic insight
    if insight:
        report += f"\n\n🧠 _{reasoning}_\n💡 _{insight}_"

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
    log.info("═══ BEROUN SNIPER v10.2 (Sovereign Oracle + Permanent Brain) STARTING ═══")
    log.info(f"  Cycle: {ORACLE_CYCLE_SEC}s | L1 bridge: {L1_STATE_JSON}")
    log.info(f"  Engine: {ENGINE_MMAP}")
    log.info(f"  Brain: {BRAIN_BIN}")
    log.info(f"  Memory: {MEMORY_FILE} (max {MAX_MEMORY_CYCLES} cycles)")

    if not os.path.exists(ENGINE_MMAP):
        log.error(f"Engine mmap not found: {ENGINE_MMAP}")
        sys.exit(1)

    fd = os.open(ENGINE_MMAP, os.O_RDWR)
    mm = mmap.mmap(fd, 0, access=mmap.ACCESS_WRITE)

    # Initialize modules
    memory = OracleMemory()
    alert_engine = TacticalAlertEngine(memory)
    escalation = AutoEscalation(memory)

    # Initialize Sniper Brain (Rust SQLite)
    try:
        subprocess.run([BRAIN_BIN, "init"], capture_output=True, timeout=5)
        log.info("  🧠 Sniper Brain (SQLite) initialized")
    except Exception as e:
        log.warning(f"  Sniper Brain init failed: {e}")

    cycle = 0

    while True:
        try:
            cycle += 1
            log.info(f"═══ ORACLE CYCLE #{cycle} ═══")

            # 1. Collect all data
            bot_state = collect_bot_state()
            l1_state = collect_l1_state()
            mmap_state = collect_mmap_state(mm)
            market_intel = collect_market_intel()

            # 1.5. Load current risk params for escalation
            risk = bot_state.get("risk_params", {})
            current_max_pos = risk.get("max_position_btc", 0.005)
            current_grid = risk.get("grid_step_usd", 5.0)

            # 2. Local regime analysis (fast fallback)
            regime_id, regime_name = analyze_regime(bot_state, l1_state)
            log.info(f"  Local regime: {regime_name} (id={regime_id})")
            log.info(f"  Memory: {len(memory.snapshots)} cycles (rolling) + SQLite (permanent)")

            # 2.5. Get permanent memory context from Sniper Brain
            brain_context = ""
            try:
                result = subprocess.run(
                    [BRAIN_BIN, "context", "--cycles", "6"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    brain_context = result.stdout.strip()
                    log.info(f"  🧠 Brain context loaded ({len(brain_context)} chars)")
            except Exception as e:
                log.warning(f"  Brain context failed: {e}")

            # 3. Consult L2 Oracle (Gemini) with memory context
            prompt = generate_l2_prompt(bot_state, l1_state, market_intel, memory, brain_context)
            decision = consult_gemini(prompt)

            if not decision:
                decision = {
                    "regime": regime_name,
                    "grid_step": current_grid,
                    "max_position": current_max_pos,
                    "risk_level": "medium",
                    "l1_advice": "keep",
                    "reasoning": "Gemini unavailable, using local analysis with memory.",
                    "tactical_recommendation": "System on auto-pilot. Monitor manually.",
                    "strategic_insight": "Fallback mode active.",
                }

            # 4. Apply decision
            # 4.5. Check auto-escalation
            escalation_state = {
                "pnl": mmap_state.get("pnl", 0),
                "fp_rate": mmap_state.get("fp_rate", 0),
                "success_rate": mmap_state.get("success_rate", 0),
            }
            escalation_result = escalation.evaluate(current_max_pos, escalation_state)
            if escalation_result["action"] != "hold":
                log.info(f"🎚️ {escalation_result['action'].upper()}: {escalation_result['reason']}")

            apply_decision(decision, mm, escalation_result)

            # 4.5. Tactical alerts
            alert_state = {
                "position": mmap_state.get("position", 0),
                "spread": mmap_state.get("spread", 0),
                "grid_step": float(decision.get("grid_step", current_grid)),
                "t2t_micros": mmap_state.get("t2t_micros", 0),
                "fp_rate": mmap_state.get("fp_rate", 0),
                "success_rate": mmap_state.get("success_rate", 0),
            }
            tactical_alerts = alert_engine.check_all(alert_state)
            for alert in tactical_alerts:
                log.info(f"  {alert['level']} ALERT: {alert['title']}")
                send_telegram(alert["text"])

            # 5. Generate & send report
            report = generate_apex_report(
                bot_state, l1_state, decision, market_intel,
                escalation_result, tactical_alerts
            )
            send_telegram(report)

            # 5.5. Save memory snapshot (rolling + permanent)
            snapshot = {
                "cycle": cycle,
                "regime": decision.get("regime", "UNKNOWN"),
                "grid_step": float(decision.get("grid_step", current_grid)),
                "max_position": float(decision.get("max_position", current_max_pos)),
                "pnl": mmap_state.get("pnl", 0),
                "position": mmap_state.get("position", 0),
                "confidence": mmap_state.get("confidence", 0),
                "fp_rate": mmap_state.get("fp_rate", 0),
                "success_rate": mmap_state.get("success_rate", 0),
                "toxic": bot_state.get("analytics", {}).get("toxic_flow_hits", 0),
                "spread": mmap_state.get("spread", 0),
                "t2t_micros": mmap_state.get("t2t_micros", 0),
                "reasoning": decision.get("reasoning", ""),
                "tactical": decision.get("tactical_recommendation", ""),
                "escalation": escalation_result.get("action", "hold"),
            }
            memory.record(snapshot)
            log.info(f"  Rolling memory: {len(memory.snapshots)}/{MAX_MEMORY_CYCLES} cycles")

            # 5.6. Save to Sniper Brain (permanent SQLite)
            brain_snapshot = {
                "regime": decision.get("regime", "UNKNOWN"),
                "grid_step": float(decision.get("grid_step", current_grid)),
                "max_position": float(decision.get("max_position", current_max_pos)),
                "spread": mmap_state.get("spread", 0),
                "position": mmap_state.get("position", 0),
                "pnl": mmap_state.get("pnl", 0),
                "equity": bot_state.get("equity", {}).get("total_usd", 0),
                "confidence": mmap_state.get("confidence", 0),
                "fp_rate": mmap_state.get("fp_rate", 0),
                "success_rate": mmap_state.get("success_rate", 0),
                "toxic_hits": bot_state.get("analytics", {}).get("toxic_flow_hits", 0),
                "t2t_micros": int(mmap_state.get("t2t_micros", 0)),
                "fear_greed": market_intel.get("fear_greed", ""),
                "reasoning": decision.get("reasoning", ""),
                "tactical": decision.get("tactical_recommendation", ""),
                "escalation": escalation_result.get("action", "hold"),
            }
            try:
                subprocess.run(
                    [BRAIN_BIN, "log-cycle", json.dumps(brain_snapshot)],
                    capture_output=True, timeout=5
                )
                log.info("  🧠 Brain: cycle logged to SQLite")
            except Exception as e:
                log.warning(f"  Brain log failed: {e}")

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
                "tactical": decision.get("tactical_recommendation", ""),
                "escalation": escalation_result.get("action", "hold"),
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
