#!/bin/bash
# 🐺 SNIPER ARMADA — Master Deployment Script
# Manages all bots: Hydra (Core 0), Trigon (Core 1 — future)

ARMADA_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "🐺 ═══════════════════════════════════════════"
echo "   SNIPER ARMADA — DEPLOY"
echo "═══════════════════════════════════════════════"

# ═══ BUILD (if needed) ═══
if [ "$1" == "--build" ]; then
    echo "📦 Building workspace..."
    cd "$ARMADA_ROOT"
    cargo build --release --workspace
    if [ $? -ne 0 ]; then
        echo "❌ Build failed!"
        exit 1
    fi
    echo "✅ Build complete"
fi

# ═══ LAUNCH HYDRA (Bot #1) ═══
echo ""
echo "🐍 Launching Hydra..."
bash "$ARMADA_ROOT/hydra/hydra-start.sh" &
HYDRA_PID=$!

# ═══ LAUNCH TRIGON (Bot #2 — Future) ═══
# Uncomment when Trigon is ready:
# echo ""
# echo "🔺 Launching Trigon..."
# bash "$ARMADA_ROOT/trigon/trigon-start.sh" &
# TRIGON_PID=$!

echo ""
echo "🐺 ═══════════════════════════════════════════"
echo "   ARMADA ONLINE"
echo "   Hydra:  Core 0 — BTC-USD Delta Lead"
echo "   Trigon: Core 1 — PENDING (not deployed)"
echo "═══════════════════════════════════════════════"

wait
