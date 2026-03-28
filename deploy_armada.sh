#!/bin/bash
# 🐺 SNIPER ARMADA v14.0 — Master Deployment Script
# Manages ALL bots + Cortex + Commander + PnL Engine + Dashboard
# Called by: systemd (sniper-armada.service) or manually
#
# Architecture:
#   Core 0 → Hydra (BTC-USD Delta Lead)
#   Core 1 → Moonshot (Multi-Symbol Flash Crash)
#   Core 2 → Grid (Dynamic Multi-Level Grid)
#   Core 3 → Trigon / OS / AI layer
#
# Watchdog: Background loop monitors all processes. If any critical
#           process dies, it auto-restarts it and sends Telegram alert.
#
# Usage:
#   ./deploy_armada.sh          # Launch with existing binaries
#   ./deploy_armada.sh --build  # Rebuild workspace first

set -euo pipefail

ARMADA_ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ARMADA_ROOT/logs"
BIN_DIR="$ARMADA_ROOT/target/release"
SHM_DIR="/dev/shm/beroun"

mkdir -p "$LOG_DIR" "$SHM_DIR"

# ═══ LOAD .env FOR TELEGRAM ═══
if [ -f "$ARMADA_ROOT/.env" ]; then
    set -a
    source "$ARMADA_ROOT/.env"
    set +a
fi

echo "🐺 ═══════════════════════════════════════════"
echo "   SNIPER ARMADA v14.0 — DEPLOY"
echo "   $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "═══════════════════════════════════════════════"

# ═══ TELEGRAM ALERT FUNCTION ═══
tg_alert() {
    local msg="$1"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -s --max-time 5 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${msg}" \
            -d "parse_mode=Markdown" > /dev/null 2>&1 || true
    fi
}

# ═══ BUILD (if needed) ═══
if [ "${1:-}" == "--build" ]; then
    echo "📦 Building workspace..."
    cd "$ARMADA_ROOT" && cargo build --release --workspace
    [ $? -ne 0 ] && echo "❌ Build failed!" && exit 1
    echo "✅ Build complete"
fi

# ═══ KILL OLD INSTANCES ═══
echo ""
echo "🧹 Cleaning old instances..."
pkill -f tg_commander.py 2>/dev/null || true
pkill -f pnl_daemon.py 2>/dev/null || true
pkill -f sovereign-cortex 2>/dev/null || true
fuser -k 3004/tcp 2>/dev/null || true
sleep 2

# ═══ LAUNCH FUNCTIONS (with logging) ═══
launch_hydra() {
    taskset -c 0 "$BIN_DIR/hydra-core" >> "$LOG_DIR/hydra-core.log" 2>&1 &
    HYDRA_PID=$!
    echo "  🐍 Hydra started (PID $HYDRA_PID)"
}

launch_dashboard() {
    taskset -c 3 "$BIN_DIR/hydra-dashboard" >> "$LOG_DIR/hydra-dashboard.log" 2>&1 &
    DASHBOARD_PID=$!
}


launch_commander() {
    python3 "$ARMADA_ROOT/architect/tg_commander.py" >> "$LOG_DIR/tg_commander.log" 2>&1 &
    COMMANDER_PID=$!
    echo "  📱 Commander started (PID $COMMANDER_PID)"
}

launch_pnl() {
    python3 "$ARMADA_ROOT/architect/pnl_daemon.py" >> "$LOG_DIR/pnl_daemon.log" 2>&1 &
    PNL_PID=$!
    echo "  💰 PnL Daemon started (PID $PNL_PID)"
}

launch_cortex() {
    taskset -c 3 "$BIN_DIR/sovereign-cortex" >> "$LOG_DIR/sovereign-cortex.log" 2>&1 &
    CORTEX_PID=$!
    echo "  🧠 Cortex started (PID $CORTEX_PID)"
}

# ═══ LAUNCH ALL ═══
echo ""
launch_hydra
launch_dashboard
sleep 3
launch_cortex
sleep 2
launch_commander
launch_pnl

echo ""
echo "🐺 ═══════════════════════════════════════════"
echo "   ARMADA v14.0 ONLINE"
echo "   🐍 Hydra:     Core 0 — BTC-USD Delta Lead    :3000 (PID $HYDRA_PID)"
echo "   🧠 Cortex:    Sovereign AI (L1+GPU+UDS)      (PID $CORTEX_PID)"
echo "   📱 Commander: Telegram C2 + Dashboard :3004  (PID $COMMANDER_PID)"
echo "   💰 PnL:       FIFO Engine                     (PID $PNL_PID)"
echo ""
echo "   🌙 Moonshot:  /moonshot start (via Telegram)"
echo "   📐 Grid:      /grid start     (via Telegram)"
echo "   🔺 Trigon:    /trigon start   (via Telegram)"
echo "═══════════════════════════════════════════════"

tg_alert "🐺 *SNIPER ARMADA v14.0 DEPLOYED*
🐍 Hydra PID $HYDRA_PID
🧠 Cortex PID $CORTEX_PID
📱 Commander PID $COMMANDER_PID
💰 PnL PID $PNL_PID
$(date '+%H:%M:%S')"

# ═══════════════════════════════════════════════
# 🛡️ WATCHDOG — Monitor & Auto-Restart
# Checks every 30s, restarts dead processes,
# sends Telegram alert on any restart.
# ═══════════════════════════════════════════════
watchdog() {
    local restart_count=0

    while true; do
        sleep 30

        # ── Commander (CRITICAL — must always run) ──
        if ! kill -0 "$COMMANDER_PID" 2>/dev/null; then
            restart_count=$((restart_count + 1))
            echo "⚠️ [WATCHDOG] Commander DEAD — restarting (#$restart_count)"
            launch_commander
            tg_alert "⚠️ *WATCHDOG RESTART #$restart_count*
📱 Commander crashed and was restarted
New PID: $COMMANDER_PID
$(date '+%H:%M:%S')"
        fi

        # ── Cortex (CRITICAL — AI intelligence) ──
        if ! kill -0 "$CORTEX_PID" 2>/dev/null; then
            restart_count=$((restart_count + 1))
            echo "⚠️ [WATCHDOG] Cortex DEAD — restarting (#$restart_count)"
            launch_cortex
            tg_alert "⚠️ *WATCHDOG RESTART #$restart_count*
🧠 Cortex crashed and was restarted
New PID: $CORTEX_PID
$(date '+%H:%M:%S')"
        fi

        # ── Dashboard ──
        if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
            launch_dashboard
        fi

        # ── Excessive restarts = something fundamentally wrong ──
        if [ "$restart_count" -ge 10 ]; then
            tg_alert "🚨 *WATCHDOG: 10+ restarts!*
Něco je zásadně špatně. Kontrola nutná!"
            restart_count=0
            sleep 300  # Cool down 5 min
        fi
    done
}

# Start watchdog in background
watchdog &
WATCHDOG_PID=$!
echo "🛡️ Watchdog started (PID $WATCHDOG_PID, checking every 30s)"

# Wait for Hydra (foreground process — if it dies, systemd restarts all)
wait $HYDRA_PID
echo "⚠️ Hydra exited — triggering systemd restart"
tg_alert "🚨 *HYDRA CRASHED!*
Systemd auto-restart za 10s..."
