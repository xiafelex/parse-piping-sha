#!/usr/bin/env python3
"""Close the final unambiguous arm of an already paired component frame.

This is a pure one-to-one topology rule.  It applies only after a frame pair
has been independently established and all but one of their incident pipes
are already mapped to each other.  It never chooses a branch arm by ordering,
geometry, page sequence, CONT text or length.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


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
    idf = {row['id']: row for row in graph['idf']['frames']}
    dxf = {row['id']: row for row in graph['dxf']['frames'] if row['page'] == args.page}
    frame_map = {row['idf_frame']: row['dxf_frame'] for row in source.get('frame_matches', [])
                 if isinstance(row.get('dxf_frame'), str)}
    pipe_map = {row['idf_pipe']: row['dxf_pipe'] for row in source['pipe_matches'] if row.get('dxf_pipe')}
    evidence = {row['idf_pipe']: row['evidence'] for row in source['pipe_matches'] if row.get('dxf_pipe')}
    additions = []
    changed = True
    while changed:
        changed = False
        used = set(pipe_map.values())
        for left_frame, right_frame in frame_map.items():
            if left_frame not in idf or right_frame not in dxf:
                continue
            left = [pipe for pipe in idf[left_frame]['incident_pipes'] if pipe in allowed]
            right = list(dxf[right_frame]['incident_pipes'])
            if len(left) != len(right) or len(left) < 2:
                continue
            pairs = [(pipe, pipe_map[pipe]) for pipe in left if pipe in pipe_map and pipe_map[pipe] in right]
            if len(pairs) != len(left) - 1:
                continue
            # Existing pairs must be injective at this component; otherwise a
            # repeated DXF contact cannot prove the remaining arm.
            if len({pair[1] for pair in pairs}) != len(pairs):
                continue
            remaining_left = [pipe for pipe in left if pipe not in pipe_map]
            remaining_right = [pipe for pipe in right if pipe not in used]
            if not (len(remaining_left) == len(remaining_right) == 1):
                continue
            left_pipe, right_pipe = remaining_left[0], remaining_right[0]
            pipe_map[left_pipe] = right_pipe
            evidence[left_pipe] = 'unique_remaining_arm_of_paired_component'
            additions.append({'idf_pipe': left_pipe, 'dxf_pipe': right_pipe,
                              'idf_frame': left_frame, 'dxf_frame': right_frame,
                              'already_matched_arms': [{'idf_pipe': a, 'dxf_pipe': b} for a, b in pairs]})
            changed = True
    rows = []
    new = {row['idf_pipe'] for row in additions}
    for row in source['pipe_matches']:
        item = dict(row)
        if item['idf_pipe'] in pipe_map:
            item['dxf_pipe'] = pipe_map[item['idf_pipe']]
            item['confidence'] = 'medium_topology_closure' if item['idf_pipe'] in new else item['confidence']
            item['evidence'] = evidence[item['idf_pipe']]
        rows.append(item)
    result = {**source, 'algorithm': 'UNIQUE_REMAINING_COMPONENT_ARM_V1',
              'policy': 'paired component and all-but-one injective incident-pipe matches only; no arm ordering',
              'pipe_matches': rows, 'unique_remaining_arm_additions': additions}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'page': args.page, 'additions': len(additions), 'total': len(pipe_map)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
