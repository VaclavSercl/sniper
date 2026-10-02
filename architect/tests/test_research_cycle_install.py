import importlib.util
from pathlib import Path
import sys
import unittest

DIRECTORY=Path(__file__).resolve().parents[2]/'infra/beroun'
sys.path.insert(0,str(DIRECTORY))
spec=importlib.util.spec_from_file_location('research_install',DIRECTORY/'install_research_cycle.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture():
    return {'status':'RESEARCH_OBSERVED','historical_qualified':0,'paper_qualified':0,'live_eligible':0,
        'interrupted':0,'blueprints_registered':2,'registered_market_variants':12,
        'completed_screens':4,'blocked_product_models':8,'stages':[{'variant':str(i),
            'status':'SCREEN_REJECTED_TRAINING' if i<4 else 'BLOCKED_PRODUCT_MODEL',
            'paper_eligible':False,'live_eligible':False} for i in range(12)]}


class InstallationTests(unittest.TestCase):
    def test_actual_cycle_required_without_promotion(self):
        self.assertEqual(len(m.validate_first(fixture())),4)
        for key,value in (('status','NOT_STARTED'),('interrupted',1),('completed_screens',3),('live_eligible',1)):
            data=fixture();data[key]=value
            with self.assertRaises(ValueError):m.validate_first(data)

    def test_duplicate_or_qualifying_variant_refused(self):
        data=fixture();data['stages'][1]['variant']=data['stages'][0]['variant']
        with self.assertRaises(ValueError):m.validate_first(data)
        data=fixture();data['stages'][0]['paper_eligible']=True
        with self.assertRaises(ValueError):m.validate_first(data)

    def test_recovery_refuses_unknown_state_changed_units_or_wrong_created_scope(self):
        hashes={str(m.UNITS/name):'a'*64 for name in m.NAMES}
        intent={'status':'PREVIEW','files':{path:{'sha256':sha} for path,sha in hashes.items()}}
        result={'status':'FAILED_PRESERVED_FOR_RECONCILIATION','created':list(hashes)}
        m.failure_contract(intent,result,hashes,False)
        with self.assertRaises(ValueError):m.failure_contract(intent,result,hashes,True)
        with self.assertRaises(ValueError):m.failure_contract(intent,result,hashes|{next(iter(hashes)):'b'*64},False)
        with self.assertRaises(ValueError):m.failure_contract(intent,result|{'created':[]},hashes,False)
        with self.assertRaises(ValueError):m.failure_contract(intent,result|{'status':'INSTALLED'},hashes,False)

    def test_repair_budget_cannot_reset_or_adopt_ambiguous_lineage(self):
        self.assertEqual(m.next_repair([]),1);self.assertEqual(m.next_repair([1,2]),3)
        for history in ([1,2,3],[2],[1,1],[True],['1']):
            with self.assertRaises(ValueError):m.next_repair(history)
