#!/usr/bin/env python3
"""Build IDF 100↔non-100 connector hyperedges without assigning code semantics."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


class UnionFind:
    def __init__(self, values): self.parent = {value: value for value in values}
    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]; value = self.parent[value]
        return value
    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right: self.parent[right] = left


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf_topology', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.idf_topology.read_text())
    raw = source['raw_geometry_graph']
    records = raw['edges']
    nodes = [node['id'] for node in raw['nodes']]
    uf = UnionFind(nodes)
    for edge in records:
        if edge['record_code'] != 100:
            uf.union(edge['a'], edge['b'])
    component_edges, pipe_at_node = defaultdict(list), defaultdict(list)
    for edge in records:
        if edge['record_code'] == 100:
            pipe_at_node[edge['a']].append(edge['id']); pipe_at_node[edge['b']].append(edge['id'])
        else:
            component_edges[uf.find(edge['a'])].append(edge)
    incident = defaultdict(set)
    for node, pipes in pipe_at_node.items():
        incident[uf.find(node)].update(pipes)
    hyperedges = []
    for root, edges in component_edges.items():
        codes = sorted(edge['record_code'] for edge in edges)
        pipes = sorted(incident[root])
        hyperedges.append({'id': f'K{len(hyperedges)+1:03d}', 'record_codes': codes,
                           'record_lines': [edge['line'] for edge in edges],
                           'incident_pipes': pipes, 'degree': len(pipes)})
    result = {'algorithm': 'IDF_PIPE_COMPONENT_TOPOLOGY_V1', 'idf': source['idf'],
              'policy': 'raw record-code sequences are evidence, not globally assigned component classes',
              'pipes': [{'id': pipe['id'], 'line': pipe['line'], 'bore': pipe['bore']} for pipe in source['pipes']],
              'connector_hyperedges': hyperedges,
              'degree_histogram': {str(degree): sum(edge['degree'] == degree for edge in hyperedges)
                                   for degree in sorted({edge['degree'] for edge in hyperedges})}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'idf': source['idf'], 'connector_count': len(hyperedges),
                      'branch_like': sum(edge['degree'] >= 3 for edge in hyperedges)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
