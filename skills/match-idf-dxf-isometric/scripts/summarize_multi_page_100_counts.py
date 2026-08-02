#!/usr/bin/env python3
"""Compare IDF 100 counts with typed DXF pipe fragments across all pages.

The result intentionally labels multi-page totals as *not directly comparable*:
page ownership and support contraction have not yet been resolved.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def count_idf_100(path: Path) -> int:
    count = 0
    for text in path.read_text(errors='replace').splitlines():
        fields = text.split()
        if not fields or fields[0] != '100' or len(fields) < 9:
            continue
        # Ignore textual/header false positives: geometry rows carry the
        # comma-delimited tail used by the IDF source format.
        if any(',' in value for value in fields[8:]):
            count += 1
    return count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inventory', type=Path); ap.add_argument('raw_counts', type=Path)
    ap.add_argument('idf_root', type=Path); ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    inventory = json.loads(args.inventory.read_text())
    # Keep last result per source: interrupted parallel batch runs may have
    # harmless duplicate JSONL lines, while classification is deterministic.
    raw = {}
    for line in args.raw_counts.read_text().splitlines():
        if line.strip():
            row = json.loads(line); raw[row['source']] = row
    rows = []
    for line in inventory['lines']:
        if line['status'] != 'multi_page_candidate':
            continue
        page_rows = [raw.get(page['file']) for page in line['dxf_pages']]
        missing = [page['file'] for page, row in zip(line['dxf_pages'], page_rows) if row is None]
        idf_paths = [args.idf_root / name for name in line['idf_files']]
        idf_counts = [count_idf_100(path) for path in idf_paths]
        rows.append({
            'line_key': line['line_key'], 'idf_files': line['idf_files'],
            'idf_100_counts': idf_counts, 'idf_100_count': max(idf_counts, default=0),
            'dxf_page_count': line['dxf_page_count'],
            'dxf_final_pipe_fragment_count': sum(row['final_pipe_fragments'] for row in page_rows if row),
            'dxf_support_related_fragment_count': sum(row['support_contraction_eligible_fragments'] for row in page_rows if row),
            'dxf_unresolved_pipe_count': sum(row['unresolved_pipe_count'] for row in page_rows if row),
            'zero_pipe_pages': [page['file'] for page, row in zip(line['dxf_pages'], page_rows) if row and row['final_pipe_fragments'] == 0],
            'missing_count_pages': missing,
            'comparison_status': 'requires_page_partition_before_100_comparison',
            'reason': 'DXF total contains page splits and support-cut fragments; it must not be equated to IDF 100 total.',
        })
    result = {'scope': 'all multi-page IDF↔DXF filename-associated lines', 'line_count': len(rows), 'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_count': len(rows), 'missing_count_pages': sum(len(row['missing_count_pages']) for row in rows)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
