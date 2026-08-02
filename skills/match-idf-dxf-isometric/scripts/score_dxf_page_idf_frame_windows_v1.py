#!/usr/bin/env python3
"""Rank IDF subgraphs for each DXF page from component-frame structure.

The score intentionally starts with structural frames, not individual pipe
identity.  Pipe count only limits the candidate window scale; it is never
treated as an individual correspondence proof.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def numeric(identifier): return int(identifier[1:])


def idf_signature(frames, start, end):
    result = Counter()
    for frame in frames:
        ids = [numeric(item) for item in frame['incident_pipes']]
        if not ids or min(ids) < start or max(ids) > end:
            continue
        if frame['kind'] == 'junction_3': result['junction'] += 1
        elif frame['kind'] == 'turn_2': result['elbow'] += 1
        elif frame['kind'] == 'inline_2' and frame['bore_change']: result['reducer'] += 1
    return result


def dxf_signature(frames):
    result = Counter()
    for frame in frames:
        if frame['kind'] in {'branch', 'tee'} and frame['degree'] == 3: result['junction'] += 1
        elif frame['kind'] == 'elbow' and frame['degree'] == 2: result['elbow'] += 1
        elif frame['kind'] == 'reducer' and frame['degree'] == 2: result['reducer'] += 1
    return result


def score(left, right):
    # Junctions are strongest anchors, then reducer, then turn sequence.
    weights = {'junction': 9, 'reducer': 5, 'elbow': 2}
    distance = sum(weights[key] * abs(left[key] - right[key]) for key in weights)
    exact = sum(weights[key] for key in weights if left[key] == right[key] and left[key])
    return exact * 10 - distance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('component_frame_graph', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--window-slack', type=int, default=2,
                        help='allow DXF support/duplicate fragments to change candidate scale')
    parser.add_argument('--top', type=int, default=5)
    args = parser.parse_args()
    graph = json.loads(args.component_frame_graph.read_text())
    idf_frames = graph['idf']['frames']; total = len(graph['idf']['pipe_frame_incidence'])
    pages = defaultdict(list)
    for frame in graph['dxf']['frames']:
        pages[frame['page']].append(frame)
    # Every classified pipe is retained even when it touches no recognised
    # component; it controls only window scale, never an I### assignment.
    raw_pipes = Counter(item['page'] for item in graph['dxf']['pipe_frame_incidence'])
    result_pages = []
    for page, frames in sorted(pages.items()):
        signature = dxf_signature(frames)
        base = max(1, raw_pipes[page])
        candidates = []
        for width in range(max(1, base - args.window_slack), min(total, base + args.window_slack) + 1):
            for start in range(1, total - width + 2):
                end = start + width - 1
                candidate = idf_signature(idf_frames, start, end)
                candidates.append({'idf_range': [f'I{start:03d}', f'I{end:03d}'], 'pipe_window_size': width,
                                   'signature': dict(candidate), 'score': score(signature, candidate)})
        candidates.sort(key=lambda item: (-item['score'], abs(item['pipe_window_size'] - base), item['idf_range']))
        result_pages.append({'page': page, 'dxf_frame_signature': dict(signature),
                             'observed_frame_incident_pipe_count': base,
                             'candidates': candidates[:args.top]})
    result = {'algorithm': 'DXF_PAGE_IDF_COMPONENT_FRAME_WINDOW_V1', 'line_key': graph['line_key'],
              'policy': 'component-first candidate ranking; no CONT input and no I###→handle conclusion',
              'pages': result_pages}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': graph['line_key'], 'page_count': len(result_pages)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
