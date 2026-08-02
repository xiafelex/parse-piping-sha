#!/usr/bin/env python3
"""Safely extend existing I100↔DXF-pipe matches across degree-two frames.

The script is intentionally downstream of an independent pipe anchor (or the
exact-raw-continuation rule).  A frame can be paired only when that fixed pipe
has exactly one still-unpaired incident semantic frame on both sides and their
categories agree.  The opposite incident pipe of the paired degree-two frame
is then uniquely determined.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def category(frame, side):
    if side == 'idf':
        if frame['kind'] == 'junction_3': return 'junction'
        if frame['kind'] == 'turn_2': return 'elbow'
        if frame['kind'] == 'inline_2' and frame.get('bore_change'): return 'reducer'
    else:
        if frame['kind'] in {'branch', 'tee'} and frame['degree'] == 3: return 'junction'
        if frame['kind'] == 'elbow' and frame['degree'] == 2: return 'elbow'
        if frame['kind'] == 'reducer' and frame['degree'] == 2: return 'reducer'
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('component_frame_graph', type=Path)
    parser.add_argument('propagation', type=Path)
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    graph = json.loads(args.component_frame_graph.read_text())
    source = json.loads(args.propagation.read_text())
    allowed = {row['idf_pipe'] for row in source['pipe_matches']}
    idf_frames = {row['id']: row for row in graph['idf']['frames']}
    dxf_frames = {row['id']: row for row in graph['dxf']['frames'] if row['page'] == args.page}
    i_incidence = {row['pipe']: [frame for frame in row['frames'] if frame in idf_frames]
                   for row in graph['idf']['pipe_frame_incidence']}
    d_incidence = {row['pipe']: [frame for frame in row['frames'] if frame in dxf_frames]
                   for row in graph['dxf']['pipe_frame_incidence']}
    frame_map = {row['idf_frame']: row['dxf_frame'] for row in source.get('frame_matches', [])}
    frame_evidence = {row['idf_frame']: row['evidence'] for row in source.get('frame_matches', [])}
    pipe_map = {row['idf_pipe']: row['dxf_pipe'] for row in source['pipe_matches'] if row.get('dxf_pipe')}
    pipe_evidence = {row['idf_pipe']: row['evidence'] for row in source['pipe_matches'] if row.get('dxf_pipe')}
    added_frames, added_pipes = [], []

    def add_frame(left, right, evidence):
        if left in frame_map or right in frame_map.values(): return False
        if category(idf_frames[left], 'idf') != category(dxf_frames[right], 'dxf') or \
           category(idf_frames[left], 'idf') is None: return False
        frame_map[left] = right; frame_evidence[left] = evidence
        added_frames.append({'idf_frame': left, 'dxf_frame': right, 'evidence': evidence})
        return True

    def add_pipe(left, right, evidence):
        if left not in allowed or left in pipe_map or right in pipe_map.values(): return False
        pipe_map[left] = right; pipe_evidence[left] = evidence
        added_pipes.append({'idf_pipe': left, 'dxf_pipe': right, 'evidence': evidence})
        return True

    changed = True
    while changed:
        changed = False
        # A matched pipe can expose one unpaired, same-category frame.
        for left_pipe, right_pipe in list(pipe_map.items()):
            left = [frame for frame in i_incidence.get(left_pipe, []) if frame not in frame_map]
            right = [frame for frame in d_incidence.get(right_pipe, []) if frame not in frame_map.values()]
            if len(left) == len(right) == 1:
                changed |= add_frame(left[0], right[0], 'unique_unpaired_frame_on_existing_pipe_match')
        # A paired degree-two component with one known arm fixes only its
        # opposite arm.  Junctions are deliberately excluded.
        for left_frame, right_frame in list(frame_map.items()):
            left = [pipe for pipe in idf_frames[left_frame]['incident_pipes'] if pipe in allowed]
            right = list(dxf_frames[right_frame]['incident_pipes'])
            if len(left) != len(right) or len(left) != 2:
                continue
            known = [(pipe, pipe_map[pipe]) for pipe in left if pipe in pipe_map]
            if len(known) != 1 or known[0][1] not in right:
                continue
            other_left = next(pipe for pipe in left if pipe != known[0][0])
            other_right = next(pipe for pipe in right if pipe != known[0][1])
            changed |= add_pipe(other_left, other_right, 'opposite_arm_of_unique_degree2_component')

    rows = []
    new_ids = {row['idf_pipe'] for row in added_pipes}
    for row in source['pipe_matches']:
        item = dict(row)
        if item['idf_pipe'] in pipe_map:
            item['dxf_pipe'] = pipe_map[item['idf_pipe']]
            item['confidence'] = 'medium_component_continuation' if item['idf_pipe'] in new_ids else item['confidence']
            item['evidence'] = pipe_evidence[item['idf_pipe']]
        rows.append(item)
    result = {**source, 'algorithm': 'DEGREE2_FRAME_PROPAGATION_FROM_PIPE_MATCHES_V1',
              'policy': 'semantic degree-two frame on both sides and one existing matched arm only; no junction ordering, coordinates, CONT, or length',
              'frame_matches': [{'idf_frame': left, 'dxf_frame': right, 'evidence': frame_evidence[left]}
                                for left in sorted(frame_map)],
              'pipe_matches': rows, 'degree2_frame_additions': added_frames,
              'degree2_pipe_additions': added_pipes}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'page': args.page, 'frame_additions': len(added_frames),
                      'pipe_additions': len(added_pipes), 'total_pipes': len(pipe_map)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
