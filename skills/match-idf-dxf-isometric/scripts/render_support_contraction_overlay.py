#!/usr/bin/env python3
"""Render vector-anchored support-contraction groups for human review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import ezdxf
import matplotlib.pyplot as plt
import matplotlib.patheffects as effects


PALETTE = ['#facc15', '#22d3ee', '#fb923c', '#f472b6', '#a78bfa', '#84cc16', '#60a5fa']


def points(entity):
    return [(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in entity.vertices]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('dxf', type=Path); ap.add_argument('audit', type=Path)
    ap.add_argument('--output', required=True, type=Path); args = ap.parse_args()
    doc = ezdxf.readfile(args.dxf); audit = json.loads(args.audit.read_text())
    fig, ax = plt.subplots(figsize=(16, 18), facecolor='#151515'); ax.set_facecolor('#151515')
    # Background is original DXF linework only.  The coloured strokes below
    # directly reuse source POLYLINE handles from the audit.
    for entity in doc.modelspace().query('POLYLINE'):
        q = points(entity)
        if len(q) >= 2:
            xs, ys = zip(*q); ax.plot(xs, ys, color='#8d8d8d', lw=.34, zorder=1)
    # CHAIN_100_V1 maps one C record per I record; support contraction maps one
    # G group (containing one or more C records).  Render both with the same
    # source-vector visual contract so a reviewer can compare cases directly.
    groups = audit.get('groups') or [
        {'group_id': row['dxf_record'], 'members': [row['dxf_record']], 'handles': row['handles']}
        for row in audit.get('matches', [])
    ]
    mapping = {(row.get('dxf_group') or row.get('dxf_record')): row['idf_id'] for row in audit.get('matches', [])}
    for index, group in enumerate(groups):
        colour = PALETTE[index % len(PALETTE)]; midpoints=[]
        for handle in group['handles']:
            q = points(doc.entitydb[handle]); xs, ys = zip(*q)
            ax.plot(xs, ys, color=colour, lw=3.6, solid_capstyle='butt', zorder=8)
            midpoints.append(((xs[0]+xs[-1])/2, (ys[0]+ys[-1])/2))
        x = sum(p[0] for p in midpoints)/len(midpoints); y = sum(p[1] for p in midpoints)/len(midpoints)
        label = f"{mapping.get(group['group_id'],'?')} / {group['group_id']}\n" + '+'.join(group['members'])
        ax.text(x, y, label, color='white', ha='center', va='center', fontsize=7, zorder=20,
                path_effects=[effects.withStroke(linewidth=1.8, foreground='#151515')])
    ax.set_aspect('equal'); ax.set_axis_off(); ax.autoscale()
    ax.set_title(f"{args.dxf.stem} — {audit.get('algorithm','IDF–DXF 100 match')}\ncolour: one proposed IDF 100 group; labels: I### / G### or C### / source fragments", color='white', fontsize=11)
    fig.tight_layout(); args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=220, facecolor=fig.get_facecolor()); plt.close(fig)


if __name__ == '__main__':
    main()
