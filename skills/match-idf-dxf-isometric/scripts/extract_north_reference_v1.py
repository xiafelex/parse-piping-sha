#!/usr/bin/env python3
"""Audit the source-vector north reference beside the ISO-sheet `N` label.

This produces a page-orientation *candidate*, not a pipe-matching seed.  The
candidate must be visually confirmed before it can corroborate a calibrated
IDF-to-DXF axis transform.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import ezdxf


def text_value(entity):
    return entity.dxf.text if entity.dxftype() == "TEXT" else entity.text


def insertion(entity):
    point = entity.dxf.insert
    return (point.x, point.y)


def closed_points(entity):
    if entity.dxftype() == "POLYLINE":
        points = [(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in entity.vertices]
    elif entity.dxftype() == "LWPOLYLINE":
        points = [(vertex[0], vertex[1]) for vertex in entity.get_points("xy")]
    else:
        return []
    if len(points) >= 2 and math.dist(points[0], points[-1]) < 1e-6:
        points.pop()
    return points


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dxf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    drawing = ezdxf.readfile(args.dxf)
    space = drawing.modelspace()
    labels = [insertion(entity) for entity in space
              if entity.dxftype() in {"TEXT", "MTEXT"} and text_value(entity).strip() == "N"]
    candidates = []
    for entity in space:
        points = closed_points(entity)
        if not 6 <= len(points) <= 12:
            continue
        distance = min((math.dist(point, label) for point in points for label in labels), default=float("inf"))
        if distance > 25:
            continue
        candidates.append((distance, entity.dxf.handle, points))
    candidates.sort(key=lambda row: (row[0], row[1]))
    result = {"algorithm": "NORTH_REFERENCE_SOURCE_VECTOR_V1", "north_label_count": len(labels),
              "north_labels": labels, "status": "unresolved", "candidates": []}
    # Sheet borders can also be closed polylines near `N`; accept the closest
    # symbol only when it is materially separated from the runner-up.
    unique_closest = candidates and (len(candidates) == 1 or candidates[1][0] - candidates[0][0] >= 4.0)
    if unique_closest and labels:
        _distance, handle, points = candidates[0]
        tip = min(points, key=lambda point: min(math.dist(point, label) for label in labels))
        centre = (sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points))
        result.update({"status": "candidate_requires_visual_confirmation", "north_symbol_handle": handle,
                       "tip_candidate": tip, "centre": centre,
                       "vector_candidate": [tip[0] - centre[0], tip[1] - centre[1]]})
    result["candidates"] = [{"handle": handle, "vertex_count": len(points), "nearest_n_distance": round(distance, 4)}
                            for distance, handle, points in candidates]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"status": result["status"], "handle": result.get("north_symbol_handle")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
