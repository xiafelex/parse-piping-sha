#!/usr/bin/env python3
"""Build an auditable IDF line ↔ DXF page inventory from source filenames.

This is page-membership inventory only.  It deliberately does not claim that
same-name pages already have a verified `100` correspondence.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


def idf_line_key(path: Path) -> str | None:
    parts = path.stem.upper().split('-')
    return ''.join(parts[1:3]) if len(parts) >= 3 else None


def dxf_line_key(path: Path) -> str | None:
    # GHYX-E-941DR200008S9412C-E04-PD0704-001-R00.dxf → DR200008
    match = re.search(r'941([A-Z0-9]+)S9412C-', path.name.upper())
    return match.group(1) if match else None


def dxf_page_number(path: Path) -> int | None:
    match = re.search(r'PD0704-(\d+)-', path.name.upper())
    return int(match.group(1)) if match else None


def continuation_in_single_page(path: Path) -> bool | None:
    """Only inspect singleton candidates; multi-page status is already clear."""
    try:
        import ezdxf
        doc = ezdxf.readfile(path)
        for kind in ('TEXT', 'MTEXT'):
            for entity in doc.modelspace().query(kind):
                value = entity.dxf.text if kind == 'TEXT' else entity.text
                if 'CONT. ON' in value.upper() or 'CONT ON' in value.upper():
                    return True
        return False
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('idf_root', type=Path); ap.add_argument('dxf_root', type=Path)
    ap.add_argument('--output-dir', type=Path, required=True)
    args = ap.parse_args()
    idfs = sorted(args.idf_root.glob('*.idf'))
    dxfs = sorted(args.dxf_root.glob('*.dxf'))
    by_idf = defaultdict(list); by_dxf = defaultdict(list)
    for path in idfs:
        key = idf_line_key(path)
        if key: by_idf[key].append(path)
    for path in dxfs:
        key = dxf_line_key(path)
        if key: by_dxf[key].append(path)
    rows = []
    for key in sorted(by_idf):
        pages = sorted(by_dxf.get(key, []), key=lambda path: (dxf_page_number(path) is None, dxf_page_number(path), path.name))
        continuation = continuation_in_single_page(pages[0]) if len(pages) == 1 else None
        status = 'no_dxf_candidate' if not pages else 'single_closed_candidate' if continuation is False else 'single_continuation_not_eligible' if continuation else 'single_unreadable' if len(pages) == 1 else 'multi_page_candidate'
        rows.append({
            'line_key': key,
            'idf_files': [path.name for path in by_idf[key]],
            'idf_file_count': len(by_idf[key]),
            'dxf_pages': [{'page': dxf_page_number(path), 'file': path.name} for path in pages],
            'dxf_page_count': len(pages),
            'continuation_in_single_page': continuation,
            'status': status,
            'matching_basis': 'source filename line key only; not a 100 correspondence result',
        })
    dxf_without_idf = [path.name for key, paths in by_dxf.items() if key not in by_idf for path in paths]
    summary = {
        'idf_file_count': len(idfs), 'unique_idf_line_count': len(rows), 'dxf_file_count': len(dxfs),
        'status_counts': {status: sum(row['status'] == status for row in rows) for status in sorted({row['status'] for row in rows})},
        'dxf_without_idf_line_key_count': len(dxf_without_idf),
        'lines': rows, 'dxf_without_idf_line_key': sorted(dxf_without_idf),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'idf-dxf-page-inventory.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    with (args.output_dir / 'idf-dxf-page-inventory.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['line_key','idf_file_count','dxf_page_count','dxf_pages','continuation_in_single_page','status'])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                'line_key': row['line_key'],
                'idf_file_count': row['idf_file_count'],
                'dxf_page_count': row['dxf_page_count'],
                'dxf_pages': '; '.join(f"{x['page']:03d}:{x['file']}" for x in row['dxf_pages']),
                'continuation_in_single_page': row['continuation_in_single_page'],
                'status': row['status'],
            })
    print(json.dumps({key: summary[key] for key in ('idf_file_count','unique_idf_line_count','dxf_file_count','status_counts','dxf_without_idf_line_key_count')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
