#!/usr/bin/env python3
"""Find review-only branch-frame hypotheses from raw IDF-41 continuation arms.

An IDF record 41 is not globally a named component.  This utility therefore
does *not* alter final propagation.  It only reports a low-confidence
hypothesis when a 3-way IDF junction has exactly one arm leading to a
degree-two ``[41]`` connector and a DXF 3-way branch has exactly one arm
leading to a degree-two DXF branch.  The paired continuation is a useful
topological discriminator when repeated tees otherwise have no calibrated
sheet-coordinate relation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def number(value: str) -> int:
    return int(value[1:])


def selected(cover: dict, page: int) -> set[str]:
    item = next(row for row in cover['best']['page_ranges'] if row['page'] == page)
    first, last = map(number, item['idf_range'])
    return {f'I{i:03d}' for i in range(first, last + 1)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('component_frame_graph', type=Path)
    parser.add_argument('global_cover', type=Path)
    parser.add_argument('--page', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.component_frame_graph.read_text())
    cover = json.loads(args.global_cover.read_text())
    if cover['status'] != 'topology_global_unique_exact_cover_candidate':
        raise SystemExit('requires a unique page range')
    allowed = selected(cover, args.page)
    idf_frames = {item['id']: item for item in graph['idf']['frames']}
    dxf_frames = {item['id']: item for item in graph['dxf']['frames'] if item['page'] == args.page}
    idf_incidence = {item['pipe']: item['frames'] for item in graph['idf']['pipe_frame_incidence']}
    dxf_incidence = {item['pipe']: item['frames'] for item in graph['dxf']['pipe_frame_incidence']}

    idf_candidates = []
    for frame in idf_frames.values():
        if frame['kind'] != 'junction_3':
            continue
        arms = []
        for pipe in frame['incident_pipes']:
            if pipe not in allowed:
                continue
            other = [item for item in idf_incidence.get(pipe, []) if item != frame['id']]
            raw41 = [item for item in other if idf_frames[item]['kind'] == 'inline_2' and
                     idf_frames[item].get('record_codes') == [41]]
            if raw41:
                arms.append({'pipe': pipe, 'continuation_frames': raw41})
        if len(arms) == 1:
            idf_candidates.append({'frame': frame['id'], **arms[0]})

    dxf_candidates = []
    for frame in dxf_frames.values():
        if not (frame['kind'] in {'branch', 'tee'} and frame['degree'] == 3):
            continue
        arms = []
        for pipe in frame['incident_pipes']:
            other = [item for item in dxf_incidence.get(pipe, []) if item != frame['id']]
            branches = [item for item in other if dxf_frames.get(item, {}).get('kind') in {'branch', 'tee'} and
                        dxf_frames[item].get('degree') == 2]
            if branches:
                arms.append({'pipe': pipe, 'continuation_frames': branches})
        if len(arms) == 1:
            dxf_candidates.append({'frame': frame['id'], **arms[0]})

    hypotheses = []
    if len(idf_candidates) == len(dxf_candidates) == 1:
        hypotheses.append({'idf_frame': idf_candidates[0]['frame'], 'idf_pipe': idf_candidates[0]['pipe'],
                           'idf_continuation_frames': idf_candidates[0]['continuation_frames'],
                           'dxf_frame': dxf_candidates[0]['frame'], 'dxf_pipe': dxf_candidates[0]['pipe'],
                           'dxf_continuation_frames': dxf_candidates[0]['continuation_frames'],
                           'confidence': 'low', 'status': 'candidate_requires_visual_review',
                           'evidence': ['unique page-local IDF junction arm to degree-2 raw [41] connector',
                                        'unique page-local DXF branch arm to degree-2 DXF branch']})
    result = {'algorithm': 'RAW41_BRANCH_CONTINUATION_HYPOTHESIS_V1', 'line_key': graph['line_key'],
              'page': args.page, 'policy': 'review-only; no coordinate fit, no CONT, no final I100 assignment',
              'idf_candidates': idf_candidates, 'dxf_candidates': dxf_candidates, 'hypotheses': hypotheses}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': graph['line_key'], 'page': args.page,
                      'idf_candidates': len(idf_candidates), 'dxf_candidates': len(dxf_candidates),
                      'hypotheses': len(hypotheses)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
