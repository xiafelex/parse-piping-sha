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
import sys
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
    # The maintained semantic adapter has sibling helper modules.  Add only
    # its own directory so this exporter remains runnable from the skill dir.
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location('dxf_semantic_adapter', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def centre(item):
    points = []
    for key in ('outline', 'anchor', 'welds', 'subpaths', 'strokes'):
        value = item.get(key)
        if not value:
            continue
        if key in ('welds', 'subpaths'):
            points.extend(point for path in value for point in path)
        elif key == 'strokes':
            points.extend(point for segment in value for point in segment)
        else:
            points.extend(value if key == 'outline' else [value])
    if not points:
        return None
    return [round(sum(p[0] for p in points) / len(points), 6),
            round(sum(p[1] for p in points) / len(points), 6)]


def source_segment(entity):
    """Return the first/last source-vector vertices for a classified handle.

    This does not decide what a pipe *is*: its caller supplies only handles
    already classified by the element-recognition adapter.  It restores the
    vector endpoints that an ``arrow_pipe`` group needs for topology: the
    adapter intentionally has no physical endpoint at the arrow glyph itself.
    """
    kind = entity.dxftype()
    if kind == 'LINE':
        return [[entity.dxf.start.x, entity.dxf.start.y], [entity.dxf.end.x, entity.dxf.end.y]]
    if kind == 'LWPOLYLINE':
        points = list(entity.get_points('xy'))
    elif kind == 'POLYLINE':
        points = [(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in entity.vertices]
    else:
        return None
    if len(points) < 2:
        return None
    return [[float(points[0][0]), float(points[0][1])], [float(points[-1][0]), float(points[-1][1])]]


def source_segments(doc, handles):
    result = []
    for handle in handles:
        entity = doc.entitydb.get(handle)
        if entity is None:
            continue
        segment = source_segment(entity)
        if segment:
            result.append({'handle': handle, 'endpoints': segment})
    return result


def export_semantics(doc, adapter, dxf: Path):
    """Serialize one already-classified page; reusable by the batch runner."""
    _paths, items = adapter.make_items(doc)
    components = []
    pipes = []
    for item in items:
        kind = item['kind']
        if kind in PIPE_KINDS:
            handles = list(item.get('handles', ()))
            pipes.append({
                'kind': kind, 'handles': handles,
                'endpoints': item.get('endpoints', []),
                'endpoint_annotations': item.get('endpoint_annotations', []),
                # Keep every individual source segment.  A multi-handle
                # arrow group can later derive its two outer endpoints
                # without pretending the arrow glyph is an IDF component.
                'source_vector_segments': source_segments(doc, handles),
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
                'subpaths': item.get('subpaths', []),
                'strokes': item.get('strokes', []),
                'anchor': item.get('anchor'),
            })
    result = {
        'dxf': dxf.name,
        'pipe_count': len(pipes),
        'component_counts': dict(Counter(x['kind'] for x in components)),
        'components': components,
        'pipes': pipes,
    }
    return result


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
    result = export_semantics(doc, adapter, args.dxf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ('dxf', 'pipe_count', 'component_counts')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
