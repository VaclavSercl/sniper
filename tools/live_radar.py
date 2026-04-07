import mmap
import struct
import time
import os

L2_PATH = '/dev/shm/beroun/l2_command.bin'
ARMADA_PATH = '/dev/shm/beroun/armada_state.bin'

def poll_memory():
    while True:
        os.system('clear')
        print("🐺🔥 APEX CONTROL RADAR | LIVE MONITORING")
        print("="*50)
        
        # Čtení L2 Orákula (Skóre)
        try:
            with open(L2_PATH, 'rb') as f:
                mm_l2 = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                # Offsety podle tvé specifikace (240 = ranging, 248 = trending)
                ranging = struct.unpack('<Q', mm_l2[240:248])[0] / 1e8
                trending = struct.unpack('<Q', mm_l2[248:256])[0] / 1e8
                print(f"[L2 ORACLE] Ranging: {ranging:.2f} | Trending: {trending:.2f}")
                mm_l2.close()
        except FileNotFoundError:
            print("[L2 ORACLE] l2_command.bin not found yet.")

        # Čtení Armady (Alokace USD)
        try:
            with open(ARMADA_PATH, 'rb') as f:
                mm_a = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                # Čtení pole authorized_capital od offsetu 32 (5x u64)
                caps = struct.unpack('<5Q', mm_a[32:72])
                print("\n[MEMPOOL ALLOCATIONS]")
                print(f"Hydra:    ${caps[0]/1e8:,.2f}")
                print(f"Moonshot: ${caps[1]/1e8:,.2f}")
                print(f"Grid:     ${caps[2]/1e8:,.2f}")
                print(f"Trigon:   ${caps[3]/1e8:,.2f}")
                print(f"Nexus:    ${caps[4]/1e8:,.2f}")
                mm_a.close()
        except FileNotFoundError:
            print("\n[MEMPOOL ALLOCATIONS] armada_state.bin not found yet.")
            
        time.sleep(1)

if __name__ == "__main__":
    poll_memory()
