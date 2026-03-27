#!/bin/bash
# 🐍 HYDRA v11.1 — Delta Lead HFT Bot Startup
# Part of Sniper Armada — Bot #1 (CPU Core 0)

ARMADA_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HYDRA_ROOT="$ARMADA_ROOT/hydra"
BIN_DIR="$ARMADA_ROOT/target/release"

echo "🐍 Starting HYDRA v11.1 (Delta Lead)..."
cd "$ARMADA_ROOT"

# ═══ DEZINSEKCE: Kill old Hydra processes ═══
pkill -9 -f hydra-core 2>/dev/null || true
pkill -9 -f hydra-dashboard 2>/dev/null || true
pkill -9 -f tg_listener 2>/dev/null || true
pkill -9 -f l1_shield 2>/dev/null || true
pkill -9 -f sniper_orchestrator 2>/dev/null || true
pkill -9 -f macro_monitor 2>/dev/null || true
sleep 2
rm -f /tmp/hydra-core.lock /tmp/hydra-dashboard.lock 2>/dev/null || true
echo "-> Dezinsekce complete"

# ═══ IPC: Fresh mmap ═══
mkdir -p /dev/shm/beroun
rm -f /dev/shm/beroun/engine_state.bin /dev/shm/beroun/risk_state.bin
echo "-> mmap cleared (fresh struct layout)"

# ═══ BRAIN: Initialize SQLite ═══
$BIN_DIR/hydra-brain init 2>/dev/null || true
echo "-> 🧠 Hydra Brain initialized"

# 1. Start Hydra Core (L0 — Rust HFT Engine, CPU Core 0)
taskset -c 0 $BIN_DIR/hydra-core &
HYDRA_PID=$!
echo "-> Hydra Core started (PID: $HYDRA_PID, CPU: Core 0)"

# 2. Wait for mmap initialization
sleep 5
if ! kill -0 $HYDRA_PID 2>/dev/null; then
    echo "❌ ERROR: hydra-core crashed during startup!"
    exit 1
fi
echo "-> Engine alive, mmap ready"

# ═══ AUTO-CONFIG: Apply saved parameters ═══
$BIN_DIR/hydra-config set-capital 400 2>/dev/null || true
$BIN_DIR/hydra-config set-loss 20 2>/dev/null || true
$BIN_DIR/hydra-config set-levels 3 2>/dev/null || true
echo "-> Config applied (Capital: \$400, DLL: \$20, Levels: 3)"

# 3. Start Dashboard (HTMX+SSE)
$BIN_DIR/hydra-dashboard &
echo "-> Dashboard started (:3000)"

# 4. Start L1 Shield
python3 $HYDRA_ROOT/scripts/l1_shield.py &
echo "-> L1 Shield started"

# 5. Start Orchestrator (L2 Oracle)
python3 $HYDRA_ROOT/scripts/sniper_orchestrator.py &
echo "-> Orchestrator started (L2, 5min cycle)"

# 6. Start Telegram C2
if [ -f "$ARMADA_ROOT/.env" ]; then
    set -a; source "$ARMADA_ROOT/.env"; set +a
fi
python3 $HYDRA_ROOT/scripts/tg_listener.py &
echo "-> Telegram C2 started"

# 7. Start Macro Intelligence
python3 $HYDRA_ROOT/scripts/macro_monitor.py &
echo "-> Macro Intelligence started (Binance + F&G + RSS)"

echo "✅ HYDRA v11.1 ONLINE — Core 0 locked, Delta Lead active"
wait
