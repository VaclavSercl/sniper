#!/usr/bin/env python3
"""T6 grid trading (BTCUSDT 1m spot, long side grid v USDT quote).

Grid: aritmetická/geometrická mřížka limit buy/sell. Long-only: kupuje USDT->BTC
na úrovních pod trhem, prodává o level výš. Rolling mřížka se přesouvá za trhem
(central price = EMA krátké periody), fixní mřížka stojí na místě.

Benchmark: buy & hold (USD return i konečný BTC stav vs HODL — bitcoin-standard).

Simulace je 1m bar-based: exekuce buy levelu = bar low <= level (fill za level
cenu, maker fee), sell levelu = bar high >= level. Inventář BTC, hotovost USDT.
Start: 10 000 USDT, 0 BTC. Fees: 0.075 % maker za stranu.
"""
from decimal import Decimal, getcontext
getcontext().prec = 28

FEE = Decimal("0.00075")  # maker per side


def run_grid(klines, spacing_pct=1.0, n_levels=25, geometric=False,
             rolling=False, roll_window=1440, stop_loss_pct=None,
             capital_usdt=10_000):
    """Vrátí dict s total_ret_usd, final_btc, hodl_btc, hodl_ret, n_trades, max_dd.

    klines: list dictů ts/open/high/low/close (str hodnoty).
    spacing_pct: rozestup úrovní v % (aritm: % startu, geom: násobitel).
    n_levels: počet úrovní pod trhem (buy side) — sell úrovně jsou symetrické.
    rolling: mřížka se centruje na EMA(roll_window) close; fixní jinak.
    stop_loss_pct: pokud cena klesne pod nejnižší buy level o X % od něj,
                   inventář se prodá (taker-like, maker fee aproximace) a
                   mřížka se resetuje.
    """
    D = Decimal
    closes = [D(k["close"]) for k in klines]
    highs = [D(k["high"]) for k in klines]
    lows = [D(k["low"]) for k in klines]

    start_price = closes[0]
    cash = D(str(capital_usdt))
    btc = D("0")
    invested_btc_cost = D("0")  # průměrná vstupní cena inventáře (pro realized PnL)
    n_trades = 0
    realized = D("0")

    def build_levels(center):
        lv = []
        if geometric:
            m = D(1) + D(str(spacing_pct)) / 100
            p = D(center)
            for _ in range(n_levels):
                p = p / m
                lv.append(p)
        else:
            step = center * D(str(spacing_pct)) / 100
            for i in range(1, n_levels + 1):
                lv.append(center - step * i)
        return lv

    # pro rolling: jednoduchá klouzavá EMA na closes (Decimal)
    ema = closes[0]
    alpha = D(2) / (D(roll_window) + 1)

    center = start_price
    buys = build_levels(center)
    # mapování: pro každý buy level existuje sell level o jedna výš
    def sell_price_for(buy_lvl, center):
        if geometric:
            m = D(1) + D(str(spacing_pct)) / 100
            return buy_lvl * m * m
        step = center * D(str(spacing_pct)) / 100
        return buy_lvl + 2 * step

    # track which levels hold inventory (index -> qty bought, entry price)
    holdings = {}  # level_index -> {"qty": btc, "entry": price}
    floor_stop = (min(buys) * (D(1) - D(str(stop_loss_pct or 100)) / 100)
                  if stop_loss_pct else None)

    peak_equity = D(0)
    max_dd = D("0")

    for i in range(len(klines)):
        if rolling:
            ema = alpha * closes[i] + (1 - alpha) * ema
            new_center = ema
            if abs(new_center - center) / center > D(str(spacing_pct)) / 100:
                center = new_center
                buys = build_levels(center)
                floor_stop = (min(buys) * (D(1) - D(str(stop_loss_pct)) / 100)
                              if stop_loss_pct else None)

        lo, hi = lows[i], highs[i]

        # stop-loss check (intra-bar aproximace: low prorazil floor)
        if floor_stop and holdings and lo <= floor_stop:
            for idx in list(holdings):
                h = holdings.pop(idx)
                proceeds = h["qty"] * floor_stop * (1 - FEE)
                cash += proceeds
                realized += proceeds - h["qty"] * h["entry"]
                n_trades += 1
            # reset mřížky na aktuální cenu
            center = closes[i]
            buys = build_levels(center)
            floor_stop = (min(buys) * (D(1) - D(str(stop_loss_pct)) / 100)
                          if stop_loss_pct else None)

        # buys: fill když bar low <= level (a nemáme už pozici z tohoto levelu)
        for idx, lvl in enumerate(buys):
            if idx in holdings:
                continue
            if lo <= lvl:
                cost = cash / n_levels if cash > 0 else D(0)
                if cost < D("1"):  # dust guard
                    continue
                qty = cost / lvl * (1 - FEE)
                if cash >= cost:
                    cash -= cost
                    holdings[idx] = {"qty": qty, "entry": lvl}
                    btc += qty
                    n_trades += 1

        # sells: fill když bar high >= sell level pozice
        for idx in list(holdings):
            h = holdings[idx]
            sp = sell_price_for(buys[idx], center)
            if hi >= sp:
                proceeds = h["qty"] * sp * (1 - FEE)
                cash += proceeds
                btc -= h["qty"]
                realized += proceeds - h["qty"] * h["entry"]
                del holdings[idx]
                n_trades += 1

        equity = cash + btc * closes[i]
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else D(0)
        if dd > max_dd:
            max_dd = dd

    final_price = closes[-1]
    equity_final = cash + btc * final_price
    total_ret = (equity_final - capital_usdt) / capital_usdt
    hodl_btc = D(str(capital_usdt)) / start_price
    hodl_ret = (final_price - start_price) / start_price
    btc_vs_hodl = (btc - hodl_btc) if btc > 0 else D("0")  # zbytkový inventář
    # srovnatelné: celkové BTC ekvivalentně (inventář + realized v USDT převedený)
    grid_btc_equiv = btc + cash / final_price

    return {
        "total_ret_usd": float(total_ret),
        "hodl_ret": float(hodl_ret),
        "grid_btc_equiv": float(grid_btc_equiv),
        "hodl_btc": float(hodl_btc),
        "final_btc_inventory": float(btc),
        "final_cash_usdt": float(cash),
        "n_trades": n_trades,
        "max_dd": float(max_dd),
        "realized_pnl_usdt": float(realized),
    }
