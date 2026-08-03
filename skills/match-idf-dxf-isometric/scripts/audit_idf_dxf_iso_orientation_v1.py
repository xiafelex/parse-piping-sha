#!/usr/bin/env python3
"""Audit whether existing IDF-100 ↔ DXF-pipe matches share ISO directions.

This is a rejection/diagnostic check, not a matching method. It preserves raw
DXF coordinates and projects only IDF E/N/Z vectors onto the audited north.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from build_idf_100_topology import parse


def unit(vector):
    length = math.hypot(*vector)
    return (vector[0] / length, vector[1] / length)


def angle(vector):
    return math.degrees(math.atan2(vector[1], vector[0])) % 180.0


def undirected_delta(left, right):
    return abs((left - right + 90.0) % 180.0 - 90.0)


def project_vector(vector, north):
    """Project one IDF E/N/Z vector, then align canonical N to DXF N."""
    e, n, z = vector
    x = .5 * (e - n)
    y = .288675 * (e + n) + .57735 * z
    canonical_n = math.atan2(.288675, -.5)
    target_n = math.atan2(north[1], north[0])
    cosine, sine = math.cos(target_n - canonical_n), math.sin(target_n - canonical_n)
    return (cosine * x - sine * y, sine * x + cosine * y)


def local_id(value):
    return value.rsplit(':', 1)[-1] if value else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf', type=Path)
    parser.add_argument('dxf_topology', type=Path)
    parser.add_argument('matches', type=Path)
    parser.add_argument('--north-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--consistent-deg', type=float, default=5.0)
    args = parser.parse_args()

    north = unit(json.loads(args.north_audit.read_text())['vector_candidate'])
    idf = {edge['id']: edge for edge in parse(args.idf) if edge['code'] == 100}
    dxf = {local_id(pipe['id']): pipe for pipe in json.loads(args.dxf_topology.read_text())['pipes']}
    rows = []
    for match in json.loads(args.matches.read_text()).get('pipe_matches', []):
        iid, pid = match.get('idf_pipe'), local_id(match.get('dxf_pipe'))
        if iid not in idf or pid not in dxf or len(dxf[pid].get('endpoints') or []) != 2:
            continue
        edge, pipe = idf[iid], dxf[pid]
        vector = tuple(edge['b'][index] - edge['a'][index] for index in range(3))
        idf_angle = angle(project_vector(vector, north))
        a, b = pipe['endpoints']
        dxf_angle = angle((b[0] - a[0], b[1] - a[1]))
        residual = undirected_delta(idf_angle, dxf_angle)
        rows.append({
            'idf_pipe': iid, 'dxf_pipe': pipe['id'], 'match_confidence': match.get('confidence'),
            'match_evidence': match.get('evidence'), 'idf_projected_angle_deg': round(idf_angle, 3),
            'dxf_source_angle_deg': round(dxf_angle, 3), 'undirected_residual_deg': round(residual, 3),
            'orientation_status': 'consistent' if residual <= args.consistent_deg else 'discrepant',
        })
    residuals = [row['undirected_residual_deg'] for row in rows]
    result = {
        'algorithm': 'IDF_DXF_ISO_ORIENTATION_AUDIT_V1',
        'policy': 'raw DXF is never transformed; only IDF is projected. A discrepant topology match is excluded from projection-fidelity evidence.',
        'north_vector': [round(value, 6) for value in north], 'consistent_threshold_deg': args.consistent_deg,
        'summary': {
            'audited_matches': len(rows), 'consistent': sum(row['orientation_status'] == 'consistent' for row in rows),
            'discrepant': sum(row['orientation_status'] == 'discrepant' for row in rows),
            'mean_residual_deg': round(sum(residuals) / len(residuals), 3) if residuals else None,
            'max_residual_deg': round(max(residuals), 3) if residuals else None,
        }, 'rows': rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
