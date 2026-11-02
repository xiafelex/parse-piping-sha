"""Chengda-style joint placement, one straight leader per supplied UAE number."""
import sys,json,math
from pathlib import Path
from dense_layout_adapter import solve_dense
CHENGDA=Path(__file__).resolve().parents[2]/'chengda_indonesia/parser'
sys.path.insert(0,str(CHENGDA))
import inject_sha_weld_callouts as layout_engine
from inject_sha_weld_callouts import (PointTarget,LayoutRegion,candidate_options,solve_page_layout,
    candidate_conflict_reasons,leader_source_crossing_counts,boxes_overlap,segment_intersects_box,SHEET_UNIT,
    _build_segment_grid,_build_box_grid,_grid_indexes)


def main():
    data=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));scale=40.
    targets=data['targets']
    points=[PointTarget(i,t['number'],t['anchor'][0]*scale,t['anchor'][1]*scale,1) for i,t in enumerate(targets)]
    segments=[tuple(v*scale/SHEET_UNIT for v in s)+(0,0) for s in data['segments']]
    primary=[tuple(v*scale/SHEET_UNIT for v in s)+(0,0) for s in data.get('blocking_segments',data['segments'])]
    boxes=[tuple(v*scale for v in b) for b in data['boxes']]
    sg=_build_segment_grid(segments);pg=_build_segment_grid(primary);bg=_build_box_grid(boxes)
    region=LayoutRegion((0,0,420*scale,297*scale),tuple(v*scale for v in data.get('region',[6,6,300,249])),'UAE native page')
    options=[]
    for i,(point,t) in enumerate(zip(points,targets)):
        weld=t['kind']=='weld';width,height=t['width']*scale,t['height']*scale
        kw=dict(marker_radius=width/2 if weld else max(width,height)/2,gap=60. if weld else 45.,
                normal=(.5,.8660254),marker_shape='circle' if weld else 'rectangle',
                marker_width=None if weld else width,marker_height=None if weld else height,
                root_clearance=120.,peer_points=tuple(points),allow_fine_fallback=False)
        choices=candidate_options(point,segments,boxes,boxes,region,max_candidates=384,best_effort=True,**kw)
        valid=[]
        for c in choices:
            marker=c.marker_box
            if any(boxes_overlap(marker,boxes[k]) for k in _grid_indexes(bg,marker)):continue
            if any(segment_intersects_box(tuple(v*SHEET_UNIT for v in segments[k][:4]),marker) for k in _grid_indexes(sg,marker)):continue
            line=(point.center_x,point.center_y,c.placement.leader_x,c.placement.leader_y)
            bounds=(min(line[0],line[2]),min(line[1],line[3]),max(line[0],line[2]),max(line[1],line[3]))
            crossed=leader_source_crossing_counts(line,[segments[k] for k in _grid_indexes(sg,bounds)],
                [boxes[k] for k in _grid_indexes(bg,bounds)],root_clearance=120.)
            main=leader_source_crossing_counts(line,[primary[k] for k in _grid_indexes(pg,bounds)],[],root_clearance=120.)[0]
            c.placement.score+=crossed[1]*50000+main*5000+max(0,crossed[0]-main)*30
            valid.append(c)
        if not valid:raise ValueError(f'No source-clear marker position for {t["kind"]}:{t["number"]}')
        valid.sort(key=lambda c:c.placement.score);options.append(valid)
        print(f'{i+1}/{len(points)} {t["number"]}: {len(valid)} straight candidates',flush=True)
    domains=[v[:96 if len(points)>48 else 48] for v in options]
    # Keep feasibility and the single-candidate improvement. The imported
    # solver's optional cubic pair-cost refinement is disproportionate for
    # dense mixed-tag pages; disable it only in this isolated UAE process,
    # after all original candidate costs have been computed.
    if len(points)>40:layout_engine.AVOIDABLE_SOURCE_CROSSING_PENALTY=math.inf
    try:placements=solve_dense(layout_engine,points,domains) if len(points)>48 else solve_page_layout(points,domains,node_limit=300000,allow_conflicts=False)
    except ValueError:
        domains=[v[:96] for v in options]
        placements=solve_page_layout(points,domains,node_limit=800000,allow_conflicts=False)
    chosen=[next(c for c in domains[i] if c.placement==p) for i,p in enumerate(placements)]
    conflicts=[]
    for i,a in enumerate(chosen):
        for j in range(i+1,len(chosen)):
            reasons=candidate_conflict_reasons(points[i],a,points[j],chosen[j])
            if reasons:conflicts.append([i,j,reasons])
    if conflicts:raise ValueError(f'Straight layout conflicts: {conflicts}')
    result=[]
    for t,point,c in zip(targets,points,chosen):
        p=c.placement;line=(point.center_x,point.center_y,p.leader_x,p.leader_y)
        main,text=leader_source_crossing_counts(line,primary,boxes,root_clearance=120.)
        all_lines,_=leader_source_crossing_counts(line,segments,boxes,root_clearance=120.)
        edge=[p.leader_x/scale,p.leader_y/scale]
        result.append({**t,'center':[p.center_x/scale,p.center_y/scale],'leader_end':edge,
            'route':[t['anchor'],edge],'marker_box':[v/scale for v in c.marker_box],
            'source_crossings':[main,text],'dimension_line_crossings':max(0,all_lines-main),
            'marker_shape':'circle' if t['kind']=='weld' else 'rectangle','leader_type':'straight'})
    Path(sys.argv[2]).write_text(json.dumps({'placements':result,'pair_conflicts':conflicts,
        'routed_leader_crossings':[],'leader_type':'straight','style':'chengda_indonesia',
        'root_clearance_mm':3.,'source_segments':len(segments),'source_boxes':len(boxes),
        'candidate_counts':[len(v) for v in domains]},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
