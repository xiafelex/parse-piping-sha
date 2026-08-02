#!/usr/bin/env python3
"""Deterministic first-pass IDF 100 ↔ DXF final-pipe matcher for one chain."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import ezdxf


def dist(a,b): return math.dist(a,b)

def idf_pipes(path: Path):
    lines=path.read_text(errors='replace').splitlines()
    parsed=[]
    for n,text in enumerate(lines,1):
        fields=text.split()
        if not fields or not re.fullmatch(r'-?\d+',fields[0]): continue
        try: code=int(fields[0])
        except ValueError: continue
        parsed.append((n,code,text,fields))
    if any(code in {41,42,55} for _n,code,_t,_f in parsed):
        raise ValueError('not_chain_eligible: IDF contains branch-class geometry records')
    rows=[]
    for pos,(line,code,text,fields) in enumerate(parsed):
        if code!=100 or len(fields)<8 or not any(',' in x for x in fields[8:]): continue
        try: a=tuple(map(float,fields[1:4]));b=tuple(map(float,fields[4:7]));bore=float(fields[7])
        except ValueError: continue
        nearby=' '.join(x[2] for x in parsed[max(0,pos-4):pos+5]).upper()
        rows.append({'id':f'I{len(rows)+1:03d}','line':line,'a':a,'b':b,'bore':bore,
                     'length':dist(a,b),'context':{'flow':'FLOW' in nearby,
                     'elbow':any(x[1] in {35,36} for x in parsed[max(0,pos-3):pos+4]),
                     'terminal':pos==len(parsed)-1 or any(x[1] in {150,151,152} for x in parsed[pos+1:pos+5])}})
    if not rows: raise ValueError('not_chain_eligible: no valid IDF 100 records')
    return rows


def pipe_endpoints(doc, handles):
    points=[];length=0.
    for handle in handles:
        e=doc.entitydb[handle]
        q=[(v.dxf.location.x,v.dxf.location.y) for v in e.vertices]
        if len(q)!=2: raise ValueError(f'{handle} is not a two-point POLYLINE')
        points.extend(q);length+=dist(*q)
    ends=max(((dist(a,b),a,b) for a in points for b in points if a!=b),key=lambda x:x[0])
    return ends[1],ends[2],length


def dxf_chain(dxf: Path, records: Path):
    doc=ezdxf.readfile(dxf)
    raw=json.loads(records.read_text())
    items=[]
    for row in raw:
        if not row['kind'].endswith('_pipe'): continue
        a,b,length=pipe_endpoints(doc,row['handles'])
        items.append({'record_id':row['id'],'kind':row['kind'],'handles':row['handles'],
                      'a':a,'b':b,'length':length})
    if not items: raise ValueError('not_chain_eligible: DXF typed graph has no final pipes')
    # Prim's MST on vector-endpoint gaps.  A simple page must produce a unique
    # path; components and elbows are represented by the small gap between ends.
    if len(items)==1: return items
    links=[]
    for i,x in enumerate(items):
        for j,y in enumerate(items[:i]):
            gap=min(dist(p,q) for p in (x['a'],x['b']) for q in (y['a'],y['b']))
            links.append((gap,j,i))
    chosen=[];seen={0}
    while len(seen)<len(items):
        candidates=[x for x in links if (x[1] in seen) != (x[2] in seen)]
        if not candidates: raise ValueError('not_chain_eligible: disconnected DXF pipe graph')
        edge=min(candidates);chosen.append(edge);seen.add(edge[2] if edge[1] in seen else edge[1])
    adjacency={i:[] for i in range(len(items))}
    for gap,a,b in chosen:
        if gap>40: raise ValueError(f'not_chain_eligible: unexplained DXF gap {gap:.2f}')
        adjacency[a].append(b);adjacency[b].append(a)
    if any(len(v)>2 for v in adjacency.values()): raise ValueError('not_chain_eligible: DXF graph branches')
    start=next(i for i,v in adjacency.items() if len(v)<=1)
    ordered=[];previous=None;current=start
    while current is not None:
        ordered.append(items[current]); nxt=[x for x in adjacency[current] if x!=previous]
        previous,current=current,(nxt[0] if nxt else None)
    return ordered


def score_pair(i,d,slot,total):
    components={}
    # A matching positive arrow signature is strong evidence.  Two absent
    # arrow signatures are merely neutral; they must not outweigh topology.
    idf_arrow=i['context']['flow']; dxf_arrow=d['kind']=='arrow_pipe'
    components['role']=4 if idf_arrow and dxf_arrow else 1 if not idf_arrow and not dxf_arrow else 0
    components['turn_context']=2 if i['context']['elbow'] and slot>0 else 0
    components['terminal']=2 if i['context']['terminal'] and slot==total-1 else 0
    return components


def evaluate(idfs,dxfs):
    if len(idfs)!=len(dxfs): raise ValueError(f'not_chain_eligible: IDF 100={len(idfs)}, DXF final pipes={len(dxfs)}')
    il=[x['length']/sum(y['length'] for y in idfs) for x in idfs]
    dl=[x['length']/sum(y['length'] for y in dxfs) for x in dxfs]
    rows=[];total=0
    for slot,(i,d) in enumerate(zip(idfs,dxfs)):
        parts=score_pair(i,d,slot,len(idfs));parts['relative_length']=max(0,3-12*abs(il[slot]-dl[slot]))
        score=sum(parts.values());total+=score
        rows.append({'idf_id':i['id'],'dxf_record':d['record_id'],'handles':d['handles'],
                     'score':round(score,3),'components':parts})
    return total,rows


def main():
    ap=argparse.ArgumentParser();ap.add_argument('idf',type=Path);ap.add_argument('dxf',type=Path)
    ap.add_argument('dxf_records',type=Path);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--overlay-json',type=Path,help='write renderer-ready DXF overlay rows from the algorithm result')
    args=ap.parse_args()
    ids=[];dx=[]
    try:
        ids=idf_pipes(args.idf);dx=dxf_chain(args.dxf,args.dxf_records)
        forward,rows=evaluate(ids,dx);reverse,back=evaluate(ids,list(reversed(dx)))
    except ValueError as error:
        support_count=sum(x['kind'].startswith('support_') for x in dx)
        reason=str(error)
        if ids and dx and len(dx)>len(ids):
            reason='count_mismatch_support_segmentation: '+reason
        result={'algorithm':'CHAIN_100_V1','eligible':False,'reason':str(error),
                'idf_100_count':len(ids),'dxf_pipe_count':len(dx),
                'dxf_support_segment_count':support_count}
        result['reason']=reason
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False));return
    winning=rows if forward>=reverse else back; margin=abs(forward-reverse)
    confidence='high' if margin>=3 else 'medium' if margin>=1 else 'unresolved'
    for row in winning: row['confidence']=confidence;row['orientation_margin']=round(margin,3)
    result={'algorithm':'CHAIN_100_V1','eligible':True,'idf_100_count':len(ids),'dxf_pipe_count':len(dx),
            'forward_score':round(forward,3),'reverse_score':round(reverse,3),'orientation_margin':round(margin,3),
            'confidence':confidence,'matches':winning,'idf':ids,'dxf_chain':dx}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    if args.overlay_json:
        args.overlay_json.parent.mkdir(parents=True,exist_ok=True)
        args.overlay_json.write_text(json.dumps([
            {'idf_no':int(row['idf_id'][1:]),'label':row['idf_id'],'handles':row['handles'],
             'confidence':1.0 if confidence=='high' else .65 if confidence=='medium' else .0,
             'rationale':f"CHAIN_100_V1 score={row['score']}; margin={margin:.3f}"}
            for row in winning],ensure_ascii=False,indent=2))
    print(json.dumps({k:result[k] for k in ('algorithm','idf_100_count','dxf_pipe_count','forward_score','reverse_score','orientation_margin','confidence')},ensure_ascii=False))


if __name__=='__main__': main()
