#!/bin/bash
# 🛑 SNIPER ARMADA — Clean Shutdown
# Called by: systemd ExecStop or manual ./infra/stop_armada.sh
# Graceful → SIGTERM first (2s wait), then SIGKILL for stragglers

set -euo pipefail

echo "🛑 Stopping Sniper Armada..."

# Save pre-shutdown state
ARMADA_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_FILE="$ARMADA_ROOT/state/armada_state.json"
if [ -f "$STATE_FILE" ]; then
    python3 -c "
import json, datetime
with open('$STATE_FILE', 'r+') as f:
    st = json.load(f)
    st['last_healthy'] = datetime.datetime.now().isoformat()
    f.seek(0); json.dump(st, f, indent=2); f.truncate()
" 2>/dev/null || true
fi

# Phase 1: Graceful SIGTERM
PROCS=(
    "tg_commander"
    "dashboard_server"
    "pnl_daemon"
    "market_recorder"
    "price_bridge"
    "sovereign-cortex"
    "hydra-core"
    "moonshot-core"
    "grid-core"
    "trigon-core"
    "nexus-core"
)

for proc in "${PROCS[@]}"; do
    pkill -f "$proc" 2>/dev/null || true
done

echo "   Waiting 2s for graceful shutdown..."
sleep 2

# Phase 2: Force-kill stragglers
for proc in "${PROCS[@]}"; do
    pkill -9 -f "$proc" 2>/dev/null || true
done

# Phase 3: Remove boot lock
rm -f /tmp/sniper_boot.lock

echo "✅ Armada stopped"
