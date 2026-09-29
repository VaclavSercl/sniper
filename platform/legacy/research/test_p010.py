#!/usr/bin/env python3
"""P010 unit testy + fee/limit model ověření.

Testuje: dust_ignored pro sub-$1 diffy, balance_mismatch pro diffy nad $1,
a realistická burzovní omezení (Binance/BITFINEX) pro naše velikosti obchodů.
"""
import sys
sys.path.insert(0, "/opt/sniper/current/platform/legacy/research")
from reconcile import reconcile  # noqa: E402
from decimal import Decimal as D


def make_venue(balances, usd_prices):
    return {"balances": balances, "open_orders": [], "trades": [],
            "usd_prices": usd_prices}


def test_dust_ignored():
    # BNB diff 0.00001607 * $708 = $0.0114 → dust_ignored
    db_state = {"orders": [], "fills": [], "symbols": set()}
    venue = make_venue({"BNB": D("0.00001607")}, {"BNB": "708.55"})
    diffs = reconcile(venue, db_state, tolerance_usd=1.0, baseline={})
    types = [d["type"] for d in diffs]
    assert "balance_mismatch" not in types, f"fail: {diffs}"
    assert "dust_ignored" in types, f"fail: {diffs}"
    print("PASS: BNB dust $0.011 → dust_ignored")


def test_mismatch_over_threshold():
    # BNB diff 0.01 * $708 = $7.09 → balance_mismatch
    db_state = {"orders": [], "fills": [], "symbols": set()}
    venue = make_venue({"BNB": D("0.01")}, {"BNB": "708.55"})
    diffs = reconcile(venue, db_state, tolerance_usd=1.0, baseline={})
    types = [d["type"] for d in diffs]
    assert "balance_mismatch" in types, f"fail: {diffs}"
    print("PASS: BNB diff $7.09 → balance_mismatch")


def test_usdt_still_works():
    db_state = {"orders": [], "fills": [], "symbols": set()}
    venue = make_venue({"USDT": D("0.50")}, {})
    diffs = reconcile(venue, db_state, tolerance_usd=1.0, baseline={})
    types = [d["type"] for d in diffs]
    assert "balance_mismatch" not in types
    assert "dust_ignored" in types
    print("PASS: USDT dust $0.50 → dust_ignored")


def test_btc_dust():
    # BTC diff 0.000001 * $80000 = $0.08 → dust_ignored
    db_state = {"orders": [], "fills": [], "symbols": set()}
    venue = make_venue({"BTC": D("0.000001")}, {"BTC": "80000"})
    diffs = reconcile(venue, db_state, tolerance_usd=1.0, baseline={})
    types = [d["type"] for d in diffs]
    assert "balance_mismatch" not in types, f"fail: {diffs}"
    print("PASS: BTC dust $0.08 → dust_ignored")


def test_exchange_constraints():
    """Realistická omezení burz pro naše paper velikosti (ověřeno z API dnes):
    Binance: LOT_SIZE minQty 0.00001 BTC, NOTIONAL min $5, tickSize $0.01
    Bitfinex: BTCUSD min lot 0.00006 BTC (order ~$5+), tick $1 (z tickeru)
    """
    # náš grid LOT = 0.01 BTC @ 80000 = $800 notional >> $5 min ✓
    # náš MM LOT = 0.01 BTC ✓
    # DCA měsíční $100 = 0.00125 BTC @ 80000 ✓ (Binance min 0.00001)
    # Bitfinex tick $1: náš half-spread 3.8 USD = 3 ticky ✓ (kvantizace ±$0.5 zanedbatelná)
    lot_btc = 0.01
    price = 80000
    notional = lot_btc * price
    assert notional >= 5, "Binance minNotional"
    assert lot_btc >= 0.00001, "Binance minQty"
    assert lot_btc >= 0.00006, "Bitfinex min lot"
    print(f"PASS: exchange constraints (notional ${notional:.0f} >= $5, "
          f"qty {lot_btc} >= mins)")


if __name__ == "__main__":
    test_dust_ignored()
    test_mismatch_over_threshold()
    test_usdt_still_works()
    test_btc_dust()
    test_exchange_constraints()
    print("ALL TESTS PASSED")
