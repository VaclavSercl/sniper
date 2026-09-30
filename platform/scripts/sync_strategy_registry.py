#!/usr/bin/env python3
"""Read-only research status; no invented legacy T15 PASS or Git publication."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
from strategy_lifecycle import report, safe_path

def load_report(state_dir, candle_state_dir=None):
    path=safe_path(Path(state_dir)/'research.sqlite3')
    if not path.is_file():
        return {'status':'BLOCKED','reason':'RESEARCH_REGISTRY_MISSING',
                'live_eligible':0,'paper_qualified':0}
    con=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        result=report(con)
        result['status']='OBSERVED'
        result['legacy_t15']='INVALIDATED_NOT_QUALIFICATION_EVIDENCE'
        if candle_state_dir is not None:
            from candle_research import status as candle_status
            result['candle_research']=candle_status(candle_state_dir)
            result['catalog'].append({'id':'T1','status':'UNQUALIFIED',
                'reason':'Exploratory spot candle screen; annual-data and execution gates unmet'})
        return result
    finally:
        con.close()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--state-dir',type=Path,default=Path('/var/lib/sniper/research'))
    ap.add_argument('--candle-state-dir',type=Path)
    args=ap.parse_args()
    try:
        result=load_report(args.state_dir,args.candle_state_dir)
    except (OSError,ValueError,sqlite3.Error) as exc:
        result={'status':'BLOCKED','reason':type(exc).__name__}
    print(json.dumps(result,indent=2,allow_nan=False))
    return 2 if result['status']=='BLOCKED' else 0

if __name__=='__main__':
    raise SystemExit(main())
