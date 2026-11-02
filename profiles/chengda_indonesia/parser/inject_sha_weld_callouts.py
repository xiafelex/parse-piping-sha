#!/usr/bin/env python3
"""Inject experimental typed weld callouts directly into SHA Sheet streams.

This tool stays on the SHA side: it identifies micro UCI connection dots that
already exist in the drawing, computes a non-overlapping typed callout near
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
import random
import re
import struct
import time
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from pathlib import Path

import olefile
from chengda_callout_text import weld_label_scale

from analyze_iso_split import (
    forward_owned_dynamic_graphics,
    read_sha_streams,
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
    sheet_rectangles,
    sheet_viewbox,
    standalone_arc_is_connected,
    standalone_arcs,
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
MARKER_SEGMENT_COUNT = 16
SOURCE_MARKER_CLEARANCE = 20.0
WELD_CALLOUT_FONT_HEIGHT = 88.0
WELD_CALLOUT_MARKER_RADIUS = 120.0
AVOIDABLE_SOURCE_CROSSING_PENALTY = 20000.0
PREFERRED_ROOT_CLEARANCE = 150.0
SOURCE_LINE_CROSSING_PENALTY = 20000.0
SOURCE_TEXT_CROSSING_PENALTY = 50000.0
SOURCE_CORRIDOR_CLEARANCE = 42.0
SOURCE_CORRIDOR_PENALTY = 18000.0
LOCAL_GEOMETRY_RADIUS = 1100.0
DIRECTIONAL_SECTOR_HALF_ANGLE = math.radians(42.5)
DIRECTIONAL_SECTOR_PENALTY = 28000.0
PEER_ROOT_RADIUS = 1700.0
PEER_ROOT_DIRECTION_PENALTY = 140000.0
CONGESTED_CARDINAL_RATIO = 0.20
CONGESTED_CARDINAL_PENALTY = 90000.0


def synthetic_weld_text_ref(page: int, index: int, text_length: int) -> int:
    """Encode an injected label's record length in its reserved reference.

    Legacy labels occupy five UTF-16 characters.  The upper four bits of the
    page-local 12-bit slot record any additional characters, while the lower
    byte remains the label index.  Existing five-character references are
    therefore byte-for-byte unchanged.
    """

    stored_length = max(5, int(text_length))
    extra = stored_length - 5
    if not 0 <= int(index) <= 0xFF:
        raise ValueError(f"Too many injected labels on page {page}: index={index}")
    if not 0 <= extra <= 0x0F:
        raise ValueError(f"Injected weld label is too long: length={text_length}")
    return SYNTHETIC_TEXT_REF_BASE + int(page) * 0x1000 + extra * 0x100 + int(index)


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
    # Logical annotation identity is distinct from the shared physical root.
    # Upstream mapping authorizes OLET/PAD sharing; preserve every accepted row.
    weld_number: str = ""
    weld_type: str = ""


@dataclass
class CalloutPlacement:
    center_x: float
    center_y: float
    leader_x: float
    leader_y: float
    number: str
    score: float = 0.0
    marker_radius: float = 0.0


@dataclass(frozen=True)
class LayoutRegion:
    """Physical frame and the smaller area available to ISO annotations."""

    outer: tuple[float, float, float, float]
    drawing: tuple[float, float, float, float]
    source: str


@dataclass(frozen=True)
class PlacementCandidate:
    placement: CalloutPlacement
    marker_box: tuple[float, float, float, float]
    remote_source_clear: bool = True
    source_line_crossings: int = 0
    source_text_crossings: int = 0


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


def segment_distance(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    if segments_intersect(first, second):
        return 0.0
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    return min(
        point_segment_distance(ax1, ay1, bx1, by1, bx2, by2),
        point_segment_distance(ax2, ay2, bx1, by1, bx2, by2),
        point_segment_distance(bx1, by1, ax1, ay1, ax2, ay2),
        point_segment_distance(bx2, by2, ax1, ay1, ax2, ay2),
    )


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
        if re.fullmatch(r"Sheet\d+", name)
        and name != "Sheet221"
        # Some one-page Chengda SHA files keep nearly all drawable objects in
        # the shared Sheet221 stream and leave only a compact page descriptor
        # in Sheet6. An eight-byte stream is still an unused placeholder, but
        # a compact Sheet6 with real text records is a valid physical page.
        and (len(data) > 1024 or (name == "Sheet6" and bool(text_records(data))))
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
    dynamic = forward_owned_dynamic_graphics(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    graphics: dict[int, list[str]] = defaultdict(list)
    for uci, records in dynamic.items():
        for record in records:
            graphics[int(record["graphic_ref"])].append(str(uci))

    # A Sheet ellipse is direct weld-dot evidence.  PSM low-16 reference
    # collisions can return a large component/container envelope for the
    # same ref, so no PSM-size gate is applied here.
    global_regions = [
        (graphic_ref, str(ucis[0]))
        for graphic_ref, ucis in graphics.items()
    ]

    result: dict[str, list[PointTarget]] = {}
    template_sheet = streams.get("Sheet221", b"")
    for target in targets:
        geometry_data = target.data
        anchors = ellipse_anchors(geometry_data)
        # In the compact-Sheet6 variant, dynamic UCI owners and their actual
        # Shape2D anchors live in Sheet221. Limit the fallback to a single
        # physical page so a shared template can never be assigned to the
        # wrong page in a multi-sheet container.
        if len(targets) == 1 and not (set(anchors) & set(graphics)) and template_sheet:
            template_anchors = ellipse_anchors(template_sheet)
            if set(template_anchors) & set(graphics):
                geometry_data = template_sheet
                anchors = template_anchors
        segments = line_segments(geometry_data) + template_line_segments(geometry_data) + composite_segments(geometry_data)
        # Use the Shape2D ellipse anchor for a weld/connection dot whenever
        # present. PSM describes its rendered envelope, not necessarily the
        # plotted black-dot centre.
        visible_points: list[PointTarget] = []
        for graphic_ref, uci in global_regions:
            # PSM regions are global to the SHA container and can appear near
            # unrelated dimension geometry on a different page.  A weld is
            # page-visible only when its actual Shape2D ellipse/dot primitive
            # is present in this Sheet stream.
            if graphic_ref not in anchors:
                continue
            center_x, center_y = anchors[graphic_ref]
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
    fallback_styles = style_fallbacks(sheet, psm)
    # Project templates vary. Prefer the calibrated 80X25 record and then any
    # existing five-character record.  A few valid ISO pages contain no
    # five-character text at all, however.  Shape2D text records have a stable
    # 24-byte prefix and 32-byte transform suffix, so a record of another
    # length can be normalised safely to the five-character synthetic format.
    candidates = [record for record in records if str(record["text"]).strip() == TEXT_TEMPLATE]
    candidates.extend(record for record in records if len(str(record["text"]).strip()) == 5 and record not in candidates)
    candidates.extend(
        record
        for record in records
        if record not in candidates
        and int(record["style_ref"]) in fallback_styles
    )
    for record in candidates:
        bbox = psm_bbox(psm, int(record["graphic_ref"]))
        if bbox is None:
            continue
        text_start = int(record.get("text_start", -1))
        text_bytes = str(record["text"]).encode("utf-16le")
        text_end = text_start + len(text_bytes)
        if text_start < 24 or text_end + 32 > len(sheet):
            continue
        raw = (
            sheet[text_start - 24 : text_start]
            + TEXT_NUMBER_TEMPLATE.encode("utf-16le")
            + sheet[text_end : text_end + 32]
        )
        if len(raw) != 66:
            continue
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


def boxes_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    tolerance: float = 1e-6,
) -> bool:
    left, bottom, right, top = first
    other_left, other_bottom, other_right, other_top = second
    return not (
        right <= other_left + tolerance
        or left >= other_right - tolerance
        or top <= other_bottom + tolerance
        or bottom >= other_top - tolerance
    )


def point_in_box(
    x: float,
    y: float,
    box: tuple[float, float, float, float],
    tolerance: float = 1e-6,
) -> bool:
    left, bottom, right, top = box
    return left - tolerance <= x <= right + tolerance and bottom - tolerance <= y <= top + tolerance


def orientation(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def segments_intersect(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    tolerance: float = 1e-6,
) -> bool:
    """Return True for proper, touching, or collinear segment intersections."""

    ax, ay, bx, by = first
    cx, cy, dx, dy = second
    if (
        max(ax, bx) < min(cx, dx) - tolerance
        or max(cx, dx) < min(ax, bx) - tolerance
        or max(ay, by) < min(cy, dy) - tolerance
        or max(cy, dy) < min(ay, by) - tolerance
    ):
        return False
    values = (
        orientation(ax, ay, bx, by, cx, cy),
        orientation(ax, ay, bx, by, dx, dy),
        orientation(cx, cy, dx, dy, ax, ay),
        orientation(cx, cy, dx, dy, bx, by),
    )
    first_a, first_b, second_a, second_b = values
    if first_a * first_b < -tolerance and second_a * second_b < -tolerance:
        return True
    def on_segment(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> bool:
        return (
            min(px, rx) - tolerance <= qx <= max(px, rx) + tolerance
            and min(py, ry) - tolerance <= qy <= max(py, ry) + tolerance
        )

    return (
        abs(first_a) <= tolerance and on_segment(ax, ay, cx, cy, bx, by)
        or abs(first_b) <= tolerance and on_segment(ax, ay, dx, dy, bx, by)
        or abs(second_a) <= tolerance and on_segment(cx, cy, ax, ay, dx, dy)
        or abs(second_b) <= tolerance and on_segment(cx, cy, bx, by, dx, dy)
    )


def segment_intersects_box(
    segment: tuple[float, float, float, float],
    box: tuple[float, float, float, float],
) -> bool:
    x1, y1, x2, y2 = segment
    if point_in_box(x1, y1, box) or point_in_box(x2, y2, box):
        return True
    left, bottom, right, top = box
    return any(
        segments_intersect(segment, edge)
        for edge in (
            (left, bottom, right, bottom),
            (right, bottom, right, top),
            (right, top, left, top),
            (left, top, left, bottom),
        )
    )


def truncate_segment_start(
    segment: tuple[float, float, float, float], distance: float
) -> tuple[float, float, float, float] | None:
    x1, y1, x2, y2 = segment
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= distance + 1e-6:
        return None
    scale = distance / length
    return (
        x1 + (x2 - x1) * scale,
        y1 + (y2 - y1) * scale,
        x2,
        y2,
    )


def infer_layout_region(
    template_data: bytes,
    fallback_viewbox: tuple[float, float, float, float],
) -> LayoutRegion:
    """Infer the frame and report-free drawing area from the backing sheet.

    ISOGEN backing sheets keep the outer frame and long material/title-panel
    divider in ``Sheet221``.  A renderer viewbox is intentionally not used as
    the authority because it may include text or symbols outside the frame.
    """

    decoded = template_line_segments(template_data)
    page_segments = [
        (x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
        for x1, y1, x2, y2, _, _ in decoded
    ]
    axis_tolerance = 8.0
    horizontal = [
        segment
        for segment in page_segments
        if abs(segment[1] - segment[3]) <= axis_tolerance
    ]
    vertical = [
        segment
        for segment in page_segments
        if abs(segment[0] - segment[2]) <= axis_tolerance
    ]
    if horizontal and vertical:
        max_horizontal = max(abs(x2 - x1) for x1, _, x2, _ in horizontal)
        max_vertical = max(abs(y2 - y1) for _, y1, _, y2 in vertical)
        frame_horizontal = [
            segment for segment in horizontal if abs(segment[2] - segment[0]) >= 0.85 * max_horizontal
        ]
        frame_vertical = [
            segment for segment in vertical if abs(segment[3] - segment[1]) >= 0.85 * max_vertical
        ]
    else:
        frame_horizontal = []
        frame_vertical = []
    if len(frame_horizontal) >= 2 and len(frame_vertical) >= 2:
        left = max(min(min(x1, x2) for x1, _, x2, _ in frame_horizontal), min(x1 for x1, _, _, _ in frame_vertical))
        right = min(max(max(x1, x2) for x1, _, x2, _ in frame_horizontal), max(x1 for x1, _, _, _ in frame_vertical))
        bottom = max(min(y1 for _, y1, _, _ in frame_horizontal), min(min(y1, y2) for _, y1, _, y2 in frame_vertical))
        top = min(max(y1 for _, y1, _, _ in frame_horizontal), max(max(y1, y2) for _, y1, _, y2 in frame_vertical))
        outer = (left, bottom, right, top)
        source = "Sheet221 outer frame"
    else:
        view_x, view_y, view_width, view_height = fallback_viewbox
        outer = (view_x, view_y, view_x + view_width, view_y + view_height)
        source = "fallback viewbox"

    left, bottom, right, top = outer
    width = right - left
    height = top - bottom
    drawing = outer

    # A long interior divider with a 15-45% smaller side is the material,
    # weld-list, or title panel boundary. Retain the larger ISO drawing side.
    divider_candidates: list[tuple[float, str, float]] = []
    for x1, y1, _, y2 in vertical:
        x = x1
        span = abs(y2 - y1)
        if not left + 0.08 * width < x < right - 0.08 * width or span < 0.75 * height:
            continue
        smaller_fraction = min(x - left, right - x) / width
        if 0.15 <= smaller_fraction <= 0.45:
            divider_candidates.append((span / height, "vertical", x))
    for x1, y1, x2, _ in horizontal:
        y = y1
        span = abs(x2 - x1)
        if not bottom + 0.08 * height < y < top - 0.08 * height or span < 0.75 * width:
            continue
        smaller_fraction = min(y - bottom, top - y) / height
        if 0.15 <= smaller_fraction <= 0.45:
            divider_candidates.append((span / width, "horizontal", y))
    if divider_candidates:
        _, direction, coordinate = max(divider_candidates, key=lambda item: item[0])
        if direction == "vertical":
            drawing = (left, bottom, coordinate, top) if coordinate - left >= right - coordinate else (coordinate, bottom, right, top)
        else:
            drawing = (left, coordinate, right, top) if top - coordinate >= coordinate - bottom else (left, bottom, right, coordinate)
        source += f" + {direction} report divider"
    return LayoutRegion(outer=outer, drawing=drawing, source=source)


def marker_edge(
    source_x: float,
    source_y: float,
    center_x: float,
    center_y: float,
    marker_shape: str,
    marker_radius: float,
    marker_width: float | None = None,
    marker_height: float | None = None,
) -> tuple[float, float]:
    delta_x = source_x - center_x
    delta_y = source_y - center_y
    if abs(delta_x) + abs(delta_y) <= 1e-6:
        raise ValueError("Callout marker centre coincides with its weld point")
    if marker_shape == "rectangle" and marker_width and marker_height:
        scales = []
        if abs(delta_x) > 1e-6:
            scales.append((marker_width / 2.0) / abs(delta_x))
        if abs(delta_y) > 1e-6:
            scales.append((marker_height / 2.0) / abs(delta_y))
        scale = min(scales)
        return center_x + delta_x * scale, center_y + delta_y * scale
    denominator = math.hypot(delta_x, delta_y) if marker_shape == "circle" else abs(delta_x) + abs(delta_y)
    scale = marker_radius / denominator
    return center_x + delta_x * scale, center_y + delta_y * scale


def leader_clear_of_source(
    leader: tuple[float, float, float, float],
    source_segments: list[tuple[float, float, float, float, int, int]],
    source_boxes: list[tuple[float, float, float, float]],
    root_clearance: float = 700.0,
) -> bool:
    visible_leader = truncate_segment_start(leader, root_clearance)
    if visible_leader is None:
        return True
    vx1, vy1, vx2, vy2 = visible_leader
    leader_bounds = (min(vx1, vx2), min(vy1, vy2), max(vx1, vx2), max(vy1, vy2))
    for x1, y1, x2, y2, _, _ in source_segments:
        source = (x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
        sx1, sy1, sx2, sy2 = source
        source_bounds = (min(sx1, sx2), min(sy1, sy2), max(sx1, sx2), max(sy1, sy2))
        if not boxes_overlap(leader_bounds, source_bounds, tolerance=-1.0):
            continue
        if segments_intersect(visible_leader, source):
            return False
    return not any(
        boxes_overlap(leader_bounds, box, tolerance=-1.0) and segment_intersects_box(visible_leader, box)
        for box in source_boxes
    )


def leader_source_crossing_counts(
    leader: tuple[float, float, float, float],
    source_segments: list[tuple[float, float, float, float, int, int]],
    source_boxes: list[tuple[float, float, float, float]],
    root_clearance: float = 20.0,
) -> tuple[int, int]:
    """Count visible line/text obstacles crossed beyond the weld symbol itself.

    A dense fitting can enclose its weld dot with several local strokes, so a
    binary clear/not-clear score sends a callout through a much busier remote
    lane merely to avoid one nearby stroke.  Counting crossings makes one
    unavoidable local exit cheaper than crossing several dimensions or text
    labels.  Coordinate keys prevent duplicate decoded primitives from
    overstating the same visible obstacle.
    """

    visible_leader = truncate_segment_start(leader, root_clearance)
    if visible_leader is None:
        return 0, 0
    vx1, vy1, vx2, vy2 = visible_leader
    leader_bounds = (min(vx1, vx2), min(vy1, vy2), max(vx1, vx2), max(vy1, vy2))
    crossed_lines: set[tuple[int, int, int, int]] = set()
    for x1, y1, x2, y2, _, _ in source_segments:
        source = (x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
        sx1, sy1, sx2, sy2 = source
        source_bounds = (min(sx1, sx2), min(sy1, sy2), max(sx1, sx2), max(sy1, sy2))
        if not boxes_overlap(leader_bounds, source_bounds, tolerance=-1.0):
            continue
        if segments_intersect(visible_leader, source):
            first = (round(sx1), round(sy1))
            second = (round(sx2), round(sy2))
            crossed_lines.add((*first, *second) if first <= second else (*second, *first))
    crossed_boxes = {
        tuple(round(value) for value in box)
        for box in source_boxes
        if boxes_overlap(leader_bounds, box, tolerance=-1.0)
        and segment_intersects_box(visible_leader, box)
    }
    return len(crossed_lines), len(crossed_boxes)


def leader_corridor_congestion(
    leader: tuple[float, float, float, float],
    source_segments: list[tuple[float, float, float, float, int, int]],
    root_clearance: float = 45.0,
    corridor_clearance: float = SOURCE_CORRIDOR_CLEARANCE,
) -> float:
    """Measure geometry close enough to make a leader look crossed/merged.

    Exact intersection tests miss the common bad case where a leader follows
    an elbow, pipe or dimension line at almost the same angle.  This returns
    a continuous congestion value: 1.0 for contact, tapering to zero at the
    edge of the visual clearance corridor.
    """

    visible_leader = truncate_segment_start(leader, root_clearance)
    if visible_leader is None:
        return 0.0
    unique_segments: dict[tuple[int, int, int, int], tuple[float, float, float, float]] = {}
    for x1, y1, x2, y2, _, _ in source_segments:
        source = (x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
        first = (round(source[0]), round(source[1]))
        second = (round(source[2]), round(source[3]))
        key = (*first, *second) if first <= second else (*second, *first)
        unique_segments.setdefault(key, source)
    congestion = 0.0
    for source in unique_segments.values():
        distance = segment_distance(visible_leader, source)
        if distance < corridor_clearance:
            congestion += (corridor_clearance - distance) / corridor_clearance
    return congestion


def arc_obstacle_segments(
    data: bytes,
    connected_to: list[tuple[float, float, float, float, int, int]],
) -> list[tuple[float, float, float, float, int, int]]:
    """Approximate the elbow arcs actually rendered by the SHA renderer."""

    result: list[tuple[float, float, float, float, int, int]] = []
    for arc in standalone_arcs(data):
        if not standalone_arc_is_connected(arc, connected_to):
            continue
        center_x = float(arc["center_x"])
        center_y = float(arc["center_y"])
        radius = float(arc["radius"])
        start = float(arc["start_angle"])
        end = float(arc["end_angle"])
        direction = int(arc["direction"])
        delta = (start - end) % math.tau if direction == 0 else (end - start) % math.tau
        signed_delta = -delta if direction == 0 else delta
        steps = max(4, math.ceil(delta / math.radians(7.5)))
        points = [
            (
                (center_x + radius * math.cos(start + signed_delta * step / steps)) / SHEET_UNIT,
                (center_y + radius * math.sin(start + signed_delta * step / steps)) / SHEET_UNIT,
            )
            for step in range(steps + 1)
        ]
        owner_ref = int(arc["owner_ref"])
        primitive_ref = int(arc["primitive_ref"])
        result.extend(
            (*points[index], *points[index + 1], owner_ref, primitive_ref)
            for index in range(steps)
        )
    return result


def directional_sector_congestion(
    point: PointTarget,
    direction: tuple[float, float],
    source_segments: list[tuple[float, float, float, float, int, int]],
) -> float:
    """Measure visible drawing mass in the proposed leader's forward sector.

    A centroid vector can cancel two occupied directions and incorrectly call
    their bisector empty. Sampling a finite angular sector instead rewards a
    genuinely open wedge and works for elbows, tees and dense dimensions.
    """

    threshold = math.cos(DIRECTIONAL_SECTOR_HALF_ANGLE)
    congestion = 0.0
    seen: set[tuple[int, int, int, int]] = set()
    for x1, y1, x2, y2, _, _ in source_segments:
        ax, ay = x1 * SHEET_UNIT, y1 * SHEET_UNIT
        bx, by = x2 * SHEET_UNIT, y2 * SHEET_UNIT
        first = (round(ax), round(ay))
        second = (round(bx), round(by))
        key = (*first, *second) if first <= second else (*second, *first)
        if key in seen:
            continue
        seen.add(key)
        length = math.hypot(bx - ax, by - ay)
        length_weight = min(1.0, length / 300.0)
        for fraction in (0.25, 0.5, 0.75):
            sample_x = ax + (bx - ax) * fraction
            sample_y = ay + (by - ay) * fraction
            offset_x = sample_x - point.center_x
            offset_y = sample_y - point.center_y
            distance = math.hypot(offset_x, offset_y)
            if distance < 90.0 or distance > LOCAL_GEOMETRY_RADIUS:
                continue
            alignment = (offset_x * direction[0] + offset_y * direction[1]) / distance
            if alignment <= threshold:
                continue
            angular_weight = (alignment - threshold) / (1.0 - threshold)
            distance_weight = 1.0 - distance / LOCAL_GEOMETRY_RADIUS
            congestion += angular_weight * distance_weight * length_weight / 3.0
    return congestion


def peer_root_direction_congestion(
    point: PointTarget,
    direction: tuple[float, float],
    peer_points: tuple[PointTarget, ...],
) -> float:
    """Penalize leaders aimed into a nearby cluster of other weld roots."""

    congestion = 0.0
    threshold = math.cos(math.radians(65.0))
    for peer in peer_points:
        if peer is point:
            continue
        offset_x = peer.center_x - point.center_x
        offset_y = peer.center_y - point.center_y
        distance = math.hypot(offset_x, offset_y)
        if distance <= 1e-6 or distance > PEER_ROOT_RADIUS:
            continue
        alignment = (offset_x * direction[0] + offset_y * direction[1]) / distance
        if alignment <= threshold:
            continue
        angular_weight = (alignment - threshold) / (1.0 - threshold)
        congestion += angular_weight * (1.0 - distance / PEER_ROOT_RADIUS)
    return congestion


BEST_EFFORT_GRID_SIZE = 500.0
_BEST_EFFORT_GRID_CACHE: dict[
    tuple[int, int, int],
    tuple[
        dict[tuple[int, int], set[int]],
        dict[tuple[int, int], set[int]],
        dict[tuple[int, int], set[int]],
    ],
] = {}


def _grid_cells(box: tuple[float, float, float, float]) -> tuple[tuple[int, int], ...]:
    left, bottom, right, top = box
    return tuple(
        (x, y)
        for x in range(math.floor(left / BEST_EFFORT_GRID_SIZE), math.floor(right / BEST_EFFORT_GRID_SIZE) + 1)
        for y in range(math.floor(bottom / BEST_EFFORT_GRID_SIZE), math.floor(top / BEST_EFFORT_GRID_SIZE) + 1)
    )


def _build_box_grid(
    boxes: list[tuple[float, float, float, float]],
) -> dict[tuple[int, int], set[int]]:
    grid: dict[tuple[int, int], set[int]] = defaultdict(set)
    for index, box in enumerate(boxes):
        for cell in _grid_cells(box):
            grid[cell].add(index)
    return grid


def _build_segment_grid(
    segments: list[tuple[float, float, float, float, int, int]],
) -> dict[tuple[int, int], set[int]]:
    boxes = [
        (
            min(x1, x2) * SHEET_UNIT,
            min(y1, y2) * SHEET_UNIT,
            max(x1, x2) * SHEET_UNIT,
            max(y1, y2) * SHEET_UNIT,
        )
        for x1, y1, x2, y2, _, _ in segments
    ]
    return _build_box_grid(boxes)


def _grid_indexes(
    grid: dict[tuple[int, int], set[int]],
    box: tuple[float, float, float, float],
) -> set[int]:
    result: set[int] = set()
    for cell in _grid_cells(box):
        result.update(grid.get(cell, ()))
    return result


def candidate_options(
    point: PointTarget,
    source_segments: list[tuple[float, float, float, float, int, int]],
    source_boxes: list[tuple[float, float, float, float]],
    leader_boxes: list[tuple[float, float, float, float]],
    region: LayoutRegion,
    marker_radius: float,
    gap: float,
    normal: tuple[float, float],
    marker_shape: str,
    strict_normal: bool = False,
    preferred_side: int | None = None,
    max_candidates: int = 48,
    fine_search: bool = False,
    root_clearance: float = 700.0,
    semantic_boxes: tuple[tuple[float, float, float, float], ...] = (),
    peer_points: tuple[PointTarget, ...] = (),
    marker_width: float | None = None,
    marker_height: float | None = None,
    best_effort: bool = False,
    allow_fine_fallback: bool = True,
) -> list[PlacementCandidate]:
    min_x, min_y, max_x, max_y = region.drawing
    normal_angle = math.atan2(normal[1], normal[0])
    if strict_normal:
        direction_specs = [(preferred_side or side, 0.0) for side in ((preferred_side,) if preferred_side else (1, -1))]
    else:
        if best_effort:
            # Dense pages need enough angular diversity to separate many
            # nearby roots.  The spatial obstacle grids keep this wider
            # inventory inexpensive; the former 0/+/-45-degree set forced
            # otherwise avoidable marker/leader conflicts on crowded pages.
            angles = (15.0, 30.0, 45.0, 60.0, 75.0)
        else:
            angles = (
                tuple(step * 2.5 for step in range(1, 36))
                if fine_search
                else (7.5, 15.0, 22.5, 30.0, 37.5, 45.0, 52.5, 60.0, 67.5, 75.0, 82.5)
            )
        deviations = (0.0,) + tuple(
            signed * angle for angle in angles for signed in (1, -1)
        )
        sides = (preferred_side,) if preferred_side else (1, -1)
        direction_specs = [(side, math.radians(deviation)) for side in sides for deviation in deviations]

    options: list[PlacementCandidate] = []
    rejected: dict[str, int] = defaultdict(int)
    seen: set[tuple[int, int]] = set()
    sector_congestion_by_direction: dict[tuple[int, int], float] = {}
    segment_grid = text_grid = leader_text_grid = None
    if best_effort:
        cache_key = (id(source_segments), id(source_boxes), id(leader_boxes))
        cached = _BEST_EFFORT_GRID_CACHE.get(cache_key)
        if cached is None:
            cached = (
                _build_segment_grid(source_segments),
                _build_box_grid(source_boxes),
                _build_box_grid(leader_boxes),
            )
            _BEST_EFFORT_GRID_CACHE[cache_key] = cached
        segment_grid, text_grid, leader_text_grid = cached
    radial_steps = (
        (700, 1100, 1600, 2200, 3000, 4000, 5000)
        if best_effort
        else (range(350, 6501, 50) if fine_search else range(400, 5201, 100))
    )
    for radius in radial_steps:
        for side, deviation in direction_specs:
            angle = normal_angle + (0 if side == 1 else math.pi) + deviation
            dx, dy = math.cos(angle), math.sin(angle)
            center_x = point.center_x + dx * radius
            center_y = point.center_y + dy * radius
            half_width = marker_width / 2.0 if marker_width is not None else marker_radius
            half_height = marker_height / 2.0 if marker_height is not None else marker_radius
            marker_box = (
                center_x - half_width - gap,
                center_y - half_height - gap,
                center_x + half_width + gap,
                center_y + half_height + gap,
            )
            source_gap = min(gap, SOURCE_MARKER_CLEARANCE)
            source_marker_box = (
                center_x - half_width - source_gap,
                center_y - half_height - source_gap,
                center_x + half_width + source_gap,
                center_y + half_height + source_gap,
            )
            if (
                marker_box[0] < min_x
                or marker_box[1] < min_y
                or marker_box[2] > max_x
                or marker_box[3] > max_y
            ):
                rejected["drawing-boundary"] += 1
                continue
            key = (round(center_x), round(center_y))
            marker_text_boxes = (
                [source_boxes[index] for index in _grid_indexes(text_grid, source_marker_box)]
                if best_effort and text_grid is not None
                else source_boxes
            )
            text_overlap_count = (
                int(any(boxes_overlap(source_marker_box, obstacle) for obstacle in marker_text_boxes))
                if best_effort
                else sum(
                    boxes_overlap(source_marker_box, obstacle)
                    for obstacle in source_boxes
                )
            )
            if key in seen or (text_overlap_count and not best_effort):
                rejected["marker-text"] += 1
                continue
            marker_segments = (
                [source_segments[index] for index in _grid_indexes(segment_grid, source_marker_box)]
                if best_effort and segment_grid is not None
                else source_segments
            )
            geometry_tests = (
                segment_intersects_box(
                    (x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT),
                    source_marker_box,
                )
                for x1, y1, x2, y2, _, _ in marker_segments
            )
            geometry_overlap_count = (
                int(any(geometry_tests)) if best_effort else sum(geometry_tests)
            )
            if geometry_overlap_count and not best_effort:
                rejected["marker-geometry"] += 1
                continue
            leader_x, leader_y = marker_edge(
                point.center_x,
                point.center_y,
                center_x,
                center_y,
                marker_shape,
                marker_radius,
                marker_width,
                marker_height,
            )
            leader = (point.center_x, point.center_y, leader_x, leader_y)
            leader_bounds = (
                min(leader[0], leader[2]),
                min(leader[1], leader[3]),
                max(leader[0], leader[2]),
                max(leader[1], leader[3]),
            )
            candidate_leader_segments = (
                [source_segments[index] for index in _grid_indexes(segment_grid, leader_bounds)]
                if best_effort and segment_grid is not None
                else source_segments
            )
            candidate_leader_boxes = (
                [leader_boxes[index] for index in _grid_indexes(leader_text_grid, leader_bounds)]
                if best_effort and leader_text_grid is not None
                else leader_boxes
            )
            strict_leader_clear = leader_clear_of_source(
                leader,
                candidate_leader_segments,
                candidate_leader_boxes,
                root_clearance=root_clearance,
            )
            if not strict_leader_clear and not best_effort:
                rejected["leader-source"] += 1
                continue
            # The broad root exemption above is needed for welds embedded in
            # dense tee/flange symbols, but it used to make every short leader
            # look equally clear. Re-evaluate with only a small root allowance
            # and strongly prefer a genuinely blank lane whenever one exists.
            remote_source_clear = (
                strict_leader_clear
                if best_effort
                else leader_clear_of_source(
                    leader,
                    source_segments,
                    leader_boxes,
                    root_clearance=PREFERRED_ROOT_CLEARANCE,
                )
            )
            avoidable_crossing_penalty = (
                0.0 if remote_source_clear else AVOIDABLE_SOURCE_CROSSING_PENALTY
            )
            crossed_lines, crossed_text_boxes = (
                (0, 0)
                if best_effort
                else leader_source_crossing_counts(
                    leader,
                    source_segments,
                    leader_boxes,
                    # Fitting, elbow and weld-symbol strokes immediately around
                    # the root are unavoidable exit geometry, not routing
                    # obstacles. Collinear/near-parallel overlap is assessed
                    # separately by the corridor metric below.
                    root_clearance=PREFERRED_ROOT_CLEARANCE,
                )
            )
            crossing_count_penalty = (
                crossed_lines * SOURCE_LINE_CROSSING_PENALTY
                + crossed_text_boxes * SOURCE_TEXT_CROSSING_PENALTY
            )
            corridor_penalty = (
                0.0
                if best_effort
                else leader_corridor_congestion(leader, source_segments)
                * SOURCE_CORRIDOR_PENALTY
            )
            direction_ratio = min(abs(dx), abs(dy)) / max(abs(dx), abs(dy), 1e-9)
            cardinal_collapse = max(
                0.0,
                (CONGESTED_CARDINAL_RATIO - direction_ratio) / CONGESTED_CARDINAL_RATIO,
            )
            congested_cardinal_penalty = (
                cardinal_collapse
                * min(1.0, corridor_penalty / (2.0 * SOURCE_CORRIDOR_PENALTY))
                * CONGESTED_CARDINAL_PENALTY
            )
            direction_key = (round(dx * 10000), round(dy * 10000))
            sector_congestion = 0.0 if best_effort else sector_congestion_by_direction.get(direction_key)
            if sector_congestion is None:
                sector_congestion = directional_sector_congestion(
                    point, (dx, dy), source_segments
                )
                sector_congestion_by_direction[direction_key] = sector_congestion
            sector_penalty = sector_congestion * DIRECTIONAL_SECTOR_PENALTY
            peer_penalty = (
                peer_root_direction_congestion(point, (dx, dy), peer_points)
                * PEER_ROOT_DIRECTION_PENALTY
            )
            angular_penalty = abs(math.degrees(deviation)) * 7.0
            side_penalty = 0.0 if preferred_side is None or side == preferred_side else 800.0
            edge_clearance = min(
                marker_box[0] - min_x,
                marker_box[1] - min_y,
                max_x - marker_box[2],
                max_y - marker_box[3],
            )
            edge_penalty = max(0.0, 180.0 - edge_clearance) * 1.5
            # Boxed source material/component numbers are semantically easy
            # to confuse with weld numbers. Prefer candidates outside their
            # vicinity, but keep this as a cost rather than a rejection so a
            # dense page never loses all otherwise valid straight leaders.
            semantic_penalty = 5000.0 * sum(
                boxes_overlap(
                    marker_box,
                    (
                        box[0] - 85.0,
                        box[1] - 85.0,
                        box[2] + 85.0,
                        box[3] + 85.0,
                    ),
                )
                for box in semantic_boxes
            )
            placement = CalloutPlacement(
                center_x,
                center_y,
                leader_x,
                leader_y,
                TEXT_NUMBER_TEMPLATE,
                score=(
                    radius
                    + angular_penalty
                    + side_penalty
                    + edge_penalty
                    + semantic_penalty
                    + avoidable_crossing_penalty
                    + crossing_count_penalty
                    + corridor_penalty
                    + congested_cardinal_penalty
                    + sector_penalty
                    + peer_penalty
                    + text_overlap_count * 2_000_000.0
                    + geometry_overlap_count * 3_000_000.0
                    + (0.0 if strict_leader_clear else 4_000_000.0)
                ),
                marker_radius=marker_radius,
            )
            options.append(
                PlacementCandidate(
                    placement=placement,
                    marker_box=marker_box,
                    remote_source_clear=remote_source_clear,
                    source_line_crossings=crossed_lines,
                    source_text_crossings=crossed_text_boxes,
                )
            )
            seen.add(key)
    options.sort(key=lambda candidate: candidate.placement.score)
    if (
        allow_fine_fallback
        and 0 < len(options) < 8
        and not fine_search
        and not strict_normal
    ):
        # A handful of coarse candidates can still be pairwise unusable for
        # adjacent welds. The fine grid is a superset of the coarse angular
        # and radial lattice, so replace the sparse set before global solving.
        return candidate_options(
            point=point,
            source_segments=source_segments,
            source_boxes=source_boxes,
            leader_boxes=leader_boxes,
            region=region,
            marker_radius=marker_radius,
            gap=gap,
            normal=normal,
            marker_shape=marker_shape,
            strict_normal=strict_normal,
            preferred_side=preferred_side,
            max_candidates=max_candidates,
            fine_search=True,
            root_clearance=root_clearance,
            semantic_boxes=semantic_boxes,
            peer_points=peer_points,
            marker_width=marker_width,
            marker_height=marker_height,
            best_effort=best_effort,
            allow_fine_fallback=allow_fine_fallback,
        )
    if allow_fine_fallback and not options and not fine_search and not strict_normal:
        return candidate_options(
            point=point,
            source_segments=source_segments,
            source_boxes=source_boxes,
            leader_boxes=leader_boxes,
            region=region,
            marker_radius=marker_radius,
            gap=gap,
            normal=normal,
            marker_shape=marker_shape,
            strict_normal=strict_normal,
            preferred_side=preferred_side,
            max_candidates=max_candidates,
            fine_search=True,
            root_clearance=root_clearance,
            semantic_boxes=semantic_boxes,
            peer_points=peer_points,
            marker_width=marker_width,
            marker_height=marker_height,
            best_effort=best_effort,
            allow_fine_fallback=allow_fine_fallback,
        )
    if fine_search and len(options) < 24 and root_clearance < 700.0:
        # Some weld dots sit inside a dense tee/valve/flange symbol. A straight
        # leader cannot leave that closed local envelope without crossing one
        # of the symbol's own strokes. Sparse fine-search results also leave
        # adjacent welds with no separable pair, so widen only the root-local
        # exemption; all geometry beyond it remains strictly protected.
        return candidate_options(
            point=point,
            source_segments=source_segments,
            source_boxes=source_boxes,
            leader_boxes=leader_boxes,
            region=region,
            marker_radius=marker_radius,
            gap=gap,
            normal=normal,
            marker_shape=marker_shape,
            strict_normal=strict_normal,
            preferred_side=preferred_side,
            max_candidates=max_candidates,
            fine_search=True,
            root_clearance=700.0,
            semantic_boxes=semantic_boxes,
            peer_points=peer_points,
            marker_width=marker_width,
            marker_height=marker_height,
            best_effort=best_effort,
            allow_fine_fallback=allow_fine_fallback,
        )
    if not options:
        print(
            f"No candidates after {'fine' if fine_search else 'coarse'} search "
            f"with root-clearance {root_clearance:.0f} "
            f"for page {point.page} weld-ref {point.graphic_ref} at "
            f"({point.center_x:.1f}, {point.center_y:.1f}); rejected={dict(rejected)}"
        )
    if len(options) <= max_candidates:
        return options
    # Keep short preferred candidates plus a deterministic sample of farther
    # lanes; dense pages often need one non-local move to remove crossings.
    head_count = max_candidates // 2
    selected = list(options[:head_count])
    remainder = options[head_count:]
    for index in range(max_candidates - head_count):
        position = round(index * (len(remainder) - 1) / max(1, max_candidates - head_count - 1))
        selected.append(remainder[position])
    return selected


def candidates_conflict(
    first_point: PointTarget,
    first: PlacementCandidate,
    second_point: PointTarget,
    second: PlacementCandidate,
) -> bool:
    return bool(
        candidate_conflict_reasons(
            first_point, first, second_point, second
        )
    )


def candidate_conflict_reasons(
    first_point: PointTarget,
    first: PlacementCandidate,
    second_point: PointTarget,
    second: PlacementCandidate,
) -> tuple[str, ...]:
    def marker_spacing_box(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        # Preserve 20 units between visible markers. The larger 60-unit box
        # is retained for frame and source-geometry clearance.
        left, bottom, right, top = box
        # Match the verifier's physical marker + 10-unit spacing envelope.
        # Candidate boxes include the 60-unit source clearance, so a 50-unit
        # inset removes only that excess without permitting visible overlap.
        inset = min(50.0, (right - left) * 0.15, (top - bottom) * 0.15)
        return left + inset, bottom + inset, right - inset, top - inset

    if boxes_overlap(marker_spacing_box(first.marker_box), marker_spacing_box(second.marker_box)):
        return ("marker-marker",)
    def physical_marker_box(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        # Candidate boxes carry the 60-unit marker-to-marker clearance. A
        # leader only needs to avoid the visible marker itself.
        left, bottom, right, top = box
        inset = min(60.0, (right - left) * 0.20, (top - bottom) * 0.20)
        return left + inset, bottom + inset, right - inset, top - inset

    first_leader = (
        first_point.center_x,
        first_point.center_y,
        first.placement.leader_x,
        first.placement.leader_y,
    )
    second_leader = (
        second_point.center_x,
        second_point.center_y,
        second.placement.leader_x,
        second.placement.leader_y,
    )
    reasons = []
    shared_root = math.hypot(
        first_point.center_x - second_point.center_x,
        first_point.center_y - second_point.center_y,
    ) <= 1.0
    if not shared_root and segments_intersect(first_leader, second_leader):
        reasons.append("leader-leader")
    if segment_intersects_box(
        first_leader, physical_marker_box(second.marker_box)
    ):
        reasons.append("first-leader-second-marker")
    if segment_intersects_box(
        second_leader, physical_marker_box(first.marker_box)
    ):
        reasons.append("second-leader-first-marker")
    return tuple(reasons)


def solve_page_layout(
    points: list[PointTarget],
    options_by_point: list[list[PlacementCandidate]],
    node_limit: int = 180_000,
    allow_conflicts: bool = False,
) -> list[CalloutPlacement]:
    """Choose one locally valid candidate per weld with zero page conflicts."""

    if len(points) != len(options_by_point):
        raise ValueError("Point/candidate count mismatch")
    empty = [index for index, options in enumerate(options_by_point) if not options]
    if empty:
        raise ValueError(f"No locally valid straight-leader candidate for weld indexes {empty}")

    count = len(points)
    full_domains = [(1 << len(options)) - 1 for options in options_by_point]
    compatible: list[list[list[int]]] = [
        [[full_domains[other] for other in range(count)] for _ in options]
        for options in options_by_point
    ]
    for first_index in range(count):
        for second_index in range(first_index + 1, count):
            for first_candidate, first in enumerate(options_by_point[first_index]):
                second_mask = 0
                for second_candidate, second in enumerate(options_by_point[second_index]):
                    if not candidates_conflict(
                        points[first_index], first, points[second_index], second
                    ):
                        second_mask |= 1 << second_candidate
                        compatible[second_index][second_candidate][first_index] |= 1 << first_candidate
                    else:
                        compatible[second_index][second_candidate][first_index] &= ~(1 << first_candidate)
                compatible[first_index][first_candidate][second_index] = second_mask

    impossible_pairs = [
        (first_index, second_index)
        for first_index in range(count)
        for second_index in range(first_index + 1, count)
        if not any(
            compatible[first_index][candidate][second_index]
            for candidate in range(len(options_by_point[first_index]))
        )
    ]
    if impossible_pairs:
        details = [
            (
                pair,
                points[pair[0]].graphic_ref,
                points[pair[1]].graphic_ref,
                len(options_by_point[pair[0]]),
                len(options_by_point[pair[1]]),
            )
            for pair in impossible_pairs
        ]
        print(f"Globally incompatible weld pairs: {details}")
        for first_index, second_index in impossible_pairs:
            reason_counts: dict[str, int] = defaultdict(int)
            for first in options_by_point[first_index]:
                for second in options_by_point[second_index]:
                    for reason in candidate_conflict_reasons(
                        points[first_index],
                        first,
                        points[second_index],
                        second,
                    ):
                        reason_counts[reason] += 1
            print(
                "Incompatible pair diagnostics: "
                f"refs=({points[first_index].graphic_ref}, "
                f"{points[second_index].graphic_ref}) "
                f"roots=(({points[first_index].center_x:.1f},"
                f"{points[first_index].center_y:.1f}),"
                f"({points[second_index].center_x:.1f},"
                f"{points[second_index].center_y:.1f})) "
                f"reason-counts={dict(reason_counts)}"
            )

    assigned = [-1] * count
    nodes = 0

    def search(current_domains: list[int], assigned_count: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > node_limit:
            return False
        if assigned_count == count:
            return True
        variable = min(
            (index for index in range(count) if assigned[index] < 0),
            key=lambda index: current_domains[index].bit_count(),
        )
        domain = current_domains[variable]
        values = [candidate for candidate in range(len(options_by_point[variable])) if domain & (1 << candidate)]
        for candidate in values:
            reduced = list(current_domains)
            reduced[variable] = 1 << candidate
            feasible = True
            for other in range(count):
                if other == variable or assigned[other] >= 0:
                    continue
                reduced[other] &= compatible[variable][candidate][other]
                if reduced[other] == 0:
                    feasible = False
                    break
            if not feasible:
                continue
            assigned[variable] = candidate
            if search(reduced, assigned_count + 1):
                return True
            assigned[variable] = -1
        return False

    # A best-effort retry follows a strict pass that has already proved the
    # page unsatisfiable.  Do not repeat the exponential proof here.
    solved = False if allow_conflicts else search(list(full_domains), 0)
    best_relaxed: tuple[tuple[int, int, float], list[int]] | None = None
    if not solved:
        # Deterministic min-conflicts fallback handles large dense components
        # without ever accepting a crossing as a valid final result.
        rng = random.Random(0x534841)
        # The best-effort path is used only after the strict layout failed.
        # With the spatially bounded 48-candidate inventory, a deeper
        # deterministic min-conflicts search is still cheap and materially
        # reduces avoidable clashes on very dense pages.
        restart_count = 8 if allow_conflicts else 24
        iteration_count = 1500 if allow_conflicts else 2500
        for restart in range(restart_count):
            chosen = [
                (restart * 7 + index * 11) % len(options)
                for index, options in enumerate(options_by_point)
            ]
            for _ in range(iteration_count):
                conflict_pairs = sum(
                    not (
                        compatible[first][chosen[first]][second]
                        & (1 << chosen[second])
                    )
                    for first in range(count)
                    for second in range(first + 1, count)
                )
                conflict_callouts = sum(
                    any(
                        other != index
                        and not (
                            compatible[index][chosen[index]][other]
                            & (1 << chosen[other])
                        )
                        for other in range(count)
                    )
                    for index in range(count)
                )
                relaxed_rank = (
                    conflict_pairs,
                    conflict_callouts,
                    sum(
                        options_by_point[index][candidate].placement.score
                        for index, candidate in enumerate(chosen)
                    ),
                )
                if best_relaxed is None or relaxed_rank < best_relaxed[0]:
                    best_relaxed = (relaxed_rank, list(chosen))
                bad = [
                    index
                    for index in range(count)
                    if any(
                        other != index
                        and not (compatible[index][chosen[index]][other] & (1 << chosen[other]))
                        for other in range(count)
                    )
                ]
                if not bad:
                    assigned = list(chosen)
                    solved = True
                    break
                variable = (
                    rng.choice(bad)
                    if allow_conflicts
                    else max(
                        bad,
                        key=lambda index: sum(
                            not (compatible[index][chosen[index]][other] & (1 << chosen[other]))
                            for other in range(count)
                            if other != index
                        ),
                    )
                )
                ranked = []
                for candidate in range(len(options_by_point[variable])):
                    conflict_count = sum(
                        not (compatible[variable][candidate][other] & (1 << chosen[other]))
                        for other in range(len(points))
                        if other != variable
                    )
                    ranked.append((conflict_count, options_by_point[variable][candidate].placement.score, candidate))
                best_count = min(item[0] for item in ranked)
                best = [item for item in ranked if item[0] == best_count]
                chosen[variable] = (
                    rng.choice(best)[2]
                    if allow_conflicts
                    else min(best, key=lambda item: (item[1], rng.random()))[2]
                )
            if solved:
                break
    if not solved:
        if allow_conflicts and best_relaxed is not None:
            assigned = best_relaxed[1]
            print(
                "Best-effort straight-leader layout used; "
                f"conflict-rank={best_relaxed[0]}"
            )
        else:
            raise ValueError(
                f"No zero-crossing global layout after {nodes} exact-search nodes and deterministic min-conflicts"
            )

    if allow_conflicts:
        # The refinements below assume an already conflict-free assignment
        # and search compatible single/pair moves.  Running them on an
        # intentionally minimum-conflict fallback is both invalid and cubic
        # in dense-page size; retain the audited min-conflicts result.
        return [
            options_by_point[index][candidate].placement
            for index, candidate in enumerate(assigned)
        ]

    # Improve local length/angle cost without changing feasibility.
    for variable in range(len(assigned)):
        current = assigned[variable]
        for candidate in range(current):
            if not any(
                other != variable
                and not (compatible[variable][candidate][other] & (1 << assigned[other]))
                for other in range(len(assigned))
            ):
                assigned[variable] = candidate
                break

    # A single-variable improvement can be trapped when a clear lane for one
    # weld is occupied by a neighbour's otherwise cheap marker. Allow a pair
    # to move together when their combined score decreases. The large
    # avoidable-crossing penalty makes a genuinely blank lane dominate while
    # preserving the zero-conflict constraints established above.
    for _ in range(count):
        improved = False
        for first in range(count):
            current_first = assigned[first]
            if (
                options_by_point[first][current_first].placement.score
                < AVOIDABLE_SOURCE_CROSSING_PENALTY
            ):
                continue
            best_move: tuple[float, int, int, int] | None = None
            for second in range(count):
                if second == first:
                    continue
                current_second = assigned[second]
                current_pair_score = (
                    options_by_point[first][current_first].placement.score
                    + options_by_point[second][current_second].placement.score
                )
                for first_candidate, first_option in enumerate(options_by_point[first]):
                    if first_option.placement.score >= current_pair_score:
                        break
                    if any(
                        other not in {first, second}
                        and not (
                            compatible[first][first_candidate][other]
                            & (1 << assigned[other])
                        )
                        for other in range(count)
                    ):
                        continue
                    for second_candidate, second_option in enumerate(options_by_point[second]):
                        pair_score = (
                            first_option.placement.score
                            + second_option.placement.score
                        )
                        if pair_score >= current_pair_score:
                            break
                        if not (
                            compatible[first][first_candidate][second]
                            & (1 << second_candidate)
                        ):
                            continue
                        if any(
                            other not in {first, second}
                            and not (
                                compatible[second][second_candidate][other]
                                & (1 << assigned[other])
                            )
                            for other in range(count)
                        ):
                            continue
                        gain = current_pair_score - pair_score
                        if best_move is None or gain > best_move[0]:
                            best_move = (
                                gain,
                                second,
                                first_candidate,
                                second_candidate,
                            )
            if best_move is not None:
                _, second, first_candidate, second_candidate = best_move
                assigned[first] = first_candidate
                assigned[second] = second_candidate
                improved = True
                break
        if not improved:
            break

    return [options_by_point[index][assigned[index]].placement for index in range(len(points))]


def solve_page_layout_in_preferred_bands(
    points: list[PointTarget],
    options_by_point: list[list[PlacementCandidate]],
    preferred_ranges: list[tuple[float, float]],
    node_limit: int = 180_000,
    layout_audit: dict[str, object] | None = None,
) -> list[CalloutPlacement]:
    """Solve zero-conflict layout in a normal leader-length band first.

    Uniform, readable leaders are preferable to globally shortest leaders.
    The search therefore admits only the normal band first and widens it in
    deterministic stages.  A very short or very long leader can only appear
    after the tighter stages have proved infeasible.
    """

    if len(points) != len(options_by_point) or len(points) != len(preferred_ranges):
        raise ValueError("Point/candidate/preferred-range count mismatch")

    stages: tuple[tuple[str, float | None, float | None], ...] = (
        ("preferred", 0.0, 0.0),
        ("near", 150.0, 250.0),
        ("expanded", 300.0, 650.0),
        ("wide", 500.0, 1400.0),
        ("unbounded", None, None),
    )
    last_error: ValueError | None = None
    for stage_name, shorten, extend in stages:
        staged: list[list[PlacementCandidate]] = []
        for point, options, (preferred_min, preferred_max) in zip(
            points, options_by_point, preferred_ranges
        ):
            if shorten is None or extend is None:
                selected = list(options)
            else:
                lower = max(0.0, preferred_min - shorten)
                upper = preferred_max + extend
                selected = [
                    option
                    for option in options
                    if lower
                    <= math.hypot(
                        option.placement.leader_x - point.center_x,
                        option.placement.leader_y - point.center_y,
                    )
                    <= upper
                ]
            staged.append(selected)
        if any(not options for options in staged):
            continue
        try:
            placements = solve_page_layout(
                points,
                staged,
                node_limit=node_limit,
                allow_conflicts=False,
            )
        except ValueError as exc:
            last_error = exc
            continue
        if layout_audit is not None:
            lengths = [
                math.hypot(
                    placement.leader_x - point.center_x,
                    placement.leader_y - point.center_y,
                )
                for point, placement in zip(points, placements)
            ]
            layout_audit.update(
                {
                    "leader_length_stage": stage_name,
                    "leader_length_min": round(min(lengths), 1) if lengths else 0.0,
                    "leader_length_max": round(max(lengths), 1) if lengths else 0.0,
                }
            )
        return placements
    raise ValueError(f"No zero-conflict layout in any leader-length band: {last_error}")


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
    """Return page-local text extents for marker and leader avoidance.

    PSM is container-global and one graphic reference can have envelopes from
    another physical page.  Prefer the current Sheet text anchor plus its
    measured style model; use a PSM box only when no local model is available.
    """

    boxes: list[tuple[float, float, float, float]] = []
    models = style_fallbacks(sheet, psm)
    for record in text_records(sheet):
        text = str(record["text"]).strip()
        x = float(record["x"])
        y = float(record["y"])
        style_ref = int(record["style_ref"])
        model = models.get(style_ref)
        if model is not None:
            offset_x, offset_y, font_height, char_width = model
            if (
                not all(
                    math.isfinite(value)
                    for value in (offset_x, offset_y, font_height, char_width)
                )
                or not (5.0 <= font_height <= 500.0)
                or not (0.5 <= char_width <= 500.0)
            ):
                # Misaligned printable-byte scans can produce an apparent
                # style such as 0xF0000000 with a 3072-unit font height.  It
                # is not visible Sheet text and must not become a page-sized
                # obstacle that makes every straight-leader lane impossible.
                model = None
        if text and model is not None and 0 <= x <= 1 and 0 <= y <= PAGE_HEIGHT / SHEET_UNIT:
            offset_x, offset_y, font_height, char_width = model
            width = max(font_height * 0.7, char_width * len(text))
            if style_ref in DIRECT_TEXT_ANCHOR_STYLES:
                left = x * SHEET_UNIT
                bottom = y * SHEET_UNIT - font_height * 0.20
            else:
                left = x * SHEET_UNIT + offset_x
                bottom = y * SHEET_UNIT + offset_y
            right = left + width
            top = bottom + font_height
        else:
            bbox = psm_bbox(psm, int(record["graphic_ref"]))
            if bbox is None:
                continue
            left, bottom, right, top = bbox
            # A container-global PSM fallback can resolve to a component or
            # another page's envelope. Such multi-thousand-unit rectangles
            # are not credible extents for one Sheet text record.
            if right - left > 2500 or top - bottom > 500:
                continue
        boxes.append((left - gap, bottom - gap, right + gap, top + gap))
    return boxes


def source_semantic_boxes(sheet: bytes) -> list[tuple[float, float, float, float]]:
    """Return small SHA frames whose numeric labels are source item tags."""

    rectangles = sheet_rectangles(line_segments(sheet) + template_line_segments(sheet))
    boxes: set[tuple[float, float, float, float]] = set()
    for record in text_records(sheet):
        text = str(record["text"]).strip()
        x, y = float(record["x"]), float(record["y"])
        if not re.fullmatch(r"\d{1,3}", text) or not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        anchor_x, anchor_y = x * SHEET_UNIT, y * SHEET_UNIT
        boxes.update(
            frame
            for frame in rectangles
            if frame[0] - 20 <= anchor_x <= frame[2] + 20
            and frame[1] - 20 <= anchor_y <= frame[3] + 20
            and frame[2] - frame[0] <= 400
            and frame[3] - frame[1] <= 250
        )
    return sorted(boxes)


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
    stored_number = number if len(number) > 5 else number.ljust(5)
    raw = bytearray(
        template.raw[:24]
        + stored_number.encode("utf-16le")
        + template.raw[34:]
    )
    struct.pack_into("<I", raw, 0, graphic_ref)
    struct.pack_into("<I", raw, 8, template.style_ref)
    visible_number = stored_number.rstrip() or stored_number
    text_scale = weld_label_scale(visible_number)
    width = max(style_model.font_height * 0.7, style_model.char_width * len(visible_number)) * text_scale
    height = style_model.font_height * text_scale
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
        anchor_x = (left - style_model.offset_x * text_scale) / SHEET_UNIT
        anchor_y = (bottom - style_model.offset_y * text_scale) / SHEET_UNIT
    transform_offset = 24 + len(stored_number.encode("utf-16le"))
    struct.pack_into("<dddd", raw, transform_offset, anchor_x, anchor_y, text_scale, 0.0)
    return bytes(raw)


def inject_into_sheet(
    target: SheetTarget,
    points: list[PointTarget],
    psm: bytes,
    template_data: bytes,
    label_start: int,
    diamond_radius: float,
    template: TextTemplate,
    shared_style: TextStyleModel | None,
    weld_number_by_point: dict[tuple[int, int, str], str] | None,
    weld_type_by_point: dict[tuple[int, int, str], str] | None,
    strict_normal: bool,
    lane_by_point: dict[tuple[int, int, str], dict[str, object]] | None = None,
    allow_text_overlap: bool = False,
    best_effort_layout: bool = False,
    inherited_viewbox: tuple[float, float, float, float] | None = None,
) -> tuple[bytes, int]:
    if not points:
        return target.data, label_start

    if best_effort_layout:
        print(f"{target.stream_name} page {target.page}: best-effort layout enabled")

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
    region = infer_layout_region(template_data, viewbox)
    # Match the renderer's visible line inventory exactly. Composite records
    # are hierarchy/geometry evidence but are not emitted as independent SVG
    # strokes; treating them as obstacles creates false blocked sectors.
    drawing_segments = line_segments(target.data) + template_line_segments(target.data)
    drawing_arc_segments = arc_obstacle_segments(target.data, drawing_segments)
    source_segments = drawing_segments + drawing_arc_segments + template_line_segments(template_data)
    # candidate_options expands the marker by the requested gap, so keep the
    # source text boxes physical here. Expanding both sides would silently
    # turn a 60-unit clearance into 120 units.
    # Compact search may broaden candidates, but must use the same complete
    # text envelopes as the independent verifier. Shrinking by 40 allowed
    # apparently valid placements which failed marker_clear_of_source_text.
    marker_text_gap = 0.0
    source_boxes = source_obstacle_boxes(target.data, psm, gap=marker_text_gap)
    source_boxes.extend(source_obstacle_boxes(template_data, psm, gap=marker_text_gap))
    leader_boxes = source_obstacle_boxes(target.data, psm, gap=5.0)
    leader_boxes.extend(source_obstacle_boxes(template_data, psm, gap=5.0))
    semantic_boxes = tuple(
        source_semantic_boxes(target.data) + source_semantic_boxes(template_data)
    )
    injected = bytearray()
    if len(target.data) % 2:
        injected.extend(b"\x00")

    guides = {id(point): nearest_segment_guide(point, drawing_segments) for point in points}
    options_by_point: list[list[PlacementCandidate]] = []
    # Sparse pages can afford a wider set of non-local lanes.  Keeping those
    # farther candidates is important when two nearby weld roots can only be
    # separated by moving one label well beyond the locally shortest choices.
    # Medium-density pages can still contain two close corrected weld roots
    # whose only mutually compatible straight leaders lie in the non-local
    # half of the candidate inventory.  Preserve 96 lanes through 16 points;
    # the compatibility bitsets keep this bounded while avoiding a false
    # "globally incompatible pair" after truncation.
    page_candidate_limit = (
        96
        if best_effort_layout
        else (64 if allow_text_overlap else (96 if len(points) <= 24 else 48))
    )
    for point in points:
        point_key = (point.page, point.graphic_ref, point.uci)
        # Chengda markup uses one visual language for every weld type. S/F is
        # retained as data, but both are deliberately drawn as circular tags.
        marker_shape = "circle"
        number = point.weld_number or (weld_number_by_point.get(point_key) if weld_number_by_point else None)
        visible_number = number or f"{label_start:05d}"
        # Use one drafting symbol size for every S/F/T label.  Radius 120 is
        # the previously proven four-character FS25 envelope; shorter labels
        # must not shrink and longer source metrics must not enlarge it.
        point_marker_radius = WELD_CALLOUT_MARKER_RADIUS
        lane = lane_by_point.get(point_key) if lane_by_point else None
        preferred_side = None
        if lane:
            preferred_side = 1 if lane.get("side") == "left" else -1
        options = candidate_options(
            point=point,
            source_segments=source_segments,
            source_boxes=source_boxes,
            leader_boxes=leader_boxes,
            region=region,
            marker_radius=point_marker_radius,
            gap=60.0,
            normal=guides[id(point)][1],
            marker_shape=marker_shape,
            strict_normal=strict_normal,
            preferred_side=preferred_side,
            max_candidates=page_candidate_limit,
            semantic_boxes=semantic_boxes,
            peer_points=tuple(points),
            best_effort=best_effort_layout,
            allow_fine_fallback=not best_effort_layout,
        )
        if not options and best_effort_layout:
            # Preserve every strict placement that already exists.  The
            # expensive minimum-violation search is only for the individual
            # root that made the page impossible, never for the whole page.
            options = candidate_options(
                point=point,
                source_segments=source_segments,
                source_boxes=source_boxes,
                leader_boxes=leader_boxes,
                region=region,
                marker_radius=point_marker_radius,
                gap=60.0,
                normal=guides[id(point)][1],
                marker_shape=marker_shape,
                strict_normal=strict_normal,
                preferred_side=preferred_side,
                max_candidates=page_candidate_limit,
                semantic_boxes=semantic_boxes,
                peer_points=tuple(points),
                best_effort=True,
                allow_fine_fallback=False,
            )
            print(
                f"Best-effort candidates for weld-ref {point.graphic_ref}: "
                f"{len(options)}"
            )
        options_by_point.append(options)

    print(
        f"{target.stream_name} page {target.page}: layout={region.source}, "
        f"outer={tuple(round(value, 1) for value in region.outer)}, "
        f"drawing={tuple(round(value, 1) for value in region.drawing)}, "
        f"candidate-counts={[len(options) for options in options_by_point]}"
    )
    placements = solve_page_layout(
        points,
        options_by_point,
        node_limit=10_000 if allow_text_overlap else 180_000,
        allow_conflicts=best_effort_layout,
    )
    planned = list(zip(points, placements))

    for index, (point, placement) in enumerate(planned):
        point_key = (point.page, point.graphic_ref, point.uci)
        number = point.weld_number or (weld_number_by_point.get(point_key) if weld_number_by_point else None)
        if not number:
            number = f"{label_start + index:05d}"

        cx = placement.center_x
        cy = placement.center_y
        r = placement.marker_radius or diamond_radius
        weld_type = point.weld_type or (weld_type_by_point.get(point_key, "") if weld_type_by_point else "")
        if weld_type not in {"S", "F"}:
            raise ValueError(f"Missing S/F weld type for page {point.page}, UCI {point.uci}")

        marker_points = [
            (
                cx + r * math.cos(math.pi / 2 - 2 * math.pi * step / MARKER_SEGMENT_COUNT),
                cy + r * math.sin(math.pi / 2 - 2 * math.pi * step / MARKER_SEGMENT_COUNT),
            )
            for step in range(MARKER_SEGMENT_COUNT)
        ]

        line_specs = [
            (point.center_x, point.center_y, placement.leader_x, placement.leader_y),
        ]
        line_specs.extend(
            (*marker_points[step], *marker_points[(step + 1) % MARKER_SEGMENT_COUNT])
            for step in range(MARKER_SEGMENT_COUNT)
        )
        for x1, y1, x2, y2 in line_specs:
            injected.extend(pack_line_record(child_ref, object_ref, x1, y1, x2, y2))
            object_ref += 1
            child_ref += 1

        # Keep synthetic text outside the existing PSM graphic-id range.
        # Otherwise the renderer can attach an unrelated source PSM envelope
        # to this new label and visibly move it out of the diamond.
        synthetic_text_ref = synthetic_weld_text_ref(
            target.page, index, len(number)
        )
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


def compound_fat_sector_indices(
    compound: bytes,
    sector_size: int,
) -> list[int]:
    """Read every FAT-sector id from the OLE header/DIFAT chain."""

    fat_sector_count = struct.unpack_from("<I", compound, 44)[0]
    first_difat_sector = struct.unpack_from("<I", compound, 68)[0]
    difat_sector_count = struct.unpack_from("<I", compound, 72)[0]
    result = [
        value
        for value in struct.unpack_from("<109I", compound, 76)
        if value not in (FREESECT, ENDOFCHAIN)
    ]
    sector = first_difat_sector
    entries_per_difat_sector = sector_size // 4 - 1
    seen: set[int] = set()
    for _ in range(difat_sector_count):
        if sector in (FREESECT, ENDOFCHAIN) or sector in seen:
            raise ValueError("Invalid OLE DIFAT chain")
        seen.add(sector)
        start = sector_offset(sector, sector_size)
        values = struct.unpack_from(
            f"<{entries_per_difat_sector + 1}I",
            compound,
            start,
        )
        result.extend(
            value
            for value in values[:-1]
            if value not in (FREESECT, ENDOFCHAIN)
        )
        sector = values[-1]
    if len(result) < fat_sector_count:
        raise ValueError(
            f"OLE header declares {fat_sector_count} FAT sectors, "
            f"but DIFAT exposes only {len(result)}"
        )
    return result[:fat_sector_count]


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

        fat_sectors = compound_fat_sector_indices(original, sector_size)
        fat_capacity = sector_size // 4
        if total_sectors + sum(
            max(0, math.ceil(len(data) / sector_size) - math.ceil(ole.direntries[ole._find(name.split("/"))].size / sector_size))
            for name, data in replacements.items()
        ) > fat_capacity * len(fat_sectors):
            raise ValueError(
                "Annotated SHA would require allocating an additional OLE FAT "
                "sector; the existing DIFAT capacity is insufficient"
            )

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

        padded_fat = fat + [FREESECT] * (
            fat_capacity * len(fat_sectors) - len(fat)
        )
        for fat_number, fat_sector in enumerate(fat_sectors):
            fat_bytes = bytearray(sector_size)
            start_index = fat_number * fat_capacity
            for index, value in enumerate(
                padded_fat[start_index : start_index + fat_capacity]
            ):
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
        help="Minimum radius of the injected circular weld marker in page units.",
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
    parser.add_argument(
        "--best-effort-layout",
        action="store_true",
        help="For an otherwise impossible dense page, minimise and audit unavoidable straight-leader conflicts instead of aborting.",
    )
    args = parser.parse_args()

    started_at = time.perf_counter()
    streams = read_sha_streams(args.sha_path)
    if args.best_effort_layout:
        print(f"Diagnostic: read SHA streams in {time.perf_counter() - started_at:.2f}s", flush=True)
    wanted_pages = parse_pages(args.pages)
    targets = sheet_streams(streams, wanted_pages)
    if not targets:
        raise SystemExit("No populated Sheet streams matched the requested page selection.")

    psm = streams.get("PSMcluster0", b"")
    stage_at = time.perf_counter()
    shared_viewbox = declared_sheet_viewbox(streams.get("Sheet6", b""))
    if shared_viewbox is None:
        shared_viewbox = next(
            (viewbox for data in streams.values() if (viewbox := declared_sheet_viewbox(data)) is not None),
            None,
        )
    if args.best_effort_layout:
        print(
            f"Diagnostic: resolved shared viewbox in {time.perf_counter() - stage_at:.2f}s",
            flush=True,
        )
    stage_at = time.perf_counter()
    shared_template = next(
        (template for template in (build_text_template(target.data, psm) for target in targets) if template is not None),
        None,
    )
    style_targets = targets
    if shared_template is None and len(targets) == 1:
        # Compact Sheet6 pages keep their usable text records in Sheet221.
        # The fixed-length Shape2D text record is safe to reuse when injecting
        # into the page descriptor stream, just as the renderer already reuses
        # the shared title-block geometry from that stream.
        template_data = streams.get("Sheet221", b"")
        shared_template = build_text_template(template_data, psm)
        if shared_template is not None:
            style_targets = [
                SheetTarget("Sheet221", targets[0].page, template_data),
                *targets,
            ]
    if shared_template is None:
        raise SystemExit(f"Could not locate the shared {TEXT_TEMPLATE!r} text template in any selected Sheet stream.")
    if args.best_effort_layout:
        print(
            f"Diagnostic: resolved text template in {time.perf_counter() - stage_at:.2f}s",
            flush=True,
        )
    stage_at = time.perf_counter()
    shared_style = shared_style_model(style_targets, psm, shared_template.style_ref)
    if args.best_effort_layout:
        print(
            f"Diagnostic: resolved shared style in {time.perf_counter() - stage_at:.2f}s",
            flush=True,
        )
    stage_at = time.perf_counter()
    # A production weld map already carries the selected physical page,
    # graphic reference and exact SHA root coordinates.  Re-discovering every
    # dynamic UCI dot and measuring it against every page segment is both
    # redundant and quadratic on dense drawings.  Keep discovery only for the
    # standalone/no-map mode; mapped production runs are populated directly
    # from the audited JSON entries below.
    points_by_stream = (
        {target.stream_name: [] for target in targets}
        if args.weld_map is not None
        else collect_connection_points(streams, targets, args.distance_threshold)
    )
    if args.best_effort_layout:
        print(
            f"Diagnostic: collected connection points in {time.perf_counter() - stage_at:.2f}s",
            flush=True,
        )
    # A UCI can be present in multiple Sheet contexts.  A PCF weld map has
    # already selected one visible SHA dot, so retain that page + graphic ref
    # identity here instead of applying the same number to every occurrence.
    weld_number_by_point: dict[tuple[int, int, str], str] | None = None
    weld_type_by_point: dict[tuple[int, int, str], str] | None = None
    payload: dict[str, object] | None = None
    if args.weld_map is not None:
        payload = json.loads(args.weld_map.read_text(encoding="utf-8"))
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
        weld_type_by_point = {
            (int(entry["page"]), int(entry["graphic_ref"]), str(entry["uci"])): str(entry.get("weld_type", "")).upper()
            for entry in payload.get("welds", [])
            if (
                entry.get("uci")
                and entry.get("weld_number")
                and entry.get("page") not in (None, 999999)
                and entry.get("graphic_ref") is not None
            )
        }
        stream_by_page = {target.page: target.stream_name for target in targets}
        # The map contains the accepted logical callouts, including explicitly
        # allowed multiple OLET/PAD welds at one physical point. Source-point
        # deduplication must not reduce this list to one number per UCI.
        points_by_stream = {target.stream_name: [] for target in targets}
        for entry in payload.get("welds", []):
            if (
                not entry.get("weld_number")
                or entry.get("page") in (None, 999999)
                or entry.get("graphic_ref") is None
                or entry.get("sha_x") in (None, "")
                or entry.get("sha_y") in (None, "")
                or not entry.get("uci")
            ):
                continue
            page = int(entry["page"])
            stream_name = str(entry.get("sheet_stream") or stream_by_page.get(page, ""))
            if stream_name not in points_by_stream:
                continue
            point = PointTarget(
                graphic_ref=int(entry["graphic_ref"]),
                uci=str(entry["uci"]),
                center_x=float(entry["sha_x"]),
                center_y=float(entry["sha_y"]),
                page=page,
                weld_number=str(entry["weld_number"]),
                weld_type=str(entry.get("weld_type", "")).upper(),
            )
            key = (point.page, point.graphic_ref, point.uci, point.weld_number)
            existing = {
                (item.page, item.graphic_ref, item.uci, item.weld_number)
                for item in points_by_stream[stream_name]
            }
            if key not in existing:
                points_by_stream[stream_name].append(point)
                points_by_stream[stream_name].sort(
                    key=lambda item: (item.center_y, item.center_x)
                )
    lane_by_point: dict[tuple[int, int, str], dict[str, object]] | None = None
    if args.lane_plan is not None:
        lane_payload = json.loads(args.lane_plan.read_text(encoding="utf-8"))
        lane_by_point = {
            (int(entry["page"]), int(entry["graphic_ref"]), str(entry["uci"])): entry
            for entry in lane_payload.get("welds", [])
        }
    replacements: dict[str, bytes] = {}
    next_number = args.start_number
    for target in targets:
        # Text styles vary between physical ISO pages. Prefer a template from
        # the current Sheet so marker sizing and the injected glyph metrics
        # match what that page actually renders; retain the shared template
        # only for pages that contain no usable text record of their own.
        page_template = build_text_template(target.data, psm) or shared_template
        points = points_by_stream.get(target.stream_name, [])
        if weld_number_by_point is not None:
            points = [
                point
                for point in points
                if (point.page, point.graphic_ref, point.uci) in weld_number_by_point
            ]
        stage_at = time.perf_counter()
        new_data, next_number = inject_into_sheet(
            target=target,
            points=points,
            psm=psm,
            template_data=streams.get("Sheet221", b""),
            label_start=next_number,
            diamond_radius=args.diamond_radius,
            template=page_template,
            shared_style=shared_style,
            weld_number_by_point=weld_number_by_point,
            weld_type_by_point=weld_type_by_point,
            strict_normal=args.strict_normal_layout,
            lane_by_point=lane_by_point,
            allow_text_overlap=args.relaxed_text_clearance,
            best_effort_layout=args.best_effort_layout,
            inherited_viewbox=shared_viewbox,
        )
        replacements[target.stream_name] = new_data
        print(
            f"{target.stream_name} page {target.page}: "
            f"{len(points)} injected callouts in {time.perf_counter() - stage_at:.2f}s"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    stage_at = time.perf_counter()
    patch_streams_in_compound(args.sha_path, args.output, replacements)
    if args.best_effort_layout:
        print(
            f"Diagnostic: patched compound streams in {time.perf_counter() - stage_at:.2f}s; "
            f"total {time.perf_counter() - started_at:.2f}s",
            flush=True,
        )
    print(f"Wrote annotated SHA copy to {args.output}")


if __name__ == "__main__":
    main()
