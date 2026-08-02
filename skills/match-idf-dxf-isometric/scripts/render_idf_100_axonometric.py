#!/usr/bin/env python3
"""Render IDF 100 geometry in a canonical axonometric plane with stable I IDs."""
from __future__ import annotations
import argparse, math
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from build_idf_100_topology import parse
def main():
 ap=argparse.ArgumentParser();ap.add_argument('idf',type=Path);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 all_edges=parse(a.idf);edges=[e for e in all_edges if e['code']==100];branch=[e for e in all_edges if e['code']==41]
 # Orthogonal EN coordinates are rotated by their observed local grid.  Z is vertical;
 # this canonical view preserves topology and relative length, not DXF paper position.
 origin=(min(min(e['a'][0],e['b'][0]) for e in edges),min(min(e['a'][1],e['b'][1]) for e in edges),min(min(e['a'][2],e['b'][2]) for e in edges))
 def p(q):
  x,y,z=(q[i]-origin[i] for i in range(3));return ((x-y)*.5,(x+y)*.288675-z*.57735)
 fig,ax=plt.subplots(figsize=(12,12),facecolor='#151515');ax.set_facecolor('#151515')
 for e in branch:
  u,v=p(e['a']),p(e['b']);ax.plot((u[0],v[0]),(u[1],v[1]),color='#22d3ee',lw=2,ls='--')
 for n,e in enumerate(edges,1):
  u,v=p(e['a']),p(e['b']);ax.plot((u[0],v[0]),(u[1],v[1]),color='#facc15',lw=2.5);ax.text((u[0]+v[0])/2,(u[1]+v[1])/2,f'I{n:03d}',color='white',fontsize=6,path_effects=[pe.withStroke(linewidth=1.5,foreground='#111111')])
 ax.set_aspect('equal');ax.set_axis_off();ax.set_title(f'{a.idf.stem} — canonical IDF 100 axonometric topology',color='white');fig.tight_layout();a.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(a.output,dpi=200,facecolor=fig.get_facecolor())
if __name__=='__main__':main()
