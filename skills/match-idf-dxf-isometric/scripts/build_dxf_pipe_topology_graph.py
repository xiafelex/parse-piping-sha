#!/usr/bin/env python3
"""Collapse a page-local DXF semantic graph to pipe↔component↔pipe topology.

It consumes the global-DXF-graph export only.  It does not classify geometry,
and it never joins pipes across a sheet boundary.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('global_dxf_graph', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.global_dxf_graph.read_text())
    endpoint_pipe = {endpoint['id']: endpoint['id'].rsplit(':', 1)[0] for endpoint in graph['endpoint_nodes']}
    pipes = {pipe['id']: pipe for pipe in graph['pipes']}
    contacts = defaultdict(list)
    direct = []
    for edge in graph['in_page_edges']:
        if edge['kind'] == 'endpoint_component_contact':
            contacts[edge['to']].append((endpoint_pipe[edge['from']], edge['from'], edge['distance']))
        elif edge['kind'] == 'in_page_exact_endpoint':
            left, right = endpoint_pipe[edge['from']], endpoint_pipe[edge['to']]
            if left != right:
                direct.append({'a': left, 'b': right, 'kind': 'exact_endpoint', 'distance': edge['distance'],
                               'endpoint_roles': edge['shared_role']})
    component_kind = {component['id']: component['kind'] for component in graph['components']}
    through = []
    for component_id, entries in contacts.items():
        # Duplicate contact records to the same pipe boundary are not a pipe
        # adjacency.  Preserve incident degree, especially for tee/branch.
        unique = {}
        for pipe, endpoint, distance in entries:
            unique.setdefault(pipe, {'pipe': pipe, 'endpoint': endpoint, 'distance': distance})
        incident = list(unique.values())
        if len(incident) < 2:
            continue
        through.append({'component_id': component_id, 'component_kind': component_kind[component_id],
                        'incident_pipes': incident, 'degree': len(incident)})
    result = {
        'algorithm': 'DXF_PIPE_COMPONENT_TOPOLOGY_V1', 'line_key': graph['line_key'],
        'coordinate_policy': 'only page-local semantic contacts; no cross-sheet coordinate merge',
        'pipes': [{'id': pipe['id'], 'page': pipe['page'], 'kind': pipe['kind'], 'handles': pipe['handles']}
                  for pipe in graph['pipes']],
        'direct_pipe_edges': direct,
        'through_component_hyperedges': through,
        'component_degree_histogram': {kind: sum(1 for edge in through if edge['component_kind'] == kind)
                                       for kind in sorted({edge['component_kind'] for edge in through})},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': graph['line_key'], 'pipe_count': len(pipes), 'direct_edges': len(direct),
                      'component_hyperedges': len(through), 'branch_like': sum(edge['degree'] >= 3 for edge in through)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
