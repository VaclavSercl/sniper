#!/bin/bash
# 🐺 SNIPER ARMADA — Master Deployment Script
# Manages all bots: Hydra (Core 0), Moonshot (Core 1), Trigon (future)

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

# ═══ SHM INIT ═══
mkdir -p /dev/shm/beroun

# ═══ LAUNCH HYDRA (Bot #1) ═══
echo ""
echo "🐍 Launching Hydra (Core 0 — BTC-USD Market Making)..."
bash "$ARMADA_ROOT/hydra/hydra-start.sh" &
HYDRA_PID=$!

# ═══ LAUNCH MOONSHOT (Bot #2) ═══
echo ""
echo "🌙 Launching Moonshot (Core 1 — Multi-Symbol Flash Crash)..."
bash "$ARMADA_ROOT/moonshot/moonshot-start.sh" &
MOONSHOT_PID=$!

# ═══ LAUNCH TRIGON (Bot #3 — Future) ═══
# Uncomment when Trigon is ready:
# echo ""
# echo "🔺 Launching Trigon (Core 1 — Triangular Arbitrage)..."
# bash "$ARMADA_ROOT/trigon/trigon-start.sh" &
# TRIGON_PID=$!

echo ""
echo "🐺 ═══════════════════════════════════════════"
echo "   ARMADA ONLINE"
echo "   Hydra:    Core 0 — BTC-USD Delta Lead    :3000"
echo "   Moonshot: Core 1 — Multi-Symbol Spike    :3001"
echo "   Trigon:   Core 1 — PENDING (not deployed)"
echo "═══════════════════════════════════════════════"

wait
