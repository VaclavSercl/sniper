#!/bin/bash
# 🐺 SNIPER ARMADA v13.0 — Master Deployment Script
# Manages ALL bots + Architect + Telegram Commander + PnL Engine
# Called by: systemd (sniper-armada.service) or manually
#
# Architecture:
#   Core 0 → Hydra (BTC-USD Delta Lead)
#   Core 1 → Moonshot (Multi-Symbol Flash Crash)
#   Core 2 → Grid (Dynamic Multi-Level Grid)
#   Core 3 → Trigon / OS / AI layer
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

echo "🐺 ═══════════════════════════════════════════"
echo "   SNIPER ARMADA v13.0 — DEPLOY"
echo "   $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "═══════════════════════════════════════════════"

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
pkill -f sniper_architect.py 2>/dev/null || true
pkill -f pnl_daemon.py 2>/dev/null || true
pkill -f sovereign-cortex 2>/dev/null || true
sleep 2

# ═══ LAUNCH HYDRA (Bot #1 — LIVE) ═══
echo ""
echo "🐍 Launching Hydra (Core 0 — BTC-USD)..."
taskset -c 0 "$BIN_DIR/hydra-core" >> "$LOG_DIR/hydra-core.log" 2>&1 &
HYDRA_PID=$!

taskset -c 3 "$BIN_DIR/hydra-dashboard" >> "$LOG_DIR/hydra-dashboard.log" 2>&1 &

# Wait for Hydra to stabilize
sleep 3

# ═══ LAUNCH ARCHITECT (:3004) ═══
echo "🏛️ Launching Architect Dashboard (:3004)..."
python3 "$ARMADA_ROOT/architect/sniper_architect.py" >> "$LOG_DIR/architect.log" 2>&1 &

# ═══ LAUNCH TELEGRAM COMMANDER ═══
echo "📱 Launching Telegram Commander..."
python3 "$ARMADA_ROOT/architect/tg_commander.py" >> "$LOG_DIR/tg_commander.log" 2>&1 &
COMMANDER_PID=$!

# ═══ LAUNCH PNL DAEMON ═══
echo "💰 Launching PnL Daemon (FIFO engine)..."
python3 "$ARMADA_ROOT/architect/pnl_daemon.py" >> "$LOG_DIR/pnl_daemon.log" 2>&1 &
PNL_PID=$!

# ═══ LAUNCH SOVEREIGN CORTEX ═══
echo "🧠 Launching Sovereign Cortex (L2 Oracle)..."
taskset -c 3 "$BIN_DIR/sovereign-cortex" >> "$LOG_DIR/sovereign-cortex.log" 2>&1 &
CORTEX_PID=$!

echo ""
echo "🐺 ═══════════════════════════════════════════"
echo "   ARMADA ONLINE"
echo "   🐍 Hydra:     Core 0 — BTC-USD Delta Lead    :3000 (PID $HYDRA_PID)"
echo "   🏛️ Architect:  Dashboard                      :3004"
echo "   📱 Commander: Telegram C2                     (PID $COMMANDER_PID)"
echo "   💰 PnL:       FIFO Engine                     (PID $PNL_PID)"
echo "   🧠 Cortex:    Sovereign Oracle L2             (PID $CORTEX_PID)"
echo ""
echo "   🌙 Moonshot:  /moonshot start (via Telegram)"
echo "   📐 Grid:      /grid start     (via Telegram)"
echo "   🔺 Trigon:    /trigon start   (via Telegram)"
echo "═══════════════════════════════════════════════"

# Wait for Hydra (foreground process for systemd)
wait $HYDRA_PID
