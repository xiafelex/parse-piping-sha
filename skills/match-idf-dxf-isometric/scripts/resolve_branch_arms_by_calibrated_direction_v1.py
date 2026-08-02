#!/usr/bin/env python3
"""Resolve the two remaining arms of an anchored three-way branch.

This is intentionally narrower than generic geometric matching.  It accepts
direction only after a project-level D4 transform was calibrated from other
pages and one branch arm was independently anchored by source-vector contact.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path


def transform(vector, name):
    x, y = vector
    return {'identity': (x, y), 'flip_x': (-x, y), 'flip_y': (x, -y),
            'flip_xy': (-x, -y), 'swap': (y, x), 'swap_flip_x': (-y, x),
            'swap_flip_y': (y, -x), 'swap_flip_xy': (-y, -x)}[name]


def unit(vector):
    size = math.hypot(*vector)
    return (vector[0] / size, vector[1] / size) if size else None


def away_from_frame(pipe, centre):
    endpoint = max(pipe['endpoints'], key=lambda point: math.dist(point, centre))
    return unit((endpoint[0] - centre[0], endpoint[1] - centre[1]))


def away_from_idf(pipe, frame):
    centre = frame['centre']
    a, b = pipe['a2'], pipe['b2']
    endpoint = max((a, b), key=lambda point: math.dist(point, centre))
    return unit((endpoint[0] - centre[0], endpoint[1] - centre[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('frame_graph', type=Path)
    parser.add_argument('dxf_topology', type=Path)
    parser.add_argument('propagation', type=Path)
    parser.add_argument('--page', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.frame_graph.read_text())
    topology = json.loads(args.dxf_topology.read_text())
    source = json.loads(args.propagation.read_text())
    axis = source.get('axis_transform')
    if axis not in {'identity', 'flip_x', 'flip_y', 'flip_xy', 'swap', 'swap_flip_x', 'swap_flip_y', 'swap_flip_xy'}:
        raise SystemExit('requires an independently calibrated axis_transform in propagation input')
    idf_frames = {item['id']: item for item in graph['idf']['frames']}
    dxf_frames = {item['id']: item for item in graph['dxf']['frames'] if item['page'] == args.page}
    idf_pipes = {item['id']: item for item in graph['idf']['pipe_geometry']}
    dxf_pipes = {item['id']: item for item in topology['pipes'] if item['page'] == args.page}
    frame_map = {item['idf_frame']: item['dxf_frame'] for item in source.get('frame_matches', [])}
    pipe_map = {item['idf_pipe']: item['dxf_pipe'] for item in source['pipe_matches'] if item.get('dxf_pipe')}
    additions = []
    for left_id, right_id in frame_map.items():
        left, right = idf_frames.get(left_id), dxf_frames.get(right_id)
        if not left or not right or left['kind'] != 'junction_3' or right['kind'] not in {'branch', 'tee'}:
            continue
        left_arms = list(left['incident_pipes'])
        right_arms = list(right['incident_pipes'])
        anchored = {pipe: pipe_map[pipe] for pipe in left_arms if pipe in pipe_map and pipe_map[pipe] in right_arms}
        if not (len(left_arms) == len(right_arms) == 3) or len(anchored) != 1:
            continue
        remaining_left = [pipe for pipe in left_arms if pipe not in anchored]
        remaining_right = [pipe for pipe in right_arms if pipe not in anchored.values()]
        if not (len(remaining_left) == len(remaining_right) == 2):
            continue
        candidates = []
        for perm in itertools.permutations(remaining_right):
            pairs = list(zip(remaining_left, perm))
            cosines = []
            for idf_pipe, dxf_pipe in pairs:
                a = away_from_idf(idf_pipes[idf_pipe], left)
                b = away_from_frame(dxf_pipes[dxf_pipe], right['centre'])
                if not a or not b:
                    break
                cosines.append(sum(x * y for x, y in zip(transform(a, axis), b)))
            if len(cosines) == 2:
                candidates.append((min(cosines), sum(cosines), pairs, cosines))
        candidates.sort(key=lambda row: (-row[0], -row[1], row[2]))
        if len(candidates) != 2:
            continue
        best, runner = candidates
        # Both outgoing directions must agree well and the full arm assignment
        # must beat the swapped assignment by a tangible margin.
        if best[0] < .80 or best[1] - runner[1] < .50:
            continue
        for (idf_pipe, dxf_pipe), cosine in zip(best[2], best[3]):
            pipe_map[idf_pipe] = dxf_pipe
            additions.append({'idf_pipe': idf_pipe, 'dxf_pipe': dxf_pipe,
                              'idf_frame': left_id, 'dxf_frame': right_id,
                              'axis_transform': axis, 'direction_cosine': round(cosine, 5),
                              'assignment_margin': round(best[1] - runner[1], 5)})
    added = {item['idf_pipe']: item for item in additions}
    rows = []
    for row in source['pipe_matches']:
        item = dict(row)
        if item['idf_pipe'] in added:
            item.update({'dxf_pipe': added[item['idf_pipe']]['dxf_pipe'],
                         'confidence': 'medium_calibrated_branch_direction',
                         'evidence': 'anchored_three_way_branch_plus_independent_project_axis'})
        rows.append(item)
    result = {**source, 'algorithm': 'CALIBRATED_BRANCH_ARM_DIRECTION_V1',
              'policy': 'only resolve two residual three-way arms after independent project-axis calibration and one non-directional arm anchor',
              'pipe_matches': rows, 'calibrated_branch_direction_additions': additions}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'page': args.page, 'additions': len(additions)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
