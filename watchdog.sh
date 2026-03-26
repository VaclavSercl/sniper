#!/bin/bash
# BEROUN WATCHDOG v5.3 — In-process watchdog replaced this script.
# The bot now has built-in 15s timeout detection on both WebSocket
# connections, error channel between tasks, and automatic reconnect
# with order book zeroing.
#
# This script is DEPRECATED and kept only for backward compatibility.
# The in-process watchdog (tokio::time::timeout + err_tx channel)
# provides sub-second failure detection, compared to this script's
# 20-second polling interval.
#
# To monitor the bot externally, use:
#   journalctl --user -u beroun-sniper -f
#   journalctl --user -u beroun-sniper | jq 'select(.fields.event | startswith("watchdog"))'

STATE_FILE="/home/wwwenda/hft-sniper/runtime/engine_state.bin"
LOG_FILE="/home/wwwenda/hft-sniper/logs/watchdog.log"

echo "[$(date)] External Watchdog v5.3 (backup monitor) ONLINE" >> $LOG_FILE

while true; do
    if ! pgrep -f "beroun-core" > /dev/null; then
        echo "[$(date)] ALERT: Sniper core not running. systemd should auto-restart." >> $LOG_FILE
    fi
    sleep 30
done
