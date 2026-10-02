import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research'))
import strategy_recipes as recipes


def bars(end=48*3600000):
    return [{'time':t,'open':'100','high':'102','low':'99','close':'101','volume':'10'} for t in range(end-48*3600000,end,3600000)]


class RecipeTests(unittest.TestCase):
    def test_economic_ids_unique_and_parameters_not_new_hypotheses(self):
        ids={recipes.economic_id(recipes.proposed(i)) for i in range(len(recipes.GRAMMAR))}
        self.assertEqual(len(ids),336)
        first=recipes.proposed(0); changed=copy.deepcopy(first)
        changed.update(holding_hours=12,lookback_hours=24,rationale='Different words do not create a new economic mechanism.')
        self.assertEqual(recipes.economic_id(first),recipes.economic_id(changed))
        self.assertEqual(recipes.CAMPAIGN['max_trials'],1848)

    def test_untrusted_generated_code_and_contradictions_refused(self):
        for key,value in (('trigger',"__import__('os').system('bad')"),('filters',['trend_up','trend_down']),('holding_hours',True)):
            candidate=recipes.proposed(0);candidate[key]=value
            with self.assertRaises(ValueError): recipes.validate(candidate)
        candidate=recipes.proposed(0);candidate['code']='print(1)'
        with self.assertRaises(ValueError): recipes.validate(candidate)

    def test_future_gap_and_warmup(self):
        data=bars(); decision=48*3600000
        self.assertFalse(recipes.signal(recipes.proposed(0),data[:47],decision))
        for changed in (data[:-1]+[{**data[-1],'time':decision}],data[:10]+data[11:]+[data[-1]]):
            with self.assertRaises(ValueError): recipes.signal(recipes.proposed(0),changed,decision)

    def test_causal_momentum_and_regime_conditions(self):
        data=bars();data[-1].update(close='103',high='104')
        self.assertTrue(recipes.signal(recipes.proposed(0),data,48*3600000))
        recipe=recipes.proposed(0);recipe['filters']=['session_europe']
        self.assertFalse(recipes.signal(recipe,data,48*3600000))


if __name__=='__main__': unittest.main()
