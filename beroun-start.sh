#!/bin/bash
# BEROUN SNIPER v10.0 APEX PREDATOR - MASTER STARTUP & ORCHESTRATOR
# (c) 2026 Sovereign HFT Systems

PROJECT_ROOT="/home/wwwenda/hft-sniper"
BIN_DIR="$PROJECT_ROOT/target/release"

echo "🐺 Starting Beroun Sovereign Suite v10.0 Apex Predator..."
cd $PROJECT_ROOT

# ═══ DEZINSEKCE: Kill ALL old processes first ═══
pkill -9 -f beroun-core 2>/dev/null || true
pkill -9 -f beroun-dashboard 2>/dev/null || true
pkill -9 -f tg_listener 2>/dev/null || true
pkill -9 -f l1_shield 2>/dev/null || true
pkill -9 -f watchdog.sh 2>/dev/null || true
rm -f /tmp/beroun.lock 2>/dev/null || true
sleep 2
echo "-> Dezinsekce complete"

# ═══ VRSTVA 1: Ensure RAM-backed IPC directory exists ═══
mkdir -p /dev/shm/beroun

# 1. Start core Sniper (The Heart)
$BIN_DIR/beroun-core &
SNIPER_PID=$!
echo "-> Sniper started (PID: $SNIPER_PID)"

# 2. Wait for mmap initialization
sleep 3

# ═══ AUTO-CONFIG: Apply saved parameters to fresh mmap ═══
$BIN_DIR/beroun-config set-capital 400 2>/dev/null || true
$BIN_DIR/beroun-config set-loss 20 2>/dev/null || true
$BIN_DIR/beroun-config set-levels 3 2>/dev/null || true
echo "-> Config applied (Capital: $400, DLL: $20, Levels: 3)"

# 3. Start Dashboard (The Eyes)
$BIN_DIR/beroun-dashboard &
echo "-> Dashboard started"

# 4. Start L1 Shield (Tactical AI)
python3 $PROJECT_ROOT/scripts/l1_shield.py &
echo "-> L1 Shield started"

# 5. Start Telegram Listener (Python C2)
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
python3 $PROJECT_ROOT/scripts/tg_listener.py &
echo "-> Telegram C2 started"

# 6. Start Watchdog (The Guardian — backup monitor)
if [ -f "$PROJECT_ROOT/watchdog.sh" ]; then
    $PROJECT_ROOT/watchdog.sh &
    echo "-> Watchdog started"
fi

echo "✅ All systems operational — v10.0 Apex Predator"
wait
