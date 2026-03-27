#!/bin/bash
# 🐺 SNIPER ARMADA v11.2 — Multi-Bot Heartbeat Watchdog
# Monitors mmap heartbeat for all bots: Hydra, Moonshot, Grid
# Runs alongside in-process watchdogs as secondary safety net.

STALE_THRESHOLD_S=120  # 2 minutes = definitely dead
LOG_DIR="/home/wwwenda/hft-sniper/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/watchdog.log"

echo "[$(date)] Watchdog v11.2 (3-bot heartbeat monitor) ONLINE" >> $LOG_FILE

check_heartbeat() {
    local name="$1"
    local state_file="$2"
    local process="$3"

    if ! pgrep -f "$process" > /dev/null 2>&1; then
        echo "[$(date)] ⚠️ $name: process '$process' not running" >> $LOG_FILE
        return
    fi

    if [ -f "$state_file" ]; then
        local file_age=$(( $(date +%s) - $(stat -c %Y "$state_file") ))
        if [ "$file_age" -gt "$STALE_THRESHOLD_S" ]; then
            echo "[$(date)] 🚨 $name: mmap stale ${file_age}s > ${STALE_THRESHOLD_S}s!" >> $LOG_FILE
        fi
    fi
}

while true; do
    check_heartbeat "Hydra"    "/dev/shm/beroun/engine_state.bin"    "hydra-core"
    check_heartbeat "Moonshot" "/dev/shm/beroun/moonshot_engine.bin" "moonshot-core"
    check_heartbeat "Grid"     "/dev/shm/beroun/grid_engine.bin"     "grid-core"
    sleep 30
done
