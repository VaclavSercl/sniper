#!/bin/bash
# 🐺 SNIPER ARMADA v15.0 — Sovereign Boot Script
# Called by: systemd (sniper-armada.service) or manually
#
# Architecture v15: ALL components start OFFLINE after reboot.
# deploy_armada.sh brings up infrastructure in sequence.
# L2 Oracle (inside Commander) reads saved state and brings up trading bots.
#
# Boot sequence:
#   T+0s:   Cortex (Sentinel + GPU + UDS)
#   T+10s:  PnL Daemon
#   T+15s:  Dashboard
#   T+20s:  Commander (includes L2 Oracle thread)
#   T+35s:  L2 Oracle cycle #1 → reads state → starts trading bots
#
# Usage:
#   ./deploy_armada.sh          # Normal boot
#   ./deploy_armada.sh --build  # Rebuild workspace first

set -euo pipefail

ARMADA_ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ARMADA_ROOT/logs"
BIN_DIR="$ARMADA_ROOT/target/release"
SHM_DIR="/dev/shm/beroun"
STATE_FILE="$ARMADA_ROOT/state/armada_state.json"

mkdir -p "$LOG_DIR" "$SHM_DIR" "$ARMADA_ROOT/state"

# ═══ LOAD .env ═══
if [ -f "$ARMADA_ROOT/.env" ]; then
    set -a
    source "$ARMADA_ROOT/.env"
    set +a
fi

# ═══ TELEGRAM ALERT ═══
tg_alert() {
    local msg="$1"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -s --max-time 5 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${msg}" \
            -d "parse_mode=Markdown" > /dev/null 2>&1 || true
    fi
}

echo "🐺 ═══════════════════════════════════════════"
echo "   SNIPER ARMADA v15.0 — SOVEREIGN BOOT"
echo "   $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "═══════════════════════════════════════════════"

# ═══ BUILD (if requested) ═══
if [ "${1:-}" == "--build" ]; then
    echo "📦 Building workspace..."
    cd "$ARMADA_ROOT" && cargo build --release --workspace
    [ $? -ne 0 ] && echo "❌ Build failed!" && exit 1
    echo "✅ Build complete"
fi

# ═══ KILL ALL OLD INSTANCES ═══
echo ""
echo "🧹 Cleaning ALL old instances..."
pkill -f tg_commander.py 2>/dev/null || true
pkill -f pnl_daemon.py 2>/dev/null || true
pkill -f sovereign-cortex 2>/dev/null || true
pkill -f hydra-core 2>/dev/null || true
pkill -f moonshot-core 2>/dev/null || true
pkill -f grid-core 2>/dev/null || true
pkill -f trigon-core 2>/dev/null || true
fuser -k 3004/tcp 2>/dev/null || true
sleep 3

# ═══ SHOW PRE-CRASH STATE ═══
echo ""
echo "📋 Pre-crash state:"
if [ -f "$STATE_FILE" ]; then
    python3 -c "
import json
with open('$STATE_FILE') as f:
    d = json.load(f)
for bot in ['hydra','moonshot','grid','trigon']:
    info = d.get(bot, {})
    mode = info.get('mode', 'UNKNOWN') if isinstance(info, dict) else info
    emoji = {'LIVE':'🟢','PAUSED':'🟡','OFFLINE':'🔴'}.get(mode,'❓')
    print(f'  {emoji} {bot.upper():10s} → {mode}')
last = d.get('last_healthy_ts', '?')
print(f'  Last healthy: {last}')
"
else
    echo "  ⚠️ No state file found — first boot?"
fi

# ═══ SEQUENTIAL INFRASTRUCTURE BOOT ═══
echo ""
echo "🧠 Starting infrastructure (trading bots stay OFFLINE)..."

# 1. Cortex (Sentinel + GPU + UDS server)
echo "  [T+0s]  Starting Cortex..."
taskset -c 3 "$BIN_DIR/sovereign-cortex" >> "$LOG_DIR/sovereign-cortex.log" 2>&1 &
CORTEX_PID=$!
sleep 10

# 2. PnL Daemon
echo "  [T+10s] Starting PnL Daemon..."
python3 "$ARMADA_ROOT/architect/pnl_daemon.py" >> "$LOG_DIR/pnl_daemon.log" 2>&1 &
PNL_PID=$!
sleep 5

# 3. Dashboard
echo "  [T+15s] Starting Dashboard..."
taskset -c 3 "$BIN_DIR/hydra-dashboard" >> "$LOG_DIR/hydra-dashboard.log" 2>&1 &
DASHBOARD_PID=$!
sleep 5

# 4. Commander (includes L2 Oracle as daemon thread)
echo "  [T+20s] Starting Commander + L2 Oracle..."
python3 "$ARMADA_ROOT/architect/tg_commander.py" >> "$LOG_DIR/tg_commander.log" 2>&1 &
COMMANDER_PID=$!

echo ""
echo "🐺 ═══════════════════════════════════════════"
echo "   INFRASTRUCTURE ONLINE"
echo "   🧠 Cortex:    PID $CORTEX_PID"
echo "   💰 PnL:       PID $PNL_PID"
echo "   📊 Dashboard: PID $DASHBOARD_PID"
echo "   📱 Commander: PID $COMMANDER_PID"
echo ""
echo "   🤖 L2 Oracle will read pre-crash state in ~15s"
echo "   🐍 Trading bots: ALL OFFLINE (Oracle decides)"
echo "═══════════════════════════════════════════════"

tg_alert "🐺 *SOVEREIGN BOOT v15.0*
🧠 Infrastructure ONLINE
🐍 Trading bots: ALL OFFLINE
🤖 L2 Oracle deciding in ~15s...
$(date '+%H:%M:%S')"

# ═══ WATCHDOG ═══
watchdog() {
    local restart_count=0
    while true; do
        sleep 30

        # Commander is CRITICAL (contains L2 Oracle)
        if ! kill -0 "$COMMANDER_PID" 2>/dev/null; then
            restart_count=$((restart_count + 1))
            echo "⚠️ [WATCHDOG] Commander+Oracle DEAD — restarting (#$restart_count)"
            python3 "$ARMADA_ROOT/architect/tg_commander.py" >> "$LOG_DIR/tg_commander.log" 2>&1 &
            COMMANDER_PID=$!
            tg_alert "⚠️ *WATCHDOG #$restart_count*
📱 Commander+Oracle crashed → restarted
PID: $COMMANDER_PID"
        fi

        # Cortex
        if ! kill -0 "$CORTEX_PID" 2>/dev/null; then
            restart_count=$((restart_count + 1))
            echo "⚠️ [WATCHDOG] Cortex DEAD — restarting (#$restart_count)"
            taskset -c 3 "$BIN_DIR/sovereign-cortex" >> "$LOG_DIR/sovereign-cortex.log" 2>&1 &
            CORTEX_PID=$!
            tg_alert "⚠️ *WATCHDOG #$restart_count* 🧠 Cortex restarted PID: $CORTEX_PID"
        fi

        # Dashboard
        if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
            taskset -c 3 "$BIN_DIR/hydra-dashboard" >> "$LOG_DIR/hydra-dashboard.log" 2>&1 &
            DASHBOARD_PID=$!
        fi

        # Excessive restarts
        if [ "$restart_count" -ge 10 ]; then
            tg_alert "🚨 *WATCHDOG: 10+ restarts!* Kontrola nutná!"
            restart_count=0
            sleep 300
        fi
    done
}

watchdog &
WATCHDOG_PID=$!
echo "🛡️ Watchdog started (PID $WATCHDOG_PID)"

# Wait for Commander (foreground — if it dies, watchdog restarts it)
# But we keep the script alive for systemd
wait $COMMANDER_PID
echo "⚠️ Commander exited — watchdog will restart"
# Keep alive for systemdog
wait
