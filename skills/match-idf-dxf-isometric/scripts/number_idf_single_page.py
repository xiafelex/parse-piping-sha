#!/usr/bin/env python3
"""Number IDF 100 straight pipes and 120 welds in source order for review."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt


def records(path: Path):
    rows=[]
    for line_no,text in enumerate(path.read_text(errors='replace').splitlines(),1):
        fields=text.split()
        if len(fields)<8 or fields[0] not in {'100','120'} or not any(',' in x for x in fields[8:]):
            continue
        try:
            values=[float(x) for x in fields[1:7]]
        except ValueError:
            continue
        rows.append({'code':int(fields[0]),'line':line_no,'raw':text,
                     'a':values[:3],'b':values[3:6], 'bore':float(fields[7])})
    counters={100:0,120:0}
    for row in rows:
        counters[row['code']]+=1
        row['id']=('I' if row['code']==100 else 'W')+f"{counters[row['code']]:03d}"
    return rows


def iso(p):
    x,y,z=p
    return ((x-y)*math.sqrt(3)/2,(x+y)/2+z)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('idf',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    rows=records(args.idf)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/'idf-numbered.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    fig,ax=plt.subplots(figsize=(15,11),facecolor='#151515');ax.set_facecolor('black')
    geometry=[]
    for row in rows:
        a,b=iso(row['a']),iso(row['b']);geometry.extend((a,b))
        if row['code']==100:
            ax.plot((a[0],b[0]),(a[1],b[1]),color='#facc15',lw=3.2,solid_capstyle='butt',zorder=2)
            p=((a[0]+b[0])/2,(a[1]+b[1])/2)
        else:
            p=a
            ax.plot(p[0],p[1],marker='o',markersize=6,color='#f472b6',zorder=4)
        ax.text(*p,row['id'],color='white',fontsize=8,ha='center',va='center',zorder=5,
                bbox={'fc':'#151515','ec':'none','pad':.25})
    if geometry:
        xs,ys=zip(*geometry);pad=max(max(xs)-min(xs),max(ys)-min(ys))*.06
        ax.set_xlim(min(xs)-pad,max(xs)+pad);ax.set_ylim(min(ys)-pad,max(ys)+pad)
    ax.set_aspect('equal');ax.set_axis_off()
    pipes=sum(x['code']==100 for x in rows);welds=sum(x['code']==120 for x in rows)
    ax.set_title(f'{args.idf.name} — IDF source-order numbering | {pipes} × 100, {welds} × 120',color='white',pad=12)
    fig.tight_layout();fig.savefig(args.output_dir/'idf-numbered.png',dpi=220,facecolor=fig.get_facecolor())


if __name__=='__main__':
    main()
