#!/usr/bin/env python3
"""
🏛️ Sniper Armada — Telegram Commander v2.0
Unified C2 for ALL bots: Hydra, Moonshot, Grid, Trigon

Two modes of interaction:
  1. Slash commands:  /hydra start, /grid pause, /status
  2. Natural language: "Ahoj snipere, vypni hydru" → AI parses intent → executes

Security: Only responds to TELEGRAM_CHAT_ID
"""

import os
import sys
import json
import subprocess
import time
import struct
import signal
import threading
import logging
from datetime import datetime, timezone, timedelta

import telebot
from telebot import apihelper

# ── CONFIG ──────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
BIN_DIR = os.path.join(PROJECT_ROOT, "target", "release")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Load .env file manually
def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AUTHORIZED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CMD] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "tg_commander.log")),
    ],
)
log = logging.getLogger("commander")

if not TOKEN:
    log.error("TELEGRAM_BOT_TOKEN not set!")
    sys.exit(1)

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

# ── BOT REGISTRY ────────────────────────────────────────────
BOTS = {
    "hydra": {
        "core": f"{BIN_DIR}/hydra-core",
        "dashboard": f"{BIN_DIR}/hydra-dashboard",
        "config": f"{BIN_DIR}/hydra-config",
        "cpu": 0,
        "port": 3000,
        "emoji": "🐍",
        "desc": "BTC-USD Delta Lead",
    },
    "moonshot": {
        "core": f"{BIN_DIR}/moonshot-core",
        "dashboard": f"{BIN_DIR}/moonshot-dashboard",
        "config": f"{BIN_DIR}/moonshot-config",
        "cpu": 1,
        "port": 3001,
        "emoji": "🌙",
        "desc": "Multi-Symbol Flash Crash",
    },
    "grid": {
        "core": f"{BIN_DIR}/grid-core",
        "dashboard": f"{BIN_DIR}/grid-dashboard",
        "config": f"{BIN_DIR}/grid-config",
        "cpu": 2,
        "port": 3002,
        "emoji": "📐",
        "desc": "Dynamic Multi-Level Grid",
    },
    "trigon": {
        "core": f"{BIN_DIR}/trigon-core",
        "dashboard": f"{BIN_DIR}/trigon-dashboard",
        "config": f"{BIN_DIR}/trigon-config",
        "cpu": 3,
        "port": 3003,
        "emoji": "🔺",
        "desc": "Triangular Arbitrage",
    },
}

# ── SECURITY ────────────────────────────────────────────────
def auth(message):
    if message.chat.id != AUTHORIZED_CHAT_ID:
        bot.reply_to(message, "⛔ Unauthorized.")
        log.warning(f"Unauthorized: chat_id={message.chat.id}")
        return False
    return True

# ── PROCESS MANAGEMENT ──────────────────────────────────────
def is_running(name):
    """Check if bot process is running."""
    try:
        r = subprocess.run(["pgrep", "-f", f"{name}-core"], capture_output=True, text=True)
        return r.returncode == 0 and int(r.stdout.strip().split("\n")[0]) > 0
    except Exception:
        return False

def get_pid(name):
    """Get PID of running bot."""
    try:
        r = subprocess.run(["pgrep", "-f", f"{name}-core"], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip().split("\n")[0]
    except Exception:
        pass
    return None

def start_bot(name):
    """Start a bot (core + dashboard)."""
    info = BOTS.get(name)
    if not info:
        return f"❌ Neznámý bot: {name}"
    if is_running(name):
        return f"{info['emoji']} {name.upper()} už běží (PID {get_pid(name)})"

    # Start core
    core_log = os.path.join(LOG_DIR, f"{name}-core.log")
    subprocess.Popen(
        ["taskset", "-c", str(info["cpu"]), info["core"]],
        stdout=open(core_log, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=PROJECT_ROOT,
    )

    # Start dashboard
    dash_log = os.path.join(LOG_DIR, f"{name}-dashboard.log")
    subprocess.Popen(
        ["taskset", "-c", "3", info["dashboard"]],
        stdout=open(dash_log, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=PROJECT_ROOT,
    )

    time.sleep(2)
    if is_running(name):
        pid = get_pid(name)
        return f"{info['emoji']} {name.upper()} spuštěn ✅ (PID {pid}, Core {info['cpu']}, :{info['port']})"
    else:
        return f"❌ {name.upper()} se nepodařilo spustit. Zkontroluj log: {core_log}"

def stop_bot(name):
    """Stop a bot (core + dashboard)."""
    info = BOTS.get(name)
    if not info:
        return f"❌ Neznámý bot: {name}"
    if not is_running(name):
        return f"{info['emoji']} {name.upper()} neběží."

    pid = get_pid(name)
    subprocess.run(["pkill", "-f", f"{name}-core"], capture_output=True)
    subprocess.run(["pkill", "-f", f"{name}-dashboard"], capture_output=True)
    time.sleep(1)

    if not is_running(name):
        return f"{info['emoji']} {name.upper()} zastaven ✅ (byl PID {pid})"
    else:
        # Force kill
        subprocess.run(["pkill", "-9", "-f", f"{name}-core"], capture_output=True)
        return f"{info['emoji']} {name.upper()} force-killed ✅"

def restart_bot(name):
    """Restart a bot."""
    stop_msg = stop_bot(name)
    time.sleep(1)
    start_msg = start_bot(name)
    return f"{stop_msg}\n{start_msg}"

def pause_bot(name):
    """Pause trading via config CLI."""
    info = BOTS.get(name)
    if not info:
        return f"❌ Neznámý bot: {name}"
    try:
        r = subprocess.run([info["config"], "pause"], capture_output=True, text=True, timeout=5)
        return f"{info['emoji']} {name.upper()} ⏸️ PAUSED\n{r.stdout.strip()}"
    except Exception as e:
        return f"❌ Pause error: {e}"

def unpause_bot(name):
    """Unpause trading via config CLI."""
    info = BOTS.get(name)
    if not info:
        return f"❌ Neznámý bot: {name}"
    try:
        r = subprocess.run([info["config"], "pause", "--off"], capture_output=True, text=True, timeout=5)
        return f"{info['emoji']} {name.upper()} ▶️ UNPAUSED\n{r.stdout.strip()}"
    except Exception as e:
        return f"❌ Unpause error: {e}"

def read_cortex_state():
    """Read live cortex_state.json for bot data."""
    try:
        path = "/dev/shm/beroun/cortex_state.json"
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return None

def build_report(period="hourly"):
    """
    Build unified report matching Cortex L2 format.
    period: "hourly", "daily", "weekly", "monthly"
    """
    now_str = datetime.now(timezone(timedelta(hours=1))).strftime("%H:%M")
    period_labels = {
        "hourly":  ("⏰ HODINOVY REPORT", [1, 24, 168]),
        "daily":   ("📅 DENNI REPORT",    [24, 168, 720]),
        "weekly":  ("📊 TYDENNI REPORT",  [168, 720, 720*3]),
        "monthly": ("📈 MESICNI REPORT",  [720, 720*3, 720*6]),
    }
    title, windows = period_labels.get(period, period_labels["hourly"])
    w_labels = {
        1: "1h", 24: "24h", 168: "7d", 720: "30d", 2160: "90d", 4320: "180d",
    }

    running = sum(1 for name in BOTS if is_running(name))
    health = "🟢" if running > 0 else "🔴"

    lines = [
        f"{health} {title} | {now_str}",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"Online: {running}/{len(BOTS)}",
    ]

    cortex = read_cortex_state()

    try:
        pnl_path = os.path.join(PROJECT_ROOT, "shared")
        if pnl_path not in sys.path:
            sys.path.insert(0, pnl_path)
        from pnl_engine import PnlDatabase, format_pnl_short

        db = PnlDatabase()
        totals = [0.0] * 3
        total_fills = 0
        has_data = False

        for bname in ["hydra", "moonshot", "grid", "trigon"]:
            info = BOTS[bname]
            alive = is_running(bname)
            icon = "🟢" if alive else "🔴"

            lines.append(f"\n{icon} {info['emoji']} {bname.upper()}")

            # Live data from Cortex
            if cortex:
                for bd in cortex.get("bots", []):
                    if bd.get("name") == bname:
                        lines.append(
                            f"💲 ${bd.get('price', 0):.2f} | "
                            f"📦 {bd.get('position', 0):.5f} BTC | "
                            f"💰 ${bd.get('pnl', 0):.4f}"
                        )
                        lines.append(
                            f"📐 Grid ${bd.get('grid', 0):.2f} ({bd.get('grid_levels', 0)}L) | "
                            f"Fills {bd.get('fills', 0)} | Toxic {bd.get('toxic', 0)}"
                        )
                        break

            # PnL from FIFO
            pnl_vals = []
            for w in windows:
                r = db.get_realized_window(bname, w)
                pnl_vals.append(r)

            w0 = pnl_vals[0]
            w1_data = pnl_vals[1]
            w2_data = pnl_vals[2]

            if w0["fills"] > 0 or w1_data["fills"] > 0:
                has_data = True
                buys = w0["fills"] - w0["closed_trades"]
                sells = w0["closed_trades"]
                lines.append(
                    f"📈 Obchodu: {w0['fills']} ({buys} nakup / {sells} prodej)"
                )
                l0 = w_labels.get(windows[0], f"{windows[0]}h")
                l1 = w_labels.get(windows[1], f"{windows[1]}h")
                l2 = w_labels.get(windows[2], f"{windows[2]}h")
                lines.append(
                    f"💰 PnL: {format_pnl_short(w0['realized'])} {l0} | "
                    f"{format_pnl_short(w1_data['realized'])} {l1} | "
                    f"{format_pnl_short(w2_data['realized'])} {l2}"
                )

                for i in range(3):
                    totals[i] += pnl_vals[i]["realized"]
                total_fills += w0["fills"]

        if has_data:
            l0 = w_labels.get(windows[0], f"{windows[0]}h")
            l1 = w_labels.get(windows[1], f"{windows[1]}h")
            l2 = w_labels.get(windows[2], f"{windows[2]}h")
            lines.append("\n━━━━━━━━━━━━━━━━━━━")
            lines.append(
                f"Σ PnL: {format_pnl_short(totals[0])} {l0} | "
                f"{format_pnl_short(totals[1])} {l1} | "
                f"{format_pnl_short(totals[2])} {l2}"
            )
            lines.append(f"Fills: {total_fills}")
        else:
            lines.append("\nℹ️ Zadne fills")

        db.close()
    except Exception as e:
        log.error(f"PnL status error: {e}")
        lines.append(f"\n💰 PnL: {e}")

    return "\n".join(lines)


def get_status():
    """Backward-compatible: returns hourly report."""
    return build_report("hourly")

# ── SLASH COMMANDS ──────────────────────────────────────────

@bot.message_handler(commands=["help", "start"])
def cmd_help(message):
    if not auth(message): return
    bot.reply_to(message, """🐺 *SNIPER ARMADA v13.0*

📊 `/status` — Stav + PnL všech botů

🎮 *Ovládání:*
`/hydra start` · `stop` · `restart` · `pause`
`/moonshot start` · `stop` · `restart`
`/grid start` · `stop` · `restart`
`/trigon start` · `stop` · `restart`

🧠 *AI:*
`/analyze` — Gemini analýza trhu
`/oracle` — L2 strategický cyklus

🚨 `/panic` — Zastavit VŠE

💬 Nebo piš česky: _"Jak se daří?"_""")

@bot.message_handler(commands=["status"])
def cmd_status(message):
    if not auth(message): return
    bot.reply_to(message, get_status())

@bot.message_handler(commands=["panic"])
def cmd_panic(message):
    if not auth(message): return
    log.warning("🚨 PANIC — stopping ALL bots!")
    results = []
    for name in BOTS:
        if is_running(name):
            results.append(stop_bot(name))
    msg = "🚨 *PANIC — Všechny boty zastaveny!*\n\n" + "\n".join(results) if results else "🚨 Žádný bot neběžel."
    bot.reply_to(message, msg)

@bot.message_handler(commands=["pnl"])
def cmd_pnl(message):
    if not auth(message): return
    log.info("PnL report requested")
    try:
        pnl_path = os.path.join(PROJECT_ROOT, "shared")
        sys.path.insert(0, pnl_path)
        from pnl_engine import PnlDatabase, format_pnl_short

        db = PnlDatabase()
        lines = ["💰 *TRADING PnL (FIFO, volatility-immune)*\n"]

        total_1h = total_24h = total_7d = total_30d = 0
        total_fees = 0

        for bname in ["hydra", "moonshot", "grid", "trigon"]:
            w1 = db.get_realized_window(bname, 1)
            w24 = db.get_realized_window(bname, 24)
            w7 = db.get_realized_window(bname, 168)
            w30 = db.get_realized_window(bname, 720)

            if w24["fills"] == 0 and w30["fills"] == 0:
                continue

            emoji = {"hydra": "🐍", "moonshot": "🌙", "grid": "📐", "trigon": "🔺"}.get(bname, "🤖")
            lines.append(f"{emoji} *{bname.upper()}*")
            lines.append(f"  1h:  `{format_pnl_short(w1['realized'])}` ({w1['fills']} fills)")
            lines.append(f"  24h: `{format_pnl_short(w24['realized'])}` ({w24['fills']} fills)")
            lines.append(f"  7d:  `{format_pnl_short(w7['realized'])}`")
            lines.append(f"  30d: `{format_pnl_short(w30['realized'])}`")
            lines.append(f"  Fees 24h: `${w24['fees']:.4f}`")
            lines.append("")

            total_1h += w1["realized"]
            total_24h += w24["realized"]
            total_7d += w7["realized"]
            total_30d += w30["realized"]
            total_fees += w24["fees"]

        if len(lines) <= 1:
            lines.append("ℹ️ Žádné fills v databázi. PnL daemon možná ještě neběží.")
        else:
            lines.append("═══════════════════")
            lines.append(f"*TOTAL 1h:*  `{format_pnl_short(total_1h)}`")
            lines.append(f"*TOTAL 24h:* `{format_pnl_short(total_24h)}`")
            lines.append(f"*TOTAL 7d:*  `{format_pnl_short(total_7d)}`")
            lines.append(f"*TOTAL 30d:* `{format_pnl_short(total_30d)}`")
            lines.append(f"*Fees 24h:*  `${total_fees:.4f}`")

        db.close()
        bot.reply_to(message, "\n".join(lines))
    except ImportError:
        bot.reply_to(message, "❌ PnL engine not found. Run: `python3 architect/pnl_daemon.py`")
    except Exception as e:
        bot.reply_to(message, f"❌ PnL error: {e}")

# ── BOT-SPECIFIC COMMANDS (/hydra start, /grid stop, etc.) ─
@bot.message_handler(commands=list(BOTS.keys()))
def cmd_bot(message):
    if not auth(message): return
    parts = message.text.strip().split()
    bot_name = parts[0][1:].lower()  # Remove /
    action = parts[1].lower() if len(parts) > 1 else "status"

    log.info(f"Command: /{bot_name} {action}")

    actions = {
        "start": lambda: start_bot(bot_name),
        "stop": lambda: stop_bot(bot_name),
        "restart": lambda: restart_bot(bot_name),
        "pause": lambda: pause_bot(bot_name),
        "unpause": lambda: unpause_bot(bot_name),
        "resume": lambda: unpause_bot(bot_name),
        "status": lambda: _bot_status(bot_name),
    }

    handler = actions.get(action)
    if handler:
        result = handler()
        bot.reply_to(message, result)
    else:
        bot.reply_to(message, f"❌ Neznámá akce: `{action}`\nPoužij: `start`, `stop`, `restart`, `pause`, `unpause`, `status`")

def _bot_status(name):
    info = BOTS[name]
    alive = is_running(name)
    pid = get_pid(name) if alive else "—"
    icon = "🟢 LIVE" if alive else "🔴 OFFLINE"
    lines = [f"""{info['emoji']} *{name.upper()}* — {info['desc']}

Status: *{icon}*
PID: `{pid}`
Core: `{info['cpu']}`
Port: `:{info['port']}`"""]

    # ── Live state from cortex_state.json ──
    try:
        cortex_path = "/dev/shm/beroun/cortex_state.json"
        if os.path.exists(cortex_path):
            with open(cortex_path) as f:
                cortex = json.load(f)
            for bot_data in cortex.get("bots", []):
                if bot_data.get("name") == name:
                    lines.append(f"""
📊 *Live State (Cortex):*
💲 Price: `${bot_data.get('price', '?')}`
📦 Position: `{bot_data.get('position', '?')} BTC`
📐 Grid: `${bot_data.get('grid', '?')}`
🎯 Regime: `{bot_data.get('regime', '?')}` | Intent: `{bot_data.get('intent', '?')}`
🛡️ L1 Conf: `{bot_data.get('confidence', '?')}%`
⚠️ Toxic: `{bot_data.get('toxic', '?')}`""")
                    break
    except Exception as e:
        log.debug(f"Cortex state read failed: {e}")

    # ── PnL from FIFO engine ──
    try:
        pnl_path = os.path.join(PROJECT_ROOT, "shared")
        if pnl_path not in sys.path:
            sys.path.insert(0, pnl_path)
        from pnl_engine import PnlDatabase, format_pnl_short

        db = PnlDatabase()
        w1 = db.get_realized_window(name, 1)
        w24 = db.get_realized_window(name, 24)
        w7 = db.get_realized_window(name, 168)
        db.close()

        if w24["fills"] > 0 or w7["fills"] > 0:
            lines.append(f"""
💰 *PnL (FIFO):*
1h:  `{format_pnl_short(w1['realized'])}` ({w1['fills']} fills)
24h: `{format_pnl_short(w24['realized'])}` ({w24['fills']} fills, {w24['closed_trades']} trades)
7d:  `{format_pnl_short(w7['realized'])}` ({w7['fills']} fills)""")
        else:
            lines.append("\n💰 _Žádné fills_")
    except Exception as e:
        log.debug(f"PnL query failed for {name}: {e}")
        lines.append("\n💰 _PnL: nedostupné_")

    return "\n".join(lines)

# ── NATURAL LANGUAGE (AI) ──────────────────────────────────
NL_INTENT_PROMPT = """You are SNIPER Commander, an AI that controls a trading bot armada.
Available bots: hydra, moonshot, grid, trigon
Available actions: start, stop, restart, pause, unpause, status, panic, help, analyze

Parse the user's message (Czech or English) and return a JSON object:
{"bot": "hydra|moonshot|grid|trigon|all|none", "action": "start|stop|restart|pause|unpause|status|panic|help|analyze|chat", "response": "short Czech response to user"}

Rules:
- "vypni/zastav/kill" → action=stop
- "zapni/spusť/start" → action=start
- "restartuj/restart" → action=restart
- "pozastav/pauza/pause" → action=pause
- "obnov/resume/unpause" → action=unpause
- "stav/status/jak se daří" → action=status, bot=all
- "panika/panic/zastavit vše" → action=panic
- "analýza/rozbor/analyze" → action=analyze
- General chat/question → action=chat, include a friendly response
- If bot is not specified but action is clear, ask which bot

User message: """

@bot.message_handler(func=lambda m: True)
def handle_natural_language(message):
    """Catch-all: parse natural language via Gemini."""
    if not auth(message): return

    text = message.text.strip()
    if not text:
        return

    log.info(f"NL input: '{text}'")

    # Try Gemini for intent parsing
    try:
        prompt = NL_INTENT_PROMPT + f'"{text}"'
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=30
        )
        raw = result.stdout.strip()

        # Extract JSON from response (Gemini might wrap it)
        json_match = None
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                json_match = line
                break
        if not json_match and "{" in raw:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            json_match = raw[start:end]

        if not json_match:
            bot.reply_to(message, f"🐺 {raw[:500]}")
            return

        intent = json.loads(json_match)
        bot_name = intent.get("bot", "none").lower()
        action = intent.get("action", "chat").lower()
        response = intent.get("response", "")

        log.info(f"NL intent: bot={bot_name} action={action}")

        # Execute the parsed intent
        if action == "chat":
            bot.reply_to(message, f"🐺 {response}")
            return

        if action == "help":
            cmd_help(message)
            return

        if action == "analyze":
            bot.reply_to(message, f"🐺 {response}\n\n🔍 Spouštím analýzu...")
            _run_analysis(message)
            return

        if action == "panic":
            cmd_panic(message)
            return

        if action == "status":
            if bot_name in BOTS:
                bot.reply_to(message, _bot_status(bot_name))
            else:
                bot.reply_to(message, get_status())
            return

        if bot_name not in BOTS:
            bot.reply_to(message, f"🐺 {response}\n\n❓ Který bot? Řekni: hydra, moonshot, grid, trigon")
            return

        # Execute bot action
        action_map = {
            "start": start_bot,
            "stop": stop_bot,
            "restart": restart_bot,
            "pause": pause_bot,
            "unpause": unpause_bot,
        }

        handler = action_map.get(action)
        if handler:
            result_msg = handler(bot_name)
            prefix = f"🐺 {response}\n\n" if response else ""
            bot.reply_to(message, f"{prefix}{result_msg}")
        else:
            bot.reply_to(message, f"🐺 {response}")

    except json.JSONDecodeError:
        bot.reply_to(message, f"🐺 Rozumím ti, ale nedokázal jsem to zpracovat. Zkus příkaz: `/hydra start`")
    except subprocess.TimeoutExpired:
        bot.reply_to(message, "⚠️ AI timeout — zkus slash příkaz: `/hydra start`")
    except Exception as e:
        log.error(f"NL error: {e}")
        bot.reply_to(message, f"❌ Chyba: {e}")

def _run_analysis(message):
    """Run Gemini market analysis."""
    try:
        # Gather status
        status_data = {}
        for name in BOTS:
            status_data[name] = {
                "running": is_running(name),
                "pid": get_pid(name),
            }

        prompt = f"""You are SNIPER, the AI commander of a 4-bot HFT trading armada.
Bots: {json.dumps(status_data)}
Give a brief strategic analysis in Czech (5 sentences max):
1) Status: which bots are running
2) Recommendations: should any bot be started/stopped?
3) Market awareness: general BTC market comment"""

        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=60
        )
        response = result.stdout.strip()[:3500]
        bot.send_message(message.chat.id, f"🔮 *SNIPER Analýza:*\n\n{response}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Analýza selhala: {e}")


# ── SCHEDULED REPORTS ──────────────────────────────────────
def scheduled_reports_loop():
    """Send hourly, daily, weekly, monthly reports automatically."""
    last_hour = -1
    last_day = -1
    last_week = -1
    last_month = -1

    while True:
        time.sleep(60)  # Check every minute
        try:
            now = datetime.now(timezone(timedelta(hours=1)))  # CET
            hour = now.hour
            day = now.day
            weekday = now.weekday()  # 0=Mon

            # ── HOURLY (every hour, on the hour) ──
            if hour != last_hour:
                last_hour = hour
                report = build_report("hourly")
                bot.send_message(AUTHORIZED_CHAT_ID, f"🐺 {report}")
                log.info(f"Hourly report sent ({hour}:00)")

            # ── DAILY (every day at 00:00) ──
            if hour == 0 and day != last_day:
                last_day = day
                report = build_report("daily")
                bot.send_message(AUTHORIZED_CHAT_ID, f"🐺 {report}")
                log.info("Daily report sent")

            # ── WEEKLY (Monday at 00:00) ──
            if hour == 0 and weekday == 0 and last_week != now.isocalendar()[1]:
                last_week = now.isocalendar()[1]
                report = build_report("weekly")
                bot.send_message(AUTHORIZED_CHAT_ID, f"🐺 {report}")
                log.info("Weekly report sent")

            # ── MONTHLY (1st of month at 00:00) ──
            if hour == 0 and day == 1 and last_month != now.month:
                last_month = now.month
                report = build_report("monthly")
                bot.send_message(AUTHORIZED_CHAT_ID, f"🐺 {report}")
                log.info("Monthly report sent")

        except Exception as e:
            log.error(f"Scheduled report error: {e}")


# ── MAIN ────────────────────────────────────────────────────
def main():
    log.info("🏛️ Sniper Armada — Telegram Commander v2.0")
    log.info(f"   Monitoring: {', '.join(BOTS.keys())}")
    log.info(f"   Auth chat_id: {AUTHORIZED_CHAT_ID}")

    # Kill any competing tg_listener processes (they use the same token)
    try:
        subprocess.run(["pkill", "-f", "tg_listener"], capture_output=True)
        log.info("Killed competing tg_listener processes")
    except Exception:
        pass

    # Clear any existing webhook (prevents 409 conflicts)
    import requests
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true", timeout=10)
        log.info(f"Webhook cleared: {r.status_code}")
    except Exception:
        pass

    # Wait for Telegram to release previous polling connection
    time.sleep(5)

    # Start scheduled reports thread (hourly/daily/weekly/monthly)
    threading.Thread(target=scheduled_reports_loop, daemon=True).start()

    # Start bot polling with robust retry
    log.info("Polling Telegram...")
    while True:
        try:
            bot.remove_webhook()
            time.sleep(1)
            bot.polling(
                timeout=60,
                long_polling_timeout=60,
                allowed_updates=["message"],
                skip_pending=True,
                non_stop=True,
            )
        except KeyboardInterrupt:
            log.info("Shutting down...")
            break
        except Exception as e:
            import traceback
            err_str = str(e)
            log.error(f"Polling crashed: {err_str}")
            log.error(traceback.format_exc())
            if "409" in err_str or "Conflict" in err_str:
                log.warning("409 Conflict — waiting 60s for API cooldown...")
                subprocess.run(["pkill", "-f", "tg_listener"], capture_output=True)
                time.sleep(60)
            else:
                time.sleep(15)


if __name__ == "__main__":
    main()
