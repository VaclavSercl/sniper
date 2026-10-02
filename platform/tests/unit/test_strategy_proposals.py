"""Balanced economic families, shared daily quota and immutable old candidates."""
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research'))
import forward_pipeline as pipeline
import strategy_proposals as proposals
import strategy_recipes as recipes

NOW=1790879900000
MARKETS=[{'product':'spot','id':'hype'},{'product':'spot','id':'btc'}]


class Proposals(unittest.TestCase):
    def test_diversifies_next_day_preserving_existing_candidate_and_frozen_settings(self):
        with tempfile.TemporaryDirectory() as directory, closing(pipeline.connect(directory)) as con:
            pipeline.generate(con,MARKETS,NOW)
            before=con.execute('SELECT id,body FROM candidates ORDER BY id').fetchall()
            settings=con.execute('SELECT * FROM settings ORDER BY key').fetchall()
            self.assertEqual(proposals.generate(con,MARKETS,NOW)['status'],'DAILY_QUOTA')
            result=proposals.generate(con,MARKETS,NOW+pipeline.DAY)
            self.assertEqual(result['new_economic_mechanisms'],2)
            self.assertEqual(result['new_market_variants'],12)
            triggers=[json.loads(r[0])['trigger'] for r in con.execute('SELECT recipe FROM families WHERE created=?',(NOW+pipeline.DAY,))]
            self.assertEqual(set(triggers),{'reversal','breakout'})
            self.assertEqual(con.execute('SELECT * FROM settings ORDER BY key').fetchall(),settings)
            for identity,body in before:self.assertEqual(con.execute('SELECT body FROM candidates WHERE id=?',(identity,)).fetchone(),(body,))
            for identity, in con.execute('SELECT id FROM candidates'):pipeline.candidate(con,identity)
    def test_holding_and_unknown_parameters_and_json_duplicates_rejected(self):
        recipe=recipes.proposed(0);recipe['holding_hours']=1
        with self.assertRaises(ValueError):proposals.external(json.dumps(recipe).encode())
        recipe=recipes.proposed(0);recipe['code']='import os'
        with self.assertRaises(ValueError):proposals.external(json.dumps(recipe).encode())
        with self.assertRaises(ValueError):proposals.external(b'{"schema":1,"schema":1}')
    def test_semantic_dedup_and_total_daily_limit_for_external_proposals(self):
        with tempfile.TemporaryDirectory() as directory, closing(pipeline.connect(directory)) as con:
            recipe=recipes.proposed(0)
            result=proposals.generate(con,MARKETS,NOW,[recipe,dict(recipe,rationale='Different wording does not create economic novelty.')])
            self.assertEqual(result['new_economic_mechanisms'],2)
            self.assertEqual(con.execute('SELECT count(*) FROM families').fetchone()[0],2)
            self.assertEqual(proposals.generate(con,MARKETS,NOW)['new_market_variants'],0)
    def test_first_four_trigger_families_appear_within_three_days(self):
        with tempfile.TemporaryDirectory() as directory, closing(pipeline.connect(directory)) as con:
            for day in range(3):proposals.generate(con,MARKETS,NOW+day*pipeline.DAY)
            triggers={json.loads(r[0])['trigger'] for r in con.execute('SELECT recipe FROM families')}
            self.assertEqual(triggers,set(recipes.TRIGGERS))


if __name__=='__main__':unittest.main()
