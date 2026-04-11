#!/bin/bash
# 🐺 SNIPER ARMADA v21.0 — Sovereign Boot Script
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

# ═══ IMMEDIATE BOOT LOCK ═══
# Must be FIRST action — watchdog.sh cron checks this to prevent race conditions.
# Without this at the top, cron can fire between systemd stop/start and spawn duplicates.
echo "$(date +%s)" > /tmp/sniper_boot.lock

ARMADA_ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ARMADA_ROOT/logs"

# Ensure ~/.cargo/bin is in PATH (systemd has minimal PATH without it)
export PATH="$HOME/.cargo/bin:$PATH"
BIN_DIR="$ARMADA_ROOT/target/release"
SHM_DIR="/dev/shm/sniper"
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
echo "   SNIPER ARMADA v21.0 — SOVEREIGN BOOT"
echo "   $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "═══════════════════════════════════════════════"

# ═══ BUILD (if requested) ═══
if [ "${1:-}" == "--build" ]; then
    echo "📦 Building workspace..."
    cd "$ARMADA_ROOT" && cargo build --release --workspace
    echo "🧱 Generating L2 Offsets for Oracle..."
    cargo run --release --bin dump_l2_offsets --features runtime -p sniper-shared
    [ $? -ne 0 ] && echo "❌ Build failed!" && exit 1
    echo "✅ Build complete"
fi

# ═══ KILL ALL OLD INSTANCES ═══
echo ""
echo "🧹 Cleaning ALL old instances..."

# Write boot lockfile (watchdog.sh checks this to avoid duplicates during boot)
echo "$(date +%s)" > /tmp/sniper_boot.lock
# First pass: SIGTERM (graceful)
for proc in tg_commander.py pnl_daemon.py sovereign-cortex hydra-core hydra-dashboard \
            moonshot-core grid-core trigon-core nexus-core armada-core price_bridge.py market_recorder.py zeroclaw; do
    pkill -f "$proc" 2>/dev/null || true
done
fuser -k 3000/tcp 2>/dev/null || true
sleep 2

# Second pass: SIGKILL (force) — catch anything that survived SIGTERM
for proc in tg_commander.py pnl_daemon.py sovereign-cortex hydra-core hydra-dashboard \
            moonshot-core grid-core trigon-core nexus-core armada-core price_bridge.py market_recorder.py zeroclaw; do
    pkill -9 -f "$proc" 2>/dev/null || true
done

# Clean stale PID files from previous run (prevents false crash alerts)
rm -f /tmp/hydra-core.pid /tmp/moonshot-core.pid /tmp/grid-core.pid \
      /tmp/trigon-core.pid /tmp/nexus-core.pid /tmp/hydra-core.lock \
      /tmp/moonshot-core.lock /tmp/grid-core.lock /tmp/trigon-core.lock \
      /tmp/nexus-core.lock 2>/dev/null || true

sleep 2

# ═══ SHOW PRE-CRASH STATE ═══
echo ""
echo "📋 Pre-crash state:"
if [ -f "$STATE_FILE" ]; then
    python3 -c "
import json
with open('$STATE_FILE') as f:
    d = json.load(f)
for bot in ['hydra','moonshot','grid','trigon','nexus']:
    info = d.get(bot, {})
    mode = info.get('mode', 'UNKNOWN') if isinstance(info, dict) else info
    emoji = {'LIVE':'🟢','PAUSED':'🟡','PAPER':'🟠','OFFLINE':'🔴'}.get(mode,'❓')
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

# 0. MMap files — managed by beroun-mmap.service (SystemD Iron Sequence)
#    Verify they exist (safety net for manual runs outside SystemD)
if [ ! -f "/dev/shm/sniper/oracle_state.bin" ]; then
    echo "  [T+0s]  MMap not pre-created by SystemD — running init_mmap.sh as fallback..."
    "$ARMADA_ROOT/infra/init_mmap.sh"
else
    echo "  [T+0s]  ✅ MMap files verified (pre-created by beroun-mmap.service)"
fi

# 1. Cortex — managed by sovereign-cortex.service (SystemD Iron Sequence)
#    Verify it's running (safety net for manual runs outside SystemD)
if pgrep -f sovereign-cortex > /dev/null 2>&1; then
    CORTEX_PID=$(pgrep -f sovereign-cortex | head -1)
    echo "  [T+0s]  ✅ Cortex already running (PID $CORTEX_PID, managed by SystemD)"
else
    echo "  [T+1s]  Starting Cortex (manual mode — not via SystemD)..."
    taskset -c 3 "$BIN_DIR/sovereign-cortex" >> "$LOG_DIR/sovereign-cortex.log" 2>&1 &
    CORTEX_PID=$!
    sleep 5
fi

# 2. Market Recorder & PnL Daemon
echo "  [T+10s] Starting Market Recorder..."
python3 "$ARMADA_ROOT/architect/market_recorder.py" >> "$LOG_DIR/market_recorder.log" 2>&1 &
RECORDER_PID=$!
sleep 2

echo "  [T+12s] Starting PnL Daemon..."
python3 "$ARMADA_ROOT/architect/pnl_daemon.py" >> "$LOG_DIR/pnl_daemon.log" 2>&1 &
PNL_PID=$!
sleep 5

# 3. Dashboard Server
echo "  [T+17s] Starting Dashboard Server..."
python3 "$ARMADA_ROOT/architect/dashboard_server.py" >> "$LOG_DIR/dashboard_server.log" 2>&1 &
DASH_PID=$!
sleep 2

# 4. Price Bridge (cross_exchange.bin for Nexus)
echo "  [T+20s] Starting Price Bridge..."
python3 "$ARMADA_ROOT/architect/price_bridge.py" >> "$LOG_DIR/price_bridge.log" 2>&1 &
BRIDGE_PID=$!
sleep 3

# 5. Commander (Telegram interface)
echo "  [T+23s] Starting Commander..."
python3 "$ARMADA_ROOT/architect/tg_commander.py" >> "$LOG_DIR/tg_commander.log" 2>&1 &
COMMANDER_PID=$!
sleep 2

# 6. ZeroClaw L2 Daemon (replaces l2_oracle.py)
echo "  [T+25s] Starting ZeroClaw L2 Oracle..."
# Safety: strip any stray quotes from GEMINI_API_KEY (.env already source'd at L36)
if [ -n "${GEMINI_API_KEY:-}" ]; then
    export GEMINI_API_KEY=$(echo "$GEMINI_API_KEY" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
fi
ZEROCLAW_BIN=$(command -v zeroclaw 2>/dev/null || echo "")
if [ -n "$ZEROCLAW_BIN" ] && [ -n "${GEMINI_API_KEY:-}" ]; then
    echo "  ✅ ZeroClaw found: $ZEROCLAW_BIN (API key: ${#GEMINI_API_KEY} chars)"
    "$ZEROCLAW_BIN" onboard --api-key "$GEMINI_API_KEY" --provider gemini --model gemini-3.1-pro-preview --quick --force >/dev/null 2>&1
    flock -n /tmp/zeroclaw.lock "$ZEROCLAW_BIN" daemon >> "$LOG_DIR/zeroclaw.log" 2>&1 &
    ZEROCLAW_PID=$!
    ACTUAL_MODEL=$(grep "default_model" ~/.zeroclaw/config.toml 2>/dev/null | head -n 1 | cut -d'"' -f2)
    [ -z "$ACTUAL_MODEL" ] && ACTUAL_MODEL="Gemini"
    echo "  ✅ ZeroClaw L2: PID $ZEROCLAW_PID ($ACTUAL_MODEL)"
else
    ZEROCLAW_PID="N/A"
    [ -z "$ZEROCLAW_BIN" ] && echo "  ⚠️  zeroclaw binary not found in PATH ($PATH)"
    [ -z "${GEMINI_API_KEY:-}" ] && echo "  ⚠️  GEMINI_API_KEY not set"
    echo "  ⚠️  ZeroClaw skipped"
fi

echo ""
echo "🐺 ═════════════════════════════════════════════"
echo "   INFRASTRUCTURE ONLINE (v21.0 — Iron Sequence)"
echo "   🧠 Cortex:    PID $CORTEX_PID (SystemD managed)"
echo "   📈 Recorder:  PID $RECORDER_PID"
echo "   💰 PnL:       PID $PNL_PID"

echo "   🌐 Bridge:    PID $BRIDGE_PID"
echo "   📱 Commander: PID $COMMANDER_PID"
echo ""
echo "   🤖 L2 Oracle will read pre-crash state in ~15s"
echo "   🐍 Trading bots: ALL OFFLINE (Oracle decides)"
echo "═══════════════════════════════════════════════"

tg_alert "🐺 *SOVEREIGN BOOT v21.0*
🧠 Candle L1: Logit Sniping
🐝 ZeroClaw L2: $ACTUAL_MODEL
🐍 Trading bots: ALL OFFLINE
🤖 L2 Oracle deciding in ~15s...
$(date '+%H:%M:%S')"

# ═══ WATCHDOG (Delegated to cron) ═══
# Process monitoring and piecewise restarts are now exclusively handled
# by watchdog.sh (cron) to prevent duplicate process race conditions.

echo "🛡️ Watchdog duties delegated to cron (watchdog.sh)"

# Script exits here — systemd Type=oneshot+RemainAfterExit=yes
# keeps child processes alive in the service cgroup.
exit 0
