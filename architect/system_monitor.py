#!/usr/bin/env python3
"""
🔬 SNIPER ARMADA — System-Wide Monitor v1.0
============================================
Captures EVERYTHING happening in the system for post-mortem analysis:
- Process births/deaths (every sniper binary, python daemon, node)
- mmap file changes (size, mtime, new/deleted)
- Trade fills from pnl.db in real-time
- Log file growth (all .log files)
- System resources (CPU, RAM, disk)
- armada_state.json changes
- L2 Oracle decisions from l2_command.bin

Writes timestamped JSONL to logs/system_monitor.jsonl
Run duration: configurable (default 33 days = 792h)

Usage:
    python3 architect/system_monitor.py [--days 33]
"""

import os
import sys
import json
import time
import sqlite3
import subprocess
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
MONITOR_LOG = os.path.join(LOG_DIR, "system_monitor.jsonl")
MONITOR_PID = os.path.join(LOG_DIR, "system_monitor.pid")
MMAP_DIR = "/dev/shm/beroun"
STATE_FILE = os.path.join(PROJECT_ROOT, "state", "armada_state.json")
PNL_DB = os.path.expanduser("~/.local/share/sniper/pnl.db")

# All process patterns to watch
WATCH_PATTERNS = [
    "hydra-core", "hydra-dashboard", "hydra-config",
    "moonshot-core", "moonshot-config",
    "grid-core", "grid-config",
    "trigon-core", "trigon-config",
    "nexus-core", "nexus-config",
    "sovereign-cortex",
    "tg_commander", "pnl_daemon", "price_bridge",
    "l1_shield", "market_recorder", "fee_monitor",
    "dashboard_server", "deploy_armada", "watchdog.sh",
    "safe_boot", "retention_cron", "market_downsampler",
    "ab_testing", "drop_copy",
]

# Log file patterns to watch for growth
LOG_PATTERNS = [
    "logs/*.log",
    "logs/sniper.db",
    "nexus/logs/*.log",
]

POLL_INTERVAL = 5  # seconds between polls


class SystemMonitor:
    def __init__(self, duration_hours=792):
        self.duration = duration_hours * 3600
        self.start_time = time.time()
        os.makedirs(LOG_DIR, exist_ok=True)

        # Overwrite old log on fresh start
        if os.path.exists(MONITOR_LOG):
            os.remove(MONITOR_LOG)

        # Write PID file for status checks
        with open(MONITOR_PID, "w") as f:
            f.write(str(os.getpid()))

        # State tracking
        self.prev_processes = {}       # pattern -> {pid, cmdline}
        self.prev_mmap_state = {}      # filename -> {size, mtime_ns}
        self.prev_log_sizes = {}       # filepath -> size
        self.prev_state_hash = ""
        self.prev_fill_count = 0
        self.last_fill_id = 0
        self.cycle = 0

        print(f"🔬 System Monitor v1.0 started")
        print(f"   Duration: {duration_hours}h ({self.duration}s)")
        print(f"   Output:   {MONITOR_LOG}")
        print(f"   Polling:  every {POLL_INTERVAL}s")
        print(f"   Watching: {len(WATCH_PATTERNS)} process patterns")
        print(f"   PnL DB:   {PNL_DB}")
        print(f"   mmap:     {MMAP_DIR}/")
        print()

    def log_event(self, category: str, event_type: str, data: dict):
        """Append a timestamped event to the JSONL log."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ts_local": datetime.now().strftime("%H:%M:%S"),
            "cycle": self.cycle,
            "elapsed_s": round(time.time() - self.start_time, 1),
            "cat": category,
            "type": event_type,
            **data,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with open(MONITOR_LOG, "a") as f:
            f.write(line + "\n")
        # Also print important events
        if event_type in ("BORN", "DIED", "TRADE", "STATE_CHANGE", "MMAP_NEW", "MMAP_GONE"):
            icon = {"BORN": "🟢", "DIED": "💀", "TRADE": "💰", "STATE_CHANGE": "🔄",
                     "MMAP_NEW": "📦", "MMAP_GONE": "🗑️"}.get(event_type, "📝")
            print(f"  {entry['ts_local']} {icon} [{category}] {event_type}: {json.dumps(data, ensure_ascii=False)[:120]}")

    # ── Process Monitor ──
    def scan_processes(self):
        """Detect process births and deaths."""
        current = {}
        for pattern in WATCH_PATTERNS:
            try:
                r = subprocess.run(
                    ["pgrep", "-a", "-f", pattern],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        if not line.strip():
                            continue
                        parts = line.split(None, 1)
                        pid = parts[0]
                        cmd = parts[1] if len(parts) > 1 else pattern
                        # Skip our own monitor and grep itself
                        if "system_monitor" in cmd or "pgrep" in cmd:
                            continue
                        key = f"{pattern}:{pid}"
                        current[key] = {"pid": pid, "cmd": cmd[:200], "pattern": pattern}
            except Exception:
                pass

        # Detect births
        for key, info in current.items():
            if key not in self.prev_processes:
                self.log_event("PROCESS", "BORN", {
                    "pid": info["pid"],
                    "pattern": info["pattern"],
                    "cmd": info["cmd"],
                })

        # Detect deaths
        for key, info in self.prev_processes.items():
            if key not in current:
                self.log_event("PROCESS", "DIED", {
                    "pid": info["pid"],
                    "pattern": info["pattern"],
                    "cmd": info["cmd"],
                })

        # Periodic full snapshot every 60 cycles (5 min)
        if self.cycle % 60 == 0:
            alive = {}
            for key, info in current.items():
                p = info["pattern"]
                if p not in alive:
                    alive[p] = []
                alive[p].append(info["pid"])
            self.log_event("PROCESS", "SNAPSHOT", {"alive": alive, "total": len(current)})

        self.prev_processes = current

    # ── mmap Monitor ──
    def scan_mmap(self):
        """Track mmap file creation, deletion, and size changes."""
        current = {}
        if os.path.isdir(MMAP_DIR):
            for fname in os.listdir(MMAP_DIR):
                fpath = os.path.join(MMAP_DIR, fname)
                try:
                    st = os.stat(fpath)
                    current[fname] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
                except Exception:
                    pass

        # New files
        for fname, info in current.items():
            if fname not in self.prev_mmap_state:
                self.log_event("MMAP", "MMAP_NEW", {"file": fname, "size": info["size"]})

        # Deleted files
        for fname in self.prev_mmap_state:
            if fname not in current:
                self.log_event("MMAP", "MMAP_GONE", {"file": fname})

        # Size changes (not every cycle — every 12 cycles = 1 min)
        if self.cycle % 12 == 0 and current:
            changed = {}
            for fname, info in current.items():
                prev = self.prev_mmap_state.get(fname)
                if prev and prev["size"] != info["size"]:
                    changed[fname] = {"old": prev["size"], "new": info["size"]}
            if changed:
                self.log_event("MMAP", "SIZE_CHANGE", {"files": changed})

        self.prev_mmap_state = current

    # ── Trade/Fill Monitor ──
    def scan_trades(self):
        """Watch pnl.db for new fills."""
        if not os.path.exists(PNL_DB):
            return
        try:
            conn = sqlite3.connect(f"file:{PNL_DB}?mode=ro", uri=True, timeout=2)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Get new fills since last check
            cur.execute(
                "SELECT rowid, * FROM fills WHERE rowid > ? ORDER BY rowid LIMIT 50",
                (self.last_fill_id,)
            )
            rows = cur.fetchall()
            for row in rows:
                d = dict(row)
                rid = d.pop("rowid", 0)
                self.last_fill_id = max(self.last_fill_id, rid)
                # Trim large fields
                for k in list(d.keys()):
                    if isinstance(d[k], str) and len(d[k]) > 100:
                        d[k] = d[k][:100] + "..."
                self.log_event("TRADE", "TRADE", d)

            # Periodic fill count
            if self.cycle % 12 == 0:
                cur.execute("SELECT COUNT(*) FROM fills")
                count = cur.fetchone()[0]
                if count != self.prev_fill_count:
                    self.log_event("TRADE", "FILL_COUNT", {
                        "total": count,
                        "delta": count - self.prev_fill_count,
                    })
                    self.prev_fill_count = count

            conn.close()
        except Exception as e:
            if self.cycle % 60 == 0:
                self.log_event("TRADE", "DB_ERROR", {"error": str(e)})

    # ── State File Monitor ──
    def scan_state(self):
        """Watch armada_state.json for changes."""
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r") as f:
                content = f.read()
            h = hashlib.md5(content.encode()).hexdigest()
            if h != self.prev_state_hash and self.prev_state_hash:
                try:
                    data = json.loads(content)
                except Exception:
                    data = {"raw": content[:500]}
                self.log_event("STATE", "STATE_CHANGE", {"state": data})
            self.prev_state_hash = h
        except Exception:
            pass

    # ── Log Growth Monitor ──
    def scan_logs(self):
        """Track log file growth rates (every 60s)."""
        if self.cycle % 12 != 0:
            return
        current = {}
        for pattern in LOG_PATTERNS:
            import glob
            for fpath in glob.glob(os.path.join(PROJECT_ROOT, pattern)):
                try:
                    current[fpath] = os.path.getsize(fpath)
                except Exception:
                    pass
        
        if self.prev_log_sizes:
            growing = {}
            for fpath, size in current.items():
                prev = self.prev_log_sizes.get(fpath, 0)
                if size > prev:
                    growing[os.path.basename(fpath)] = {
                        "delta_bytes": size - prev,
                        "total_bytes": size,
                    }
            if growing:
                self.log_event("LOGS", "LOG_GROWTH", {"files": growing})

        self.prev_log_sizes = current

    # ── System Resources ──
    def scan_system(self):
        """CPU, RAM snapshot every 5 min."""
        if self.cycle % 60 != 0:
            return
        try:
            load1, load5, load15 = os.getloadavg()
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":")[:2]
                    mem[k.strip()] = int(v.strip().split()[0])
            total = mem.get("MemTotal", 1)
            avail = mem.get("MemAvailable", 0)
            ram_pct = round(100.0 * (total - avail) / total, 1)

            self.log_event("SYSTEM", "RESOURCES", {
                "load": [round(load1, 2), round(load5, 2), round(load15, 2)],
                "ram_pct": ram_pct,
                "ram_used_mb": round((total - avail) / 1024, 0),
            })
        except Exception:
            pass

    # ── L2 Oracle Decisions ──
    def scan_l2(self):
        """Read l2_reasoning.txt for oracle decision changes."""
        if self.cycle % 6 != 0:  # every 30s
            return
        path = os.path.join(MMAP_DIR, "l2_reasoning.txt")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r") as f:
                content = f.read().strip()
            if content and not hasattr(self, "_prev_l2") or (hasattr(self, "_prev_l2") and content != self._prev_l2):
                self.log_event("L2_ORACLE", "DECISION", {"reasoning": content[:500]})
                self._prev_l2 = content
        except Exception:
            pass

    # ── Main Loop ──
    def run(self):
        """Main monitoring loop."""
        self.log_event("MONITOR", "START", {
            "duration_h": self.duration / 3600,
            "poll_s": POLL_INTERVAL,
            "patterns": WATCH_PATTERNS,
            "pid": os.getpid(),
        })

        # Initialize fill tracking
        if os.path.exists(PNL_DB):
            try:
                conn = sqlite3.connect(f"file:{PNL_DB}?mode=ro", uri=True, timeout=2)
                cur = conn.cursor()
                cur.execute("SELECT MAX(rowid) FROM fills")
                r = cur.fetchone()
                self.last_fill_id = r[0] if r and r[0] else 0
                cur.execute("SELECT COUNT(*) FROM fills")
                self.prev_fill_count = cur.fetchone()[0]
                conn.close()
                self.log_event("MONITOR", "INIT_DB", {
                    "last_fill_id": self.last_fill_id,
                    "fill_count": self.prev_fill_count,
                })
            except Exception as e:
                self.log_event("MONITOR", "INIT_DB_ERROR", {"error": str(e)})

        try:
            while time.time() - self.start_time < self.duration:
                self.cycle += 1
                self.scan_processes()
                self.scan_mmap()
                self.scan_trades()
                self.scan_state()
                self.scan_logs()
                self.scan_system()
                self.scan_l2()
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n⏹️  Monitor stopped by user")
        finally:
            elapsed = time.time() - self.start_time
            self.log_event("MONITOR", "STOP", {
                "elapsed_s": round(elapsed, 1),
                "cycles": self.cycle,
                "events_file": MONITOR_LOG,
            })
            # Print summary
            try:
                with open(MONITOR_LOG, "r") as f:
                    lines = f.readlines()
                cats = {}
                for line in lines:
                    try:
                        e = json.loads(line)
                        t = e.get("type", "?")
                        cats[t] = cats.get(t, 0) + 1
                    except Exception:
                        pass
                print(f"\n📊 Monitor Summary ({round(elapsed/60, 1)} min, {self.cycle} cycles):")
                for t, c in sorted(cats.items(), key=lambda x: -x[1]):
                    print(f"   {t}: {c}")
                print(f"   Total events: {sum(cats.values())}")
                print(f"   Log: {MONITOR_LOG}")
            except Exception:
                pass


def get_status():
    """Return monitor status dict for dashboard/telegram."""
    result = {"online": False, "pid": None, "log_size_kb": 0, "events": 0, "uptime_h": 0}
    try:
        if os.path.exists(MONITOR_PID):
            with open(MONITOR_PID) as f:
                pid = int(f.read().strip())
            # Check if process alive
            os.kill(pid, 0)
            result["online"] = True
            result["pid"] = pid
    except (ProcessLookupError, ValueError, FileNotFoundError):
        pass
    try:
        if os.path.exists(MONITOR_LOG):
            st = os.stat(MONITOR_LOG)
            result["log_size_kb"] = round(st.st_size / 1024, 1)
            result["uptime_h"] = round((time.time() - st.st_ctime) / 3600, 1)
            with open(MONITOR_LOG) as f:
                result["events"] = sum(1 for _ in f)
    except Exception:
        pass
    return result


if __name__ == "__main__":
    days = 33
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--days" and i + 2 < len(sys.argv):
            days = float(sys.argv[i + 2])
        elif arg == "--hours" and i + 2 < len(sys.argv):
            days = float(sys.argv[i + 2]) / 24

    monitor = SystemMonitor(duration_hours=days * 24)
    monitor.run()
