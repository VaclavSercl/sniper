#!/usr/bin/env python3
"""
🐺 BEROUN SNIPER v10.1 — Telegram Oracle Interface (Overlord)
Bi-directional command & control via encrypted Telegram channel.
Includes AI L1/L2 telemetry, Shadow Mode, and inline button callbacks.

Commands:
  /status    — Live bot state (PnL, position, AI bias)
  /analyze   — Gemini 3.1 Pro market analysis
  /ai        — AI Layer Status (L1 + L2 telemetry)
  /shadow    — Toggle Shadow Mode (simulate without trading)
  /golive    — Return from Shadow Mode to live trading
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
import hashlib
import hmac
import requests
import logging
import threading
from datetime import datetime, timedelta, timezone
import telebot

# ── CONFIG ──────────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AUTHORIZED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
CONFIG_BIN = "/home/wwwenda/hft-sniper/target/release/beroun-config"
ORACLE_SCRIPT = "/home/wwwenda/hft-sniper/scripts/oracle_brain.sh"
STATE_JSON = "/dev/shm/beroun/state.json"
L1_STATE_JSON = "/dev/shm/beroun/l1_state.json"
ENGINE_MMAP = "/dev/shm/beroun/engine_state.bin"
BFX_API_KEY = os.environ.get("BITFINEX_API_KEY", "")
BFX_API_SECRET = os.environ.get("BITFINEX_API_SECRET", "")
BFX_REST_URL = "https://api.bitfinex.com"
LOG_DIR = "/home/wwwenda/hft-sniper/logs"
DAILY_STATS_PATH = f"{LOG_DIR}/daily_stats.json"
CET = timezone(timedelta(hours=1))
PRICE_SCALE = 1e8

# Mmap offsets for AI fields (v10.1)
OFF_SHADOW_MODE = 1616

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

def bfx_rest(path, body=None):
    """Authenticated Bitfinex REST v2 request."""
    nonce = str(int(time.time() * 1000000))
    body_json = json.dumps(body) if body else "{}"
    sig_payload = f"/api/{path}{nonce}{body_json}"
    sig = hmac.new(
        BFX_API_SECRET.encode(), sig_payload.encode(), hashlib.sha384
    ).hexdigest()
    headers = {
        "bfx-nonce": nonce,
        "bfx-apikey": BFX_API_KEY,
        "bfx-signature": sig,
        "Content-Type": "application/json",
    }
    r = requests.post(f"{BFX_REST_URL}/{path}", headers=headers,
                      data=body_json, timeout=10)
    return r.json()

# ── COMMANDS ────────────────────────────────────────────────
@bot.message_handler(commands=["start", "help"])
def cmd_help(message):
    if not auth(message): return
    log.info("Help/Start requested")
    try:
        bot.reply_to(message, """🐺 *SNIPER v10.2 — Command Center*

📊 /status — Live stav
🧠 /ai — AI Status (L1 + L2)
🧠 /brain — Sniper Brain (trvalá paměť)
🌑 /shadow — Shadow Mode
🚀 /golive — Návrat do Live
📅 /report — Denní report
📊 /analytics — Trade analytics
🚨 /close CONFIRM — EMERGENCY CLOSE
🔍 /analyze — Gemini analýza
📐 /grid 8.5 — Nastavit grid
💰 /capital 400 — Kapitál
🛑 /loss 20 — Loss limit
⏸️ /pause / ▶️ /resume
⚠️ /cautious — Macro defense
🔮 /oracle — Vynutit AI cyklus
❓ /help — Tento přehled""")
        log.info("Help sent OK")
    except Exception as e:
        log.error(f"Help send error: {e}")


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

        eq = state.get("equity", {})
        total_eq = eq.get('total_usd', 0)

        msg = f"""📊 *BEROUN SNIPER v9.2 — Live Status*

💎 *Total Equity:* `${total_eq:.2f}`
  ├─ Cash:  `${eq.get('wallet_usd', 0):.2f}`
  └─ BTC:   `${eq.get('btc_value_usd', 0):.2f}` (`{eq.get('wallet_btc', 0):.6f}` BTC)

💲 *Cena:*  `${p.get('micro_price', 0):.2f}`  (spread: `${p.get('spread', 0):.2f}`)
📦 *Pozice:* `{pos.get('net_btc', 0):.5f}` BTC @ `${pos.get('avg_entry_price', 0):.2f}`

💰 *PnL:*
  Realized:   `${pnl.get('realized_usd', 0):.4f}`
  Unrealized: `${pnl.get('unrealized_usd', 0):.4f}`
  *Total:*    `${pnl.get('total_usd', 0):.4f}`

⚙️ *Risk:*
  Grid: `${risk.get('grid_step_usd', 0):.2f}` ({risk.get('grid_levels', '?')} levels)
  Capital: `${risk.get('authorized_capital_usd', 0):.2f}` / DLL: `-${risk.get('daily_loss_limit_usd', 0):.2f}`
  Paused: `{'YES ⏸️' if risk.get('paused') else 'NO ▶️'}`"""
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

    # Build Gemini prompt with brain context
    brain_ctx = ""
    try:
        result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "context", "--cycles", "6"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            brain_ctx = result.stdout.strip()
    except Exception:
        pass

    prompt = f"""You are SNIPER, the AI commander of Beroun Sniper v10.2 HFT system.
Architecture: L0=Rust Engine, L1=Python Shield, L2=YOU with permanent SQLite memory.
Bot state: {state}
Permanent memory: {brain_ctx}
Analyze: 1) Current market regime 2) Is grid optimal based on your memory? 3) Any risks?
Answer in Czech, 3-5 sentences max. Reference your permanent memory data."""

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


# ── CAUTIOUS MODE (Macro-Event Awareness v9.5) ────────────
cautious_state = {"active": False, "orig_grid": None, "orig_levels": None}

@bot.message_handler(commands=["cautious"])
def cmd_cautious(message):
    if not auth(message): return
    parts = message.text.split()

    if len(parts) >= 2 and parts[1].lower() == "off":
        if cautious_state["active"]:
            _restore_cautious()
            bot.reply_to(message, "▶️ *CAUTIOUS MODE OFF* — Normal trading restored.")
        else:
            bot.reply_to(message, "ℹ️ Cautious mode not active.")
        return

    if cautious_state["active"]:
        bot.reply_to(message, "⚠️ Already in CAUTIOUS MODE. `/cautious off` to disable.")
        return

    raw = run_config("export-json")
    try:
        state = json.loads(raw)
        risk = state.get("risk_params", {})
        cautious_state["orig_grid"] = risk.get("grid_step_usd", 3.0)
        cautious_state["orig_levels"] = risk.get("grid_levels", 3)
    except Exception:
        cautious_state["orig_grid"] = 3.0
        cautious_state["orig_levels"] = 3

    new_grid = cautious_state["orig_grid"] * 2.0
    run_config("set-grid", str(new_grid))
    run_config("set-levels", "1")
    cautious_state["active"] = True
    log.warning(f"CAUTIOUS MODE: grid ${new_grid:.2f}, 1 level, 15min timer")

    def _auto_restore():
        time.sleep(900)
        if cautious_state["active"]:
            _restore_cautious()
            try:
                bot.send_message(AUTHORIZED_CHAT_ID,
                    "🐺 ⏰ *CAUTIOUS MODE expired* (15 min). Normal trading restored.",
                    parse_mode="Markdown")
            except Exception:
                pass

    threading.Thread(target=_auto_restore, daemon=True).start()

    bot.reply_to(message, f"""⚠️ *CAUTIOUS MODE ACTIVATED*

📐 Grid: `${cautious_state['orig_grid']:.2f}` → `${new_grid:.2f}` (2×)
📊 Levels: `{cautious_state['orig_levels']}` → `1`

⏰ Auto-restore za *15 minut*
_Nebo: `/cautious off`_""")


def _restore_cautious():
    if cautious_state["orig_grid"]:
        run_config("set-grid", str(cautious_state["orig_grid"]))
    if cautious_state["orig_levels"]:
        run_config("set-levels", str(cautious_state["orig_levels"]))
    cautious_state["active"] = False
    log.info("CAUTIOUS MODE deactivated")


@bot.message_handler(commands=["close"])
def cmd_close(message):
    if not auth(message): return
    parts = message.text.split()

    if len(parts) < 2 or parts[1].upper() != "CONFIRM":
        bot.reply_to(message, """🚨 *EMERGENCY CLOSE*

Toto uzavře celou pozici MARKET příkazem a ZASTAVÍ bota.

⚠️ Pro potvrzení pošli: `/close CONFIRM`""")
        return

    log.warning("🚨 EMERGENCY CLOSE initiated via Telegram!")
    bot.reply_to(message, "⏳ Provádím Emergency Close...")

    try:
        # 1. Read current position
        raw = run_config("export-json")
        state = json.loads(raw)
        net_btc = state.get("position", {}).get("net_btc", 0)
        price = state.get("price", {}).get("micro_price", 0)

        # 2. Cancel ALL orders via REST API
        cancel_result = bfx_rest("v2/order/cancel/all", {"all": 1})
        log.info(f"Cancel all result: {cancel_result}")

        # 3. Market close if position exists
        close_msg = ""
        if abs(net_btc) > 0.00001:
            close_amt = -net_btc  # Opposite direction
            order_result = bfx_rest("v2/order/submit", {
                "type": "EXCHANGE MARKET",
                "symbol": "tBTCUSD",
                "amount": f"{close_amt:.5f}",
            })
            log.info(f"Market close result: {order_result}")
            close_msg = f"\n📦 Market {'BUY' if close_amt > 0 else 'SELL'} `{abs(close_amt):.5f}` BTC @ ~`${price:.2f}`"
        else:
            close_msg = "\n📦 Žádná pozice k uzavření"

        # 4. PAUSE bot via mmap
        run_config("pause", "true")

        bot.send_message(message.chat.id, f"""🚨 *EMERGENCY CLOSE EXECUTED*
{close_msg}
🛑 Bot is *PAUSED*

_Pro obnovení: `/resume`_""")
        log.warning(f"Emergency close complete. Position was {net_btc:.5f} BTC")

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Emergency close error: `{e}`")
        log.error(f"Emergency close failed: {e}")


def generate_daily_report():
    """Generate daily trading report from logs and equity."""
    today = datetime.now(CET).strftime("%Y-%m-%d")
    log_file = f"{LOG_DIR}/trading.log.{today}"

    # Count trades from log
    buys, sells, volume = 0, 0, 0.0
    try:
        result = subprocess.run(
            ["grep", "trade_executed", log_file],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                d = json.loads(line)
                amt = d.get("fields", {}).get("amount", 0)
                if amt > 0:
                    buys += 1
                else:
                    sells += 1
                volume += abs(amt)
            except json.JSONDecodeError:
                pass
    except Exception:
        pass

    # Count reconnects
    reconnects = 0
    try:
        result = subprocess.run(
            ["grep", "-c", "reconnecting", log_file],
            capture_output=True, text=True, timeout=5
        )
        reconnects = int(result.stdout.strip())
    except Exception:
        pass

    # Get equity
    raw = run_config("export-json")
    try:
        state = json.loads(raw)
    except Exception:
        state = {}

    eq = state.get("equity", {})
    pnl = state.get("pnl", {})
    price = state.get("price", {})

    # Save daily stats
    stats = {
        "date": today,
        "trades": buys + sells,
        "buys": buys,
        "sells": sells,
        "volume_btc": round(volume, 8),
        "realized_pnl": pnl.get("realized_usd", 0),
        "total_equity": eq.get("total_usd", 0),
        "reconnects": reconnects,
        "btc_price": price.get("micro_price", 0),
    }
    try:
        with open(DAILY_STATS_PATH, "w") as f:
            json.dump(stats, f, indent=2)
    except Exception:
        pass

    total_trades = buys + sells
    return f"""📅 *DENNÍ REPORT — {today}*

💎 *Equity:* `${eq.get('total_usd', 0):.2f}`
  ├─ Cash: `${eq.get('wallet_usd', 0):.2f}`
  └─ BTC:  `${eq.get('btc_value_usd', 0):.2f}` (`{eq.get('wallet_btc', 0):.6f}`)

📈 *Obchody:* `{total_trades}` ({buys} buy / {sells} sell)
💰 *Objem:* `{volume:.5f}` BTC
💲 *Realized PnL:* `${pnl.get('realized_usd', 0):.4f}`

🔄 *Reconnecty:* `{reconnects}`
💲 *BTC cena:* `${price.get('micro_price', 0):.2f}`"""


@bot.message_handler(commands=["report"])
def cmd_report(message):
    if not auth(message): return
    log.info("Daily report requested")
    report = generate_daily_report()
    bot.reply_to(message, report)


@bot.message_handler(commands=["analytics"])
def cmd_analytics(message):
    if not auth(message): return
    log.info("Analytics requested")
    bot.reply_to(message, "📊 Analyzuji obchody...")
    try:
        today = datetime.now(CET).strftime("%Y-%m-%d")
        log_file = f"{LOG_DIR}/trading.log.{today}"
        result = subprocess.run(
            ["python3", "/home/wwwenda/hft-sniper/scripts/analytics.py", log_file, "--telegram"],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip()[:3800]
        bot.send_message(message.chat.id, output if output else "⚠️ No data")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Analytics error: `{e}`")


def auto_daily_report():
    """Send daily report at 08:00 CET."""
    while True:
        now = datetime.now(CET)
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait_secs = (target - now).total_seconds()
        log.info(f"Daily report scheduled for {target.isoformat()} ({wait_secs:.0f}s)")
        time.sleep(wait_secs)

        try:
            report = generate_daily_report()
            if TOKEN and AUTHORIZED_CHAT_ID:
                bot.send_message(AUTHORIZED_CHAT_ID, f"🐺 {report}", parse_mode="Markdown")
                log.info("Auto daily report sent")
        except Exception as e:
            log.error(f"Auto daily report failed: {e}")


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

# ── BRAIN COMMAND ──────────────────────────────────────────
@bot.message_handler(commands=["brain"])
def cmd_brain(message):
    if not auth(message): return
    log.info("Brain status requested")

    # Get stats
    stats = ""
    try:
        result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "stats"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            import json as j
            d = j.loads(result.stdout)
            stats = (f"📊 *Statistiky:*\n"
                     f"`Cyklů:     {d.get('total_cycles', 0)}`\n"
                     f"`Alertů:    {d.get('total_alerts', 0)}`\n"
                     f"`Regimes:   {d.get('regime_distribution', 'N/A')}`\n"
                     f"`Avg PnL:   {d.get('avg_pnl_per_cycle', 'N/A')}`\n"
                     f"`Eskalace:  {d.get('escalation_events', 0)}`\n"
                     f"`DB:        {d.get('db_size_bytes', 0)} bytes`\n"
                     f"`Od:        {d.get('first_cycle', 'N/A')}`")
    except Exception as e:
        stats = f"❌ Stats error: {e}"

    # Get context
    context = ""
    try:
        result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "context", "--cycles", "4"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            context = f"\n\n📜 *Paměť:*\n`{result.stdout.strip()[:1500]}`"
    except Exception:
        pass

    bot.reply_to(message, f"🧠 *SNIPER BRAIN v10.2*\n\n{stats}{context}",
                 parse_mode="Markdown")


# Catch-all: forward to Gemini as free-form question
@bot.message_handler(func=lambda m: True)
def cmd_freeform(message):
    if not auth(message): return
    if len(message.text) < 3: return

    log.info(f"Free-form query: {message.text[:50]}")
    state = run_config("export-json")

    # Get Sniper Brain context for permanent memory
    brain_ctx = ""
    try:
        result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "context", "--cycles", "4"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            brain_ctx = result.stdout.strip()
    except Exception:
        pass

    prompt = f"""You are SNIPER, the AI brain of Beroun Sniper v10.2 HFT trading system.
Architecture: L0=Rust HFT Engine, L1=Python Tactical Shield, L2=YOU (Sovereign Oracle with permanent SQLite memory).
You control grid, position limits, and risk parameters. You have permanent memory of all past decisions.

User asks: "{message.text}"
Current bot state: {state}
Permanent memory context: {brain_ctx}

Answer concisely in Czech as the system's AI commander. Max 5 sentences. Be specific with numbers."""

    try:
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=60
        )
        bot.reply_to(message, result.stdout.strip()[:3500])
    except Exception as e:
        bot.reply_to(message, f"❌ {e}")


# ── AI STATUS COMMAND ──────────────────────────────────────
@bot.message_handler(commands=["ai"])
def cmd_ai(message):
    if not auth(message): return
    log.info("AI status requested")

    # Read L1 state
    try:
        with open(L1_STATE_JSON, 'r') as f:
            l1 = json.load(f)
    except Exception:
        l1 = {}

    raw = run_config("export-json")
    try:
        state = json.loads(raw)
    except Exception:
        state = {}

    analytics = state.get('analytics', {})
    toxic = analytics.get('toxic_flow_hits', 0)
    fills = analytics.get('session_fills', 0)
    confidence = l1.get('confidence', 0)
    fp_rate = l1.get('false_positive_rate', 0)
    success_rate = l1.get('success_rate', 0)
    threshold = l1.get('sweep_threshold', 0.70)
    total_sweeps = l1.get('total_sweeps', 0)
    obi = l1.get('obi', 0)

    health = "🟢 Sharp" if confidence > 0.7 else "🟢 Balanced"
    if fp_rate > 0.5:
        health = "🟡 Too sensitive"

    # Read shadow mode
    shadow = "🚀 LIVE"
    try:
        import mmap as mmap_mod
        import struct
        fd_m = os.open(ENGINE_MMAP, os.O_RDONLY)
        mm = mmap_mod.mmap(fd_m, 0, access=mmap_mod.ACCESS_READ)
        shadow_val = struct.unpack_from('<Q', mm, OFF_SHADOW_MODE)[0]
        shadow = "🌑 SHADOW" if shadow_val == 1 else "🚀 LIVE"
        mm.close()
        os.close(fd_m)
    except Exception:
        pass

    msg = f"""🧠 *AI INTELLIGENCE STATUS v10.1*
━━━━━━━━━━━━━━━━━━━━━━━━

🎤 *Mode:* `{shadow}`

🛡️ *L1 Shield (Taktika)*
`OBI:          {obi:+.4f}`
`Confidence:   {confidence:.0%}`
`Threshold:    {threshold:.3f}`
`FP Rate:      {fp_rate:.0%}`
`Success Rate: {success_rate:.0%}`
`Total Sweeps: {total_sweeps}`
`Health:       {health}`

📊 *Session*
`Fills:        {fills}`
`Toxic Hits:   {toxic}`
`Toxicity:     {toxic / max(fills, 1) * 100:.1f}%`

_L1 se adaptivně učí z každého sweepu._
_L2 Oracle běží každých 5 minut._"""

    bot.reply_to(message, msg)


# ── SHADOW MODE COMMANDS ──────────────────────────────────
@bot.message_handler(commands=["shadow"])
def cmd_shadow(message):
    if not auth(message): return
    log.warning("Shadow mode activated via Telegram")
    try:
        import mmap as mmap_mod
        import struct
        fd_m = os.open(ENGINE_MMAP, os.O_RDWR)
        mm = mmap_mod.mmap(fd_m, 0, access=mmap_mod.ACCESS_WRITE)
        struct.pack_into('<Q', mm, OFF_SHADOW_MODE, 1)
        mm.close()
        os.close(fd_m)
        run_config("pause", "true")
        bot.reply_to(message, """🌑 *SHADOW MODE ACTIVATED*

Bot pokračuje v simulaci, ale neposílá reálné objednávky.
L1 + L2 AI dál běží a učí se.

_Pro návrat: `/golive`_""")
    except Exception as e:
        bot.reply_to(message, f"❌ Shadow mode error: {e}")


@bot.message_handler(commands=["golive"])
def cmd_golive(message):
    if not auth(message): return
    log.warning("Go Live activated via Telegram")
    try:
        import mmap as mmap_mod
        import struct
        fd_m = os.open(ENGINE_MMAP, os.O_RDWR)
        mm = mmap_mod.mmap(fd_m, 0, access=mmap_mod.ACCESS_WRITE)
        struct.pack_into('<Q', mm, OFF_SHADOW_MODE, 0)
        struct.pack_into('<q', mm, 1624, 0)  # Reset shadow PnL
        mm.close()
        os.close(fd_m)
        run_config("pause", "false")
        bot.reply_to(message, """🚀 *LIVE MODE RESTORED*

Bot obnoven do ostrého režimu.
Shadow PnL resetováno.

⚠️ _Všechny objednávky jsou ostré!_""")
    except Exception as e:
        bot.reply_to(message, f"❌ Go live error: {e}")


# ── MAIN ────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("🐺 Beroun Telegram Interface v10.1 (Overlord) starting...")
    log.info(f"   Authorized chat_id: {AUTHORIZED_CHAT_ID}")
    log.info(f"   Config binary: {CONFIG_BIN}")

    backoff = 5
    MAX_BACKOFF = 60
    offset = None

    # Start auto daily report scheduler (08:00 CET)
    report_thread = threading.Thread(target=auto_daily_report, daemon=True)
    report_thread.start()

    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout=30, long_polling_timeout=25)
            backoff = 5  # Reset on success

            for update in updates:
                offset = update.update_id + 1
                bot.process_new_updates([update])

        except telebot.apihelper.ApiTelegramException as e:
            if "409" in str(e):
                log.warning(f"409 Conflict — waiting {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
            else:
                log.error(f"Telegram API error: {e}")
                time.sleep(10)

        except KeyboardInterrupt:
            log.info("Shutting down...")
            break

        except Exception as e:
            log.error(f"Polling error: {e}")
            time.sleep(10)
            backoff = 5

