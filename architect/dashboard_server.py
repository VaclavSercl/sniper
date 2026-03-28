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
    return state


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
