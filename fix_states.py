import mmap, struct, os
PRICE_SCALE = 1e8

def write_u64(path, offset, val):
    if not os.path.exists(path): return
    with open(path, 'r+b') as f:
        mm = mmap.mmap(f.fileno(), 0)
        mm[offset:offset+8] = struct.pack('<Q', int(val))
        mm.close()

def write_u32(path, offset, val):
    if not os.path.exists(path): return
    with open(path, 'r+b') as f:
        mm = mmap.mmap(f.fileno(), 0)
        mm[offset:offset+4] = struct.pack('<I', int(val))
        mm.close()

# Hydra Risk State (aligned layout requires specific offsets)
# Paused is offset 0
write_u32('/dev/shm/beroun/risk_state.bin', 0, 0) # paused = 0

# Grid Risk State (global_paused is offset 8*8 = 64)
write_u64('/dev/shm/beroun/grid_risk.bin', 64, 0)

# Moonshot Risk State (global_paused is offset 16)
write_u64('/dev/shm/beroun/moonshot_risk.bin', 16, 0)

# Trigon Risk State (global_paused is offset 16)
write_u64('/dev/shm/beroun/trigon_risk.bin', 16, 0)

# Nexus Cross Exchange State (paper_mode is offset 4, emergency_pause is offset 8)
write_u32('/dev/shm/beroun/cross_exchange.bin', 4, 1) # paper=1
write_u32('/dev/shm/beroun/cross_exchange.bin', 8, 0) # paused=0

# Seed L2 Command with 0.5 ranging and 0.5 trending to wake up Kelly limits
with open('/dev/shm/beroun/l2_command.bin', 'r+b') as f:
    mm = mmap.mmap(f.fileno(), 0)
    mm[240:248] = struct.pack('<q', int(0.5 * PRICE_SCALE)) # ranging
    mm[248:256] = struct.pack('<q', int(0.5 * PRICE_SCALE)) # trending
    mm.close()

print("Risk states unpaused to PAPER. L2 Matrix seeded.")
