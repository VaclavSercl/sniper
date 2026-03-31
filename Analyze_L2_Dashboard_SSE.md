# Analyze_L2_Dashboard_SSE.md

## 1. Bug Hunting & Security
- **File Descriptor Leak & Mmap Overhead**: Funkce jako `_get_latency_percentiles` a `_get_cross_exchange_state` při každém zhodnocení (až 2x za sekundu per klient) otevírají soubory z `/dev/shm/` a dělají `mmap.mmap()`, načež je hned zavřou `mm.close()`. To vyvolává tisíce zbytečných trapů do kernelu (`mmap` syscall) a neskutečně plýtvá systémovými prostředky. Mmap regiony se přitom chovají jako pointer a měly by být inicializovány a drženy v paměti trvale.
- **DDoS náchylnost (Threading Bottleneck)**: Implementace `ThreadingHTTPServer` pro SSE znamená, že *každé* připojené okno prohlížeče vyžaduje od Pythonu zbrusu nové systémové vlákno. Nekonečný while loop v `_handle_sse` s tvrdým `time.sleep(SSE_INTERVAL)` absolutně paralyzuje GIL a při větším počtu taborů zničí stabilitu celé L2 vrstvy i pro risk modul, protože GIL se bude muset kontext-switchovat nad nečinnými sleep thready.

## 2. Odstranění Zombie
Mrtvé, synchronní a blokující vzory z dob Pythonu 2 smazat:
```python
# Smazat celé blokování pomocí http.server:
from http.server import HTTPServer, BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

# Smazat thready blokující sleep:
while True:
    data = json.dumps(state)
    self.wfile.write(f"data: {data}\n\n".encode())
    time.sleep(SSE_INTERVAL)  # ZOMBIE BLOCKING
```

## 3. Optimalizace podle Vrstvy (L2 Python)
- **Asynchronous IO (FastAPI/Aiohttp)**: Pro SSE endpoint je nutné kompletně přejít na `asyncio` a asynchronní framework (např. `FastAPI` + `sse-starlette` nebo nativní `aiohttp`). Žádné vlákna, jeden čistý Event Loop zvládne obsloužit stovky web socketů asynchronně přes `await asyncio.sleep()`.
- **Persistentní sdílená paměť (Mmap Caching)**: Python musí otevřít hlavičky `ENGINE_STATE_PATH` přes `mmap()` pouze *jednou* při spuštění programu do globální proměnné. V getterech na ně pak jen zavolá `struct.unpack_from()`. Odstraní se tím 99 % IO režie.
- **Asynchronní Subprocesy**: Spouštění `subprocess.run(['nvidia-smi', ...], timeout=3)` je synchronní past. Během 3 vteřin timeoutu by se bot vůbec nemohl vyhodnocovat. `asyncio.create_subprocess_exec` je absolutní nutnost.

## 4. Refaktorovaný kód (Návrh Async/FastAPI přechodu)
```python
import mmap
import asyncio
from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse

app = FastAPI()

# 1. OPTIMALIZACE: Persistentní Mmap
_engine_file = open("/dev/shm/beroun/engine_state.bin", "rb")
_engine_mmap = mmap.mmap(_engine_file.fileno(), 0, access=mmap.ACCESS_READ)

async def event_generator():
    """Asynchronní yield uvolňující GIL na every tick."""
    while True:
        # Příklad: Mmap čtení O(1) z připraveného pointeru bez syscallu
        skew_raw = struct.unpack_from('<q', _engine_mmap, 1584)[0]
        
        state = {
            "skew": skew_raw / 100_000_000.0,
            # další metriky...
        }
        yield {
            "event": "message",
            "data": json.dumps(state)
        }
        await asyncio.sleep(0.5)  # Ne-blokující sleep!

@app.get("/events")
async def sse_endpoint():
    return EventSourceResponse(event_generator())

def start_dashboard_server(cortex_client):
    """Spustí uvicorn v dedikovaném non-blocking módu."""
    import uvicorn
    # GIL-free spuštění uv loopu
    t = threading.Thread(target=uvicorn.run, args=(app,), kwargs={"host":"0.0.0.0", "port":3004})
    t.daemon = True
    t.start()
```
