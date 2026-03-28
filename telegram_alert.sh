#!/bin/bash
# Called by systemd when sniper-armada.service fails
source /home/wwwenda/sniper/.env 2>/dev/null
[ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ] && \
    curl -s --max-time 10 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=❌ SYSTEMD: ${1:-sniper-armada} CRASHED!
Systemd restart za 10s...
$(date '+%H:%M:%S %Z')" > /dev/null 2>&1
