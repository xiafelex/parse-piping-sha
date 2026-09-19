#!/usr/bin/env python3
"""Independently verify typed SHA weld-callout writeback against source geometry.

The checker reads the original and annotated SHA files.  It does not trust the
injection loop: it re-parses the appended line/text records, compares each
single straight leader with the original Sheet ellipse anchor, checks marker
closure and label placement, then records the nearest original geometry.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
from pathlib import Path

from analyze_iso_split import read_sha_streams
from inject_sha_weld_callouts import (
    arc_obstacle_segments,
    boxes_overlap,
    collect_connection_points,
    DIRECT_TEXT_ANCHOR_STYLES,
    infer_layout_region,
    leader_clear_of_source,
    point_segment_distance,
    segment_intersects_box,
    segments_intersect,
    sheet_streams,
    source_obstacle_boxes,
    nearest_segment_guide,
    MARKER_SEGMENT_COUNT,
    SOURCE_MARKER_CLEARANCE,
    PointTarget,
)
from sha_to_svg_prototype import (
    SHEET_UNIT,
    declared_sheet_viewbox,
    line_segments,
    merged_style_fallbacks,
    sheet_viewbox,
    template_line_segments,
)


LINE_SIZE = struct.calcsize("<I7H4d")
TEXT_SIZE = 66
CALLOUT_LINE_COUNT = 1 + MARKER_SEGMENT_COUNT
CALLOUT_SIZE = LINE_SIZE * CALLOUT_LINE_COUNT + TEXT_SIZE
WELD_NUMBER_PARTS = re.compile(r"^(.*?)(\d+)$")


def distribution_number_sequence_is_valid(values: list[str]) -> bool:
    """Return whether labels share one prefix and have consecutive suffixes."""

    parsed = [WELD_NUMBER_PARTS.fullmatch(value.strip()) for value in values]
    if not values or any(match is None for match in parsed):
        return False
    matches = [match for match in parsed if match is not None]
    prefixes = {match.group(1).casefold() for match in matches}
    numbers = [int(match.group(2)) for match in matches]
    return len(prefixes) == 1 and numbers == list(range(numbers[0], numbers[0] + len(numbers)))


def read_line(data: bytes, offset: int) -> tuple[float, float, float, float]:
    return tuple(value * SHEET_UNIT for value in struct.unpack_from("<4d", data, offset + 18))


def close(a: tuple[float, float], b: tuple[float, float], tolerance: float = 0.02) -> bool:
    return math.dist(a, b) <= tolerance


def inside_diamond(x: float, y: float, center_x: float, center_y: float, radius: float, margin: float = 0.0) -> bool:
    return abs(x - center_x) + abs(y - center_y) <= radius - margin


def inside_circle(x: float, y: float, center_x: float, center_y: float, radius: float, margin: float = 0.0) -> bool:
    return math.hypot(x - center_x, y - center_y) <= radius - margin


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
    template_data = source_streams.get("Sheet221", b"")
    shared_viewbox = declared_sheet_viewbox(source_streams.get("Sheet6", b""))
    point_by_key = {
        (point.page, point.graphic_ref, point.uci): point
        for points in collect_connection_points(source_streams, targets, 80.0).values()
        for point in points
    }
    rows = json.loads(args.weld_map.read_text(encoding="utf-8"))["welds"]
    for row in rows:
        if (
            row.get("page") not in (None, 999999)
            and row.get("graphic_ref") is not None
            and row.get("sha_x") not in (None, "")
            and row.get("sha_y") not in (None, "")
            and row.get("uci")
        ):
            point = PointTarget(
                graphic_ref=int(row["graphic_ref"]),
                uci=str(row["uci"]),
                center_x=float(row["sha_x"]),
                center_y=float(row["sha_y"]),
                page=int(row["page"]),
            )
            point_by_key[(point.page, point.graphic_ref, point.uci)] = point
    wanted = {
        (int(row["page"]), int(row["graphic_ref"]), str(row["uci"])): row
        for row in rows
        if row.get("page") not in (None, 999999) and row.get("graphic_ref") is not None
    }
    wanted_by_label = {
        str(row["weld_number"]): row
        for row in rows
        if (
            row.get("weld_number") not in (None, "")
            and row.get("page") not in (None, 999999)
            and row.get("graphic_ref") is not None
            and row.get("uci")
        )
    }

    results: list[dict[str, object]] = []
    for target in targets:
        source = target.data
        annotated = annotated_streams[target.stream_name]
        start = len(source) + (len(source) % 2)
        payload = annotated[start:]
        if len(payload) % CALLOUT_SIZE:
            raise ValueError(f"{target.stream_name}: appended payload has unexpected length {len(payload)}")
        drawing_segments = line_segments(source) + template_line_segments(source)
        segments = (
            drawing_segments
            + arc_obstacle_segments(source, drawing_segments)
            + template_line_segments(template_data)
        )
        psm = source_streams.get("PSMcluster0", b"")
        # The marker comparison below already adds the required 60-unit
        # clearance. Keep these as physical text extents to avoid doubling it.
        source_boxes = source_obstacle_boxes(source, psm, gap=0.0)
        source_boxes.extend(source_obstacle_boxes(template_data, psm, gap=0.0))
        leader_boxes = source_obstacle_boxes(source, psm, gap=5.0)
        leader_boxes.extend(source_obstacle_boxes(template_data, psm, gap=5.0))
        region = infer_layout_region(template_data, sheet_viewbox(source, shared_viewbox))
        # Synthetic labels reuse a text style selected from any physical ISO
        # page in the same SHA.  Resolve its metrics globally as well, rather
        # than assuming that style has a local text sample on this Sheet.
        style_models = merged_style_fallbacks(source_streams, source_streams.get("PSMcluster0", b""))
        page_results: list[dict[str, object]] = []
        for index in range(0, len(payload), CALLOUT_SIZE):
            lines = [
                read_line(payload, index + line_index * LINE_SIZE)
                for line_index in range(CALLOUT_LINE_COUNT)
            ]
            leader = lines[0]
            marker = lines[1:]
            vertices = [line[:2] for line in marker]
            center_x = sum(point[0] for point in vertices) / len(vertices)
            center_y = sum(point[1] for point in vertices) / len(vertices)
            text_offset = index + LINE_SIZE * CALLOUT_LINE_COUNT
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
            # Two different physical welds may deliberately share one drawn
            # root (for example a shop/field pair at a component boundary).
            # Coordinate-only lookup then assigns the second marker the first
            # weld's S/F type.  The visible weld number is unique within one
            # PCF, so resolve that identity first and use the source point only
            # to independently check its leader root.
            expected = wanted_by_label.get(label, {})
            key = (
                (
                    int(expected["page"]),
                    int(expected["graphic_ref"]),
                    str(expected["uci"]),
                )
                if expected
                else None
            )
            point = point_by_key.get(key) if key is not None else None
            if point is None:
                matching = next(
                    (
                        (candidate_key, candidate_point)
                        for candidate_key, candidate_point in point_by_key.items()
                        if candidate_key in wanted
                        and close(
                            (leader[0], leader[1]),
                            (candidate_point.center_x, candidate_point.center_y),
                        )
                    ),
                    None,
                )
                key, point = matching if matching else (None, None)
                expected = wanted[key] if key else {}
            expected_type = str(expected.get("weld_type", "")).upper()
            pipe_visible_segment = expected.get("pipe_visible_segment")
            virtual_pipe_point = expected.get("point_evidence") in {
                "pcf-pipe-interpolation",
                "pcf-topology-pipe-end",
                "pcf-topology-component-end",
            }
            distance_to_pipe_centerline: float | None = None
            on_expected_pipe_centerline = True
            if (
                virtual_pipe_point
                and isinstance(pipe_visible_segment, list)
                and len(pipe_visible_segment) == 2
                and all(
                    isinstance(endpoint, list) and len(endpoint) == 2
                    for endpoint in pipe_visible_segment
                )
            ):
                distance_to_pipe_centerline = point_segment_distance(
                    leader[0],
                    leader[1],
                    float(pipe_visible_segment[0][0]),
                    float(pipe_visible_segment[0][1]),
                    float(pipe_visible_segment[1][0]),
                    float(pipe_visible_segment[1][1]),
                )
                on_expected_pipe_centerline = (
                    distance_to_pipe_centerline <= args.on_geometry_tolerance
                )
            elif virtual_pipe_point:
                on_expected_pipe_centerline = False
            marker_shape = "circle"
            radius = sum(math.hypot(x - center_x, y - center_y) for x, y in vertices) / len(vertices)
            boundary_deviations = [
                abs(math.hypot(x - center_x, y - center_y) - radius)
                for x, y in vertices
            ]
            boundary_matches = max(boundary_deviations, default=math.inf) <= 0.05
            inside_marker = inside_circle
            edge_distance = math.hypot(
                leader[2] - center_x,
                leader[3] - center_y,
            )
            label_inside = all(
                inside_marker(x, y, center_x, center_y, radius, margin=4.0)
                for x, y in (
                    (label_left, label_bottom),
                    (label_left + label_width, label_bottom),
                    (label_left, label_bottom + label_height),
                    (label_left + label_width, label_bottom + label_height),
                )
            )
            nearest = min(
                point_segment_distance(leader[0], leader[1], x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
                for x1, y1, x2, y2, _, _ in segments
            )
            marker_closed = all(
                close(marker[i][2:], marker[(i + 1) % len(marker)][:2])
                for i in range(len(marker))
            )
            leader_ends_at_marker_edge = abs(edge_distance - radius) <= 0.05
            leader_is_single_straight_line = math.hypot(
                leader[2] - leader[0], leader[3] - leader[1]
            ) > 1e-6
            marker_clear_of_geometry = min(
                point_segment_distance(center_x, center_y, x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
                for x1, y1, x2, y2, _, _ in segments
            ) > radius + SOURCE_MARKER_CLEARANCE
            marker_clear_of_text = all(
                center_x + radius + SOURCE_MARKER_CLEARANCE < left
                or center_x - radius - SOURCE_MARKER_CLEARANCE > right
                or center_y + radius + SOURCE_MARKER_CLEARANCE < bottom
                or center_y - radius - SOURCE_MARKER_CLEARANCE > top
                for left, bottom, right, top in source_boxes
            )
            physical_box = (
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            )
            clearance_box = (
                center_x - radius - 60.0,
                center_y - radius - 60.0,
                center_x + radius + 60.0,
                center_y + radius + 60.0,
            )
            outer_left, outer_bottom, outer_right, outer_top = region.outer
            draw_left, draw_bottom, draw_right, draw_top = region.drawing
            marker_inside_outer_frame = (
                physical_box[0] >= outer_left
                and physical_box[1] >= outer_bottom
                and physical_box[2] <= outer_right
                and physical_box[3] <= outer_top
            )
            marker_inside_drawing_region = (
                physical_box[0] >= draw_left
                and physical_box[1] >= draw_bottom
                and physical_box[2] <= draw_right
                and physical_box[3] <= draw_top
            )
            marker_has_drawing_clearance = (
                clearance_box[0] >= draw_left
                and clearance_box[1] >= draw_bottom
                and clearance_box[2] <= draw_right
                and clearance_box[3] <= draw_top
            )
            leader_clear_of_geometry_and_text = leader_clear_of_source(
                leader, segments, leader_boxes
            )
            perpendicular = False
            if point is not None:
                tangent, _, _ = nearest_segment_guide(point, segments)
                stub_x, stub_y = leader[2] - leader[0], leader[3] - leader[1]
                stub_length = math.hypot(stub_x, stub_y)
                perpendicular = stub_length > 1e-6 and abs((stub_x * tangent[0] + stub_y * tangent[1]) / stub_length) < 0.02
            page_results.append(
                {
                    "page": target.page,
                    "sheet_stream": target.stream_name,
                    "label": label,
                    "uci": key[2] if key else None,
                    "graphic_ref": key[1] if key else None,
                    "weld_type": expected_type,
                    "marker_shape": marker_shape,
                    "marker_shape_matches_weld_type": expected_type in {"S", "F"} and boundary_matches,
                    "marker_boundary_max_deviation": max(boundary_deviations, default=None),
                    "leader_starts_at_source_dot": (
                        point is not None
                        and close(
                            (leader[0], leader[1]),
                            (point.center_x, point.center_y),
                        )
                    ),
                    "leader_is_single_straight_line": leader_is_single_straight_line,
                    "marker_closed": marker_closed,
                    "leader_ends_at_marker_edge": leader_ends_at_marker_edge,
                    "leader_marker_edge_deviation": abs(edge_distance - radius),
                    "leader_initial_segment_perpendicular": perpendicular,
                    "marker_clear_of_source_geometry": marker_clear_of_geometry,
                    "marker_clear_of_source_text": marker_clear_of_text,
                    "leader_clear_of_source_geometry_and_text": leader_clear_of_geometry_and_text,
                    "marker_inside_outer_frame": marker_inside_outer_frame,
                    "marker_inside_drawing_region": marker_inside_drawing_region,
                    "marker_has_drawing_clearance": marker_has_drawing_clearance,
                    "leader_clear_of_other_leaders": True,
                    "leader_clear_of_other_markers": True,
                    "marker_clear_of_other_markers": True,
                    "label_inside_marker": label_inside,
                    "leader_start": [round(leader[0], 3), round(leader[1], 3)],
                    "leader_end": [round(leader[2], 3), round(leader[3], 3)],
                    "marker_center": [round(center_x, 3), round(center_y, 3)],
                    "distance_to_original_geometry": round(nearest, 3),
                    "on_geometry": nearest <= args.on_geometry_tolerance,
                    "distance_to_expected_pipe_centerline": (
                        round(distance_to_pipe_centerline, 3)
                        if distance_to_pipe_centerline is not None
                        else None
                    ),
                    "on_expected_pipe_centerline": on_expected_pipe_centerline,
                    "_leader": leader,
                    "_physical_box": physical_box,
                }
            )

        for first_index, first in enumerate(page_results):
            for second in page_results[first_index + 1 :]:
                first_leader = tuple(first["_leader"])
                second_leader = tuple(second["_leader"])
                first_box = tuple(first["_physical_box"])
                second_box = tuple(second["_physical_box"])
                shared_root = math.hypot(
                    first_leader[0] - second_leader[0],
                    first_leader[1] - second_leader[1],
                ) <= 1.0
                if (
                    not shared_root
                    and segments_intersect(first_leader, second_leader)
                ):
                    first["leader_clear_of_other_leaders"] = False
                    second["leader_clear_of_other_leaders"] = False
                if segment_intersects_box(first_leader, second_box):
                    first["leader_clear_of_other_markers"] = False
                if segment_intersects_box(second_leader, first_box):
                    second["leader_clear_of_other_markers"] = False
                first_spacing = (
                    first_box[0] - 10.0,
                    first_box[1] - 10.0,
                    first_box[2] + 10.0,
                    first_box[3] + 10.0,
                )
                second_spacing = (
                    second_box[0] - 10.0,
                    second_box[1] - 10.0,
                    second_box[2] + 10.0,
                    second_box[3] + 10.0,
                )
                if boxes_overlap(first_spacing, second_spacing):
                    first["marker_clear_of_other_markers"] = False
                    second["marker_clear_of_other_markers"] = False
        for item in page_results:
            item.pop("_leader", None)
            item.pop("_physical_box", None)
        results.extend(page_results)

    results.sort(key=lambda item: (int(item["page"]), str(item["label"])))
    verified = sum(
        bool(item["leader_starts_at_source_dot"])
        and bool(item["leader_is_single_straight_line"])
        and bool(item["marker_closed"])
        and bool(item["leader_ends_at_marker_edge"])
        and bool(item["marker_shape_matches_weld_type"])
        and bool(item["marker_clear_of_source_geometry"])
        and bool(item["marker_clear_of_source_text"])
        and bool(item["leader_clear_of_source_geometry_and_text"])
        and bool(item["marker_inside_outer_frame"])
        and bool(item["marker_inside_drawing_region"])
        and bool(item["marker_has_drawing_clearance"])
        and bool(item["leader_clear_of_other_leaders"])
        and bool(item["leader_clear_of_other_markers"])
        and bool(item["marker_clear_of_other_markers"])
        and bool(item["label_inside_marker"])
        and bool(item["on_expected_pipe_centerline"])
        for item in results
    )
    on_geometry = sum(bool(item["on_geometry"]) for item in results)
    virtual_pipe_callouts = sum(
        item["distance_to_expected_pipe_centerline"] is not None
        for item in results
    )
    virtual_pipe_centerline_verified = sum(
        item["distance_to_expected_pipe_centerline"] is not None
        and bool(item["on_expected_pipe_centerline"])
        for item in results
    )
    frame_overflows = sum(not bool(item["marker_inside_outer_frame"]) for item in results)
    drawing_region_violations = sum(not bool(item["marker_inside_drawing_region"]) for item in results)
    leader_source_crossings = sum(
        not bool(item["leader_clear_of_source_geometry_and_text"]) for item in results
    )
    leader_leader_crossing_callouts = sum(
        not bool(item["leader_clear_of_other_leaders"]) for item in results
    )
    callout_conflicts = sum(
        not (
            bool(item["leader_clear_of_other_leaders"])
            and bool(item["leader_clear_of_other_markers"])
            and bool(item["marker_clear_of_other_markers"])
        )
        for item in results
    )
    distribution_groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        group_value = row.get("drawing_distribution_group")
        if group_value:
            distribution_groups.setdefault(str(group_value), []).append(row)
    distribution_checks: list[dict[str, object]] = []
    for group_id, group_rows in sorted(distribution_groups.items()):
        ordered = sorted(
            group_rows,
            key=lambda row: int(row["drawing_distribution_rank"]),
        )
        ranks = [int(row["drawing_distribution_rank"]) for row in ordered]
        numbers = [str(row["weld_number"]) for row in ordered]
        expected_count = int(ordered[0]["drawing_distribution_count"])
        target_points = [
            tuple(float(value) for value in row["drawing_target_3d"])
            for row in ordered
        ]
        target_spacings = [
            math.dist(first, second)
            for first, second in zip(target_points, target_points[1:])
        ]
        target_equal = (
            not target_spacings
            or max(target_spacings) - min(target_spacings) <= 1.0
        )
        away_from_owner_endpoints = all(
            0.05 < float(row["drawing_owner_fraction"]) < 0.95
            for row in ordered
        )

        page_checks: list[dict[str, object]] = []
        for page in sorted({int(row["page"]) for row in ordered}):
            page_rows = [
                row for row in ordered if int(row["page"]) == page
            ]
            if len(page_rows) < 3:
                continue
            vectors = [
                (
                    float(second["sha_x"]) - float(first["sha_x"]),
                    float(second["sha_y"]) - float(first["sha_y"]),
                )
                for first, second in zip(page_rows, page_rows[1:])
            ]
            lengths = [math.hypot(dx, dy) for dx, dy in vectors]
            same_direction = all(
                (
                    first[0] * second[0] + first[1] * second[1]
                )
                / (
                    math.hypot(*first) * math.hypot(*second)
                )
                >= 0.99
                for first, second in zip(vectors, vectors[1:])
                if math.hypot(*first) > 1e-9
                and math.hypot(*second) > 1e-9
            )
            display_equal = (
                min(lengths) > 1e-9
                and max(lengths) / min(lengths) <= 1.15
            )
            page_checks.append(
                {
                    "page": page,
                    "weld_numbers": [
                        str(row["weld_number"]) for row in page_rows
                    ],
                    "same_drawing_direction": same_direction,
                    "near_equal_drawing_spacing": display_equal,
                    "drawing_spacings": [
                        round(value, 3) for value in lengths
                    ],
                }
            )
        verified_group = (
            len(ordered) == expected_count
            and ranks == list(range(1, expected_count + 1))
            and distribution_number_sequence_is_valid(numbers)
            and target_equal
            and away_from_owner_endpoints
            and all(
                bool(check["same_drawing_direction"])
                and bool(check["near_equal_drawing_spacing"])
                for check in page_checks
            )
        )
        distribution_checks.append(
            {
                "group": group_id,
                "weld_numbers": [str(number) for number in numbers],
                "rank_sequence": ranks,
                "target_spacings_3d": [
                    round(value, 3) for value in target_spacings
                ],
                "equal_target_spacing_3d": target_equal,
                "away_from_owner_endpoints": away_from_owner_endpoints,
                "page_checks": page_checks,
                "verified": verified_group,
            }
        )
    distribution_verified = sum(
        bool(check["verified"]) for check in distribution_checks
    )
    payload = {
        "source_sha": str(args.source_sha),
        "annotated_sha": str(args.annotated_sha),
        "callout_count": len(results),
        "typed_marker_structurally_verified": verified,
        "on_original_geometry": on_geometry,
        "virtual_pipe_callout_count": virtual_pipe_callouts,
        "virtual_pipe_centerline_verified": virtual_pipe_centerline_verified,
        "virtual_pipe_centerline_violation_count": (
            virtual_pipe_callouts - virtual_pipe_centerline_verified
        ),
        "frame_overflow_count": frame_overflows,
        "drawing_region_violation_count": drawing_region_violations,
        "leader_source_crossing_count": leader_source_crossings,
        "leader_leader_crossing_callout_count": leader_leader_crossing_callouts,
        "callout_conflict_count": callout_conflicts,
        "long_pipe_distribution_group_count": len(distribution_checks),
        "long_pipe_distribution_callout_count": sum(
            len(check["weld_numbers"]) for check in distribution_checks
        ),
        "long_pipe_distribution_verified": distribution_verified,
        "long_pipe_distribution_violation_count": (
            len(distribution_checks) - distribution_verified
        ),
        "long_pipe_distribution_checks": distribution_checks,
        "geometry_tolerance": args.on_geometry_tolerance,
        "callouts": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: payload[key] for key in payload if key != "callouts"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
