import mmap
import struct
import sys

L2_PATH = '/dev/shm/sniper/l2_command.bin'
STORM_PATH = '/dev/shm/sniper/toxic_storm.bin'

def inject_chaos():
    print("🔥 INITIATING TOXIC STORM INJECTION 🔥")
    
    # 1. Inject Trending Score = 0.99 (99,000,000)
    try:
        with open(L2_PATH, 'r+b') as f:
            mm = mmap.mmap(f.fileno(), 0)
            # Offset 248 for trending score
            mm[248:256] = struct.pack('<q', int(0.99 * 1e8))
            mm.close()
        print("✅ L2 Matrix: Trending Score forced to 0.99")
    except Exception as e:
        print(f"❌ Error writing to L2 Matrix: {e}")

    # 2. Inject Toxic Storm = 1
    try:
        with open(STORM_PATH, 'r+b') as f:
            mm = mmap.mmap(f.fileno(), 0)
            mm[0:1] = b'\x01'
            mm.close()
        print("✅ Hive Mind: Toxic Storm forced to 1")
    except Exception as e:
        print(f"❌ Error writing to Toxic Storm: {e}")

def clear_chaos():
    print("🌤️ CLEARING TOXIC STORM 🌤️")
    try:
        with open(L2_PATH, 'r+b') as f:
            mm = mmap.mmap(f.fileno(), 0)
            mm[248:256] = struct.pack('<q', 0)
            mm.close()
        print("✅ L2 Matrix: Trending Score reset to 0.00")
    except Exception as e:
        pass

    try:
        with open(STORM_PATH, 'r+b') as f:
            mm = mmap.mmap(f.fileno(), 0)
            mm[0:1] = b'\x00'
            mm.close()
        print("✅ Hive Mind: Toxic Storm reset to 0")
    except Exception as e:
        pass

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "clear":
        clear_chaos()
    else:
        inject_chaos()
        print("\nRun 'python3 tools/toxic_storm_injector.py clear' to stop the storm.")
