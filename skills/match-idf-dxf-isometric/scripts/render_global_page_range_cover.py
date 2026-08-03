#!/usr/bin/env python3
"""Render a review-only whole-IDF page-range cover beside original DXF page pipe vectors."""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import matplotlib.pyplot as plt

COLOURS = ['#f59e0b','#22c55e','#38bdf8','#e879f9','#f97316','#a3e635','#c084fc','#fb7185']

def project(points, north=None):
    origin = [min(p[i] for p in points) for i in range(3)]
    # Canonical N is (-.5, .288675).  Rotate it onto the source DXF north
    # vector so the review drawing uses the same paper orientation.
    canonical_n = math.atan2(.288675, -.5)
    target_n = math.atan2(north[1], north[0]) if north else canonical_n
    c, s = math.cos(target_n-canonical_n), math.sin(target_n-canonical_n)
    def p(v):
        x,y,z = [v[i]-origin[i] for i in range(3)]
        u,w = (x-y)*.5, (x+y)*.288675+z*.57735
        return (c*u-s*w, s*u+c*w)
    return p

def axis_triad(ax, origin, size, north):
    # In the verified E/N/Z projection: E is clockwise 120° from N and +Z
    # is clockwise 60° from N (screen-up for the source drawing).
    base = math.atan2(north[1], north[0])
    for label, offset, colour in [('N',0,'#38bdf8'),('E',-2*math.pi/3,'#f59e0b'),('Z+',-math.pi/3,'#22c55e')]:
        x,y=origin; angle=base+offset;dx,dy=size*math.cos(angle),size*math.sin(angle)
        ax.annotate('',xy=(x+dx,y+dy),xytext=(x,y),arrowprops={'arrowstyle':'->','color':colour,'lw':1.8})
        ax.text(x+dx*1.1,y+dy*1.1,label,color=colour,fontsize=8,ha='center',va='center')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('idf_topology',type=Path);ap.add_argument('global_graph',type=Path);ap.add_argument('cover',type=Path);ap.add_argument('--north-audit',type=Path,help='source-vector DXF north audit for paper-orientation alignment');ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    idf=json.loads(a.idf_topology.read_text()); graph=json.loads(a.global_graph.read_text()); cover=json.loads(a.cover.read_text())
    ranges=cover.get('best',{}).get('page_ranges',[])
    if not ranges: raise SystemExit('cover has no best page ranges')
    page_for={}
    for row in ranges:
        lo,hi=(int(x[1:]) for x in row['idf_range'])
        for n in range(lo,hi+1): page_for[f'I{n:03}']=row['page']
    north = None
    if a.north_audit and a.north_audit.exists():
        north = json.loads(a.north_audit.read_text()).get('vector_candidate')
    if not north: north = [-.5,.288675]
    norm=math.hypot(*north);north=[north[0]/norm,north[1]/norm]
    points=[x for p in idf['pipes'] for x in (p['a'],p['b'])];pr=project(points,north)
    pages=[r['page'] for r in ranges];fig=plt.figure(figsize=(16,4+3*((len(pages)+2)//3)),facecolor='#151515')
    gs=fig.add_gridspec(2,1,height_ratios=[1.2,max(1,(len(pages)+2)//3)])
    ax=fig.add_subplot(gs[0]);ax.set_facecolor('#151515');ax.set_aspect('equal');ax.set_axis_off()
    for p in idf['pipes']:
        page=page_for.get(p['id']);col=COLOURS[pages.index(page)%len(COLOURS)] if page in pages else '#6b7280';u,v=pr(p['a']),pr(p['b']);ax.plot((u[0],v[0]),(u[1],v[1]),color=col,lw=3);ax.text((u[0]+v[0])/2,(u[1]+v[1])/2,p['id'],color='white',fontsize=6)
    projected=[pr(p) for p in points]; xmin,xmax=min(p[0] for p in projected),max(p[0] for p in projected);ymin,ymax=min(p[1] for p in projected),max(p[1] for p in projected)
    axis_triad(ax,(xmin,ymin),max(xmax-xmin,ymax-ymin)*.09,north)
    ax.set_title('IDF complete 100 topology — colour = structurally assigned DXF page range; axes aligned to source DXF north',color='white',fontsize=13)
    sub=gs[1].subgridspec((len(pages)+2)//3,3)
    for pos,page in enumerate(pages):
        dax=fig.add_subplot(sub[pos]);dax.set_facecolor('#151515');dax.set_aspect('equal');dax.set_axis_off();col=COLOURS[pos%len(COLOURS)]
        for p in graph['pipes']:
            if p['page']!=page:continue
            u,v=p['endpoints'];dax.plot((u[0],v[0]),(u[1],v[1]),color=col,lw=2.6);dax.text((u[0]+v[0])/2,(u[1]+v[1])/2,p['id'].rsplit(':',1)[-1],color='white',fontsize=5)
        # Repeat the same source-sheet axes in every raw-DXF pane. This keeps
        # the projection comparison auditable rather than IDF-only.
        dxf_points=[q for p in graph['pipes'] if p['page']==page for q in p['endpoints']]
        if dxf_points:
            dxmin,dxmax=min(q[0] for q in dxf_points),max(q[0] for q in dxf_points)
            dymin,dymax=min(q[1] for q in dxf_points),max(q[1] for q in dxf_points)
            axis_triad(dax,(dxmin,dymin),max(dxmax-dxmin,dymax-dymin)*.12,north)
        row=next(r for r in ranges if r['page']==page);dax.set_title(f'DXF page {page}: {row["idf_range"][0]}–{row["idf_range"][1]}',color='white',fontsize=9)
    fig.suptitle(f'{cover["line_key"]} — global page-range relation (review-only; not individual I→P proof)',color='white',fontsize=15);fig.tight_layout();a.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(a.output,dpi=180,facecolor=fig.get_facecolor())
if __name__=='__main__':main()
