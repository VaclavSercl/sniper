#!/bin/bash
set -e

echo "🐺 BEROUN SNIPER - PGO OPTIMIZATION START"

# 1. CLEAN
cargo clean

# 2. INSTRUMENTED BUILD
RUSTFLAGS="-Cprofile-generate=/tmp/pgo-data" cargo build --release

# 3. DATA COLLECTION (Run for 30 seconds)
echo "Collecting market data patterns (30s)..."
./target/release/beroun-core &
SNIPER_PID=$!
sleep 30
kill $SNIPER_PID

# 4. FINAL PGO BUILD
echo "Compiling final Sovereign binary..."
RUSTFLAGS="-Cprofile-use=/tmp/pgo-data" cargo build --release

echo "✅ Optimization Complete! Run with: ./target/release/beroun-core"
