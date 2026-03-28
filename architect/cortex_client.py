"""
🔌 Cortex UDS Client — Hemisphere Bridge (Python side)

Communicates with Sovereign Cortex via Unix Domain Socket.
Replaces the old cortex_state.json file-based IPC.

Usage:
    from cortex_client import CortexClient
    cortex = CortexClient()
    snap = cortex.get_snapshot()
    cortex.set_grid(25.0)
"""

import socket
import json
import logging

log = logging.getLogger("cortex_client")

SOCKET_PATH = "/tmp/cortex.sock"
DEFAULT_TIMEOUT = 2.0  # seconds


class CortexClient:
    """Non-blocking UDS client for Sovereign Cortex."""

    def __init__(self, sock_path: str = SOCKET_PATH, timeout: float = DEFAULT_TIMEOUT):
        self.sock_path = sock_path
        self.timeout = timeout

    def _send(self, payload: dict) -> dict:
        """Send JSON command via UDS, return parsed response."""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.sock_path)

                # Send JSON + newline (Rust BufReader reads lines)
                msg = json.dumps(payload) + "\n"
                sock.sendall(msg.encode("utf-8"))

                # Shutdown write side so Rust knows we're done
                sock.shutdown(socket.SHUT_WR)

                # Read full response (may exceed one recv buffer)
                chunks = []
                while True:
                    chunk = sock.recv(16384)
                    if not chunk:
                        break
                    chunks.append(chunk)

                raw = b"".join(chunks).decode("utf-8").strip()
                if not raw:
                    return {"ok": False, "error": "EMPTY_RESPONSE"}
                return json.loads(raw)

        except FileNotFoundError:
            log.error("Socket %s not found — is Cortex running?", self.sock_path)
            return {"ok": False, "error": "CORTEX_OFFLINE"}
        except socket.timeout:
            log.error("Cortex timeout (%ss)", self.timeout)
            return {"ok": False, "error": "TIMEOUT"}
        except ConnectionRefusedError:
            log.error("Cortex connection refused")
            return {"ok": False, "error": "CORTEX_OFFLINE"}
        except json.JSONDecodeError as e:
            log.error("Invalid JSON from Cortex: %s", e)
            return {"ok": False, "error": f"BAD_JSON: {e}"}
        except Exception as e:
            log.error("UDS error: %s", e)
            return {"ok": False, "error": str(e)}

    # ── Public API ──

    def ping(self) -> dict:
        return self._send({"cmd": "PING"})

    def get_snapshot(self) -> dict:
        """Live mmap dump of all bots. Replaces cortex_state.json."""
        return self._send({"cmd": "GET_SNAPSHOT"})

    def set_grid(self, value: float, bot: str = "hydra") -> dict:
        """Set grid step (clamped 2.0-50.0 by Cortex)."""
        return self._send({"cmd": "SET_GRID", "bot": bot, "value": value})

    def set_maxpos(self, value: float, bot: str = "hydra") -> dict:
        """Set max position (clamped 0.001-0.02 by Cortex)."""
        return self._send({"cmd": "SET_MAXPOS", "bot": bot, "value": value})

    def set_regime(self, regime: str) -> dict:
        """Set market regime (TRENDING/RANGING/CHAOS/BEARISH_SHOCK)."""
        return self._send({"cmd": "SET_REGIME", "regime": regime})

    def pause(self, bot: str = "hydra") -> dict:
        return self._send({"cmd": "PAUSE", "bot": bot})

    def unpause(self, bot: str = "hydra") -> dict:
        return self._send({"cmd": "UNPAUSE", "bot": bot})

    def is_online(self) -> bool:
        """Quick health check."""
        r = self.ping()
        return r.get("ok", False)
