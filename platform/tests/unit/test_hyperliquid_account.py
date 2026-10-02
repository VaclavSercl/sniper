import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research'))
import hyperliquid_account as account

ACTUAL='0x'+'12'*20
AGENT='0x'+'34'*20
CONFIG={'schema':1,'account':ACTUAL,'signer':AGENT,'account_role':'user','evidence':'a'*64}


def fixture(kind,user):
    if kind=='userRole':return {'role':'agent','data':{'user':ACTUAL}} if user==AGENT else {'role':'user'}
    if kind=='clearinghouseState':return {'marginSummary':{'accountValue':'0.0'},'withdrawable':'0.0','assetPositions':[]}
    if kind=='spotClearinghouseState':return {'balances':[{'coin':'USDC','total':'6.26','hold':'0.0'}]}
    if kind=='openOrders':return []
    if kind=='userFees':return dict(zip(('userCrossRate','userAddRate','userSpotCrossRate','userSpotAddRate'),('0.00045','0.00015','0.0007','0.0004')))
    raise AssertionError('Unexpected account API')


class AccountTests(unittest.TestCase):
    def test_queries_actual_account_and_keeps_balance_separate_from_mandate(self):
        seen=[]
        def fetch(kind,user):seen.append((kind,user));return fixture(kind,user)
        data=account.observe(CONFIG,100000,fetch)
        self.assertTrue(data['association_revalidated']);self.assertEqual(data['spot_balances'][0]['total'],'6.26')
        self.assertFalse(data['live_enabled']);self.assertEqual(data['mandate'],'NOT_CONFIGURED')
        self.assertTrue(all(user==ACTUAL for kind,user in seen if kind!='userRole'))
        self.assertNotIn(ACTUAL,json.dumps(data));self.assertNotIn(AGENT,json.dumps(data))

    def test_revoked_or_reassigned_agent_blocks_without_balance_reads(self):
        for role in ({'role':'missing'},{'role':'agent','data':{'user':'0x'+'56'*20}}):
            seen=[]
            def fetch(kind,user):seen.append(kind);return role
            with self.assertRaises(ValueError):account.observe(CONFIG,100000,fetch)
            self.assertEqual(seen,['userRole'])

    def test_invalid_config_and_nonfinite_values_fail(self):
        for update in ({'schema':True},{'account':AGENT},{'account':'0x'+'00'*20},{'secret_key':'must-not-be-supported'}):
            with self.assertRaises(ValueError):account.identity(CONFIG|update)
        def broken(kind,user):
            row=fixture(kind,user)
            if kind=='userFees':row['userCrossRate']='NaN'
            return row
        with self.assertRaises(ValueError):account.observe(CONFIG,100000,broken)
        with self.assertRaises(ValueError):account.decode(b'{"schema":1,"schema":1}')
        with self.assertRaises(ValueError):account.decode(b'{"n":NaN}')
        with self.assertRaises(ValueError):account.numeric('1e999999999')

    def test_changed_role_and_duplicate_balances_block(self):
        def broken(kind,user):
            row=fixture(kind,user)
            if kind=='spotClearinghouseState':row['balances']*=2
            return row
        with self.assertRaises(ValueError):account.observe(CONFIG,100000,broken)
        with self.assertRaises(ValueError):account.observe(CONFIG|{'account_role':'subAccount'},100000,fixture)

    @unittest.skipIf(os.name=='nt','Durable directory fsync is verified on the Linux deployment host')
    def test_fresh_snapshot_and_interrupted_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=account.observe(CONFIG,100000,fixture)
            account.write_snapshot(root,data)
            self.assertEqual(account.summary(root,100100)['status'],'ACCOUNT_OBSERVED_READ_ONLY')
            self.assertEqual(account.summary(root,4000000)['status'],'STALE')
            self.assertEqual(account.summary(root,1)['status'],'STALE')
            (root/'account.new').write_bytes(b'partial')
            with self.assertRaises(ValueError):account.write_snapshot(root,data)
            self.assertEqual(json.loads((root/'account.json').read_bytes()),data)

    def test_no_exchange_or_arbitrary_request_type(self):
        with self.assertRaises(ValueError):account.post('order',ACTUAL)
        with self.assertRaises(ValueError):account.post('userRole','invalid')


if __name__=='__main__':unittest.main()
