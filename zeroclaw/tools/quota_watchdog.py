#!/usr/bin/env python3
"""
ZeroClaw Quota Watchdog — monitors Gemini API quota status.
Runs as a cron job every 5 minutes via ZeroClaw scheduler.

If the Gemini API returns 429 (quota exhausted), sends a Telegram alert
and logs the event for the L2 Oracle to handle on next cycle.

Usage: python3 quota_watchdog.py
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Load from .env
def load_env():
    env = {}
    env_path = "/home/wwwenda/sniper/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env

def check_gemini_quota(api_key: str) -> dict:
    """Send a minimal request to Gemini to check quota status."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": "1"}]}],
        "generationConfig": {"maxOutputTokens": 1}
    }).encode()

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"status": "ok", "code": resp.status}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        return {"status": "quota_exhausted" if e.code == 429 else "error", "code": e.code, "body": body}
    except Exception as e:
        return {"status": "error", "code": 0, "body": str(e)}

def send_telegram_alert(bot_token: str, message: str):
    """Send alert to all recent Telegram chat IDs."""
    # Read last known chat_id from commander state
    chat_id_file = "/home/wwwenda/beroun-brain/short_term/telegram_chat_id.txt"
    chat_ids = []

    if os.path.exists(chat_id_file):
        with open(chat_id_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    chat_ids.append(line)

    if not chat_ids:
        print(f"  No chat_id found, logging only: {message}")
        return

    for chat_id in chat_ids[:3]:  # Max 3 recipients
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

def main():
    env = load_env()
    api_key = env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    tg_token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")

    if not api_key:
        print("ERROR: GEMINI_API_KEY not set")
        sys.exit(1)

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    result = check_gemini_quota(api_key)

    if result["status"] == "ok":
        print(f"  ✅ [{now}] Gemini quota OK")
    elif result["status"] == "quota_exhausted":
        msg = f"🚨 <b>L2 QUOTA EXHAUSTED</b>\n\n⏰ {now}\n📛 Gemini API returned 429\n💡 ZeroClaw L2 Oracle is OFFLINE\n\n🔧 Action: Top up billing at https://ai.dev\n\n<i>L1 Candle (local) is unaffected.</i>"
        print(f"  🚨 [{now}] QUOTA EXHAUSTED — sending Telegram alert")
        if tg_token:
            send_telegram_alert(tg_token, msg)
        # Log to file for L2 to read
        log_path = "/home/wwwenda/beroun-brain/short_term/quota_watchdog.log"
        with open(log_path, "a") as f:
            f.write(f"{now} | QUOTA_EXHAUSTED | {result.get('body', '')[:200]}\n")
    else:
        msg = f"⚠️ <b>L2 API ERROR</b>\n\n⏰ {now}\nCode: {result['code']}\n{result.get('body', '')[:200]}"
        print(f"  ⚠️ [{now}] API error {result['code']}")
        if tg_token:
            send_telegram_alert(tg_token, msg)

if __name__ == "__main__":
    main()
