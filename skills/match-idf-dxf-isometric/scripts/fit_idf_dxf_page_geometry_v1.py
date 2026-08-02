#!/usr/bin/env python3
"""Fit one sheet-local DXF pipe graph into the canonical IDF axonometric graph.

This is a geometry/topology hypothesis generator.  It does not read CONT.
labels and it does not assert correspondence merely because a sheet number is
adjacent.  The caller must validate component/endpoint topology before using a
hypothesis as an I###→handle match.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from build_idf_100_topology import parse


def distance(a, b): return math.dist(a, b)
def vec(a, b): return (b[0] - a[0], b[1] - a[1])
def length(a, b): return math.hypot(b[0] - a[0], b[1] - a[1])
def angle(a, b): return math.atan2(b[1] - a[1], b[0] - a[0])


def outer_endpoints(pipe):
    endpoints = pipe.get('endpoints', [])
    if len(endpoints) == 2:
        return endpoints
    points = [point for segment in pipe.get('source_vector_segments', []) for point in segment.get('endpoints', [])]
    if len(points) < 2:
        return []
    return list(max(((a, b) for index, a in enumerate(points) for b in points[index + 1:]), key=lambda x: distance(*x)))


def canonical_projection(edges):
    origin = tuple(min(min(edge['a'][axis], edge['b'][axis]) for edge in edges) for axis in range(3))
    def project(point):
        x, y, z = (point[index] - origin[index] for index in range(3))
        return ((x - y) * .5, (x + y) * .288675 - z * .57735)
    return project


def transform(point, seed_from, seed_to, scale, rotation, reflected):
    x, y = point[0] - seed_from[0], point[1] - seed_from[1]
    if reflected:
        y = -y
    c, s = math.cos(rotation), math.sin(rotation)
    return (seed_to[0] + scale * (c * x - s * y), seed_to[1] + scale * (s * x + c * y))


def affine_from_pairs(source_one, source_two, target_one, target_two):
    """Return affine map carrying two source direction vectors to target ones."""
    sv1, sv2 = vec(*source_one), vec(*source_two)
    tv1, tv2 = vec(*target_one), vec(*target_two)
    det = sv1[0] * sv2[1] - sv1[1] * sv2[0]
    if abs(det) < 1e-8:
        return None
    inverse = ((sv2[1] / det, -sv2[0] / det), (-sv1[1] / det, sv1[0] / det))
    matrix = (
        (tv1[0] * inverse[0][0] + tv2[0] * inverse[1][0], tv1[0] * inverse[0][1] + tv2[0] * inverse[1][1]),
        (tv1[1] * inverse[0][0] + tv2[1] * inverse[1][0], tv1[1] * inverse[0][1] + tv2[1] * inverse[1][1]),
    )
    tx = target_one[0][0] - matrix[0][0] * source_one[0][0] - matrix[0][1] * source_one[0][1]
    ty = target_one[0][1] - matrix[1][0] * source_one[0][0] - matrix[1][1] * source_one[0][1]
    return matrix, (tx, ty)


def affine_apply(point, model):
    matrix, offset = model
    return (matrix[0][0] * point[0] + matrix[0][1] * point[1] + offset[0],
            matrix[1][0] * point[0] + matrix[1][1] * point[1] + offset[1])


def segment_error(left, right):
    # Orientation was fixed by the seed, so use both endpoints and tolerate a
    # swapped representation for source polylines.
    direct = (distance(left[0], right[0]) + distance(left[1], right[1])) / 2
    reverse = (distance(left[0], right[1]) + distance(left[1], right[0])) / 2
    return min(direct, reverse)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf', type=Path)
    parser.add_argument('dxf_topology', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--top', type=int, default=12)
    args = parser.parse_args()
    raw = [edge for edge in parse(args.idf) if edge['code'] == 100]
    project = canonical_projection(raw)
    idf = [{'id': edge['id'], 'line': edge['line'], 'endpoints': [project(edge['a']), project(edge['b'])]} for edge in raw]
    source = json.loads(args.dxf_topology.read_text())
    dxf = []
    for index, pipe in enumerate(source['pipes']):
        endpoints = outer_endpoints(pipe)
        if len(endpoints) != 2 or length(*endpoints) < .01:
            continue
        dxf.append({'index': index, 'handles': pipe.get('handles', []), 'kind': pipe['kind'], 'endpoints': endpoints})
    hypotheses = []
    for source_pipe in dxf:
        source_length = length(*source_pipe['endpoints'])
        source_angle = angle(*source_pipe['endpoints'])
        for target in idf:
            target_length = length(*target['endpoints'])
            if not target_length:
                continue
            scale = target_length / source_length
            for reverse_target in (False, True):
                target_start, target_end = target['endpoints'] if not reverse_target else list(reversed(target['endpoints']))
                target_angle = angle(target_start, target_end)
                for reflected in (False, True):
                    rotation = target_angle - (-source_angle if reflected else source_angle)
                    mapped = []
                    for candidate in dxf:
                        mapped_endpoints = [transform(point, source_pipe['endpoints'][0], target_start, scale, rotation, reflected)
                                            for point in candidate['endpoints']]
                        best = min(idf, key=lambda edge: segment_error(mapped_endpoints, edge['endpoints']))
                        error = segment_error(mapped_endpoints, best['endpoints'])
                        tolerance = max(scale * 1.5, length(*best['endpoints']) * .025)
                        if error <= tolerance:
                            mapped.append({'pipe_index': candidate['index'], 'handles': candidate['handles'],
                                           'kind': candidate['kind'], 'idf_id': best['id'],
                                           'error': round(error, 3), 'tolerance': round(tolerance, 3)})
                    unique = len({item['idf_id'] for item in mapped})
                    # Prefer a genuine subgraph embedding: one DXF group per
                    # IDF segment.  Support-split collisions lower the score,
                    # rather than being silently treated as a match.
                    score = unique * 10 + len(mapped) - (len(mapped) - unique) * 6
                    hypotheses.append({'seed': {'dxf_pipe_index': source_pipe['index'], 'dxf_handles': source_pipe['handles'],
                                                'idf_id': target['id']},
                                       'transform': {'scale': scale, 'rotation_degrees': math.degrees(rotation), 'reflected': reflected},
                                       'score': score, 'unique_idf_hits': unique, 'dxf_inlier_count': len(mapped),
                                       'matches': mapped})
    # A DXF ISO and the canonical IDF plane can use different two-axis bases.
    # A two-direction affine fit therefore supplies an independent hypothesis
    # class; it is still accepted only by later component/topology validation.
    for first_index, source_one in enumerate(dxf):
        for source_two in dxf[first_index + 1:]:
            if abs(math.sin(angle(*source_two['endpoints']) - angle(*source_one['endpoints']))) < .15:
                continue
            for target_index, target_one in enumerate(idf):
                for target_two in idf[target_index + 1:]:
                    for reverse_one in (False, True):
                        for reverse_two in (False, True):
                            one = target_one['endpoints'] if not reverse_one else list(reversed(target_one['endpoints']))
                            two = target_two['endpoints'] if not reverse_two else list(reversed(target_two['endpoints']))
                            model = affine_from_pairs(source_one['endpoints'], source_two['endpoints'], one, two)
                            if model is None:
                                continue
                            # A line-direction fit alone is not a placement:
                            # the second seed must also land in its IDF place.
                            placed_two = [affine_apply(point, model) for point in source_two['endpoints']]
                            nominal = max(length(*one), length(*two))
                            if segment_error(placed_two, two) > nominal * .035:
                                continue
                            mapped = []
                            for candidate in dxf:
                                placed = [affine_apply(point, model) for point in candidate['endpoints']]
                                best = min(idf, key=lambda edge: segment_error(placed, edge['endpoints']))
                                error = segment_error(placed, best['endpoints'])
                                tolerance = max(length(*best['endpoints']) * .035, 1500)
                                if error <= tolerance:
                                    mapped.append({'pipe_index': candidate['index'], 'handles': candidate['handles'],
                                                   'kind': candidate['kind'], 'idf_id': best['id'],
                                                   'error': round(error, 3), 'tolerance': round(tolerance, 3)})
                            unique = len({item['idf_id'] for item in mapped})
                            score = unique * 10 + len(mapped) - (len(mapped) - unique) * 6
                            hypotheses.append({'seed': {'dxf_pipe_indices': [source_one['index'], source_two['index']],
                                                        'dxf_handles': [source_one['handles'], source_two['handles']],
                                                        'idf_ids': [target_one['id'], target_two['id']]},
                                               'transform': {'model': 'affine_two_axis', 'matrix': model[0], 'offset': model[1]},
                                               'score': score, 'unique_idf_hits': unique, 'dxf_inlier_count': len(mapped),
                                               'matches': mapped})
    hypotheses.sort(key=lambda item: (-item['score'], -item['unique_idf_hits'],
                                      str(item['seed'].get('idf_id', item['seed'].get('idf_ids', [])))))
    # Deduplicate equivalent fit results caused by a different seed line.
    chosen = []
    seen = set()
    for item in hypotheses:
        key = tuple(sorted((match['pipe_index'], match['idf_id']) for match in item['matches']))
        if key in seen:
            continue
        seen.add(key); chosen.append(item)
        if len(chosen) == args.top:
            break
    result = {'algorithm': 'IDF_DXF_PAGE_GEOMETRY_FIT_V1', 'idf': args.idf.name, 'dxf': source['dxf'],
              'policy': 'no CONT. input; hypotheses require later component/topology validation',
              'idf_100_count': len(idf), 'dxf_pipe_count': len(dxf), 'hypotheses': chosen}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'dxf': source['dxf'], 'hypothesis_count': len(chosen),
                      'best_unique_idf_hits': chosen[0]['unique_idf_hits'] if chosen else 0,
                      'best_score': chosen[0]['score'] if chosen else 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
