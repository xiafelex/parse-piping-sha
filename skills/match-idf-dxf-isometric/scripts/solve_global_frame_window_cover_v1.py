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
    parser.add_argument('--beam-width', type=int, default=4000,
                        help='bounded state count for high-page-count drawings')
    args = parser.parse_args()

    candidates = json.loads(args.window_candidates.read_text())
    graph = json.loads(args.component_frame_graph.read_text())
    pipe_count = len(graph['idf']['pipe_frame_incidence'])
    universe = set(range(1, pipe_count + 1))
    pages = candidates['pages']
    candidate_lists = [page['candidates'][:args.per_page] for page in pages]
    if not pages or any(not choices for choices in candidate_lists):
        raise SystemExit('every DXF page must have at least one local frame-window candidate')

    # Exhaustive cartesian products become impractical for many-page ISO sets.
    # This beam retains the same final objective but limits intermediate states
    # by structural score and duplicate coverage.  Two-page regression lines
    # remain exhaustive because their product is below the beam limit.
    states = [([], set(), set(), 0)]  # selection, covered, duplicate, score
    for page, choices in zip(pages, candidate_lists):
        expanded = []
        for selection, covered, duplicate, structure_score in states:
            for candidate in choices:
                nodes = range_set(candidate)
                expanded.append((selection + [(page, candidate)], covered | nodes,
                                 duplicate | (covered & nodes), structure_score + candidate['score']))
        expanded.sort(key=lambda item: (-item[3] + 1000 * len(item[2]), -len(item[1])))
        states = expanded[:args.beam_width]
    ranked = []
    for selection, _, _, _ in states:
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
    exact_ranked = [item for item in ranked if not item['missing_indices'] and not item['duplicate_indices']]
    next_exact_score = exact_ranked[1]['structure_score'] if len(exact_ranked) > 1 else None
    exact_margin = (best['structure_score'] - next_exact_score
                    if next_exact_score is not None else None)
    # A range may close purely because pipe counts happen to add up.  It is
    # admissible for individual propagation only when its component score is
    # demonstrably stronger than every other exact cover.
    if not exact:
        status = 'topology_global_partial_cover_candidate'
    elif best['structure_score'] < 50:
        status = 'topology_global_weak_exact_cover_candidate'
    elif exact_margin is not None and exact_margin < 10:
        status = 'topology_global_ambiguous_exact_cover_candidate'
    else:
        status = 'topology_global_unique_exact_cover_candidate'
    result = {
        'algorithm': 'GLOBAL_COMPONENT_FRAME_WINDOW_COVER_V1',
        'line_key': candidates['line_key'],
        'policy': (
            'component/topology-first page-subgraph selection; no CONT input; '
            'not an I###→DXF-handle mapping'
        ),
        'idf_100_count': pipe_count,
        'status': status,
        'best': best,
        'alternatives': ranked[1:6],
        'exact_cover_competition': {'exact_candidate_count_in_retained_states': len(exact_ranked),
                                    'next_exact_structure_score': next_exact_score,
                                    'structure_margin_to_next_exact': exact_margin},
        'next_step': (
            'Use unique component anchors and ordered local frame paths inside the selected '
            'page ranges; retain individual pipes unresolved until their local topology is unique.'
        ),
        'search': {'method': 'bounded_component_frame_beam', 'beam_width': args.beam_width,
                   'retained_final_states': len(states)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': result['line_key'], 'status': result['status'],
                      'objective': best['objective']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
