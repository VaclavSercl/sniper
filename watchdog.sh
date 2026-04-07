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

# ═══ BOOT GUARD (lockfile-based) ═══
# deploy_armada.sh writes a lockfile with epoch timestamp at boot.
# Watchdog skips its cycle for 120s after boot to prevent duplicates.
BOOT_LOCK="/tmp/sniper_boot.lock"
if [ -f "$BOOT_LOCK" ]; then
    BOOT_TS=$(cat "$BOOT_LOCK" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    BOOT_AGE=$(( NOW - BOOT_TS ))
    if [ "$BOOT_AGE" -lt 120 ]; then
        echo "[$(date '+%H:%M:%S')] ⏳ Boot guard: boot ${BOOT_AGE}s ago (<120s), skipping"
        exit 0
    fi
fi

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
count_procs() { pgrep -f "$1" 2>/dev/null | wc -l; }

# Kill duplicates helper
kill_dupes() {
    local pattern="$1"
    local count=$(count_procs "$pattern")
    if [ "$count" -gt 1 ]; then
        echo "[$(date '+%H:%M:%S')] ⚠️ $pattern has $count instances, killing extras"
        # Keep the oldest PID, kill the rest
        local oldest=$(pgrep -f "$pattern" 2>/dev/null | head -1)
        pgrep -f "$pattern" 2>/dev/null | tail -n +2 | xargs -r kill -9 2>/dev/null
        tg_alert "dupe_${pattern}" "⚠️ WATCHDOG: Duplikát $pattern ($count×) → vyčištěn"
    fi
}

ALERTS=0

# ═══ PROCESS HEALTH ═══

# Trading bots: auto-restart if mode is PAPER or LIVE
for BOT_NAME in hydra moonshot grid trigon nexus; do
    CORE="${BOT_NAME}-core"
    if ! is_alive "$CORE"; then
        BOT_MODE=$(python3 -c "import json; print(json.load(open('$ARMADA_ROOT/state/armada_state.json')).get('$BOT_NAME',{}).get('mode','OFFLINE'))" 2>/dev/null || echo "OFFLINE")
        if [ "$BOT_MODE" = "OFFLINE" ] || [ "$BOT_MODE" = "STOPPED" ]; then
            continue
        fi
        tg_alert "${BOT_NAME}_down" "🚨 WATCHDOG: ${CORE} CRASHED! (was $BOT_MODE) → restartuji přes SBP"
        # Kill any zombie remnants first
        pkill -9 -f "$CORE" 2>/dev/null; sleep 1
        cd "$ARMADA_ROOT" && python3 -c "from architect.orchestration import start_bot; start_bot('$BOT_NAME')" &
        ALERTS=$((ALERTS + 1))
    fi
done

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

if ! is_alive "armada-core"; then
    tg_alert "armada_core_down" "🚨 WATCHDOG: Armada Core spadl → restartuji"
    cd "$ARMADA_ROOT" && nohup ./target/release/armada-core >> "$LOG_DIR/armada-core.log" 2>&1 &
    ALERTS=$((ALERTS + 1))
fi

if ! is_alive "hydra-dashboard"; then
    tg_alert "dashboard_down" "⚠️ WATCHDOG: Dashboard spadl → restartuji"
    cd "$ARMADA_ROOT" && nohup ./target/release/hydra-dashboard >> "$LOG_DIR/hydra-dashboard.log" 2>&1 &
    ALERTS=$((ALERTS + 1))
fi

if ! is_alive "pnl_daemon"; then
    tg_alert "pnl_down" "⚠️ WATCHDOG: PnL Daemon spadl → restartuji"
    cd "$ARMADA_ROOT" && python3 architect/pnl_daemon.py >> "$LOG_DIR/pnl_daemon.log" 2>&1 &
    ALERTS=$((ALERTS + 1))
fi

if ! is_alive "market_recorder"; then
    tg_alert "recorder_down" "⚠️ WATCHDOG: Market Recorder spadl → restartuji"
    cd "$ARMADA_ROOT" && python3 architect/market_recorder.py >> "$LOG_DIR/market_recorder.log" 2>&1 &
    ALERTS=$((ALERTS + 1))
fi

if ! is_alive "price_bridge"; then
    tg_alert "bridge_down" "⚠️ WATCHDOG: Price Bridge spadl → restartuji"
    cd "$ARMADA_ROOT" && python3 architect/price_bridge.py >> "$LOG_DIR/price_bridge.log" 2>&1 &
    ALERTS=$((ALERTS + 1))
fi

if ! is_alive "ml_shield"; then
    tg_alert "shield_down" "⚠️ WATCHDOG: ML Shield spadl → restartuji"
    cd "$ARMADA_ROOT/architect" && python3 ml_shield.py >> "$LOG_DIR/ml_shield.log" 2>&1 &
    # Ensure Hive Mind flag exists
    mkdir -p /dev/shm/beroun
    [ ! -f /dev/shm/beroun/toxic_storm.bin ] && printf '\x00' > /dev/shm/beroun/toxic_storm.bin
    ALERTS=$((ALERTS + 1))
fi



# ═══ DUPLICATE CLEANUP ═══
# Note: watchdog.sh is NOT checked — cron creates new bash instances each minute, pgrep sees them all
for proc in tg_commander pnl_daemon price_bridge market_recorder sovereign-cortex hydra-core moonshot-core grid-core trigon-core nexus-core armada-core hydra-dashboard; do
    kill_dupes "$proc"
done

# ═══ LOG ═══
if [ "$ALERTS" -gt 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ $ALERTS alerts"
elif [ "$((10#$(date +%M) % 15))" -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ All processes OK"
fi
