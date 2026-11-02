"""Run the existing Chengda global solver in an isolated import process.

Only geometric candidate/layout functions are called; no SHA writer is invoked.
Input/output coordinates are page millimetres, scaled to solver units internally.
"""
import json
import sys
from pathlib import Path

CHENGDA = Path(__file__).resolve().parents[2]/'chengda_indonesia'/'parser'
sys.path.insert(0, str(CHENGDA))
from inject_sha_weld_callouts import (
    PointTarget, LayoutRegion, candidate_options, solve_page_layout,
    candidate_conflict_reasons, leader_source_crossing_counts,
)

def main():
    data=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    scale=40.0
    points=[PointTarget(i,str(i),p[0]*scale,p[1]*scale,1) for i,p in enumerate(data['points'])]
    segments=[tuple(v*scale for v in s)+(0,0) for s in data['segments']]
    boxes=[tuple(v*scale for v in b) for b in data['boxes']]
    region=LayoutRegion((0,0,420*scale,297*scale),(10*scale,10*scale,285*scale,228*scale),'UAE custom template')
    options=[]
    for i,p in enumerate(points):
        choices=candidate_options(p,segments,boxes,boxes,region,
            marker_radius=data['radius']*scale,gap=60,
            normal=(0.5,0.8660254),marker_shape='circle',
            root_clearance=100,max_candidates=40,peer_points=tuple(points),
            allow_fine_fallback=False)
        if not choices:raise ValueError(f'No collision-free candidate for weld {i}; refusing overlap fallback')
        print(f'Weld {i+1}: {len(choices)} valid candidates',flush=True)
        options.append(choices)
    placements=solve_page_layout(points,options,allow_conflicts=False)
    selected=[next(c for c in options[i] if c.placement==p) for i,p in enumerate(placements)]
    conflicts=[]
    for i,a in enumerate(selected):
        for j in range(i+1,len(selected)):
            reasons=candidate_conflict_reasons(points[i],a,points[j],selected[j])
            if reasons:conflicts.append([i,j,reasons])
    assert not conflicts
    crossings=[]
    for p,chosen in zip(points,placements):
        crossings.append(leader_source_crossing_counts((p.center_x,p.center_y,chosen.leader_x,chosen.leader_y),segments,boxes,root_clearance=100))
    assert not any(a or b for a,b in crossings),crossings
    result={'placements':[[p.center_x/scale,p.center_y/scale,p.leader_x/scale,p.leader_y/scale] for p in placements],
            'solver':str(CHENGDA/'inject_sha_weld_callouts.py'),'pair_conflicts':conflicts,
            'source_crossings_outside_root_allowance':crossings,'root_allowance_mm':2.5,
            'obstacle_segments':len(segments),'text_boxes':len(boxes),'candidate_counts':[len(o) for o in options]}
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
