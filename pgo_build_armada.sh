#!/bin/bash
# ═══════════════════════════════════════════════════════════
# 📊 PGO Build Script for Sniper Armada (ALL bots)
# Profile-Guided Optimization: 3-step process
#   1. Build ALL workspace binaries with profiling counters
#   2. Run under live market load for 60s to generate profiles
#   3. Rebuild with profile data for optimized hot paths
#
# WARNING: This stops live trading for ~3 minutes!
#          Schedule during low-volume hours (UTC 06:00-08:00)
# ═══════════════════════════════════════════════════════════
set -euo pipefail

PROJECT="/home/wwwenda/sniper"
PROFILE_DIR="${PROJECT}/target/pgo-profiles"
ARMADA_SERVICE="sniper-armada"

echo "📊 ═══════════════════════════════════════════"
echo "📊  PGO Build Pipeline for ENTIRE Armada"
echo "📊 ═══════════════════════════════════════════"
echo ""

# Check for llvm-profdata
PROFDATA=$(command -v llvm-profdata 2>/dev/null || command -v llvm-profdata-18 2>/dev/null || command -v llvm-profdata-17 2>/dev/null || echo "")
if [ -z "$PROFDATA" ]; then
    echo "❌ llvm-profdata not found. Install llvm:"
    echo "   sudo apt install llvm"
    exit 1
fi
echo "✅ Found: $PROFDATA"

# ═══ STEP 0: Stop live trading ═══
echo ""
echo "🛑 Step 0/4: Stopping live armada..."
sudo systemctl stop "$ARMADA_SERVICE" 2>/dev/null || true
sleep 3
# Kill any remaining bot processes (including those restored by L2 Oracle)
pkill -9 -f "hydra-core|moonshot-core|grid-core|trigon-core|sovereign-cortex|l2_oracle|tg_commander|pnl_daemon" 2>/dev/null || true
sleep 2
# Remove lock files so instrumented binaries can start
rm -f /tmp/hydra-core.lock /tmp/moonshot-core.lock /tmp/grid-core.lock /tmp/trigon-core.lock 2>/dev/null || true
# Verify nothing is running
if pgrep -f "hydra-core|moonshot-core|grid-core|trigon-core" > /dev/null 2>&1; then
    echo "⚠️  Some processes still alive, force-killing..."
    pkill -9 -f "hydra-core|moonshot-core|grid-core|trigon-core" 2>/dev/null || true
    sleep 2
fi
echo "✅ Armada fully stopped"

# ═══ STEP 1: Instrumented Build ═══
echo ""
echo "🔧 Step 1/4: Building instrumented workspace..."
rm -rf "$PROFILE_DIR"
mkdir -p "$PROFILE_DIR"

RUSTFLAGS="-Cprofile-generate=${PROFILE_DIR}" \
    cargo build --release --workspace --manifest-path="${PROJECT}/Cargo.toml"

echo "✅ Instrumented binaries built"

# ═══ STEP 2: Profiling Run (live market) ═══
echo ""
echo "⏱️  Step 2/4: Running instrumented armada for 60s under live market..."
echo "   (Bots connect to Bitfinex WS, process real orderbook data)"

# Start instrumented bots in background
cd "$PROJECT"
LLVM_PROFILE_FILE="${PROFILE_DIR}/hydra-%p.profraw" \
    ./target/release/hydra-core &
HYDRA_PID=$!

LLVM_PROFILE_FILE="${PROFILE_DIR}/moonshot-%p.profraw" \
    ./target/release/moonshot-core &
MOONSHOT_PID=$!

LLVM_PROFILE_FILE="${PROFILE_DIR}/grid-%p.profraw" \
    ./target/release/grid-core &
GRID_PID=$!

LLVM_PROFILE_FILE="${PROFILE_DIR}/trigon-%p.profraw" \
    ./target/release/trigon-core &
TRIGON_PID=$!

echo "   PIDs: hydra=$HYDRA_PID moonshot=$MOONSHOT_PID grid=$GRID_PID trigon=$TRIGON_PID"

# Let them run for 60 seconds processing real market data
sleep 60

echo "   Stopping instrumented bots..."
kill $HYDRA_PID $MOONSHOT_PID $GRID_PID $TRIGON_PID 2>/dev/null || true
wait $HYDRA_PID $MOONSHOT_PID $GRID_PID $TRIGON_PID 2>/dev/null || true
sleep 2

echo ""
echo "📂 Profile data generated:"
ls -lh "$PROFILE_DIR"/*.profraw 2>/dev/null | head -10 || echo "   (no .profraw files found)"
PROF_COUNT=$(ls "$PROFILE_DIR"/*.profraw 2>/dev/null | wc -l)
echo "   Total profiles: $PROF_COUNT"

if [ "$PROF_COUNT" -eq 0 ]; then
    echo "❌ No profile data generated! Aborting."
    echo "   Restarting normal armada..."
    sudo systemctl start "$ARMADA_SERVICE"
    exit 1
fi

# ═══ STEP 3: Merge profiles ═══
echo ""
echo "🔗 Step 3/4: Merging profile data..."
${PROFDATA} merge -o "${PROFILE_DIR}/merged.profdata" "${PROFILE_DIR}"/*.profraw
echo "✅ Merged: $(ls -lh ${PROFILE_DIR}/merged.profdata | awk '{print $5}')"

# ═══ STEP 4: Optimized Build ═══
echo ""
echo "🚀 Step 4/4: Building PGO-optimized workspace..."

RUSTFLAGS="-Cprofile-use=${PROFILE_DIR}/merged.profdata -Cllvm-args=-pgo-warn-missing-function" \
    cargo build --release --workspace --manifest-path="${PROJECT}/Cargo.toml"

echo ""
echo "✅ PGO BUILD COMPLETE!"
echo ""

# Compare binary sizes
echo "📦 Optimized binaries:"
for bin in hydra-core moonshot-core grid-core trigon-core sovereign-cortex; do
    FINAL="${PROJECT}/target/release/${bin}"
    if [ -f "$FINAL" ]; then
        echo "   $bin: $(ls -lh $FINAL | awk '{print $5}')"
    fi
done

# ═══ STEP 5: Restart armada with PGO binaries ═══
echo ""
echo "🚀 Restarting armada with PGO-optimized binaries..."
sudo systemctl start "$ARMADA_SERVICE"
echo "✅ Armada restarted with PGO optimization!"
echo ""
echo "🎯 Monitor P50/P95/P99 latency improvement via Oracle prompt."
