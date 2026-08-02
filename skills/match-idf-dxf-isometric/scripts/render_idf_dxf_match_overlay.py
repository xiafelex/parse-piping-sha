#!/usr/bin/env python3
"""Render source-vector DXF with accepted/candidate IDF 100 labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import ezdxf
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe


def entity_segments(entity):
    kind = entity.dxftype()
    if kind == 'LINE':
        yield ((entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y))
    elif kind == 'LWPOLYLINE':
        points = [(p[0], p[1]) for p in entity.get_points('xy')]
        for a, b in zip(points, points[1:]):
            yield a, b
    elif kind == 'POLYLINE':
        points = [(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in entity.vertices]
        for a, b in zip(points, points[1:]):
            yield a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dxf', type=Path)
    ap.add_argument('matches', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    rows = json.loads(args.matches.read_text()).get('matches', [])
    selected = {}
    for row in rows:
        ident = row.get('idf_id', row.get('idf'))
        handles = row.get('handles', row.get('dxf_handles', []))
        for handle in handles:
            selected[handle.upper()] = ident
    doc = ezdxf.readfile(args.dxf)
    paths = {entity.dxf.handle.upper(): list(entity_segments(entity)) for entity in doc.modelspace()
             if entity.dxftype() in {'LINE', 'LWPOLYLINE', 'POLYLINE'}}
    all_points = [p for parts in paths.values() for segment in parts for p in segment]
    if not all_points:
        raise ValueError('DXF has no renderable LINE/LWPOLYLINE paths')
    fig, ax = plt.subplots(figsize=(16, 14), facecolor='#151515'); ax.set_facecolor('#151515')
    for parts in paths.values():
        for a, b in parts:
            ax.plot((a[0], b[0]), (a[1], b[1]), color='#a1a1aa', linewidth=.45, zorder=1)
    for handle, ident in selected.items():
        for a, b in paths.get(handle, []):
            ax.plot((a[0], b[0]), (a[1], b[1]), color='#facc15', linewidth=4, solid_capstyle='butt', zorder=5)
            x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            ax.text(x, y, ident, color='#facc15', fontsize=9, ha='center', va='center', zorder=6,
                    path_effects=[pe.withStroke(linewidth=2, foreground='#111111')])
    xs, ys = zip(*all_points); margin = max(max(xs)-min(xs), max(ys)-min(ys))*.03
    ax.set_xlim(min(xs)-margin,max(xs)+margin);ax.set_ylim(min(ys)-margin,max(ys)+margin);ax.set_aspect('equal');ax.set_axis_off()
    ax.set_title(f'{args.dxf.stem} — yellow: IDF 100 match/candidate label at source-vector segment', color='white', fontsize=11)
    fig.tight_layout();args.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(args.output,dpi=220,facecolor=fig.get_facecolor());plt.close(fig)
    print(json.dumps({'dxf':args.dxf.name,'label_count':len(set(selected.values())),'output':str(args.output)},ensure_ascii=False))


if __name__ == '__main__':
    main()
