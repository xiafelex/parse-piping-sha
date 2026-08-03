#!/usr/bin/env python3
"""Extract review-only page-port candidates from original DXF continuation labels.

The label is not a connection proof.  It merely provides a source-vector
neighbourhood in which the nearest pipe endpoint can be reviewed as a likely
page port before global assembly scores page combinations.
"""
from __future__ import annotations
import argparse, json, math, re
from pathlib import Path
import ezdxf

def text_rows(path):
    doc=ezdxf.readfile(path); out=[]
    for e in doc.modelspace():
        if e.dxftype() not in {'TEXT','MTEXT'}: continue
        text=e.dxf.text if e.dxftype()=='TEXT' else e.text
        if 'CONT' not in text.upper(): continue
        target=re.search(r'DRG\s*(\d+)',text.upper())
        pt=e.dxf.insert; out.append({'text':text.replace('\\P',' '),'point':[pt.x,pt.y],
                                      'target_page':int(target.group(1)) if target else None})
    return out
def main():
    ap=argparse.ArgumentParser();ap.add_argument('global_graph',type=Path);ap.add_argument('dxf_dir',type=Path);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    graph=json.loads(a.global_graph.read_text()); pipes={p['id']:p for p in graph['pipes']};rows=[]
    by_source={Path(p['source']).name:p['source'] for p in graph['pipes']}
    for source_name,source in by_source.items():
        path=a.dxf_dir/source_name
        if not path.exists():continue
        endpoints=[]
        for node in graph['endpoint_nodes']:
            pid=node['id'].rsplit(':E',1)[0];pipe=pipes.get(pid)
            if pipe and pipe['source']==source:endpoints.append((node,pipe))
        for label in text_rows(path):
            x,y=label['point']; ranked=sorted(endpoints,key=lambda item:(item[0]['point'][0]-x)**2+(item[0]['point'][1]-y)**2)
            if not ranked:continue
            node,pipe=ranked[0]; distance=math.dist(label['point'],node['point'])
            rows.append({'page':pipe['page'],'source':source_name,'label':label,'candidate_endpoint':node['id'],
                         'candidate_pipe':pipe['id'].rsplit(':',1)[-1],'pipe_kind':pipe['kind'],
                         'endpoint_role':node['role'],'distance':round(distance,3),
                         'status':'review_candidate_not_connection'})
    out={'algorithm':'LABEL_NEIGHBOUR_SOURCE_VECTOR_PORT_V1','policy':'CONT labels seed a nearby endpoint review only; global topology must confirm any page join','candidates':rows}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({'candidates':len(rows)},ensure_ascii=False))
if __name__=='__main__':main()
