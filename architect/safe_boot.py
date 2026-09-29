#!/usr/bin/env python3
"""
🛡️ Sniper Armada — Safe Boot Protocol (SBP) v2.0
Handles Fáze 1 (Pre-Flight) and Fáze 2 (Walk-Forward Gates).

If validation passes, enforces Fáze 3 (PAPER) before allowing the bot
to connect safely. Used by orchestration.py.
"""

import os
import sys
import json
import time
import subprocess
import sqlite3
import logging
from datetime import datetime, timezone

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

log = logging.getLogger("safe_boot")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.expanduser("~/.local/share/sniper/market_data.db")
STATE_FILE = os.path.join(PROJECT_ROOT, "state", "armada_state.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orchestration import BOTS, RiskClass

BOT_RISK_MAP = {
    "hydra":    ("/dev/shm/sniper/risk_state.bin", 0),
    "moonshot": ("/dev/shm/sniper/moonshot_risk.bin", 2560),
    "grid":     ("/dev/shm/sniper/grid_risk.bin", 72),
    "trigon":   ("/dev/shm/sniper/trigon_risk.bin", 3072),
}

class SafeBootPipeline:
    def __init__(self, bot_name: str):
        self.bot = bot_name
        self.binary = os.path.join(PROJECT_ROOT, "target", "release", f"{bot_name}-core")

    def run_phase1_preflight(self) -> dict:
        """Fáze 1: Pre-Flight Check
        - Binary exists
        - .env exists
        - Ping connection placeholder
        """
        log.info(f"🚀 [SBP] Phase 1 (Pre-Flight) starting for {self.bot}...")
        
        # 1. Check binary
        if not os.path.exists(self.binary):
            return {"ok": False, "error": f"Binary not found: {self.binary}"}
            
        # 2. Check auth context
        env_path = os.path.join(PROJECT_ROOT, ".env")
        if not os.path.exists(env_path):
            return {"ok": False, "error": ".env file missing!"}
            
        with open(env_path) as f:
            content = f.read()
            if "BITFINEX_API_KEY" not in content:
                return {"ok": False, "error": "API Key missing in .env"}

        # 3. Check mmap directory capability
        if not os.path.ismount("/dev/shm"):
            pass # allow fallback but log warning
            
        log.info(f"✅ [SBP] Phase 1 PASSED for {self.bot}")
        return {"ok": True}

    def run_phase2_wfa(self) -> dict:
        """Require an actual simulator result; volatility is not a backtest."""
        from pathlib import Path
        from counterfactual_backtest import run_full_walk_forward
        config = Path(PROJECT_ROOT) / "state" / "backtest_parameters.json"
        try:
            if config.is_symlink() or not config.is_file():
                return {"ok": False, "error": "BLOCKED: tested parameters missing"}
            parameters = json.loads(config.read_text())[self.bot]
            if not isinstance(parameters, dict):
                raise ValueError("Invalid parameter record")
            ins, oos = run_full_walk_forward(self.bot, parameters)
            reasons = list(ins.fail_reasons) + list(oos.fail_reasons)
            if not ins.passed or not oos.passed or reasons:
                return {"ok": False, "error": "BLOCKED: backtest not qualified",
                        "reasons": sorted(set(reasons))}
            return {"ok": True, "scope": "paper_start_only"}
        except Exception as exc:
            return {"ok": False, "error": "BLOCKED: " + type(exc).__name__}

    def run_pipeline(self) -> dict:
        """Execute the full start sequence."""
        # 1. Preflight
        p1 = self.run_phase1_preflight()
        if not p1.get("ok"):
            return p1
            
        # 2. Walk-Forward / Volatility check
        p2 = self.run_phase2_wfa()
        if not p2.get("ok"):
            return p2
            
        # 3. Set state to PAPER (Phase 3 enforcement)
        self.enforce_paper_state()
        
        return {"ok": True, "cmd": self.binary}

    def enforce_paper_state(self):
        """Pause known Rust IPC before publishing PAPER; errors abort startup.

        This is a startup interlock, not an execution-model qualification and
        not an atomic transaction across Rust memory and JSON. A crash leaves
        IPC paused. Unknown layouts cannot be started through this path.
        """
        import mmap
        import struct
        import tempfile
        from pathlib import Path
        entry = BOT_RISK_MAP.get(self.bot)
        if entry is None:
            raise RuntimeError("Unverified risk IPC layout")
        risk_path, offset = entry
        risk = Path(risk_path)
        target = Path(STATE_FILE)
        if risk.is_symlink() or not risk.is_file() or target.is_symlink():
            raise RuntimeError("Missing or unsafe state path")
        state = json.loads(target.read_text()) if target.exists() else {}
        if not isinstance(state, dict):
            raise ValueError("Invalid state")
        with risk.open("r+b") as stream:
            if os.fstat(stream.fileno()).st_size < offset + 8:
                raise RuntimeError("Truncated risk IPC")
            with mmap.mmap(stream.fileno(), 0) as mapping:
                struct.pack_into('<Q', mapping, offset, 1)
                mapping.flush()
        state[self.bot] = {"mode": "PAPER", "since": datetime.now(timezone.utc).isoformat()}
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=target.parent, prefix=".paper-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, target)
        finally:
            if os.path.exists(name): os.unlink(name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) < 2:
        print("Usage: python3 safe_boot.py <bot_name>")
        sys.exit(1)
        
    pipeline = SafeBootPipeline(sys.argv[1])
    res = pipeline.run_pipeline()
    if res.get("ok"):
        print("OK")
        sys.exit(0)
    else:
        print(f"FAILED: {res.get('error')}")
        sys.exit(1)
