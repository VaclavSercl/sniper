#!/usr/bin/env python3
"""
ZeroClaw Tool: write_risk_param
Writes risk parameters to mmap /dev/shm/beroun/risk_state.bin

Usage: python3 write_risk_param.py <field> <value>

RiskState struct layout (repr(C, align(64))):
  offset  0: paused           (u64, 8 bytes)
  offset  8: _pad_paused      (56 bytes) → cache-line aligned
  offset 64: grid_step         (u64)
  offset 72: grid_size         (u64)
  offset 80: order_usd         (u64)
  offset 88: max_inv_delta     (u64)
  offset 96: bias_offset       (i64, SIGNED)
  offset 104: authorized_capital (u64)
  offset 112: daily_loss_limit   (u64)
"""
import struct
import mmap
import os
import sys
import json

PRICE_SCALE = 1e8

# Field offsets in RiskState struct — CORRECTED for 56-byte cache-line padding
FIELD_MAP = {
    "paused":              (0,   "uint64", lambda v: 1 if v.lower() in ("1", "true", "yes") else 0),
    "grid_step":           (64,  "uint64", lambda v: int(float(v) * PRICE_SCALE)),
    "grid_size":           (72,  "uint64", lambda v: int(float(v))),
    "order_usd":           (80,  "uint64", lambda v: int(float(v) * PRICE_SCALE)),
    "max_inv_delta":       (88,  "uint64", lambda v: int(float(v) * PRICE_SCALE)),
    "bias_offset":         (96,  "int64",  lambda v: int(float(v) * PRICE_SCALE)),
    "authorized_capital":  (104, "uint64", lambda v: int(float(v) * PRICE_SCALE)),
    "daily_loss_limit":    (112, "uint64", lambda v: int(float(v) * PRICE_SCALE)),
}

def main():
    if len(sys.argv) != 3:
        print(json.dumps({"error": "Usage: write_risk_param.py <field> <value>"}))
        return

    field = sys.argv[1]
    raw_value = sys.argv[2]

    if field not in FIELD_MAP:
        print(json.dumps({"error": f"Unknown field: {field}", "valid_fields": list(FIELD_MAP.keys())}))
        return

    offset, dtype, converter = FIELD_MAP[field]
    value = converter(raw_value)

    path = "/dev/shm/beroun/risk_state.bin"
    if not os.path.exists(path):
        print(json.dumps({"error": "Risk state mmap not found", "path": path}))
        return

    fd = os.open(path, os.O_RDWR)
    mm = mmap.mmap(fd, 0, access=mmap.ACCESS_WRITE)

    try:
        fmt = '<q' if dtype == "int64" else '<Q'
        struct.pack_into(fmt, mm, offset, value)
        mm.flush()

        result = {
            "status": "ok",
            "field": field,
            "raw_value": raw_value,
            "atoms": value,
            "offset": offset,
            "human": f"{field} = {raw_value}",
        }
        print(json.dumps(result, indent=2))
    finally:
        mm.close()
        os.close(fd)

if __name__ == "__main__":
    main()
