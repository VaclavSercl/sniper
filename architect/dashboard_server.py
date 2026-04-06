"""
🎛️ Master Dashboard SSE Server — Port 3004
Lightweight SSE (Server-Sent Events) server that proxies Cortex data
to the browser-based Master Dashboard.

Runs as a daemon thread inside Commander.
Zero external dependencies — uses stdlib http.server.
"""

import json
import time
import socket
import struct
import mmap
import threading
import logging
import subprocess
import os
import asyncio
from typing import AsyncGenerator

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

log = logging.getLogger("dashboard_sse")

DASHBOARD_PORT = 3004
SSE_INTERVAL = 0.5  # 500ms between updates

app = FastAPI(title="Sniper Master Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# mmap paths
CROSS_EXCHANGE_PATH = "/dev/shm/beroun/cross_exchange.bin"
ENGINE_STATE_PATH = "/dev/shm/beroun/engine_state.bin"
L2_COMMAND_PATH = "/dev/shm/beroun/l2_command.bin"
PRICE_SCALE = 100_000_000

# Latency ring buffer layout (from l2_command.rs CL6+)
# CL1-CL5 = 5×64 = 320 bytes, then L1TelemetryRing starts
LATENCY_HEAD_OFF = 320         # AtomicUsize (8 bytes) + 56 pad = 64B
LATENCY_RING_OFF = 384         # 64 × AtomicU64 (8 bytes each) = 512B
LATENCY_RING_SIZE = 64
LATENCY_SPARKLINE_MAX = 120    # 120 samples = 1 minute at 500ms interval

# Cross-exchange pair names (match CROSS_EXCHANGE_PAIRS in binance.rs)
PAIR_NAMES = [
    "BTC", "ETH", "XRP", "SOL", "DOGE",
    "ADA", "AVAX", "LTC", "LINK", "DOT",
    "", "", "", "", "", "",
]

# Global reference to CortexClient (set by start_dashboard_server)
_cortex = None


# Global permanent mmaps
_mmap_cross = None
_mmap_engine = None
_mmap_l2 = None

def _init_mmaps():
    """Vytvoří permanentní memory-mapping objektů k zamezení kernel overheadu."""
    global _mmap_cross, _mmap_engine, _mmap_l2
    try:
        if os.path.exists(CROSS_EXCHANGE_PATH) and _mmap_cross is None:
            fd = os.open(CROSS_EXCHANGE_PATH, os.O_RDONLY)
            _mmap_cross = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
            os.close(fd)
    except Exception as e: log.error(f"Cross mmap err: {e}")

    try:
        if os.path.exists(ENGINE_STATE_PATH) and _mmap_engine is None:
            fd = os.open(ENGINE_STATE_PATH, os.O_RDONLY)
            _mmap_engine = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
            os.close(fd)
    except Exception as e: log.error(f"Engine mmap err: {e}")

    try:
        if os.path.exists(L2_COMMAND_PATH) and _mmap_l2 is None:
            fd = os.open(L2_COMMAND_PATH, os.O_RDONLY)
            _mmap_l2 = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
            os.close(fd)
    except Exception as e: log.error(f"L2 mmap err: {e}")


def _build_dashboard_state():
    """Fetch all data from Cortex and build a unified JSON state."""
    if not _cortex:
        return {}

    state = {}

    # 1. Bot snapshot
    try:
        snap = _cortex.get_snapshot()
        if snap.get("ok"):
            state["snapshot"] = snap["data"]
    except Exception:
        state["snapshot"] = {}

    # 2. GPU telemetry
    try:
        gpu = _cortex.get_gpu_stats()
        if gpu.get("ok"):
            state["gpu"] = gpu["data"]
    except Exception:
        state["gpu"] = {}

    # 3. L2 reasoning (file-based, written by L2 Oracle)
    try:
        with open("/dev/shm/beroun/l2_reasoning.txt", "r") as f:
            lines = f.read().strip().split("\n", 1)
            state["l2"] = {
                "regime": lines[0] if lines else "UNKNOWN",
                "reasoning": lines[1] if len(lines) > 1 else "",
            }
    except Exception:
        state["l2"] = {"regime": "UNKNOWN", "reasoning": ""}

    state["timestamp_ms"] = int(time.time() * 1000)

    # 4. System health metrics (cached 2s)
    state["system"] = _get_system_health()

    # 5. Cross-Exchange Intelligence (Phase 5.4/5.5)
    state["cross_exchange"] = _get_cross_exchange_state()

    # 6. ML Shield metrics (Phase 6)
    state["ml_shield"] = _get_ml_shield_state()

    # 7. Latency percentiles + sparkline history
    lat = _get_latency_percentiles()
    state["latency"] = lat
    # Collect history for sparkline (only if we have data)
    if lat.get("p50", 0) > 0:
        _latency_history.append({
            "p50": lat["p50"], "p95": lat["p95"], "p99": lat["p99"],
            "ts": int(time.time() * 1000),
        })
        while len(_latency_history) > LATENCY_SPARKLINE_MAX:
            _latency_history.popleft()
    state["latency_history"] = list(_latency_history)

    # 8. Nexus state (Phase N6)
    state["nexus"] = _get_nexus_state()

    # 9. System Monitor status
    try:
        from system_monitor import get_status as _mon_status
        state["monitor"] = _mon_status()
    except Exception:
        state["monitor"] = {"online": False, "log_size_kb": 0, "events": 0}

    return state


# ═══════════════════════════════════════════════════════════
# Latency Ring Buffer Reader (Phase 4.1 → Phase 7.2 Sparkline)
# ═══════════════════════════════════════════════════════════
from collections import deque
_latency_history = deque(maxlen=LATENCY_SPARKLINE_MAX)

def _get_latency_percentiles():
    """Read latency ring buffer from l2_command.bin mmap and compute percentiles."""
    result = {"p50": 0, "p95": 0, "p99": 0, "samples": 0, "mean": 0}

    try:
        _init_mmaps()
        if _mmap_l2 is None:
            return result

        mm = _mmap_l2

        if mm.size() < LATENCY_RING_OFF + LATENCY_RING_SIZE * 8:
            return result

        # Read ring head
        head = struct.unpack_from('<Q', mm, LATENCY_HEAD_OFF)[0]

        # Read all 64 ring slots
        values = []
        for i in range(LATENCY_RING_SIZE):
            v = struct.unpack_from('<Q', mm, LATENCY_RING_OFF + i * 8)[0]
            if v > 0 and v < 1_000_000:  # Sanity: 0 < v < 1 second in µs
                values.append(v)

        if not values:
            return result

        values.sort()
        n = len(values)
        result["samples"] = n
        result["mean"] = round(sum(values) / n, 1)
        result["p50"] = values[n // 2]
        result["p95"] = values[min(int(n * 0.95), n - 1)]
        result["p99"] = values[min(int(n * 0.99), n - 1)]

    except Exception as e:
        log.debug(f"Latency ring read: {e}")

    return result

# ═══════════════════════════════════════════════════════════
# Cross-Exchange State Reader (Phase 5.4)
# ═══════════════════════════════════════════════════════════
_cross_cache = {}
_cross_cache_ts = 0
_CROSS_TTL = 1.0

def _get_cross_exchange_state():
    """Read cross-exchange mmap and extract pair spreads + risk data."""
    global _cross_cache, _cross_cache_ts
    now = time.time()
    if now - _cross_cache_ts < _CROSS_TTL:
        return _cross_cache

    result = {"pairs": [], "alive": False, "binance": False, "bitfinex": False}

    try:
        _init_mmaps()
        if _mmap_cross is None:
            return result

        mm = _mmap_cross

        # Simplified: read raw pair data
        BBA_SIZE = 64
        PAIR_SIZE = 256  # 2×BBA(128) + metrics block (64) + identity (64) = 256 bytes

        pairs = []
        for i in range(10):  # Max 10 pairs
            pair_off = i * PAIR_SIZE
            if pair_off + PAIR_SIZE > mm.size():
                break

            # Bitfinex BBA
            bfx_bid = struct.unpack_from('<q', mm, pair_off + 0)[0]
            bfx_ask = struct.unpack_from('<q', mm, pair_off + 8)[0]
            bfx_ts = struct.unpack_from('<Q', mm, pair_off + 32)[0]
            bfx_conn = struct.unpack_from('<I', mm, pair_off + 44)[0]

            # Binance BBA
            bnb_off = pair_off + BBA_SIZE
            bnb_bid = struct.unpack_from('<q', mm, bnb_off + 0)[0]
            bnb_ask = struct.unpack_from('<q', mm, bnb_off + 8)[0]
            bnb_ts = struct.unpack_from('<Q', mm, bnb_off + 32)[0]
            bnb_conn = struct.unpack_from('<I', mm, bnb_off + 44)[0]

            # Spread metrics (after both BBAs = pair_off + 128)
            metrics_off = pair_off + 2 * BBA_SIZE
            spread_bfx_bnb = struct.unpack_from('<q', mm, metrics_off + 0)[0]
            spread_bnb_bfx = struct.unpack_from('<q', mm, metrics_off + 8)[0]
            best_spread_bps = struct.unpack_from('<q', mm, metrics_off + 16)[0]
            best_dir = struct.unpack_from('<I', mm, metrics_off + 24)[0]
            arb_signals = struct.unpack_from('<Q', mm, metrics_off + 32)[0]

            # Pair identity
            ident_off = metrics_off + 48
            enabled = struct.unpack_from('<I', mm, ident_off + 28)[0] if ident_off + 32 <= mm.size() else 0

            name = PAIR_NAMES[i] if i < len(PAIR_NAMES) else f"P{i}"

            if bnb_bid > 0 or bfx_bid > 0:
                pairs.append({
                    "name": name,
                    "bfx_bid": round(bfx_bid / PRICE_SCALE, 2),
                    "bfx_ask": round(bfx_ask / PRICE_SCALE, 2),
                    "bnb_bid": round(bnb_bid / PRICE_SCALE, 2),
                    "bnb_ask": round(bnb_ask / PRICE_SCALE, 2),
                    "spread_bps": round(best_spread_bps / 100, 2),
                    "direction": "BFX→BNB" if best_dir == 0 else "BNB→BFX",
                    "arb_signals": arb_signals,
                    "bfx_alive": bfx_conn == 1,
                    "bnb_alive": bnb_conn == 1,
                })

        # Global metadata (after pairs array)
        global_off = 16 * PAIR_SIZE
        if global_off + 80 <= mm.size():
            active = struct.unpack_from('<I', mm, global_off)[0]
            heartbeat = struct.unpack_from('<Q', mm, global_off + 8)[0]
            bfx_alive = struct.unpack_from('<I', mm, global_off + 16)[0]
            bnb_alive = struct.unpack_from('<I', mm, global_off + 20)[0]
            emergency = struct.unpack_from('<I', mm, global_off + 48)[0]
            daily_pnl = struct.unpack_from('<q', mm, global_off + 56)[0]

            result["active_pairs"] = active
            result["bitfinex"] = bfx_alive == 1
            result["binance"] = bnb_alive == 1
            result["emergency_pause"] = emergency == 1
            result["daily_pnl"] = round(daily_pnl / PRICE_SCALE, 4)
            age_ms = int(time.time() * 1000) - heartbeat if heartbeat > 0 else 99999
            result["alive"] = age_ms < 30000

        result["pairs"] = pairs

    except Exception as e:
        log.debug(f"Cross-exchange read: {e}")

    _cross_cache = result
    _cross_cache_ts = now
    return result


# ═══════════════════════════════════════════════════════════
# ML Shield State Reader (Phase 6)
# ═══════════════════════════════════════════════════════════
_ml_cache = {}
_ml_cache_ts = 0
_ML_TTL = 1.0

# Byte offsets in EngineState — AUTO-IMPORTED from Rust struct layout
# Regenerated on every: ./deploy_armada.sh --build
try:
    from l2_rust_offsets import OFF_L1_SKEW, OFF_L1_CONF, OFF_L1_TOXIC, OFF_L1_UPTIME, OFF_AI_BIAS
except ImportError:
    # Fallback: hardcoded values (stale risk — run deploy --build to fix)
    OFF_L1_SKEW = 1584
    OFF_L1_CONF = 1600
    OFF_L1_TOXIC = 1568
    OFF_L1_UPTIME = 1648
    OFF_AI_BIAS = 1512

def _get_nexus_state():
    """Get Nexus bot status for dashboard."""
    result = {
        "online": False, "paper": True,
        "total_signals": 0, "total_trades": 0, "leg_risks": 0,
        "daily_pnl": 0.0, "best_spread_bps": 0.0,
        "last_signal_age_s": 0,
        "bfx_fee_bps": 10, "bnb_fee_bps": 10,
        "min_profit_bps": 5, "cooldown_ms": 5000,
    }

    # Check if nexus-core process is running
    try:
        import subprocess as _sp
        r = _sp.run(["pgrep", "-f", "nexus-core"], capture_output=True, text=True)
        result["online"] = r.returncode == 0
    except Exception:
        pass

    # Read armada_state.json for actual mode (PAPER/LIVE)
    try:
        state_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "armada_state.json")
        if os.path.exists(state_file):
            import json
            with open(state_file) as sf:
                s_data = json.load(sf)
                mode = s_data.get("nexus", {}).get("mode", "OFFLINE")
                result["paper"] = (mode != "LIVE")
    except Exception:
        pass

    # Read live fees from fee_state.bin
    try:
        FEE_PATH = "/dev/shm/beroun/fee_state.bin"
        if os.path.exists(FEE_PATH):
            with open(FEE_PATH, 'rb') as f:
                data = f.read(64)
            if len(data) >= 32:
                maker = struct.unpack_from('<Q', data, 0)[0]
                taker = struct.unpack_from('<Q', data, 8)[0]
                # deriv_maker/taker used for Binance fees
                bnb_maker = struct.unpack_from('<Q', data, 16)[0]
                bnb_taker = struct.unpack_from('<Q', data, 24)[0]
                result["bfx_fee_bps"] = taker / 100.0  # bps×100 → bps
                result["bnb_fee_bps"] = bnb_taker / 100.0 if bnb_taker > 0 else 10
    except Exception:
        pass

    # Read alerts.log for signal/trade count
    try:
        alerts_log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nexus", "logs", "alerts.log")
        trades_log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nexus", "logs", "trades.log")
        if os.path.exists(trades_log):
            with open(trades_log, 'r') as f:
                lines = f.readlines()
            result["total_trades"] = len(lines)
    except Exception:
        pass

    return result

def _get_ml_shield_state():
    """Read ML Shield inference state from engine mmap."""
    global _ml_cache, _ml_cache_ts
    now = time.time()
    if now - _ml_cache_ts < _ML_TTL:
        return _ml_cache

    result = {"online": False, "skew": 0.0, "confidence": 0.0}

    try:
        _init_mmaps()
        if _mmap_engine is None:
            return result

        mm = _mmap_engine

        if mm.size() > OFF_L1_CONF + 8:
            skew_raw = struct.unpack_from('<q', mm, OFF_L1_SKEW)[0]
            conf_raw = struct.unpack_from('<Q', mm, OFF_L1_CONF)[0]
            toxic_hits = struct.unpack_from('<Q', mm, OFF_L1_TOXIC)[0]
            ai_bias = struct.unpack_from('<q', mm, OFF_AI_BIAS)[0]

            result["skew"] = round(skew_raw / PRICE_SCALE, 6)
            result["confidence"] = round(conf_raw / 10000, 4)
            result["toxic_hits"] = toxic_hits
            result["ai_bias"] = round(ai_bias / PRICE_SCALE, 6)
            result["online"] = abs(skew_raw) > 0 or conf_raw > 0

    except Exception as e:
        log.debug(f"ML Shield read: {e}")

    _ml_cache = result
    _ml_cache_ts = now
    return result


# ── System Health Collector (cached) ──
_health_cache = {}
_health_cache_ts = 0
_HEALTH_TTL = 2.0  # seconds
_prev_cpu_idle = 0
_prev_cpu_total = 0

_gpu_cache = {
    'gpu_temp': 0, 'gpu_vram_used': 0, 'gpu_vram_total': 1, 
    'gpu_util': 0, 'gpu_vram_pct': 0.0
}

import pynvml

def _init_nvml():
    try:
        pynvml.nvmlInit()
        return True
    except Exception:
        return False

_nvml_inited = _init_nvml()

async def _gpu_updater_task():
    while True:
        try:
            if _nvml_inited:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
                
                _gpu_cache['gpu_temp'] = temp
                _gpu_cache['gpu_vram_used'] = memory.used // (1024 * 1024)
                _gpu_cache['gpu_vram_total'] = memory.total // (1024 * 1024)
                _gpu_cache['gpu_util'] = util
                _gpu_cache['gpu_vram_pct'] = round(100.0 * memory.used / max(memory.total, 1), 1)
        except Exception:
            pass
        await asyncio.sleep(10.0)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(_gpu_updater_task())


def _get_system_health():
    global _health_cache, _health_cache_ts
    now = time.time()
    if now - _health_cache_ts < _HEALTH_TTL:
        return _health_cache

    result = {}

    # CPU usage from /proc/stat
    global _prev_cpu_idle, _prev_cpu_total
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        parts = line.split()
        idle = int(parts[4])
        total = sum(int(p) for p in parts[1:])
        d_idle = idle - _prev_cpu_idle
        d_total = total - _prev_cpu_total
        _prev_cpu_idle = idle
        _prev_cpu_total = total
        if d_total > 0:
            result['cpu_pct'] = round(100.0 * (1.0 - d_idle / d_total), 1)
        else:
            result['cpu_pct'] = 0.0
    except Exception:
        result['cpu_pct'] = 0.0

    # RAM from /proc/meminfo
    try:
        mem = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':')[:2]
                mem[k.strip()] = int(v.strip().split()[0])
        total_kb = mem.get('MemTotal', 1)
        avail_kb = mem.get('MemAvailable', 0)
        used_kb = total_kb - avail_kb
        result['ram_pct'] = round(100.0 * used_kb / total_kb, 1)
        result['ram_used_gb'] = round(used_kb / 1048576, 1)
        result['ram_total_gb'] = round(total_kb / 1048576, 1)
    except Exception:
        result['ram_pct'] = 0.0

    # GPU from async background 10s cache
    result.update(_gpu_cache)

    # Disk usage (both mounts)
    try:
        for mount, label in [('/', 'disk_root'), ('/data', 'disk_data')]:
            out = subprocess.run(['df', mount, '--output=pcent,avail,size'],
                                 capture_output=True, text=True, timeout=3)
            for line in out.stdout.strip().split('\n')[1:]:
                parts = line.split()
                result[f'{label}_pct'] = int(parts[0].rstrip('%'))
                result[f'{label}_avail_gb'] = round(int(parts[1]) / 1048576, 1)
                result[f'{label}_total_gb'] = round(int(parts[2]) / 1048576, 1)
    except Exception:
        pass

    # Load average
    try:
        load1, load5, load15 = os.getloadavg()
        result['load_1m'] = round(load1, 2)
        result['load_5m'] = round(load5, 2)
        result['load_15m'] = round(load15, 2)
    except Exception:
        pass

    # Uptime
    try:
        with open('/proc/uptime') as f:
            uptime_s = float(f.read().split()[0])
        hours = int(uptime_s // 3600)
        mins = int((uptime_s % 3600) // 60)
        result['uptime'] = f"{hours}h {mins}m"
    except Exception:
        result['uptime'] = '?'

    _health_cache = result
    _health_cache_ts = now
    return result


async def _event_generator() -> AsyncGenerator[dict, None]:
    """Asynchronní yield datových událostí pro SSE klienty, uvolňující GIL."""
    while True:
        state = _build_dashboard_state()
        yield {
            "event": "message",
            "data": json.dumps(state)
        }
        await asyncio.sleep(SSE_INTERVAL)

@app.get("/events")
async def sse_endpoint():
    return EventSourceResponse(_event_generator())

@app.get("/api/state")
async def api_state():
    return _build_dashboard_state()

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def serve_master_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "master_dashboard.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return Response(content="master_dashboard.html not found", status_code=404)

@app.get("/bot/{bot_name}", response_class=HTMLResponse)
async def serve_bot_dashboard(bot_name: str):
    html_path = os.path.join(os.path.dirname(__file__), "bot_dashboard.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return Response(content="bot_dashboard.html not found", status_code=404)


def start_dashboard_server(cortex_client):
    """Start the SSE dashboard server as a daemon thread."""
    global _cortex
    _cortex = cortex_client

    def _run():
        import uvicorn
        log.info(f"🎛️ Master Dashboard SSE server (FastAPI): http://0.0.0.0:{DASHBOARD_PORT}")
        uvicorn.run(app, host="0.0.0.0", port=DASHBOARD_PORT, log_level="warning")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
