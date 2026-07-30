#!/usr/bin/env python3
"""Inject experimental weld-diamond callouts directly into SHA Sheet streams.

This tool stays on the SHA side: it identifies micro UCI connection dots that
already exist in the drawing, computes a non-overlapping diamond callout near
each visible point, then appends new Shape2D-like line/text records into the
target Sheet streams.

It does not use PDF geometry, text, or image data.

Current limits:
- The injected graphics are validated against the local SHA-only renderer in
  this repository.
- They are written back into the compound file, but vendor-engine acceptance
  still depends on undocumented Shape2D hierarchy rules that are only partly
  decoded.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from pathlib import Path

import olefile

from analyze_iso_split import read_sha_streams
from analyze_psm_hierarchy import (
    bounded_dynamic_graphics_by_uci,
    parse_dynamic_attribute_property_records,
)
from sha_to_svg_prototype import (
    PAGE_HEIGHT,
    PAGE_WIDTH,
    SHEET_UNIT,
    composite_segments,
    declared_sheet_viewbox,
    ellipse_anchors,
    line_segments,
    psm_bbox,
    sheet_viewbox,
    style_fallbacks,
    template_line_segments,
    text_records,
)

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIR_ENTRY_SIZE = 128

TEXT_TEMPLATE = "80X25"
TEXT_TEMPLATE_STYLE = 1413
LINE_STYLE = 245
LINE_PARENT_REF = 1414
TEXT_NUMBER_TEMPLATE = "12345"
SYNTHETIC_TEXT_REF_BASE = 0x00F00000
DIRECT_TEXT_ANCHOR_STYLES = {0x0585, 0x0586, 0x0897, 0x0E74}


@dataclass
class SheetTarget:
    stream_name: str
    page: int
    data: bytes


@dataclass
class PointTarget:
    graphic_ref: int
    uci: str
    center_x: float
    center_y: float
    page: int


@dataclass
class CalloutPlacement:
    center_x: float
    center_y: float
    elbow_x: float
    elbow_y: float
    leader_x: float
    leader_y: float
    number: str


@dataclass
class TextTemplate:
    raw: bytes
    style_ref: int


@dataclass
class TextStyleModel:
    offset_x: float
    offset_y: float
    font_height: float
    char_width: float


def shared_style_model(targets: list[SheetTarget], psm: bytes, style_ref: int) -> TextStyleModel | None:
    samples: list[tuple[float, float, float, float]] = []
    for target in targets:
        local = style_fallbacks(target.data, psm).get(style_ref)
        if local is not None:
            samples.append(local)
            continue
        for record in text_records(target.data):
            if int(record["style_ref"]) != style_ref:
                continue
            text = str(record["text"]).strip()
            x = float(record["x"])
            y = float(record["y"])
            bbox = psm_bbox(psm, int(record["graphic_ref"]))
            if not text or bbox is None:
                continue
            left, bottom, right, top = bbox
            samples.append(
                (
                    left - x * SHEET_UNIT,
                    bottom - y * SHEET_UNIT,
                    top - bottom,
                    (right - left) / max(1, len(text)),
                )
            )
    if not samples:
        return None
    return TextStyleModel(*(median(value[index] for value in samples) for index in range(4)))


def point_segment_distance(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx = x1 + t * dx
    qy = y1 + t * dy
    return math.hypot(px - qx, py - qy)


def sheet_streams(streams: dict[str, bytes], wanted_pages: set[int] | None) -> list[SheetTarget]:
    """Return physical ISO sheets in their SHA container order.

    Page-number text is not reliable in every project: some Sheet6 title
    blocks contain a later revision field that looks like ``4 OF 4``. The
    Shape2D convention observed across the supplied files is deterministic:
    ``Sheet6`` is the first physical page and later sheets are numbered in
    ascending stream-id order. Keep that identity for UCI mapping, callout
    insertion, and verification instead of deriving it from drawing text.
    """

    physical = [
        (name, data)
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name) and name != "Sheet221" and len(data) > 1024
    ]
    physical.sort(key=lambda item: (0 if item[0] == "Sheet6" else 1, int(item[0][5:])))
    targets = [
        SheetTarget(stream_name=name, page=index, data=data)
        for index, (name, data) in enumerate(physical, start=1)
    ]
    return [target for target in targets if not wanted_pages or target.page in wanted_pages]


def collect_connection_points(
    streams: dict[str, bytes],
    targets: list[SheetTarget],
    distance_threshold: float,
) -> dict[str, list[PointTarget]]:
    psm = streams.get("PSMcluster0", b"")
    dynamic = bounded_dynamic_graphics_by_uci(
        parse_dynamic_attribute_property_records(
            streams.get("Unclustered Dynamic Attributes", b"")
        )
    )
    graphics: dict[int, list[str]] = defaultdict(list)
    for uci, records in dynamic.items():
        for record in records:
            graphics[int(record["graphic_ref"])].append(str(uci))

    global_regions = []
    for graphic_ref, ucis in graphics.items():
        bbox = psm_bbox(psm, graphic_ref)
        if bbox is None:
            continue
        left, bottom, right, top = bbox
        if (right - left) > 45 or (top - bottom) > 45:
            continue
        global_regions.append((graphic_ref, str(ucis[0]), (left + right) / 2, (bottom + top) / 2))

    result: dict[str, list[PointTarget]] = {}
    for target in targets:
        segments = line_segments(target.data) + template_line_segments(target.data) + composite_segments(target.data)
        vector_refs = {ref for *_, ref, child_ref in segments} | {child_ref for *_, ref, child_ref in segments}
        # Use the Shape2D ellipse anchor for a weld/connection dot whenever
        # present. PSM describes its rendered envelope, not necessarily the
        # plotted black-dot centre.
        anchors = ellipse_anchors(target.data)
        visible_points: list[PointTarget] = []
        for graphic_ref, uci, center_x, center_y in global_regions:
            if graphic_ref in vector_refs:
                continue
            center_x, center_y = anchors.get(graphic_ref, (center_x, center_y))
            best = min(
                point_segment_distance(
                    center_x,
                    center_y,
                    x1 * SHEET_UNIT,
                    y1 * SHEET_UNIT,
                    x2 * SHEET_UNIT,
                    y2 * SHEET_UNIT,
                )
                for x1, y1, x2, y2, _, _ in segments
            )
            if best <= distance_threshold:
                visible_points.append(
                    PointTarget(
                        graphic_ref=graphic_ref,
                        uci=uci,
                        center_x=center_x,
                        center_y=center_y,
                        page=target.page,
                    )
                )
        result[target.stream_name] = sorted(visible_points, key=lambda item: (item.center_y, item.center_x))
    return result


def build_text_template(sheet: bytes, psm: bytes) -> TextTemplate | None:
    records = text_records(sheet)
    # Project templates vary. Prefer the calibrated 80X25 record but fall
    # back to any five-character record, which has the same fixed record size
    # required by the synthetic weld label writer.
    candidates = [record for record in records if str(record["text"]).strip() == TEXT_TEMPLATE]
    candidates.extend(record for record in records if len(str(record["text"]).strip()) == 5 and record not in candidates)
    for record in candidates:
        bbox = psm_bbox(psm, int(record["graphic_ref"]))
        if bbox is None:
            continue
        text_bytes = str(record["text"]).encode("utf-16le")
        offset = sheet.find(text_bytes)
        if offset == -1:
            continue
        raw = sheet[offset - 24 : offset + len(text_bytes) + 32]
        if len(raw) == 66:
            return TextTemplate(
                raw=raw,
                style_ref=int(record["style_ref"]),
            )
    return None


def max_local_refs(sheet: bytes) -> tuple[int, int]:
    max_u16 = 5000
    max_u32 = 5000
    for start in range(0, len(sheet) - 50, 2):
        u32 = struct.unpack_from("<I", sheet, start)[0]
        if u32 < 0x00F00000:
            max_u32 = max(max_u32, u32)
        u16 = struct.unpack_from("<H", sheet, start + 4)[0]
        if u16 < 0xF000:
            max_u16 = max(max_u16, u16)
    return max_u16, max_u32


def candidate_positions(
    point: PointTarget,
    used_boxes: list[tuple[float, float, float, float]],
    used_leaders: list[tuple[float, float, float, float]],
    source_segments: list[tuple[float, float, float, float, int, int]],
    source_boxes: list[tuple[float, float, float, float]],
    viewbox: tuple[float, float, float, float],
    diamond_radius: float,
    gap: float,
    normal: tuple[float, float],
    side: int,
    allow_crossings: bool = False,
    strict_normal: bool = False,
    allow_text_overlap: bool = False,
) -> CalloutPlacement | None:
    view_x, view_y, view_width, view_height = viewbox
    min_x = view_x
    max_x = view_x + view_width
    min_y = view_y
    max_y = view_y + view_height

    def overlaps(box: tuple[float, float, float, float], other: tuple[float, float, float, float]) -> bool:
        left, bottom, right, top = box
        other_left, other_bottom, other_right, other_top = other
        return not (right < other_left or left > other_right or top < other_bottom or bottom > other_top)

    def clear(center_x: float, center_y: float) -> bool:
        left = center_x - diamond_radius - gap
        right = center_x + diamond_radius + gap
        bottom = center_y - diamond_radius - gap
        top = center_y + diamond_radius + gap
        box = (left, bottom, right, top)
        if any(overlaps(box, existing) for existing in used_boxes):
            return False
        if not allow_text_overlap and any(overlaps(box, obstacle) for obstacle in source_boxes):
            return False
        # A segment close to the enclosing square can cross the diamond even
        # if it does not cross a text/PSM envelope.
        clearance = diamond_radius * math.sqrt(2) + gap
        return all(
            point_segment_distance(
                center_x,
                center_y,
                x1 * SHEET_UNIT,
                y1 * SHEET_UNIT,
                x2 * SHEET_UNIT,
                y2 * SHEET_UNIT,
            ) > clearance
            for x1, y1, x2, y2, _, _ in source_segments
        )

    def orientation(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    def crosses_existing(x1: float, y1: float, x2: float, y2: float) -> bool:
        for ax, ay, bx, by in used_leaders:
            first = orientation(x1, y1, x2, y2, ax, ay)
            second = orientation(x1, y1, x2, y2, bx, by)
            third = orientation(ax, ay, bx, by, x1, y1)
            fourth = orientation(ax, ay, bx, by, x2, y2)
            if first * second < -1e-6 and third * fourth < -1e-6:
                return True
        return False

    def edge_from(source_x: float, source_y: float, center_x: float, center_y: float) -> tuple[float, float]:
        delta_x = source_x - center_x
        delta_y = source_y - center_y
        edge_scale = diamond_radius / (abs(delta_x) + abs(delta_y))
        return center_x + delta_x * edge_scale, center_y + delta_y * edge_scale

    # A leader normal to its nearest pipe segment is clearer than an arbitrary
    # radial line.  A group uses one side of that segment consistently.
    normal_x, normal_y = normal
    elbow_x = point.center_x + side * normal_x * 260.0
    elbow_y = point.center_y + side * normal_y * 260.0
    for radius in range(550, 5001, 250):
        center_x = point.center_x + side * normal_x * radius
        center_y = point.center_y + side * normal_y * radius
        left = center_x - diamond_radius - gap
        right = center_x + diamond_radius + gap
        bottom = center_y - diamond_radius - gap
        top = center_y + diamond_radius + gap
        if left < min_x or right > max_x or bottom < min_y or top > max_y:
            continue
        if not clear(center_x, center_y):
            continue
        leader_x, leader_y = edge_from(elbow_x, elbow_y, center_x, center_y)
        if not allow_crossings and (crosses_existing(point.center_x, point.center_y, elbow_x, elbow_y) or crosses_existing(elbow_x, elbow_y, leader_x, leader_y)):
            continue
        return CalloutPlacement(center_x, center_y, elbow_x, elbow_y, leader_x, leader_y, TEXT_NUMBER_TEMPLATE)

    if strict_normal:
        return None

    # If the entire normal ray is blocked, retain a short perpendicular first
    # segment then route from that elbow to free space on the same side.
    directions = [(math.cos(angle), math.sin(angle)) for angle in (index * math.pi / 12 for index in range(24))]
    for radius in range(650, 5001, 250):
        for dx, dy in directions:
            if side * (dx * normal_x + dy * normal_y) < 0.35:
                continue
            center_x = point.center_x + dx * radius
            center_y = point.center_y + dy * radius
            left = center_x - diamond_radius - gap
            right = center_x + diamond_radius + gap
            bottom = center_y - diamond_radius - gap
            top = center_y + diamond_radius + gap
            if left < min_x or right > max_x or bottom < min_y or top > max_y or not clear(center_x, center_y):
                continue
            leader_x, leader_y = edge_from(elbow_x, elbow_y, center_x, center_y)
            if not allow_crossings and (crosses_existing(point.center_x, point.center_y, elbow_x, elbow_y) or crosses_existing(elbow_x, elbow_y, leader_x, leader_y)):
                continue
            return CalloutPlacement(center_x, center_y, elbow_x, elbow_y, leader_x, leader_y, TEXT_NUMBER_TEMPLATE)

    return None


def nearest_segment_guide(
    point: PointTarget, source_segments: list[tuple[float, float, float, float, int, int]]
) -> tuple[tuple[float, float], tuple[float, float], tuple[int, int]]:
    """Return a stable tangent/normal and a coarse key for one pipe run."""

    # Do not orient a weld callout from a flange edge, small reducer outline,
    # or symbol stroke. Long Sheet segments are the actual pipe-run evidence.
    pipe_segments = [
        item
        for item in source_segments
        if math.hypot((item[2] - item[0]) * SHEET_UNIT, (item[3] - item[1]) * SHEET_UNIT) >= 220.0
    ] or source_segments
    x1, y1, x2, y2, _, _ = min(
        pipe_segments,
        key=lambda item: point_segment_distance(
            point.center_x, point.center_y,
            item[0] * SHEET_UNIT, item[1] * SHEET_UNIT,
            item[2] * SHEET_UNIT, item[3] * SHEET_UNIT,
        ),
    )
    dx, dy = (x2 - x1) * SHEET_UNIT, (y2 - y1) * SHEET_UNIT
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return (1.0, 0.0), (0.0, 1.0), (0, 0)
    tangent = (dx / length, dy / length)
    if tangent[0] < 0 or (abs(tangent[0]) < 1e-6 and tangent[1] < 0):
        tangent = (-tangent[0], -tangent[1])
    normal = (-tangent[1], tangent[0])
    angle_bin = round(math.degrees(math.atan2(tangent[1], tangent[0])) / 10)
    offset_bin = round((point.center_x * normal[0] + point.center_y * normal[1]) / 200)
    return tangent, normal, (angle_bin, offset_bin)


def source_obstacle_boxes(sheet: bytes, psm: bytes, gap: float) -> list[tuple[float, float, float, float]]:
    """Reserve source text extents so a new diamond stays in whitespace."""

    boxes: list[tuple[float, float, float, float]] = []
    for record in text_records(sheet):
        bbox = psm_bbox(psm, int(record["graphic_ref"]))
        if bbox is None:
            continue
        left, bottom, right, top = bbox
        boxes.append((left - gap, bottom - gap, right + gap, top + gap))
    return boxes


def pack_line_record(child_ref: int, object_ref: int, x1: float, y1: float, x2: float, y2: float) -> bytes:
    return struct.pack(
        "<I7H4d",
        child_ref,
        object_ref,
        0,
        LINE_PARENT_REF,
        0,
        0,
        LINE_STYLE,
        0,
        x1 / SHEET_UNIT,
        y1 / SHEET_UNIT,
        x2 / SHEET_UNIT,
        y2 / SHEET_UNIT,
    )


def pack_text_record(
    template: TextTemplate,
    style_model: TextStyleModel,
    graphic_ref: int,
    center_x: float,
    center_y: float,
    number: str,
) -> bytes:
    raw = bytearray(template.raw)
    struct.pack_into("<I", raw, 0, graphic_ref)
    struct.pack_into("<I", raw, 8, template.style_ref)
    fixed_number = number[:5].ljust(5)
    raw[24:34] = fixed_number.encode("utf-16le")
    visible_number = fixed_number.rstrip() or fixed_number
    width = max(style_model.font_height * 0.7, style_model.char_width * len(visible_number))
    height = style_model.font_height
    left = center_x - width / 2
    bottom = center_y - height / 2
    if template.style_ref in DIRECT_TEXT_ANCHOR_STYLES:
        # These verified styles render from their Shape2D text baseline.
        anchor_x = left / SHEET_UNIT
        anchor_y = (center_y - height * 0.30) / SHEET_UNIT
    else:
        # The selected 80X25 template uses a PSM-style local text offset.
        # Invert that offset so the final rendered glyph box is centred in
        # the diamond rather than displaced with the borrowed template.
        anchor_x = (left - style_model.offset_x) / SHEET_UNIT
        anchor_y = (bottom - style_model.offset_y) / SHEET_UNIT
    struct.pack_into("<dddd", raw, 34, anchor_x, anchor_y, 1.0, 0.0)
    return bytes(raw)


def inject_into_sheet(
    target: SheetTarget,
    points: list[PointTarget],
    psm: bytes,
    label_start: int,
    diamond_radius: float,
    template: TextTemplate,
    shared_style: TextStyleModel | None,
    weld_number_by_point: dict[tuple[int, int, str], str] | None,
    strict_normal: bool,
    lane_by_point: dict[tuple[int, int, str], dict[str, object]] | None = None,
    allow_text_overlap: bool = False,
    inherited_viewbox: tuple[float, float, float, float] | None = None,
) -> tuple[bytes, int]:
    if not points:
        return target.data, label_start

    max_u16, max_u32 = max_local_refs(target.data)
    fallback_models = style_fallbacks(target.data, psm)
    fallback = fallback_models.get(template.style_ref)
    if fallback is None:
        if shared_style is None:
            raise ValueError(
                f"Missing fallback style metrics for style 0x{template.style_ref:04X} in {target.stream_name}"
            )
        style_model = shared_style
    else:
        style_model = TextStyleModel(*fallback)
    object_ref = max_u16 + 1
    child_ref = max_u32 + 1

    viewbox = sheet_viewbox(target.data, inherited_viewbox)
    used_boxes: list[tuple[float, float, float, float]] = []
    used_leaders: list[tuple[float, float, float, float]] = []
    source_segments = line_segments(target.data) + template_line_segments(target.data) + composite_segments(target.data)
    source_boxes = source_obstacle_boxes(target.data, psm, gap=80.0)
    injected = bytearray()
    if len(target.data) % 2:
        injected.extend(b"\x00")

    guides = {id(point): nearest_segment_guide(point, source_segments) for point in points}
    grouped: dict[object, list[PointTarget]] = defaultdict(list)
    for point in points:
        key = (point.page, point.graphic_ref, point.uci)
        lane = lane_by_point.get(key) if lane_by_point else None
        group_key: object = ("pcf", lane["pipe_component_id"]) if lane else guides[id(point)][2]
        grouped[group_key].append(point)

    planned: list[tuple[PointTarget, CalloutPlacement]] = []
    # Plan a complete collinear group on one side before committing it.  This
    # gives parallel leaders and avoids the fan/crossing appearance.
    planning_groups: list[tuple[object, list[PointTarget], tuple[float, float], tuple[float, float]]] = []
    for group_key in sorted(grouped):
        initial = grouped[group_key]
        tangent, _, _ = guides[id(initial[0])]
        ordered = sorted(initial, key=lambda point: point.center_x * tangent[0] + point.center_y * tangent[1])
        # A component id can recur in separate page/projection contexts.  The
        # SHA line nearest each weld is the authority for leader direction;
        # never infer a normal from the line joining two weld dots.
        clusters: list[list[PointTarget]] = []
        for point in ordered:
            point_guide = guides[id(point)][2]
            matching = next(
                (
                    cluster
                    for cluster in clusters
                    if guides[id(cluster[0])][2] == point_guide
                    and any(math.dist((point.center_x, point.center_y), (other.center_x, other.center_y)) <= 1200.0 for other in cluster)
                ),
                None,
            )
            if matching is None:
                clusters.append([point])
            else:
                matching.append(point)
        for cluster in clusters:
            cluster_tangent, cluster_normal, _ = guides[id(cluster[0])]
            planning_groups.append((group_key, cluster, cluster_tangent, cluster_normal))

    # Reserve whitespace for the long, dense main-run groups first.  This
    # reduces later forced crossings caused by short branch callouts taking
    # the only usable exterior lanes.
    planning_groups.sort(key=lambda item: (len(item[1]), item[0]), reverse=True)

    drawing_mid_x = median(point.center_x for point in points)
    for group_key, group, tangent, normal in planning_groups:
        candidates: list[tuple[float, int, list[tuple[PointTarget, CalloutPlacement]], list[tuple[float, float, float, float]], list[tuple[float, float, float, float]]]] = []
        first_key = (group[0].page, group[0].graphic_ref, group[0].uci)
        lane = lane_by_point.get(first_key) if lane_by_point else None
        # Topology mode fixes one side per PCF pipe run.  No per-weld flip,
        # no radial fallback: leaders remain parallel and cannot fan across.
        side = 1 if lane and lane.get("side") == "left" else -1
        # On a vertical projected run, horizontal outward placement is more
        # legible than a literal PCF left/right label.  This avoids throwing
        # central riser callouts back through the drawing.
        if lane and abs(tangent[0]) < 0.20:
            side = -1 if median(point.center_x for point in group) >= drawing_mid_x else 1
        sides = (side,) if lane else (1, -1)
        for side in sides:
            test_boxes = list(used_boxes)
            test_leaders = list(used_leaders)
            test_plan: list[tuple[PointTarget, CalloutPlacement]] = []
            for point in group:
                placement = candidate_positions(
                    point, test_boxes, test_leaders, source_segments, source_boxes,
                    viewbox, diamond_radius, gap=60.0, normal=normal, side=side,
                    strict_normal=strict_normal,
                    allow_text_overlap=allow_text_overlap,
                    allow_crossings=allow_text_overlap,
                )
                if placement is None:
                    break
                test_plan.append((point, placement))
                test_boxes.append((
                    placement.center_x - diamond_radius - 60.0,
                    placement.center_y - diamond_radius - 60.0,
                    placement.center_x + diamond_radius + 60.0,
                    placement.center_y + diamond_radius + 60.0,
                ))
                test_leaders.extend((
                    (point.center_x, point.center_y, placement.elbow_x, placement.elbow_y),
                    (placement.elbow_x, placement.elbow_y, placement.leader_x, placement.leader_y),
                ))
            if len(test_plan) == len(group):
                total_length = sum(math.hypot(p.center_x - c.center_x, p.center_y - c.center_y) for p, c in test_plan)
                candidates.append((total_length, side, test_plan, test_boxes, test_leaders))
        if not candidates:
            if strict_normal or lane:
                print(f"Skipped unresolved strict-normal group on page {target.page}: {group_key}")
                continue
            # A compact branch can have no globally feasible common side once
            # earlier leaders are reserved. Fall back only for this group,
            # keeping every individual leader perpendicular at its weld and
            # collision-free rather than drawing an intersecting callout.
            for point in group:
                options: list[CalloutPlacement] = []
                for side in (1, -1):
                    placement = candidate_positions(
                        point, used_boxes, used_leaders, source_segments, source_boxes,
                        viewbox, diamond_radius, gap=60.0, normal=normal, side=side,
                        strict_normal=strict_normal,
                        allow_text_overlap=allow_text_overlap,
                    )
                    if placement is None:
                        placement = candidate_positions(
                            point, used_boxes, used_leaders, source_segments, source_boxes,
                            viewbox, diamond_radius, gap=60.0, normal=normal, side=side,
                            allow_crossings=True,
                            strict_normal=strict_normal,
                            allow_text_overlap=allow_text_overlap,
                        )
                    if placement is not None:
                        options.append(placement)
                if not options:
                    raise ValueError(f"No collision-free callout plan for page {target.page}, group {group_key}")
                placement = min(options, key=lambda item: math.hypot(point.center_x - item.center_x, point.center_y - item.center_y))
                planned.append((point, placement))
                used_boxes.append((
                    placement.center_x - diamond_radius - 60.0,
                    placement.center_y - diamond_radius - 60.0,
                    placement.center_x + diamond_radius + 60.0,
                    placement.center_y + diamond_radius + 60.0,
                ))
                used_leaders.extend((
                    (point.center_x, point.center_y, placement.elbow_x, placement.elbow_y),
                    (placement.elbow_x, placement.elbow_y, placement.leader_x, placement.leader_y),
                ))
            continue
        _, _, group_plan, used_boxes, used_leaders = min(candidates, key=lambda item: item[0])
        planned.extend(group_plan)

    for index, (point, placement) in enumerate(planned):
        point_key = (point.page, point.graphic_ref, point.uci)
        number = weld_number_by_point.get(point_key) if weld_number_by_point else None
        if not number:
            number = f"{label_start + index:05d}"

        cx = placement.center_x
        cy = placement.center_y
        r = diamond_radius
        top = (cx, cy + r)
        right = (cx + r, cy)
        bottom = (cx, cy - r)
        left = (cx - r, cy)

        line_specs = [
            (point.center_x, point.center_y, placement.elbow_x, placement.elbow_y),
            (placement.elbow_x, placement.elbow_y, placement.leader_x, placement.leader_y),
            (*top, *right),
            (*right, *bottom),
            (*bottom, *left),
            (*left, *top),
        ]
        for x1, y1, x2, y2 in line_specs:
            injected.extend(pack_line_record(child_ref, object_ref, x1, y1, x2, y2))
            object_ref += 1
            child_ref += 1

        # Keep synthetic text outside the existing PSM graphic-id range.
        # Otherwise the renderer can attach an unrelated source PSM envelope
        # to this new label and visibly move it out of the diamond.
        synthetic_text_ref = SYNTHETIC_TEXT_REF_BASE + target.page * 0x1000 + index
        injected.extend(pack_text_record(template, style_model, synthetic_text_ref, cx, cy, number))
        child_ref += 1

    return target.data + bytes(injected), label_start + len(points)


def sector_offset(sector_index: int, sector_size: int) -> int:
    return (sector_index + 1) * sector_size


def stream_chain(fat: list[int], start_sector: int) -> list[int]:
    if start_sector in (FREESECT, ENDOFCHAIN):
        return []
    chain: list[int] = []
    sector = start_sector
    seen: set[int] = set()
    while sector not in (FREESECT, ENDOFCHAIN):
        if sector in seen:
            raise ValueError("Detected a sector loop while traversing the OLE FAT")
        seen.add(sector)
        chain.append(sector)
        sector = fat[sector]
    return chain


def patch_streams_in_compound(
    source_path: Path,
    output_path: Path,
    replacements: dict[str, bytes],
) -> None:
    original = bytearray(source_path.read_bytes())
    with olefile.OleFileIO(source_path) as ole:
        sector_size = ole.sectorsize
        fat = list(ole.fat)
        total_sectors = len(fat)

        difat = list(struct.unpack_from("<109I", original, 76))
        fat_sector = next(value for value in difat if value not in (FREESECT, ENDOFCHAIN))
        fat_capacity = sector_size // 4
        if total_sectors + sum(
            max(0, math.ceil(len(data) / sector_size) - math.ceil(ole.direntries[ole._find(name.split("/"))].size / sector_size))
            for name, data in replacements.items()
        ) > fat_capacity:
            raise ValueError("This experimental patcher currently supports only one FAT sector")

        for stream_name, new_data in replacements.items():
            sid = ole._find(stream_name.split("/"))
            entry = ole.direntries[sid]
            if entry is None:
                raise ValueError(f"Missing stream {stream_name} in OLE directory")
            old_size = int(entry.size)
            old_chain = stream_chain(fat, int(entry.isectStart))
            old_sector_count = max(1, math.ceil(old_size / sector_size))
            new_sector_count = max(1, math.ceil(len(new_data) / sector_size))
            if old_sector_count != len(old_chain):
                old_chain = old_chain[:old_sector_count]
            if new_sector_count < old_sector_count:
                raise ValueError("Shrinking streams is intentionally disabled in this experimental patcher")

            extra_needed = new_sector_count - old_sector_count
            if extra_needed:
                first_new_sector = total_sectors
                for index in range(extra_needed):
                    next_sector = total_sectors + index + 1 if index + 1 < extra_needed else ENDOFCHAIN
                    fat.append(next_sector)
                fat[old_chain[-1]] = first_new_sector
                original.extend(b"\x00" * sector_size * extra_needed)
                total_sectors += extra_needed
            else:
                fat[old_chain[-1]] = ENDOFCHAIN

            full_chain = old_chain + list(range(len(fat) - extra_needed, len(fat))) if extra_needed else old_chain
            padded = new_data + b"\x00" * (new_sector_count * sector_size - len(new_data))
            for index, sector in enumerate(full_chain[:new_sector_count]):
                chunk = padded[index * sector_size : (index + 1) * sector_size]
                start = sector_offset(sector, sector_size)
                original[start : start + sector_size] = chunk

            dir_chain = stream_chain(fat, ole.first_dir_sector)
            dir_offset = sid * DIR_ENTRY_SIZE
            dir_sector = dir_chain[dir_offset // sector_size]
            dir_pos = sector_offset(dir_sector, sector_size) + (dir_offset % sector_size)
            struct.pack_into("<I", original, dir_pos + 116, int(entry.isectStart))
            struct.pack_into("<Q", original, dir_pos + 120, len(new_data))

        fat_bytes = bytearray(sector_size)
        for index, value in enumerate(fat):
            struct.pack_into("<I", fat_bytes, index * 4, value)
        fat_pos = sector_offset(fat_sector, sector_size)
        original[fat_pos : fat_pos + sector_size] = fat_bytes
        output_path.write_bytes(original)


def parse_pages(raw: str | None) -> set[int] | None:
    if not raw:
        return None
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha_path", type=Path, help="Input SHA file to annotate.")
    parser.add_argument("--output", type=Path, required=True, help="Output SHA path for the annotated copy.")
    parser.add_argument(
        "--pages",
        help="Comma-separated logical ISO pages to annotate. Default: all populated pages.",
    )
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=80.0,
        help="Maximum distance from a micro UCI dot to page geometry for it to count as visible.",
    )
    parser.add_argument(
        "--diamond-radius",
        type=float,
        default=145.0,
        help="Half-diagonal radius of the injected diamond marker in page units.",
    )
    parser.add_argument(
        "--start-number",
        type=int,
        default=12345,
        help="First numeric label to write into the injected diamonds.",
    )
    parser.add_argument(
        "--weld-map",
        type=Path,
        help="Optional JSON map emitted by number_pcf_welds.py. Uses PCF-derived weld numbers by UCI.",
    )
    parser.add_argument(
        "--strict-normal-layout",
        action="store_true",
        help="Keep only collision-free straight normal leaders; skip groups without a valid same-side lane.",
    )
    parser.add_argument(
        "--lane-plan",
        type=Path,
        help="Optional PCF topology plan from plan_pcf_weld_lanes.py; fixes a shared side for each PIPE run.",
    )
    parser.add_argument(
        "--relaxed-text-clearance",
        action="store_true",
        help="Keep PCF lane and noncrossing rules, but permit a diamond near existing text when no fully clear lane exists.",
    )
    args = parser.parse_args()

    streams = read_sha_streams(args.sha_path)
    wanted_pages = parse_pages(args.pages)
    targets = sheet_streams(streams, wanted_pages)
    if not targets:
        raise SystemExit("No populated Sheet streams matched the requested page selection.")

    psm = streams.get("PSMcluster0", b"")
    shared_viewbox = declared_sheet_viewbox(streams.get("Sheet6", b""))
    if shared_viewbox is None:
        shared_viewbox = next(
            (viewbox for data in streams.values() if (viewbox := declared_sheet_viewbox(data)) is not None),
            None,
        )
    shared_template = next(
        (template for template in (build_text_template(target.data, psm) for target in targets) if template is not None),
        None,
    )
    if shared_template is None:
        raise SystemExit(f"Could not locate the shared {TEXT_TEMPLATE!r} text template in any selected Sheet stream.")
    shared_style = shared_style_model(targets, psm, shared_template.style_ref)
    points_by_stream = collect_connection_points(streams, targets, args.distance_threshold)
    # A UCI can be present in multiple Sheet contexts.  A PCF weld map has
    # already selected one visible SHA dot, so retain that page + graphic ref
    # identity here instead of applying the same number to every occurrence.
    weld_number_by_point: dict[tuple[int, int, str], str] | None = None
    if args.weld_map is not None:
        payload = json.loads(args.weld_map.read_text())
        weld_number_by_point = {
            (int(entry["page"]), int(entry["graphic_ref"]), str(entry["uci"])): str(entry["weld_number"])
            for entry in payload.get("welds", [])
            if (
                entry.get("uci")
                and entry.get("weld_number")
                and entry.get("page") not in (None, 999999)
                and entry.get("graphic_ref") is not None
            )
        }
    lane_by_point: dict[tuple[int, int, str], dict[str, object]] | None = None
    if args.lane_plan is not None:
        lane_payload = json.loads(args.lane_plan.read_text())
        lane_by_point = {
            (int(entry["page"]), int(entry["graphic_ref"]), str(entry["uci"])): entry
            for entry in lane_payload.get("welds", [])
        }
    replacements: dict[str, bytes] = {}
    next_number = args.start_number
    for target in targets:
        points = points_by_stream.get(target.stream_name, [])
        if weld_number_by_point is not None:
            points = [
                point
                for point in points
                if (point.page, point.graphic_ref, point.uci) in weld_number_by_point
            ]
        new_data, next_number = inject_into_sheet(
            target=target,
            points=points,
            psm=psm,
            label_start=next_number,
            diamond_radius=args.diamond_radius,
            template=shared_template,
            shared_style=shared_style,
            weld_number_by_point=weld_number_by_point,
            strict_normal=args.strict_normal_layout,
            lane_by_point=lane_by_point,
            allow_text_overlap=args.relaxed_text_clearance,
            inherited_viewbox=shared_viewbox,
        )
        replacements[target.stream_name] = new_data
        print(
            f"{target.stream_name} page {target.page}: "
            f"{len(points)} injected callouts"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    patch_streams_in_compound(args.sha_path, args.output, replacements)
    print(f"Wrote annotated SHA copy to {args.output}")


if __name__ == "__main__":
    main()
