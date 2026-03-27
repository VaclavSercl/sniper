#!/bin/bash
# 🐺 BEROUN SNIPER v10.4 "Neural Cross" — MASTER STARTUP
# Sovereign HFT Systems | SQLite Permanent Memory | Zero-JS Dashboard

PROJECT_ROOT="/home/wwwenda/hft-sniper"
BIN_DIR="$PROJECT_ROOT/target/release"

echo "🐺 Starting SNIPER v10.4 (Neural Cross)..."
cd $PROJECT_ROOT

# ═══ DEZINSEKCE: Kill ALL old processes ═══
pkill -9 -f beroun-core 2>/dev/null || true
pkill -9 -f beroun-dashboard 2>/dev/null || true
pkill -9 -f tg_listener 2>/dev/null || true
pkill -9 -f l1_shield 2>/dev/null || true
pkill -9 -f sniper_orchestrator 2>/dev/null || true
pkill -9 -f watchdog.sh 2>/dev/null || true
rm -f /tmp/beroun.lock 2>/dev/null || true
sleep 2
echo "-> Dezinsekce complete"

# ═══ IPC: Ensure RAM-backed mmap directory ═══
mkdir -p /dev/shm/beroun

# ═══ BRAIN: Initialize SQLite permanent memory ═══
$BIN_DIR/beroun-brain init 2>/dev/null || true
echo "-> 🧠 Sniper Brain initialized"

# 1. Start core Sniper (L0 — HFT Engine)
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

# 3. Start Dashboard (HTMX+SSE, Zero JS)
$BIN_DIR/beroun-dashboard &
echo "-> Dashboard started"

# 4. Start L1 Shield (Tactical AI — Kinetic Shield)
python3 $PROJECT_ROOT/scripts/l1_shield.py &
echo "-> L1 Shield started (Kinetic Shield)"

# 5. Start Sniper Orchestrator (L2 — Sovereign Oracle + Brain)
python3 $PROJECT_ROOT/scripts/sniper_orchestrator.py &
echo "-> Sniper Orchestrator started (L2 Oracle + SQLite Brain, 5min cycle)"

# 6. Start Telegram Command Center
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
python3 $PROJECT_ROOT/scripts/tg_listener.py &
echo "-> Telegram Command Center started (SNIPER v10.4)"

# 7. Start Watchdog (The Guardian)
if [ -f "$PROJECT_ROOT/watchdog.sh" ]; then
    $PROJECT_ROOT/watchdog.sh &
    echo "-> Watchdog started"
fi

echo "✅ All systems operational — SNIPER v10.4 (Neural Cross)"
wait
