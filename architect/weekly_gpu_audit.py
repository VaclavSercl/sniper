#!/usr/bin/env python3
"""
🤖 Weekly L1 GPU Audit — SIM v2.0 P2-B
Sniper Armada · Continuous Learning Pipeline

Runs every Sunday at 06:00 via cron.
Gemini analyzes GPU telemetry from the past week and proposes
new L1 tuning parameters (skew_max, obi_threshold, inference_interval).

Cron entry:
  0 6 * * 0 cd /home/wwwenda/sniper/architect && python3 weekly_gpu_audit.py >> /home/wwwenda/sniper/logs/gpu_audit.log 2>&1
"""

import os
import sys
import json
import time
import subprocess
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [GPU-AUDIT] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('gpu_audit')

HISTORY_PATH = os.path.expanduser("~/.local/share/sniper/l1_tuning_history.json")
CORTEX_SOCK = "/tmp/sniper_cortex.sock"


def get_gpu_stats():
    """Read GPU telemetry from Cortex UDS."""
    try:
        import socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(CORTEX_SOCK)
        sock.sendall(b'{"cmd":"GET_GPU_STATS"}\n')
        data = sock.recv(4096)
        sock.close()
        return json.loads(data)
    except Exception as e:
        log.error(f"Cannot read GPU stats: {e}")
        return None


def load_tuning_history():
    """Load tuning history from disk."""
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_tuning_history(history):
    """Save tuning history to disk."""
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, 'w') as f:
        json.dump(history, f, indent=2)


def ask_gemini_for_tuning(gpu_stats, history):
    """Call Gemini to analyze GPU performance and propose new L1 tuning."""
    
    hist_context = ""
    for h in history[-4:]:  # Last 4 weeks
        hist_context += (
            f"  Week {h.get('week', '?')}: skew_max=${h.get('skew_max', '?')}, "
            f"obi_thr={h.get('obi_threshold', '?')}, interval={h.get('interval_ms', '?')}ms, "
            f"verdict={h.get('verdict', '?')}\n"
        )
    
    prompt = f"""You are SNIPER L1 TUNING ADVISOR. Analyze GPU (Phi-3.5 Mini) telemetry 
from the past week and propose optimal L1 tuning parameters.

═══ GPU TELEMETRY ═══
{json.dumps(gpu_stats, indent=2)}

═══ TUNING HISTORY ═══
{hist_context if hist_context else "No history yet."}

═══ RULES ═══
- skew_max_usd: range [0.5, 5.0]. Higher = more aggressive quote skew.
  If toxic_rate > 20%: REDUCE. If win_rate > 60%: can INCREASE.
- obi_threshold: range [0.0, 0.8]. Higher = more selective (fewer actions).
  If too many HOLDS: reduce. If toxic > 15%: increase.
- inference_interval_ms: range [500, 10000]. Lower = more frequent.
  If GPU uptime stable: can reduce. If failures > 0: increase.

═══ RESPOND IN JSON ONLY ═══
{{"skew_max_usd": float, "obi_threshold": float, "inference_interval_ms": int, 
  "reasoning": "string (max 200 chars)", "confidence": int (0-100)}}"""

    try:
        result = subprocess.run(
            ["gemini", "-m", "gemini-3.6-flash", "--output-format=json", "-p", prompt],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            log.error(f"Gemini failed: {result.stderr[:200]}")
            return None
        
        raw = result.stdout.strip()
        # Parse JSON from response
        start = raw.find('{')
        end = raw.rfind('}')
        if start >= 0 and end > start:
            return json.loads(raw[start:end+1])
    except Exception as e:
        log.error(f"Gemini call failed: {e}")
    
    return None


def apply_tuning(tuning):
    """Apply new L1 tuning via Cortex UDS."""
    try:
        import socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(CORTEX_SOCK)
        
        cmd = json.dumps({
            "cmd": "SET_L1_TUNING",
            "skew_max_usd": tuning['skew_max_usd'],
            "obi_threshold": tuning['obi_threshold'],
            "inference_interval_ms": tuning['inference_interval_ms'],
        })
        sock.sendall((cmd + '\n').encode())
        resp = sock.recv(1024)
        sock.close()
        
        log.info(f"Applied L1 tuning: {cmd}")
        return True
    except Exception as e:
        log.error(f"Failed to apply tuning: {e}")
        return False


def main():
    log.info("═══ WEEKLY GPU AUDIT — SIM v2.0 P2-B ═══")
    log.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    
    # 1. Get current GPU stats
    gpu_resp = get_gpu_stats()
    if not gpu_resp or not gpu_resp.get('ok'):
        log.error("Cannot read GPU stats. Cortex offline?")
        return
    
    gpu_stats = gpu_resp.get('data', {})
    total_inf = gpu_stats.get('total_inferences', 0)
    
    if total_inf < 100:
        log.warning(f"Not enough inferences ({total_inf}). Need 100+. Skipping.")
        return
    
    log.info(f"GPU stats: {total_inf} inferences, "
             f"toxic={gpu_stats.get('toxic_rate_pct', 0):.1f}%")
    
    # 2. Load history
    history = load_tuning_history()
    
    # 3. Ask Gemini
    log.info("Calling Gemini for tuning recommendation...")
    tuning = ask_gemini_for_tuning(gpu_stats, history)
    
    if not tuning:
        log.error("Gemini returned no recommendation.")
        return
    
    # 4. Validate ranges
    skew = max(0.5, min(5.0, float(tuning.get('skew_max_usd', 3.0))))
    obi_thr = max(0.0, min(0.8, float(tuning.get('obi_threshold', 0.0))))
    interval = max(500, min(10000, int(tuning.get('inference_interval_ms', 2000))))
    confidence = int(tuning.get('confidence', 50))
    reasoning = tuning.get('reasoning', '')[:200]
    
    log.info(f"Gemini recommends: skew_max=${skew:.1f}, obi_thr={obi_thr:.2f}, "
             f"interval={interval}ms (confidence={confidence}%)")
    log.info(f"Reasoning: {reasoning}")
    
    # 5. Apply if confidence > 50%
    if confidence >= 50:
        applied = apply_tuning({
            'skew_max_usd': skew,
            'obi_threshold': obi_thr,
            'inference_interval_ms': interval,
        })
    else:
        log.info(f"Low confidence ({confidence}%) — keeping current tuning")
        applied = False
    
    # 6. Record history
    week_num = datetime.now().isocalendar()[1]
    entry = {
        'week': f"{datetime.now().year}-W{week_num:02d}",
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'gpu_inferences': total_inf,
        'toxic_rate': gpu_stats.get('toxic_rate_pct', 0),
        'skew_max': skew,
        'obi_threshold': obi_thr,
        'interval_ms': interval,
        'confidence': confidence,
        'reasoning': reasoning,
        'applied': applied,
        'verdict': 'APPLIED' if applied else 'SKIPPED',
    }
    history.append(entry)
    history = history[-52:]  # Keep 1 year
    save_tuning_history(history)
    
    log.info(f"═══ AUDIT COMPLETE: {'APPLIED' if applied else 'SKIPPED'} ═══")


if __name__ == '__main__':
    main()
