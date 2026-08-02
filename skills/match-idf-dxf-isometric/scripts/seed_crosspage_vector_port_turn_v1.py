#!/usr/bin/env python3
"""Seed a cross-page IDF turn from a vector-traced continuation port.

The continuation text itself is not an anchor.  Its attached short DXF
leader/polyline package is traced from the text extent; the current-page pipe
must have an exact source endpoint contact with that package.  A prior-page
pipe is used only after it has already been independently matched.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
from pathlib import Path

import ezdxf


def text_port(path: Path, phrase: str, page: int):
    doc = ezdxf.readfile(path)
    rows = []
    for entity in doc.modelspace():
        if entity.dxftype() in {'TEXT', 'MTEXT'}:
            value = (entity.dxf.text if entity.dxftype() == 'TEXT' else entity.text).upper()
            point = entity.dxf.insert
            rows.append((value, (float(point.x), float(point.y))))
    phrase_points = [point for value, point in rows if phrase in value]
    page_points = [point for value, point in rows if f'DRG {page}' in value]
    pairs = [(a, b) for a in phrase_points for b in page_points if math.dist(a, b) <= 8]
    return ((pairs[0][0][0] + pairs[0][1][0]) / 2, (pairs[0][0][1] + pairs[0][1][1]) / 2) if len(pairs) == 1 else None


def segment_points(entity):
    try:
        if entity.dxftype() == 'LINE':
            return [(float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y))]
        if entity.dxftype() == 'LWPOLYLINE':
            return [(float(point[0]), float(point[1])) for point in entity.get_points()]
        if entity.dxftype() == 'POLYLINE':
            return [(float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in entity.vertices]
    except Exception:
        return []
    return []


def entity_width(entity):
    """Return the effective polyline width when available.

    Leader tracing must never traverse a real 0.6-wide pipe merely because a
    leader arrow touches it; otherwise a support joint leaks the port to the
    adjacent pipe.
    """
    try:
        if entity.dxftype() == 'LWPOLYLINE':
            width = float(entity.dxf.const_width or 0)
            if width:
                return width
            values = entity.get_points('sew')
            return max((max(abs(point[2]), abs(point[3])) for point in values), default=0.0)
        if entity.dxftype() == 'POLYLINE':
            return max(abs(float(entity.dxf.default_start_width or 0)),
                       abs(float(entity.dxf.default_end_width or 0)))
    except Exception:
        return 0.0
    return 0.0


def leader_points(path: Path, seed):
    doc = ezdxf.readfile(path)
    segments = []
    for entity in doc.modelspace():
        if entity.dxftype() not in {'LINE', 'LWPOLYLINE', 'POLYLINE'}:
            continue
        if abs(entity_width(entity) - .6) <= .05:
            continue
        points = segment_points(entity)
        if len(points) != 2 or math.dist(*points) > 30:
            continue
        if min(math.dist(seed, point) for point in points) <= 100:
            segments.append(points)
    adjacent = [[] for _ in segments]
    for i, left in enumerate(segments):
        for j, right in enumerate(segments[:i]):
            if min(math.dist(a, b) for a in left for b in right) <= .25:
                adjacent[i].append(j); adjacent[j].append(i)
    queue, seen, reached = [], set(), []
    for index, points in enumerate(segments):
        distance = min(math.dist(seed, point) for point in points)
        if distance <= 25:
            heapq.heappush(queue, (distance, index))
    while queue:
        distance, index = heapq.heappop(queue)
        if index in seen or distance > 80:
            continue
        seen.add(index); reached.extend(segments[index])
        for neighbour in adjacent[index]:
            heapq.heappush(queue, (distance + math.dist(*segments[index]), neighbour))
    return reached


def exact_port_pipes(pipes, points, page):
    return [pipe['id'] for pipe in pipes if pipe['page'] == page and
            any(math.dist(endpoint, point) <= .15 for endpoint in pipe['endpoints'] for point in points)]


def unique_near_port_pipe(pipes, points, page, occupied):
    """Select a current-page pipe only for a strongly separated leader contact.

    Exported leader arrow tips are sometimes slightly short of the pipe end.
    This accepts that bounded export error, but does not turn generic text
    proximity into an anchor: the closest unoccupied pipe endpoint must be
    within 1 drawing unit and separated from the next candidate by 2 units.
    """
    scores = []
    for pipe in pipes:
        if pipe['page'] != page or pipe['id'] in occupied:
            continue
        distance = min(math.dist(endpoint, point)
                       for endpoint in pipe['endpoints'] for point in points)
        scores.append((distance, pipe['id']))
    scores.sort()
    if not scores or scores[0][0] > 1.0:
        return None
    if len(scores) > 1 and scores[1][0] - scores[0][0] < 2.0:
        return None
    return scores[0]


def elbow(frame, side):
    return frame['kind'] == 'turn_2' and frame['degree'] == 2 if side == 'idf' else \
           frame['kind'] == 'elbow' and frame['degree'] == 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('frame_graph', type=Path)
    parser.add_argument('dxf_topology', type=Path)
    parser.add_argument('prior_matches', type=Path)
    parser.add_argument('current_matches', type=Path)
    parser.add_argument('--prior-page', required=True, type=int)
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--prior-dxf', type=Path, required=True)
    parser.add_argument('--current-dxf', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.frame_graph.read_text())
    topology = json.loads(args.dxf_topology.read_text())
    prior = json.loads(args.prior_matches.read_text())
    source = json.loads(args.current_matches.read_text())
    idf_frames = {row['id']: row for row in graph['idf']['frames']}
    dxf_frames = {row['id']: row for row in graph['dxf']['frames'] if row['page'] == args.page}
    idf_incidence = {row['pipe']: row['frames'] for row in graph['idf']['pipe_frame_incidence']}
    dxf_incidence = {row['pipe']: row['frames'] for row in graph['dxf']['pipe_frame_incidence']}
    pipes = topology['pipes']
    prior_map = {row['idf_pipe']: row['dxf_pipe'] for row in prior['pipe_matches'] if row.get('dxf_pipe')}
    current = {row['idf_pipe']: row.get('dxf_pipe') for row in source['pipe_matches']}
    prior_text = text_port(args.prior_dxf, 'CONT. ON', args.page)
    current_text = text_port(args.current_dxf, 'CONT. FROM', args.prior_page)
    additions, frame_additions = [], []
    if prior_text and current_text:
        prior_port = set(exact_port_pipes(pipes, leader_points(args.prior_dxf, prior_text), args.prior_page))
        current_port = unique_near_port_pipe(
            pipes, leader_points(args.current_dxf, current_text), args.page,
            set(value for value in current.values() if value))
        previous = [(idf_pipe, dxf_pipe) for idf_pipe, dxf_pipe in prior_map.items() if dxf_pipe in prior_port]
        if len(previous) == 1 and current_port:
            prior_idf, prior_dxf = previous[0]; current_distance, current_dxf = current_port
            options = []
            for frame_id in idf_incidence.get(prior_idf, []):
                frame = idf_frames[frame_id]
                if not elbow(frame, 'idf'):
                    continue
                for pipe in frame['incident_pipes']:
                    if pipe != prior_idf and pipe in current and current[pipe] is None:
                        options.append((frame_id, pipe))
            if len(options) == 1:
                crossing, current_idf = options[0]
                current[current_idf] = current_dxf
                additions.append({'idf_pipe': current_idf, 'dxf_pipe': current_dxf,
                                  'evidence': 'independent_prior_match_plus_idf_turn_plus_unique_vector_port_contact',
                                  'prior_idf_pipe': prior_idf, 'prior_dxf_pipe': prior_dxf,
                                  'leader_endpoint_distance': round(current_distance, 4)})
                left = [frame for frame in idf_incidence.get(current_idf, []) if frame != crossing and elbow(idf_frames[frame], 'idf')]
                right = [frame for frame in dxf_incidence.get(current_dxf, []) if frame in dxf_frames and elbow(dxf_frames[frame], 'dxf')]
                existing = {row['idf_frame']: row['dxf_frame'] for row in source.get('frame_matches', [])}
                if len(left) == len(right) == 1 and left[0] not in existing and right[0] not in existing.values():
                    frame_additions.append({'idf_frame': left[0], 'dxf_frame': right[0],
                                            'evidence': 'unique_non_crossing_turn_after_exact_vector_port_bridge'})
    added = {row['idf_pipe'] for row in additions}
    rows = []
    for row in source['pipe_matches']:
        item = dict(row)
        if item['idf_pipe'] in added:
            item.update({'dxf_pipe': current[item['idf_pipe']], 'confidence': 'medium_crosspage_vector_port',
                         'evidence': 'independent_prior_match_plus_idf_turn_plus_unique_vector_port_contact'})
        rows.append(item)
    result = {**source, 'algorithm': 'CROSSPAGE_VECTOR_PORT_TURN_V1',
              'policy': 'CONT text is corroboration only; trace its zero-width source leader after an independent prior match and unique IDF 35/36 turn; current endpoint contact must be uniquely nearest (<=1.0, margin >=2.0)',
              'pipe_matches': rows, 'frame_matches': list(source.get('frame_matches', [])) + frame_additions,
              'crosspage_vector_port_additions': additions, 'crosspage_vector_port_frame_additions': frame_additions}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'pipe_additions': len(additions), 'frame_additions': len(frame_additions)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
