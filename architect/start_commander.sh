#!/bin/bash
# 🏛️ Sniper Armada — Telegram Commander Launcher
# Kills any existing instances, waits for API cooldown, then starts

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG="$PROJECT_ROOT/logs/tg_commander.log"

# Kill existing instances
pkill -9 -f tg_commander.py 2>/dev/null
pkill -9 -f tg_listener.py 2>/dev/null
sleep 2

echo "[$(date)] Starting Telegram Commander..." >> "$LOG"

cd "$PROJECT_ROOT" || exit 1

# Source .env
set -a
source "$PROJECT_ROOT/.env" 2>/dev/null
set +a

exec python3 "$SCRIPT_DIR/tg_commander.py"
