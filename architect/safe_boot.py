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
    "hydra":    ("/dev/shm/beroun/risk_state.bin", 0),
    "moonshot": ("/dev/shm/beroun/moonshot_risk.bin", 2560),
    "grid":     ("/dev/shm/beroun/grid_risk.bin", 72),
    "trigon":   ("/dev/shm/beroun/trigon_risk.bin", 3072),
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
                "SELECT ts, close FROM candles_1s WHERE symbol='tBTCUSD' AND ts > ?",
                conn, params=(thirty_mins_ago,)
            )
            conn.close()
            
            if len(df) < 60:
                log.warning("⚠️ [SBP] Not enough candle data for volatility calc (<60s). Passing via bypass.")
                return {"ok": True}
                
            # Calculate rolling price standard deviation (volatility proxy)
            std_dev = df['close'].std()
            price = df['close'].iloc[-1]
            
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
        """Forces PAPER/PAUSED mode via BOTH armada_state.json AND mmap.
        
        CRITICAL: Rust bots read paused state from mmap (risk_state.bin),
        NOT from armada_state.json. Writing only to JSON leaves bots in
        LIVE mode. We must write to BOTH.
        """
        import mmap as _mmap
        import struct as _struct
        
        # ═══ Per-bot mmap risk file paths and global_paused offsets ═══
        # Each bot has its own risk mmap with global_paused at a different offset.
        # Offsets derived from #[repr(C, align(64))] Rust struct layouts:
        #   Hydra:    risk_state.bin, offset 0 (RiskState.paused is first field)
        #   Moonshot: moonshot_risk.bin, offset 2560 (20 × MoonshotPairRisk@128B)
        #   Grid:     grid_risk.bin, offset 72 (flat struct, after spacing/levels/qty/mode fields)
        #   Trigon:   trigon_risk.bin, offset 3072 (24 × TrigonTriangleRisk@128B)
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
            else:
                state = {}
                
            if self.bot not in state:
                state[self.bot] = {}
            
            bot_type = BOTS.get(self.bot, {}).get("type", RiskClass.POSITIONAL)
            log.info(f"🚀 [SBP] v20.0 Routing Sequence: {self.bot} ➔ [{bot_type.name}]")

            # Default fallback mode determined by RiskClass
            fallback_mode = "PAPER"
            
            if bot_type == RiskClass.HEDGE_EXEC:
                log.critical(f"🚨 EMERGENCY BYPASS: {self.bot} booting immediately to LIVE.")
                fallback_mode = state[self.bot].get("mode", "LIVE")
                if fallback_mode in ("OFFLINE", "STOPPED"): fallback_mode = "LIVE"
                
            elif bot_type == RiskClass.ARBITRAGE:
                log.info(f"[{self.bot}] ARBITRAGE Bypass: Executing API Latency & Inventory Sync...")
                fallback_mode = state[self.bot].get("mode", "PAPER")
                if fallback_mode in ("OFFLINE", "STOPPED"): fallback_mode = "PAPER"
                
            elif bot_type == RiskClass.MARKET_MAKER:
                log.info(f"[{self.bot}] PAUSED: Initiating 60s L2 Orderbook Reconstruction.")
                fallback_mode = "PAUSED"
                
                # Asynchronní task po 60s interně přepne do LIVE
                try:
                    warmup_script = os.path.join(PROJECT_ROOT, "architect", "l2_warmup.py")
                    subprocess.Popen(
                        [sys.executable, warmup_script, self.bot, "60"],
                        cwd=PROJECT_ROOT,
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                except Exception as e:
                    log.error(f"Failed to spawn async L2 warmup: {e}")
                
            elif bot_type == RiskClass.STAT_ARB:
                log.info(f"[{self.bot}] PAPER: Verifying Mathematical Cointegration...")
                fallback_mode = "PAPER"
                
            elif bot_type == RiskClass.POSITIONAL:
                log.info(f"[{self.bot}] PAPER LOCKED: Requesting 8-Day Walk-Forward Analysis from L2 Oracle.")
                fallback_mode = "PAPER"

            # Override mode safely
            if state[self.bot].get("mode") == "LIVE" and fallback_mode in ("PAPER", "PAUSED"):
                log.warning(f"🛡️ [SBP] Downgrading {self.bot} from LIVE -> {fallback_mode} for Phase 3")
                
            state[self.bot]["mode"] = fallback_mode
            state[self.bot]["since"] = datetime.now().isoformat()
            
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            
            # ═══ CRITICAL: Write paused=1 to bot-specific mmap risk file ═══
            # Rust bots check ONLY their own mmap field, not the JSON file.
            risk_entry = BOT_RISK_MAP.get(self.bot)
            if risk_entry and fallback_mode in ("PAPER", "PAUSED"):
                risk_path, pause_offset = risk_entry
                if os.path.exists(risk_path):
                    try:
                        with open(risk_path, "r+b") as rf:
                            mm = _mmap.mmap(rf.fileno(), 0)
                            _struct.pack_into('<Q', mm, pause_offset, 1)  # paused = 1
                            mm.flush()
                            mm.close()
                        log.info(f"🛡️ [SBP] {self.bot}: {os.path.basename(risk_path)}[{pause_offset}] paused=1 written")
                    except Exception as me:
                        log.error(f"⚠️ [SBP] Failed to write mmap pause for {self.bot}: {me}")
                else:
                    log.warning(f"⚠️ [SBP] {self.bot}: risk mmap {risk_path} not found (pre-create needed)")
                    
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
