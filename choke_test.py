import mmap
import os

MMAP_FILE = "/dev/shm/beroun/armada_state.bin"

def trigger_kill_switch():
    if not os.path.exists(MMAP_FILE):
        print("MMap soubor neexistuje!")
        return

    # Otevřeme sdílenou paměť pro zápis
    with open(MMAP_FILE, "r+b") as f:
        # Velikost mapování stačí malá, potřebujeme jen prvních pár bajtů
        mm = mmap.mmap(f.fileno(), 192) 
        
        # Struktura ArmadaState v Rustu:
        # offset 0: version (AtomicU64 -> 8 bajtů)
        # offset 8: global_kill_switch (AtomicU8 -> 1 bajt)
        
        mm.seek(8)
        mm.write(b'\x01') # Zápis 1 (TRUE) do Kill-Switche
        
        print("[TACTICAL OVERRIDE] ☢️ GLOBAL KILL-SWITCH AKTIVOVÁN V RAM!")
        mm.close()

if __name__ == "__main__":
    trigger_kill_switch()
