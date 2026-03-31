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
from http.server import HTTPServer, BaseHTTPRequestHandler

log = logging.getLogger("dashboard_sse")

DASHBOARD_PORT = 3004
SSE_INTERVAL = 0.5  # 500ms between updates

# mmap paths
CROSS_EXCHANGE_PATH = "/dev/shm/beroun/cross_exchange.bin"
ENGINE_STATE_PATH = "/dev/shm/beroun/engine_state.bin"
PRICE_SCALE = 100_000_000

# Cross-exchange pair names (match CROSS_EXCHANGE_PAIRS in binance.rs)
PAIR_NAMES = [
    "BTC", "ETH", "XRP", "SOL", "DOGE",
    "ADA", "AVAX", "LTC", "LINK", "DOT",
    "", "", "", "", "", "",
]

# Global reference to CortexClient (set by start_dashboard_server)
_cortex = None


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

    return state


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
        if not os.path.exists(CROSS_EXCHANGE_PATH):
            return result

        with open(CROSS_EXCHANGE_PATH, 'rb') as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

            # ExchangeBBA = 64 bytes: i64 bid, i64 ask, i64 bid_vol, i64 ask_vol, u64 ts, u32 exch_id, u32 connected, 16 pad
            # CrossPairState = 2×ExchangeBBA(128) + arb metrics(56) + pair identity(32) = ~216 bytes per pair
            # But repr(C, align(64)) means each ExchangeBBA = 64B, so CrossPairState is bigger
            # ExchangeBBA: 5×8 + 2×4 + 16 = 64 bytes (perfect)
            # CrossPairState: 2×64(BBA) + i64+i64+i64+u32+u64+u64+u64+u64+u32+u32 = 128 + 72 = ~256 bytes (aligned)
            # With align(64): ceil to next 64 multiple = 256 (4 cache lines)

            # Simplified: read raw pair data
            BBA_SIZE = 64
            PAIR_SIZE = 448  # 2×BBA(128) + metrics block aligned to 64B boundaries

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
            if global_off + 64 <= mm.size():
                active = struct.unpack_from('<I', mm, global_off)[0]
                heartbeat = struct.unpack_from('<Q', mm, global_off + 4)[0]
                bfx_alive = struct.unpack_from('<I', mm, global_off + 12)[0]
                bnb_alive = struct.unpack_from('<I', mm, global_off + 16)[0]
                emergency = struct.unpack_from('<I', mm, global_off + 52)[0]
                daily_pnl = struct.unpack_from('<q', mm, global_off + 56)[0]

                result["active_pairs"] = active
                result["bitfinex"] = bfx_alive == 1
                result["binance"] = bnb_alive == 1
                result["emergency_pause"] = emergency == 1
                result["daily_pnl"] = round(daily_pnl / PRICE_SCALE, 4)
                age_ms = int(time.time() * 1000) - heartbeat if heartbeat > 0 else 99999
                result["alive"] = age_ms < 30000

            result["pairs"] = pairs
            mm.close()

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

# Byte offsets in EngineState (from types.rs analysis)
OFF_L1_SKEW = 1584      # i64 l1_skew_adjustment
OFF_L1_CONF = 1600      # u64 l1_confidence_score
OFF_L1_TOXIC = 1568     # u64 toxic_flow_hits
OFF_L1_UPTIME = 1648    # u64 l1_uptime_pct
OFF_AI_BIAS = 1464      # i64 current_ai_bias

def _get_ml_shield_state():
    """Read ML Shield inference state from engine mmap."""
    global _ml_cache, _ml_cache_ts
    now = time.time()
    if now - _ml_cache_ts < _ML_TTL:
        return _ml_cache

    result = {"online": False, "skew": 0.0, "confidence": 0.0}

    try:
        if not os.path.exists(ENGINE_STATE_PATH):
            return result

        with open(ENGINE_STATE_PATH, 'rb') as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

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

            mm.close()

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

    # GPU from nvidia-smi
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=temperature.gpu,memory.used,memory.total,utilization.gpu',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3
        )
        if out.returncode == 0:
            parts = out.stdout.strip().split(',')
            if len(parts) >= 4:
                result['gpu_temp'] = int(parts[0].strip())
                result['gpu_vram_used'] = int(parts[1].strip())
                result['gpu_vram_total'] = int(parts[2].strip())
                result['gpu_util'] = int(parts[3].strip())
                result['gpu_vram_pct'] = round(100.0 * result['gpu_vram_used'] / max(result['gpu_vram_total'], 1), 1)
    except Exception:
        pass

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


class DashboardHandler(BaseHTTPRequestHandler):
    """Handles SSE stream + static HTML serving."""

    def log_message(self, format, *args):
        pass  # Silence HTTP logs

    def do_GET(self):
        if self.path == "/events":
            self._handle_sse()
        elif self.path == "/" or self.path == "/dashboard":
            self._serve_html()
        elif self.path == "/api/state":
            self._handle_api()
        else:
            self.send_error(404)

    def _handle_sse(self):
        """Stream SSE events with dashboard state."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            while True:
                state = _build_dashboard_state()
                data = json.dumps(state)
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
                time.sleep(SSE_INTERVAL)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_api(self):
        """One-shot JSON API endpoint."""
        state = _build_dashboard_state()
        body = json.dumps(state).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        """Serve the master dashboard HTML."""
        import os
        html_path = os.path.join(os.path.dirname(__file__), "master_dashboard.html")
        try:
            with open(html_path, "r") as f:
                body = f.read().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404, "master_dashboard.html not found")


def start_dashboard_server(cortex_client):
    """Start the SSE dashboard server as a daemon thread."""
    global _cortex
    _cortex = cortex_client

    def _run():
        class ReusableServer(HTTPServer):
            allow_reuse_address = True
            allow_reuse_port = True
        server = ReusableServer(("0.0.0.0", DASHBOARD_PORT), DashboardHandler)
        log.info(f"🎛️ Master Dashboard SSE server: http://0.0.0.0:{DASHBOARD_PORT}")
        server.serve_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
