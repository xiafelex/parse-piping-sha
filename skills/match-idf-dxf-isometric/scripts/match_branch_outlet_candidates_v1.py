#!/usr/bin/env python3
"""Find conservative DXF candidates for an IDF 41 outlet leg.

This matches only a directly vector-touching DXF pipe to the uniquely typed
IDF outlet leg.  Main legs are deliberately left unresolved until a complete
component adjacency graph is available.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def point_segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    t = 0 if not denom else max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denom))
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))


def touches_outline(endpoint, outline):
    return min((point_segment_distance(endpoint, a, b) for a, b in zip(outline, outline[1:])), default=float('inf'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('idf_legs', type=Path)
    ap.add_argument('dxf_topology', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--tolerance', type=float, default=1.1)
    args = ap.parse_args()
    idf = json.loads(args.idf_legs.read_text())
    dxf = json.loads(args.dxf_topology.read_text())
    branches = [x for x in dxf['components'] if x['kind'] in {'branch', 'tee'}]
    results = []
    # This v1 is intentionally only safe for one branch on each side.  Global
    # branch assignment is a later graph-matching problem.
    if len(idf['branches']) == 1 and len(branches) == 1:
        outlet = [x for x in idf['branches'][0]['idf_100_legs'] if x['role'] == 'outlet_leg']
        body = branches[0]
        contacts = []
        for pipe in dxf['pipes']:
            distances = [touches_outline(end, body.get('outline', [])) for end in pipe['endpoints']]
            best = min(distances, default=float('inf'))
            if best <= args.tolerance:
                contacts.append({'handles': pipe['handles'], 'kind': pipe['kind'],
                                 'distance': round(best, 4), 'endpoints': pipe['endpoints']})
        if len(outlet) == 1 and len(contacts) == 1:
            results.append({'idf': outlet[0]['id'], 'dxf_source': dxf['dxf'],
                            'dxf_handles': contacts[0]['handles'], 'dxf_kind': contacts[0]['kind'],
                            'confidence': 'medium',
                            'evidence': ['unique IDF 41 outlet_leg', 'exact DXF typed-pipe to branch-body vector contact'],
                            'distance': contacts[0]['distance'],
                            'status': 'candidate_requires_local_review'})
    result = {'algorithm': 'BRANCH_OUTLET_CONTACT_V1', 'idf': idf['idf'], 'dxf': dxf['dxf'],
              'matches': results,
              'policy': 'only unique direct branch-body contacts; main legs remain unresolved'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'match_count': len(results), 'algorithm': result['algorithm']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
