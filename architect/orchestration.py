"""
Shared bot orchestration logic (v15.2).
Centralized process management ensuring duplicate guards.
Used by L2 Oracle and Telegram Commander.
"""

import os
import subprocess
import logging

log = logging.getLogger("orchestration")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(PROJECT_ROOT, "target", "release")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

BOTS = {
    "hydra": {
        "core": os.path.join(BIN_DIR, "hydra-core"),
        "dashboard": os.path.join(BIN_DIR, "hydra-dashboard"),
        "config": os.path.join(BIN_DIR, "hydra-config"),
        "cpu": 0,
        "port": 3000,
        "emoji": "🐍",
        "desc": "BTC-USD Delta Lead",
    },
    "moonshot": {
        "core": os.path.join(BIN_DIR, "moonshot-core"),
        "config": os.path.join(BIN_DIR, "moonshot-config"),
        "cpu": 1,
        "port": 3001,
        "emoji": "🌙",
        "desc": "Multi-Symbol Flash Crash",
    },
    "grid": {
        "core": os.path.join(BIN_DIR, "grid-core"),
        "config": os.path.join(BIN_DIR, "grid-config"),
        "cpu": 2,
        "port": 3002,
        "emoji": "📐",
        "desc": "Dynamic Multi-Level Grid",
    },
    "trigon": {
        "core": os.path.join(BIN_DIR, "trigon-core"),
        "config": os.path.join(BIN_DIR, "trigon-config"),
        "cpu": 3,
        "port": 3003,
        "emoji": "🔺",
        "desc": "Triangular Arbitrage",
    },
    "nexus": {
        "core": os.path.join(BIN_DIR, "nexus-core"),
        "cpu": 3,
        "port": 3004,
        "emoji": "🪐",
        "desc": "Cross-Exchange Arbitrage",
        "args": ["--paper"],  # Start in paper mode by default
    },
}

def is_running(name: str) -> bool:
    """Check if bot core process is running."""
    try:
        r = subprocess.run(["pgrep", "-f", f"{name}-core"], capture_output=True, text=True)
        return r.returncode == 0 and int(r.stdout.strip().split("\n")[0]) > 0
    except Exception:
        return False

def get_pid(name: str) -> str:
    """Get PID of running bot core."""
    try:
        r = subprocess.run(["pgrep", "-f", f"{name}-core"], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip().split("\n")[0]
    except Exception:
        pass
    return None

def start_bot(name: str) -> str:
    """Start a bot and its dashboard (if applicable). Returns status message."""
    info = BOTS.get(name)
    if not info:
        msg = f"❌ Neznámý bot: {name}"
        log.error(msg)
        return msg

    if is_running(name):
        msg = f"{info['emoji']} {name.upper()} už běží (PID {get_pid(name)})"
        log.warning(msg)
        return msg

    os.makedirs(LOG_DIR, exist_ok=True)
    
    # Start core
    core_log = os.path.join(LOG_DIR, f"{name}-core.log")
    try:
        subprocess.Popen(
            ["taskset", "-c", str(info["cpu"]), info["core"]],
            stdout=open(core_log, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=PROJECT_ROOT,
        )
        msg = f"✅ {info['emoji']} {name.upper()} nastartován na CPU {info['cpu']}"
        log.info(msg)
    except Exception as e:
        msg = f"❌ Failed to start {name} core: {e}"
        log.error(msg)
        return msg

    # Start dashboard if exists (only hydra has it now)
    if "dashboard" in info:
        dash_log = os.path.join(LOG_DIR, f"{name}-dashboard.log")
        try:
            # Check if dashboard is running to prevent dual-dashboards
            r = subprocess.run(["pgrep", "-f", f"{name}-dashboard"], capture_output=True, text=True)
            if not (r.returncode == 0 and int(r.stdout.strip().split("\n")[0]) > 0):
                subprocess.Popen(
                    ["taskset", "-c", "3", info["dashboard"]],
                    stdout=open(dash_log, "a"),
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    cwd=PROJECT_ROOT,
                )
                log.info(f"✅ {name.upper()} dashboard started on CPU 3")
        except Exception as e:
            log.error(f"❌ Failed to start {name} dashboard: {e}")

    import time
    time.sleep(2)
    if is_running(name):
        pid = get_pid(name)
        msg = f"{info['emoji']} {name.upper()} spuštěn ✅ (PID {pid}, Core {info['cpu']}, :{info['port']})"
    else:
        msg = f"❌ {name.upper()} se nepodařilo spustit. Zkontroluj log: {core_log}"

    return msg

def stop_bot(name: str) -> str:
    """Kill bot core and dashboard (if applicable). Returns status message."""
    info = BOTS.get(name)
    if not info:
        return f"❌ Neznámý bot: {name}"

    if not is_running(name):
        return f"{info['emoji']} {name.upper()} neběží."

    # Kill dashboard first
    if "dashboard" in info:
        subprocess.run(["pkill", "-f", f"{name}-dashboard"], capture_output=True)
            
    # Kill core
    pid = get_pid(name)
    subprocess.run(["pkill", "-f", f"{name}-core"], capture_output=True)
    
    import time
    time.sleep(1)

    if not is_running(name):
        msg = f"{info['emoji']} {name.upper()} zastaven ✅ (byl PID {pid})"
        log.info(msg)
        return msg
    else:
        subprocess.run(["pkill", "-9", "-f", f"{name}-core"], capture_output=True)
        msg = f"{info['emoji']} {name.upper()} force-killed ✅"
        log.warning(msg)
        return msg
