#!/bin/bash
# BEROUN WATCHDOG v10.4 — mmap Heartbeat Monitor
# Checks engine_state.bin heartbeat via mmap. Restarts if stale > 60s.
# Runs alongside in-process 15s watchdog as a secondary safety net.

STATE_FILE="/dev/shm/beroun/engine_state.bin"
LOG_FILE="/home/wwwenda/hft-sniper/logs/watchdog.log"
STALE_THRESHOLD_S=120  # 2 minutes = definitely dead (in-process watchdog handles faster cases)

echo "[$(date)] Watchdog v10.4 (mmap heartbeat monitor) ONLINE" >> $LOG_FILE

while true; do
    if ! pgrep -f "beroun-core" > /dev/null; then
        echo "[$(date)] ALERT: Sniper core not running. systemd should auto-restart." >> $LOG_FILE
    elif [ -f "$STATE_FILE" ]; then
        # Check mmap heartbeat (first 8 bytes = latency_ns = epoch nanoseconds)
        HEARTBEAT_NS=$(od -A n -t u8 -N 8 "$STATE_FILE" 2>/dev/null | tr -d ' ')
        if [ -n "$HEARTBEAT_NS" ] && [ "$HEARTBEAT_NS" -gt 0 ] 2>/dev/null; then
            NOW_NS=$(date +%s%N)
            AGE_S=$(( (NOW_NS - HEARTBEAT_NS) / 1000000000 ))
            if [ "$AGE_S" -gt "$STALE_THRESHOLD_S" ]; then
                echo "[$(date)] CRITICAL: Heartbeat stale ${AGE_S}s > ${STALE_THRESHOLD_S}s threshold!" >> $LOG_FILE
            fi
        fi
    fi
    sleep 30
done
