import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import perfect_market_ingest as ingest

class FailureReportingTests(unittest.TestCase):
    def test_partial_ingest_failure_is_counted(self):
        with patch.object(ingest,'BINANCE_PAIRS',['BTCUSDT']),patch.object(ingest,'BITFINEX_PAIRS',[]),patch.object(ingest,'HYPERLIQUID_COINS',[]):
            runner=ingest.PerfectMarketIngest(dry_run=True)
            with patch.object(runner,'fetch_binance_klines',side_effect=RuntimeError('fixture')):
                self.assertEqual(runner.run_incremental_cycle()['errors'],1)

    def test_cli_fails_on_partial_ingest_error(self):
        with patch.object(sys,'argv',['ingest','--incremental']),patch.object(ingest.PerfectMarketIngest,'run_incremental_cycle',return_value={'errors':1}):
            self.assertEqual(ingest.main(),1)

if __name__=='__main__':unittest.main()
