#!/usr/bin/env python3
"""
ZeroClaw Tool: write_risk_param
Writes risk parameters to mmap /dev/shm/beroun/risk_state.bin

Usage: python3 write_risk_param.py <field> <value>
Fields: grid_step, order_usd, paused, bias_offset
"""
import struct
import mmap
import os
import sys
import json

PRICE_SCALE = 1e8

# Field offsets in RiskState struct (cache-aligned)
FIELD_MAP = {
    "grid_step":    (0,  "uint64", lambda v: int(float(v) * PRICE_SCALE)),
    "order_usd":    (8,  "uint64", lambda v: int(float(v) * PRICE_SCALE)),
    "paused":       (16, "uint64", lambda v: 1 if v.lower() in ("1", "true", "yes") else 0),
    "bias_offset":  (24, "int64",  lambda v: int(float(v) * PRICE_SCALE)),
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
            "human": f"{field} = {raw_value}",
        }
        print(json.dumps(result, indent=2))
    finally:
        mm.close()
        os.close(fd)

if __name__ == "__main__":
    main()
