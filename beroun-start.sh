#!/bin/bash
# BEROUN SNIPER v8.1 - MASTER STARTUP & ORCHESTRATOR
# (c) 2026 Sovereign HFT Systems

PROJECT_ROOT="/home/wwwenda/hft-sniper"
BIN_DIR="$PROJECT_ROOT/target/release"

echo "🐺 Starting Beroun Sovereign Suite v8.1..."
cd $PROJECT_ROOT

# ═══ VRSTVA 1: Ensure RAM-backed IPC directory exists ═══
mkdir -p /dev/shm/beroun

# 1. Start core Sniper (The Heart)
$BIN_DIR/beroun-core &
SNIPER_PID=$!
echo "-> Sniper started (PID: $SNIPER_PID)"

# 2. Wait for mmap initialization
sleep 2

# 3. Start Dashboard (The Eyes)
$BIN_DIR/beroun-dashboard &
echo "-> Dashboard started"

# 4. Start Sovereign AI (The Brain)
$BIN_DIR/beroun-sovereign-ai &
echo "-> Sovereign AI started"

# 5. Start Telegram Brain (Communication)
$BIN_DIR/beroun-tele-brain &
echo "-> Telegram Brain started"

# 6. Start Watchdog (The Guardian)
$PROJECT_ROOT/watchdog.sh &

echo "✅ All systems operational."
wait
