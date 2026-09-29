"""Fail-closed T15 research accounting. No network, storage or order submission.

Valuations are observations, not executable fills. Funding requires normalized
settled Hyperliquid events and the settlement oracle price, never current-rate
snapshots. Qualification remains blocked until execution evidence is integrated.
"""
import copy
import hashlib
import json
import math
import re
import statistics
from decimal import Decimal, InvalidOperation

HOUR = 3600000
DAY = 24 * HOUR
MAX_AGE_MS = 180000
MAX_SKEW_MS = 60000
SETTLEMENT_GRACE_MS = 300000
SCHEMA = 2
INSTRUMENTS = (("bitfinex_candles", "tBTCEUR"),
               ("bitfinex_candles", "tEURUSD"),
               ("bitfinex_candles", "tUDCUSD"),
               ("hyperliquid_candles", "BTC-PERP"))


class DataBlocked(RuntimeError):
    """Insufficient, inconsistent or corrupt evidence; no accounting mutation."""


def number(value, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DataBlocked("Invalid numeric evidence") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise DataBlocked("Non-finite or non-positive numeric evidence")
    return result


def timestamp(value):
    result = number(value)
    if result < 0 or result != result.to_integral_value():
        raise DataBlocked("Invalid millisecond timestamp")
    return int(result)


def market_snapshot(rows, now_ms):
    now_ms = timestamp(now_ms)
    if len(rows) != len(INSTRUMENTS):
        raise DataBlocked("Missing or ambiguous market instruments")
    found = {}
    for row in rows:
        try:
            key = (row["src"], row["symbol"])
            ts = timestamp(row["close_ms"])
            price = number(row["close"], positive=True)
        except KeyError as exc:
            raise DataBlocked("Incomplete quote evidence") from exc
        if key not in INSTRUMENTS or key in found:
            raise DataBlocked("Unexpected or duplicate instrument/source")
        if not 0 <= now_ms - ts <= MAX_AGE_MS:
            raise DataBlocked("Stale or future quote")
        found[key] = (ts, price)
    times = [v[0] for v in found.values()]
    if max(times) - min(times) > MAX_SKEW_MS:
        raise DataBlocked("Unsynchronized quotes")
    eur_btc = found[INSTRUMENTS[0]][1]
    usd_eur = found[INSTRUMENTS[1]][1]
    usd_usdc = found[INSTRUMENTS[2]][1]
    perp = found[INSTRUMENTS[3]][1]
    return {"ts": min(times), "spot_usd": eur_btc * usd_eur,
            "usdc_usd": usd_usdc, "perp": perp,
            "spread": float(eur_btc * usd_eur / (perp * usd_usdc))}


def new_state(rows, now_ms, initial_capital=1000):
    m = market_snapshot(rows, now_ms)
    capital = number(initial_capital, positive=True)
    half = capital / 2
    # Explicit hypothetical opening, NOT a confirmed maker execution.
    qty = half / m["spot_usd"]
    return {
        "schema_version": SCHEMA, "strategy": "T15_mica_cross_basis",
        "ladder_level": "RESEARCH_UNQUALIFIED", "started_ms": timestamp(now_ms),
        "revision": 0, "initial_capital_usd": float(capital),
        "current_equity_usd": float(capital), "peak_equity_usd": float(capital),
        "max_drawdown_pct": 0.0, "spot_btc": str(qty), "perp_short_btc": str(qty),
        "spot_entry_price": str(m["spot_usd"]), "perp_entry_price": str(m["perp"]),
        "cash_margin_usdc": str(half / m["usdc_usd"]), "funding_usdc": "0",
        "funding_events": {}, "accumulated_funding_usd": 0.0,
        "accumulated_arb_profit_usd": 0.0, "accumulated_rebates_usd": 0.0,
        "total_trades": 0, "execution_verified": False,
        "hourly_samples": [], "daily_samples": [], "last_market_ms": m["ts"],
        "last_tick_ms": timestamp(now_ms), "data_gap_count": 0,
        "qualification": "BLOCKED", "qualification_reason": "UNVERIFIED_EXECUTION",
        "falsification_gates": {name: "UNKNOWN" for name in (
            "F1_max_drawdown_under_10pct", "F2_sharpe_above_1_0",
            "F3_positive_funding_yield", "F4_half_life_under_72h",
            "F5_zero_delta_maintained", "F6_funding_complete", "F7_execution_verified")},
    }


def validate_state(state):
    if state.get("schema_version") != SCHEMA:
        raise DataBlocked("Legacy paper state requires a separate explicitly initialized epoch")
    try:
        timestamp(state["started_ms"])
        timestamp(state["last_tick_ms"])
        for name in ("initial_capital_usd", "spot_btc", "perp_short_btc",
                     "perp_entry_price", "cash_margin_usdc", "peak_equity_usd"):
            number(state[name], positive=True)
        number(state["funding_usdc"])
        if not isinstance(state["funding_events"], dict):
            raise DataBlocked("Invalid persisted funding ledger")
    except KeyError as exc:
        raise DataBlocked("Incomplete persisted state") from exc
    # This version cannot verify any executions. Refuse adopted/fabricated fills.
    if state.get("execution_verified") is not False or any(state.get(k) != 0 for k in (
            "total_trades", "accumulated_arb_profit_usd", "accumulated_rebates_usd")):
        raise DataBlocked("Execution evidence is unsupported by this accounting version")


def settled_events(events, state, now_ms):
    if len(events) > 5000:
        raise DataBlocked("Funding history exceeds reviewed bound")
    normalized = {}
    for event in events:
        try:
            ts = timestamp(event["settled_ms"])
            rate = number(event["rate"])
            oracle = number(event["oracle_price"], positive=True)
            if (event["venue"], event["symbol"]) != ("hyperliquid", "BTC"):
                raise DataBlocked("Wrong funding venue/instrument")
            if event["interval_ms"] != HOUR or ts % HOUR:
                raise DataBlocked("Unnormalized funding settlement interval")
            if not re.fullmatch(r"[0-9a-f]{64}", event["evidence_sha256"]):
                raise DataBlocked("Missing source evidence digest")
        except (KeyError, TypeError) as exc:
            raise DataBlocked("Incomplete settled funding evidence; snapshots are not settlements") from exc
        if ts > now_ms or ts <= state["started_ms"]:
            raise DataBlocked("Funding outside position holding interval")
        key = f"hyperliquid:BTC:{ts}"
        value = {"settled_ms": ts, "rate": str(rate), "oracle_price": str(oracle),
                 "evidence_sha256": event["evidence_sha256"]}
        if key in normalized and normalized[key] != value:
            raise DataBlocked("Conflicting duplicate funding event")
        normalized[key] = value
    for key, old in state["funding_events"].items():
        if key not in normalized or normalized[key] != old:
            raise DataBlocked("Previously accounted funding missing or revised")
    required_end = (now_ms - SETTLEMENT_GRACE_MS) // HOUR * HOUR
    if normalized:
        required_end = max(required_end, max(v["settled_ms"] for v in normalized.values()))
    for ts in range((state["started_ms"] // HOUR + 1) * HOUR, required_end + 1, HOUR):
        if f"hyperliquid:BTC:{ts}" not in normalized:
            raise DataBlocked("Missing settled funding interval")
    return normalized


def evaluate_tick(state, rows, events, now_ms):
    """Return a new state; validate the whole batch before any persistence."""
    validate_state(state)
    now_ms = timestamp(now_ms)
    if now_ms < state["last_tick_ms"]:
        raise DataBlocked("Clock moved backwards")
    market = market_snapshot(rows, now_ms)
    if market["ts"] < state["last_market_ms"]:
        raise DataBlocked("Market observations regressed")
    normalized = settled_events(events, state, now_ms)
    result = copy.deepcopy(state)
    # Recompute the entire bounded ledger; do not trust a stored sum independently.
    income = sum((number(e["rate"]) * number(e["oracle_price"]) * number(state["perp_short_btc"])
                  for e in normalized.values()), Decimal(0))
    old_income = sum((number(e["rate"]) * number(e["oracle_price"]) * number(state["perp_short_btc"])
                      for e in state["funding_events"].values()), Decimal(0))
    if old_income != number(state["funding_usdc"]):
        raise DataBlocked("Persisted funding sum does not match event ledger")
    result["funding_usdc"] = str(income)
    result["funding_events"] = normalized
    result["accumulated_funding_usd"] = float(income * market["usdc_usd"])
    perp_pnl = number(state["perp_short_btc"]) * (number(state["perp_entry_price"]) - market["perp"])
    equity = number(state["spot_btc"]) * market["spot_usd"] + (
        number(state["cash_margin_usdc"]) + perp_pnl + income) * market["usdc_usd"]
    result["current_equity_usd"] = float(equity)
    peak = max(number(state["peak_equity_usd"]), equity)
    result["peak_equity_usd"] = float(peak)
    result["max_drawdown_pct"] = max(float(state["max_drawdown_pct"]), float((peak-equity)/peak*100))
    if now_ms - state["last_tick_ms"] > MAX_AGE_MS:
        result["data_gap_count"] += 1
    result["last_tick_ms"] = now_ms
    result["last_market_ms"] = market["ts"]
    result["current_synthetic_rate"] = market["spread"]
    for field, size, value, limit in (("hourly_samples", HOUR, market["spread"], 168),
                                      ("daily_samples", DAY, float(equity), 92)):
        bucket = market["ts"] // size
        samples = result[field]
        # One observation per bucket, never 30 duplicated measurements per hour.
        if not samples or bucket > samples[-1][0]:
            samples.append([bucket, value])
            del samples[:-limit]
    gates = result["falsification_gates"]
    gates["F1_max_drawdown_under_10pct"] = "PASS" if result["max_drawdown_pct"] < 10 else "FAIL"
    gates["F3_positive_funding_yield"] = "UNKNOWN" if not normalized else ("PASS" if income > 0 else "FAIL")
    gates["F5_zero_delta_maintained"] = "PASS" if abs(number(state["spot_btc"])-number(state["perp_short_btc"])) < Decimal("0.000001") else "FAIL"
    gates["F6_funding_complete"] = "PASS"
    result["sharpe_ratio"] = None
    result["ou_half_life_hours"] = None
    gates["F2_sharpe_above_1_0"] = "UNKNOWN"
    gates["F4_half_life_under_72h"] = "UNKNOWN"
    daily = result["daily_samples"]
    if (len(daily) >= 31 and not result["data_gap_count"] and
            all(b[0]-a[0] == 1 and a[1] > 0 for a, b in zip(daily, daily[1:]))):
        returns = [b[1]/a[1]-1 for a, b in zip(daily, daily[1:])]
        sd = statistics.stdev(returns)
        if sd > 0:
            result["sharpe_ratio"] = statistics.mean(returns)/sd * math.sqrt(365)
            gates["F2_sharpe_above_1_0"] = "PASS" if result["sharpe_ratio"] >= 1 else "FAIL"
    hourly = result["hourly_samples"]
    if len(hourly) >= 24 and all(b[0]-a[0] == 1 for a, b in zip(hourly, hourly[1:])):
        from t15_mica_cross_basis import T15CrossBasisEngine
        theta, _, half_life = T15CrossBasisEngine.estimate_ou_parameters([p[1] for p in hourly])
        if theta > 0:
            result["ou_half_life_hours"] = half_life
            gates["F4_half_life_under_72h"] = "PASS" if half_life < 72 else "FAIL"
        else:
            gates["F4_half_life_under_72h"] = "FAIL"
    # Do not manufacture executions from a z-score. This is not a trading model.
    gates["F7_execution_verified"] = "UNKNOWN"
    result["qualification"] = "BLOCKED"
    result["qualification_reason"] = "UNVERIFIED_EXECUTION"
    result["revision"] += 1
    return result
