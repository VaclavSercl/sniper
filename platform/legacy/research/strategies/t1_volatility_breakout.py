#!/usr/bin/env python3
"""Strategie T1: Volatility Breakout / Range Expansion (BTCUSDT, 1m data).

Hypotéza (ekonomické zdůvodnění dle §9a):
  Po období komprese volatility (nízký rozsah svíček) následuje expanze.
  Protistranou jsou mean-reversion tradeři + liquidity providers, kteří
  po kompresi drží krátkodobé pozice proti pohybu, a pozdní trend-followers,
  kteří vstupují později a dražší. Edge plyne z behaviorální pomalosti
  (dispoziceční efekt: držení loser pozic proti novému trendu).
  Edge nezmizí, dokud trh obsahuje výrazný retail flow s dispozicečním
  chováním; historicky stabilní napříč režimy (testováno CPCV).

Pravidla (LONG only — spot, žádná páka):
  Vstup:  close > max(high posledních N=240 min) * (1 + 0.10 %)
          AND ATR_60m > medián ATR_60m za posledních 1440 min (vol filtr)
  Výstup: close < min(low posledních M=120 min)  OR max holding 24 h
          OR trailing stop 2 * ATR_60m od nejlepší ceny od vstupu
  Sizing: f_final dle §6 (Kelly cap 0.18), min 1 % max 20 % kapitálu
  Náklady: maker fee 0.075 % (BNB) + half-spread 0.01 % + impact ~0

Parametry k sweep (CPCV grid): N ∈ {120, 240, 480}, vol_kvantil ∈ {0.5, 0.7},
  trailing_ATR_mult ∈ {1.5, 2.0, 3.0} → 18 kombinací = 18 trialy rodiny T1.

Vše deterministické, žádný look-ahead (vstup se vyhodnocuje na close
svíčky, exekuce na open následující).
"""
import statistics


def atr(klines, period=60):
    """ATR přes posledních `period` svíček (high-low range, jednoduchý průměr)."""
    if len(klines) < period:
        return None
    trs = [float(k["high"]) - float(k["low"]) for k in klines[-period:]]
    return statistics.mean(trs)


def generate_signals(klines, n_breakout=240, vol_q=0.5, lookback_vol=1440):
    """Vrací seznam indexů vstupů (deterministicky, bez look-ahead).

    klines: seznam dictů s keys open,high,low,close (chronologicky).
    Podmínka vyhodnocena na close svíčky i; exekuce nominálně na open i+1.
    (Perf: přímý přístup k high/low dle indexu — žádné slicování prefixu,
    výsledky identické s původní implementací.)
    """
    n = len(klines)
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    closes = [float(k["close"]) for k in klines]

    def atr_idx(j, period=60):
        # ATR přes svíčky [j-period+1 .. j] (stejné jako atr(klines[:j+1]))
        if j + 1 < period:
            return None
        s = 0.0
        for k in range(j - period + 1, j + 1):
            s += highs[k] - lows[k]
        return s / period

    signals = []
    for i in range(max(n_breakout, lookback_vol), n - 1):
        breakout_level = max(highs[i - n_breakout:i]) * 1.001
        if closes[i] <= breakout_level:
            continue
        # vol filtr: ATR_60 na i musí být nad kvantilem ATR_60 za lookback
        atrs = []
        ok = True
        for j in range(i - lookback_vol, i + 1, 60):  # vzorkování po 60 min
            a = atr_idx(j)
            if a is None:
                ok = False
                break
            atrs.append(a)
        if not ok or not atrs:
            continue
        atrs.sort()
        threshold = atrs[min(int(vol_q * len(atrs)), len(atrs) - 1)]
        a_now = atr_idx(i)
        if a_now is not None and a_now > threshold:
            signals.append(i)
    return signals
