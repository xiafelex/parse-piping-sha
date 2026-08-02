#!/usr/bin/env python3
"""Emit auditable, non-forced I100↔DXF-pipe correspondence candidates.

This is the geometric/error-tolerant graph-matching layer.  It consumes only
already-classified DXF semantics and a globally selected page range.  It does
not create component classes, use CONT text, or turn the highest score into a
match when its evidence is insufficient.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path


TRANSFORMS = ('identity', 'flip_x', 'flip_y', 'flip_xy', 'swap', 'swap_flip_x',
              'swap_flip_y', 'swap_flip_xy')


def number(value: str) -> int:
    return int(value[1:])


def transform(vector, name: str):
    x, y = vector
    return {'identity': (x, y), 'flip_x': (-x, y), 'flip_y': (x, -y),
            'flip_xy': (-x, -y), 'swap': (y, x), 'swap_flip_x': (-y, x),
            'swap_flip_y': (y, -x), 'swap_flip_xy': (-y, -x)}[name]


def unit_from_frame(centre, endpoints):
    if not centre or len(endpoints) != 2:
        return None
    point = max(endpoints, key=lambda item: math.dist(centre, item))
    dx, dy = point[0] - centre[0], point[1] - centre[1]
    norm = math.hypot(dx, dy)
    return (dx / norm, dy / norm) if norm else None


def selected_range(cover: dict, page: int):
    chosen = next(item for item in cover['best']['page_ranges'] if item['page'] == page)
    low, high = map(number, chosen['idf_range'])
    return [f'I{value:03d}' for value in range(low, high + 1)]


def category(frame: dict, side: str):
    if side == 'idf':
        if frame['kind'] == 'junction_3': return 'junction'
        if frame['kind'] == 'turn_2': return 'elbow'
        if frame['kind'] == 'inline_2' and frame['bore_change']: return 'reducer'
    else:
        if frame['kind'] in {'branch', 'tee'} and frame['degree'] == 3: return 'junction'
        if frame['kind'] == 'elbow' and frame['degree'] == 2: return 'elbow'
        if frame['kind'] == 'reducer' and frame['degree'] == 2: return 'reducer'
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('component_frame_graph', type=Path)
    parser.add_argument('global_cover', type=Path)
    parser.add_argument('dxf_pipe_topology', type=Path)
    parser.add_argument('--page', type=int, required=True)
    parser.add_argument('--propagation', type=Path,
                        help='optional conservative propagation; used only as evidence, never as a hidden constraint')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.component_frame_graph.read_text())
    cover = json.loads(args.global_cover.read_text())
    if cover['status'] != 'topology_global_unique_exact_cover_candidate':
        raise SystemExit('requires unique global page range; ambiguous covers remain unresolved')
    allowed = selected_range(cover, args.page)
    pipes = {item['id']: item for item in json.loads(args.dxf_pipe_topology.read_text())['pipes']
             if item['page'] == args.page}
    idf_geometry = {item['id']: item for item in graph['idf'].get('pipe_geometry', [])}
    idf_frames = {item['id']: item for item in graph['idf']['frames']}
    dxf_frames = {item['id']: item for item in graph['dxf']['frames'] if item['page'] == args.page}
    idf_incidence = {item['pipe']: item['frames'] for item in graph['idf']['pipe_frame_incidence']}
    dxf_incidence = {item['pipe']: item['frames'] for item in graph['dxf']['pipe_frame_incidence']}
    propagation = json.loads(args.propagation.read_text()) if args.propagation and args.propagation.exists() else {}
    fixed_rows = {item['idf_pipe']: item for item in propagation.get('pipe_matches', []) if item.get('dxf_pipe')}
    fixed = {idf_pipe: item['dxf_pipe'] for idf_pipe, item in fixed_rows.items()}
    # Direction-derived tee legs must never calibrate the coordinate transform
    # that produced them.  Only audits / unique-frame propagation are
    # independent calibration evidence.
    independent_fixed = {idf_pipe: item['dxf_pipe'] for idf_pipe, item in fixed_rows.items()
                         if item.get('evidence') != 'degree3_projected_arm_direction'}
    selected_axis = propagation.get('axis_transform')
    frame_map = {item['idf_frame']: item['dxf_frame'] for item in propagation.get('frame_matches', [])}

    # Score each allowed candidate by evidence independently.  A fixed mapping
    # is reported, but no other candidate is removed: reviewers can inspect
    # the alternatives and score margin.
    rows = []
    for idf_pipe in allowed:
        incident = [frame for frame in idf_incidence.get(idf_pipe, []) if frame in frame_map]
        candidates = []
        for dxf_pipe, dxf in pipes.items():
            score, evidence = 0.0, []
            for idf_frame_id in incident:
                dxf_frame_id = frame_map[idf_frame_id]
                if dxf_frame_id not in dxf_incidence.get(dxf_pipe, []):
                    continue
                score += 8.0
                evidence.append({'kind': 'matched_frame_incidence', 'idf_frame': idf_frame_id,
                                 'dxf_frame': dxf_frame_id, 'score': 8.0})
                a = unit_from_frame(idf_frames[idf_frame_id].get('centre'),
                                    [idf_geometry[idf_pipe]['a2'], idf_geometry[idf_pipe]['b2']])
                b = unit_from_frame(dxf_frames[dxf_frame_id].get('centre'), dxf.get('endpoints', []))
                if a and b:
                    axis_cosines = {name: round(sum(x*y for x, y in zip(transform(a, name), b)), 5)
                                    for name in TRANSFORMS}
                    direction = {'kind': 'local_arm_direction', 'axis_cosines': axis_cosines}
                    # Geometry contributes to the matching score only after a
                    # separately auditable project calibration has been
                    # selected.  Otherwise it is review evidence, not a way
                    # to silently choose the best mirror per candidate.
                    if selected_axis:
                        directional_score = max(0.0, axis_cosines[selected_axis]) * 2.0
                        score += directional_score
                        direction.update({'axis_transform': selected_axis,
                                          'score': round(directional_score, 5)})
                    evidence.append(direction)
            if fixed.get(idf_pipe) == dxf_pipe:
                if idf_pipe in independent_fixed:
                    score += 20.0
                    evidence.append({'kind': 'independent_existing_propagation', 'score': 20.0})
                else:
                    evidence.append({'kind': 'conditional_existing_propagation',
                                     'condition': f'axis_transform={selected_axis}'})
            if score:
                candidates.append({'dxf_pipe': dxf_pipe, 'score': round(score, 5), 'evidence': evidence})
        candidates.sort(key=lambda item: (-item['score'], item['dxf_pipe']))
        margin = None if len(candidates) < 2 else round(candidates[0]['score'] - candidates[1]['score'], 5)
        rows.append({'idf_pipe': idf_pipe, 'candidates': candidates,
                     'best_score_margin': margin,
                     'status': 'fixed_medium_candidate' if idf_pipe in independent_fixed else
                               'conditional_medium_candidate' if idf_pipe in fixed else
                               'ranked_not_assigned' if candidates else 'no_frame_evidence'})

    # Show whether calibration itself is identifiable.  Each already paired
    # frame/pipe observation votes for every D4 transform.  This report does
    # not invent a transform from an unpaired symmetric tee.
    calibration = []
    for name in TRANSFORMS:
        votes = []
        for idf_pipe, dxf_pipe in independent_fixed.items():
            for idf_frame_id in idf_incidence.get(idf_pipe, []):
                dxf_frame_id = frame_map.get(idf_frame_id)
                if not dxf_frame_id or dxf_frame_id not in dxf_incidence.get(dxf_pipe, []):
                    continue
                a = unit_from_frame(idf_frames[idf_frame_id].get('centre'),
                                    [idf_geometry[idf_pipe]['a2'], idf_geometry[idf_pipe]['b2']])
                b = unit_from_frame(dxf_frames[dxf_frame_id].get('centre'), pipes[dxf_pipe].get('endpoints', []))
                if a and b:
                    votes.append(sum(x*y for x, y in zip(transform(a, name), b)))
        calibration.append({'transform': name, 'evidence_count': len(votes),
                            'mean_cosine': round(sum(votes) / len(votes), 5) if votes else None})
    calibration.sort(key=lambda item: (-1 if item['mean_cosine'] is None else -item['mean_cosine'], item['transform']))
    usable = [item for item in calibration if item['mean_cosine'] is not None]
    calibration_status = 'insufficient_independent_geometry_evidence'
    if len(usable) >= 2 and usable[0]['evidence_count'] >= 2 and usable[0]['mean_cosine'] - usable[1]['mean_cosine'] >= .15:
        calibration_status = 'unique_project_axis_candidate'
    result = {'algorithm': 'PAGE_PIPE_CORRESPONDENCE_CANDIDATES_V1', 'line_key': graph['line_key'], 'page': args.page,
              'policy': 'attributed geometric graph candidates only; no CONT, no page order, no forced assignment',
              'idf_range': allowed, 'axis_transform_used_for_conditional_geometry': selected_axis,
              'axis_calibration_status': calibration_status,
              'axis_calibration_candidates': calibration, 'idf_pipe_candidates': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': graph['line_key'], 'page': args.page,
                      'with_candidates': sum(bool(row['candidates']) for row in rows),
                      'fixed': len(fixed)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
