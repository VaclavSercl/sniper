"""
🧪 A/B Testing Framework — ML Shield Validation
Sniper Armada · Phase 8 · v18.0

Hodinové střídání ML Shield ON/OFF s měřením PnL, toxic fills,
Sharpe ratio a statistickou analýzou (Welchův t-test).

Integrace:
- Telegram: /ab, /ab start, /ab stop, /ab history
- Dashboard: JSON data via dashboard_server.py
- mmap: Přepisuje l1_skew_adjustment v OFF hodinách
"""

import os
import sys
import json
import time
import struct
import mmap
import sqlite3
import math
import threading
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger("ab_testing")

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
ENGINE_STATE_PATH = "/dev/shm/beroun/engine_state.bin"
PRICE_SCALE = 100_000_000
AB_DB_PATH = os.path.join(PROJECT_ROOT, 'state', 'ab_test.db')

# mmap offsets (from types.rs)
OFF_L1_SKEW = 1584       # i64 l1_skew_adjustment
OFF_L1_CONF = 1600       # u64 l1_confidence_score

# Test parameters
TEST_DURATION_DAYS = 7
SESSION_DURATION_HOURS = 1
MIN_FILLS_FOR_SIGNIFICANCE = 50


# ═══════════════════════════════════════════════════════════
# SQLite Schema
# ═══════════════════════════════════════════════════════════

def _init_db():
    """Create A/B testing tables if they don't exist."""
    os.makedirs(os.path.dirname(AB_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(AB_DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ab_tests (
            id INTEGER PRIMARY KEY,
            start_ts TEXT NOT NULL,
            end_ts TEXT,
            status TEXT DEFAULT 'RUNNING',  -- RUNNING, COMPLETED, STOPPED
            verdict TEXT DEFAULT '',
            total_fills INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS ab_sessions (
            id INTEGER PRIMARY KEY,
            test_id INTEGER REFERENCES ab_tests(id),
            start_ts TEXT NOT NULL,
            end_ts TEXT,
            ml_enabled INTEGER NOT NULL,  -- 0=OFF, 1=ON
            fills INTEGER DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            toxic_fills INTEGER DEFAULT 0,
            total_volume REAL DEFAULT 0,
            avg_spread_captured REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS ab_fills (
            id INTEGER PRIMARY KEY,
            session_id INTEGER REFERENCES ab_sessions(id),
            timestamp TEXT NOT NULL,
            bot TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            amount REAL NOT NULL,
            pnl REAL DEFAULT 0,
            is_toxic INTEGER DEFAULT 0,
            ml_skew REAL DEFAULT 0,
            ml_confidence REAL DEFAULT 0
        );
    """)
    conn.close()
    return AB_DB_PATH


# ═══════════════════════════════════════════════════════════
# ABController — Toggle ML ON/OFF
# ═══════════════════════════════════════════════════════════

class ABController:
    """Controls ML Shield ON/OFF toggling and session management."""

    def __init__(self):
        self._running = False
        self._test_id = None
        self._current_session_id = None
        self._thread = None
        self._ml_enabled = True
        _init_db()

    @property
    def is_running(self):
        return self._running

    @property
    def ml_enabled(self):
        return self._ml_enabled

    def start_test(self):
        """Start a new A/B test (7 days)."""
        if self._running:
            return "⚠️ A/B test is already running"

        conn = sqlite3.connect(AB_DB_PATH)
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO ab_tests (start_ts) VALUES (?)", (now,)
        )
        self._test_id = cur.lastrowid
        conn.commit()
        conn.close()

        self._running = True
        self._thread = threading.Thread(
            target=self._toggle_loop, daemon=True, name="ab-controller"
        )
        self._thread.start()
        log.info(f"🧪 A/B test #{self._test_id} started")
        return f"🧪 A/B test #{self._test_id} started! Duration: {TEST_DURATION_DAYS} days"

    def stop_test(self):
        """Stop the current A/B test and generate report."""
        if not self._running:
            return "ℹ️ No A/B test running"

        self._running = False
        self._restore_ml()

        conn = sqlite3.connect(AB_DB_PATH)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE ab_tests SET end_ts=?, status='STOPPED' WHERE id=?",
            (now, self._test_id)
        )
        conn.commit()
        conn.close()

        report = self.generate_report()
        log.info(f"🧪 A/B test #{self._test_id} stopped")
        return report

    def _toggle_loop(self):
        """Main loop: toggle ML ON/OFF every hour."""
        start_time = time.time()
        end_time = start_time + TEST_DURATION_DAYS * 86400

        while self._running and time.time() < end_time:
            hour = datetime.now(timezone.utc).hour
            ml_on = (hour % 2 == 0)  # Even hours = ON

            self._ml_enabled = ml_on
            self._start_session(ml_on)

            if not ml_on:
                self._suppress_ml()
            else:
                self._restore_ml()

            log.info(f"🧪 Session: ML {'ON' if ml_on else 'OFF'} (hour {hour})")

            # Wait until next hour
            now = datetime.now(timezone.utc)
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            sleep_sec = (next_hour - now).total_seconds()

            # Sleep in 30s chunks to allow clean shutdown
            elapsed = 0
            while elapsed < sleep_sec and self._running:
                time.sleep(min(30, sleep_sec - elapsed))
                elapsed += 30
                self._collect_fills()

        # Test complete
        if self._running:
            self._running = False
            self._restore_ml()
            conn = sqlite3.connect(AB_DB_PATH)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE ab_tests SET end_ts=?, status='COMPLETED' WHERE id=?",
                (now, self._test_id)
            )
            conn.commit()
            conn.close()
            log.info(f"🧪 A/B test #{self._test_id} COMPLETED after {TEST_DURATION_DAYS} days")

    def _start_session(self, ml_on):
        """Create a new session record."""
        conn = sqlite3.connect(AB_DB_PATH)
        now = datetime.now(timezone.utc).isoformat()

        # Close previous session
        if self._current_session_id:
            conn.execute(
                "UPDATE ab_sessions SET end_ts=? WHERE id=?",
                (now, self._current_session_id)
            )

        cur = conn.execute(
            "INSERT INTO ab_sessions (test_id, start_ts, ml_enabled) VALUES (?, ?, ?)",
            (self._test_id, now, 1 if ml_on else 0)
        )
        self._current_session_id = cur.lastrowid
        conn.commit()
        conn.close()

    def _suppress_ml(self):
        """Write zeros to ML fields in engine_state mmap."""
        try:
            if not os.path.exists(ENGINE_STATE_PATH):
                return
            with open(ENGINE_STATE_PATH, 'r+b') as f:
                mm = mmap.mmap(f.fileno(), 0)
                if mm.size() > OFF_L1_CONF + 8:
                    struct.pack_into('<q', mm, OFF_L1_SKEW, 0)
                    struct.pack_into('<Q', mm, OFF_L1_CONF, 0)
                mm.close()
        except Exception as e:
            log.debug(f"ML suppress error: {e}")

    def _restore_ml(self):
        """Stop suppressing — ML Shield will overwrite on next cycle."""
        pass  # ML Shield writes every 50ms, so it auto-restores

    def _collect_fills(self):
        """Read new fills from pnl.db and tag them with current session."""
        if not self._current_session_id:
            return

        try:
            pnl_path = os.path.join(PROJECT_ROOT, 'state', 'pnl.db')
            if not os.path.exists(pnl_path):
                return

            pnl_conn = sqlite3.connect(pnl_path)
            ab_conn = sqlite3.connect(AB_DB_PATH)

            # Get last collected fill timestamp for this session
            row = ab_conn.execute(
                "SELECT MAX(timestamp) FROM ab_fills WHERE session_id=?",
                (self._current_session_id,)
            ).fetchone()
            last_ts = row[0] if row and row[0] else '2000-01-01'

            # Read new fills from pnl.db
            fills = pnl_conn.execute(
                "SELECT timestamp, bot, side, price, amount, pnl FROM fills WHERE timestamp > ? ORDER BY timestamp",
                (last_ts,)
            ).fetchall()

            # Read current ML state
            ml_skew = 0.0
            ml_conf = 0.0
            try:
                if os.path.exists(ENGINE_STATE_PATH):
                    with open(ENGINE_STATE_PATH, 'rb') as f:
                        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                        if mm.size() > OFF_L1_CONF + 8:
                            ml_skew = struct.unpack_from('<q', mm, OFF_L1_SKEW)[0] / PRICE_SCALE
                            ml_conf = struct.unpack_from('<Q', mm, OFF_L1_CONF)[0] / 10000
                        mm.close()
            except Exception:
                pass

            for f in fills:
                ts, bot, side, price, amount, pnl = f
                # Simple toxic detection: negative PnL on roundtrip
                is_toxic = 1 if pnl and pnl < -0.5 else 0

                ab_conn.execute(
                    """INSERT INTO ab_fills
                    (session_id, timestamp, bot, side, price, amount, pnl, is_toxic, ml_skew, ml_confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self._current_session_id, ts, bot, side, price, amount,
                     pnl or 0, is_toxic, ml_skew, ml_conf)
                )

            # Update session stats
            if fills:
                total_pnl = sum(f[5] or 0 for f in fills)
                total_fills = len(fills)
                toxic_count = sum(1 for f in fills if f[5] and f[5] < -0.5)

                ab_conn.execute(
                    """UPDATE ab_sessions SET
                    fills = fills + ?,
                    realized_pnl = realized_pnl + ?,
                    toxic_fills = toxic_fills + ?
                    WHERE id = ?""",
                    (total_fills, total_pnl, toxic_count, self._current_session_id)
                )

            ab_conn.commit()
            ab_conn.close()
            pnl_conn.close()

        except Exception as e:
            log.debug(f"Fill collection error: {e}")

    def generate_report(self, test_id=None):
        """Generate A/B test report."""
        tid = test_id or self._test_id
        if not tid:
            return "ℹ️ No A/B test data"

        return ABAnalyzer.report(tid)

    def get_dashboard_data(self):
        """Return JSON-serializable data for dashboard panel."""
        if not self._test_id:
            return {"running": False}
        return ABAnalyzer.dashboard_data(self._test_id, self._ml_enabled)


# ═══════════════════════════════════════════════════════════
# ABAnalyzer — Statistical Analysis
# ═══════════════════════════════════════════════════════════

class ABAnalyzer:

    @staticmethod
    def _get_group_stats(test_id):
        """Get aggregated stats for ON and OFF groups."""
        conn = sqlite3.connect(AB_DB_PATH)

        groups = {}
        for ml in [0, 1]:
            rows = conn.execute(
                """SELECT fills, realized_pnl, toxic_fills
                FROM ab_sessions WHERE test_id=? AND ml_enabled=?
                AND fills > 0""",
                (test_id, ml)
            ).fetchall()

            if not rows:
                groups[ml] = {
                    'sessions': 0, 'total_fills': 0, 'total_pnl': 0,
                    'toxic_fills': 0, 'pnl_values': [], 'toxic_rate': 0,
                }
                continue

            total_fills = sum(r[0] for r in rows)
            total_pnl = sum(r[1] for r in rows)
            toxic = sum(r[2] for r in rows)
            pnl_values = [r[1] for r in rows]

            groups[ml] = {
                'sessions': len(rows),
                'total_fills': total_fills,
                'total_pnl': round(total_pnl, 4),
                'toxic_fills': toxic,
                'toxic_rate': round(toxic / total_fills * 100, 1) if total_fills > 0 else 0,
                'pnl_values': pnl_values,
                'avg_pnl': round(total_pnl / len(rows), 4) if rows else 0,
            }

        conn.close()
        return groups

    @staticmethod
    def _sharpe(values):
        """Calculate Sharpe ratio from PnL values."""
        if len(values) < 2:
            return 0
        mean = sum(values) / len(values)
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
        return round(mean / std, 2) if std > 0 else 0

    @staticmethod
    def _welch_t_test(a, b):
        """Welch's t-test without scipy."""
        if len(a) < 2 or len(b) < 2:
            return 1.0  # Not significant

        mean_a = sum(a) / len(a)
        mean_b = sum(b) / len(b)
        var_a = sum((x - mean_a) ** 2 for x in a) / (len(a) - 1)
        var_b = sum((x - mean_b) ** 2 for x in b) / (len(b) - 1)

        se = math.sqrt(var_a / len(a) + var_b / len(b))
        if se == 0:
            return 1.0

        t_stat = (mean_a - mean_b) / se

        # Approximate p-value using normal distribution (good for n > 30)
        # For smaller n, this is conservative
        z = abs(t_stat)
        # Abramowitz & Stegun approximation
        p = math.exp(-0.5 * z * z) / (z * math.sqrt(2 * math.pi) + 1e-10)
        p = min(p * 2, 1.0)  # Two-tailed
        return round(p, 4)

    @staticmethod
    def _cohens_d(a, b):
        """Calculate Cohen's d effect size."""
        if len(a) < 2 or len(b) < 2:
            return 0
        mean_a = sum(a) / len(a)
        mean_b = sum(b) / len(b)
        var_a = sum((x - mean_a) ** 2 for x in a) / (len(a) - 1)
        var_b = sum((x - mean_b) ** 2 for x in b) / (len(b) - 1)
        pooled_std = math.sqrt((var_a + var_b) / 2)
        return round((mean_a - mean_b) / pooled_std, 2) if pooled_std > 0 else 0

    @staticmethod
    def report(test_id):
        """Generate Telegram-formatted A/B report."""
        groups = ABAnalyzer._get_group_stats(test_id)
        on = groups.get(1, {})
        off = groups.get(0, {})

        conn = sqlite3.connect(AB_DB_PATH)
        test = conn.execute(
            "SELECT start_ts, status FROM ab_tests WHERE id=?", (test_id,)
        ).fetchone()
        conn.close()

        if not test:
            return "ℹ️ Test not found"

        start = test[0][:10]
        status = test[1]
        total = on.get('total_fills', 0) + off.get('total_fills', 0)

        lines = [
            "🧪 *A/B TEST — ML Shield*",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"📊 Status: {status} (od {start})",
            f"📈 Fills: {total} ({on.get('total_fills',0)} ON / {off.get('total_fills',0)} OFF)",
            "",
            "```",
            f"{'':12s} {'ML ON':>10s} {'ML OFF':>10s} {'Δ':>10s}",
            f"{'PnL':12s} {'$'+str(on.get('total_pnl',0)):>10s} {'$'+str(off.get('total_pnl',0)):>10s}",
            f"{'Toxic':12s} {str(on.get('toxic_rate',0))+'%':>10s} {str(off.get('toxic_rate',0))+'%':>10s}",
        ]

        # Sharpe
        sharpe_on = ABAnalyzer._sharpe(on.get('pnl_values', []))
        sharpe_off = ABAnalyzer._sharpe(off.get('pnl_values', []))
        lines.append(f"{'Sharpe':12s} {str(sharpe_on):>10s} {str(sharpe_off):>10s}")
        lines.append("```")

        # Statistical significance
        pnl_on = on.get('pnl_values', [])
        pnl_off = off.get('pnl_values', [])

        if len(pnl_on) >= 5 and len(pnl_off) >= 5:
            p = ABAnalyzer._welch_t_test(pnl_on, pnl_off)
            d = ABAnalyzer._cohens_d(pnl_on, pnl_off)
            sig = "✅ Significant" if p < 0.05 else "⏳ Not yet"
            lines.append(f"\np-value: `{p}` {sig}")
            lines.append(f"Effect size: d=`{d}`")

            if p < 0.05:
                if on.get('avg_pnl', 0) > off.get('avg_pnl', 0):
                    lines.append("\n🏆 *VERDICT: ML Shield ZLEPŠUJE výkon*")
                else:
                    lines.append("\n⚠️ *VERDICT: ML Shield ZHORŠUJE výkon*")
        else:
            lines.append(f"\n⏳ Potřeba více dat ({len(pnl_on)}+{len(pnl_off)} sessions, min 5+5)")

        return "\n".join(lines)

    @staticmethod
    def dashboard_data(test_id, ml_currently_on):
        """Return JSON for dashboard panel."""
        groups = ABAnalyzer._get_group_stats(test_id)
        on = groups.get(1, {})
        off = groups.get(0, {})

        pnl_on = on.get('pnl_values', [])
        pnl_off = off.get('pnl_values', [])
        p = ABAnalyzer._welch_t_test(pnl_on, pnl_off) if len(pnl_on) >= 5 and len(pnl_off) >= 5 else 1.0

        return {
            "running": True,
            "ml_on_now": ml_currently_on,
            "on_pnl": on.get('total_pnl', 0),
            "off_pnl": off.get('total_pnl', 0),
            "on_fills": on.get('total_fills', 0),
            "off_fills": off.get('total_fills', 0),
            "on_toxic": on.get('toxic_rate', 0),
            "off_toxic": off.get('toxic_rate', 0),
            "on_sharpe": ABAnalyzer._sharpe(pnl_on),
            "off_sharpe": ABAnalyzer._sharpe(pnl_off),
            "p_value": p,
            "significant": p < 0.05,
            "sessions": on.get('sessions', 0) + off.get('sessions', 0),
        }


# ═══════════════════════════════════════════════════════════
# Historical Analysis (Variant 1 — Pre/Post ML deployment)
# ═══════════════════════════════════════════════════════════

def analyze_historical(ml_deploy_date="2026-03-30"):
    """Compare PnL before (no ML) and after (with ML) deployment."""
    pnl_path = os.path.join(PROJECT_ROOT, 'state', 'pnl.db')
    if not os.path.exists(pnl_path):
        return "❌ pnl.db not found"

    conn = sqlite3.connect(pnl_path)

    before = conn.execute(
        "SELECT COUNT(*), SUM(pnl), AVG(pnl) FROM fills WHERE timestamp < ?",
        (ml_deploy_date,)
    ).fetchone()

    after = conn.execute(
        "SELECT COUNT(*), SUM(pnl), AVG(pnl) FROM fills WHERE timestamp >= ?",
        (ml_deploy_date,)
    ).fetchone()

    conn.close()

    b_fills, b_pnl, b_avg = before[0] or 0, before[1] or 0, before[2] or 0
    a_fills, a_pnl, a_avg = after[0] or 0, after[1] or 0, after[2] or 0

    lines = [
        "📊 *HISTORICAL A/B (Pre/Post ML Shield)*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"Deployment: {ml_deploy_date}",
        "",
        "```",
        f"{'':12s} {'Pre-ML':>10s} {'Post-ML':>10s}",
        f"{'Fills':12s} {b_fills:>10d} {a_fills:>10d}",
        f"{'Total PnL':12s} {'$'+f'{b_pnl:.2f}':>10s} {'$'+f'{a_pnl:.2f}':>10s}",
        f"{'Avg PnL':12s} {'$'+f'{b_avg:.4f}':>10s} {'$'+f'{a_avg:.4f}':>10s}",
        "```",
    ]

    if a_avg > b_avg:
        lines.append(f"\n📈 Post-ML avg PnL je lepší o `${a_avg - b_avg:.4f}` per fill")
    else:
        lines.append(f"\n📉 Post-ML avg PnL je horší o `${b_avg - a_avg:.4f}` per fill")

    lines.append("\n_⚠️ Historická analýza — market conditions se liší!_")
    return "\n".join(lines)


# Global instance
ab_controller = ABController()
