#!/usr/bin/env python3
"""Render one review-only two-page DXF assembly against complete IDF geometry."""
from __future__ import annotations
import argparse,json,math
from pathlib import Path
import ezdxf, matplotlib.pyplot as plt

def segs(e):
 if e.dxftype()=='LINE':yield ((e.dxf.start.x,e.dxf.start.y),(e.dxf.end.x,e.dxf.end.y))
 elif e.dxftype()=='LWPOLYLINE':
  p=[(x[0],x[1]) for x in e.get_points('xy')];yield from zip(p,p[1:])
def project(ps):
 o=[min(p[i] for p in ps) for i in range(3)]
 return lambda p: (
  (p[0]-o[0]-p[1]+o[1])*.5,
  (p[0]-o[0]+p[1]-o[1])*.288675-(p[2]-o[2])*.57735,
 )
def main():
 ap=argparse.ArgumentParser();ap.add_argument('idf',type=Path);ap.add_argument('graph',type=Path);ap.add_argument('dxf_dir',type=Path);ap.add_argument('--left',required=True);ap.add_argument('--right',required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 idf=json.loads(a.idf.read_text());g=json.loads(a.graph.read_text());pipes={p['id']:p for p in g['pipes']}
 def endpoint(spec):
  pid,ei=spec.rsplit(':E',1);p=pipes[pid];n=int(ei);q=tuple(p['endpoints'][n]);other=tuple(p['endpoints'][1-n]);return p,q,(q[0]-other[0],q[1]-other[1])
 lp,la,lv=endpoint(a.left);rp,ra,rv=endpoint(a.right)
 # Rotate right page so its outward source vector is opposite the left port ray.
 al=math.atan2(lv[1],lv[0]); ar=math.atan2(rv[1],rv[0]); theta=al+math.pi-ar;c,s=math.cos(theta),math.sin(theta)
 def xf(p):
  x,y=p[0]-ra[0],p[1]-ra[1];return (la[0]+c*x-s*y,la[1]+s*x+c*y)
 fig,axs=plt.subplots(1,2,figsize=(18,9),facecolor='#151515')
 for ax in axs:ax.set_facecolor('#151515');ax.set_aspect('equal');ax.set_axis_off()
 pp=project([x for p in idf['pipes'] for x in (p['a'],p['b'])])
 for p in idf['pipes']:
  u,v=pp(p['a']),pp(p['b']);axs[0].plot((u[0],v[0]),(u[1],v[1]),color='#facc15',lw=2)
 axs[0].set_title('Complete IDF 100 canonical axonometric',color='white')
 for page,colour,transform in [(lp['source'],'#22c55e',lambda q:q),(rp['source'],'#38bdf8',xf)]:
  doc=ezdxf.readfile(a.dxf_dir/Path(page).name)
  for e in doc.modelspace():
   if e.dxftype() not in {'LINE','LWPOLYLINE'}:continue
   for u,v in segs(e):
    u,v=transform(u),transform(v);axs[1].plot((u[0],v[0]),(u[1],v[1]),color='#78716c',lw=.35)
  for p in g['pipes']:
   if p['source']!=page:continue
   u,v=transform(tuple(p['endpoints'][0])),transform(tuple(p['endpoints'][1]));axs[1].plot((u[0],v[0]),(u[1],v[1]),color=colour,lw=2.5)
 axs[1].scatter(*la,s=45,color='#f472b6');axs[1].set_title('DXF two-page assembly candidate\ngreen=page A, blue=page B, pink=joined source-vector ports',color='white')
 fig.suptitle(f'Review-only assembly: {a.left.rsplit(":",1)[0].rsplit(":",1)[-1]} ↔ {a.right.rsplit(":",1)[0].rsplit(":",1)[-1]}',color='white');fig.tight_layout();a.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(a.output,dpi=180,facecolor=fig.get_facecolor())
if __name__=='__main__':main()
