#!/usr/bin/env python3
"""Attribute possible causes of multi-page IDF 100/DXF count differences.

This is deliberately an evidence ledger, not a count-correction script.  It
never subtracts a candidate merely because it is empty, support-related, or
near a continuation label.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path


def load_last(path: Path):
    result = {}
    for text in path.read_text().splitlines():
        if text.strip():
            row = json.loads(text); result[row['source']] = row
    return result


def line_and_page(source: str):
    match = re.search(r'941([A-Z0-9]+)S9412C.*PD0704-(\d+)-', source)
    if not match:
        raise ValueError(f'cannot parse source filename: {source}')
    return match.group(1), int(match.group(2))


def length(fragment):
    return math.dist(*fragment['endpoints'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('count_summary', type=Path); ap.add_argument('terminal_audit', type=Path)
    ap.add_argument('continuations', type=Path); ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    summary = json.loads(args.count_summary.read_text())
    terminals = load_last(args.terminal_audit); continuations = load_last(args.continuations)
    by_line = defaultdict(list)
    for source, row in terminals.items():
        key, page = line_and_page(source); by_line[key].append((page, source, row))
    rows = []
    for count_row in summary['rows']:
        key = count_row['line_key']; pages = {page: (source, row) for page, source, row in by_line[key]}
        high = []; medium = []
        for page, source, row in by_line[key]:
            for link in continuations.get(source, {}).get('links', []):
                if link['mode'] != 'on' or link['page'] not in pages:
                    continue
                target_source, target = pages[link['page']]
                for left in row['terminal_pipe_fragments']:
                    for right in target['terminal_pipe_fragments']:
                        if left['kind'] != right['kind']:
                            continue
                        if abs(length(left)-length(right)) > max(.1, .02*max(length(left),length(right))):
                            continue
                        evidence = {'from_page': page, 'to_page': link['page'], 'kind': left['kind'],
                                    'left_handles': left['handles'], 'right_handles': right['handles'],
                                    'left_length': round(length(left),3), 'right_length': round(length(right),3),
                                    'continuation_evidence': link['evidence']}
                        # Same source handle plus same semantic role and
                        # length is a strong vector recurrence signal.  It is
                        # still recorded, not silently deducted.
                        if left['handles'] == right['handles']:
                            high.append(evidence)
                        else:
                            medium.append(evidence)
        kinds = defaultdict(int)
        for _page, _source, page_row in by_line[key]:
            for kind, amount in page_row['pipe_kind_counts'].items(): kinds[kind] += amount
        idf_count = count_row['idf_100_count']
        rows.append({
            'line_key': key, 'idf_100_count': count_row['idf_100_count'],
            'idf_selection_status': count_row.get('idf_selection_status', 'unknown'),
            'idf_100_candidates': count_row.get('idf_100_candidates', []),
            'dxf_final_pipe_fragment_count': count_row['dxf_final_pipe_fragment_count'],
            'raw_difference': (count_row['dxf_final_pipe_fragment_count'] - idf_count
                               if idf_count is not None else None),
            'arrow_pipe_count': kinds['arrow_pipe'],
            'support_related_fragment_count': kinds['support_pipe'] + kinds['support_weld_pipe'] + kinds['support_empty_pipe'],
            'empty_terminal_fragment_count': kinds['support_empty_pipe'] + kinds['weld_empty_pipe'],
            'cross_page_high_confidence_duplicate_candidates': high,
            'cross_page_medium_confidence_terminal_candidates': medium,
            'unresolved_pipe_count': kinds['unresolved_pipe'],
            'automatic_adjustment': 0,
            'next_step': ('select the IDF candidate from topology/specification evidence before count comparison'
                          if idf_count is None else
                          'review high candidates first; then test local IDF topology before any support/component aggregation'),
        })
    result = {'algorithm': 'MULTI_PAGE_100_DIFFERENCE_ATTRIBUTION_V1',
              'policy': 'evidence ledger only; no automatic count correction', 'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_count':len(rows), 'high_candidates':sum(len(r['cross_page_high_confidence_duplicate_candidates']) for r in rows), 'medium_candidates':sum(len(r['cross_page_medium_confidence_terminal_candidates']) for r in rows)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
