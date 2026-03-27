#!/bin/bash
# 📐 GRID — Bot #3 Start Script
# Sniper Armada · CPU Core 2 (shared) · Dynamic Multi-Level Grid
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN_DIR="$ROOT_DIR/target/release"
LOG_DIR="$SCRIPT_DIR/logs"
ENV_FILE="$ROOT_DIR/.env"

echo "📐 ═══════════════════════════════════════════"
echo "   GRID v1.0.0 — Dynamic Multi-Level BTC-USD"
echo "═══════════════════════════════════════════════"

pkill -9 -f "grid-core" 2>/dev/null || true
pkill -9 -f "grid-dashboard" 2>/dev/null || true
pkill -9 -f "grid-brain" 2>/dev/null || true
pkill -9 -f "grid/scripts/" 2>/dev/null || true
sleep 1

mkdir -p "$LOG_DIR" /dev/shm/beroun
[ -f "$ENV_FILE" ] && { set -a; source "$ENV_FILE"; set +a; }

echo "🚀 Starting L0 Core Engine..."
"$BIN_DIR/grid-core" >> "$LOG_DIR/core.log" 2>&1 &
echo "   PID: $!"

echo "📊 Starting Dashboard on :3002..."
"$BIN_DIR/grid-dashboard" >> "$LOG_DIR/dashboard.log" 2>&1 &

echo "🧠 Starting Brain..."
"$BIN_DIR/grid-brain" >> "$LOG_DIR/brain.log" 2>&1 &

echo "🛡️ Starting L1 Shield..."
python3 "$SCRIPT_DIR/scripts/l1_shield.py" >> "$LOG_DIR/l1_shield.log" 2>&1 &

echo "🧠 Starting L2 Orchestrator..."
python3 "$SCRIPT_DIR/scripts/sniper_orchestrator.py" >> "$LOG_DIR/orchestrator.log" 2>&1 &

echo "📱 Starting Telegram C2..."
python3 "$SCRIPT_DIR/scripts/tg_listener.py" >> "$LOG_DIR/telegram.log" 2>&1 &

echo "📡 Starting Macro Monitor..."
python3 "$SCRIPT_DIR/scripts/macro_monitor.py" >> "$LOG_DIR/macro.log" 2>&1 &

echo ""
echo "📐 GRID ONLINE | Dashboard: http://localhost:3002"
wait
