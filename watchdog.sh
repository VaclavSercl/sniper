#!/bin/bash
# 🛡️ SNIPER ARMADA — OS-Level Watchdog (Minimal)
# Runs via cron every minute.
#
# This script ONLY monitors if key processes are alive.
# All other monitoring (GPU, disk, RAM, trading, AI) is handled
# by Sentinel inside sovereign-cortex (Rust).
#
# This exists because if Cortex crashes, the Rust sentinel dies too.
# The bash watchdog is the last line of defense.
#
# Install: crontab -e → * * * * * /home/wwwenda/sniper/watchdog.sh >> /home/wwwenda/sniper/logs/watchdog.log 2>&1

set -uo pipefail

ARMADA_ROOT="/home/wwwenda/sniper"
LOCK_DIR="/tmp/sniper_watchdog_locks"
COOLDOWN=900  # 15 min
LOG_DIR="$ARMADA_ROOT/logs"

mkdir -p "$LOCK_DIR" "$LOG_DIR"

# Load .env
if [ -f "$ARMADA_ROOT/.env" ]; then
    set -a; source "$ARMADA_ROOT/.env"; set +a
fi

TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TG_CHAT="${TELEGRAM_CHAT_ID:-}"

# ── Telegram with anti-spam ──
tg_alert() {
    local type="$1" msg="$2"
    local lock="$LOCK_DIR/$type"
    if [ -f "$lock" ]; then
        local age=$(( $(date +%s) - $(stat -c %Y "$lock" 2>/dev/null || echo 0) ))
        [ "$age" -lt "$COOLDOWN" ] && return 0
    fi
    [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
        curl -s --max-time 5 "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d "chat_id=${TG_CHAT}" -d "text=${msg}" > /dev/null 2>&1
    touch "$lock"
    echo "[$(date '+%H:%M:%S')] ALERT [$type]: $(echo "$msg" | head -1)"
}

is_alive() { pgrep -f "$1" > /dev/null 2>&1; }

ALERTS=0

# ═══ PROCESS HEALTH ═══

if ! is_alive "hydra-core"; then
    tg_alert "hydra_down" "🚨 WATCHDOG: Hydra-core CRASHED!"
    ALERTS=$((ALERTS + 1))
fi

if ! is_alive "tg_commander"; then
    tg_alert "commander_down" "⚠️ WATCHDOG: TG Commander spadl → restartuji"
    cd "$ARMADA_ROOT" && python3 architect/tg_commander.py >> "$LOG_DIR/tg_commander.log" 2>&1 &
    ALERTS=$((ALERTS + 1))
fi

if ! is_alive "sovereign-cortex"; then
    tg_alert "cortex_down" "🚨 WATCHDOG: Sovereign Cortex spadl → restartuji"
    cd "$ARMADA_ROOT" && taskset -c 3 ./target/release/sovereign-cortex >> "$LOG_DIR/sovereign-cortex.log" 2>&1 &
    ALERTS=$((ALERTS + 1))
fi

if ! is_alive "pnl_daemon"; then
    tg_alert "pnl_down" "⚠️ WATCHDOG: PnL Daemon spadl → restartuji"
    cd "$ARMADA_ROOT" && python3 architect/pnl_daemon.py >> "$LOG_DIR/pnl_daemon.log" 2>&1 &
    ALERTS=$((ALERTS + 1))
fi

if ! is_alive "price_bridge"; then
    tg_alert "bridge_down" "⚠️ WATCHDOG: Price Bridge spadl → restartuji"
    cd "$ARMADA_ROOT" && python3 architect/price_bridge.py >> "$LOG_DIR/price_bridge.log" 2>&1 &
    ALERTS=$((ALERTS + 1))
fi

# Nexus: only alert, don't restart (L2 Oracle manages trading bots)
if ! is_alive "nexus-core"; then
    # Only alert if nexus is supposed to be running (check armada_state)
    NEXUS_MODE=$(python3 -c "import json; print(json.load(open('$ARMADA_ROOT/state/armada_state.json')).get('nexus',{}).get('mode','OFFLINE'))" 2>/dev/null || echo "OFFLINE")
    if [ "$NEXUS_MODE" != "OFFLINE" ] && [ "$NEXUS_MODE" != "PAUSED" ]; then
        tg_alert "nexus_down" "🚨 WATCHDOG: Nexus-core CRASHED! (was $NEXUS_MODE)"
        ALERTS=$((ALERTS + 1))
    fi
fi

# ═══ LOG ═══
if [ "$ALERTS" -gt 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ $ALERTS alerts"
elif [ "$(($(date +%M) % 15))" -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ All processes OK"
fi
