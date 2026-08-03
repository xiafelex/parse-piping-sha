#!/usr/bin/env python3
"""Render a review-only whole-IDF page-range cover beside original DXF page pipe vectors."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import matplotlib.pyplot as plt

COLOURS = ['#f59e0b','#22c55e','#38bdf8','#e879f9','#f97316','#a3e635','#c084fc','#fb7185']

def project(points):
    origin = [min(p[i] for p in points) for i in range(3)]
    def p(v):
        x,y,z = [v[i]-origin[i] for i in range(3)]
        return ((x-y)*.5, (x+y)*.288675-z*.57735)
    return p

def main():
    ap=argparse.ArgumentParser();ap.add_argument('idf_topology',type=Path);ap.add_argument('global_graph',type=Path);ap.add_argument('cover',type=Path);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    idf=json.loads(a.idf_topology.read_text()); graph=json.loads(a.global_graph.read_text()); cover=json.loads(a.cover.read_text())
    ranges=cover.get('best',{}).get('page_ranges',[])
    if not ranges: raise SystemExit('cover has no best page ranges')
    page_for={}
    for row in ranges:
        lo,hi=(int(x[1:]) for x in row['idf_range'])
        for n in range(lo,hi+1): page_for[f'I{n:03}']=row['page']
    points=[x for p in idf['pipes'] for x in (p['a'],p['b'])];pr=project(points)
    pages=[r['page'] for r in ranges];fig=plt.figure(figsize=(16,4+3*((len(pages)+2)//3)),facecolor='#151515')
    gs=fig.add_gridspec(2,1,height_ratios=[1.2,max(1,(len(pages)+2)//3)])
    ax=fig.add_subplot(gs[0]);ax.set_facecolor('#151515');ax.set_aspect('equal');ax.set_axis_off()
    for p in idf['pipes']:
        page=page_for.get(p['id']);col=COLOURS[pages.index(page)%len(COLOURS)] if page in pages else '#6b7280';u,v=pr(p['a']),pr(p['b']);ax.plot((u[0],v[0]),(u[1],v[1]),color=col,lw=3);ax.text((u[0]+v[0])/2,(u[1]+v[1])/2,p['id'],color='white',fontsize=6)
    ax.set_title('IDF complete 100 topology — colour = structurally assigned DXF page range',color='white',fontsize=13)
    sub=gs[1].subgridspec((len(pages)+2)//3,3)
    for pos,page in enumerate(pages):
        dax=fig.add_subplot(sub[pos]);dax.set_facecolor('#151515');dax.set_aspect('equal');dax.set_axis_off();col=COLOURS[pos%len(COLOURS)]
        for p in graph['pipes']:
            if p['page']!=page:continue
            u,v=p['endpoints'];dax.plot((u[0],v[0]),(u[1],v[1]),color=col,lw=2.6);dax.text((u[0]+v[0])/2,(u[1]+v[1])/2,p['id'].rsplit(':',1)[-1],color='white',fontsize=5)
        row=next(r for r in ranges if r['page']==page);dax.set_title(f'DXF page {page}: {row["idf_range"][0]}–{row["idf_range"][1]}',color='white',fontsize=9)
    fig.suptitle(f'{cover["line_key"]} — global page-range relation (review-only; not individual I→P proof)',color='white',fontsize=15);fig.tight_layout();a.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(a.output,dpi=180,facecolor=fig.get_facecolor())
if __name__=='__main__':main()
