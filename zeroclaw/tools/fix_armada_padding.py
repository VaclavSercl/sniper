import struct
import mmap
import os

# Formát: 
# <   = Little Endian (jako Rust)
# Q   = unsigned long long (8 bajtů, u64) - version
# B   = unsigned char (1 bajt, u8)        - kill_switch
# 7x  = 7 bajtů paddingu                  - (DŮLEŽITÉ!)
# Q   = u64                               - total_equity
# Q   = u64                               - global_var
# 5Q  = 5x u64 array                      - authorized_capital[5]
ARMADA_STRUCT_FORMAT = '< Q B 7x Q Q 5Q'
expected_size = struct.calcsize(ARMADA_STRUCT_FORMAT) # Bude přesně 72 bajtů

print(f"Expected armada state size for packing: {expected_size} bytes")

version = 1
kill_switch = 0
total_equity_scaled = int(10000 * 1e8) # $10,000
global_var = 0

# Zde jsou alokace pro [Hydra, Moonshot, Grid, Trigon, Nexus]
# Dáme všem štědrých $2,000
caps = [
    int(2000 * 1e8),  # Hydra
    int(2000 * 1e8),  # Moonshot
    int(2000 * 1e8),  # Grid
    int(2000 * 1e8),  # Trigon
    int(2000 * 1e8)   # Nexus
]

# Zabalení dat přesně na bajt
packed_data = struct.pack(
    ARMADA_STRUCT_FORMAT,
    version,
    kill_switch,
    total_equity_scaled,
    global_var,
    *caps # Rozbalí pole do 5 samostatných argumentů
)

# Zápis do MMapu
filepath = '/dev/shm/sniper/armada_state.bin'
if os.path.exists(filepath):
    with open(filepath, 'r+b') as f:
        mm = mmap.mmap(f.fileno(), 0)
        mm[0:struct.calcsize(ARMADA_STRUCT_FORMAT)] = packed_data
        mm.close()
    print("MMap updated via Python with correct padding.")

# Kontrolní Sonda
if os.path.exists(filepath):
    with open(filepath, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        unpacked = struct.unpack(ARMADA_STRUCT_FORMAT, mm[0:expected_size])
        
        print(f"Version:     {unpacked[0]}")
        print(f"Kill Switch: {unpacked[1]}")
        print(f"Equity:      ${unpacked[2] / 1e8:,.2f}")
        print(f"Global VAR:  ${unpacked[3] / 1e8:,.2f}")
        print(f"Hydra Cap:   ${unpacked[4] / 1e8:,.2f}")
        print(f"Moon Cap:    ${unpacked[5] / 1e8:,.2f}")
        mm.close()
