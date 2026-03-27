#!/usr/bin/env python3
"""
🐺 BEROUN SNIPER v10.7 — Telegram Sovereign Command Center
Bi-directional command & control via encrypted Telegram channel.
Includes AI L1/L2 telemetry, Shadow Mode, Ghost Mode, and Sovereign Intent.

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
import re
import subprocess
import time
import hashlib
import hmac
import requests
import logging
import threading
from datetime import datetime, timedelta, timezone
import telebot
from telebot import apihelper
apihelper.ENABLE_MIDDLEWARE = True

# ── CONFIG ──────────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AUTHORIZED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
CONFIG_BIN = "/home/wwwenda/hft-sniper/target/release/beroun-config"
ORACLE_SCRIPT = "/home/wwwenda/hft-sniper/scripts/sniper_orchestrator.py"
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

# Mmap offsets for AI fields (v10.4-v10.7)
OFF_SHADOW_MODE = 1616
OFF_L1_UPTIME = 1664
OFF_GHOST_TRANSPARENCY = 1672
OFF_GHOST_INJECTIONS = 1688
OFF_GHOST_VEL_REJECTS = 1696
OFF_AI_FREEZE_MS = 1784
OFF_AI_FIRE_INTERVAL = 1792
OFF_AI_GHOST_TRIGGER = 1800
OFF_AI_INTENT = 1808
OFF_AI_REGISTRY_VER = 1816

# Intent presets: {intent_id: (name, grid_mult, freeze_ms, fire_interval, ghost_trigger_pct, ghost_trans)}
INTENT_PRESETS = {
    0: ("SOVEREIGN",   1.0, 4000, 3000, 50, None),     # AI decides everything
    1: ("AGGRESSIVE",  0.6, 2000, 1500, 30, 10000),    # Tight grid, fast fire, no ghost
    2: ("DEFENSIVE",   2.0, 6000, 5000, 80, 1000),     # Wide grid, slow fire, ghost ON
    3: ("SCOUT",       0.8, 3000, 2000, 40, None),      # Shadow mode + narrow grid for data
}

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
        bot.reply_to(message, """🐺 *SNIPER v10.7 — Sovereign Command Center*

🎯 *Intent (Strategické směry):*
⚡ /aggressive — Úzký grid, rychlá exekuce
🛡 /defensive — Široký grid, Ghost ON
🔬 /scout — Shadow Mode + sběr dat
🧠 /sovereign — AI řídí vše (default)

📊 *Monitoring:*
/status — Live stav
/ai — AI Status (L1+L2+Ghost)
/brain — Sniper Brain
/analytics — Trade analytics
/report — Denní report

🧠 *AI:*
/oracle — Vynutit AI cyklus
/analyze — Gemini analýza
/backtest — Neural Cross backtest
/validate — Lesson Validation

⚙️ *Manuální:*
/grid 8.5 — Grid (override AI)
/capital 400 — Kapitál
/loss 20 — Loss limit
/pause / /resume
/cautious — Macro defense
/shadow / /golive
🚨 /close CONFIRM — EMERGENCY CLOSE""")
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

        msg = f"""📊 *SNIPER v10.4 — Live Status*

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

    prompt = f"""You are SNIPER, the AI commander of Beroun Sniper v10.4 HFT system.
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
        # Get live state
        state = run_config("export-json")
        if state:
            d = json.loads(state)
            a = d.get("analytics", {})
            pnl = d.get("pnl", {})
            eq = d.get("equity", {})
            fills = a.get("session_fills", 0)
            buy_vol = a.get("session_buy_volume_btc", 0)
            sell_vol = a.get("session_sell_volume_btc", 0)
            capture = a.get("net_spread_capture_per_btc", 0)
            toxic = a.get("toxic_flow_hits", 0)

            msg = (f"📊 *Trade Analytics (Live)*\n\n"
                   f"`Fills:       {fills}`\n"
                   f"`Buy Vol:     {buy_vol:.6f} BTC`\n"
                   f"`Sell Vol:    {sell_vol:.6f} BTC`\n"
                   f"`Capture:     ${capture:.2f}/BTC`\n"
                   f"`Toxic Hits:  {toxic}`\n"
                   f"`PnL:         ${pnl.get('total_usd', 0):.4f}`\n"
                   f"`Equity:      ${eq.get('total_usd', 0):.2f}`\n")

            # Add brain regime analysis
            try:
                r = subprocess.run(
                    ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "analyze"],
                    capture_output=True, text=True, timeout=10
                )
                if r.returncode == 0 and r.stdout.strip() != "[]":
                    regimes = json.loads(r.stdout)
                    msg += "\n📈 *Brain Analýza (per regime):*\n"
                    for reg in regimes:
                        msg += f"`  {reg['regime']}: {reg['cycles']}x avg_pnl=${reg['avg_pnl']:.4f}`\n"
            except Exception:
                pass

            bot.send_message(message.chat.id, msg, parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, "⚠️ Nelze načíst stav")
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
    bot.reply_to(message, "🔮 Spouštím Sniper AI cyklus... (čekám na Gemini ~30s)")
    log.info("Sniper AI cycle forced via Telegram")
    try:
        # Get brain context + current state for a quick Gemini consultation
        state = run_config("export-json")
        brain_ctx = ""
        try:
            r = subprocess.run(
                ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "context", "--cycles", "6"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                brain_ctx = r.stdout.strip()
        except Exception:
            pass

        prompt = f"""You are SNIPER, the AI commander of Beroun Sniper v10.4.
Bot state: {state}
Permanent memory: {brain_ctx}
Do a quick strategic analysis: 1) Market regime 2) Optimal grid 3) Risk assessment.
Output a concise report in Czech, max 8 sentences."""

        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=120
        )
        response = result.stdout.strip()[:3500]
        bot.send_message(message.chat.id, f"🔮 *SNIPER Oracle v10.4:*\n\n{response}",
                         parse_mode="Markdown")
    except subprocess.TimeoutExpired:
        bot.send_message(message.chat.id, "⚠️ Gemini timeout (120s)")
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
            d = json.loads(result.stdout)
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

    # Get lessons
    lessons = ""
    try:
        result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "lessons"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            
            ll = json.loads(result.stdout)
            if ll:
                lessons = f"\n\n📝 *Lekce ({len(ll)}):*"
                for lx in ll[:5]:
                    lessons += (f"\n`  {lx['regime']}/{lx['rule_type']} "
                               f"(conf={lx.get('confidence',0):.0%})`")
    except Exception:
        pass

    # Get Alpha Audit (v10.4)
    alpha = ""
    try:
        result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "alpha-report"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            
            a = json.loads(result.stdout)
            ai = a.get("alpha", {})
            verdict_icon = "📈" if ai.get("verdict") == "EFFICIENT" else "📉" if ai.get("verdict") == "OVER_CAUTIOUS" else "➖"
            alpha = (f"\n\n{verdict_icon} *Alpha Audit (24h):*\n"
                    f"`  Saved:  +${ai.get('saved_usd','0')}`\n"
                    f"`  Missed: -${ai.get('missed_usd','0')}`\n"
                    f"`  Net:    ${ai.get('net_alpha','0')}`\n"
                    f"`  Verdict: {ai.get('verdict','?')}`")
    except Exception:
        pass

    bot.reply_to(message, f"🧠 *SNIPER BRAIN v10.4*\n\n{stats}{context}{lessons}{alpha}",
                 parse_mode="Markdown")


@bot.message_handler(commands=["backtest"])
def cmd_backtest(message):
    if not auth(message): return
    log.info("Backtest requested")
    bot.reply_to(message, "🌙 Spouštím Neural Cross backtest + AI Coach...")

    try:
        

        # Step 1: Run statistical backtest
        result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "backtest", "--days", "1"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            bot.send_message(message.chat.id, f"❌ Backtest error: {result.stderr[:200]}")
            return

        d = json.loads(result.stdout)

        msg = (f"🌙 *Neural Cross Backtest v10.4*\n\n"
               f"`Status:   {d.get('status', '?')}`\n"
               f"`Cyklů:    {d.get('cycles_analyzed', 0)}`\n"
               f"`Regimes:  {', '.join(d.get('regimes', []))}`\n"
               f"`Lekce:    +{d.get('lessons_generated',0)} nové, "
               f"{d.get('lessons_updated',0)} aktualizované`\n")

        # Add findings
        for f in d.get('findings', []):
            w = f.get('winners', {})
            l = f.get('losers', {})
            msg += (f"\n📊 *{f.get('regime', '?')}:*\n"
                    f"`  Winners: {w.get('count',0)} (grid ${w.get('avg_grid','?')}, toxic {w.get('avg_toxic','?')})`\n"
                    f"`  Losers:  {l.get('count',0)} (grid ${l.get('avg_grid','?')}, toxic {l.get('avg_toxic','?')})`\n"
                    f"`  Neutral: {f.get('neutral_count',0)}`")

        bot.send_message(message.chat.id, msg, parse_mode="Markdown")

        # Step 2: Get worst cycles
        worst_result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "worst-cycles", "--n", "3", "--hours", "24"],
            capture_output=True, text=True, timeout=10
        )
        worst_cycles = worst_result.stdout.strip() if worst_result.returncode == 0 else "[]"
        worst_list = json.loads(worst_cycles)

        if not worst_list:
            bot.send_message(message.chat.id, "✅ Žádné ztrátové cykly — není co kritizovat!")
            return

        # Show worst cycles (plain text — reasoning has special chars)
        worst_msg = "📉 Nejhorší rozhodnutí (24h):\n"
        for wc in worst_list[:3]:
            worst_msg += (f"\n{wc.get('timestamp','')} PnL=${wc.get('pnl',0):.2f}\n"
                         f"  Grid=${wc.get('grid_step',0):.1f} | Toxic={wc.get('toxic_hits',0)}\n"
                         f"  {wc.get('reasoning_at_the_time','?')[:120]}\n")
        bot.send_message(message.chat.id, worst_msg)

        # Step 3: AI Coach (Gemini Self-Critique)
        bot.send_message(message.chat.id, "🧠 Spouštím Gemini Self-Critique (AI Coach)... ~30s")

        coach_prompt = f"""You are SNIPER performing SELF-CRITIQUE of your past decisions.

═══ STATISTICAL BACKTEST ═══
{result.stdout.strip()[:2000]}

═══ YOUR WORST DECISIONS ═══
{worst_cycles[:2000]}

═══ YOUR TASK ═══
1. What was WRONG with your reasoning in the worst cycles?
2. What PATTERN connects your failures?
3. Generate 1-3 LESSONS (rules for yourself)

Be BRUTALLY HONEST. RESPOND WITH JSON ONLY:
{{"self_critique": "2-3 sentences", "pattern_identified": "common thread", "lessons": [{{"regime": "X", "rule_type": "grid_floor", "condition": {{"toxic_above": 400}}, "action": {{"grid_min": 12.0}}, "reasoning": "why", "confidence": 0.65}}]}}"""

        coach_result = subprocess.run(
            ["gemini", "-p", coach_prompt],
            capture_output=True, text=True, timeout=120
        )
        raw = coach_result.stdout.strip()

        match = re.search(r'\{[\s\S]*"lessons"[\s\S]*\}', raw)
        if match:
            coach = json.loads(match.group())
            critique = coach.get('self_critique', 'N/A')
            pattern = coach.get('pattern_identified', 'N/A')
            lessons = coach.get('lessons', [])

            # Save lessons
            saved = 0
            for lesson in lessons[:3]:
                if not all(k in lesson for k in ['regime', 'rule_type', 'reasoning']):
                    continue
                try:
                    subprocess.run(
                        ["/home/wwwenda/hft-sniper/target/release/beroun-brain",
                         "save-lesson", json.dumps(lesson)],
                        capture_output=True, text=True, timeout=5
                    )
                    saved += 1
                except Exception:
                    pass

            # Coach report (plain text — AI content has special chars)
            coach_msg = (f"🧠 AI Self-Critique (Gemini Coach)\n\n"
                        f"🔍 Sebekritika:\n{critique}\n\n"
                        f"🎯 Vzorec selhání:\n{pattern}\n\n"
                        f"📝 Nové lekce: {saved} uloženo do Brain\n")
            for i, lesson in enumerate(lessons[:3]):
                conf = lesson.get('confidence', 0)
                icon = '🔴' if conf >= 0.7 else '🟡'
                coach_msg += (f"\n{icon} {i+1}. {lesson.get('regime','?')}/{lesson.get('rule_type','?')} "
                             f"(conf={conf:.0%})\n"
                             f"   {lesson.get('reasoning','?')}")

            bot.send_message(message.chat.id, coach_msg)
        else:
            bot.send_message(message.chat.id, "⚠️ Gemini Coach nedodal validní JSON")

        # Step 4: Show all active lessons
        lessons_result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "lessons"],
            capture_output=True, text=True, timeout=5
        )
        if lessons_result.returncode == 0:
            all_lessons = json.loads(lessons_result.stdout)
            if all_lessons:
                lmsg = "📚 Aktivní lekce v Brain:\n"
                for ll in all_lessons[:5]:
                    conf = ll.get('confidence', 0)
                    icon = '🔴' if conf >= 0.7 else '🟡' if conf >= 0.3 else '⚪'
                    action_str = str(ll.get('action', '')).replace('{','').replace('}','').replace('"','')[:80]
                    reasoning = str(ll.get('reasoning', ''))[:100]
                    lmsg += (f"\n{icon} {ll['regime']}/{ll['rule_type']} "
                            f"(conf={conf:.0%}, n={ll.get('sample_count',0)})\n"
                            f"   Action: {action_str}\n"
                            f"   Reason: {reasoning}")
                bot.send_message(message.chat.id, lmsg)

    except Exception as e:
        bot.send_message(message.chat.id, f"Backtest error: {e}")


@bot.message_handler(commands=["validate"])
def cmd_validate(message):
    if not auth(message): return
    log.info("Validation requested")
    bot.reply_to(message, "🔬 Spouštím Lesson Validation Engine...")

    try:
        

        # Step 1: Validate lessons
        result = subprocess.run(
            ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "validate-lessons"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            bot.send_message(message.chat.id, f"Validation error: {result.stderr[:200]}")
            return

        d = json.loads(result.stdout)
        if d.get("status") == "no_active_lessons":
            bot.send_message(message.chat.id, "Zadne aktivni lekce k validaci.")
            return

        msg = f"🔬 Lesson Validation v10.4\n\nValidovano: {d.get('validated', 0)} lekci\n"
        confirmed = 0
        degraded = 0
        for v in d.get("validations", []):
            status = v.get("status", "?")
            icon = "✅" if status == "CONFIRMED" else "⚠️" if status == "DEGRADED" else "⏳" if status == "INSUFFICIENT_DATA" else "➖"
            if status == "CONFIRMED": confirmed += 1
            if status == "DEGRADED": degraded += 1
            msg += (f"\n{icon} {v.get('regime','?')}/{v.get('rule_type','?')}\n"
                   f"   Pre: avg PnL ${v.get('pre_lesson',{}).get('avg_pnl','?')} ({v.get('pre_lesson',{}).get('cycles',0)} cycles)\n"
                   f"   Post: avg PnL ${v.get('post_lesson',{}).get('avg_pnl','?')} ({v.get('post_lesson',{}).get('cycles',0)} cycles)\n"
                   f"   Impact: ${v.get('net_impact','?')} | Conf: {v.get('confidence','?')} ({v.get('confidence_delta','?')})\n"
                   f"   Status: {status}")
        msg += f"\n\nSouhrn: {confirmed} confirmed, {degraded} degraded"
        bot.send_message(message.chat.id, msg)

        # Step 2: Alpha report (separate try/except)
        try:
            alpha = subprocess.run(
                ["/home/wwwenda/hft-sniper/target/release/beroun-brain", "alpha-report"],
                capture_output=True, text=True, timeout=10
            )
            if alpha.returncode == 0:
                a = json.loads(alpha.stdout)
                ai = a.get("alpha", {})
                verdict = ai.get("verdict", "?").replace("_", " ")
                verdict_icon = "📈" if "EFFICIENT" in verdict else "📉" if "CAUTIOUS" in verdict else "➖"
                amsg = (f"{verdict_icon} AI ALPHA REPORT (24h)\n\n"
                       f"PnL total: {a.get('total_pnl','?')} USD\n"
                       f"Cyklu: {a.get('total_cycles',0)} (W:{a.get('winners',0)} L:{a.get('losers',0)})\n"
                       f"Aktivni lekce: {a.get('active_lessons',0)}\n\n"
                       f"AI Saved: +{ai.get('saved_usd','0')} USD\n"
                       f"AI Missed: -{ai.get('missed_usd','0')} USD\n"
                       f"Net Alpha: {ai.get('net_alpha','0')} USD\n"
                       f"Verdict: {verdict}")
                bot.send_message(message.chat.id, amsg, parse_mode=None)
        except Exception as ae:
            log.warning(f"Alpha report display error: {ae}")

    except Exception as e:
        err = str(e).replace('`','').replace('*','').replace('_','')[:200]
        bot.send_message(message.chat.id, f"Validation error: {err}")

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

    prompt = f"""You are SNIPER, the AI brain of Beroun Sniper v10.4 HFT trading system.
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

    msg = f"""🧠 *AI INTELLIGENCE STATUS v10.4*
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


# ═══ v10.7 SOVEREIGN INTENT COMMANDS ═══
def _write_mmap_u64(offset, value):
    """Write u64 to engine mmap."""
    import mmap as mmap_mod
    import struct
    fd_m = os.open(ENGINE_MMAP, os.O_RDWR)
    mm = mmap_mod.mmap(fd_m, 0, access=mmap_mod.ACCESS_WRITE)
    struct.pack_into('<Q', mm, offset, value)
    mm.close()
    os.close(fd_m)

def _read_mmap_u64(offset):
    """Read u64 from engine mmap."""
    import mmap as mmap_mod
    import struct
    fd_m = os.open(ENGINE_MMAP, os.O_RDONLY)
    mm = mmap_mod.mmap(fd_m, 0, access=mmap_mod.ACCESS_READ)
    val = struct.unpack_from('<Q', mm, offset)[0]
    mm.close()
    os.close(fd_m)
    return val

def _apply_intent(intent_id):
    """Apply an intent preset to the AI registry via mmap."""
    name, grid_mult, freeze_ms, fire_interval, ghost_trigger, ghost_trans = INTENT_PRESETS[intent_id]
    _write_mmap_u64(OFF_AI_INTENT, intent_id)
    _write_mmap_u64(OFF_AI_FREEZE_MS, freeze_ms)
    _write_mmap_u64(OFF_AI_FIRE_INTERVAL, fire_interval)
    _write_mmap_u64(OFF_AI_GHOST_TRIGGER, ghost_trigger)
    if ghost_trans is not None:
        _write_mmap_u64(OFF_GHOST_TRANSPARENCY, ghost_trans)
    # Increment registry version
    ver = _read_mmap_u64(OFF_AI_REGISTRY_VER)
    _write_mmap_u64(OFF_AI_REGISTRY_VER, ver + 1)
    # Apply grid via beroun-config
    raw = run_config("export-json")
    try:
        state = json.loads(raw)
        current_grid = state.get("risk_params", {}).get("grid_step_usd", 8.0)
        new_grid = max(3.0, min(100.0, current_grid * grid_mult))
        run_config("set-grid", str(round(new_grid, 2)))
    except Exception:
        pass
    # Log to SQLite
    try:
        import sqlite3
        db = sqlite3.connect('/home/wwwenda/hft-sniper/logs/sniper.db')
        db.execute("INSERT INTO ai_registry (intent, freeze_ms, fire_interval_ms, ghost_mode, reasoning) VALUES (?,?,?,?,?)",
                   (name, freeze_ms, fire_interval, ghost_trans is not None and ghost_trans < 10000,
                    f"Commander set intent to {name} via Telegram"))
        db.commit()
        db.close()
    except Exception:
        pass
    return name

@bot.message_handler(commands=["aggressive"])
def cmd_aggressive(message):
    if not auth(message): return
    log.warning("⚡ AGGRESSIVE intent activated via Telegram")
    try:
        name = _apply_intent(1)
        bot.reply_to(message, """⚡ *AGGRESSIVE MODE ACTIVATED*
════════════════════════
🎯 Grid: ×0.6 (užší)
⏱ Fire Interval: 1500ms (rychlejší)
🛡 Freeze: 2000ms (kratší)
👻 Ghost: OFF (veřejná likvidita pro maker rebates)

⚠️ _Vyšší rychlost = vyšší riziko. DLL zůstává jako pojistka._
_Pro návrat: `/sovereign`_""")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=["defensive"])
def cmd_defensive(message):
    if not auth(message): return
    log.warning("🛡 DEFENSIVE intent activated via Telegram")
    try:
        name = _apply_intent(2)
        bot.reply_to(message, """🛡 *DEFENSIVE MODE ACTIVATED*
════════════════════════
🎯 Grid: ×2.0 (širší)
⏱ Fire Interval: 5000ms (pomalejší)
🛡 Freeze: 6000ms (delší)
👻 Ghost: ON (10% transparency, stealth mode)

_Sniper se stáhne do stínu a loví jen jisté příležitosti._
_Pro návrat: `/sovereign`_""")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=["scout"])
def cmd_scout(message):
    if not auth(message): return
    log.warning("🔬 SCOUT intent activated via Telegram")
    try:
        name = _apply_intent(3)
        # Also activate shadow mode
        _write_mmap_u64(OFF_SHADOW_MODE, 1)
        bot.reply_to(message, """🔬 *SCOUT MODE ACTIVATED*
════════════════════════
🎯 Grid: ×0.8 (mírně užší)
⏱ Fire Interval: 2000ms
🌑 Shadow Mode: ON (simulace bez rizika)

_Sniper sbírá data. Žádné reálné objednávky._
_Pro návrat: `/sovereign` + `/golive`_""")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=["sovereign"])
def cmd_sovereign(message):
    if not auth(message): return
    log.info("🧠 SOVEREIGN intent restored via Telegram")
    try:
        name = _apply_intent(0)
        bot.reply_to(message, """🧠 *SOVEREIGN MODE RESTORED*
════════════════════════
🎯 AI řídí všechny parametry
⏱ Fire: 3000ms | Freeze: 4000ms
👻 Ghost: AI rozhoduje automaticky

_Oracle převzal plné řízení systému._""")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")


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
    log.info("🐺 SNIPER v10.7 Sovereign Telegram Command Center starting...")
    log.info(f"   Authorized chat_id: {AUTHORIZED_CHAT_ID}")
    log.info(f"   Config binary: {CONFIG_BIN}")

    # Start auto daily report scheduler (08:00 CET)
    report_thread = threading.Thread(target=auto_daily_report, daemon=True)
    report_thread.start()

    # Register commands in Telegram menu
    try:
        from telebot.types import BotCommand
        bot.set_my_commands([
            BotCommand("status", "📊 Live stav"),
            BotCommand("ai", "🧠 AI + Ghost + Sovereign"),
            BotCommand("brain", "🧠 Sniper Brain"),
            BotCommand("aggressive", "⚡ Aggressive mode"),
            BotCommand("defensive", "🛡 Defensive mode"),
            BotCommand("scout", "🔬 Scout (Shadow)"),
            BotCommand("sovereign", "🧠 AI kontrola (default)"),
            BotCommand("backtest", "🌙 Neural Cross backtest"),
            BotCommand("validate", "🔬 Lesson Validation"),
            BotCommand("oracle", "🔮 Vynutit AI cyklus"),
            BotCommand("analyze", "🔍 Gemini analýza"),
            BotCommand("analytics", "📊 Trade analytics"),
            BotCommand("report", "📅 Denní report"),
            BotCommand("shadow", "🌑 Shadow Mode"),
            BotCommand("golive", "🚀 Návrat do Live"),
            BotCommand("grid", "📐 Nastavit grid"),
            BotCommand("capital", "💰 Kapitál"),
            BotCommand("loss", "🛑 Loss limit"),
            BotCommand("pause", "⏸️ Pauza"),
            BotCommand("resume", "▶️ Pokračovat"),
            BotCommand("cautious", "⚠️ Macro defense"),
            BotCommand("close", "🚨 EMERGENCY CLOSE"),
            BotCommand("help", "❓ Přehled příkazů"),
        ])
        log.info("   ✅ Telegram menu commands registered")
    except Exception as e:
        log.warning(f"   Menu registration failed: {e}")

    # Debug: log ALL incoming messages
    @bot.middleware_handler(update_types=['message'])
    def log_all_messages(bot_instance, message):
        log.info(f"📩 IN: chat={message.chat.id} text='{message.text[:50] if message.text else 'N/A'}'")

    log.info("Starting polling...")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)
