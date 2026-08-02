#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,math
from collections import Counter
from pathlib import Path
from build_idf_100_topology import parse
def main():
 ap=argparse.ArgumentParser();ap.add_argument('idf',type=Path);ap.add_argument('dxf_topology',type=Path);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 c=Counter()
 for e in parse(a.idf):
  if e['code']!=100:continue
  v=[e['b'][i]-e['a'][i] for i in range(3)];l=math.sqrt(sum(x*x for x in v));c[tuple(round(x/l,3) for x in v)]+=1
 d=json.loads(a.dxf_topology.read_text());q=Counter()
 for x in d['pipes']:
  if not x['endpoints']:continue
  p,r=x['endpoints'];q[round((math.degrees(math.atan2(r[1]-p[1],r[0]-p[0]))%180)/5)*5]+=1
 out={'idf':a.idf.name,'dxf':d['dxf'],'idf_axis_vectors':{'/'.join(map(str,k)):v for k,v in c.items()},'dxf_axis_angles_degrees':dict(q),'policy':'direction-only; no page-coordinate correspondence'};a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
