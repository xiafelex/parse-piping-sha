#!/usr/bin/env python3
"""Extend independently anchored I100↔DXF pipes through exact raw joins.

This is not support contraction.  Each DXF pipe remains a distinct semantic
edge.  The only inference is that an already matched I100 and its immediately
next IDF 100 are both joined at their exact source endpoints, and the matched
DXF pipe has exactly one unused DXF pipe sharing that same exact endpoint.
Under those conditions the next *numbered* pipe can be propagated without
using drawing order, CONT text, or length.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def distance(a, b):
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


def number(pipe_id):
    return int(pipe_id[1:])


def idf_neighbours(pipes, tolerance):
    """Return exact source-coordinate neighbours, preserving I100 identity."""
    result = defaultdict(set)
    ordered = sorted(pipes.values(), key=lambda item: number(item['id']))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            # Only adjacent IDs are accepted.  This makes an incidental
            # coordinate coincidence elsewhere in a branched line unable to
            # create a skipped numbering path.
            if number(right['id']) != number(left['id']) + 1:
                break
            if any(distance(a, b) <= tolerance for a in (left['a'], left['b'])
                   for b in (right['a'], right['b'])):
                result[left['id']].add(right['id'])
                result[right['id']].add(left['id'])
    return result


def dxf_neighbours(pipes, tolerance):
    result = defaultdict(set)
    values = list(pipes.values())
    for index, left in enumerate(values):
        for right in values[index + 1:]:
            if any(distance(a, b) <= tolerance for a in left['endpoints'] for b in right['endpoints']):
                result[left['id']].add(right['id'])
                result[right['id']].add(left['id'])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf_topology', type=Path)
    parser.add_argument('dxf_pipe_topology', type=Path)
    parser.add_argument('propagation', type=Path,
                        help='conservative anchor propagation JSON')
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--idf-tolerance', type=float, default=1.5)
    parser.add_argument('--dxf-tolerance', type=float, default=.15)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    idf = json.loads(args.idf_topology.read_text())
    source = json.loads(args.propagation.read_text())
    dxf = json.loads(args.dxf_pipe_topology.read_text())
    selected = {row['idf_pipe'] for row in source['pipe_matches']}
    idf_pipes = {row['id']: row for row in idf['pipes'] if row['id'] in selected}
    dxf_pipes = {row['id']: row for row in dxf['pipes'] if row['page'] == args.page}
    i_neighbours = idf_neighbours(idf_pipes, args.idf_tolerance)
    d_neighbours = dxf_neighbours(dxf_pipes, args.dxf_tolerance)
    mapping = {row['idf_pipe']: row['dxf_pipe'] for row in source['pipe_matches'] if row.get('dxf_pipe')}
    evidence = {row['idf_pipe']: row['evidence'] for row in source['pipe_matches'] if row.get('dxf_pipe')}
    additions = []
    changed = True
    while changed:
        changed = False
        used = set(mapping.values())
        for idf_pipe, dxf_pipe in list(mapping.items()):
            left = [candidate for candidate in i_neighbours.get(idf_pipe, []) if candidate not in mapping]
            right = [candidate for candidate in d_neighbours.get(dxf_pipe, []) if candidate not in used]
            if len(left) != len(right) or len(left) != 1:
                continue
            candidate_i, candidate_d = left[0], right[0]
            mapping[candidate_i] = candidate_d
            evidence[candidate_i] = 'unique_exact_raw_continuation_after_independent_anchor'
            additions.append({'idf_pipe': candidate_i, 'dxf_pipe': candidate_d,
                              'from_idf_pipe': idf_pipe, 'from_dxf_pipe': dxf_pipe,
                              'idf_source_endpoint_tolerance': args.idf_tolerance,
                              'dxf_source_endpoint_tolerance': args.dxf_tolerance,
                              'dxf_pipe_kind': dxf_pipes[candidate_d]['kind']})
            changed = True
    rows = []
    for row in source['pipe_matches']:
        item = dict(row)
        if row['idf_pipe'] in mapping:
            item['dxf_pipe'] = mapping[row['idf_pipe']]
            item['confidence'] = ('medium' if row['idf_pipe'] not in {x['idf_pipe'] for x in additions}
                                  else 'medium_continuation')
            item['evidence'] = evidence[row['idf_pipe']]
        rows.append(item)
    result = {**source,
              'algorithm': 'EXACT_RAW_PIPE_CONTINUATION_V1',
              'policy': 'separate DXF pipes stay separate at supports; exact endpoint adjacency only propagates numbering from an existing anchor',
              'raw_continuation_additions': additions,
              'pipe_matches': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'page': args.page, 'existing': len(mapping) - len(additions),
                      'additions': len(additions), 'total': len(mapping)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
