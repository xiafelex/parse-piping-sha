#!/usr/bin/env python3
"""Enumerate review-only attributed component-skeleton correspondences.

This is deliberately a small, dependency-free maximum-common-subgraph style
search.  It matches only confirmed landmark components (junction, elbow,
reducer) and treats unnamed degree-two connectors, welds and DXF drawing
splits as traversable topology.  It neither reads CONT text nor assigns I100
numbers; its candidates are input evidence for the later pipe matcher.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


def number(value: str) -> int:
    return int(value[1:])


def category(frame: dict, side: str) -> str | None:
    if side == 'idf':
        if frame['kind'] == 'junction_3':
            return 'junction'
        if frame['kind'] == 'turn_2':
            return 'elbow'
        if frame['kind'] == 'inline_2' and frame.get('bore_change'):
            return 'reducer'
    else:
        if frame['kind'] in {'branch', 'tee'} and frame['degree'] == 3:
            return 'junction'
        if frame['kind'] == 'elbow' and frame['degree'] == 2:
            return 'elbow'
        if frame['kind'] == 'reducer' and frame['degree'] == 2:
            return 'reducer'
    return None


def selected_idf_pipes(cover: dict, page: int) -> set[str]:
    row = next((item for item in cover['best']['page_ranges'] if item['page'] == page), None)
    if row is None:
        raise ValueError(f'page {page} has no selected IDF range')
    low, high = map(number, row['idf_range'])
    return {f'I{i:03d}' for i in range(low, high + 1)}


def side_data(graph: dict, side: str, page: int, allowed: set[str] | None):
    source = graph[side]
    frames = {item['id']: item for item in source['frames']
              if side == 'idf' or item['page'] == page}
    pipe_frames = {item['pipe']: [frame for frame in item['frames'] if frame in frames]
                   for item in source['pipe_frame_incidence']
                   if allowed is None or item['pipe'] in allowed}
    frame_pipes = defaultdict(set)
    for pipe, incident in pipe_frames.items():
        for frame in incident:
            frame_pipes[frame].add(pipe)
    # Keep only frames actually touched by the selected page/range.  This
    # prevents a connector in another IDF page becoming a false bridge.
    frames = {key: value for key, value in frames.items() if key in frame_pipes}
    pipe_frames = {pipe: [frame for frame in values if frame in frames]
                   for pipe, values in pipe_frames.items()}
    landmarks = {key for key, value in frames.items() if category(value, side)}
    return frames, pipe_frames, {key: set(value) for key, value in frame_pipes.items()}, landmarks


def landmark_paths(frames, pipe_frames, frame_pipes, landmarks, start):
    """Find labelled landmark endpoints reachable through unnamed frames.

    A path begins through one of ``start``'s incident pipes.  Traversal may
    cross only non-landmark frames.  Reaching another landmark ends that arm.
    This makes explicit that a DXF weld or a degree-two raw connector is an
    edit-tolerant drawing representation, not a semantic anchor.
    """
    results = []
    queue = [(start, 0)]
    seen = {start}
    while queue:
        frame, hops = queue.pop(0)
        for pipe in frame_pipes.get(frame, []):
            for neighbour in pipe_frames.get(pipe, []):
                if neighbour == frame:
                    continue
                if neighbour in landmarks:
                    results.append((neighbour, hops + 1))
                elif neighbour not in seen:
                    seen.add(neighbour)
                    queue.append((neighbour, hops + 1))
    return results


def signature(frame_id, frames, pipe_frames, frame_pipes, landmarks, side):
    endings = landmark_paths(frames, pipe_frames, frame_pipes, landmarks, frame_id)
    end_labels = Counter((category(frames[item], side), frames[item]['degree']) for item, _ in endings)
    # Direct pipe arms with no other frame are still useful but weak evidence.
    open_arms = sum(1 for pipe in frame_pipes[frame_id] if len(pipe_frames.get(pipe, [])) <= 1)
    return {'label': category(frames[frame_id], side), 'degree': frames[frame_id]['degree'],
            'landmark_neighbours': sorted([{'category': key[0], 'degree': key[1], 'count': count}
                                           for key, count in end_labels.items()],
                                          key=lambda item: (item['category'], item['degree'])),
            'open_arms': open_arms}


def local_score(left, right):
    if left['label'] != right['label'] or left['degree'] != right['degree']:
        return None
    l = Counter((x['category'], x['degree'], x['count']) for x in left['landmark_neighbours'])
    r = Counter((x['category'], x['degree'], x['count']) for x in right['landmark_neighbours'])
    # Landmark neighbourhood agreement is stronger than an equal degree;
    # unmatched arms are allowed because pages can be partial and DXF can split
    # one semantic attachment into several drawing frames.
    overlap = sum((l & r).values())
    diff = sum((l - r).values()) + sum((r - l).values())
    return 10 + overlap * 4 - diff * 1.5 - abs(left['open_arms'] - right['open_arms'])


def assignment_score(mapping, left_paths, right_paths):
    score = 0.0
    for left, right in mapping.items():
        for left_target, _ in left_paths[left]:
            if left_target not in mapping:
                continue
            right_target = mapping[left_target]
            if any(target == right_target for target, _ in right_paths[right]):
                score += 3.0
            else:
                score -= 3.0
    return score / 2  # each undirected structural relation is seen twice


TRANSFORMS = ('identity', 'flip_x', 'flip_y', 'flip_xy', 'swap', 'swap_flip_x',
              'swap_flip_y', 'swap_flip_xy')


def transform(vector, name):
    x, y = vector
    return {'identity': (x, y), 'flip_x': (-x, y), 'flip_y': (x, -y),
            'flip_xy': (-x, -y), 'swap': (y, x), 'swap_flip_x': (-y, x),
            'swap_flip_y': (y, -x), 'swap_flip_xy': (-y, -x)}[name]


def geometry_hypothesis_score(mapping, left_frames, right_frames):
    """Return the best *review-only* D4 orientation score for a frame map.

    ISO sheets can be mirrored/rotated and are non-scale, so only the cosine
    of relative landmark directions is considered.  This can rank symmetric
    skeleton candidates but does not establish a project calibration and is
    therefore never a final matching proof.
    """
    pairs = [(left, right) for left, right in mapping.items()
             if left_frames[left].get('centre') and right_frames[right].get('centre')]
    votes = []
    for name in TRANSFORMS:
        cosines = []
        for index, (left_a, right_a) in enumerate(pairs):
            for left_b, right_b in pairs[index + 1:]:
                left = (left_frames[left_b]['centre'][0] - left_frames[left_a]['centre'][0],
                        left_frames[left_b]['centre'][1] - left_frames[left_a]['centre'][1])
                right = (right_frames[right_b]['centre'][0] - right_frames[right_a]['centre'][0],
                         right_frames[right_b]['centre'][1] - right_frames[right_a]['centre'][1])
                left = transform(left, name)
                norm = math.hypot(*left) * math.hypot(*right)
                if norm:
                    cosines.append((left[0] * right[0] + left[1] * right[1]) / norm)
        votes.append((sum(cosines), name, cosines))
    score, name, cosines = max(votes, key=lambda item: (item[0], item[1]))
    return score, name, cosines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('component_frame_graph', type=Path)
    parser.add_argument('global_cover', type=Path)
    parser.add_argument('--page', type=int, required=True)
    parser.add_argument('--max-candidates', type=int, default=20)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.component_frame_graph.read_text())
    cover = json.loads(args.global_cover.read_text())
    if cover.get('status') != 'topology_global_unique_exact_cover_candidate':
        raise SystemExit('requires topology_global_unique_exact_cover_candidate')
    allowed = selected_idf_pipes(cover, args.page)
    li, pi, fi, landmarks_i = side_data(graph, 'idf', args.page, allowed)
    ld, pd, fd, landmarks_d = side_data(graph, 'dxf', args.page, None)
    sig_i = {key: signature(key, li, pi, fi, landmarks_i, 'idf') for key in landmarks_i}
    sig_d = {key: signature(key, ld, pd, fd, landmarks_d, 'dxf') for key in landmarks_d}
    paths_i = {key: landmark_paths(li, pi, fi, landmarks_i, key) for key in landmarks_i}
    paths_d = {key: landmark_paths(ld, pd, fd, landmarks_d, key) for key in landmarks_d}
    options = {key: [] for key in landmarks_i}
    for left in landmarks_i:
        for right in landmarks_d:
            score = local_score(sig_i[left], sig_d[right])
            if score is not None:
                options[left].append((right, score))
        options[left].sort(key=lambda item: (-item[1], item[0]))

    # Exact global search is intentionally limited to the semantic frames of
    # one selected page.  A page with too many symmetric frames remains
    # unresolved rather than being greedily forced.
    ordered = sorted(landmarks_i, key=lambda key: (len(options[key]), key))
    results = []
    def visit(index, mapping, used, score):
        if index == len(ordered):
            if mapping:
                results.append((score + assignment_score(mapping, paths_i, paths_d), dict(mapping)))
            return
        left = ordered[index]
        # Missing semantic frame is an allowed edit.  Its cost prevents a
        # partial candidate from looking equal to a complete one.
        visit(index + 1, mapping, used, score - 2.0)
        for right, local in options[left]:
            if right in used:
                continue
            mapping[left] = right; used.add(right)
            visit(index + 1, mapping, used, score + local)
            used.remove(right); mapping.pop(left)
    visit(0, {}, set(), 0.0)
    ranked = []
    seen = set()
    enriched = []
    for score, mapping in results:
        geometry, transform_name, cosines = geometry_hypothesis_score(mapping, li, ld)
        # Geometry remains a bounded tie-breaker; topology still dominates.
        enriched.append((score + min(3.0, max(-3.0, geometry)), score, transform_name, cosines, mapping))
    for total, topology, transform_name, cosines, mapping in sorted(enriched,
                                                                      key=lambda item: (-item[0], sorted(item[4].items()))):
        key = tuple(sorted(mapping.items()))
        if key in seen:
            continue
        seen.add(key)
        ranked.append({'score': round(total, 3), 'topology_score': round(topology, 3),
                       'geometry_axis_hypothesis': transform_name,
                       'geometry_relative_cosines': [round(value, 4) for value in cosines],
                       'frame_map': [{'idf_frame': left, 'dxf_frame': right}
                                                                 for left, right in sorted(mapping.items())],
                       'matched_landmarks': len(mapping),
                       'unmatched_idf_landmarks': sorted(landmarks_i - set(mapping))})
        if len(ranked) >= args.max_candidates:
            break
    margin = None if len(ranked) < 2 else round(ranked[0]['score'] - ranked[1]['score'], 3)
    result = {'algorithm': 'ATTRIBUTED_COMPONENT_SKELETON_CANDIDATES_V1',
              'policy': 'review-only maximum-common-subgraph style search; unnamed DXF/IDF connectors are traversable edits; no CONT, coordinates, or final I100 assignment',
              'line_key': graph['line_key'], 'page': args.page, 'idf_range': sorted(allowed, key=number),
              'idf_landmark_signatures': sig_i, 'dxf_landmark_signatures': sig_d,
              'candidate_count': len(ranked), 'best_score_margin': margin,
              'candidates': ranked}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': graph['line_key'], 'page': args.page, 'idf_landmarks': len(landmarks_i),
                      'dxf_landmarks': len(landmarks_d), 'candidates': len(ranked), 'margin': margin}, ensure_ascii=False))


if __name__ == '__main__':
    main()
