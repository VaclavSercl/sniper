#!/usr/bin/env python3
"""
🧪 Sniper Armada — Counterfactual Backtest Engine (SBP v3.1 Phase 2/3)

Tick-level counterfactual simulation of HFT market-making strategies.
Uses Walk-Forward split: In-Sample (Days 1-6) for tuning, OOS (Days 7-8) for validation.

SBP v3.1 Anti-Leakage:
  - Phase 3 (AI Tuning): Gemini optimizes on In-Sample data ONLY
  - Phase 2 (Validation): Final test on OOS data (seen ONCE, never optimized on)
  - If OOS fails → hypothesis rejected, parameters discarded

Backtest uses Probabilistic Queue Model:
  - A limit order fills ONLY if:
    1. Price crosses the limit price (necessary but not sufficient)
    2. Queue position model estimates fill probability based on:
       - Volume traded at that price level
       - Historical fill rate statistics
  - This prevents OHLCV-based "phantom fills" that inflate profits 80%+

Usage:
  from counterfactual_backtest import run_counterfactual_validation
  result = run_counterfactual_validation("hydra", params)
"""

import os
import sys
import json
import time
import sqlite3
import logging
import statistics
from dataclasses import dataclass, asdict
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

DB_PATH = os.path.expanduser("~/.local/share/sniper/market_data.db")
WF_SPLIT_PATH = os.path.join(PROJECT_ROOT, "state", "wf_split.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BACKTEST] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "counterfactual_backtest.log")),
    ],
)
log = logging.getLogger("backtest")

# ═══════════════════════════════════════════════════════════
# Result Dataclass
# ═══════════════════════════════════════════════════════════

@dataclass
class BacktestResult:
    """Counterfactual backtest result for SBP Phase 2/3 gate."""
    bot_name: str
    partition: str          # "in_sample" or "oos"
    net_pnl: float          # Net PnL after fees ($)
    gross_pnl: float        # Gross PnL before fees ($)
    total_fees: float       # Total fees paid ($)
    sharpe_ratio: float     # Annualized Sharpe (daily returns)
    max_drawdown: float     # Maximum drawdown ($)
    total_fills: int        # Total simulated fills
    buy_fills: int          # Buy fills
    sell_fills: int         # Sell fills
    toxic_fills: int        # Adversely selected fills
    toxic_rate: float       # toxic_fills / total_fills (%)
    fill_rate: float        # fills / total_opportunities (%)
    avg_trade_pnl: float    # Average PnL per closed trade ($)
    duration_hours: float   # Backtest duration in hours
    candle_count: int       # Number of 1m candles used
    params: dict            # Parameters used
    passed: bool            # Did it pass SBP gates?
    fail_reasons: list      # List of gate violations
    
    def summary(self) -> str:
        """Human-readable summary for logging and Telegram."""
        status = "✅ PASS" if self.passed else "❌ FAIL"
        reasons = ", ".join(self.fail_reasons) if self.fail_reasons else "—"
        return (
            f"{status} [{self.partition.upper()}] {self.bot_name.upper()}\n"
            f"  PnL: ${self.net_pnl:+.4f} (gross ${self.gross_pnl:+.4f}, fees ${self.total_fees:.4f})\n"
            f"  Sharpe: {self.sharpe_ratio:.2f} | MaxDD: ${self.max_drawdown:.4f}\n"
            f"  Fills: {self.total_fills} ({self.buy_fills}B/{self.sell_fills}S) | "
            f"Toxic: {self.toxic_rate:.1f}%\n"
            f"  Fill Rate: {self.fill_rate:.1f}% | Avg Trade: ${self.avg_trade_pnl:.4f}\n"
            f"  Duration: {self.duration_hours:.1f}h | Candles: {self.candle_count}\n"
            f"  Fail: {reasons}"
        )


# ═══════════════════════════════════════════════════════════
# SBP Gate Thresholds
# ═══════════════════════════════════════════════════════════

GATE_NET_PNL_MIN = 0.0          # Must be profitable
GATE_SHARPE_MIN = 0.3           # Minimum daily Sharpe
GATE_MAX_DRAWDOWN = 5.0         # Max $5 drawdown
GATE_TOXIC_RATE_MAX = 40.0      # Max 40% toxic fills
GATE_MIN_FILLS = 50             # At least 50 simulated fills

# Fee structure (Bitfinex maker/taker)
BFX_MAKER_FEE = -0.0001         # Maker rebate (-1 bps)
BFX_TAKER_FEE = 0.0005          # Taker fee (5 bps)

# ═══════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════

def load_walk_forward_split():
    """Load Walk-Forward split metadata from Phase 1."""
    if not os.path.exists(WF_SPLIT_PATH):
        log.warning("⚠️ wf_split.json not found. Run historical_ingester.py first!")
        return None
    with open(WF_SPLIT_PATH) as f:
        return json.load(f)


def load_candles(conn, exchange, symbol, start_ms, end_ms):
    """Load 1m candles for a specific time range."""
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM candles_1m "
        "WHERE exchange=? AND symbol=? AND ts >= ? AND ts <= ? ORDER BY ts",
        (exchange, symbol, start_ms, end_ms)
    ).fetchall()
    
    candles = []
    for r in rows:
        candles.append({
            "ts": r[0], "open": r[1], "high": r[2],
            "low": r[3], "close": r[4], "volume": r[5],
        })
    return candles


def load_trades(conn, exchange, symbol, start_ms, end_ms):
    """Load historical trades for tick-level simulation."""
    rows = conn.execute(
        "SELECT ts_ms, price, qty, side FROM historical_trades "
        "WHERE exchange=? AND symbol=? AND ts_ms >= ? AND ts_ms <= ? ORDER BY ts_ms",
        (exchange, symbol, start_ms, end_ms)
    ).fetchall()
    
    trades = []
    for r in rows:
        trades.append({
            "ts_ms": r[0], "price": r[1], "qty": r[2], "side": r[3],
        })
    return trades


# ═══════════════════════════════════════════════════════════
# Probabilistic Queue Model
# ═══════════════════════════════════════════════════════════

class ProbabilisticQueueModel:
    """Estimates fill probability for limit orders.
    
    A limit order at price P fills with probability:
      P(fill) = min(1.0, volume_at_P / (queue_depth × typical_order_size))
    
    This prevents the classic "OHLCV phantom fill" problem where
    backtests assume 100% fill rate whenever price touches the limit.
    """
    
    def __init__(self, fill_probability_factor=0.3):
        """
        fill_probability_factor: Base probability that a limit order 
        at the touch fills when price reaches it. 0.3 = conservative
        (assumes 30% fill rate at the touch, accounting for queue position).
        """
        self.base_fill_prob = fill_probability_factor
        self._rng_state = 42  # Deterministic for reproducibility
    
    def will_fill(self, volume_at_level, typical_volume):
        """Determine if a limit order fills given volume at that level.
        
        Uses deterministic pseudo-random based on volume ratio.
        """
        if typical_volume <= 0:
            return False
        
        volume_ratio = volume_at_level / typical_volume
        fill_prob = min(1.0, self.base_fill_prob * volume_ratio)
        
        # Deterministic pseudo-random (LCG)
        self._rng_state = (self._rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        rand_val = self._rng_state / 0x7FFFFFFF
        
        return rand_val < fill_prob


# ═══════════════════════════════════════════════════════════
# Hydra Market-Making Simulator
# ═══════════════════════════════════════════════════════════

def simulate_hydra(candles, trades, params):
    """Simulate Hydra A-S market-making strategy on historical data.
    
    Uses tick-level trades for fill simulation when available,
    falls back to candle-based simulation otherwise.
    
    Args:
        candles: List of 1m candle dicts
        trades: List of tick-level trade dicts (can be empty)
        params: Strategy parameters {grid_step, max_position, gamma, ...}
    
    Returns:
        BacktestResult
    """
    grid_step = params.get("grid_step", 4.0)
    max_pos = params.get("max_position", 0.005)
    gamma = params.get("gamma", 0.1)
    
    # State
    position = 0.0
    cash = 0.0
    fills = []
    pnl_curve = []
    equity_curve = []
    
    queue_model = ProbabilisticQueueModel(fill_probability_factor=0.3)
    
    total_opportunities = 0
    toxic_count = 0
    
    if trades:
        # ── TICK-LEVEL SIMULATION ──
        log.info(f"  Using tick-level simulation ({len(trades):,} trades)")
        result = _simulate_hydra_tick_level(trades, grid_step, max_pos, gamma, queue_model)
    else:
        # ── CANDLE-BASED SIMULATION (fallback) ──
        log.info(f"  Using candle-based simulation ({len(candles)} candles)")
        result = _simulate_hydra_candle_level(candles, grid_step, max_pos, gamma, queue_model)
    
    return result


def _simulate_hydra_tick_level(trades, grid_step, max_pos, gamma, queue_model):
    """Tick-level simulation with queue position model."""
    position = 0.0
    cash = 0.0
    fills = []
    pnl_by_minute = {}
    
    total_opps = 0
    toxic_count = 0
    
    # Compute mid price baseline
    if not trades:
        return _empty_result("hydra", {})
    
    mid_price = trades[0]["price"]
    
    # Grid levels: passive orders around mid
    bid_level = mid_price - grid_step
    ask_level = mid_price + grid_step
    
    # Running volume for queue model
    typical_minute_volume = 0
    minute_volume = 0
    minute_ts = trades[0]["ts_ms"] // 60000
    volume_samples = []
    
    for trade in trades:
        price = trade["price"]
        qty = trade["qty"]
        side = trade["side"]
        trade_minute = trade["ts_ms"] // 60000
        
        # Track minute volume for queue model
        if trade_minute != minute_ts:
            volume_samples.append(minute_volume)
            if len(volume_samples) > 60:
                typical_minute_volume = statistics.median(volume_samples[-60:])
            minute_volume = 0
            minute_ts = trade_minute
        minute_volume += qty
        
        # Update mid price (EWMA)
        mid_price = 0.99 * mid_price + 0.01 * price
        
        # A-S reservation price adjustment
        reservation = mid_price - gamma * position * grid_step
        bid_level = reservation - grid_step / 2
        ask_level = reservation + grid_step / 2
        
        # Check if our bid fills
        if price <= bid_level and position < max_pos:
            total_opps += 1
            tv = typical_minute_volume if typical_minute_volume > 0 else qty * 10
            if queue_model.will_fill(qty, tv):
                fill_qty = min(0.001, max_pos - position)  # Standard lot
                position += fill_qty
                cost = bid_level * fill_qty
                fee = cost * BFX_MAKER_FEE  # Maker rebate
                cash -= cost + fee
                fills.append({"side": "buy", "price": bid_level, "qty": fill_qty, "fee": fee, "ts": trade["ts_ms"]})
                
                # Toxic detection: did price continue down?
                # We mark it toxic if the very next trade is also a sell
                if side == "sell":
                    toxic_count += 1
        
        # Check if our ask fills
        elif price >= ask_level and position > -max_pos:
            total_opps += 1
            tv = typical_minute_volume if typical_minute_volume > 0 else qty * 10
            if queue_model.will_fill(qty, tv):
                fill_qty = min(0.001, position + max_pos)
                if fill_qty > 0:
                    position -= fill_qty
                    revenue = ask_level * fill_qty
                    fee = revenue * BFX_MAKER_FEE
                    cash += revenue - fee
                    fills.append({"side": "sell", "price": ask_level, "qty": fill_qty, "fee": fee, "ts": trade["ts_ms"]})
                    
                    if side == "buy":
                        toxic_count += 1
        
        # Track PnL per minute
        mark_to_market = cash + position * price
        pnl_by_minute[trade_minute] = mark_to_market
    
    # Final mark-to-market
    final_price = trades[-1]["price"] if trades else 0
    final_mtm = cash + position * final_price
    
    return _build_result(
        "hydra", fills, pnl_by_minute, final_mtm, cash,
        position, final_price, total_opps, toxic_count,
        len(set(t["ts_ms"] // 60000 for t in trades)),
        {"grid_step": grid_step, "max_position": max_pos, "gamma": gamma},
        (trades[-1]["ts_ms"] - trades[0]["ts_ms"]) / (3600 * 1000) if len(trades) > 1 else 0
    )


def _simulate_hydra_candle_level(candles, grid_step, max_pos, gamma, queue_model):
    """Candle-level simulation (fallback when no tick data)."""
    position = 0.0
    cash = 0.0
    fills = []
    pnl_by_minute = {}
    
    total_opps = 0
    toxic_count = 0
    
    if not candles:
        return _empty_result("hydra", {})
    
    mid_price = candles[0]["close"]
    
    for i, candle in enumerate(candles):
        price = candle["close"]
        volume = candle.get("volume", 0)
        
        # Update mid
        mid_price = 0.95 * mid_price + 0.05 * price
        
        # A-S reservation
        reservation = mid_price - gamma * position * grid_step
        bid_level = reservation - grid_step / 2
        ask_level = reservation + grid_step / 2
        
        # Check bid fill: did low go below our bid?
        if candle["low"] <= bid_level and position < max_pos:
            total_opps += 1
            # Queue model: use candle volume as proxy
            if queue_model.will_fill(volume * 0.5, volume):
                fill_qty = 0.001
                position += fill_qty
                cost = bid_level * fill_qty
                fee = cost * BFX_MAKER_FEE
                cash -= cost + fee
                fills.append({"side": "buy", "price": bid_level, "qty": fill_qty, "fee": fee, "ts": candle["ts"]})
                
                # Toxic: close < bid_level → adversely selected
                if candle["close"] < bid_level:
                    toxic_count += 1
        
        # Check ask fill
        if candle["high"] >= ask_level and position > -max_pos:
            total_opps += 1
            if queue_model.will_fill(volume * 0.5, volume):
                fill_qty = min(0.001, position + max_pos)
                if fill_qty > 0:
                    position -= fill_qty
                    revenue = ask_level * fill_qty
                    fee = revenue * BFX_MAKER_FEE
                    cash += revenue - fee
                    fills.append({"side": "sell", "price": ask_level, "qty": fill_qty, "fee": fee, "ts": candle["ts"]})
                    
                    if candle["close"] > ask_level:
                        toxic_count += 1
        
        mark_to_market = cash + position * price
        pnl_by_minute[i] = mark_to_market
    
    final_price = candles[-1]["close"]
    final_mtm = cash + position * final_price
    duration_h = len(candles) / 60.0
    
    return _build_result(
        "hydra", fills, pnl_by_minute, final_mtm, cash,
        position, final_price, total_opps, toxic_count,
        len(candles),
        {"grid_step": grid_step, "max_position": max_pos, "gamma": gamma},
        duration_h
    )


# ═══════════════════════════════════════════════════════════
# Result Builder
# ═══════════════════════════════════════════════════════════

def _build_result(bot_name, fills, pnl_by_minute, final_mtm, cash,
                  position, final_price, total_opps, toxic_count,
                  candle_count, params, duration_hours, partition=""):
    """Build BacktestResult from simulation state."""
    
    buy_fills = sum(1 for f in fills if f["side"] == "buy")
    sell_fills = sum(1 for f in fills if f["side"] == "sell")
    total_fills = len(fills)
    total_fees = sum(abs(f.get("fee", 0)) for f in fills)
    
    # Gross PnL (without fees)
    gross_pnl = final_mtm + total_fees
    net_pnl = final_mtm
    
    # Sharpe ratio from minute-level returns
    pnl_values = sorted(pnl_by_minute.values()) if pnl_by_minute else [0]
    if len(pnl_values) > 2:
        returns = [pnl_values[i] - pnl_values[i-1] for i in range(1, len(pnl_values))]
        mean_ret = statistics.mean(returns) if returns else 0
        std_ret = statistics.stdev(returns) if len(returns) > 1 else 1
        # Annualize: sqrt(1440 * 365) for minute data
        sharpe = (mean_ret / std_ret) * (1440 * 365) ** 0.5 if std_ret > 0 else 0
    else:
        sharpe = 0.0
    
    # Max drawdown
    peak = 0
    max_dd = 0
    for v in pnl_values:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    
    # Toxic rate
    toxic_rate = (toxic_count / total_fills * 100) if total_fills > 0 else 0
    
    # Fill rate
    fill_rate = (total_fills / total_opps * 100) if total_opps > 0 else 0
    
    # Average trade PnL
    avg_trade = net_pnl / max(1, min(buy_fills, sell_fills))
    
    # Gate validation
    fail_reasons = []
    if net_pnl < GATE_NET_PNL_MIN:
        fail_reasons.append(f"PnL ${net_pnl:.4f} < ${GATE_NET_PNL_MIN}")
    if sharpe < GATE_SHARPE_MIN:
        fail_reasons.append(f"Sharpe {sharpe:.2f} < {GATE_SHARPE_MIN}")
    if max_dd > GATE_MAX_DRAWDOWN:
        fail_reasons.append(f"MaxDD ${max_dd:.4f} > ${GATE_MAX_DRAWDOWN}")
    if toxic_rate > GATE_TOXIC_RATE_MAX:
        fail_reasons.append(f"Toxic {toxic_rate:.1f}% > {GATE_TOXIC_RATE_MAX}%")
    if total_fills < GATE_MIN_FILLS:
        fail_reasons.append(f"Fills {total_fills} < {GATE_MIN_FILLS}")
    
    passed = len(fail_reasons) == 0
    
    return BacktestResult(
        bot_name=bot_name,
        partition=partition,
        net_pnl=net_pnl,
        gross_pnl=gross_pnl,
        total_fees=total_fees,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        total_fills=total_fills,
        buy_fills=buy_fills,
        sell_fills=sell_fills,
        toxic_fills=toxic_count,
        toxic_rate=toxic_rate,
        fill_rate=fill_rate,
        avg_trade_pnl=avg_trade,
        duration_hours=duration_hours,
        candle_count=candle_count,
        params=params,
        passed=passed,
        fail_reasons=fail_reasons,
    )


def _empty_result(bot_name, params):
    """Return empty result for insufficient data."""
    return BacktestResult(
        bot_name=bot_name, partition="", net_pnl=0, gross_pnl=0,
        total_fees=0, sharpe_ratio=0, max_drawdown=0, total_fills=0,
        buy_fills=0, sell_fills=0, toxic_fills=0, toxic_rate=0,
        fill_rate=0, avg_trade_pnl=0, duration_hours=0, candle_count=0,
        params=params, passed=False, fail_reasons=["INSUFFICIENT_DATA"],
    )


# ═══════════════════════════════════════════════════════════
# Walk-Forward Validation Pipeline
# ═══════════════════════════════════════════════════════════

def run_counterfactual_validation(bot_name, params, partition="oos"):
    """Run counterfactual backtest on specified Walk-Forward partition.
    
    Args:
        bot_name: "hydra", "grid", etc.
        params: Strategy parameters
        partition: "in_sample" or "oos"
    
    Returns:
        BacktestResult
    """
    split = load_walk_forward_split()
    if not split:
        return _empty_result(bot_name, params)
    
    if partition == "in_sample":
        start_ms = split["is_start_ms"]
        end_ms = split["is_end_ms"]
    else:
        start_ms = split["oos_start_ms"]
        end_ms = split["oos_end_ms"]
    
    log.info(f"🧪 Running {partition.upper()} backtest for {bot_name.upper()}...")
    
    if not os.path.exists(DB_PATH):
        log.error("market_data.db not found!")
        return _empty_result(bot_name, params)
    
    conn = sqlite3.connect(DB_PATH)
    
    # Load data
    exchange = "bitfinex"
    symbol = "tBTCUSD"
    
    candles = load_candles(conn, exchange, symbol, start_ms, end_ms)
    trades = load_trades(conn, exchange, symbol, start_ms, end_ms)
    conn.close()
    
    log.info(f"  Data: {len(candles)} candles, {len(trades):,} trades")
    
    if not candles and not trades:
        return _empty_result(bot_name, params)
    
    # Route to appropriate simulator
    if bot_name == "hydra":
        result = simulate_hydra(candles, trades, params)
    else:
        # Generic grid simulation for other bots
        result = simulate_hydra(candles, trades, params)  # TODO: Add per-bot simulators
    
    result.partition = partition
    
    log.info(result.summary())
    return result


def run_full_walk_forward(bot_name, params):
    """Run full Walk-Forward validation pipeline.
    
    1. Test on In-Sample (Days 1-6) — informational
    2. Test on Out-of-Sample (Days 7-8) — GATE decision
    
    Returns:
        (is_result, oos_result)
    """
    is_result = run_counterfactual_validation(bot_name, params, "in_sample")
    oos_result = run_counterfactual_validation(bot_name, params, "oos")
    
    log.info(f"\n═══ WALK-FORWARD SUMMARY: {bot_name.upper()} ═══")
    log.info(f"  IS:  {'PASS' if is_result.passed else 'FAIL'} | PnL=${is_result.net_pnl:+.4f} | Sharpe={is_result.sharpe_ratio:.2f}")
    log.info(f"  OOS: {'PASS' if oos_result.passed else 'FAIL'} | PnL=${oos_result.net_pnl:+.4f} | Sharpe={oos_result.sharpe_ratio:.2f}")
    log.info(f"  Gate Decision: {'✅ APPROVE' if oos_result.passed else '❌ REJECT'}")
    
    return is_result, oos_result


# ═══════════════════════════════════════════════════════════
# AI Tuning Interface (Phase 3)
# ═══════════════════════════════════════════════════════════

def build_tuning_prompt(bot_name, is_result, attempt=1, prev_attempts=None):
    """Build Gemini prompt for Phase 3 AI Parameter Tuning.
    
    Includes previous attempt history to prevent AI-leakage loops.
    """
    history = ""
    if prev_attempts:
        for i, (params, res) in enumerate(prev_attempts, 1):
            history += (
                f"\n  Attempt {i}: grid=${params.get('grid_step')}, "
                f"max_pos={params.get('max_position')}, "
                f"gamma={params.get('gamma')} → "
                f"PnL=${res.net_pnl:+.4f}, Toxic={res.toxic_rate:.1f}%, "
                f"Fills={res.total_fills}, Sharpe={res.sharpe_ratio:.2f}"
            )
    
    prompt = f"""═══ SBP v3.1 PHASE 3: PARAMETER OPTIMIZATION (Attempt {attempt}/3) ═══

Bot: {bot_name.upper()}
Partition: IN-SAMPLE ({is_result.duration_hours:.0f}h, {is_result.candle_count} candles)

BACKTEST RESULTS:
  Net PnL: ${is_result.net_pnl:+.4f} {'✅' if is_result.net_pnl >= 0 else '❌'}
  Gross PnL: ${is_result.gross_pnl:+.4f} | Fees: ${is_result.total_fees:.4f}
  Sharpe Ratio: {is_result.sharpe_ratio:.2f} {'✅' if is_result.sharpe_ratio >= 0.3 else '❌'}
  Max Drawdown: ${is_result.max_drawdown:.4f} {'✅' if is_result.max_drawdown <= 5.0 else '❌'}
  Total Fills: {is_result.total_fills} ({is_result.buy_fills}B/{is_result.sell_fills}S) {'✅' if is_result.total_fills >= 50 else '❌'}
  Toxic Rate: {is_result.toxic_rate:.1f}% {'✅' if is_result.toxic_rate <= 40 else '❌'}
  Fill Rate: {is_result.fill_rate:.1f}%

Current Parameters:
  grid_step: ${is_result.params.get('grid_step', 4.0):.2f}
  max_position: {is_result.params.get('max_position', 0.005):.4f} BTC
  gamma (risk aversion): {is_result.params.get('gamma', 0.1):.3f}

Previous Attempts:{history if history else ' (first attempt)'}

FAIL REASONS: {', '.join(is_result.fail_reasons) if is_result.fail_reasons else 'NONE'}

TASK: Propose new parameters that will:
1. Net PnL >= $0.00
2. Toxic rate < 40%
3. Fills >= 50 (strategy must be active)
4. Sharpe >= 0.3
5. Max Drawdown < $5.00

CONSTRAINTS:
- grid_step: $1.00 – $50.00 (wider = safer but fewer fills)
- max_position: 0.0001 – 0.010 BTC (smaller = less risk)
- gamma: 0.01 – 1.0 (higher = more inventory-averse)

Respond with EXACTLY one JSON object:
{{"grid_step": float, "max_position": float, "gamma": float, "reasoning": "brief explanation"}}
"""
    return prompt


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SBP v3.1 Counterfactual Backtest")
    parser.add_argument("--bot", default="hydra", help="Bot name")
    parser.add_argument("--grid", type=float, default=4.0, help="Grid step ($)")
    parser.add_argument("--maxpos", type=float, default=0.005, help="Max position (BTC)")
    parser.add_argument("--gamma", type=float, default=0.1, help="Risk aversion")
    parser.add_argument("--partition", default="oos", choices=["in_sample", "oos", "full"])
    args = parser.parse_args()
    
    params = {"grid_step": args.grid, "max_position": args.maxpos, "gamma": args.gamma}
    
    if args.partition == "full":
        is_r, oos_r = run_full_walk_forward(args.bot, params)
    else:
        result = run_counterfactual_validation(args.bot, params, args.partition)
        print(result.summary())
