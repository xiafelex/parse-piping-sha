#!/usr/bin/env python3
"""Calibrate one project-level IDF canonical→DXF axis hypothesis.

Only independently established frame maps may be supplied.  The tool measures
relative frame displacements, never absolute sheet coordinates, and requires
agreement across at least two source-page samples before a transform can be
used by later arm-ordering rules.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path


TRANSFORMS = ('identity', 'flip_x', 'flip_y', 'flip_xy', 'swap', 'swap_flip_x',
              'swap_flip_y', 'swap_flip_xy')


def transform(vector, name):
    x, y = vector
    return {'identity': (x, y), 'flip_x': (-x, y), 'flip_y': (x, -y),
            'flip_xy': (-x, -y), 'swap': (y, x), 'swap_flip_x': (-y, x),
            'swap_flip_y': (y, -x), 'swap_flip_xy': (-y, -x)}[name]


def load_sample(frame_graph_path, matches_path):
    graph = json.loads(frame_graph_path.read_text())
    matches = json.loads(matches_path.read_text())
    idf = {row['id']: row for row in graph['idf']['frames']}
    dxf = {row['id']: row for row in graph['dxf']['frames']}
    pairs = [(row['idf_frame'], row['dxf_frame']) for row in matches.get('frame_matches', [])
             if row['idf_frame'] in idf and row['dxf_frame'] in dxf and
             idf[row['idf_frame']].get('centre') and dxf[row['dxf_frame']].get('centre') and
             row.get('evidence') != 'canonical_relative_frame_direction']
    return graph['line_key'], matches.get('page'), idf, dxf, pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sample', nargs=2, action='append', metavar=('FRAME_GRAPH', 'MATCHES'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    observations = {name: [] for name in TRANSFORMS}
    samples = []
    for frame_path, match_path in args.sample:
        line, page, idf, dxf, pairs = load_sample(Path(frame_path), Path(match_path))
        per_transform = {name: [] for name in TRANSFORMS}
        for (idf_a, dxf_a), (idf_b, dxf_b) in itertools.combinations(pairs, 2):
            left = (idf[idf_b]['centre'][0] - idf[idf_a]['centre'][0],
                    idf[idf_b]['centre'][1] - idf[idf_a]['centre'][1])
            right = (dxf[dxf_b]['centre'][0] - dxf[dxf_a]['centre'][0],
                     dxf[dxf_b]['centre'][1] - dxf[dxf_a]['centre'][1])
            for name in TRANSFORMS:
                candidate = transform(left, name)
                normalizer = math.hypot(*candidate) * math.hypot(*right)
                if normalizer:
                    per_transform[name].append((candidate[0] * right[0] + candidate[1] * right[1]) / normalizer)
        samples.append({'line_key': line, 'page': page, 'frame_pair_count': len(pairs),
                        'relative_observation_count': len(next(iter(per_transform.values()))),
                        'best_per_transform': {name: round(sum(values) / len(values), 5) if values else None
                                               for name, values in per_transform.items()}})
        for name, values in per_transform.items():
            observations[name].extend((line, page, value) for value in values)
    score = []
    for name, rows in observations.items():
        values = [row[2] for row in rows]
        sample_keys = {(row[0], row[1]) for row in rows if row[2] >= .9}
        score.append({'transform': name, 'observation_count': len(values),
                      'inlier_count_cos_ge_0_9': sum(value >= .9 for value in values),
                      'independent_samples_with_inlier': len(sample_keys),
                      'mean_cosine': round(sum(values) / len(values), 5) if values else None,
                      'inlier_mean_cosine': round(sum(value for value in values if value >= .9) /
                                                  sum(value >= .9 for value in values), 5)
                                           if any(value >= .9 for value in values) else None})
    score.sort(key=lambda row: (-row['independent_samples_with_inlier'], -row['inlier_count_cos_ge_0_9'],
                                -(row['mean_cosine'] if row['mean_cosine'] is not None else -2), row['transform']))
    best, second = score[:2]
    valid = (best['independent_samples_with_inlier'] >= 2 and
             best['inlier_count_cos_ge_0_9'] - second['inlier_count_cos_ge_0_9'] >= 2)
    result = {'algorithm': 'PROJECT_AXIS_CALIBRATION_FROM_VERIFIED_FRAME_MAPS_V1',
              'policy': 'relative displacement only; two independently solved source pages required; no candidate under review may calibrate itself',
              'samples': samples, 'transform_scores': score,
              'status': 'project_axis_calibrated' if valid else 'insufficient_independent_frame_evidence',
              'axis_transform': best['transform'] if valid else None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'status': result['status'], 'axis_transform': result['axis_transform'],
                      'best': best['transform'], 'inliers': best['inlier_count_cos_ge_0_9']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
