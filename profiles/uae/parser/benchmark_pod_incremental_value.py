"""Test whether POD is needed to associate PCF weld identities with SHA dots.

Baseline deliberately never reads POD. POD is an independent validation pass.
This benchmark is not a production annotation algorithm or construction numbering.
"""
from pathlib import Path
from collections import defaultdict, Counter
import csv
import json
import math
import re
import struct
import olefile
from compare_three_format_fields import pcf_read
from analyze_iso_split import read_sha_streams, forward_owned_dynamic_graphics
from sha_contours import records
from pod_decode import decode

ROOT=Path(__file__).resolve().parents[4]
PROJECT=ROOT/'阿联酋项目'
OUT=PROJECT/'POD实际增益验证'


def sha_weld_baseline(pcf, sha):
    """PCF+SHA only: UCI -> graphic -> declared owner tree -> circle."""
    blocks,_,_=pcf_read(pcf)
    welds=[b for b in blocks if b['type']=='WELD']
    streams=read_sha_streams(sha)
    bindings=forward_owned_dynamic_graphics(streams.get('Unclustered Dynamic Attributes',b''))
    sheets={}
    for name,data in streams.items():
        if not re.fullmatch(r'Sheet\d+',name) or name=='Sheet39817':continue
        by_id={};children=defaultdict(list);circles={}
        for off,kind,rec in records(data):
            if len(rec)<14:continue
            ref,parent=struct.unpack_from('<II',rec,6)
            by_id[ref]=(off,kind)
            children[parent].append(ref)
            if kind==0x59 and len(rec)==49:
                x,y,r=struct.unpack_from('<3d',rec,24)
                if all(math.isfinite(v) for v in (x,y,r)) and 0<r<=.0015:
                    circles[ref]={'sheet':name,'ref':ref,'offset':off,'x_mm':x*1000,'y_mm':y*1000,'radius_mm':r*1000}
        sheets[name]=(by_id,children,circles)
    results=[]
    for w in welds:
        uci=w['attributes'].get('UCI',[''])[0]
        candidates=[];roots=[]
        for b in bindings.get(uci,[]):
            for name,(by_id,children,circles) in sheets.items():
                root=b['graphic_ref']
                if root not in by_id:continue
                roots.append((name,root))
                todo=[root];seen=set()
                while todo:
                    ref=todo.pop()
                    if ref in seen:continue
                    seen.add(ref)
                    if ref in circles:candidates.append(circles[ref])
                    todo.extend(children.get(ref,[]))
        unique={(p['sheet'],p['ref']):p for p in candidates}
        results.append({'uci':uci,'pcf_id':w['attributes'].get('COMPONENT-IDENTIFIER',[''])[0],
            'pcf_status':w['attributes'].get('STATUS',[]),'pcf_material_list':w['attributes'].get('MATERIAL-LIST',[]),
            'pcf_line':w['line'],'sha_roots':sorted(set(roots)),'sha_circle_candidates':list(unique.values())})
    return results, [p for _,_,circles in sheets.values() for p in circles.values()]


def main():
    OUT.mkdir(exist_ok=True)
    all_rows=[];coverage=[]
    for pod in sorted(p for d in PROJECT.glob('30201*') for p in d.rglob('*-pod.pod')):
        base=pod.name.removesuffix('-pod.pod');pcf=pod.with_name(base+'-pcf.pcf');sha=pod.with_name(base+'.sha')
        if not pcf.exists() or not sha.exists():continue
        baseline,all_circles=sha_weld_baseline(pcf,sha)
        # Only now open POD: no POD-derived identity/coordinate enters baseline.
        with olefile.OleFileIO(pod) as ole:model=decode(ole.openstream('PODFile').read())
        by_uci=defaultdict(list)
        for c in model['components']:by_uci[c['uci']].append(c)
        groups={g['offset']:g for g in model['graphics']}
        stats=Counter()
        for row in baseline:
            row['drawing']=base
            cs=by_uci.get(row['uci'],[])
            pod_circles=[p for c in cs for off in c['drawing_group_offsets'] for p in groups[off]['primitives'] if p['kind']=='circle']
            points={tuple(p['drawing_xy']) for c in cs for p in c['ports'] if not p['name'].startswith('Centre_')}
            sha_candidates=row['sha_circle_candidates']
            deltas=[math.hypot(s['x_mm']-p['center'][0]/1000,s['y_mm']-p['center'][1]/1000)
                for s in sha_candidates for p in pod_circles]
            port_deltas=[math.hypot(s['x_mm']-x/1000,s['y_mm']-y/1000) for s in sha_candidates for x,y in points]
            fallback={(s['sheet'],s['ref']) for s in all_circles for p in pod_circles
                if math.hypot(s['x_mm']-p['center'][0]/1000,s['y_mm']-p['center'][1]/1000)<.001
                and abs(s['radius_mm']-p['radius']/1000)<.001}
            status='unique_circle' if len(sha_candidates)==1 else 'no_circle' if not sha_candidates else 'multiple_circles'
            stats[status]+=1;stats['pcf_welds']+=1
            if len(row['sha_roots'])==1:stats['unique_sha_owner_group']+=1
            if status=='no_circle' and row['pcf_status']==['DOTTED-UNDIMENSIONED'] and row['pcf_material_list']==['EXCLUDE']:
                stats['no_circle_with_explicit_pcf_reference_flags']+=1
            if len(cs)==1:stats['unique_pod_object']+=1
            if len(sha_candidates)==1 and deltas and min(deltas)<.001:stats['sha_unique_pod_circle_agrees_0_001mm']+=1
            if len(sha_candidates)==1 and port_deltas and min(port_deltas)<.1:stats['sha_unique_pod_port_agrees_0_1mm']+=1
            if len(sha_candidates)!=1 and len(fallback)==1:stats['pod_unique_geometry_fallback_candidate']+=1
            row.update(status=status,pod_types=sorted({c['type_name'] for c in cs}),
                pod_circle_count=len(pod_circles),pod_ports_xy=list(points),
                circle_delta_mm=min(deltas) if deltas else None,port_delta_mm=min(port_deltas) if port_deltas else None,
                pod_geometry_candidates=sorted(fallback))
            all_rows.append(row)
        coverage.append({'drawing':base,**dict(stats)})
        if len(coverage)%20==0:print(f'benchmarked {len(coverage)}',flush=True)
    totals=Counter()
    for row in coverage:totals.update({k:v for k,v in row.items() if k!='drawing'})
    (OUT/'benchmark.json').write_text(json.dumps({'files':len(coverage),'totals':dict(totals),'coverage':coverage,'welds':all_rows},ensure_ascii=False,indent=2),encoding='utf-8')
    columns=['drawing','pcf_id','uci','status','sha_roots','sha_circle_candidates','pod_types','pod_circle_count','circle_delta_mm','port_delta_mm','pod_geometry_candidates']
    with (OUT/'焊口定位验证.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        for r in all_rows:writer.writerow({k:json.dumps(r[k],ensure_ascii=False) if isinstance(r[k],(list,dict)) else r[k] for k in columns})
    print(json.dumps({'files':len(coverage),'totals':dict(totals)},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
