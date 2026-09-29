#!/usr/bin/env python3
"""T7 funding-rate mean reversion (BTCUSDT, perpetual, 8h funding horizon).

HYPOTÉZA (§9a): Extrémně vysoký funding rate (z-score nad prahem) signalizuje
přehřáté long crowding → předpokládáme krátkodobou mean-reversion ceny
(directně i fundingu samotného). Obchodujeme LONG spotovéno/perp straddle?
NE — jednoduše LONG BTCUSDT (spot) po negativním extrému fundingu (short
crowding → squeeze potenciál), SHORT po pozitivním extrému nelze spotem,
rodina je tedy long-only na spotu s entry filtrem funding z-score < -prah.

Protistrana: crowding perp traderů platících extrémní funding; jejich
stop-lossy/ unwind vytváří mechanický protitlak. Edge nezmizí, dokud
funding zůstává placen perp crowdem ( strukturální, ne informační).

Falzifikace: SR_oos/SR_is < 0.4, DSR < 0.95, PBO > 0.50 při N=18.

Signál generujeme z funding historie (8h frekvence) + 1m klines pro exekuci.
Entry: po funding fixu s z-score pod -prah, long na open následující 1m svíčky.
Exit: holding period H barů (1m) nebo trailing stop.
Funding jako cash-flow: za holding přes funding fix se připisuje/přičítá
(rate * notional) — znaménko dle strany (long dostává při negativním fundingu).
"""
import math


def funding_zscore(funding_rates, window):
    """Rolling z-score poslední hodnoty v okně (min. window hodnot)."""
    w = funding_rates[-window:]
    if len(w) < window:
        return None
    mu = sum(w) / window
    var = sum((x - mu) ** 2 for x in w) / window
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return (w[-1] - mu) / sd


def generate_signals(funding_events, kline_index, z_window=90, entry_z=-1.5):
    """Vrací list of (ts, side) entry signálů.

    funding_events: [(ts, rate)] chronologicky (8h)
    kline_index: dict ts(1m) -> kline index pro lookup exekuce
    """
    rates = []
    signals = []
    for ts, rate in funding_events:
        rates.append(rate)
        if len(rates) < z_window:
            continue
        z = funding_zscore(rates, z_window)
        if z is None:
            continue
        if z <= entry_z:
            # long: short-crowding squeeze potenciál
            signals.append((ts, "long"))
    return signals
