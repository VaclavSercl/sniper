#!/usr/bin/env python3
"""
BEROUN Rich Executive Daily Report Generator (§14)
- Collects verified ground-truth data from PostgreSQL (ticks, funding, paper arbitrage state, ledger)
- Computes multi-currency PnL in Satoshi (benchmark numeraire), USD, and EUR
- Invokes Autonomous 4-Tier AI Cascade (Codex -> Claude -> AGY -> Hermes) for regime & anomaly narrative
- Persists report to PostgreSQL outbox table (reports)
- Dispatches report to Telegram (via hermes send or direct Telegram Bot API)
"""

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("rich_report")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import autonomous_cascade_runner as cascade_runner

CORE_DIR = Path("/opt/beroun/core")
MODE_FILE = Path("/opt/beroun/state/mode")
ENV_FILE = Path("/home/wwwenda/.hermes/.env")


def _get_db_cmd(query: str) -> list[str]:
    """Return command list for psql depending on current execution user."""
    import getpass
    base = ["psql", "-d", "beroun", "-P", "pager=off", "-t", "-A", "-c", query]
    if getpass.getuser() != "beroun":
        return ["sudo", "-u", "beroun"] + base
    return base


def run_query(query: str) -> str:
    """Run SQL query against beroun PostgreSQL DB and return stdout string."""
    try:
        cmd = _get_db_cmd(query)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            logger.error("DB Query error: %s", proc.stderr)
            return ""
        return proc.stdout.strip()
    except Exception as e:
        logger.error("DB query exception: %s", e)
        return ""


def sha256_file(path: Path) -> str:
    """Compute sha256 of file, or return N/A if missing."""
    if not path.exists():
        return "N/A_NOT_FOUND"
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except PermissionError:
        try:
            proc = subprocess.run(
                ["sudo", "-u", "beroun", "sha256sum", str(path)],
                capture_output=True, text=True, timeout=5
            )
            if proc.returncode == 0:
                return proc.stdout.split()[0][:12]
        except Exception:
            pass
        return "N/A_PERM"
    except Exception:
        return "N/A_ERR"


def load_telegram_creds() -> Tuple[Optional[str], Optional[str]]:
    """Load TELEGRAM_BOT_TOKEN and default chat_id from .env."""
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


def get_ground_truth_data() -> Dict[str, Any]:
    """Extract verified state from PostgreSQL and system files."""
    data: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "core_hash": sha256_file(CORE_DIR / "BEROUN_MASTER_v2_5.md"),
        "envelope_hash": sha256_file(CORE_DIR / "envelope.yaml"),
        "mode": MODE_FILE.read_text().strip() if MODE_FILE.exists() else "L0",
        "ladder_level": "L0",
        "ticks": {},
        "funding": {},
        "strategies": {},
        "db_counts": {},
    }

    # 1. Latest ticks per symbol
    ticks_out = run_query(
        "SELECT symbol, price FROM (SELECT DISTINCT ON (symbol) symbol, price, ts "
        "FROM market_ticks ORDER BY symbol, ts DESC) sub;"
    )
    for line in ticks_out.splitlines():
        if "|" in line:
            sym, pr = line.split("|", 1)
            try:
                data["ticks"][sym] = float(pr)
            except ValueError:
                pass

    # 2. Latest funding
    fund_out = run_query(
        "SELECT symbol, rate, mark_price FROM (SELECT DISTINCT ON (symbol) symbol, rate, mark_price, funding_time "
        "FROM market_funding ORDER BY symbol, funding_time DESC) sub;"
    )
    for line in fund_out.splitlines():
        if "|" in line:
            parts = line.split("|")
            sym = parts[0]
            try:
                rate = float(parts[1])
                mark = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
                data["funding"][sym] = {
                    "hourly_rate": rate,
                    "annual_apr_pct": round(rate * 24 * 365 * 100, 2),
                    "mark_price": mark,
                }
            except ValueError:
                pass

    # 3. Strategy Paper States
    strat_out = run_query("SELECT id, state FROM paper_arbitrage_state;")
    for line in strat_out.splitlines():
        if "|" in line:
            sid, state_json = line.split("|", 1)
            try:
                data["strategies"][sid] = json.loads(state_json)
            except Exception:
                pass

    # 4. DB counts
    counts_out = run_query(
        "SELECT 'orders', count(*) FROM orders "
        "UNION ALL SELECT 'fills', count(*) FROM fills "
        "UNION ALL SELECT 'ledger', count(*) FROM ledger;"
    )
    for line in counts_out.splitlines():
        if "|" in line:
            k, v = line.split("|", 1)
            data["db_counts"][k] = int(v) if v.isdigit() else v

    return data


def format_progress_bar(ratio: float, length: int = 10) -> str:
    """Render Unicode visual progress bar [■■□□□□□□□□]."""
    filled = max(0, min(length, int(round(ratio * length))))
    empty = length - filled
    return f"[{'■' * filled}{'□' * empty}]"


def generate_ai_narrative(ground_truth: Dict[str, Any]) -> Tuple[str, str]:
    """Deterministic observation, without launching an unrestricted agent."""
    return ("Výsledky starých paper modelů nejsou kvalifikací strategie. "
            "Stav dat, reálných účtů a provádění vyžaduje samostatné ověření.",
            "DETERMINISTIC")


def build_full_report(data: Dict[str, Any], narrative: str, winning_ai_tier: str) -> str:
    """Never substitute invented balances, reconciliation or qualification."""
    import math
    lines = [
        "SNIPER — PROVOZNÍ REPORT / BEROUN",
        "Vygenerováno UTC: " + datetime.now(timezone.utc).isoformat(),
        "STAV SYSTÉMU: " + str(data.get("mode", "UNKNOWN")),
        "Skutečný burzovní kapitál a PnL: NEOVĚŘENO.",
        "T13: Basis & Funding Carry — starý model, kvalifikace NEOVĚŘENA.",
        "T14: Triangular FX Dislocation — starý model, kvalifikace NEOVĚŘENA.",
        "T15: MiCA Cross-Basis Carry — stará evidence ZNEPLATNĚNA; nové období nepotvrzeno.",
        "Paper dny, počet plnění ani PASS nelze odvodit z běžícího kalendáře.",
        "TRŽNÍ POZOROVÁNÍ (čerstvost zde není ověřena):",
    ]
    for symbol, value in sorted(data.get("ticks", {}).items()):
        if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>0:
            lines.append(f"{symbol}: {value}")
    lines.extend([
        "Reconcile: NEOVĚŘENO; počet porušení není dokladem úspěšných testů.",
        "Zdroj: PostgreSQL; tento report nepotvrzuje zůstatky ani provedené obchody na burze.",
        "Komentář (není kvalifikační důkaz): " + narrative,
        "Způsob komentáře: " + winning_ai_tier,
    ])
    return "\n".join(lines)


def save_report_to_outbox(report_text: str, data: Dict[str, Any]) -> int:
    """Persist report to PostgreSQL reports table (§14 transactional outbox)."""
    payload_obj = {
        "text": report_text,
        "raw_data": data,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload_json = json.dumps(payload_obj, ensure_ascii=False)
    escaped_payload = payload_json.replace("'", "''")

    sql = (
        f"INSERT INTO reports (kind, payload) VALUES ('daily_rich', '{escaped_payload}') "
        "RETURNING id;"
    )
    res = run_query(sql)
    try:
        first_line = res.strip().splitlines()[0] if res.strip() else ""
        report_id = int(first_line)
        logger.info("Report saved to DB outbox with id=%d", report_id)
        return report_id
    except Exception:
        logger.warning("Failed to parse returned report id from: %r", res)
        return -1


def mark_report_delivered(report_id: int):
    """Mark report delivered_at in PostgreSQL."""
    if report_id > 0:
        run_query(f"UPDATE reports SET delivered_at = now() WHERE id = {report_id};")


def send_telegram_direct(text: str, token: str, chat_id: str) -> bool:
    """Fallback direct delivery via Telegram Bot API HTTPS."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error("Direct Telegram send failed: %s", e)
        return False


def deliver_to_telegram(report_text: str) -> bool:
    """Deliver report text to user via hermes send or direct Telegram API."""
    # Method 1: hermes send --to telegram
    hermes_bin = "/home/wwwenda/.local/bin/hermes"
    if os.path.exists(hermes_bin):
        try:
            logger.info("Attempting delivery via hermes send --to telegram")
            proc = subprocess.run(
                [hermes_bin, "send", "--to", "telegram", report_text],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0:
                logger.info("hermes send delivered successfully")
                return True
            else:
                logger.warning("hermes send returned code %d: %s", proc.returncode, proc.stderr)
        except Exception as e:
            logger.warning("hermes send execution failed: %s", e)

    # Method 2: Fallback to direct Telegram Bot API
    token, chat_id = load_telegram_creds()
    if token and chat_id:
        logger.info("Falling back to direct Telegram Bot API delivery to %s", chat_id)
        return send_telegram_direct(report_text, token, chat_id)

    logger.error("No valid delivery channel available for Telegram")
    return False


def main():
    parser = argparse.ArgumentParser(description="Generate and dispatch BEROUN rich executive daily report")
    parser.add_argument("--send", action="store_true", help="Send report to Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Print report to stdout without sending or saving")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose debug logging")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)

    logger.info("Starting BEROUN Rich Executive Report Generation")

    # Step 1: Collect deterministic ground-truth data
    data = get_ground_truth_data()

    # Step 2: AI Narrative synthesis
    narrative, winning_tier = generate_ai_narrative(data)

    # Step 3: Format full report
    report_text = build_full_report(data, narrative, winning_tier)

    if args.dry_run:
        print(report_text)
        return

    # Step 4: Persist to DB outbox (§14)
    report_id = save_report_to_outbox(report_text, data)

    # Step 5: Deliver to Telegram if requested or by default
    if args.send:
        success = deliver_to_telegram(report_text)
        if success:
            mark_report_delivered(report_id)
            logger.info("Daily report successfully delivered to Telegram!")
        else:
            logger.error("Failed to deliver daily report to Telegram")
            sys.exit(1)
    else:
        print(report_text)


if __name__ == "__main__":
    main()
