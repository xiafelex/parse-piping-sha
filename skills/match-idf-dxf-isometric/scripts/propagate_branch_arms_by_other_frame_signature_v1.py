#!/usr/bin/env python3
"""Resolve paired-branch arms from their other-side semantic frame signature.

At an already paired degree-three junction, branch-arm drawing order is not a
topological fact.  This rule instead looks only at what each arm touches away
from that junction: elbow, reducer, another junction, or open.  A mapping is
emitted only when the complete injective assignment has one strict best
signature score while respecting any existing arm matches.
"""
from __future__ import annotations

import argparse
import itertools
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


def signature(pipe, through, incidence, frames, side):
    others = [frame for frame in incidence.get(pipe, []) if frame != through]
    labels = sorted(category(frames[frame], side) for frame in others if category(frames[frame], side))
    return tuple(labels) if labels else ('open',)


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
    left_frames = {row['id']: row for row in graph['idf']['frames']}
    right_frames = {row['id']: row for row in graph['dxf']['frames'] if row['page'] == args.page}
    left_incidence = {row['pipe']: [item for item in row['frames'] if item in left_frames]
                      for row in graph['idf']['pipe_frame_incidence']}
    right_incidence = {row['pipe']: [item for item in row['frames'] if item in right_frames]
                       for row in graph['dxf']['pipe_frame_incidence']}
    frame_map = {row['idf_frame']: row['dxf_frame'] for row in source.get('frame_matches', [])
                 if isinstance(row.get('dxf_frame'), str)}
    pipe_map = {row['idf_pipe']: row['dxf_pipe'] for row in source['pipe_matches'] if row.get('dxf_pipe')}
    evidence = {row['idf_pipe']: row['evidence'] for row in source['pipe_matches'] if row.get('dxf_pipe')}
    additions = []
    for left_frame, right_frame in frame_map.items():
        if left_frame not in left_frames or right_frame not in right_frames:
            continue
        if not (category(left_frames[left_frame], 'idf') == category(right_frames[right_frame], 'dxf') == 'junction' and
                left_frames[left_frame]['degree'] == right_frames[right_frame]['degree'] == 3):
            continue
        left = [pipe for pipe in left_frames[left_frame]['incident_pipes'] if pipe in allowed]
        right = list(right_frames[right_frame]['incident_pipes'])
        if not (len(left) == len(right) == 3):
            continue
        existing = {pipe: pipe_map[pipe] for pipe in left if pipe in pipe_map and pipe_map[pipe] in right}
        if not existing:
            continue
        possibilities = []
        for permutation in itertools.permutations(right):
            candidate = dict(zip(left, permutation))
            if any(candidate[pipe] != target for pipe, target in existing.items()):
                continue
            score = 0
            valid = True
            for left_pipe, right_pipe in candidate.items():
                a = signature(left_pipe, left_frame, left_incidence, left_frames, 'idf')
                b = signature(right_pipe, right_frame, right_incidence, right_frames, 'dxf')
                if a != b:
                    valid = False; break
                score += 10
            if valid:
                possibilities.append((score, candidate))
        possibilities.sort(key=lambda row: (-row[0], sorted(row[1].items())))
        if len(possibilities) != 1:
            continue
        _, candidate = possibilities[0]
        for left_pipe, right_pipe in candidate.items():
            if left_pipe in pipe_map or right_pipe in pipe_map.values():
                continue
            pipe_map[left_pipe] = right_pipe
            evidence[left_pipe] = 'unique_branch_arm_other_side_semantic_signature'
            additions.append({'idf_pipe': left_pipe, 'dxf_pipe': right_pipe,
                              'idf_frame': left_frame, 'dxf_frame': right_frame,
                              'other_side_signature': signature(left_pipe, left_frame, left_incidence, left_frames, 'idf')})
    rows = []
    new = {row['idf_pipe'] for row in additions}
    for row in source['pipe_matches']:
        item = dict(row)
        if item['idf_pipe'] in pipe_map:
            item['dxf_pipe'] = pipe_map[item['idf_pipe']]
            item['confidence'] = 'medium_branch_signature' if item['idf_pipe'] in new else item['confidence']
            item['evidence'] = evidence[item['idf_pipe']]
        rows.append(item)
    result = {**source, 'algorithm': 'BRANCH_ARM_OTHER_FRAME_SIGNATURE_V1',
              'policy': 'paired 3-way branch plus exact complete other-side semantic signatures only; no arm order or geometry',
              'pipe_matches': rows, 'branch_signature_additions': additions}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'page': args.page, 'additions': len(additions)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
