"""Regression: live venue checks must not count simulated or other venue trades."""
import sqlite3
import unittest
from unittest.mock import patch
import reconcile

class VenueIsolationTest(unittest.TestCase):
    def test_live_state_excludes_paper_and_other_venues(self):
        db = sqlite3.connect(":memory:")
        db.executescript("""
        CREATE TABLE orders(id,client_order_id,venue,symbol,side,type,qty,price,status,created_at);
        CREATE TABLE fills(id,order_id,venue_fill_id,qty,price,fee,fee_currency,filled_at);
        INSERT INTO orders VALUES(1,'live','binance','BTCUSDT','buy','limit','1','10','FILLED','now');
        INSERT INTO orders VALUES(2,'sim','paper','ETHUSDT','buy','limit','1','636','NEW','now');
        INSERT INTO orders VALUES(3,'other','bitfinex','ETHUSD','buy','limit','1','20','NEW','now');
        INSERT INTO fills VALUES(1,1,'livefill','1','10','0','USDT','now');
        INSERT INTO fills VALUES(2,2,'paperfill','1','636','0','USDT','now');
        INSERT INTO fills VALUES(3,3,'otherfill','1','20','0','USD','now');
        """)
        with patch.object(reconcile, '_db_query', side_effect=lambda sql: db.execute(sql).fetchall()):
            state = reconcile.fetch_db_state()
        self.assertEqual([o['client_order_id'] for o in state['orders']], ['live'])
        self.assertEqual([f['venue_fill_id'] for f in state['fills']], ['livefill'])
        self.assertEqual(state['symbols'], {'BTCUSDT'})
        self.assertEqual(reconcile.compute_expected_balances(state)['USDT'], -10)
        db.close()

if __name__ == '__main__':
    unittest.main()
