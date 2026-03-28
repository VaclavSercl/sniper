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

def get_status():
    """Get status of all bots."""
    lines = ["🏛️ *SNIPER ARMADA — Status*\n"]
    running = 0
    for name, info in BOTS.items():
        alive = is_running(name)
        pid = get_pid(name) if alive else "—"
        if alive:
            running += 1
        icon = "🟢" if alive else "🔴"
        lines.append(f"{icon} {info['emoji']} *{name.upper()}* — {info['desc']}")
        lines.append(f"   PID: `{pid}` · Core {info['cpu']} · :{info['port']}")

    lines.insert(1, f"Online: *{running}/{len(BOTS)}*\n")
    return "\n".join(lines)

# ── SLASH COMMANDS ──────────────────────────────────────────

@bot.message_handler(commands=["help", "start"])
def cmd_help(message):
    if not auth(message): return
    bot.reply_to(message, """🏛️ *SNIPER ARMADA — Commander v2.0*

🎮 *Ovládání botů:*
`/hydra start` · `/hydra stop` · `/hydra restart`
`/moonshot start` · `/grid pause` · `/trigon unpause`

📊 *Monitoring:*
`/status` — Stav všech botů
`/hydra status` — Detail jednoho botu

🧠 *AI Příkazy:*
`/analyze` — Gemini analýza
`/oracle` — AI strategický cyklus

💬 *Přirozená řeč:*
_"Vypni grid"_ · _"Spusť moonshot"_
_"Jak se daří?"_ · _"Restartuj trigon"_

⚠️ *Nouzové:*
`/panic` — Zastaví VŠECHNY boty
`/resume all` — Spustí všechny boty""")

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
    return f"""{info['emoji']} *{name.upper()}* — {info['desc']}

Status: *{icon}*
PID: `{pid}`
Core: `{info['cpu']}`
Port: `:{info['port']}`
Dashboard: `http://localhost:{info['port']}`"""

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


# ── HOURLY REPORT ──────────────────────────────────────────
def hourly_report_loop():
    """Send armada status every hour."""
    while True:
        time.sleep(3600)
        try:
            status = get_status()
            bot.send_message(AUTHORIZED_CHAT_ID, f"🐺 {status}", parse_mode="Markdown")
        except Exception as e:
            log.error(f"Hourly report error: {e}")


# ── MAIN ────────────────────────────────────────────────────
def main():
    log.info("🏛️ Sniper Armada — Telegram Commander v2.0")
    log.info(f"   Monitoring: {', '.join(BOTS.keys())}")
    log.info(f"   Auth chat_id: {AUTHORIZED_CHAT_ID}")

    # Start hourly report thread
    threading.Thread(target=hourly_report_loop, daemon=True).start()

    # Start bot polling
    log.info("Polling Telegram...")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            log.error(f"Polling error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
