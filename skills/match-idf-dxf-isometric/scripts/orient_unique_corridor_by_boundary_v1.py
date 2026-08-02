#!/usr/bin/env python3
"""Orient one already-unique corridor by a matching external component boundary."""
from __future__ import annotations
import argparse, json
from collections import defaultdict
from pathlib import Path
from propagate_unique_corridor_signature_v1 import idf_transitions, dxf_transitions

def labels_idf(graph, topology, pipe):
    links = defaultdict(list)
    for a,b,_ in idf_transitions(topology): links[a].append(b);links[b].append(a)
    frames={x['id']:x for x in graph['idf']['frames']}; inc={x['pipe']:x['frames'] for x in graph['idf']['pipe_frame_incidence']}
    out=[]
    for n in links[pipe]:
        for f in inc.get(n,[]):
            if frames[f]['kind']=='junction_3': out.append('junction')
    return set(out)

def labels_dxf(graph, topology, page, pipe):
    links=defaultdict(list)
    for a,b,_ in dxf_transitions(topology,page): links[a].append(b);links[b].append(a)
    frames={x['id']:x for x in graph['dxf']['frames']};inc={x['pipe']:x['frames'] for x in graph['dxf']['pipe_frame_incidence']}
    out=[]
    for n in links[pipe]:
        for f in inc.get(n,[]):
            if frames[f]['kind'] in {'branch','tee'}:out.append('junction')
    return set(out)

def main():
 p=argparse.ArgumentParser();p.add_argument('frame_graph',type=Path);p.add_argument('idf',type=Path);p.add_argument('dxf',type=Path);p.add_argument('candidate',type=Path);p.add_argument('--page',type=int,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 g=json.loads(a.frame_graph.read_text());i=json.loads(a.idf.read_text());d=json.loads(a.dxf.read_text());s=json.loads(a.candidate.read_text())
 pairs=s.get('corridor_candidates',[])
 if len(pairs)!=2: raise SystemExit('requires exactly forward/reverse corridor candidates')
 forward=pairs[0]; left=forward['idf_path']; opts=[]
 for cand in pairs:
  right=cand['dxf_path']; score=(bool(labels_idf(g,i,left[0]) & labels_dxf(g,d,a.page,right[0])),bool(labels_idf(g,i,left[-1]) & labels_dxf(g,d,a.page,right[-1])))
  opts.append((sum(score),score,cand))
 opts.sort(key=lambda x:-x[0])
 result={**s,'algorithm':'ORIENT_UNIQUE_CORRIDOR_BY_BOUNDARY_V1','boundary_orientation_additions':[]}
 if opts[0][0]>=1 and opts[0][0]>opts[1][0]:
  mapping=dict(zip(left,opts[0][2]['dxf_path'])); rows=[]
  for row in s['pipe_matches']:
   z=dict(row)
   if z['idf_pipe'] in mapping:z.update(dxf_pipe=mapping[z['idf_pipe']],confidence='medium_corridor_boundary',evidence='unique_corridor_signature_plus_external_component_boundary')
   rows.append(z)
  result.update(pipe_matches=rows,status='unique_corridor_oriented_by_boundary',boundary_orientation_additions=[{'idf_pipe':x,'dxf_pipe':mapping[x]} for x in left])
 else: result['status']='candidate_requires_external_boundary'
 a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'status':result['status'],'additions':len(result['boundary_orientation_additions'])}))
if __name__=='__main__':main()
