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
