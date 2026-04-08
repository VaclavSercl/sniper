import mmap, struct, os
if not os.path.exists('/dev/shm/sniper/fee_matrix.bin'):
    print("No fee_matrix.bin")
    exit()
with open('/dev/shm/sniper/fee_matrix.bin', 'rb') as f:
    mm = mmap.mmap(f.fileno(), 512, access=mmap.ACCESS_READ)
    for i in range(4):
        maker, taker, ts, status = struct.unpack('<QQQI', mm[i*64:i*64+28])
        print(f"Venue {i}: Maker {maker/100:.2f} bps, Taker {taker/100:.2f} bps, Status: {status}")
