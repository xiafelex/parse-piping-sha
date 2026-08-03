#!/usr/bin/env python3
"""Render original DXF, a DXF pipe skeleton, and an IDF ISO reconstruction.

DXF panels always use untransformed source coordinates. Only IDF E/N/Z record
geometry is projected and rigidly rotated onto the audited DXF north vector.
This is visual-fidelity evidence, not an I100 matching algorithm.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import ezdxf
import matplotlib.pyplot as plt

from build_idf_100_topology import parse


def unit(vector):
    length = math.hypot(*vector)
    return (vector[0] / length, vector[1] / length) if length else (-.5, .288675)


def project_idf(edges, north):
    origin = [min(min(edge['a'][axis], edge['b'][axis]) for edge in edges) for axis in range(3)]
    canonical_n = math.atan2(.288675, -.5)
    target_n = math.atan2(north[1], north[0])
    cosine, sine = math.cos(target_n - canonical_n), math.sin(target_n - canonical_n)
    def project(point):
        e, n, z = (point[index] - origin[index] for index in range(3))
        x, y = .5 * (e - n), .288675 * (e + n) + .57735 * z
        return (cosine * x - sine * y, sine * x + cosine * y)
    return project


def linework(doc):
    rows = []
    for entity in doc.modelspace():
        kind = entity.dxftype()
        try:
            if kind == 'LINE':
                rows.append([(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)])
            elif kind in {'LWPOLYLINE', 'POLYLINE'}:
                points = ([(p[0], p[1]) for p in entity.get_points('xy')]
                          if kind == 'LWPOLYLINE'
                          else [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices])
                if len(points) >= 2:
                    if entity.is_closed:
                        points.append(points[0])
                    rows.append(points)
            elif kind == 'ARC':
                centre = entity.dxf.center; start, end = math.radians(entity.dxf.start_angle), math.radians(entity.dxf.end_angle)
                if end < start: end += math.tau
                rows.append([(centre.x + entity.dxf.radius * math.cos(start + (end - start) * i / 24), centre.y + entity.dxf.radius * math.sin(start + (end - start) * i / 24)) for i in range(25)])
            elif kind == 'CIRCLE':
                centre = entity.dxf.center
                rows.append([(centre.x + entity.dxf.radius * math.cos(math.tau * i / 32), centre.y + entity.dxf.radius * math.sin(math.tau * i / 32)) for i in range(33)])
        except (AttributeError, TypeError):
            continue
    return rows


def limits(points, pad=.08):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1)
    return min(xs) - span * pad, max(xs) + span * pad, min(ys) - span * pad, max(ys) + span * pad


def triad(ax, origin, size, north):
    base = math.atan2(north[1], north[0])
    for label, offset, colour in [('N', 0, '#38bdf8'), ('E', -2 * math.pi / 3, '#f59e0b'), ('Z+', -math.pi / 3, '#22c55e')]:
        angle = base + offset; end = (origin[0] + size * math.cos(angle), origin[1] + size * math.sin(angle))
        ax.annotate('', xy=end, xytext=origin, arrowprops={'arrowstyle': '->', 'color': colour, 'lw': 1.7})
        ax.text(*end, label, color=colour, fontsize=8, ha='center', va='center')


def style(ax, title, points, north):
    ax.set_facecolor('#151515'); ax.set_aspect('equal'); ax.set_axis_off()
    xmin, xmax, ymin, ymax = limits(points)
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax)
    triad(ax, (xmin + (xmax - xmin) * .1, ymin + (ymax - ymin) * .1), max(xmax - xmin, ymax - ymin) * .08, north)
    ax.set_title(title, color='white', fontsize=10, pad=8)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf', type=Path); parser.add_argument('dxf', type=Path); parser.add_argument('dxf_topology', type=Path)
    parser.add_argument('--north-audit', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--page', type=int, help='DXF page number in a multi-page topology file')
    parser.add_argument('--idf-range', help='inclusive stable IDF range, e.g. I012:I019')
    args = parser.parse_args()
    north = unit(json.loads(args.north_audit.read_text()).get('vector_candidate') or [-.5, .288675])
    raw = linework(ezdxf.readfile(args.dxf)); raw_points = [point for row in raw for point in row]
    if not raw_points: raise SystemExit('DXF contains no supported source-vector primitives')
    topology = json.loads(args.dxf_topology.read_text())
    dxf_pipes = [row for row in topology['pipes'] if len(row.get('endpoints') or []) == 2 and (args.page is None or row.get('page') == args.page)]
    dxf_pipe_points = [point for pipe in dxf_pipes for point in pipe['endpoints']]
    if not dxf_pipe_points: raise SystemExit('DXF semantic topology has no endpointed pipes')
    all_edges = parse(args.idf)
    pipes = [edge for edge in all_edges if edge['code'] == 100]
    if args.idf_range:
        start, end = (int(value[1:]) for value in args.idf_range.split(':', 1))
        selected = [edge for edge in pipes if start <= int(edge['id'][1:]) <= end]
    else:
        selected = pipes
    if not selected: raise SystemExit('IDF range selected no 100 records')
    # A page-level reconstruction includes only the selected 100 records and
    # contiguous 35/36 elbow records.  It never crosses into the next page's
    # 100 records simply because coordinates happen to touch.
    elbow_pool = [edge for edge in all_edges if edge['code'] in {35, 36} and edge['a'] != edge['b']]
    known_points = {point for edge in selected for point in (edge['a'], edge['b'])}
    elbows = []
    while True:
        additions = [edge for edge in elbow_pool if edge not in elbows and (edge['a'] in known_points or edge['b'] in known_points)]
        if not additions: break
        elbows.extend(additions)
        known_points.update(point for edge in additions for point in (edge['a'], edge['b']))
    design = selected + elbows
    project = project_idf(design, north)
    idf_rows = [(edge, project(edge['a']), project(edge['b'])) for edge in design]
    idf_points = [point for _edge, a, b in idf_rows for point in (a, b)]
    fig, axes = plt.subplots(1, 3, figsize=(21, 9), facecolor='#151515')
    for row in raw: axes[0].plot([p[0] for p in row], [p[1] for p in row], color='#d1d5db', lw=.35, alpha=.85)
    style(axes[0], '1. Original DXF piping area (raw source coordinates)', dxf_pipe_points, north)
    for row in raw: axes[1].plot([p[0] for p in row], [p[1] for p in row], color='#4b5563', lw=.25, alpha=.65)
    for index, pipe in enumerate(dxf_pipes):
        a, b = pipe['endpoints']; axes[1].plot((a[0], b[0]), (a[1], b[1]), color='#facc15', lw=2.3, solid_capstyle='butt')
        axes[1].text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, f'P{index:03d}', color='white', fontsize=5)
    style(axes[1], '2. DXF-derived pipe skeleton (raw source coordinates)', dxf_pipe_points, north)
    for edge, a, b in idf_rows:
        colour = '#facc15' if edge['code'] == 100 else ('#22d3ee' if edge['code'] in {35, 36} else '#9ca3af')
        axes[2].plot((a[0], b[0]), (a[1], b[1]), color=colour, lw=2.5 if edge['code'] == 100 else 1.2, solid_capstyle='butt')
        if edge['code'] == 100: axes[2].text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, edge['id'], color='white', fontsize=5)
    style(axes[2], '3. IDF E/N/Z ISO reconstruction (only IDF transformed)', idf_points, north)
    fig.suptitle(f'{args.idf.stem} — one-page ISO orientation audit; yellow=100, cyan=IDF 35/36 elbow geometry', color='white', fontsize=14)
    fig.tight_layout(); args.output.parent.mkdir(parents=True, exist_ok=True); fig.savefig(args.output, dpi=190, facecolor=fig.get_facecolor())
    print(json.dumps({'idf_100': len(selected), 'idf_range': args.idf_range, 'dxf_page': args.page, 'dxf_pipes': len(dxf_pipes), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__': main()
