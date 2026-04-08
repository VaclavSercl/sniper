#!/usr/bin/env python3
"""
ZeroClaw Tool: read_engine_state
Reads live engine state from mmap /dev/shm/sniper/engine_state.bin

Output: JSON with prices, PnL, positions, bot status
"""
import struct
import mmap
import os
import json

def main():
    path = "/dev/shm/sniper/engine_state.bin"
    if not os.path.exists(path):
        print(json.dumps({"error": "Engine state mmap not found", "path": path}))
        return

    PRICE_SCALE = 1e8
    fd = os.open(path, os.O_RDONLY)
    mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)

    try:
        # Read key fields from EngineState struct
        # Offsets based on Rust struct layout (64B cache-aligned)
        best_bid = struct.unpack_from('<q', mm, 0)[0]
        best_ask = struct.unpack_from('<q', mm, 8)[0]
        micro_price = struct.unpack_from('<q', mm, 16)[0]
        realized_pnl = struct.unpack_from('<q', mm, 24)[0]
        session_fill_count = struct.unpack_from('<Q', mm, 32)[0]
        l2_imbalance = struct.unpack_from('<q', mm, 40)[0]
        toxic_flow_hits = struct.unpack_from('<Q', mm, 48)[0]
        l1_skew_adjustment = struct.unpack_from('<q', mm, 56)[0]
        net_position_atoms = struct.unpack_from('<q', mm, 64)[0]
        sweep_freeze_until = struct.unpack_from('<Q', mm, 72)[0]
        ai_heartbeat_ms = struct.unpack_from('<Q', mm, 80)[0]

        result = {
            "best_bid": round(best_bid / PRICE_SCALE, 2),
            "best_ask": round(best_ask / PRICE_SCALE, 2),
            "micro_price": round(micro_price / PRICE_SCALE, 2),
            "spread_usd": round((best_ask - best_bid) / PRICE_SCALE, 2),
            "realized_pnl_usd": round(realized_pnl / PRICE_SCALE, 4),
            "session_fills": session_fill_count,
            "obi": round(l2_imbalance / PRICE_SCALE, 4),
            "toxic_hits": toxic_flow_hits,
            "l1_skew_usd": round(l1_skew_adjustment / PRICE_SCALE, 4),
            "net_position_btc": round(net_position_atoms / PRICE_SCALE, 6),
            "ai_heartbeat_ms": ai_heartbeat_ms,
        }
        print(json.dumps(result, indent=2))
    finally:
        mm.close()
        os.close(fd)

if __name__ == "__main__":
    main()
