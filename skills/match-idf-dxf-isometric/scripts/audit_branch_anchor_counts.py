#!/usr/bin/env python3
"""Compare IDF branch anchors with pre-classified DXF branch/tee anchors.

This is an eligibility audit, not a correspondence matcher.  It prevents a
multi-page line with branches from being incorrectly sent to a linear matcher.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('idf_topology', type=Path)
    ap.add_argument('--page-topology', action='append', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    idf = json.loads(args.idf_topology.read_text())
    pages = [json.loads(path.read_text()) for path in args.page_topology]
    idf_junctions = [x for x in idf['branch_nodes'] if x['role'] == 'junction']
    dxf_anchors = []
    for page in pages:
        for component in page['components']:
            if component['kind'] in {'branch', 'tee'}:
                dxf_anchors.append({'source': page['dxf'], **component})
    status = ('anchor_counts_agree' if len(idf_junctions) == len(dxf_anchors)
              else 'branch_anchor_mismatch')
    result = {
        'algorithm': 'BRANCH_ANCHOR_AUDIT_V1',
        'idf': idf['idf'],
        'idf_100_count': idf['idf_100_count'],
        'idf_junction_count': len(idf_junctions),
        'idf_junctions': idf_junctions,
        'dxf_branch_or_tee_count': len(dxf_anchors),
        'dxf_branch_or_tee_anchors': dxf_anchors,
        'status': status,
        'next_step': ('partition from each rare anchor before matching 100 legs'
                      if status == 'anchor_counts_agree' else
                      'do not force a pipe mapping; review missing/extra DXF branch or IDF anchor'),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ('idf', 'idf_junction_count', 'dxf_branch_or_tee_count', 'status')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
