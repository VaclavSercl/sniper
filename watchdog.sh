#!/bin/bash
# BEROUN SMART WATCHDOG v5.1
# Monitoring project-local runtime for heartbeat

STATE_FILE="/home/wwwenda/hft-sniper/runtime/engine_state.bin"
LOG_FILE="/home/wwwenda/hft-sniper/logs/watchdog.log"

echo "[$(date)] Watchdog Guardian ONLINE" >> $LOG_FILE

while true; do
    if ! pgrep -f "beroun-core" > /dev/null; then
        echo "[$(date)] ALERT: Sniper core DEAD." >> $LOG_FILE
    fi

    if [ -f "$STATE_FILE" ]; then
        LAST_PRICE=$(od -An -N8 -t u8 "$STATE_FILE" | tr -d ' ')
        sleep 5
        NEW_PRICE=$(od -An -N8 -t u8 "$STATE_FILE" | tr -d ' ')
        
        if [ "$LAST_PRICE" == "$NEW_PRICE" ] && [ "$LAST_PRICE" != "0" ]; then
            echo "[$(date)] WARNING: Stale data detected in project runtime. Restarting..." >> $LOG_FILE
            pkill -f "beroun-core"
        fi
    fi
    sleep 10
done
