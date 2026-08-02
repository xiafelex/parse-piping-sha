#!/usr/bin/env python3
"""Seed a preceding page pipe from a known current CONT.FROM pipe.

The rule needs both the vector leader and the expected turn semantics on both
sides.  It deliberately rejects an ambiguous leader unless component type
makes exactly one candidate compatible.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from seed_crosspage_vector_port_turn_v1 import (
    elbow, exact_port_pipes, leader_points, text_port, unique_near_port_pipe,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('frame_graph', type=Path)
    parser.add_argument('dxf_topology', type=Path)
    parser.add_argument('prior_matches', type=Path)
    parser.add_argument('current_matches', type=Path)
    parser.add_argument('--prior-page', required=True, type=int)
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--prior-dxf', required=True, type=Path)
    parser.add_argument('--current-dxf', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    graph = json.loads(args.frame_graph.read_text())
    topology = json.loads(args.dxf_topology.read_text())
    prior = json.loads(args.prior_matches.read_text())
    source = json.loads(args.current_matches.read_text())
    idf_frames = {row['id']: row for row in graph['idf']['frames']}
    dxf_frames = {row['id']: row for row in graph['dxf']['frames']}
    incidence_i = {row['pipe']: row['frames'] for row in graph['idf']['pipe_frame_incidence']}
    incidence_d = {row['pipe']: row['frames'] for row in graph['dxf']['pipe_frame_incidence']}
    prior_map = {row['idf_pipe']: row['dxf_pipe'] for row in prior['pipe_matches'] if row.get('dxf_pipe')}
    current_map = {row['idf_pipe']: row['dxf_pipe'] for row in source['pipe_matches'] if row.get('dxf_pipe')}
    current_text = text_port(args.current_dxf, 'CONT. FROM', args.prior_page)
    prior_text = text_port(args.prior_dxf, 'CONT. ON', args.page)
    additions = []
    if current_text and prior_text:
        known_current = unique_near_port_pipe(
            topology['pipes'], leader_points(args.current_dxf, current_text), args.page, set())
        prior_hits = exact_port_pipes(topology['pipes'], leader_points(args.prior_dxf, prior_text), args.prior_page)
        if known_current:
            _, current_dxf = known_current
            known_idf = [pipe for pipe, target in current_map.items() if target == current_dxf]
            if len(known_idf) == 1:
                now = known_idf[0]
                idf_options = []
                for fid in incidence_i.get(now, []):
                    frame = idf_frames[fid]
                    if elbow(frame, 'idf'):
                        for pipe in frame['incident_pipes']:
                            # A current-page matched neighbour is an internal
                            # turn on that page, not the preceding cross-page
                            # arm.  Keep only a pipe not already owned by
                            # either page's mapping.
                            if pipe != now and pipe not in prior_map and pipe not in current_map:
                                idf_options.append((pipe, fid))
                # The previous-page candidates may share a leader.  Semantic
                # compatibility with the required IDF turn must select one.
                dxf_options = []
                for pipe in prior_hits:
                    turns = [fid for fid in incidence_d.get(pipe, [])
                             if fid in dxf_frames and dxf_frames[fid]['page'] == args.prior_page and elbow(dxf_frames[fid], 'dxf')]
                    if len(turns) == 1:
                        dxf_options.append((pipe, turns[0]))
                if len(idf_options) == len(dxf_options) == 1:
                    previous_idf, idf_turn = idf_options[0]
                    previous_dxf, dxf_turn = dxf_options[0]
                    additions.append({'idf_pipe': previous_idf, 'dxf_pipe': previous_dxf,
                                      'current_idf_pipe': now, 'current_dxf_pipe': current_dxf,
                                      'evidence': 'known_current_vector_port_plus_unique_turn_compatible_prior_leader'})
    added = {row['idf_pipe']: row['dxf_pipe'] for row in additions}
    rows = []
    for row in prior['pipe_matches']:
        item = dict(row)
        if item['idf_pipe'] in added:
            item.update({'dxf_pipe': added[item['idf_pipe']], 'confidence': 'medium_reverse_crosspage_turn',
                         'evidence': 'known_current_vector_port_plus_unique_turn_compatible_prior_leader'})
        rows.append(item)
    result = {**prior, 'algorithm': 'REVERSE_CROSSPAGE_TURN_V1',
              'policy': 'current CONT.FROM leader must identify an existing mapping; ambiguous prior leader is permitted only if exactly one hit has the required elbow semantics',
              'pipe_matches': rows,
              # A DXF elbow next to a page port may represent a different IDF
              # turn on the visible page.  It validates the candidate type,
              # but cannot itself be paired across the page boundary.
              'reverse_crosspage_additions': additions, 'reverse_crosspage_frame_additions': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'pipe_additions': len(additions), 'frame_additions': 0}))


if __name__ == '__main__':
    main()
