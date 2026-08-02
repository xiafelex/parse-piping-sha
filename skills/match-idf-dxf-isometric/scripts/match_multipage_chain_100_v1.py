#!/usr/bin/env python3
"""Conservative IDF 100 matcher for an explicitly ordered multi-page DXF chain."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def chain_module():
    path = Path(__file__).with_name('match_chain_100_v1.py')
    spec = importlib.util.spec_from_file_location('chain_100_v1', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def page_chain(chain, dxf: Path, records: Path, page_number: int):
    rows = json.loads(records.read_text())
    if not rows:
        return []
    ordered = chain.dxf_chain(dxf, records)
    for row in ordered:
        row['page_number'] = page_number
        row['page_record'] = row['record_id']
        row['record_id'] = f'P{page_number:03d}:{row["record_id"]}'
    return ordered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('idf', type=Path)
    ap.add_argument('--page', action='append', required=True,
                    help='ordered dxf|records|page_number; repeat once per page')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args(); chain = chain_module()
    try:
        idfs = chain.idf_pipes(args.idf)
        dxfs=[]; page_audit=[]
        for raw in args.page:
            dxf_text, records_text, page_text = raw.split('|', 2)
            page = int(page_text); dxf = Path(dxf_text); records = Path(records_text)
            ordered = page_chain(chain, dxf, records, page)
            page_audit.append({'page':page,'dxf':dxf.name,'record_count':len(ordered)})
            dxfs.extend(ordered)
        forward, rows = chain.evaluate(idfs, dxfs)
        reverse, backwards = chain.evaluate(idfs, list(reversed(dxfs)))
        winning = rows if forward >= reverse else backwards; margin=abs(forward-reverse)
        for row in winning:
            target=next(x for x in dxfs if x['record_id']==row['dxf_record'])
            row['page_number']=target['page_number'];row['page_record']=target['page_record']
        result={'algorithm':'MULTIPAGE_CHAIN_100_V1','eligible':True,'idf_100_count':len(idfs),'dxf_pipe_count':len(dxfs),
                'pages':page_audit,'forward_score':round(forward,3),'reverse_score':round(reverse,3),'orientation_margin':round(margin,3),
                'confidence':'high' if margin>=3 else 'medium' if margin>=1 else 'unresolved','matches':winning,'idf':idfs,'dxf_chain':dxfs}
    except (ValueError, FileNotFoundError) as error:
        result={'algorithm':'MULTIPAGE_CHAIN_100_V1','eligible':False,'reason':str(error)}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({key:result.get(key) for key in ('algorithm','eligible','idf_100_count','dxf_pipe_count','forward_score','reverse_score','orientation_margin','confidence','reason')},ensure_ascii=False))


if __name__=='__main__':main()
