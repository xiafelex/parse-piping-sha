#!/usr/bin/env python3
"""Render an IDF-local topology and raw-DXF-local vector pair for a hypothesis."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import ezdxf
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe


def segments(entity):
    if entity.dxftype() == 'LINE':
        yield ((entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y))
    elif entity.dxftype() == 'LWPOLYLINE':
        points = [(point[0], point[1]) for point in entity.get_points('xy')]
        yield from zip(points, points[1:])
    elif entity.dxftype() == 'POLYLINE':
        points = [(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in entity.vertices]
        yield from zip(points, points[1:])


def project(points):
    origin = [min(point[axis] for point in points) for axis in range(3)]
    return lambda point: ((point[0] - origin[0] - (point[1] - origin[1])) * .5,
                          ((point[0] - origin[0] + point[1] - origin[1]) * .288675 +
                           (point[2] - origin[2]) * .57735))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('idf_topology', type=Path)
    parser.add_argument('component_frame_graph', type=Path)
    parser.add_argument('dxf', type=Path)
    parser.add_argument('dxf_pipe_topology', type=Path)
    parser.add_argument('hypotheses', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    idf = json.loads(args.idf_topology.read_text())
    graph = json.loads(args.component_frame_graph.read_text())
    hypothesis = json.loads(args.hypotheses.read_text())['hypotheses'][0]
    pipes = {pipe['id']: pipe for pipe in idf['pipes']}
    p = pipes[hypothesis['idf_pipe']]
    local_ids = {hypothesis['idf_pipe']}
    frame_by_id = {frame['id']: frame for frame in graph['idf']['frames']}
    for frame_id in [hypothesis['idf_frame'], *hypothesis['idf_continuation_frames']]:
        local_ids.update(frame_by_id[frame_id]['incident_pipes'])
    local = [pipes[pipe_id] for pipe_id in local_ids]
    to2 = project([point for pipe in local for point in (pipe['a'], pipe['b'])])
    dxf_pipes = {pipe['id']: pipe for pipe in json.loads(args.dxf_pipe_topology.read_text())['pipes']}
    selected = dxf_pipes[hypothesis['dxf_pipe']]
    handles = {handle.upper() for handle in selected['handles']}
    doc = ezdxf.readfile(args.dxf)
    raw = {entity.dxf.handle.upper(): list(segments(entity)) for entity in doc.modelspace()
           if entity.dxftype() in {'LINE', 'LWPOLYLINE', 'POLYLINE'}}
    target = [point for handle in handles for pair in raw.get(handle, []) for point in pair]
    xs, ys = zip(*target)
    fig, axes = plt.subplots(1, 2, figsize=(16, 8), facecolor='#151515')
    left, right = axes
    for axis in axes:
        axis.set_facecolor('#151515'); axis.set_aspect('equal'); axis.set_axis_off()
    for pipe in local:
        a, b = to2(pipe['a']), to2(pipe['b'])
        color, width = ('#facc15', 5) if pipe['id'] == hypothesis['idf_pipe'] else ('#9ca3af', 2)
        left.plot((a[0], b[0]), (a[1], b[1]), color=color, linewidth=width, solid_capstyle='butt')
        if pipe['id'] == hypothesis['idf_pipe']:
            left.text((a[0]+b[0])/2, (a[1]+b[1])/2, pipe['id'], color=color, fontsize=13,
                      ha='center', va='center', path_effects=[pe.withStroke(linewidth=3, foreground='#151515')])
    for frame_id, label in [(hypothesis['idf_frame'], 'IDF branch'),
                            *[(item, 'IDF raw [41] continuation') for item in hypothesis['idf_continuation_frames']]]:
        c = to2(frame_by_id[frame_id]['centre3'])
        left.scatter(*c, s=35, color='#f472b6', zorder=4)
        left.text(c[0], c[1], f'{frame_id}\n{label}', color='#f472b6', fontsize=8, va='bottom')
    for parts in raw.values():
        for a, b in parts:
            right.plot((a[0], b[0]), (a[1], b[1]), color='#71717a', linewidth=.55)
    for handle in handles:
        for a, b in raw.get(handle, []):
            right.plot((a[0], b[0]), (a[1], b[1]), color='#facc15', linewidth=5, solid_capstyle='butt')
    right.text(sum(xs)/len(xs), sum(ys)/len(ys), hypothesis['dxf_pipe'].rsplit(':', 1)[1] + '\n' + hypothesis['idf_pipe'],
               color='#facc15', fontsize=13, ha='center', va='center',
               path_effects=[pe.withStroke(linewidth=3, foreground='#151515')])
    dxf_frames = {frame['id']: frame for frame in graph['dxf']['frames']}
    for frame_id, label in [(hypothesis['dxf_frame'], 'DXF branch'),
                            *[(item, 'DXF degree-2 branch') for item in hypothesis['dxf_continuation_frames']]]:
        c = dxf_frames[frame_id]['centre']; right.scatter(*c, s=35, color='#f472b6', zorder=4)
        right.text(c[0], c[1], frame_id.rsplit(':', 1)[1] + '\n' + label, color='#f472b6', fontsize=8, va='bottom')
    # The prior renderer padded only from the selected pipe length.  A short pipe
    # then produced a crop dominated by whitespace, even when its two structural
    # frames were nearby.  Include those frames in the local evidence bounds and
    # add a deliberately modest visual margin.
    frame_centres = [dxf_frames[frame_id]['centre'] for frame_id in
                     [hypothesis['dxf_frame'], *hypothesis['dxf_continuation_frames']]]
    crop_points = [*target, *frame_centres]
    crop_xs, crop_ys = zip(*crop_points)
    span = max(max(crop_xs) - min(crop_xs), max(crop_ys) - min(crop_ys), 1)
    margin = max(15, span * .25)
    right.set_xlim(min(crop_xs)-margin, max(crop_xs)+margin)
    right.set_ylim(min(crop_ys)-margin, max(crop_ys)+margin)
    left.set_title('IDF local topology — review evidence only', color='white')
    right.set_title('DXF original vectors — yellow is candidate only', color='white')
    fig.suptitle('RAW41_BRANCH_CONTINUATION_HYPOTHESIS_V1 — low confidence; no final assignment', color='white', fontsize=14)
    fig.tight_layout(); args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=220, facecolor=fig.get_facecolor()); plt.close(fig)
    print(json.dumps({'idf_pipe': hypothesis['idf_pipe'], 'dxf_pipe': hypothesis['dxf_pipe'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
