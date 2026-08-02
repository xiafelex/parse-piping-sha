#!/usr/bin/env python3
"""Match a unique unmatched straight-pipe corridor by its transition signature.

This is an error-tolerant *attributed path-subgraph* rule.  IDF may express a
physical join directly while the DXF has a support cut there; the comparison
therefore preserves only whether each transition is an elbow or a non-elbow
direct/cut connection.  It is deliberately unable to introduce a mapping when
two candidate corridors have the same signature.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import ezdxf


def idf_transitions(idf):
    pipes = {row['id']: row for row in idf['pipes']}
    points = {row['id']: tuple(row['point']) for row in idf['raw_geometry_graph']['nodes']}
    edges = idf['raw_geometry_graph']['edges']
    endpoint_edges = defaultdict(list)
    for edge in edges:
        endpoint_edges[edge['a']].append(edge)
        endpoint_edges[edge['b']].append(edge)
    # Only two-record 35/36 chains are promoted to elbows.  All remaining
    # exact pipe-to-pipe joins stay `direct`; no other raw IDF code is named.
    links = []
    for left in pipes.values():
        for right in pipes.values():
            if left['id'] >= right['id']:
                continue
            shared = set(map(tuple, (left['a'], left['b']))) & set(map(tuple, (right['a'], right['b'])))
            if shared:
                links.append((left['id'], right['id'], 'direct'))
    # Build raw 35/36 connected components and locate 100 records touching
    # each.  An exact two-code component with exactly two pipes is an elbow.
    raw = {edge['id']: edge for edge in edges if edge['record_code'] in {35, 36}}
    seen = set()
    for start in raw:
        if start in seen:
            continue
        stack, comp = [start], []
        touched_points = set()
        while stack:
            eid = stack.pop()
            if eid in seen:
                continue
            seen.add(eid); edge = raw[eid]; comp.append(edge)
            touched_points.update((edge['a'], edge['b']))
            for point in (edge['a'], edge['b']):
                for near in endpoint_edges[point]:
                    if near['id'] in raw and near['id'] not in seen:
                        stack.append(near['id'])
        if len(comp) != 2 or {edge['record_code'] for edge in comp} != {35, 36}:
            continue
        touched = []
        for pipe in pipes.values():
            if set(map(tuple, (pipe['a'], pipe['b']))) & {points[p] for p in touched_points}:
                touched.append(pipe['id'])
        if len(touched) == 2:
            links.append((min(touched), max(touched), 'elbow'))
    return links


def dxf_transitions(dxf, page):
    frame_kind = {}
    for row in dxf['through_component_hyperedges']:
        pipes = [item['pipe'] for item in row.get('incident_pipes', [])]
        if row.get('component_kind') == 'elbow' and len(pipes) == 2:
            frame_kind[tuple(sorted(pipes))] = 'elbow'
    links = []
    for row in dxf['direct_pipe_edges']:
        # Page is verified below from the pipe catalog.
        links.append((row['a'], row['b'], 'direct'))
    for pair, kind in frame_kind.items():
        links.append((pair[0], pair[1], kind))
    page_pipes = {row['id'] for row in dxf['pipes'] if row['page'] == page}
    return [row for row in links if row[0] in page_pipes and row[1] in page_pipes]


def path_candidates(nodes, links, length):
    graph = defaultdict(list)
    for left, right, kind in links:
        if left in nodes and right in nodes:
            graph[left].append((right, kind)); graph[right].append((left, kind))
    found = set()
    for start in nodes:
        def walk(path, signature):
            if len(path) == length:
                forward = tuple(path)
                reverse = tuple(reversed(path))
                key = min(forward, reverse)
                found.add((key, tuple(signature if key == forward else reversed(signature))))
                return
            for nxt, kind in graph[path[-1]]:
                if nxt not in path:
                    walk(path + [nxt], signature + [kind])
        walk([start], [])
    return found


def continuation_port(path: Path, phrase: str, page: int):
    """Return the unique CAD text port carrying ``phrase`` and ``DRG page``.

    This is deliberately a corroboration check.  It is never called until the
    attributed corridor itself is already unique.
    """
    doc = ezdxf.readfile(path)
    texts = []
    for entity in doc.modelspace():
        if entity.dxftype() not in {'TEXT', 'MTEXT'}:
            continue
        value = entity.dxf.text if entity.dxftype() == 'TEXT' else entity.text
        point = entity.dxf.insert
        texts.append((value.upper().strip(), (float(point.x), float(point.y))))
    phrase_rows = [point for value, point in texts if phrase in value]
    label = f'DRG {page}'
    labelled = [point for value, point in texts if label in value]
    pairs = [(a, b) for a in phrase_rows for b in labelled
             if ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** .5 <= 8.0]
    if len(pairs) != 1:
        return None
    a, b = pairs[0]
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def closest_port_distance(pipe, point):
    return min(((endpoint[0] - point[0]) ** 2 + (endpoint[1] - point[1]) ** 2) ** .5
               for endpoint in pipe['endpoints'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf_topology', type=Path)
    parser.add_argument('dxf_topology', type=Path)
    parser.add_argument('propagation', type=Path)
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--length', required=True, type=int)
    parser.add_argument('--prior-dxf', type=Path,
                        help='previous, independently matched DXF page; used only to corroborate an already unique corridor')
    parser.add_argument('--prior-pipe',
                        help='independently matched pipe on --prior-dxf ending at CONT. ON')
    parser.add_argument('--prior-page', type=int,
                        help='page number represented by --prior-dxf; current page must say CONT. FROM DRG this number')
    parser.add_argument('--current-dxf', type=Path,
                        help='source DXF for the current page, required with all --prior-* arguments')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    idf = json.loads(args.idf_topology.read_text())
    dxf = json.loads(args.dxf_topology.read_text())
    source = json.loads(args.propagation.read_text())
    current = {row['idf_pipe']: row.get('dxf_pipe') for row in source['pipe_matches']}
    idf_nodes = {pipe for pipe, target in current.items() if target is None}
    dxf_page = {row['id'] for row in dxf['pipes'] if row['page'] == args.page}
    used = {target for target in current.values() if target}
    dxf_nodes = dxf_page - used
    left_paths = path_candidates(idf_nodes, idf_transitions(idf), args.length)
    right_paths = path_candidates(dxf_nodes, dxf_transitions(dxf, args.page), args.length)
    matches = []
    for left_path, left_signature in left_paths:
        for right_path, right_signature in right_paths:
            if left_signature == right_signature:
                matches.append((left_path, right_path, left_signature))
    # A path can be mirrored in the sheet.  Both directions of each DXF path
    # represent separate assignments, and only a unique assignment is safe.
    assignments = []
    for left, right, signature in matches:
        assignments.append((left, right, signature))
        assignments.append((left, tuple(reversed(right)), tuple(reversed(signature))))
    # IDF direction is intrinsic record sequence here; accept only one exact
    # candidate once a direction-independent signature was selected.
    if len(matches) != 1:
        result = {**source, 'algorithm': 'UNIQUE_CORRIDOR_SIGNATURE_V1',
                  'status': 'unresolved_nonunique_corridor',
                  'corridor_candidates': len(matches), 'corridor_additions': []}
    else:
        left, right, signature = matches[0]
        orientation = None
        evidence = None
        provided = (args.prior_dxf, args.prior_pipe, args.prior_page, args.current_dxf)
        if any(provided) and not all(provided):
            raise SystemExit('continuation corroboration requires --prior-dxf --prior-pipe --prior-page --current-dxf together')
        if all(provided):
            prior_port = continuation_port(args.prior_dxf, 'CONT. ON', args.page)
            current_port = continuation_port(args.current_dxf, 'CONT. FROM', args.prior_page)
            prior = next((pipe for pipe in dxf['pipes'] if pipe['id'] == args.prior_pipe), None)
            current = {pipe['id']: pipe for pipe in dxf['pipes']}
            if prior_port and current_port and prior:
                prior_distance = closest_port_distance(prior, prior_port)
                forward = closest_port_distance(current[right[0]], current_port)
                reverse = closest_port_distance(current[right[-1]], current_port)
                if prior_distance <= 50 and forward <= 50 and reverse - forward >= 15:
                    orientation = right
                    evidence = {'kind': 'topology_unique_then_continuation_port_corroboration',
                                'prior_pipe': args.prior_pipe, 'prior_port_distance': round(prior_distance, 4),
                                'current_port_distance': round(forward, 4),
                                'reverse_endpoint_distance': round(reverse, 4)}
        if orientation:
            rows = []
            added = set(left)
            mapping = dict(zip(left, orientation))
            for row in source['pipe_matches']:
                item = dict(row)
                if item['idf_pipe'] in mapping:
                    item.update({'dxf_pipe': mapping[item['idf_pipe']], 'confidence': 'medium_corridor_signature',
                                 'evidence': 'unique_corridor_signature_with_continuation_corroboration'})
                rows.append(item)
            result = {**source, 'algorithm': 'UNIQUE_CORRIDOR_SIGNATURE_V1',
                      'status': 'unique_corridor_oriented_by_corroboration',
                      'corridor_signature': list(signature), 'pipe_matches': rows,
                      'corridor_additions': [{'idf_pipe': item, 'dxf_pipe': mapping[item], 'evidence': evidence}
                                             for item in left]}
        else:
            result = {**source, 'algorithm': 'UNIQUE_CORRIDOR_SIGNATURE_V1',
                      'status': 'candidate_requires_orientation_anchor',
                      'corridor_signature': list(signature),
                      'corridor_candidates': [{'idf_path': left, 'dxf_path': right},
                                               {'idf_path': left, 'dxf_path': tuple(reversed(right))}],
                      'corridor_additions': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'status': result['status'], 'candidates': result.get('corridor_candidates')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
