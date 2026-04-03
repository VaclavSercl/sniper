#!/usr/bin/env python3
"""
💰 Sniper Armada — FIFO PnL Engine v2.0
Volatility-Neutral Trade-Level Realized PnL Aggregation

Core principles:
  1. ONLY fills (executions) — NEVER wallet balances
  2. FIFO matching (First In, First Out)
  3. USD fixation at the MILLISECOND of execution
  4. Fees tracked separately for transparent breakdown

Used by: pnl_daemon.py (reads fills, computes PnL, writes mmap + SQLite)
"""

import os
import json
import sqlite3
import struct
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

# ── CONSTANTS ──────────────────────────────────────────────
PRICE_SCALE = 100_000_000  # 1e8
DB_DIR = os.path.expanduser("~/.local/share/sniper")
DB_PATH = os.path.join(DB_DIR, "pnl.db")
PNL_MMAP_PATH = "/dev/shm/beroun/pnl_state.bin"

BOT_INDEX = {
    "hydra": 0, "moonshot": 1, "grid": 2, "trigon": 3,
    "nexus": 4, "bot5": 5, "bot6": 6, "bot7": 7,
}

# Cross-pair symbols (profit in BTC, needs USD conversion)
CROSS_PAIRS = {"tETHBTC", "tLTCBTC", "tXRPBTC", "tSOLBTC", "tSOLETH"}


# ═══════════════════════════════════════════════════════════
# SQLite Schema
# ═══════════════════════════════════════════════════════════

SCHEMA_SQL = """
-- Raw fills from Bitfinex "tu" (trade update) events
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    bot TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL,
    fee_currency TEXT DEFAULT 'USD',
    trade_id TEXT UNIQUE,
    order_id TEXT,
    is_closer INTEGER DEFAULT 0,
    matched_entry_price REAL,
    gross_pnl REAL,
    net_pnl REAL,
    numeraire_rate REAL DEFAULT 1.0,
    is_shadow INTEGER DEFAULT 0
);

-- FIFO state per (bot, symbol, is_shadow)
CREATE TABLE IF NOT EXISTS fifo_state (
    bot TEXT NOT NULL,
    symbol TEXT NOT NULL,
    is_shadow INTEGER DEFAULT 0,
    queue_json TEXT DEFAULT '[]',
    total_realized REAL DEFAULT 0,
    total_fees REAL DEFAULT 0,
    total_closed_trades INTEGER DEFAULT 0,
    net_position REAL DEFAULT 0,
    last_fill_ts TEXT,
    PRIMARY KEY (bot, symbol, is_shadow)
);

-- Hourly PnL snapshots for time-window queries
CREATE TABLE IF NOT EXISTS pnl_hourly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ts_hour TEXT NOT NULL,
    bot TEXT NOT NULL,
    is_shadow INTEGER DEFAULT 0,
    realized REAL DEFAULT 0,
    fees REAL DEFAULT 0,
    fills_count INTEGER DEFAULT 0,
    buy_volume REAL DEFAULT 0,
    sell_volume REAL DEFAULT 0
);

-- Wallet snapshots (INFO ONLY, not for Trading PnL)
CREATE TABLE IF NOT EXISTS wallet_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    usd REAL DEFAULT 0,
    btc REAL DEFAULT 0,
    btc_price REAL DEFAULT 0,
    total_usd REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_fills_bot_ts ON fills(bot, ts_ms);
CREATE INDEX IF NOT EXISTS idx_fills_trade ON fills(trade_id);
CREATE INDEX IF NOT EXISTS idx_hourly_bot ON pnl_hourly(bot, ts_hour);
CREATE INDEX IF NOT EXISTS idx_wallet_ts ON wallet_snapshots(ts);
"""


# ═══════════════════════════════════════════════════════════
# FIFO Engine
# ═══════════════════════════════════════════════════════════

class FIFOEngine:
    """
    Per (bot, symbol) FIFO position tracker.
    
    Every BUY fill is pushed to the queue.
    Every SELL fill pops from the front (FIFO) and calculates realized PnL.
    PnL is fixed to USD at the millisecond of the SELL execution.
    """

    def __init__(self, bot: str, symbol: str, is_shadow: bool = False, queue: Optional[list] = None):
        self.bot = bot
        self.symbol = symbol
        self.is_shadow = is_shadow
        self.is_cross = symbol in CROSS_PAIRS
        # Queue entries: [qty_remaining, entry_price, entry_fee, entry_ts_ms]
        self.queue: deque = deque(queue or [])
        self.total_realized = 0.0
        self.total_fees = 0.0
        self.total_closed = 0
        self.net_position = 0.0

    def on_fill(self, side: str, qty: float, price: float,
                fee: float, ts_ms: int, usd_spot: float = 1.0) -> dict:
        """
        Process a single fill.
        
        Args:
            side: "buy" or "sell"
            qty: absolute amount (always positive)
            price: fill price
            fee: fee in USD (or base currency for cross-pairs)
            ts_ms: epoch milliseconds
            usd_spot: BTC/USD spot price at execution time (for cross-pairs)
        
        Returns:
            dict with fill result (net_pnl, gross_pnl, matched_price, etc.)
        """
        result = {
            "side": side,
            "qty": qty,
            "price": price,
            "fee": fee,
            "ts_ms": ts_ms,
            "is_closer": False,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "matched_entry_price": 0.0,
            "numeraire_rate": usd_spot,
        }

        if side == "buy":
            self.queue.append([qty, price, fee, ts_ms])
            self.net_position += qty
            return result

        elif side == "sell":
            remaining = qty
            gross_pnl = 0.0
            total_entry_fees = 0.0
            weighted_entry_price = 0.0
            total_matched = 0.0

            while remaining > 1e-12 and self.queue:
                entry = self.queue[0]
                entry_qty, entry_price, entry_fee, entry_ts = entry

                matched = min(remaining, entry_qty)

                # Proportional entry fee for this portion
                proportional_fee = entry_fee * (matched / entry_qty) if entry_qty > 0 else 0

                # Gross PnL for this FIFO pair
                if self.is_cross:
                    # Cross-pair: PnL is in base currency (BTC/ETH)
                    # Convert to USD at execution time
                    pair_pnl = (price - entry_price) * matched * usd_spot
                    proportional_fee *= usd_spot
                else:
                    # Linear pair: PnL is natively in USD
                    pair_pnl = (price - entry_price) * matched

                gross_pnl += pair_pnl
                total_entry_fees += proportional_fee
                weighted_entry_price += entry_price * matched
                total_matched += matched

                # Consume from FIFO queue
                if matched >= entry_qty - 1e-12:
                    self.queue.popleft()
                else:
                    entry[0] = entry_qty - matched
                    entry[2] = entry_fee - proportional_fee

                remaining -= matched

            # Calculate net PnL (gross - entry fees - exit fee)
            exit_fee = fee * usd_spot if self.is_cross else fee
            net_pnl = gross_pnl - total_entry_fees - exit_fee

            avg_entry = weighted_entry_price / total_matched if total_matched > 0 else 0

            self.total_realized += net_pnl
            self.total_fees += total_entry_fees + exit_fee
            self.total_closed += 1
            self.net_position -= qty

            result.update({
                "is_closer": True,
                "gross_pnl": round(gross_pnl, 8),
                "net_pnl": round(net_pnl, 8),
                "matched_entry_price": round(avg_entry, 2),
                "total_fees": round(total_entry_fees + exit_fee, 8),
                "numeraire_rate": usd_spot,
            })

        return result

    def get_queue_json(self) -> str:
        """Serialize queue for SQLite persistence."""
        return json.dumps(list(self.queue))

    @property
    def fifo_front_price(self) -> float:
        """Price at front of FIFO queue (oldest entry)."""
        return self.queue[0][1] if self.queue else 0.0


# ═══════════════════════════════════════════════════════════
# PnL Database
# ═══════════════════════════════════════════════════════════

class PnlDatabase:
    """SQLite storage for fills, FIFO state, and hourly aggregates."""

    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def insert_fill(self, bot, symbol, side, qty, price, fee, fee_currency,
                    trade_id, order_id, ts, ts_ms, is_closer, matched_price,
                    gross_pnl, net_pnl, numeraire_rate, is_shadow=False):
        """Insert a processed fill with FIFO results."""
        try:
            self.conn.execute("""
                INSERT OR IGNORE INTO fills
                (ts, ts_ms, bot, symbol, side, qty, price, fee, fee_currency,
                 trade_id, order_id, is_closer, matched_entry_price,
                 gross_pnl, net_pnl, numeraire_rate, is_shadow)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (ts, ts_ms, bot, symbol, side, qty, price, fee, fee_currency,
                  trade_id, order_id, is_closer, matched_price,
                  gross_pnl, net_pnl, numeraire_rate, int(is_shadow)))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # Duplicate trade_id

    def save_fifo_state(self, engine: FIFOEngine):
        """Persist FIFO queue state."""
        self.conn.execute("""
            INSERT OR REPLACE INTO fifo_state
            (bot, symbol, is_shadow, queue_json, total_realized, total_fees,
             total_closed_trades, net_position, last_fill_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (engine.bot, engine.symbol, int(engine.is_shadow), engine.get_queue_json(),
              engine.total_realized, engine.total_fees,
              engine.total_closed, engine.net_position,
              datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def load_fifo_state(self, bot: str, symbol: str, is_shadow: bool = False) -> FIFOEngine:
        """Load FIFO state from SQLite."""
        row = self.conn.execute(
            "SELECT queue_json, total_realized, total_fees, total_closed_trades, net_position "
            "FROM fifo_state WHERE bot=? AND symbol=? AND is_shadow=?", (bot, symbol, int(is_shadow))
        ).fetchone()

        engine = FIFOEngine(bot, symbol, is_shadow=is_shadow)
        if row:
            engine.queue = deque(json.loads(row[0]))
            engine.total_realized = row[1]
            engine.total_fees = row[2]
            engine.total_closed = row[3]
            engine.net_position = row[4]
        return engine

    def get_realized_window(self, bot: str, hours: int, is_shadow: bool = False) -> dict:
        """Get aggregated PnL for a time window."""
        cutoff_ms = int((time.time() - hours * 3600) * 1000)
        row = self.conn.execute("""
            SELECT COALESCE(SUM(net_pnl), 0),
                   COALESCE(SUM(CASE WHEN is_closer=1 THEN 
                       COALESCE(
                           (SELECT SUM(f2.fee) FROM fills f2 WHERE f2.trade_id = fills.trade_id),
                           fills.fee
                       ) ELSE fills.fee END), 0),
                   COUNT(*),
                   COALESCE(SUM(CASE WHEN is_closer=1 THEN 1 ELSE 0 END), 0)
            FROM fills WHERE bot=? AND is_shadow=? AND ts_ms>=?
        """, (bot, int(is_shadow), cutoff_ms)).fetchone()

        # Simpler query for fees
        fee_row = self.conn.execute(
            "SELECT COALESCE(SUM(fee), 0) FROM fills WHERE bot=? AND is_shadow=? AND ts_ms>=?",
            (bot, int(is_shadow), cutoff_ms)
        ).fetchone()

        return {
            "realized": row[0] if row else 0,
            "fees": fee_row[0] if fee_row else 0,
            "fills": row[2] if row else 0,
            "closed_trades": row[3] if row else 0,
        }

    def get_all_bots_pnl(self, is_shadow: bool = False) -> dict:
        """Get PnL for all bots across all time windows."""
        windows = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}
        result = {}

        for bot in BOT_INDEX:
            bot_data = {}
            for label, hours in windows.items():
                bot_data[label] = self.get_realized_window(bot, hours, is_shadow=is_shadow)
            result[bot] = bot_data

        return result

    def record_hourly(self, bot: str, is_shadow: bool = False):
        """Aggregate last hour's fills into pnl_hourly."""
        now = datetime.now(timezone.utc)
        hour_key = now.strftime("%Y-%m-%dT%H")
        cutoff_ms = int((now - timedelta(hours=1)).timestamp() * 1000)

        row = self.conn.execute("""
            SELECT COALESCE(SUM(net_pnl), 0),
                   COALESCE(SUM(fee), 0),
                   COUNT(*),
                   COALESCE(SUM(CASE WHEN side='buy' THEN qty ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN side='sell' THEN qty ELSE 0 END), 0)
            FROM fills WHERE bot=? AND is_shadow=? AND ts_ms>=?
        """, (bot, int(is_shadow), cutoff_ms)).fetchone()

        if row and row[2] > 0:
            self.conn.execute("""
                INSERT OR REPLACE INTO pnl_hourly
                (ts, ts_hour, bot, is_shadow, realized, fees, fills_count, buy_volume, sell_volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (now.isoformat(), hour_key, bot, int(is_shadow), row[0], row[1], row[2], row[3], row[4]))
            self.conn.commit()

    def record_wallet(self, usd: float, btc: float, btc_price: float):
        """Record a wallet snapshot."""
        total = usd + btc * btc_price
        self.conn.execute("""
            INSERT INTO wallet_snapshots (ts, usd, btc, btc_price, total_usd)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now(timezone.utc).isoformat(), usd, btc, btc_price, total))
        self.conn.commit()

    def get_wallet_delta(self, hours: int) -> float:
        """Get wallet value change over N hours."""
        now_row = self.conn.execute(
            "SELECT total_usd FROM wallet_snapshots ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if not now_row:
            return 0.0

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        old_row = self.conn.execute(
            "SELECT total_usd FROM wallet_snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
            (cutoff,)
        ).fetchone()

        if not old_row:
            return 0.0

        return now_row[0] - old_row[0]

    def cleanup_old(self, days: int = 90):
        """Remove fills older than N days."""
        cutoff_ms = int((time.time() - days * 86400) * 1000)
        self.conn.execute("DELETE FROM fills WHERE ts_ms < ?", (cutoff_ms,))
        cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        self.conn.execute("DELETE FROM pnl_hourly WHERE ts < ?", (cutoff_ts,))
        self.conn.execute("DELETE FROM wallet_snapshots WHERE ts < ?", (cutoff_ts,))
        self.conn.commit()

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════════════
# mmap Writer
# ═══════════════════════════════════════════════════════════

class PnlMmapWriter:
    """Write PnL data to mmap for real-time dashboard consumption."""

    # Offsets calculated from PnlBotState struct (all i64/u64 = 8 bytes, u32 = 4 bytes)
    # PnlBotState size: aligned to 64 bytes, let's calculate:
    #   realized_1h(8) + realized_24h(8) + realized_7d(8) + realized_30d(8) = 32
    #   fees_1h(8) + fees_24h(8) = 16
    #   fills_1h(4) + fills_24h(4) + closed_24h(4) + pad(4) + total_closed(8) = 24
    #   net_position(8) + fifo_front(8) + mark_price(8) + inventory_pnl(8) = 32
    #   avg_pnl(8) + avg_fee(8) + last_fill_ms(8) + name_hash(8) + pad(24) = 56
    # Total: 32+16+24+32+56 = 160 bytes → round to 192 (3×64 cache lines)
    BOT_STATE_SIZE = 192
    GLOBAL_OFFSET = BOT_STATE_SIZE * 16  # after 16 bot states

    def __init__(self, path: str = PNL_MMAP_PATH):
        import mmap as mmap_module
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Total size: 16 bots × 192 + global section (128 bytes) = 3200
        total_size = self.GLOBAL_OFFSET + 128
        self.f = open(path, "r+b" if os.path.exists(path) else "w+b")
        self.f.seek(total_size - 1)
        self.f.write(b'\0')
        self.f.flush()
        self.mm = mmap_module.mmap(self.f.fileno(), total_size)

    def write_bot(self, bot_idx: int, data: dict):
        """Write per-bot PnL data to mmap."""
        base = bot_idx * self.BOT_STATE_SIZE
        self._write_i64(base + 0, data.get("realized_1h", 0))
        self._write_i64(base + 8, data.get("realized_24h", 0))
        self._write_i64(base + 16, data.get("realized_7d", 0))
        self._write_i64(base + 24, data.get("realized_30d", 0))
        self._write_i64(base + 32, data.get("fees_1h", 0))
        self._write_i64(base + 40, data.get("fees_24h", 0))
        self._write_u32(base + 48, data.get("fills_1h", 0))
        self._write_u32(base + 52, data.get("fills_24h", 0))
        self._write_u32(base + 56, data.get("closed_trades_24h", 0))
        # pad 4 bytes at 60
        self._write_u64(base + 64, data.get("total_closed", 0))
        self._write_i64(base + 72, data.get("net_position", 0))
        self._write_u64(base + 80, data.get("fifo_front_price", 0))
        self._write_u64(base + 88, data.get("mark_price", 0))
        self._write_i64(base + 96, data.get("inventory_pnl", 0))
        self._write_i64(base + 104, data.get("avg_pnl_per_trade", 0))
        self._write_i64(base + 112, data.get("avg_fee_per_fill", 0))
        self._write_u64(base + 120, data.get("last_fill_ms", 0))

    def write_global(self, data: dict):
        """Write global totals + wallet data."""
        base = self.GLOBAL_OFFSET
        self._write_i64(base + 0, data.get("total_realized_1h", 0))
        self._write_i64(base + 8, data.get("total_realized_24h", 0))
        self._write_i64(base + 16, data.get("total_realized_7d", 0))
        self._write_i64(base + 24, data.get("total_realized_30d", 0))
        self._write_i64(base + 32, data.get("total_fees_24h", 0))
        self._write_u32(base + 40, data.get("total_fills_24h", 0))
        # Wallet
        self._write_i64(base + 48, data.get("wallet_usd", 0))
        self._write_i64(base + 56, data.get("wallet_btc", 0))
        self._write_i64(base + 64, data.get("wallet_total_usd", 0))
        self._write_i64(base + 72, data.get("wallet_delta_1h", 0))
        self._write_i64(base + 80, data.get("wallet_delta_24h", 0))
        self._write_u64(base + 88, int(time.time() * 1000))  # heartbeat

    def _write_i64(self, offset, value):
        v = int(value * PRICE_SCALE) if isinstance(value, float) else int(value)
        struct.pack_into("<q", self.mm, offset, v)

    def _write_u64(self, offset, value):
        struct.pack_into("<Q", self.mm, offset, int(value))

    def _write_u32(self, offset, value):
        struct.pack_into("<I", self.mm, offset, int(value))

    def close(self):
        self.mm.close()
        self.f.close()


# ═══════════════════════════════════════════════════════════
# Convenience functions
# ═══════════════════════════════════════════════════════════

def format_pnl(value: float) -> str:
    """Format PnL value with color hint."""
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:.4f}"

def format_pnl_short(value: float) -> str:
    """Short format for Telegram."""
    sign = "+" if value >= 0 else ""
    if abs(value) >= 1000:
        return f"{sign}${value:,.0f}"
    elif abs(value) >= 1:
        return f"{sign}${value:.2f}"
    else:
        return f"{sign}${value:.4f}"
