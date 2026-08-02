#!/usr/bin/env python3
"""Export a compact DXF semantic-topology snapshot from a classifier adapter.

The matcher deliberately does not classify DXF primitives itself.  The adapter
must expose ``make_items(doc)`` from the separate DXF-element skill; this
script serialises only its already-classified graph evidence.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from pathlib import Path

import ezdxf


PIPE_KINDS = {
    'arrow_pipe', 'support_pipe', 'support_weld_pipe', 'weld_pipe',
    'support_empty_pipe', 'weld_empty_pipe', 'unresolved_pipe',
}
COMPONENT_KINDS = {
    'elbow', 'tee', 'branch', 'flange', 'flange_flat', 'flange_longneck',
    'valve', 'reducer', 'weld', 'support',
}


def load_adapter(path: Path):
    spec = importlib.util.spec_from_file_location('dxf_semantic_adapter', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def centre(item):
    points = []
    for key in ('outline', 'anchor'):
        value = item.get(key)
        if not value:
            continue
        points.extend(value if key == 'outline' else [value])
    if not points:
        return None
    return [round(sum(p[0] for p in points) / len(points), 6),
            round(sum(p[1] for p in points) / len(points), 6)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dxf', type=Path)
    ap.add_argument('--adapter', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    adapter = load_adapter(args.adapter)
    # Existing adapters use SOURCE to qualify their regression evidence.
    if hasattr(adapter, 'SOURCE'):
        adapter.SOURCE = args.dxf
    doc = ezdxf.readfile(args.dxf)
    _paths, items = adapter.make_items(doc)
    components = []
    pipes = []
    for item in items:
        kind = item['kind']
        if kind in PIPE_KINDS:
            pipes.append({
                'kind': kind, 'handles': list(item.get('handles', ())),
                'endpoints': item.get('endpoints', []),
                'endpoint_annotations': item.get('endpoint_annotations', []),
                'title': item.get('title', ''),
            })
        elif kind in COMPONENT_KINDS:
            components.append({
                'kind': kind, 'handles': list(item.get('handles', ())),
                'centre': centre(item), 'title': item.get('title', ''),
                # Preserve source geometry for the matching layer.  The
                # element classifier decided the kind; matching only asks
                # which typed pipe endpoints touch that confirmed body.
                'outline': item.get('outline', []),
                'welds': item.get('welds', []),
                'anchor': item.get('anchor'),
            })
    result = {
        'dxf': args.dxf.name,
        'pipe_count': len(pipes),
        'component_counts': dict(Counter(x['kind'] for x in components)),
        'components': components,
        'pipes': pipes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ('dxf', 'pipe_count', 'component_counts')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
