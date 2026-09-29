#!/usr/bin/env python3
"""
BEROUN Telegram Document & Attachment Dispatcher (§14)
Sends files as downloadable attachments to the user's Telegram chat.
"""

import os
import subprocess
import sys
from pathlib import Path

ENV_FILE = Path("/home/wwwenda/.hermes/.env")


def load_creds():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ALLOWED_USERS", "1076582576")

    if not token and ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TELEGRAM_ALLOWED_USERS="):
                    val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                    chat_id = val.split(",")[0].strip()
    return token, chat_id


def send_doc(file_path: Path, caption: str) -> bool:
    token, chat_id = load_creds()
    if not token or not chat_id:
        print("[ERROR] Missing Telegram bot token or chat ID", file=sys.stderr)
        return False

    if not file_path.exists():
        print(f"[ERROR] File not found: {file_path}", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    cmd = [
        "curl", "-s", "-X", "POST", url,
        "-F", f"chat_id={chat_id}",
        "-F", f"caption={caption}",
        "-F", f"document=@{file_path}"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and '"ok":true' in res.stdout:
        print(f"[SENT] Successfully sent {file_path.name} to Telegram chat {chat_id}")
        return True
    else:
        print(f"[FAIL] Error sending {file_path.name}: {res.stdout}", file=sys.stderr)
        return False


def main():
    files = [
        (
            Path("/opt/sniper/current/platform/research/strategies/T15_MICA_STRATEGIE_REPORT.md"),
            "📄 Strategie T15: Kompletní technická a výzkumná zpráva (2026)"
        ),
        (
            Path("/opt/sniper/current/platform/research/strategies/t15_mica_cross_basis.py"),
            "⚙️ Příloha 1: Výkonný Python modul strategie T15 (research/strategies/t15_mica_cross_basis.py)"
        ),
        (
            Path("/opt/sniper/current/platform/.synthbit/tests/test_t15_strategy.py"),
            "🧪 Příloha 2: Testovací a verifikační sada (.synthbit/tests/test_t15_strategy.py)"
        ),
        (
            Path("/opt/sniper/current/platform/proposals/2026-09-20_P018_strategy_t15_cross_basis.md"),
            "📜 Příloha 3: Formální výzkumný návrh P018 dle §13 Ústavy BEROUN"
        ),
    ]

    success_all = True
    for path, caption in files:
        ok = send_doc(path, caption)
        if not ok:
            success_all = False

    return 0 if success_all else 1


if __name__ == "__main__":
    sys.exit(main())
