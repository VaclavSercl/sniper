#!/usr/bin/env python3
"""
Telegram Dispatcher: Repository Description & Structure (§14)
Sends a comprehensive summary message and markdown document attachments
of the ai-trader-strategy repository to the user's Telegram.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ENV_FILE = Path("/home/wwwenda/.hermes/.env")
REPO_DIR = Path("/home/wwwenda/ai-trader-strategy")


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


def send_message(html_text: str) -> bool:
    token, chat_id = load_creds()
    if not token or not chat_id:
        print("[ERROR] Missing credentials", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    cmd = [
        "curl", "-s", "-X", "POST", url,
        "-H", "Content-Type: application/json",
        "-d", json.dumps(payload, ensure_ascii=False)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and '"ok":true' in res.stdout:
        print("[SENT] Successfully sent message to Telegram")
        return True
    else:
        print(f"[FAIL] Error sending message: {res.stdout}", file=sys.stderr)
        return False


def send_doc(file_path: Path, caption: str) -> bool:
    token, chat_id = load_creds()
    if not token or not chat_id:
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
        print(f"[SENT] Successfully sent document {file_path.name}")
        return True
    else:
        print(f"[FAIL] Error sending document: {res.stdout}", file=sys.stderr)
        return False


def main():
    message_text = (
        "🤖 <b>AI-TRADER-STRATEGY: POPIS A STRUKTURA REPOZITÁŘE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔗 <b>GitHub:</b> <a href=\"https://github.com/VaclavSercl/ai-trader-strategy\">github.com/VaclavSercl/ai-trader-strategy</a>\n\n"
        "🌐 <b>ZÁKLADNÍ ARCHITEKTURA & PRINCIP:</b>\n"
        "• <b>100% Platformově agnostické:</b> Žádné závislosti na konkrétním hardware/OS; funguje na libovolném PC, Linux/Mac/Win serveru, Dockeru i cloudu.\n"
        "• <b>Zero Human in the Loop (ZITL):</b> Všechny strategie jsou navrhovány, zpětně testovány, nasazovány i korigovány výhradně pomocí AI.\n"
        "• <b>Univerzální Master Prompty:</b> Každá strategie obsahuje prompt pro vytvoření kompletního kódu jakýmkoliv moderním LLM agentem.\n"
        "• <b>Deterministický risk perimeter:</b> Hard firewally (Delta neutralita = 0, de-peg circuit breaker na 50 bps, 1x izolovaná marže).\n\n"
        "📁 <b>ČTYŘSTUPŇOVÝ ŽIVOTNÍ CYKLUS STRATEGIÍ:</b>\n"
        "────────────────────────────────────\n"
        "1️⃣ <b><code>strategies/live/</code> — Reálně nasazené</b>\n"
        "   • <b>T14: Triangular FX Dislocation</b>: <b>+16.5 % p.a.</b> (Sharpe: 11.45, DD: 0.095 %)\n"
        "   • <b>T13: Basis & Funding Carry</b>: <b>+13.2 % p.a.</b> (Sharpe: 8.92, DD: 0.084 %)\n\n"
        "2️⃣ <b><code>strategies/paper-trading/</code> — V paper tradingu</b>\n"
        "   • <b>T15: MiCA Cross-Basis Carry</b>: <b>+28.4 % p.a.</b> (Sharpe: 14.60, DD: 0.131 %, Den 1/30)\n\n"
        "3️⃣ <b><code>strategies/backtested/</code> — Otestované na datech</b>\n"
        "   • <b>T12: Dynamic Kalman Cointegration</b>: <b>+22.4 % p.a.</b> (Sharpe: 9.85, DD: 0.280 %)\n\n"
        "4️⃣ <b><code>strategies/proposals/</code> — Návrhy k otestování</b>\n"
        "   • <b>P019: DEX-CEX Synthetic Carry (AMM Hooks)</b>: Cílový výnos: <b>+34.0 % p.a.</b> (est.)\n\n"
        "📑 <b>STANDARD DOKUMENTACE KAŽDÉ STRATEGIE:</b>\n"
        "Každá strategie v repozitáři povinně obsahuje:\n"
        "• <code>README.md</code> — Matematický model a ekonomický edge.\n"
        "• <code>AI_GENERATION_PROMPT.md</code> — Master Prompt pro AI.\n"
        "• <code>PERFORMANCE_HISTORY.md</code> — Historie výnosů v % p.a., max DD a Sharpe.\n"
        "• <code>SPECIFICATION.json</code> — Strojová metadata RFC-001 pro AI agenty.\n\n"
        "<i>Kompletní dokumentace a průvodce životním cyklem přiloženy níže v souborech.</i>"
    )

    print("Sending main Telegram message...")
    msg_ok = send_message(message_text)

    readme_path = REPO_DIR / "README.md"
    lifecycle_path = REPO_DIR / "strategies" / "README.md"
    framework_path = REPO_DIR / "docs" / "AUTONOMOUS_AI_FRAMEWORK.md"

    print("Sending document attachments...")
    d1 = send_doc(readme_path, "📄 AI-TRADER-STRATEGY: Hlavní registr a leaderboard (README.md)")
    d2 = send_doc(lifecycle_path, "🧭 Průvodce 4-stupňovým životním cyklem strategií (strategies/README.md)")
    d3 = send_doc(framework_path, "🛡 Architektura autonomního řízení bez člověka (AUTONOMOUS_AI_FRAMEWORK.md)")

    if msg_ok and d1 and d2 and d3:
        print("[SUCCESS] All telegram dispatches completed successfully.")
        return 0
    else:
        print("[WARNING] Some dispatches failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
