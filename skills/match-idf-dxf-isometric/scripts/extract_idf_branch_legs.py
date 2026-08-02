#!/usr/bin/env python3
"""Emit stable IDF 100 leg IDs incident to verified 41 branch junctions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('idf_topology', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    source = json.loads(args.idf_topology.read_text())
    graph = source['contracted_pipe_graph']
    edge_by_id = {edge['id']: edge for edge in graph['edges']}
    branch_sets = []
    for node in graph['nodes']:
        if node['degree'] != 3:
            continue
        # A three-way node becomes an IDF branch candidate only when its three
        # IDs agree with the 41-derived junction evidence from the raw graph.
        ids = node['incident_100']
        if not any(set(ids) >= set(raw['incident_100']) for raw in source['branch_nodes']
                   if raw['role'] == 'junction'):
            continue
        outlet_ids = set()
        for raw in source['branch_nodes']:
            if raw['role'] == 'outlet_leg':
                outlet_ids.update(raw['incident_100'])
        branch_sets.append({
            'node': node['id'],
            'idf_100_legs': [
                {'id': ident, 'line': edge_by_id[ident]['line'], 'bore': edge_by_id[ident]['bore'],
                 'role': 'outlet_leg' if ident in outlet_ids else 'main_leg'}
                for ident in ids
            ],
            'evidence': 'three-degree node created solely by verified IDF record 41',
        })
    result = {'algorithm': 'IDF_BRANCH_LEGS_V1', 'idf': source['idf'], 'branches': branch_sets,
              'status': 'ready_for_dxf_anchor_partition' if branch_sets else 'no_verified_41_branch_junction'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'idf': source['idf'], 'branch_count': len(branch_sets), 'status': result['status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
