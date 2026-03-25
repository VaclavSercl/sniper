#!/bin/bash
# BEROUN SNIPER v5.1 - MASTER STARTUP & ORCHESTRATOR
# (c) 2026 Sovereign HFT Systems

PROJECT_ROOT="/home/wwwenda/hft-sniper"
BIN_DIR="$PROJECT_ROOT/target/release"

echo "🐺 Starting Beroun Sovereign Suite..."
cd $PROJECT_ROOT

# Clear old runtime state
rm -f $PROJECT_ROOT/runtime/*.bin

# 1. Start core Sniper (The Heart)
$BIN_DIR/beroun-core &
SNIPER_PID=$!
echo "-> Sniper started (PID: $SNIPER_PID)"

# 2. Wait for mmap initialization
sleep 2

# 3. Start AI Manager (The Eyes)
$BIN_DIR/beroun-ai &
echo "-> AI Manager started"

# 5. Start Sovereign AI (The Brain)
$BIN_DIR/beroun-sovereign-ai &
echo "-> Sovereign AI started"

# 6. Start Telegram Brain (Communication)
$BIN_DIR/beroun-tele-brain &
echo "-> Telegram Brain started"

# 7. Start Watchdog (The Guardian)
$PROJECT_ROOT/watchdog.sh &

echo "✅ All systems operational."
wait
