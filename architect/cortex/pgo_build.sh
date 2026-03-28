#!/bin/bash
# ═══════════════════════════════════════════════════════════
# 📊 PGO Build Script for Sovereign Cortex
# Profile-Guided Optimization: 3-step process
#   1. Build instrumented binary (with profiling counters)
#   2. Run under real workload to generate profile data
#   3. Rebuild with profile data for optimized hot paths
# ═══════════════════════════════════════════════════════════
set -euo pipefail

PROJECT="/home/wwwenda/sniper"
PROFILE_DIR="${PROJECT}/target/pgo-profiles"
BINARY="sovereign-cortex"
CRATE="-p sovereign-cortex"

echo "📊 ═══════════════════════════════════════════"
echo "📊  PGO Build Pipeline for Sovereign Cortex"
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

# ═══ STEP 1: Instrumented Build ═══
echo ""
echo "🔧 Step 1/3: Building instrumented binary..."
rm -rf "$PROFILE_DIR"
mkdir -p "$PROFILE_DIR"

RUSTFLAGS="-Cprofile-generate=${PROFILE_DIR}" \
    cargo build --release ${CRATE} --manifest-path="${PROJECT}/Cargo.toml"

echo "✅ Instrumented binary built"

# ═══ STEP 2: Profiling Run ═══
echo ""
echo "⏱️  Step 2/3: Running instrumented binary for 30s..."
echo "   (dry-run mode — reads mmap, runs L1 loop, no Gemini/Telegram)"

timeout 30 "${PROJECT}/target/release/${BINARY}" --dry-run --no-macro --no-gpu 2>&1 \
    | head -50 || true

echo ""
echo "📂 Profile data generated:"
ls -lh "$PROFILE_DIR"/*.profraw 2>/dev/null | head -5

# Merge profiles
echo ""
echo "🔗 Merging profile data..."
${PROFDATA} merge -o "${PROFILE_DIR}/merged.profdata" "${PROFILE_DIR}"/*.profraw
echo "✅ Merged: $(ls -lh ${PROFILE_DIR}/merged.profdata | awk '{print $5}')"

# ═══ STEP 3: Optimized Build ═══
echo ""
echo "🚀 Step 3/3: Building PGO-optimized binary..."

RUSTFLAGS="-Cprofile-use=${PROFILE_DIR}/merged.profdata -Cllvm-args=-pgo-warn-missing-function" \
    cargo build --release ${CRATE} --manifest-path="${PROJECT}/Cargo.toml"

echo ""
echo "✅ PGO BUILD COMPLETE!"
echo ""

# Compare sizes
FINAL="${PROJECT}/target/release/${BINARY}"
echo "📦 Binary: $(ls -lh ${FINAL} | awk '{print $5}')"
echo "📍 Path:   ${FINAL}"
echo ""
echo "🎯 Deploy with:"
echo "   sudo systemctl restart sniper-armada"
echo "   # or: ./deploy_armada.sh"
