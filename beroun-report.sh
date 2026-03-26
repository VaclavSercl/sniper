#!/bin/bash
# BEROUN INTELLIGENT REPORTER v5.1
# (c) 2026 Sovereign HFT Systems

PROJECT_ROOT="/home/wwwenda/hft-sniper"
STATE_FILE="/dev/shm/beroun/engine_state.bin"
source $PROJECT_ROOT/.env

# 1. Collect real-time data from project runtime
if [ -f "$STATE_FILE" ]; then
    RAW_PRICE=$(od -An -N8 -t u8 "$STATE_FILE" | tr -d ' ')
    PRICE=$(echo "scale=2; $RAW_PRICE / 100000000" | bc)
    
    RAW_POS=$(od -An -j 24 -N8 -t d8 "$STATE_FILE" | tr -d ' ')
    POS=$(echo "scale=4; $RAW_POS / 100000000" | bc)
    
    RAW_PNL=$(od -An -j 32 -N8 -t d8 "$STATE_FILE" | tr -d ' ')
    PNL=$(echo "scale=2; $RAW_PNL / 100000000" | bc)
else
    PRICE="N/A"; POS="N/A"; PNL="N/A"
fi

CPU_LOAD=$(uptime | awk -F'load average:' '{ print $2 }' | cut -d, -f1 | sed 's/ //g')
RAM_FREE=$(free -m | awk '/Mem:/ { print $4 }')

PROMPT="Jsi Beroun Sniper AI. Vygeneruj stručný report. 
AKTUÁLNÍ DATA: Cena BTC: $PRICE USD, Čistá pozice: $POS BTC, Realizovaný PnL: $PNL USD. 
SYSTÉM: CPU Load: $CPU_LOAD, Volná RAM: ${RAM_FREE}MB."

REPORT=$(/usr/local/bin/gemini -p "$PROMPT" --yolo 2>/dev/null | grep -vE "YOLO|Keychain|Loaded|Fallback")

HEADER="<b>🐺 BEROUN SOVEREIGN REPORT</b>%0A%0A"
BODY=$(echo "$REPORT" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')
TEXT="${HEADER}${BODY}"

curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
     -d "chat_id=$TELEGRAM_CHAT_ID" \
     -d "text=$TEXT" \
     -d "parse_mode=HTML"
