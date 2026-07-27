#!/usr/bin/env python3
"""Independently verify SHA weld-diamond writeback against source geometry.

The checker reads the original and annotated SHA files.  It does not trust the
injection loop: it re-parses the appended line/text records, compares each
leader start with the original Sheet ellipse anchor, checks diamond closure and
label placement, then records the nearest original pipe/vector distance.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path

from analyze_iso_split import read_sha_streams
from inject_sha_weld_callouts import (
    collect_connection_points,
    DIRECT_TEXT_ANCHOR_STYLES,
    point_segment_distance,
    sheet_streams,
    source_obstacle_boxes,
    nearest_segment_guide,
)
from sha_to_svg_prototype import (
    SHEET_UNIT,
    composite_segments,
    line_segments,
    merged_style_fallbacks,
    template_line_segments,
)


LINE_SIZE = struct.calcsize("<I7H4d")
TEXT_SIZE = 66
CALLOUT_SIZE = LINE_SIZE * 6 + TEXT_SIZE


def read_line(data: bytes, offset: int) -> tuple[float, float, float, float]:
    return tuple(value * SHEET_UNIT for value in struct.unpack_from("<4d", data, offset + 18))


def close(a: tuple[float, float], b: tuple[float, float], tolerance: float = 0.02) -> bool:
    return math.dist(a, b) <= tolerance


def inside_diamond(x: float, y: float, center_x: float, center_y: float, radius: float, margin: float = 0.0) -> bool:
    return abs(x - center_x) + abs(y - center_y) <= radius - margin


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_sha", type=Path)
    parser.add_argument("annotated_sha", type=Path)
    parser.add_argument("weld_map", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--on-geometry-tolerance", type=float, default=25.0)
    args = parser.parse_args()

    source_streams = read_sha_streams(args.source_sha)
    annotated_streams = read_sha_streams(args.annotated_sha)
    targets = sheet_streams(source_streams, None)
    point_by_key = {
        (point.page, point.graphic_ref, point.uci): point
        for points in collect_connection_points(source_streams, targets, 80.0).values()
        for point in points
    }
    rows = json.loads(args.weld_map.read_text())["welds"]
    wanted = {
        (int(row["page"]), int(row["graphic_ref"]), str(row["uci"])): row
        for row in rows
        if row.get("page") not in (None, 999999) and row.get("graphic_ref") is not None
    }

    results: list[dict[str, object]] = []
    for target in targets:
        source = target.data
        annotated = annotated_streams[target.stream_name]
        start = len(source) + (len(source) % 2)
        payload = annotated[start:]
        if len(payload) % CALLOUT_SIZE:
            raise ValueError(f"{target.stream_name}: appended payload has unexpected length {len(payload)}")
        segments = line_segments(source) + template_line_segments(source) + composite_segments(source)
        source_boxes = source_obstacle_boxes(source, source_streams.get("PSMcluster0", b""), gap=80.0)
        # Synthetic labels reuse a text style selected from any physical ISO
        # page in the same SHA.  Resolve its metrics globally as well, rather
        # than assuming that style has a local text sample on this Sheet.
        style_models = merged_style_fallbacks(source_streams, source_streams.get("PSMcluster0", b""))
        for index in range(0, len(payload), CALLOUT_SIZE):
            lines = [read_line(payload, index + line_index * LINE_SIZE) for line_index in range(6)]
            leader_start = lines[0]
            leader_finish = lines[1]
            diamond = lines[2:]
            vertices = [line[:2] for line in diamond]
            center_x = sum(point[0] for point in vertices) / 4
            center_y = sum(point[1] for point in vertices) / 4
            radius = sum(abs(x - center_x) + abs(y - center_y) for x, y in vertices) / 4
            text_offset = index + LINE_SIZE * 6
            label = payload[text_offset + 24 : text_offset + 34].decode("utf-16le").rstrip()
            style_ref = struct.unpack_from("<I", payload, text_offset + 8)[0]
            text_x, text_y = struct.unpack_from("<dd", payload, text_offset + 34)
            offset_x, offset_y, font_height, char_width = style_models.get(
                style_ref,
                # The writer only emits a proven template style. This narrow
                # fallback keeps the verifier diagnostic if a future SHA has
                # no measurable PSM sample for that shared style.
                (0.0, 0.0, 80.0, 50.0),
            )
            label_width = max(font_height * 0.7, char_width * len(label))
            label_height = font_height
            label_left = text_x * SHEET_UNIT
            # SVG alphabetic baseline maps to a source-space glyph box that
            # extends mostly upward with a small descender below the baseline.
            label_bottom = text_y * SHEET_UNIT - label_height * 0.20
            if style_ref not in DIRECT_TEXT_ANCHOR_STYLES:
                label_left += offset_x
                label_bottom = text_y * SHEET_UNIT + offset_y
            label_inside = all(
                inside_diamond(x, y, center_x, center_y, radius, margin=4.0)
                for x, y in (
                    (label_left, label_bottom),
                    (label_left + label_width, label_bottom),
                    (label_left, label_bottom + label_height),
                    (label_left + label_width, label_bottom + label_height),
                )
            )
            matching = next(
                (
                    (key, point)
                    for key, point in point_by_key.items()
                    if key in wanted and close((leader_start[0], leader_start[1]), (point.center_x, point.center_y))
                ),
                None,
            )
            nearest = min(
                point_segment_distance(leader_start[0], leader_start[1], x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
                for x1, y1, x2, y2, _, _ in segments
            )
            diamond_closed = all(close(diamond[i][2:], diamond[(i + 1) % 4][:2]) for i in range(4))
            leader_ends_at_diamond_edge = close(leader_start[2:], leader_finish[:2]) and inside_diamond(leader_finish[2], leader_finish[3], center_x, center_y, radius, margin=-0.02) and abs(
                abs(leader_finish[2] - center_x) + abs(leader_finish[3] - center_y) - radius
            ) <= 0.02
            diamond_clear_of_geometry = min(
                point_segment_distance(center_x, center_y, x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
                for x1, y1, x2, y2, _, _ in segments
            ) > radius * math.sqrt(2) + 60.0
            diamond_clear_of_text = all(
                center_x + radius + 60 < left
                or center_x - radius - 60 > right
                or center_y + radius + 60 < bottom
                or center_y - radius - 60 > top
                for left, bottom, right, top in source_boxes
            )
            key, point = matching if matching else (None, None)
            perpendicular = False
            if point is not None:
                tangent, _, _ = nearest_segment_guide(point, segments)
                stub_x, stub_y = leader_start[2] - leader_start[0], leader_start[3] - leader_start[1]
                stub_length = math.hypot(stub_x, stub_y)
                perpendicular = stub_length > 1e-6 and abs((stub_x * tangent[0] + stub_y * tangent[1]) / stub_length) < 0.02
            results.append(
                {
                    "page": target.page,
                    "sheet_stream": target.stream_name,
                    "label": label,
                    "uci": key[2] if key else None,
                    "graphic_ref": key[1] if key else None,
                    "leader_starts_at_source_dot": matching is not None,
                    "diamond_closed": diamond_closed,
                    "leader_ends_at_diamond_edge": leader_ends_at_diamond_edge,
                    "leader_initial_segment_perpendicular": perpendicular,
                    "diamond_clear_of_source_geometry": diamond_clear_of_geometry,
                    "diamond_clear_of_source_text": diamond_clear_of_text,
                    "label_inside_diamond": label_inside,
                    "leader_start": [round(leader_start[0], 3), round(leader_start[1], 3)],
                    "diamond_center": [round(center_x, 3), round(center_y, 3)],
                    "distance_to_original_geometry": round(nearest, 3),
                    "on_geometry": nearest <= args.on_geometry_tolerance,
                }
            )

    results.sort(key=lambda item: (int(item["page"]), str(item["label"])))
    verified = sum(
        bool(item["leader_starts_at_source_dot"])
        and bool(item["diamond_closed"])
        and bool(item["leader_ends_at_diamond_edge"])
        and bool(item["leader_initial_segment_perpendicular"])
        and bool(item["diamond_clear_of_source_geometry"])
        and bool(item["diamond_clear_of_source_text"])
        and bool(item["label_inside_diamond"])
        for item in results
    )
    on_geometry = sum(bool(item["on_geometry"]) for item in results)
    payload = {
        "source_sha": str(args.source_sha),
        "annotated_sha": str(args.annotated_sha),
        "callout_count": len(results),
        "leader_and_diamond_structurally_verified": verified,
        "on_original_geometry": on_geometry,
        "geometry_tolerance": args.on_geometry_tolerance,
        "callouts": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: payload[key] for key in payload if key != "callouts"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
