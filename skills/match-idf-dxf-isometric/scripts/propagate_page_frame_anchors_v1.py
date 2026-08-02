#!/usr/bin/env python3
"""Propagate conservative pipe candidates from matched component frames.

The input page range has already been selected globally by component topology.
This script still does not use page order or CONT text.  It seeds only unique
frame types (reducer/bore-change, elbow/turn, branch/junction) and audited
branch outlets, then walks across degree-two frames.  A pipe is emitted only
when the two *other-side* frame signatures make its pairing unique.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from collections import defaultdict
from pathlib import Path


def number(value: str) -> int:
    return int(value[1:])


def category(frame: dict, side: str) -> str | None:
    if side == 'idf':
        if frame['kind'] == 'junction_3': return 'junction'
        if frame['kind'] == 'turn_2': return 'elbow'
        if frame['kind'] == 'inline_2' and frame['bore_change']: return 'reducer'
        return None
    if frame['kind'] in {'branch', 'tee'} and frame['degree'] == 3: return 'junction'
    if frame['kind'] == 'elbow' and frame['degree'] == 2: return 'elbow'
    if frame['kind'] == 'reducer' and frame['degree'] == 2: return 'reducer'
    return None


def build_indexes(frames: list[dict], incidence: list[dict], side: str):
    by_id = {frame['id']: frame for frame in frames}
    pipe_frames = {item['pipe']: item['frames'] for item in incidence}
    frame_pipes = {frame['id']: frame['incident_pipes'] for frame in frames}
    return by_id, pipe_frames, frame_pipes


def other_signature(pipe: str, through: str, pipe_frames: dict, frames: dict, side: str) -> tuple[str, ...]:
    others = [frames[item] for item in pipe_frames.get(pipe, []) if item != through]
    semantic = sorted(category(item, side) for item in others if category(item, side))
    if not others:
        semantic.append('open')
    return tuple(semantic)


def score_pair(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    # Exact structural continuation is proof; open ends have less evidence.
    if left == right:
        return 8 if left != ('open',) else 2
    if not left or not right:
        return 0
    return -6


def outward_vector(centre, endpoints):
    if not centre or len(endpoints) != 2:
        return None
    end = max(endpoints, key=lambda point: math.dist(centre, point))
    vector = [end[axis] - centre[axis] for axis in range(2)]
    length = math.hypot(*vector)
    return [value / length for value in vector] if length else None


def chosen_range(cover: dict, page: int) -> set[str]:
    selected = next(item for item in cover['best']['page_ranges'] if item['page'] == page)
    start, end = map(number, selected['idf_range'])
    return {f'I{value:03d}' for value in range(start, end + 1)}


def page_frames(dxf_frames: list[dict], page: int) -> list[dict]:
    return [frame for frame in dxf_frames if frame['page'] == page]


def unique_seed_pairs(idf_frames: list[dict], dxf_frames: list[dict]):
    idf_by_kind, dxf_by_kind = defaultdict(list), defaultdict(list)
    for frame in idf_frames:
        kind = category(frame, 'idf')
        if kind: idf_by_kind[kind].append(frame)
    for frame in dxf_frames:
        kind = category(frame, 'dxf')
        if kind: dxf_by_kind[kind].append(frame)
    result = []
    for kind in sorted(set(idf_by_kind) & set(dxf_by_kind)):
        if len(idf_by_kind[kind]) == len(dxf_by_kind[kind]) == 1:
            result.append((idf_by_kind[kind][0]['id'], dxf_by_kind[kind][0]['id'],
                           'unique_page_frame_kind'))
    return result


def positional_seed_pairs(idf_frames: list[dict], dxf_frames: list[dict]):
    """Match repeated component frames through their relative projected axes."""
    left_groups, right_groups = defaultdict(list), defaultdict(list)
    for frame in idf_frames:
        kind = category(frame, 'idf')
        if kind and frame.get('centre'):
            left_groups[kind].append(frame)
    for frame in dxf_frames:
        kind = category(frame, 'dxf')
        if kind and frame.get('centre'):
            right_groups[kind].append(frame)
    seeds = []
    for kind in sorted(set(left_groups) & set(right_groups)):
        left, right = left_groups[kind], right_groups[kind]
        if len(left) != len(right) or not 2 <= len(left) <= 5:
            continue
        ranked = []
        for permutation in itertools.permutations(right):
            score = 0.0
            for i, j in itertools.combinations(range(len(left)), 2):
                u = [left[j]['centre'][axis] - left[i]['centre'][axis] for axis in range(2)]
                v = [permutation[j]['centre'][axis] - permutation[i]['centre'][axis] for axis in range(2)]
                un, vn = math.hypot(*u), math.hypot(*v)
                if un and vn:
                    score += (u[0] * v[0] + u[1] * v[1]) / (un * vn)
            ranked.append((score, permutation))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if ranked[0][0] <= 0 or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 1.0):
            continue
        seeds.extend((idf['id'], dxf['id'], 'canonical_relative_frame_direction')
                     for idf, dxf in zip(left, ranked[0][1]))
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('component_frame_graph', type=Path)
    parser.add_argument('global_cover', type=Path)
    parser.add_argument('--page', type=int, required=True)
    parser.add_argument('--anchor-audit', type=Path,
                        help='optional direct branch outlet audit')
    parser.add_argument('--dxf-pipe-topology', type=Path,
                        help='optional source pipe/handle index for audited outlet anchors')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.component_frame_graph.read_text())
    cover = json.loads(args.global_cover.read_text())
    if cover['status'] != 'topology_global_unique_exact_cover_candidate':
        raise SystemExit('requires a unique exact global page-range cover; ambiguous or partial covers stay unresolved')

    allowed = chosen_range(cover, args.page)
    idf_frames_all = graph['idf']['frames']
    idf_frames = [frame for frame in idf_frames_all if set(frame['incident_pipes']) & allowed]
    dxf_frames = page_frames(graph['dxf']['frames'], args.page)
    idf_by_id, idf_pipe_frames, idf_frame_pipes = build_indexes(
        idf_frames_all, graph['idf']['pipe_frame_incidence'], 'idf')
    dxf_by_id, dxf_pipe_frames, dxf_frame_pipes = build_indexes(
        graph['dxf']['frames'], graph['dxf']['pipe_frame_incidence'], 'dxf')
    idf_pipe_geometry = {item['id']: item for item in graph['idf'].get('pipe_geometry', [])}

    frame_map: dict[str, str] = {}  # idf frame -> dxf frame
    frame_evidence: dict[str, str] = {}
    pipe_map: dict[str, str] = {}   # idf pipe -> dxf pipe
    pipe_evidence: dict[str, str] = {}

    def add_frame(idf_id: str, dxf_id: str, evidence: str) -> bool:
        if idf_id in frame_map and frame_map[idf_id] != dxf_id: return False
        if dxf_id in frame_map.values() and frame_map.get(idf_id) != dxf_id: return False
        if category(idf_by_id[idf_id], 'idf') != category(dxf_by_id[dxf_id], 'dxf'): return False
        if frame_map.get(idf_id) == dxf_id:
            return False
        frame_map[idf_id] = dxf_id; frame_evidence[idf_id] = evidence
        return True

    def add_pipe(idf_id: str, dxf_id: str, evidence: str) -> bool:
        if idf_id not in allowed: return False
        if idf_id in pipe_map and pipe_map[idf_id] != dxf_id: return False
        if dxf_id in pipe_map.values() and pipe_map.get(idf_id) != dxf_id: return False
        if pipe_map.get(idf_id) == dxf_id:
            return False
        pipe_map[idf_id] = dxf_id; pipe_evidence[idf_id] = evidence
        return True

    for item in unique_seed_pairs(idf_frames, dxf_frames):
        add_frame(*item)
    for item in positional_seed_pairs(idf_frames, dxf_frames):
        add_frame(*item)

    dxf_handles = {}
    if args.dxf_pipe_topology and args.dxf_pipe_topology.exists():
        source_pipes = json.loads(args.dxf_pipe_topology.read_text()).get('pipes', [])
        dxf_handles = {item['id']: set(item.get('handles', [])) for item in source_pipes}
        dxf_geometry = {item['id']: item.get('endpoints', []) for item in source_pipes}
    else:
        dxf_geometry = {}
    if args.anchor_audit and args.anchor_audit.exists():
        audit = json.loads(args.anchor_audit.read_text())
        for direct in audit.get('direct_anchor_matches', []):
            if direct['idf_anchor'] not in idf_by_id or direct['dxf_anchor'] not in dxf_by_id:
                continue
            # A unique category seed may already have inserted this same frame;
            # the audited outlet leg remains additional evidence and must still
            # be resolved in that case.
            existing = frame_map.get(direct['idf_anchor'])
            if existing is not None and existing != direct['dxf_anchor']:
                continue
            if existing is None:
                add_frame(direct['idf_anchor'], direct['dxf_anchor'], 'audited_unique_branch_anchor')
            wanted = set(direct['outlet_dxf_handles'])
            for pipe in dxf_frame_pipes[direct['dxf_anchor']]:
                if wanted <= dxf_handles.get(pipe, set()):
                    add_pipe(direct['outlet_idf_pipe'], pipe, 'audited_direct_branch_outlet')
    changed = True
    while changed:
        changed = False
        for idf_frame_id, dxf_frame_id in list(frame_map.items()):
            left = [item for item in idf_frame_pipes[idf_frame_id] if item in allowed]
            right = list(dxf_frame_pipes[dxf_frame_id])
            if len(left) == len(right) == 3:
                # A paired tee/branch has three arms.  The arm ordering is
                # determined from its projected outward vectors, not from pipe
                # creation order.  This breaks repeated tee symmetry only
                # when the engineering coordinate convention provides a clear
                # angular winner.
                candidates = []
                for permutation in itertools.permutations(right):
                    score = 0.0; valid = True
                    for idf_pipe, dxf_pipe in zip(left, permutation):
                        idf_endpoints = [idf_pipe_geometry[idf_pipe]['a2'], idf_pipe_geometry[idf_pipe]['b2']]
                        u = outward_vector(idf_by_id[idf_frame_id].get('centre'), idf_endpoints)
                        v = outward_vector(dxf_by_id[dxf_frame_id].get('centre'), dxf_geometry.get(dxf_pipe, []))
                        if not u or not v:
                            valid = False; break
                        # IDF canonical axonometric projection and DXF model
                        # coordinates use opposite vertical signs.  CWR's
                        # independently audited reducer/elbow chain provides
                        # the project-local calibration; no sheet sequence is
                        # involved in this transform.
                        u[1] *= -1
                        score += u[0] * v[0] + u[1] * v[1]
                    if valid:
                        candidates.append((score, permutation))
                candidates.sort(key=lambda item: item[0], reverse=True)
                if candidates and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 1.0):
                    for idf_pipe, dxf_pipe in zip(left, candidates[0][1]):
                        changed |= add_pipe(idf_pipe, dxf_pipe, 'degree3_projected_arm_direction')
                continue
            if len(left) != len(right) or len(left) != 2:
                continue
            # Once one incident pipe was independently fixed, a degree-two
            # component fixes the opposite pair exactly; do not discard that
            # fact merely because both sides have the same elbow signature.
            fixed = [(idf_pipe, pipe_map[idf_pipe]) for idf_pipe in left if idf_pipe in pipe_map]
            if len(fixed) == 1 and fixed[0][1] in right:
                other_left = next(item for item in left if item != fixed[0][0])
                other_right = next(item for item in right if item != fixed[0][1])
                changed |= add_pipe(other_left, other_right, 'opposite_side_of_fixed_degree2_frame')
                continue
            options = []
            for permutation in itertools.permutations(right):
                pair_score = sum(score_pair(
                    other_signature(i, idf_frame_id, idf_pipe_frames, idf_by_id, 'idf'),
                    other_signature(d, dxf_frame_id, dxf_pipe_frames, dxf_by_id, 'dxf'))
                    for i, d in zip(left, permutation))
                options.append((pair_score, permutation))
            options.sort(reverse=True)
            if len(options) < 2 or options[0][0] <= options[1][0]:
                continue
            for idf_pipe, dxf_pipe in zip(left, options[0][1]):
                changed |= add_pipe(idf_pipe, dxf_pipe, 'degree2_other_side_signature')

        for idf_pipe, dxf_pipe in list(pipe_map.items()):
            left = [item for item in idf_pipe_frames.get(idf_pipe, []) if item in idf_by_id and item not in frame_map]
            right = [item for item in dxf_pipe_frames.get(dxf_pipe, []) if item not in frame_map.values()]
            if len(left) == len(right) == 1:
                changed |= add_frame(left[0], right[0], 'propagated_from_unique_pipe')

    pipe_rows = []
    for idf_pipe in sorted(allowed, key=number):
        dxf_pipe = pipe_map.get(idf_pipe)
        pipe_rows.append({
            'idf_pipe': idf_pipe,
            'dxf_pipe': dxf_pipe,
            'confidence': 'medium' if dxf_pipe else 'unresolved',
            'evidence': pipe_evidence.get(idf_pipe, 'no unique component-frame propagation'),
        })
    result = {
        'algorithm': 'PROPAGATE_PAGE_FRAME_ANCHORS_V1', 'line_key': graph['line_key'], 'page': args.page,
        'policy': 'selected page range + component-frame propagation only; no CONT and no page-order matching',
        'idf_range': sorted(allowed, key=number),
        'frame_matches': [{'idf_frame': left, 'dxf_frame': right,
                           'evidence': frame_evidence[left]} for left, right in sorted(frame_map.items())],
        'pipe_matches': pipe_rows,
        'summary': {'mapped': sum(row['dxf_pipe'] is not None for row in pipe_rows),
                    'unresolved': sum(row['dxf_pipe'] is None for row in pipe_rows)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': graph['line_key'], 'page': args.page,
                      **result['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
