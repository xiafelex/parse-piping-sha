#!/usr/bin/env python3
"""Render an intentionally non-committal IDF/DXF range-boundary review image.

This is a human-review aid.  It never proposes individual I-to-P assignments:
the left panel only shows numbered IDF 100 segments, while the right panels
show the original DXF source vectors plus the independently detected DXF pipe
segments.  The user can therefore judge a page boundary without a renderer
silently turning a hypothesis into a match.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import ezdxf
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe


def dxf_segments(entity):
    if entity.dxftype() == 'LINE':
        yield ((entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y))
    elif entity.dxftype() == 'LWPOLYLINE':
        points = [(p[0], p[1]) for p in entity.get_points('xy')]
        yield from zip(points, points[1:])
    elif entity.dxftype() == 'POLYLINE':
        points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
        yield from zip(points, points[1:])


def project_axonometric(points):
    origin = [min(p[i] for p in points) for i in range(3)]
    def projected(point):
        x, y, z = (point[i] - origin[i] for i in range(3))
        return ((x - y) * .5, (x + y) * .288675 - z * .57735)
    return projected


def pipe_number(pipe_id):
    return pipe_id.rsplit(':', 1)[-1]


def draw_idf(ax, payload, start, end, boundary):
    pipes = payload['pipes']
    selected = pipes[start - 1:end]
    all_points = [p for pipe in payload['pipes'] for p in (pipe['a'], pipe['b'])]
    to2 = project_axonometric(all_points)
    displayed = [to2(p) for pipe in selected for p in (pipe['a'], pipe['b'])]
    xs, ys = zip(*displayed)
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1)
    for pipe in selected:
        a, b = to2(pipe['a']), to2(pipe['b'])
        color = '#f472b6' if pipe['id'] == boundary else '#facc15'
        width = 4.5 if pipe['id'] == boundary else 2.5
        ax.plot((a[0], b[0]), (a[1], b[1]), color=color, linewidth=width,
                solid_capstyle='butt', zorder=3)
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if pipe['id'] == boundary:
            # A short boundary edge can have its midpoint label obscured by
            # adjacent I labels.  Draw an explicit callout rather than relying
            # on the coloured stroke alone.
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = max((dx * dx + dy * dy) ** .5, 1)
            nx, ny = -dy / length, dx / length
            ax.annotate(f'{pipe["id"]}  ← unresolved', xy=(mx, my), xytext=(mx + nx * span * .11, my + ny * span * .11),
                        color='#f472b6', fontsize=9, ha='center', va='center', zorder=8,
                        arrowprops={'arrowstyle': '-', 'color': '#f472b6', 'lw': 1.5},
                        path_effects=[pe.withStroke(linewidth=3, foreground='#151515')])
        else:
            ax.text(mx, my, pipe['id'], color='white', fontsize=8,
                    ha='center', va='center', zorder=4,
                    path_effects=[pe.withStroke(linewidth=2.5, foreground='#151515')])
    # Draw actual IDF 41 branch vectors.  Their endpoint Z delta is retained
    # in the label: screen direction in an axonometric projection is not a
    # substitute for physical elevation direction.
    branch_count = 0
    local_ids = {pipe['id'] for pipe in selected}
    for branch in payload.get('branch_connectors_41', []):
        incident = [pipe['id'] for pipe in payload['pipes']
                    if any(distance <= 10 for distance in (
                        ((pipe['a'][0]-branch['a'][0])**2 + (pipe['a'][1]-branch['a'][1])**2 + (pipe['a'][2]-branch['a'][2])**2)**.5,
                        ((pipe['b'][0]-branch['a'][0])**2 + (pipe['b'][1]-branch['a'][1])**2 + (pipe['b'][2]-branch['a'][2])**2)**.5))]
        if not set(incident) & local_ids:
            continue
        a, b = to2(branch['a']), to2(branch['b'])
        ax.plot((a[0], b[0]), (a[1], b[1]), color='#22d3ee', linewidth=2.2, linestyle='--', zorder=6)
        dz = branch['b'][2] - branch['a'][2]
        label = f"{branch['id']}  {'UP' if dz > 0 else 'DOWN'} Z{dz:+.0f}"
        ax.text(b[0], b[1], label, color='#22d3ee', fontsize=7, ha='left', va='bottom', zorder=7,
                path_effects=[pe.withStroke(linewidth=2, foreground='#151515')])
        branch_count += 1
    # `150` is a one-point IDF support record.  It may be repeated by the
    # source export; show a single blue cross per projected point, only when
    # it lies within this review's local window.  The symbol is evidence, not
    # an I-to-P assignment.
    margin = span * .09
    seen = set()
    support_count = 0
    for support in payload.get('supports_150', []):
        x, y = to2(support['point'])
        key = (round(x, 3), round(y, 3))
        if key in seen or not (min(xs)-margin <= x <= max(xs)+margin and min(ys)-margin <= y <= max(ys)+margin):
            continue
        seen.add(key); support_count += 1
        ax.scatter(x, y, marker='x', s=65, color='#38bdf8', linewidths=2, zorder=7)
        ax.text(x, y, f"{support['id']}/150", color='#38bdf8', fontsize=7, va='bottom', ha='left', zorder=8,
                path_effects=[pe.withStroke(linewidth=2, foreground='#151515')])
    ax.set_title(f'IDF 100 local topology  {selected[0]["id"]}–{selected[-1]["id"]}\n'
                 f'pink = unresolved {boundary}; blue × = IDF record 150 support ({support_count} unique); '
                 f'cyan dashed = IDF 41 branch ({branch_count})',
                 color='white', fontsize=10)


def draw_dxf(ax, dxf_path, topology, title):
    doc = ezdxf.readfile(dxf_path)
    raw = {e.dxf.handle.upper(): list(dxf_segments(e)) for e in doc.modelspace()
           if e.dxftype() in {'LINE', 'LWPOLYLINE', 'POLYLINE'}}
    for parts in raw.values():
        for a, b in parts:
            ax.plot((a[0], b[0]), (a[1], b[1]), color='#71717a', linewidth=.42, zorder=1)
    pipes = [p for p in topology['pipes'] if Path(p['id'].rsplit(':', 1)[0]).name == dxf_path.name]
    point_pool = []
    colors = {
        'arrow_pipe': '#facc15', 'support_pipe': '#fb923c', 'support_weld_pipe': '#ea580c',
        'weld_pipe': '#22c55e', 'weld_empty_pipe': '#38bdf8',
    }
    for pipe in pipes:
        colour = colors.get(pipe['kind'], '#a78bfa')
        points = []
        for handle in pipe.get('handles', []):
            for a, b in raw.get(handle.upper(), []):
                ax.plot((a[0], b[0]), (a[1], b[1]), color=colour, linewidth=3.3,
                        solid_capstyle='butt', zorder=5)
                points.extend((a, b))
        if not points:
            points = [tuple(p) for p in pipe.get('endpoints', [])]
        if points:
            mx = sum(p[0] for p in points) / len(points); my = sum(p[1] for p in points) / len(points)
            ax.text(mx, my, pipe_number(pipe['id']), color='white', fontsize=6.5, ha='center', va='center',
                    zorder=6, path_effects=[pe.withStroke(linewidth=2, foreground='#151515')])
            point_pool.extend(points)
    # Make the DXF support evidence explicit as well.  Raw support geometry is
    # deliberately left visible below; the magenta cross is only its detected
    # centre, numbered in source-page order for manual count reconciliation.
    supports = [component for component in topology.get('components', [])
                if component.get('kind') == 'support' and Path(component.get('source', '')).name == dxf_path.name]
    for index, support in enumerate(supports, 1):
        x, y = support['centre']
        ax.scatter(x, y, marker='x', s=55, color='#f472b6', linewidths=1.8, zorder=8)
        ax.text(x, y, f'DS{index:02d}', color='#f472b6', fontsize=6.5, ha='left', va='bottom', zorder=9,
                path_effects=[pe.withStroke(linewidth=2, foreground='#151515')])
        point_pool.append((x, y))
    if point_pool:
        xs, ys = zip(*point_pool); span = max(max(xs) - min(xs), max(ys) - min(ys), 1)
        margin = span * .12
        ax.set_xlim(min(xs) - margin, max(xs) + margin); ax.set_ylim(min(ys) - margin, max(ys) + margin)
    ax.set_title(title + f'\nmagenta × = DXF support ({len(supports)}); colours are pipe categories only; no I-to-P assignment is implied',
                 color='white', fontsize=9)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf_topology', type=Path)
    parser.add_argument('dxf_pipe_topology', type=Path)
    parser.add_argument('--idf-range', nargs=2, type=int, required=True, metavar=('START', 'END'))
    parser.add_argument('--boundary', required=True, help='IDF ID, e.g. I037')
    parser.add_argument('--dxf', type=Path, nargs='+', required=True)
    parser.add_argument('--question', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    idf = json.loads(args.idf_topology.read_text())
    topology = json.loads(args.dxf_pipe_topology.read_text())
    cols = 1 + len(args.dxf)
    fig, axes = plt.subplots(1, cols, figsize=(7 * cols, 8), facecolor='#151515')
    if cols == 2:
        axes = list(axes)
    for ax in axes:
        ax.set_facecolor('#151515'); ax.set_aspect('equal'); ax.set_axis_off()
    draw_idf(axes[0], idf, *args.idf_range, args.boundary)
    for index, dxf_path in enumerate(args.dxf, 1):
        draw_dxf(axes[index], dxf_path, topology, f'DXF source vectors — page {dxf_path.stem[-7:-4]}')
    fig.suptitle(args.question + '\nNo assignment is asserted: please identify which DXF page/vector visibly contains the pink IDF boundary segment.',
                 color='white', fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, .91)); args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=220, facecolor=fig.get_facecolor()); plt.close(fig)
    print(json.dumps({'output': str(args.output), 'boundary': args.boundary}, ensure_ascii=False))


if __name__ == '__main__':
    main()
