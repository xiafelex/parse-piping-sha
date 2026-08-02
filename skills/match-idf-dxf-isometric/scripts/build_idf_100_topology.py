#!/usr/bin/env python3
"""Extract an IDF geometry graph while preserving stable 100 IDs and branches."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def parse(path: Path):
    edges=[]; serial=0
    for line, text in enumerate(path.read_text(errors='replace').splitlines(), 1):
        fields=text.split()
        if len(fields)<8 or not re.fullmatch(r'-?\d+',fields[0]): continue
        try:
            code=int(fields[0]);a=tuple(map(float,fields[1:4]));b=tuple(map(float,fields[4:7]));bore=float(fields[7])
        except ValueError: continue
        if code==100 and not any(',' in value for value in fields[8:]): continue
        if code==100: serial+=1
        edges.append({'line':line,'code':code,'a':a,'b':b,'bore':bore,'id':f'I{serial:03d}' if code==100 else None})
    return edges


def main():
    ap=argparse.ArgumentParser();ap.add_argument('idf',type=Path);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    edges=parse(args.idf);nodes=defaultdict(list)
    for edge in edges:
        nodes[edge['a']].append((edge,'a'))
        if edge['b']!=edge['a']:nodes[edge['b']].append((edge,'b'))
    # Ordinary component/weld records (notably 150) make a coordinate's raw
    # incidence look like degree three even on a simple chain.  In these IDFs
    # record 41 is the observed branch/outlet connector.  55 must *not* be
    # used: it occurs on ordinary two-ended inline geometry and previously
    # created false branch anchors.  Retain raw degree as evidence rather than
    # using it as the classification itself.
    branch=[]
    for point,incident in nodes.items():
        non_self=[entry for entry in incident if entry[0]['a']!=entry[0]['b']]
        if any(edge['code'] == 41 for edge,_side in non_self):
            branch.append({'point':point,
                           'role':'junction' if len(non_self) >= 3 else 'outlet_leg',
                           'degree':len(non_self),'incident_100':[edge['id'] for edge,_side in non_self if edge['id']],
                           'incident_codes':[edge['code'] for edge,_side in non_self]})
    pipes=[]
    for edge in edges:
        if edge['code']!=100:continue
        pipes.append({**edge,'endpoint_signature':[
            {'degree':len([x for x in nodes[p] if x[0]['a']!=x[0]['b']]),
             'codes':sorted(x[0]['code'] for x in nodes[p] if x[0] is not edge)} for p in (edge['a'],edge['b'])]})
    result={'idf':args.idf.name,'idf_100_count':len(pipes),'geometry_edge_count':len(edges),'branch_nodes':branch,'pipes':pipes}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'idf_100_count':len(pipes),'branch_node_count':len(branch),'branches':branch},ensure_ascii=False))
if __name__=='__main__':main()
