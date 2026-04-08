#!/usr/bin/env python3
"""
🔬 SNIPER ARMADA — Monitor Watchdog v1.0
==========================================
Automated surveillance: every 5 minutes checks all processes,
modes, duplicates, mmap integrity, trades. Writes to Monitor.md.
Runs for 24 hours.

Usage: nohup python3 architect/monitor_watchdog.py &
"""

import os
import sys
import json
import time
import sqlite3
import subprocess
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONITOR_MD = os.path.join(PROJECT_ROOT, "logs", "Monitor.md")
STATE_FILE = os.path.join(PROJECT_ROOT, "state", "armada_state.json")
PNL_DB = os.path.expanduser("~/.local/share/sniper/pnl.db")
MMAP_DIR = "/dev/shm/sniper"
MONITOR_JSONL = os.path.join(PROJECT_ROOT, "logs", "system_monitor.jsonl")

DURATION_H = 24
INTERVAL_S = 300  # 5 minutes
FIRST_CHECK_S = 66  # First check after 66 seconds

EXPECTED_PROCESSES = {
    "hydra-core": 1,
    "hydra-dashboard": 1,
    "sovereign-cortex": 1,
    "tg_commander": 1,
    "pnl_daemon": 1,
    "price_bridge": 1,
    "market_recorder": 1,
    "watchdog.sh": 1,
    "deploy_armada": 1,
    "system_monitor": 1,
}

OPTIONAL_PROCESSES = [
    "moonshot-core", "grid-core", "trigon-core", "nexus-core",
    "l1_shield", "fee_monitor",
]

EXPECTED_MMAP = [
    "engine_state.bin", "risk_state.bin", "l2_command.bin",
    "cross_exchange.bin", "pnl_state.bin", "fee_state.bin",
]


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def count_processes(pattern):
    """Return list of PIDs matching pattern."""
    try:
        r = subprocess.run(["pgrep", "-a", "-f", pattern],
                           capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return []
        pids = []
        for line in r.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(None, 1)
            cmd = parts[1] if len(parts) > 1 else ""
            if "monitor_watchdog" in cmd or "pgrep" in cmd:
                continue
            pids.append({"pid": parts[0], "cmd": cmd[:120]})
        return pids
    except Exception:
        return []


def get_armada_state():
    """Read armada_state.json."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def get_fill_count():
    """Get total fills from pnl.db."""
    try:
        conn = sqlite3.connect(f"file:{PNL_DB}?mode=ro", uri=True, timeout=2)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM fills")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return -1


def get_monitor_stats():
    """Get system_monitor.py stats."""
    try:
        st = os.stat(MONITOR_JSONL)
        with open(MONITOR_JSONL) as f:
            events = sum(1 for _ in f)
        return {"size_kb": round(st.st_size / 1024, 1), "events": events}
    except Exception:
        return {"size_kb": 0, "events": 0}


def get_mmap_state():
    """Check mmap files."""
    files = {}
    if os.path.isdir(MMAP_DIR):
        for f in os.listdir(MMAP_DIR):
            try:
                st = os.stat(os.path.join(MMAP_DIR, f))
                files[f] = st.st_size
            except Exception:
                pass
    return files


def get_system_load():
    """Get CPU/RAM."""
    try:
        load1, load5, _ = os.getloadavg()
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")[:2]
                mem[k.strip()] = int(v.strip().split()[0])
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", 0)
        ram_pct = round(100.0 * (total - avail) / total, 1)
        return {"load_1m": round(load1, 2), "load_5m": round(load5, 2), "ram_pct": ram_pct}
    except Exception:
        return {}


def run_check(cycle, elapsed_min):
    """Run a full system check and return markdown report."""
    ts = now_str()
    lines = []
    alerts = []

    lines.append(f"### Cyklus #{cycle} — {ts} (t+{elapsed_min:.0f}m)")
    lines.append("")

    # 1. Process check
    lines.append("**Procesy:**")
    lines.append("| Proces | Očekáváno | Nalezeno | Stav |")
    lines.append("|--------|-----------|----------|------|")

    for pattern, expected in EXPECTED_PROCESSES.items():
        pids = count_processes(pattern)
        count = len(pids)
        if count == expected:
            status = "✅"
        elif count == 0:
            status = "❌ CHYBÍ"
            alerts.append(f"❌ `{pattern}` neběží!")
        elif count > expected:
            status = f"⚠️ DUPLIKÁT ({count}×)"
            alerts.append(f"⚠️ `{pattern}` běží {count}× místo {expected}×!")
        else:
            status = f"⚠️ {count}"
        lines.append(f"| `{pattern}` | {expected} | {count} | {status} |")

    for pattern in OPTIONAL_PROCESSES:
        pids = count_processes(pattern)
        count = len(pids)
        if count > 1:
            alerts.append(f"⚠️ `{pattern}` duplikát: {count}×!")
        status = f"{'✅' if count == 1 else '⏸️ Off'}" if count <= 1 else f"⚠️ {count}×"
        lines.append(f"| `{pattern}` | 0-1 | {count} | {status} |")
    lines.append("")

    # 2. Bot modes
    state = get_armada_state()
    lines.append("**Režimy botů:**")
    lines.append("| Bot | Mód | Od |")
    lines.append("|-----|-----|----|")
    for bot_name in ["hydra", "moonshot", "grid", "trigon", "nexus"]:
        bdata = state.get(bot_name, {})
        mode = bdata.get("mode", "UNKNOWN")
        since = bdata.get("since", "?")[:19]
        icon = "🟢" if mode == "LIVE" else "📝" if mode == "PAPER" else "⏸️" if mode == "PAUSED" else "❓"
        lines.append(f"| {bot_name.upper()} | {icon} {mode} | {since} |")
        if bot_name == "hydra" and mode == "LIVE":
            alerts.append(f"⚠️ HYDRA je v LIVE módu (ne PAPER)")
    lines.append("")

    # 3. mmap
    mmap = get_mmap_state()
    missing_mmap = [f for f in EXPECTED_MMAP if f not in mmap]
    extra_mmap = [f for f in mmap if f not in EXPECTED_MMAP and f not in ["state.json", "l2_reasoning.txt"]]
    lines.append(f"**mmap:** {len(mmap)} souborů v `/dev/shm/sniper/`")
    if missing_mmap:
        lines.append(f"  - ❌ Chybí: {', '.join(missing_mmap)}")
        alerts.append(f"❌ Chybí mmap: {', '.join(missing_mmap)}")
    if extra_mmap:
        lines.append(f"  - 📦 Extra: {', '.join(extra_mmap)}")
    lines.append("")

    # 4. Trades
    fills = get_fill_count()
    lines.append(f"**Obchody:** {fills} fills celkem v pnl.db")
    lines.append("")

    # 5. Monitor
    mon = get_monitor_stats()
    lines.append(f"**System Monitor:** {mon['events']} events, {mon['size_kb']} KB")
    lines.append("")

    # 6. System
    sys_load = get_system_load()
    lines.append(f"**Systém:** Load {sys_load.get('load_1m', '?')}/{sys_load.get('load_5m', '?')} | RAM {sys_load.get('ram_pct', '?')}%")
    lines.append("")

    # 7. Alerts
    if alerts:
        lines.append("**🚨 ALARMY:**")
        for a in alerts:
            lines.append(f"- {a}")
    else:
        lines.append("**✅ Žádné alarmy**")
    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines), alerts


def main():
    start = time.time()
    os.makedirs(os.path.dirname(MONITOR_MD), exist_ok=True)

    # Write header
    with open(MONITOR_MD, "w") as f:
        f.write(f"# 🔬 Sniper Armada — Monitor Report\n\n")
        f.write(f"> Start: {now_str()} | Trvání: {DURATION_H}h | Interval: {INTERVAL_S//60}m\n\n")
        f.write(f"---\n\n")

    print(f"🔬 Monitor Watchdog started ({DURATION_H}h, every {INTERVAL_S//60}m)")
    print(f"   First check in {FIRST_CHECK_S}s")
    print(f"   Output: {MONITOR_MD}")

    # First check after 66s
    time.sleep(FIRST_CHECK_S)
    cycle = 1
    elapsed = (time.time() - start) / 60
    report, alerts = run_check(cycle, elapsed)

    with open(MONITOR_MD, "a") as f:
        f.write(report + "\n")

    print(f"  [{now_str()}] Cycle #{cycle}: {len(alerts)} alerts")
    for a in alerts:
        print(f"    {a}")

    # Regular checks every 5 min
    while time.time() - start < DURATION_H * 3600:
        time.sleep(INTERVAL_S)
        cycle += 1
        elapsed = (time.time() - start) / 60
        report, alerts = run_check(cycle, elapsed)

        with open(MONITOR_MD, "a") as f:
            f.write(report + "\n")

        print(f"  [{now_str()}] Cycle #{cycle}: {len(alerts)} alerts")
        for a in alerts:
            print(f"    {a}")

    # Final summary
    with open(MONITOR_MD, "a") as f:
        f.write(f"\n## 📊 Konec monitoringu\n\n")
        f.write(f"- Celkem cyklů: {cycle}\n")
        f.write(f"- Trvání: {(time.time()-start)/3600:.1f}h\n")
        f.write(f"- Dokončeno: {now_str()}\n")

    print(f"\n📊 Monitor Watchdog finished after {cycle} cycles")


if __name__ == "__main__":
    main()
