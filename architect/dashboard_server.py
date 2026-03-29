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
import threading
import logging
import subprocess
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

log = logging.getLogger("dashboard_sse")

DASHBOARD_PORT = 3004
SSE_INTERVAL = 0.5  # 500ms between updates

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

    return state


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
