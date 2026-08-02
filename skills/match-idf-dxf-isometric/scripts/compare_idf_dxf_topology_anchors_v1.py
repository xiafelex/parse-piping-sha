#!/usr/bin/env python3
"""Compare branch/tee topology anchors before any page-order matching."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf_pipe_topology', type=Path)
    parser.add_argument('dxf_pipe_topology', type=Path)
    parser.add_argument('--outlet-candidates', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    idf = json.loads(args.idf_pipe_topology.read_text())
    dxf = json.loads(args.dxf_pipe_topology.read_text())
    idf_anchors = [edge for edge in idf['connector_hyperedges'] if edge['degree'] == 3 and edge['record_codes'] == [41]]
    dxf_anchors = [edge for edge in dxf['through_component_hyperedges']
                   if edge['degree'] == 3 and edge['component_kind'] in {'branch', 'tee'}]
    dxf_handles = {pipe['id']: set(pipe['handles']) for pipe in dxf['pipes']}
    outlet = []
    if args.outlet_candidates and args.outlet_candidates.exists():
        outlet = json.loads(args.outlet_candidates.read_text()).get('matches', [])
    direct = []
    for candidate in outlet:
        candidate_id = candidate.get('idf')
        candidate_handles = set(candidate.get('dxf_handles', []))
        left = next((anchor for anchor in idf_anchors if candidate_id in anchor['incident_pipes']), None)
        right = next((anchor for anchor in dxf_anchors if any(candidate_handles <= dxf_handles[pipe['pipe']]
                                                             for pipe in anchor['incident_pipes'])), None)
        if left and right:
            direct.append({'idf_anchor': left['id'], 'idf_incident_pipes': left['incident_pipes'],
                           'dxf_anchor': right['component_id'],
                           'dxf_incident_pipes': [{'pipe': pipe['pipe'], 'handles': next(
                               item['handles'] for item in dxf['pipes'] if item['id'] == pipe['pipe'])}
                               for pipe in right['incident_pipes']],
                           'outlet_idf_pipe': candidate_id, 'outlet_dxf_handles': sorted(candidate_handles),
                           'confidence': 'medium',
                           'evidence': 'unique direct source-vector contact from prior outlet audit'})
    result = {'algorithm': 'IDF_DXF_TOPOLOGY_ANCHORS_V1', 'idf': idf['idf'], 'line_key': dxf['line_key'],
              'policy': 'branch/tee degree and direct outlet contact only; no page order or CONT input',
              'idf_branch_anchors': idf_anchors, 'dxf_branch_anchors': dxf_anchors,
              'counts_equal': len(idf_anchors) == len(dxf_anchors),
              'direct_anchor_matches': direct,
              'status': ('unique_branch_anchor' if len(idf_anchors) == len(dxf_anchors) == 1 else
                         'partial_multi_anchor_observability' if len(idf_anchors) == len(dxf_anchors) else
                         'branch_anchor_mismatch')}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': dxf['line_key'], 'idf_anchors': len(idf_anchors),
                      'dxf_anchors': len(dxf_anchors), 'direct_matches': len(direct), 'status': result['status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
