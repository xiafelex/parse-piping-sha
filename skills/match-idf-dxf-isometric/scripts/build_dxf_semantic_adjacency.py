#!/usr/bin/env python3
"""Build vector-contact adjacency between typed DXF pipes and components."""
from __future__ import annotations
import argparse, json, math
from pathlib import Path

def dseg(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]; den=dx*dx+dy*dy
    t=0 if not den else max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den))
    return math.dist(p,(a[0]+t*dx,a[1]+t*dy))

def paths(component):
    result=[]
    for q in [component.get('outline',[]), *component.get('welds',[]), *component.get('subpaths',[])]:
        result.extend(zip(q,q[1:]))
    result.extend(component.get('strokes', []))
    return result

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('topology',type=Path);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--tolerance',type=float,default=1.1);args=ap.parse_args()
    d=json.loads(args.topology.read_text()); edges=[]
    for pi,pipe in enumerate(d['pipes']):
        for ci,component in enumerate(d['components']):
            segs=paths(component)
            if not segs: continue
            distances=[min((dseg(endpoint,a,b) for a,b in segs),default=float('inf')) for endpoint in pipe['endpoints']]
            best=min(distances,default=float('inf'))
            if best<=args.tolerance:
                edges.append({'pipe_index':pi,'pipe_handles':pipe['handles'],'pipe_kind':pipe['kind'],'component_index':ci,'component_kind':component['kind'],'component_handles':component['handles'],'distance':round(best,4)})
    out={'algorithm':'DXF_SEMANTIC_ADJACENCY_V1','dxf':d['dxf'],'pipe_count':len(d['pipes']),'component_count':len(d['components']),'edges':edges,'policy':'source-vector endpoint-to-confirmed-component contact only'}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({'dxf':d['dxf'],'edge_count':len(edges)},ensure_ascii=False))
if __name__=='__main__':main()
