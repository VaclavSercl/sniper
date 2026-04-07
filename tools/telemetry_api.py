import mmap
import struct
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import json

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

L2_PATH = '/dev/shm/beroun/l2_command.bin'
ARMADA_PATH = '/dev/shm/beroun/armada_state.bin'
# Cesty k L0 enginům
HYDRA_PATH = '/dev/shm/beroun/engine_state.bin' # Používá engine_state.bin tradičně pro Hydru

def read_mmap_state():
    state = {}
    
    # 1. Čtení Armada Allocations & Orákula
    try:
        with open(L2_PATH, 'rb') as f:
            mm_l2 = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            state['oracle'] = {
                'ranging': struct.unpack('<Q', mm_l2[240:248])[0] / 1e8,
                'trending': struct.unpack('<Q', mm_l2[248:256])[0] / 1e8
            }
            mm_l2.close()
            
        with open(ARMADA_PATH, 'rb') as f:
            mm_a = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            caps = struct.unpack('<5Q', mm_a[32:72])
            state['armada'] = {
                'hydra': caps[0]/1e8, 'moonshot': caps[1]/1e8,
                'grid': caps[2]/1e8, 'trigon': caps[3]/1e8, 'nexus': caps[4]/1e8
            }
            mm_a.close()
            
        # 2. Čtení Hydra L0 State (Inventory Skew a PnL)
        with open(HYDRA_PATH, 'rb') as f:
            mm_h = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            state['hydra_l0'] = {
                'inventory_skew': struct.unpack('<q', mm_h[1408:1416])[0], # net_position
                'realized_pnl': struct.unpack('<q', mm_h[1416:1424])[0] / 1e8, # realized_pnl
                'virtual_pnl': struct.unpack('<q', mm_h[1424:1432])[0] / 1e8
            }
            mm_h.close()

        # 3. Paper Performance - Grid, Moonshot, Trigon, Nexus
        try:
            with open('/dev/shm/beroun/grid_engine.bin', 'rb') as f:
                mm_g = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                grid_vpnl = struct.unpack('<q', mm_g[464:472])[0] / 1e8
                mm_g.close()
            state['hydra_l0']['grid_virtual_pnl'] = grid_vpnl
        except: pass

        try:
            with open('/dev/shm/beroun/moonshot_engine.bin', 'rb') as f:
                mm_m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                moonshot_vpnl = struct.unpack('<q', mm_m[2592:2600])[0] / 1e8
                mm_m.close()
            state['hydra_l0']['moonshot_virtual_pnl'] = moonshot_vpnl
        except: pass

        try:
            with open('/dev/shm/beroun/trigon_engine.bin', 'rb') as f:
                mm_t = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                trigon_vpnl = struct.unpack('<q', mm_t[6192:6200])[0] / 1e8
                mm_t.close()
            state['hydra_l0']['trigon_virtual_pnl'] = trigon_vpnl
        except: pass

        try:
            with open('/dev/shm/beroun/cross_exchange.bin', 'rb') as f:
                mm_n = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                nexus_vpnl = struct.unpack('<q', mm_n[6208:6216])[0] / 1e8
                mm_n.close()
            state['hydra_l0']['nexus_virtual_pnl'] = nexus_vpnl
        except: pass
        
    except Exception as e:
        state['error'] = str(e)
        
    return state

@app.websocket("/ws/panopticon")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = read_mmap_state()
        await websocket.send_json(data)
        await asyncio.sleep(0.1) # 10 FPS refresh rate
