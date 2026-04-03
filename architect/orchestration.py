"""
Shared bot orchestration logic (v15.2).
Centralized process management ensuring duplicate guards.
Used by L2 Oracle and Telegram Commander.
"""

import os
import sys
import subprocess
import logging

log = logging.getLogger("orchestration")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(PROJECT_ROOT, "target", "release")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

from enum import Enum

class RiskClass(Enum):
    HEDGE_EXEC = "HEDGE_EXEC"
    ARBITRAGE = "ARBITRAGE"
    MARKET_MAKER = "MARKET_MAKER"
    STAT_ARB = "STAT_ARB"
    POSITIONAL = "POSITIONAL"

BOTS = {
    "hydra": {
        "core": os.path.join(BIN_DIR, "hydra-core"),
        "dashboard": os.path.join(BIN_DIR, "hydra-dashboard"),
        "config": os.path.join(BIN_DIR, "hydra-config"),
        "cpu": 0,
        "port": 3000,
        "emoji": "🐍",
        "desc": "BTC-USD Delta Lead",
        "type": RiskClass.MARKET_MAKER,
    },
    "moonshot": {
        "core": os.path.join(BIN_DIR, "moonshot-core"),
        "config": os.path.join(BIN_DIR, "moonshot-config"),
        "cpu": 1,
        "port": 3001,
        "emoji": "🌙",
        "desc": "Multi-Symbol Flash Crash",
        "type": RiskClass.POSITIONAL,
    },
    "grid": {
        "core": os.path.join(BIN_DIR, "grid-core"),
        "config": os.path.join(BIN_DIR, "grid-config"),
        "cpu": 2,
        "port": 3002,
        "emoji": "📐",
        "desc": "Dynamic Multi-Level Grid",
        "type": RiskClass.POSITIONAL,
    },
    "trigon": {
        "core": os.path.join(BIN_DIR, "trigon-core"),
        "config": os.path.join(BIN_DIR, "trigon-config"),
        "cpu": 3,
        "port": 3003,
        "emoji": "🔺",
        "desc": "Triangular Arbitrage",
        "type": RiskClass.STAT_ARB,
    },
    "nexus": {
        "core": os.path.join(BIN_DIR, "nexus-core"),
        "config": os.path.join(BIN_DIR, "nexus-config"),
        "cpu": 3,
        "port": 3004,
        "emoji": "🪐",
        "desc": "Cross-Exchange Arbitrage",
        "type": RiskClass.ARBITRAGE,
    },
}

def is_running(name: str) -> bool:
    """Check if bot core process is running (exact binary match)."""
    try:
        binary_path = BOTS[name]["core"]
        r = subprocess.run(["pgrep", "-f", binary_path], capture_output=True, text=True)
        pids = [p for p in r.stdout.strip().split("\n") if p.strip()]
        return r.returncode == 0 and len(pids) > 0
    except Exception:
        return False

def get_pid(name: str) -> str:
    """Get PID of running bot core (exact binary match)."""
    try:
        binary_path = BOTS[name]["core"]
        r = subprocess.run(["pgrep", "-f", binary_path], capture_output=True, text=True)
        if r.returncode == 0:
            pids = [p for p in r.stdout.strip().split("\n") if p.strip()]
            return pids[0] if pids else None
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
    
    # Run Safe Boot Protocol (SBP) Pre-flight & WFA
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from safe_boot import SafeBootPipeline
        pipeline = SafeBootPipeline(name)
        
        # Phase 1 & 2
        p1 = pipeline.run_phase1_preflight()
        if not p1.get("ok"):
            msg = f"❌ [SBP REJECTED] {name.upper()} failed Pre-flight: {p1.get('error')}"
            log.error(msg)
            return msg
            
        p2 = pipeline.run_phase2_wfa()
        if not p2.get("ok"):
            msg = f"❌ [SBP REJECTED] {name.upper()} failed Walk-Forward gate: {p2.get('error')}"
            log.error(msg)
            return msg
            
        # Phase 3 mode enforcement
        pipeline.enforce_paper_state()
            
    except Exception as e:
        log.error(f"⚠️ SBP evaluation crashed: {e}. Bot start aborted for safety.")
        return f"❌ [SBP CRASHED] {name.upper()}: {e}"
    
    # Check state for PAPER mode
    state_file = os.path.join(PROJECT_ROOT, "state", "armada_state.json")
    is_paper = False
    try:
        if os.path.exists(state_file):
            import json
            with open(state_file) as sf:
                s_data = json.load(sf)
                if s_data.get(name, {}).get("mode") == "PAPER":
                    is_paper = True
    except Exception:
        pass

    # Build args
    args = ["taskset", "-c", str(info["cpu"]), info["core"]]
    if is_paper and name == "nexus":
        args.append("--paper")

    # Start core
    core_log = os.path.join(LOG_DIR, f"{name}-core.log")
    try:
        subprocess.Popen(
            args,
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
        save_bot_state(name, "STOPPED")
        msg = f"{info['emoji']} {name.upper()} zastaven ✅ (byl PID {pid})"
        log.info(msg)
        return msg
    else:
        subprocess.run(["pkill", "-9", "-f", f"{name}-core"], capture_output=True)
        save_bot_state(name, "STOPPED")
        msg = f"{info['emoji']} {name.upper()} force-killed ✅"
        log.warning(msg)
        return msg

def save_bot_state(name: str, mode: str):
    """Save bot state (LIVE, PAPER, PAUSED, OFFLINE) to armada_state.json"""
    import json
    state_file = os.path.join(PROJECT_ROOT, "state", "armada_state.json")
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
        except Exception:
            pass
    
    if name not in state:
        state[name] = {}
    elif isinstance(state[name], str):
        state[name] = {"mode": state[name]}
        
    state[name]["mode"] = mode.upper()
    
    import datetime
    state["last_healthy_ts"] = datetime.datetime.now().isoformat()
    
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)
