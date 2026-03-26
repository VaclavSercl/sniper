#!/usr/bin/env python3
"""
🐺 BEROUN SNIPER v9.0 — Telegram Oracle Interface
Bi-directional command & control via encrypted Telegram channel.

Commands:
  /status    — Live bot state (PnL, position, AI bias)
  /analyze   — Gemini 3.1 Pro market analysis
  /grid <N>  — Set grid_step to N USD
  /pause     — Emergency stop
  /resume    — Resume trading
  /oracle    — Force Oracle cycle
  /help      — Show commands
"""

import os
import json
import subprocess
import time
import logging
import telebot

# ── CONFIG ──────────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AUTHORIZED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
CONFIG_BIN = "/home/wwwenda/hft-sniper/target/release/beroun-config"
ORACLE_SCRIPT = "/home/wwwenda/hft-sniper/scripts/oracle_brain.sh"
STATE_JSON = "/dev/shm/beroun/state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TG] %(message)s")
log = logging.getLogger("beroun-tg")

if not TOKEN:
    log.error("TELEGRAM_BOT_TOKEN not set!")
    exit(1)

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

# ── SECURITY: Only respond to authorized chat ──────────────
def auth(message):
    if message.chat.id != AUTHORIZED_CHAT_ID:
        bot.reply_to(message, "⛔ Unauthorized. Your chat ID is not whitelisted.")
        log.warning(f"Unauthorized access from chat_id={message.chat.id}")
        return False
    return True

def run_config(*args):
    """Run beroun-config and return output."""
    try:
        result = subprocess.run(
            [CONFIG_BIN] + list(args),
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() + result.stderr.strip()
    except Exception as e:
        return f"❌ Error: {e}"

# ── COMMANDS ────────────────────────────────────────────────
@bot.message_handler(commands=["start", "help"])
def cmd_help(message):
    if not auth(message): return
    bot.reply_to(message, """🐺 *Beroun Sniper v9.0 — Oracle Interface*

📊 `/status` — Live stav (PnL, pozice, Capital Guard)
🔍 `/analyze` — Gemini 3.1 Pro analýza trhu
📐 `/grid 8.5` — Nastavit grid (s sanity check)
💰 `/capital 400` — Nastavit autorizovaný kapitál ($)
🛑 `/loss 20` — Nastavit denní loss limit ($)
⏸️ `/pause` — Nouzové zastavení
▶️ `/resume` — Obnovit trading
🔮 `/oracle` — Vynutit Oracle cyklus
❓ `/help` — Tento přehled

_Zabezpečeno: jen chat\\_id {}_""".format(AUTHORIZED_CHAT_ID))


@bot.message_handler(commands=["status"])
def cmd_status(message):
    if not auth(message): return
    log.info("Status requested")

    # Get live state from beroun-config
    raw = run_config("export-json")
    try:
        state = json.loads(raw)
        p = state.get("price", {})
        pos = state.get("position", {})
        pnl = state.get("pnl", {})
        intel = state.get("intelligence", {})
        risk = state.get("risk_params", {})

        msg = f"""📊 *BEROUN SNIPER — Live Status*

💲 *Cena:*  `${p.get('micro_price', 0):.2f}`  (spread: `${p.get('spread', 0):.2f}`)
📦 *Pozice:* `{pos.get('net_btc', 0):.5f}` BTC @ `${pos.get('avg_entry_price', 0):.2f}`

💰 *PnL:*
  Realized:   `${pnl.get('realized_usd', 0):.4f}`
  Unrealized: `${pnl.get('unrealized_usd', 0):.4f}`
  *Total:*    `${pnl.get('total_usd', 0):.4f}`

🤖 *AI:*
  L1 Bias: `${intel.get('ai_bias_usd', 0):.2f}`
  L2 OBI:  `{intel.get('l2_obi', 0):+.3f}`
  T2T:     `{intel.get('t2t_micros', 0)}` µs

⚙️ *Risk:*
  Grid: `${risk.get('grid_step_usd', 0):.2f}` ({risk.get('grid_levels', '?')} levels)
  MaxInv: `{risk.get('max_inv_delta_btc', 0):.4f}` BTC
  Paused: `{'YES ⏸️' if risk.get('paused') else 'NO ▶️'}`

🛡️ *Capital Guard:*
  Auth Capital: `${risk.get('authorized_capital_usd', 0):.2f}`
  Loss Limit:   `-${risk.get('daily_loss_limit_usd', 0):.2f}`"""
        bot.reply_to(message, msg)
    except json.JSONDecodeError:
        bot.reply_to(message, f"```\n{raw[:500]}\n```")


@bot.message_handler(commands=["analyze"])
def cmd_analyze(message):
    if not auth(message): return
    log.info("Analysis requested via Gemini")
    bot.reply_to(message, "🔍 Analyzing... (Gemini 3.1 Pro, ~30s)")

    # Get bot state
    state = run_config("export-json")

    # Build Gemini prompt
    prompt = f"""You are the CIO of HFT fund Beroun Sniper.
Bot state: {state}
Analyze: 1) Current market regime 2) Is grid optimal? 3) Any risks?
Answer in Czech, 3-5 sentences max."""

    try:
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=120
        )
        response = result.stdout.strip()[:3500]  # Telegram limit
        bot.send_message(message.chat.id, f"🔮 *Gemini Analýza:*\n\n{response}")
    except subprocess.TimeoutExpired:
        bot.send_message(message.chat.id, "⚠️ Gemini timeout (120s)")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")


@bot.message_handler(commands=["grid"])
def cmd_grid(message):
    if not auth(message): return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: `/grid 8.5`")
        return
    try:
        value = float(parts[1])
        result = run_config("set-grid", str(value))
        bot.reply_to(message, f"📐 {result}")
        log.info(f"Grid set to {value} via Telegram")
    except ValueError:
        bot.reply_to(message, "❌ Neplatné číslo")


@bot.message_handler(commands=["pause"])
def cmd_pause(message):
    if not auth(message): return
    result = run_config("pause", "true")
    bot.reply_to(message, f"🛑 *NOUZOVÉ ZASTAVENÍ*\n{result}")
    log.warning("BOT PAUSED via Telegram!")


@bot.message_handler(commands=["resume"])
def cmd_resume(message):
    if not auth(message): return
    result = run_config("pause", "false")
    bot.reply_to(message, f"▶️ *Trading obnoven*\n{result}")
    log.info("Bot resumed via Telegram")


@bot.message_handler(commands=["capital"])
def cmd_capital(message):
    if not auth(message): return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: `/capital 400`")
        return
    try:
        value = float(parts[1])
        result = run_config("set-capital", str(value))
        bot.reply_to(message, f"🛡️ {result}")
        log.info(f"Capital set to ${value} via Telegram")
    except ValueError:
        bot.reply_to(message, "❌ Neplatné číslo")


@bot.message_handler(commands=["loss"])
def cmd_loss(message):
    if not auth(message): return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: `/loss 20`")
        return
    try:
        value = float(parts[1])
        result = run_config("set-loss", str(value))
        bot.reply_to(message, f"🛡️ {result}")
        log.info(f"Daily loss limit set to ${value} via Telegram")
    except ValueError:
        bot.reply_to(message, "❌ Neplatné číslo")


@bot.message_handler(commands=["oracle"])
def cmd_oracle(message):
    if not auth(message): return
    bot.reply_to(message, "🔮 Spouštím Oracle cyklus... (2-3 min)")
    log.info("Oracle forced via Telegram")
    try:
        result = subprocess.run(
            [ORACLE_SCRIPT],
            capture_output=True, text=True, timeout=300
        )
        # Get last 5 lines of output
        output = result.stdout.strip().split("\n")[-5:]
        bot.send_message(message.chat.id, "✅ Oracle dokončen:\n```\n{}\n```".format("\n".join(output)))
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Oracle error: {e}")


# Catch-all: forward to Gemini as free-form question
@bot.message_handler(func=lambda m: True)
def cmd_freeform(message):
    if not auth(message): return
    if len(message.text) < 3: return

    log.info(f"Free-form query: {message.text[:50]}")
    state = run_config("export-json")

    prompt = f"""User asks: "{message.text}"
Bot state: {state}
Answer concisely in Czech as a Senior HFT Trading Advisor. Max 5 sentences."""

    try:
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=60
        )
        bot.reply_to(message, result.stdout.strip()[:3500])
    except Exception as e:
        bot.reply_to(message, f"❌ {e}")


# ── MAIN ────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info(f"🐺 Beroun Telegram Interface v9.0 starting...")
    log.info(f"   Authorized chat_id: {AUTHORIZED_CHAT_ID}")
    log.info(f"   Config binary: {CONFIG_BIN}")

    while True:
        try:
            bot.polling(non_stop=True, timeout=30)
        except Exception as e:
            log.error(f"Polling error: {e}")
            time.sleep(5)
