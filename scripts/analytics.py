#!/usr/bin/env python3
"""
🐺 BEROUN SNIPER v10.0 — Trade Analytics Engine
Parses trading logs and computes:
- Per-trade win/loss stats
- Sharpe Ratio (annualized)
- Hourly profitability heatmap
- Fill balance metrics
"""

import json
import sys
import math
from collections import defaultdict
from datetime import datetime, timezone

LOG_DIR = "/home/wwwenda/hft-sniper/logs"


def parse_trades(log_file):
    """Parse trade_executed and pnl_realized events from log."""
    trades = []
    pnl_events = []

    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    fields = d.get("fields", {})
                    event = fields.get("event", "")
                    ts = d.get("timestamp", "")

                    if event == "trade_executed":
                        trades.append({
                            "ts": ts,
                            "amount": fields.get("amount", 0),
                            "price": fields.get("price", 0),
                            "new_pos": fields.get("new_pos", 0),
                            "aep": fields.get("aep", 0),
                        })
                    elif event == "pnl_realized":
                        pnl_events.append({
                            "ts": ts,
                            "gain": fields.get("gain", 0),
                            "closed": fields.get("closed", 0),
                            "aep": fields.get("aep", 0),
                        })
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass

    return trades, pnl_events


def compute_analytics(trades, pnl_events):
    """Compute comprehensive trading analytics."""
    if not trades:
        return None

    # Basic counts
    buys = [t for t in trades if t["amount"] > 0]
    sells = [t for t in trades if t["amount"] < 0]
    total_volume = sum(abs(t["amount"]) for t in trades)

    # PnL stats
    wins = [p for p in pnl_events if p["gain"] > 0]
    losses = [p for p in pnl_events if p["gain"] <= 0]
    total_pnl = sum(p["gain"] for p in pnl_events)
    win_rate = len(wins) / len(pnl_events) * 100 if pnl_events else 0
    avg_win = sum(p["gain"] for p in wins) / len(wins) if wins else 0
    avg_loss = sum(p["gain"] for p in losses) / len(losses) if losses else 0

    # Sharpe Ratio (annualized, assuming 5-min returns)
    if len(pnl_events) >= 2:
        returns = [p["gain"] for p in pnl_events]
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
        # Annualize: ~105,120 five-minute periods per year
        sharpe = (mean_r / std_r) * math.sqrt(105_120)
    else:
        sharpe = 0.0

    # Hourly heatmap
    hourly_pnl = defaultdict(float)
    hourly_trades = defaultdict(int)
    for p in pnl_events:
        try:
            hour = int(p["ts"][11:13])
            hourly_pnl[hour] += p["gain"]
            hourly_trades[hour] += 1
        except (ValueError, IndexError):
            pass

    # Best/worst hours
    best_hour = max(hourly_pnl, key=hourly_pnl.get) if hourly_pnl else 0
    worst_hour = min(hourly_pnl, key=hourly_pnl.get) if hourly_pnl else 0

    # Fill balance
    total_trades = len(buys) + len(sells)
    fill_balance = 1.0 - abs(len(buys) - len(sells)) / total_trades if total_trades > 0 else 0

    # Max drawdown (running PnL)
    running_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnl_events:
        running_pnl += p["gain"]
        if running_pnl > peak:
            peak = running_pnl
        dd = peak - running_pnl
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades": total_trades,
        "buys": len(buys),
        "sells": len(sells),
        "volume_btc": round(total_volume, 8),
        "pnl_events": len(pnl_events),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 6),
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "sharpe_ratio": round(sharpe, 2),
        "fill_balance": round(fill_balance, 3),
        "max_drawdown": round(max_dd, 6),
        "best_hour": best_hour,
        "best_hour_pnl": round(hourly_pnl.get(best_hour, 0), 4),
        "worst_hour": worst_hour,
        "worst_hour_pnl": round(hourly_pnl.get(worst_hour, 0), 4),
        "hourly_pnl": {str(h): round(v, 4) for h, v in sorted(hourly_pnl.items())},
        "hourly_trades": {str(h): v for h, v in sorted(hourly_trades.items())},
    }


def format_telegram(stats):
    """Format analytics for Telegram message."""
    if not stats:
        return "📊 No trade data available."

    # Hourly heatmap visualization (compact)
    heatmap = ""
    for h in range(24):
        pnl = stats["hourly_pnl"].get(str(h), 0)
        count = stats["hourly_trades"].get(str(h), 0)
        if count > 0:
            icon = "🟢" if pnl > 0 else "🔴"
            heatmap += f"  `{h:02d}h` {icon} `${pnl:+.4f}` ({count})\n"

    return f"""📊 *TRADE ANALYTICS*

🎯 *Win Rate:* `{stats['win_rate']}%` ({stats['wins']}W / {stats['losses']}L)
📈 *Sharpe Ratio:* `{stats['sharpe_ratio']}`
📉 *Max Drawdown:* `${stats['max_drawdown']:.4f}`

💰 *PnL:*
  Total: `${stats['total_pnl']:.4f}`
  Avg Win:  `${stats['avg_win']:.6f}`
  Avg Loss: `${stats['avg_loss']:.6f}`

📦 *Trades:* `{stats['total_trades']}` ({stats['buys']}B/{stats['sells']}S)
  Volume: `{stats['volume_btc']:.5f}` BTC
  Balance: `{stats['fill_balance']:.2f}`

🕐 *Hourly Heatmap:*
{heatmap}
⭐ Best: `{stats['best_hour']:02d}h` (`${stats['best_hour_pnl']:+.4f}`)
💀 Worst: `{stats['worst_hour']:02d}h` (`${stats['worst_hour_pnl']:+.4f}`)"""


def main():
    from datetime import date
    today = date.today().isoformat()
    log_file = f"{LOG_DIR}/trading.log.{today}"

    if len(sys.argv) > 1:
        log_file = sys.argv[1]

    trades, pnl_events = parse_trades(log_file)
    stats = compute_analytics(trades, pnl_events)

    if "--json" in sys.argv:
        print(json.dumps(stats, indent=2))
    elif "--telegram" in sys.argv:
        print(format_telegram(stats))
    else:
        print(f"📊 Analytics for {log_file}:")
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
