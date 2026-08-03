#!/usr/bin/env python3
"""Rank multi-page IDF range covers using unique IDF 150 support locations.

This is a range-level constraint only.  A support count cannot by itself map
individual 100 pipes, and it must not overrule independently calibrated local
geometry when the two signals conflict.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def number(identifier):
    return int(identifier[1:])


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def unique_supports(rows, tolerance=10.0):
    output = []
    for row in rows:
        if not any(distance(row['point'], prior['point']) <= tolerance for prior in output):
            output.append(row)
    return output


def range_support_count(idf, supports, start, end):
    pipes = idf['pipes'][start - 1:end]
    return sum(any(min(distance(s['point'], pipe['a']), distance(s['point'], pipe['b'])) <= 10.0
                       for pipe in pipes) for s in supports)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf_topology', type=Path, help='full output from build_idf_100_topology.py')
    parser.add_argument('global_cover', type=Path)
    parser.add_argument('semantic_dir', type=Path)
    parser.add_argument('--line-key', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    idf = json.loads(args.idf_topology.read_text())
    cover = json.loads(args.global_cover.read_text())
    supports = unique_supports(idf.get('supports_150', []))
    dxf_supports = {}
    for path in sorted(args.semantic_dir.glob(f'*{args.line_key}*.json')):
        row = json.loads(path.read_text())
        page = int(path.stem.rsplit('-', 2)[-2])
        dxf_supports[page] = sum(component.get('kind') == 'support' for component in row.get('components', []))
    candidates = []
    for index, candidate in enumerate([cover['best'], *cover.get('alternatives', [])]):
        if candidate.get('missing_indices') or candidate.get('duplicate_indices'):
            continue
        pages = []
        residual = 0
        for page_range in candidate['page_ranges']:
            start, end = (number(value) for value in page_range['idf_range'])
            expected = range_support_count(idf, supports, start, end)
            observed = dxf_supports.get(page_range['page'])
            difference = None if observed is None else abs(expected - observed)
            residual += difference if difference is not None else 99
            pages.append({'page': page_range['page'], 'idf_range': page_range['idf_range'],
                          'idf_unique_150_supports': expected, 'dxf_supports': observed,
                          'absolute_difference': difference})
        candidates.append({'cover_candidate_index': index, 'support_count_residual': residual, 'pages': pages})
    ranked = sorted(candidates, key=lambda row: row['support_count_residual'])
    unique = len(ranked) == 1 or ranked[0]['support_count_residual'] < ranked[1]['support_count_residual']
    result = {'algorithm': 'IDF150_DXF_SUPPORT_COUNT_RANGE_V1',
              'policy': 'count unique physical 150 points at selected 100 endpoints; range evidence only, never individual I-to-P mapping',
              'line_key': args.line_key, 'idf_unique_150_total': len(supports), 'dxf_page_support_counts': dxf_supports,
              'candidates': candidates, 'status': 'unique_support_count_range_candidate' if unique else 'support_count_tied'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'status': result['status'], 'best_index': ranked[0]['cover_candidate_index'],
                      'residual': ranked[0]['support_count_residual']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
