#!/usr/bin/env python3
"""Select page-to-IDF frame windows using global topology coverage.

This deliberately resolves *page subgraph* candidates only.  It does not
assign any I### to a DXF handle.  Candidate windows come from component-frame
signatures (junctions, reducers, elbows) and the selector uses no CONT text.
For closed, count-equal regression lines it prefers a non-overlapping complete
cover of the IDF 100 index universe.  On other lines it reports the best
partial hypothesis without pretending that it is a correspondence.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path


def index(value: str) -> int:
    return int(value[1:])


def range_set(candidate: dict) -> set[int]:
    left, right = map(index, candidate['idf_range'])
    return set(range(left, right + 1))


def evaluate(selection: list[tuple[dict, dict]], universe: set[int]) -> dict:
    covered: set[int] = set()
    duplicated: set[int] = set()
    for _, candidate in selection:
        nodes = range_set(candidate)
        duplicated |= covered & nodes
        covered |= nodes
    missing = universe - covered
    score = sum(candidate['score'] for _, candidate in selection)
    # Structural score is decisive only after a valid global range cover.  The
    # large penalties retain ambiguity rather than letting overlapping local
    # signatures masquerade as a page partition.
    objective = score - 1000 * len(missing) - 1000 * len(duplicated)
    return {
        'structure_score': score,
        'missing_indices': sorted(missing),
        'duplicate_indices': sorted(duplicated),
        'objective': objective,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('window_candidates', type=Path)
    parser.add_argument('component_frame_graph', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--per-page', type=int, default=5,
                        help='number of already ranked local candidates to combine')
    args = parser.parse_args()

    candidates = json.loads(args.window_candidates.read_text())
    graph = json.loads(args.component_frame_graph.read_text())
    pipe_count = len(graph['idf']['pipe_frame_incidence'])
    universe = set(range(1, pipe_count + 1))
    pages = candidates['pages']
    candidate_lists = [page['candidates'][:args.per_page] for page in pages]
    if not pages or any(not choices for choices in candidate_lists):
        raise SystemExit('every DXF page must have at least one local frame-window candidate')

    ranked = []
    for picks in itertools.product(*candidate_lists):
        selection = list(zip(pages, picks))
        audit = evaluate(selection, universe)
        audit['page_ranges'] = [
            {
                'page': page['page'],
                'idf_range': candidate['idf_range'],
                'pipe_window_size': candidate['pipe_window_size'],
                'local_frame_signature': page['dxf_frame_signature'],
                'local_structure_score': candidate['score'],
            }
            for page, candidate in selection
        ]
        ranked.append(audit)
    ranked.sort(key=lambda item: (-item['objective'], -item['structure_score'],
                                  len(item['missing_indices']), len(item['duplicate_indices'])))
    best = ranked[0]
    exact = not best['missing_indices'] and not best['duplicate_indices']
    result = {
        'algorithm': 'GLOBAL_COMPONENT_FRAME_WINDOW_COVER_V1',
        'line_key': candidates['line_key'],
        'policy': (
            'component/topology-first page-subgraph selection; no CONT input; '
            'not an I###→DXF-handle mapping'
        ),
        'idf_100_count': pipe_count,
        'status': ('topology_global_exact_cover_candidate' if exact
                   else 'topology_global_partial_cover_candidate'),
        'best': best,
        'alternatives': ranked[1:6],
        'next_step': (
            'Use unique component anchors and ordered local frame paths inside the selected '
            'page ranges; retain individual pipes unresolved until their local topology is unique.'
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': result['line_key'], 'status': result['status'],
                      'objective': best['objective']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
