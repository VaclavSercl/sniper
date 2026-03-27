#!/bin/bash
# 🌙 MOONSHOT — Bot #2 Start Script
# Sniper Armada · CPU Core 1 · Multi-Symbol Flash Crash
#
# Usage: ./moonshot-start.sh
# Requires: cargo build --release --workspace (from repo root)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN_DIR="$ROOT_DIR/target/release"
LOG_DIR="$SCRIPT_DIR/logs"
ENV_FILE="$ROOT_DIR/.env"

echo "🌙 ═══════════════════════════════════════════"
echo "   MOONSHOT v1.0.0 — Multi-Symbol Flash Crash"
echo "   CPU: Core 1 (taskset -c 1)"
echo "═══════════════════════════════════════════════"

# ═══ CLEANUP ═══
echo "🧹 Cleaning previous processes..."
pkill -9 -f "moonshot-core" 2>/dev/null || true
pkill -9 -f "moonshot-dashboard" 2>/dev/null || true
pkill -9 -f "moonshot-brain" 2>/dev/null || true
pkill -9 -f "moonshot/scripts/l1_shield" 2>/dev/null || true
pkill -9 -f "moonshot/scripts/sniper_orchestrator" 2>/dev/null || true
pkill -9 -f "moonshot/scripts/tg_listener" 2>/dev/null || true
pkill -9 -f "moonshot/scripts/macro_monitor" 2>/dev/null || true
sleep 1

# ═══ DIRECTORIES ═══
mkdir -p "$LOG_DIR"
mkdir -p /dev/shm/beroun

# ═══ ENV ═══
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# ═══ L0: CORE ENGINE (Rust, Core 1) ═══
echo "🚀 Starting L0 Core Engine..."
taskset -c 1 "$BIN_DIR/moonshot-core" \
    >> "$LOG_DIR/core.log" 2>&1 &
echo "   PID: $!"

# ═══ DASHBOARD (Rust, :3001) ═══
echo "📊 Starting Dashboard on :3001..."
"$BIN_DIR/moonshot-dashboard" \
    >> "$LOG_DIR/dashboard.log" 2>&1 &
echo "   PID: $!"

# ═══ BRAIN (Rust, SQLite) ═══
echo "🧠 Starting Brain (SQLite)..."
"$BIN_DIR/moonshot-brain" \
    >> "$LOG_DIR/brain.log" 2>&1 &
echo "   PID: $!"

# ═══ L1: SHIELD (Python) ═══
echo "🛡️ Starting L1 Shield..."
python3 "$SCRIPT_DIR/scripts/l1_shield.py" \
    >> "$LOG_DIR/l1_shield.log" 2>&1 &
echo "   PID: $!"

# ═══ L2: ORCHESTRATOR (Python, AI) ═══
echo "🧠 Starting L2 Orchestrator (AI Pair Selection)..."
python3 "$SCRIPT_DIR/scripts/sniper_orchestrator.py" \
    >> "$LOG_DIR/orchestrator.log" 2>&1 &
echo "   PID: $!"

# ═══ TELEGRAM C2 (Python) ═══
echo "📱 Starting Telegram C2..."
python3 "$SCRIPT_DIR/scripts/tg_listener.py" \
    >> "$LOG_DIR/telegram.log" 2>&1 &
echo "   PID: $!"

# ═══ MACRO MONITOR (Python) ═══
echo "📡 Starting Macro Monitor..."
python3 "$SCRIPT_DIR/scripts/macro_monitor.py" \
    >> "$LOG_DIR/macro.log" 2>&1 &
echo "   PID: $!"

echo ""
echo "🌙 ═══════════════════════════════════════════"
echo "   MOONSHOT ONLINE"
echo "   Dashboard: http://localhost:3001"
echo "   Logs: $LOG_DIR/"
echo "═══════════════════════════════════════════════"

wait
