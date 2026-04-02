#!/usr/bin/env python3
"""
Sovereign HFT - L2 Orderbook Warmup Daemon (v20.0)

Systematically manages the MARKET_MAKER 60s quarantine sequence.
This script is detached by SBP and executes the L2 -> LIVE transition
safely after the book reconstruction is complete.
"""

import os
import sys
import time
import json
import mmap
import struct
import logging
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from architect.safe_boot import BOT_RISK_MAP, STATE_FILE
from architect.orchestration import save_bot_state

logging.basicConfig(
    filename=os.path.join(PROJECT_ROOT, "logs", "tg_commander.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("l2_warmup")

def trigger_l2_warmup_task(bot_name: str, duration_sec: int):
    log.info(f"⏳ [WARMUP] Starting L2 Orderbook Reconstruction for {bot_name} ({duration_sec}s)...")
    time.sleep(duration_sec)
    
    # Check if still in PAUSED mode
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            
        if state.get(bot_name, {}).get("mode") != "PAUSED":
            log.warning(f"⚠️ [WARMUP] {bot_name} is no longer PAUSED. Aborting L2 transition.")
            return
    except Exception as e:
        log.error(f"❌ [WARMUP] Failed reading state for {bot_name}: {e}")
        return

    # Flip Armada State
    try:
        save_bot_state(bot_name, "LIVE")
    except Exception as e:
        log.error(f"❌ [WARMUP] Failed to save LIVE state for {bot_name}: {e}")
        return

    # Flip Risk IPC MMap Offset
    risk_entry = BOT_RISK_MAP.get(bot_name)
    if risk_entry:
        risk_path, offset = risk_entry
        if os.path.exists(risk_path):
            try:
                with open(risk_path, 'r+b') as rf:
                    mm = mmap.mmap(rf.fileno(), 0)
                    struct.pack_into('<Q', mm, offset, 0)
                    mm.flush()
                    mm.close()
                log.info(f"✅ [WARMUP] {bot_name} L2 Reconstruction Complete -> Transitioned to LIVE.")
            except Exception as e:
                log.error(f"❌ [WARMUP] Failed to write mmap offset for {bot_name}: {e}")
        else:
            log.error(f"❌ [WARMUP] Mmap file {risk_path} missing. Cannot unpause {bot_name}.")
    else:
        log.error(f"❌ [WARMUP] Missing BOT_RISK_MAP entry for {bot_name}.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bot_name", help="Name of the bot to warmup")
    parser.add_argument("duration", type=int, help="Duration of warmup in seconds")
    args = parser.parse_args()
    
    trigger_l2_warmup_task(args.bot_name, args.duration)
