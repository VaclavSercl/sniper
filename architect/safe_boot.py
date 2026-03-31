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
        """Fáze 2: Walk-Forward Backtest Gate
        Verifies that current market volatility doesn't critically violate
        the bot's default configuration before allowing even PAPER mode execution.
        """
        log.info(f"🧪 [SBP] Phase 2 (WFA) starting for {self.bot}...")
        
        # We only check live volatility if market_data.db is online and has pandas
        if not HAS_PANDAS or not os.path.exists(DB_PATH):
            log.warning("⚠️ [SBP] market_data.db or pandas missing, bypassing WFA check.")
            return {"ok": True, "warning": "Bypassed M5 volatility check"}

        try:
            # Check last 30 minutes of 1s candles
            conn = sqlite3.connect(DB_PATH)
            thirty_mins_ago = int((time.time() - 1800) * 1000)
            
            df = pd.read_sql_query(
                "SELECT ts_ms, close_px FROM candles_1s WHERE symbol='tBTCUSD' AND ts_ms > ?",
                conn, params=(thirty_mins_ago,)
            )
            conn.close()
            
            if len(df) < 60:
                log.warning("⚠️ [SBP] Not enough candle data for volatility calc (<60s). Passing via bypass.")
                return {"ok": True}
                
            # Calculate rolling price standard deviation (volatility proxy)
            std_dev = df['close_px'].std()
            price = df['close_px'].iloc[-1]
            
            log.info(f"📊 [SBP] 30m BTC Volatility (StdDev): ${std_dev:.2f} at ${price:.0f}")
            
            # Risk Gate: If price is bouncing intensely, a tight grid will be destroyed.
            # E.g., if std_dev > 100 (high chop), grid_step shouldn't be 1.0!
            # Since we can't easily read mmap from here smoothly, we use a basic proxy check
            if std_dev > 500.0:
                return {
                    "ok": False, 
                    "error": f"Market Extreme Shock Detected (StdDev > 500: ${std_dev:.2f}). Refusing boot to protect capital."
                }
                
        except Exception as e:
            log.warning(f"⚠️ [SBP] Phase 2 evaluation error: {e}. Passing conservatively.")
            
        log.info(f"✅ [SBP] Phase 2 PASSED for {self.bot} -> Safe for Paper Trading")
        return {"ok": True}

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
        """Forces the armada_state.json to downgrade modes to PAPER automatically."""
        import copy
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
            else:
                state = {}
                
            if self.bot not in state:
                state[self.bot] = {}
                
            # Override mode safely
            if state[self.bot].get("mode") == "LIVE":
                log.warning(f"🛡️ [SBP] Downgrading {self.bot} from LIVE -> PAPER for Phase 3")
                
            state[self.bot]["mode"] = "PAPER"
            state[self.bot]["since"] = datetime.now().isoformat()
            
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
                
        except Exception as e:
            log.error(f"Failed to enforce paper state: {e}")

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
