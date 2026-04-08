#!/bin/bash
# 🧠 Beroun MMap Allocator — Pre-creates ALL shared memory structures
# Called by: beroun-mmap.service (Type=oneshot)
# This MUST complete before any Rust binary starts.

set -euo pipefail

SHM_DIR="/dev/shm/beroun"
mkdir -p "$SHM_DIR"

echo "[MMAP] Pre-creating shared memory structures in $SHM_DIR..."

# Standard 4096-byte mmap files
for mfile in engine_state.bin risk_state.bin moonshot_engine.bin moonshot_risk.bin \
             grid_engine.bin grid_risk.bin trigon_engine.bin trigon_risk.bin \
             l2_command.bin cross_exchange.bin pnl_state.bin fee_state.bin \
             state.json armada_state.bin fee_matrix.bin; do
    [ ! -f "$SHM_DIR/$mfile" ] && dd if=/dev/zero of="$SHM_DIR/$mfile" bs=4096 count=1 2>/dev/null
done

# Oracle state (64 bytes = cache-line aligned OracleState struct)
# CRITICAL: armada-core panics without this file
[ ! -f "$SHM_DIR/oracle_state.bin" ] && dd if=/dev/zero of="$SHM_DIR/oracle_state.bin" bs=64 count=1 2>/dev/null

# Toxic storm flag (1 byte)
[ ! -f "$SHM_DIR/toxic_storm.bin" ] && printf '\x00' > "$SHM_DIR/toxic_storm.bin"

# Oracle params (24 bytes)
[ ! -f "$SHM_DIR/oracle_params.bin" ] && dd if=/dev/zero of="$SHM_DIR/oracle_params.bin" bs=24 count=1 2>/dev/null

# ML Weights (192 bytes = cache-line aligned MlWeightsState struct)
# CRITICAL: hydra-core panics without this file (load_ml_weights_ro)
# Zero-init is safe — MlShield reads version=0 and skips hot-swap until Python injects real weights
[ ! -f "$SHM_DIR/ml_weights.bin" ] && dd if=/dev/zero of="$SHM_DIR/ml_weights.bin" bs=192 count=1 2>/dev/null

echo "[MMAP] ✅ All $(ls "$SHM_DIR" | wc -l) structures ready in $SHM_DIR"
ls -la "$SHM_DIR"
