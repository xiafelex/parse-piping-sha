#!/usr/bin/env python3
"""Create a SHA-only SVG layout prototype for one ISO sheet.

Decodes the observed Sheet6 line-record families, template content, text
anchors and PSM spatial bounds directly from a Shape2D SHA container.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import re
import struct
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from chengda_paths import ASSET_DIR, PROJECT_DIR
from statistics import median

from analyze_iso_split import dynamic_graphics, read_sha_streams
from analyze_sha_pages import logical_page, text_objects

PAGE_WIDTH = 16800
PAGE_HEIGHT = 11880
SHEET_UNIT = 16800
PSM_COORDINATE_UNIT = 32768
BOXED_MARKER_RE = re.compile(r"^(?:[FGBS]\d+(?:-[A-Z0-9]+)?)(?:\s+[FGBS]\d+(?:-[A-Z0-9]+)?)*$|^T\d+-\d+$")
WELD_CALLOUT_COLOR = "#ff0000"
WELD_CALLOUT_STROKE_WIDTH = 10.0
WELD_CALLOUT_HALO_WIDTH = 24.0
WELD_CALLOUT_HALO_ROOT_GAP = 24.0
WELD_CALLOUT_FONT_HEIGHT = 88.0
# Chengda Sheet6 stores the values in the lower characteristic strip as one
# shared text run format.  Its StyleCluster format record is indirect and is
# not yet returned by ``style_format_metrics``.  Contour comparison against
# the issued reference PDF identifies the face as Windows Arial Narrow
# Regular. The issued PDF text matrix is 4.074 pt with equal x/y scale on A3,
# equivalent to 8.148 pt on this A2 output.  PSM is a line/glyph envelope, not
# the nominal font size; its same-format median therefore needs this one plot
# calibration. Width remains the per-object PSM width, but SVG adjusts spacing
# only so the Arial Narrow outlines themselves are never stretched.
CHENGDA_FEATURE_VALUE_FORMAT_REF = 0x15C
CHENGDA_FEATURE_VALUE_STYLE_REF = 0x1897
CHENGDA_FEATURE_VALUE_HEIGHT_SCALE = 0.8720
# The issued A3 grid-zone font is 4.744 pt. The SHA-derived 50.36-unit cap
# height plotted as 4.250 pt after A2-to-A3 comparison, so all four sides use
# one format-level correction rather than side- or character-specific sizes.
CHENGDA_GRID_ZONE_FONT_SCALE = 1.1160
CHENGDA_GRID_ZONE_GLYPH_Y_SCALE = 1.2200
CHENGDA_FEATURE_STRIP_GLYPH_Y_SCALE = 1.2300
CHENGDA_FOOTER_LABEL_GLYPH_Y_SCALE = 1.1000
# The revision header and LEGEND title use the same nominal Shape2D height as
# nearby text, but the Chengda plotter applies a taller character matrix.  The
# PSM width is already correct, so only the y component must be restored.
CHENGDA_REVISION_HEADER_GLYPH_Y_SCALE = 1.1800
CHENGDA_LEGEND_TITLE_GLYPH_Y_SCALE = 1.2800
# This local cell label is plotted with a slightly wider character matrix than
# Chromium's Arial Narrow.  Scale around the cell's SHA start point so the
# left inset remains source-controlled.
CHENGDA_PROJECT_DOC_LABEL_GLYPH_X_SCALE = 1.1650
# Approved-by and Date share one lower review-panel row family.  Their stored
# after-edge is six sheet units above the visual centre under Chromium.
CHENGDA_APPROVAL_LABEL_Y_OFFSET = 12.0
# The three fixed title-block families use the same declared Shape2D heights
# as neighbouring labels, but the issued plotter expands their vertical em
# box before fitting the glyph run to its PSM width/cell.  Chromium otherwise
# renders them visibly shorter and lighter.  These are format/region rules,
# not drawing-number or literal-text overrides.
CHENGDA_PROJECT_TITLE_FORMAT_REF = 0x154
CHENGDA_PROJECT_TITLE_FONT_SCALE = 1.4000
CHENGDA_CLIENT_TITLE_FORMAT_REF = 0x14F
CHENGDA_CLIENT_TITLE_FONT_SCALE = 1.1800
CHENGDA_REVIEW_PANEL_FORMAT_REF = 0x142
CHENGDA_REVIEW_PANEL_FONT_SCALE = 4.0 / 3.0
# The issued FIN reference PDF carries this as a page-level /Artifact
# /Watermark rather than a Shape2D object.  The source page is /Rotate 90, so
# its raw PDF text origin must first be transformed into the *displayed* page
# coordinate system.  Store that displayed origin relative to the page; this
# also makes the SVG rotation agree with what a PDF viewer actually shows.
CHENGDA_FIN_WATERMARK_TEXT = "ONLY FOR FIN"
CHENGDA_FIN_WATERMARK_X_RATIO = 0.362688409040589
CHENGDA_FIN_WATERMARK_Y_RATIO = 0.7369893905486742
CHENGDA_FIN_WATERMARK_FONT_RATIO = 0.08720022819640558
CHENGDA_FIN_WATERMARK_ANGLE = -45.0
# The page-level graphics state applies 40% opacity to the form.  The form's
# own #808080 fill is opaque, yielding the light grey seen in the issued PDF.
CHENGDA_FIN_WATERMARK_OPACITY = 0.4


def psm_bbox(psm: bytes, graphic_ref: int) -> tuple[float, float, float, float] | None:
    """Read the observed PSM entry: graphic id followed by five uint16 values."""

    needle = struct.pack("<I", graphic_ref)
    for match in re.finditer(re.escape(needle), psm):
        offset = match.start() + 4
        if offset + 10 > len(psm):
            continue
        left, bottom, right, top, _ = struct.unpack_from("<5H", psm, offset)
        if left < right <= PSM_COORDINATE_UNIT and bottom < top <= PSM_COORDINATE_UNIT:
            scale = SHEET_UNIT / PSM_COORDINATE_UNIT
            return left * scale, bottom * scale, right * scale, top * scale
    return None


def psm_bboxes(psm: bytes, graphic_ref: int) -> list[tuple[float, float, float, float]]:
    """Return every valid PSM envelope for one graphic reference.

    A few SHA files repeat a reference in PSMcluster0.  The first envelope can
    be a page/container record while a later one is the actual text extent, so
    consumers with an independent SHA anchor must be able to choose between
    all source candidates.
    """

    boxes: list[tuple[float, float, float, float]] = []
    needle = struct.pack("<I", graphic_ref)
    for match in re.finditer(re.escape(needle), psm):
        offset = match.start() + 4
        if offset + 10 > len(psm):
            continue
        left, bottom, right, top, _ = struct.unpack_from("<5H", psm, offset)
        if left < right <= PSM_COORDINATE_UNIT and bottom < top <= PSM_COORDINATE_UNIT:
            scale = SHEET_UNIT / PSM_COORDINATE_UNIT
            box = (left * scale, bottom * scale, right * scale, top * scale)
            if box not in boxes:
                boxes.append(box)
    return boxes


def svg_y(y: float) -> float:
    return PAGE_HEIGHT - y


def point_segment_distance(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    """Return the shortest page-unit distance from a point to a segment."""

    dx, dy = x2 - x1, y2 - y1
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.hypot(px - x1, py - y1)
    projection = ((px - x1) * dx + (py - y1) * dy) / length_squared
    projection = max(0.0, min(1.0, projection))
    return math.hypot(px - (x1 + projection * dx), py - (y1 + projection * dy))


def rotated_text_extent(width: float, height: float, angle_degrees: float) -> tuple[float, float]:
    """Recover local glyph height and text length from a rotated PSM envelope.

    PSM stores an axis-aligned envelope. For a rotated label its width/height
    include each other's projection, so using the envelope height as a font
    size makes diagonal annotations much too large.
    """

    radians = math.radians(angle_degrees)
    cosine, sine = abs(math.cos(radians)), abs(math.sin(radians))
    determinant = cosine * cosine - sine * sine
    if abs(determinant) > 0.08:
        text_length = (width * cosine - height * sine) / determinant
        glyph_height = (height * cosine - width * sine) / determinant
        if text_length > 0 and glyph_height > 0:
            return max(34.0, glyph_height), max(34.0, text_length)
    # At about 45 degrees the envelope projection matrix is singular. Retain
    # the previous conservative fallback until a local StyleCluster metric is
    # decoded for that record family.
    return max(34.0, min(width, height)), max(width, height)


def declared_sheet_viewbox(data: bytes) -> tuple[float, float, float, float] | None:
    """Read the visible sheet extent declared in the Shape2D sheet header.

    Shape2D stores an ISO page in a square workspace.  The physical A1 page
    occupies about 0.841 by 0.594 of that workspace, ending at y=0.706.
    Rendering it in a full 16800-wide viewBox compresses every x coordinate,
    including the BOM, while discarding its y origin clips the title block.
    """

    # Chengda's A2 landscape template declares its physical extent as an
    # adjacent 594 x 420 mm pair in Sheet221.  Unlike the Blue Star A1 files,
    # that declaration is outside the short Sheet header and its y origin is
    # zero in Shape2D's normalized workspace.  Match the adjacent A-series
    # dimensions structurally before applying the legacy A1 header heuristic.
    scan_limit = min(len(data) - 16, 2048)
    for offset in range(max(0, scan_limit)):
        width, height = struct.unpack_from("<dd", data, offset)
        if 0.59 <= width <= 0.60 and 0.415 <= height <= 0.425:
            return (
                0.0,
                PAGE_HEIGHT - height * SHEET_UNIT,
                width * SHEET_UNIT,
                height * SHEET_UNIT,
            )

    values = [struct.unpack_from("<d", data, offset)[0] for offset in range(0, min(len(data) - 8, 240))]
    widths = [value for value in values if 0.82 <= value <= 0.86]
    heights = [value for value in values if 0.58 <= value <= 0.62]
    y_maxes = [value for value in values if 0.68 <= value <= 0.73]
    if not widths or not heights or not y_maxes:
        return None
    width = max(widths)
    height = max(heights)
    y_max = max(y_maxes)
    return 0.0, max(0.0, y_max - height) * SHEET_UNIT, width * SHEET_UNIT, height * SHEET_UNIT


def sheet_viewbox(
    data: bytes,
    inherited_viewbox: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float, float]:
    """Return a physical ISO viewbox, inheriting a sibling's declared page.

    Some later Sheet streams omit the physical A1 header values entirely even
    though their graphics retain the same normalized page coordinate system.
    The shared Sheet6 header is part of the same SHA source and is therefore a
    valid layout declaration; only use it when the selected stream has no
    declaration of its own. Falling back to the square workspace otherwise
    visibly shrinks the full ISO into the upper-left corner.
    """

    return declared_sheet_viewbox(data) or inherited_viewbox or (0.0, 0.0, PAGE_WIDTH, PAGE_HEIGHT)


def line_segments(data: bytes) -> list[tuple[float, float, float, float, int, int]]:
    """Decode the observed Shape2D two-point segment record.

    For line-like graphics, the reference is followed by a 14-byte header and
    four unaligned little-endian float64 values: x1, y1, x2, y2.
    """

    segments: list[tuple[float, float, float, float, int, int]] = []
    # This record layout also covers dimensions, leaders, boxes and title-frame
    # strokes, which do not have component UCI references.
    for start in range(0, len(data) - 46, 2):
        object_ref, zero_a, parent_ref, zero_b, zero_c, style_ref, zero_d = struct.unpack_from(
            "<7H", data, start
        )
        if zero_a or zero_b or zero_c or zero_d:
            continue
        if not (1100 <= parent_ref <= 1500 and 1 <= style_ref <= 1000):
            continue
        x1, y1, x2, y2 = struct.unpack_from("<dddd", data, start + 14)
        if not all(0.01 <= value <= 1 for value in (x1, y1, x2, y2)):
            continue
        # Sheet x/y are normalized against a square Shape2D workspace;
        # this ISO page occupies only its visible-height portion.
        if y1 > PAGE_HEIGHT / SHEET_UNIT or y2 > PAGE_HEIGHT / SHEET_UNIT:
            continue
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 * SHEET_UNIT
        # Page border and title-block dividers can span the whole ISO sheet.
        if not 4 <= length <= math.hypot(PAGE_WIDTH, PAGE_HEIGHT):
            continue
        # The preceding uint32 is the child primitive; object_ref is its parent
        # Shape2D graphic.  Composite records use the child id to mark arcs.
        child_ref = struct.unpack_from("<I", data, start - 4)[0] if start >= 4 else 0
        segments.append((x1, y1, x2, y2, object_ref, child_ref))
    unique: dict[tuple[float, float, float, float, int], tuple[float, float, float, float, int, int]] = {}
    for segment in segments:
        x1, y1, x2, y2, _, child_ref = segment
        # A segment can be repeated by multiple Shape2D subobjects; preserve
        # one copy while treating the same line in reverse as identical.
        ends = tuple(sorted(((round(x1, 6), round(y1, 6)), (round(x2, 6), round(y2, 6)))))
        unique[ends + (child_ref,)] = segment
    return list(unique.values())


def line_style_widths(style_data: bytes) -> dict[int, float]:
    """Decode observed StyleCluster line-weight records.

    The records identified by the ``0x002E, 0x0036`` header store the Shape2D
    line style id at byte 20 and a page-normalized line width at byte 40.  A
    Sheet two-point primitive uses that same id in its ``style_ref`` field.
    Keep this deliberately narrow: it restores the proven width relation
    without assigning unsupported semantics to the remaining StyleCluster
    fields (colour and linetype are still separate unresolved records).
    """

    widths: dict[int, float] = {}
    for signature in (b"\x2e\x00\x36\x00", b"\x2e\x00\x3a\x00"):
        for match in re.finditer(re.escape(signature), style_data):
            start = match.start()
            if start + 48 > len(style_data):
                continue
            style_ref = struct.unpack_from("<I", style_data, start + 20)[0]
            width_ratio = struct.unpack_from("<d", style_data, start + 40)[0]
            if 1 <= style_ref <= 1000 and 0.00001 <= width_ratio <= 0.01:
                widths[style_ref] = width_ratio * SHEET_UNIT
    return widths


def line_style_parent_refs(style_data: bytes) -> dict[int, int]:
    """Decode StyleCluster records whose line style inherits another style."""

    parents: dict[int, int] = {}
    for match in re.finditer(re.escape(b"\x30\x00\x5a\x00"), style_data):
        start = match.start()
        if start + 32 > len(style_data):
            continue
        style_ref = struct.unpack_from("<I", style_data, start + 20)[0]
        parent_ref = struct.unpack_from("<I", style_data, start + 28)[0]
        if 1 <= style_ref <= 1000 and 1 <= parent_ref <= 1000 and style_ref != parent_ref:
            parents[style_ref] = parent_ref
    return parents


def resolved_line_style_widths(style_data: bytes) -> dict[int, float]:
    """Return direct and inherited line widths in SHA page units."""

    widths = line_style_widths(style_data)
    # Values above 2 mm are not printable ISO pen weights. The Chengda A2
    # template stores 6.34485 mm on its physical boundary style although the
    # reference PDF plots that boundary with the standard 0.25 mm pen.
    for style_ref, width in list(widths.items()):
        if width > 0.002 * SHEET_UNIT:
            widths[style_ref] = 0.00025 * SHEET_UNIT
    parents = line_style_parent_refs(style_data)

    def resolve(style_ref: int, trail: set[int]) -> float | None:
        if style_ref in widths:
            return widths[style_ref]
        parent_ref = parents.get(style_ref)
        if parent_ref is None or parent_ref in trail:
            return None
        return resolve(parent_ref, trail | {style_ref})

    for style_ref in parents:
        inherited = resolve(style_ref, set())
        if inherited is not None:
            widths[style_ref] = inherited
    return widths


def line_style_dasharrays(style_data: bytes) -> dict[int, tuple[float, ...]]:
    """Decode dashed-line style links and their normalized dash lengths."""

    patterns: dict[int, tuple[float, ...]] = {}
    for match in re.finditer(re.escape(b"\x2f\x00\x42\x00"), style_data):
        start = match.start()
        if start + 72 > len(style_data):
            continue
        pattern_ref = struct.unpack_from("<I", style_data, start + 20)[0]
        count = struct.unpack_from("<H", style_data, start + 54)[0]
        if not 1 <= count <= 8 or start + 56 + count * 8 > len(style_data):
            continue
        values = tuple(
            abs(struct.unpack_from("<d", style_data, start + 56 + index * 8)[0]) * SHEET_UNIT
            for index in range(count)
        )
        if all(1 <= value <= SHEET_UNIT * 0.1 for value in values):
            patterns[pattern_ref] = values

    result: dict[int, tuple[float, ...]] = {}
    for match in re.finditer(re.escape(b"\x2e\x00\x3a\x00"), style_data):
        start = match.start()
        if start + 64 > len(style_data):
            continue
        style_ref = struct.unpack_from("<I", style_data, start + 20)[0]
        pattern_ref = struct.unpack_from("<I", style_data, start + 60)[0]
        if pattern_ref in patterns:
            result[style_ref] = patterns[pattern_ref]
    return result


def sheet_line_widths(data: bytes, style_data: bytes) -> dict[int, float]:
    """Return SHA page-unit stroke widths keyed by Sheet child primitive id."""

    style_widths = resolved_line_style_widths(style_data)
    result: dict[int, float] = {}
    for start in range(0, len(data) - 46, 2):
        _, zero_a, parent_ref, zero_b, zero_c, style_ref, zero_d = struct.unpack_from(
            "<7H", data, start
        )
        if zero_a or zero_b or zero_c or zero_d:
            continue
        if not (1100 <= parent_ref <= 1500 and style_ref in style_widths):
            continue
        child_ref = struct.unpack_from("<I", data, start - 4)[0] if start >= 4 else 0
        result[child_ref] = style_widths[style_ref]
    return result


def template_line_widths(data: bytes, style_data: bytes) -> dict[int, float]:
    """Return widths for the observed ``0x18/0x32`` Sheet line record family.

    Later physical ISO sheets use this family extensively for page geometry,
    dimensions and component strokes. Its child primitive id is at byte 6 and
    its 16-bit style reference is at byte 20; both are directly observable in
    the source record and use the same StyleCluster width table as Sheet6.
    """

    style_widths = resolved_line_style_widths(style_data)
    result: dict[int, float] = {}
    signature = b"\x18\x00\x32\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        if start + 56 > len(data):
            continue
        child_ref = struct.unpack_from("<I", data, start + 6)[0]
        style_ref = struct.unpack_from("<H", data, start + 20)[0]
        if style_ref in style_widths:
            result[child_ref] = style_widths[style_ref]
    return result


def composite_records(
    data: bytes,
    alignment: int | None = None,
) -> list[tuple[int, list[tuple[int, int, int, int, int, int]]]]:
    """Read validated Shape2D ``0x7B`` composite records at any byte offset.

    Shape2D records are not guaranteed to start on a two-byte boundary.  The
    former decoder stepped through the stream by two bytes and consequently
    discarded every odd-aligned composite, including genuine elbow curves.
    The record's own length field provides a stronger boundary check:
    ``record_size == 32 + child_count * 14``.  Use that declaration together
    with the fixed zero word and known child types to reject coincidental
    ``0x7B00`` byte pairs inside payloads.

    The enclosing graphic reference is stored at byte 6.  Byte 2 is the
    record length, not a parent reference; retaining that distinction is also
    required for direct UCI provenance on composite component outlines.
    """

    records: list[tuple[int, list[tuple[int, int, int, int, int, int]]]] = []
    for match in re.finditer(re.escape(b"\x7b\x00"), data):
        start = match.start()
        if alignment is not None and start % 2 != alignment:
            continue
        if start + 34 > len(data):
            continue
        record_size = struct.unpack_from("<H", data, start + 2)[0]
        count = struct.unpack_from("<I", data, start + 22)[0]
        if (
            not 1 <= count <= 100
            or record_size != 32 + count * 14
            or data[start + 4 : start + 6] != b"\x00\x00"
            or start + 2 + record_size > len(data)
        ):
            continue
        children = [
            struct.unpack_from("<I5H", data, start + 34 + index * 14)
            for index in range(count)
        ]
        if any(child[5] not in {0, 5, 6, 11, 16} for child in children):
            continue
        parent_ref = struct.unpack_from("<I", data, start + 6)[0]
        records.append((parent_ref, children))
    return records


def composite_arcs(data: bytes) -> dict[int, tuple[float, float, float, float]]:
    """Read type-6 child primitives and their Shape2D page-space bounding boxes."""

    arcs: dict[int, tuple[float, float, float, float]] = {}
    for _, children in composite_records(data):
        for child_ref, left, bottom, right, top, primitive_type in children:
            if primitive_type == 6 and left < right and bottom < top:
                # Composite records use a coordinate scale of two page units.
                arcs[child_ref] = (left / 2, bottom / 2, right / 2, top / 2)
    return arcs


def standalone_arcs(data: bytes) -> list[dict[str, float | int]]:
    """Decode the observed ``0x61/0x3B`` centre-radius-angle arc family.

    These records carry the smooth centreline at an elbow between adjacent
    straight primitives.  Their layout is directly observable as primitive
    ref, owner ref, local object group at byte 14, line style at byte 20,
    centre x/y, radius, start/end angles and a one-byte direction flag.
    Shape2D angles use a y-up page coordinate system; SVG's y axis is
    inverted later when the path is emitted.
    """

    result: list[dict[str, float | int]] = []
    signature = b"\x61\x00\x3b\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        if start + 65 > len(data):
            continue
        primitive_ref, owner_ref = struct.unpack_from("<II", data, start + 6)
        local_object_group = struct.unpack_from("<H", data, start + 14)[0]
        style_ref = struct.unpack_from("<H", data, start + 20)[0]
        center_x, center_y, radius, start_angle, end_angle = struct.unpack_from(
            "<5d", data, start + 24
        )
        direction = data[start + 64]
        if (
            not (0.01 <= center_x <= 1 and 0.01 <= center_y <= PAGE_HEIGHT / SHEET_UNIT)
            or not (1e-6 <= radius <= 0.2)
            or not (0 <= start_angle <= math.tau + 1e-6)
            or not (0 <= end_angle <= math.tau + 1e-6)
            or direction not in {0, 1}
        ):
            continue
        result.append(
            {
                "primitive_ref": primitive_ref,
                "owner_ref": owner_ref,
                "local_object_group": local_object_group,
                "style_ref": style_ref,
                "center_x": center_x * SHEET_UNIT,
                "center_y": center_y * SHEET_UNIT,
                "radius": radius * SHEET_UNIT,
                "start_angle": start_angle,
                "end_angle": end_angle,
                "direction": direction,
            }
        )
    return result


def template_line_style_refs(data: bytes) -> dict[int, int]:
    """Return the direct StyleCluster line-style id for each template child."""

    result: dict[int, int] = {}
    signature = b"\x18\x00\x32\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        if start + 24 > len(data):
            continue
        result[struct.unpack_from("<I", data, start + 6)[0]] = struct.unpack_from(
            "<H", data, start + 20
        )[0]
    return result


def standalone_arc_geometry(
    arc: dict[str, float | int],
) -> tuple[float, float, float, float, int, int]:
    """Return page-space endpoints and SVG large-arc/sweep flags."""

    center_x = float(arc["center_x"])
    center_y = float(arc["center_y"])
    radius = float(arc["radius"])
    start_angle = float(arc["start_angle"])
    end_angle = float(arc["end_angle"])
    direction = int(arc["direction"])
    start_x = center_x + radius * math.cos(start_angle)
    start_y = center_y + radius * math.sin(start_angle)
    end_x = center_x + radius * math.cos(end_angle)
    end_y = center_y + radius * math.sin(end_angle)
    source_delta = (
        (start_angle - end_angle) % math.tau
        if direction == 0
        else (end_angle - start_angle) % math.tau
    )
    large_arc = 1 if source_delta > math.pi else 0
    # Shape2D is y-up. Inverting y for SVG reverses the source sweep.
    svg_sweep = 1 if direction == 0 else 0
    return start_x, start_y, end_x, end_y, large_arc, svg_sweep


def standalone_arc_is_connected(
    arc: dict[str, float | int],
    segments: list[tuple[float, float, float, float, int, int]],
    tolerance: float = 2.0,
) -> bool:
    """Require both arc ends to meet independent SHA segment endpoints."""

    if not segments:
        return False
    start_x, start_y, end_x, end_y, _, _ = standalone_arc_geometry(arc)
    endpoints = [
        point
        for x1, y1, x2, y2, _, _ in segments
        for point in ((x1 * SHEET_UNIT, y1 * SHEET_UNIT), (x2 * SHEET_UNIT, y2 * SHEET_UNIT))
    ]
    return all(
        min(math.hypot(px - x, py - y) for x, y in endpoints) <= tolerance
        for px, py in ((start_x, start_y), (end_x, end_y))
    )


def revision_cloud_arc_groups(
    arcs: list[dict[str, float | int]], tolerance: float = 2.0
) -> list[list[dict[str, float | int]]]:
    """Return closed multi-arc groups used for revision clouds.

    Elbows connect their two ends to straight pipe segments.  Revision clouds
    instead contain four or more arcs with one shared owner, and every arc end
    meets an end of another arc in that owner group.
    """

    grouped: dict[tuple[int, int], list[dict[str, float | int]]] = defaultdict(list)
    for arc in arcs:
        grouped[(int(arc["owner_ref"]), int(arc["local_object_group"]))].append(arc)
    clouds: list[list[dict[str, float | int]]] = []
    for group in grouped.values():
        if len(group) < 4:
            continue
        endpoints = {
            int(arc["primitive_ref"]): standalone_arc_geometry(arc)[:4]
            for arc in group
        }
        if all(
            any(
                math.hypot(px - x, py - y) <= tolerance
                for other_ref, other in endpoints.items()
                if other_ref != int(arc["primitive_ref"])
                for x, y in ((other[0], other[1]), (other[2], other[3]))
            )
            for arc in group
            for px, py in (
                (endpoints[int(arc["primitive_ref"])][0], endpoints[int(arc["primitive_ref"])][1]),
                (endpoints[int(arc["primitive_ref"])][2], endpoints[int(arc["primitive_ref"])][3]),
            )
        ):
            clouds.append(sorted(group, key=lambda arc: int(arc["primitive_ref"])))
    return sorted(clouds, key=lambda group: int(group[0]["owner_ref"]))


def revision_cloud_text_offsets(
    records: list[dict[str, object]],
    cloud_groups: list[list[dict[str, float | int]]],
    psm: bytes | None = None,
) -> dict[tuple[int, int], tuple[float, float]]:
    """Recover connection-note blocks stored in a cloud-local text space.

    Chengda SHA pages can keep the closed revision cloud in page coordinates
    while the six-line ``SEE ISO`` connection note retains its untranslated
    local y coordinate. The records still form a strict source relationship:
    one x/style/format, an evenly spaced connection-note block, and the only
    nearby cloud whose horizontal span contains it.
    """

    cloud_boxes: list[tuple[float, float, float, float]] = []
    for group in cloud_groups:
        if not group:
            continue
        bounds = [
            (
                float(arc["center_x"]) - float(arc["radius"]),
                float(arc["center_y"]) - float(arc["radius"]),
                float(arc["center_x"]) + float(arc["radius"]),
                float(arc["center_y"]) + float(arc["radius"]),
            )
            for arc in group
        ]
        cloud_boxes.append(
            (
                min(box[0] for box in bounds),
                min(box[1] for box in bounds),
                max(box[2] for box in bounds),
                max(box[3] for box in bounds),
            )
        )

    def is_connection_line(text: str) -> bool:
        return bool(
            text == "SEE ISO"
            or re.fullmatch(r"\d{6}", text)
            or re.fullmatch(r"\d{2}(?:-[A-Za-z0-9_]+){3,}", text)
            or re.fullmatch(r"(?:E|N|EL)\s+[+-]?\d+", text)
        )

    grouped: dict[tuple[int, int, int], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        text = str(record.get("text", "")).strip()
        direction_x = float(record.get("direction_x", 0.0))
        direction_y = float(record.get("direction_y", 0.0))
        if (
            not is_connection_line(text)
            or abs(direction_x - 1.0) > 0.05
            or abs(direction_y) > 0.05
        ):
            continue
        x = float(record["x"]) * SHEET_UNIT
        grouped[
            (
                round(x / 4),
                int(record["style_ref"]),
                int(record.get("format_ref", 0)),
            )
        ].append(record)

    offsets: dict[tuple[int, int], tuple[float, float]] = {}
    for candidates in grouped.values():
        ordered = sorted(candidates, key=lambda item: float(item["y"]), reverse=True)
        clusters: list[list[dict[str, object]]] = []
        for record in ordered:
            y = float(record["y"]) * SHEET_UNIT
            if (
                not clusters
                or abs(float(clusters[-1][-1]["y"]) * SHEET_UNIT - y) > 180
            ):
                clusters.append([record])
            else:
                clusters[-1].append(record)
        for cluster in clusters:
            texts = {str(record["text"]).strip() for record in cluster}
            if (
                len(cluster) < 5
                or "SEE ISO" not in texts
                or not any(
                    re.fullmatch(r"\d{2}(?:-[A-Za-z0-9_]+){3,}", text)
                    for text in texts
                )
                or sum(
                    bool(re.fullmatch(r"(?:E|N|EL)\s+[+-]?\d+", text))
                    for text in texts
                ) < 2
            ):
                continue
            # A later revision can cloud only one changed line of an otherwise
            # ordinary connection-note block.  In that case the raw text is
            # already in its intended page space.  Attracting the whole block
            # to another cloud empties the small source cloud and piles the
            # note over unrelated piping.  An anchor inside any closed cloud
            # is direct ownership evidence and therefore vetoes relocation.
            if any(
                box[0] <= float(record["x"]) * SHEET_UNIT <= box[2]
                and box[1] <= float(record["y"]) * SHEET_UNIT <= box[3]
                for record in cluster
                for box in cloud_boxes
            ):
                continue
            x = float(cluster[0]["x"]) * SHEET_UNIT
            ys = [float(record["y"]) * SHEET_UNIT for record in cluster]
            top_baseline = max(ys)
            matching_clouds = [
                box
                for box in cloud_boxes
                if box[0] <= x <= box[2]
                and top_baseline < box[1]
                and box[1] - top_baseline
                <= max(1400.0, (box[3] - box[1]) * 2.0)
            ]
            if psm is not None:
                source_widths = [
                    box[2] - box[0]
                    for record in cluster
                    for box in psm_bboxes(psm, int(record["graphic_ref"]))
                    if box[2] - box[0] <= 1600
                ]
                if source_widths:
                    widest_source_line = max(source_widths)
                    matching_clouds = [
                        box
                        for box in matching_clouds
                        if box[2] - box[0] >= widest_source_line
                    ]
            if not matching_clouds:
                continue
            cloud = min(matching_clouds, key=lambda box: box[1] - top_baseline)
            cloud_height = cloud[3] - cloud[1]
            target_top_baseline = cloud[3] - max(90.0, cloud_height * 0.17)
            dy = target_top_baseline - top_baseline
            for record in cluster:
                offsets[(int(record["graphic_ref"]), int(record["text_start"]))] = (
                    0.0,
                    dy,
                )
    return offsets


def compass_collision_connection_text_offsets(
    records: list[dict[str, object]],
    psm: bytes,
    template_circles: list[dict[str, object]],
) -> dict[tuple[int, int], tuple[float, float]]:
    """Move a connection-note block out of the template north-arrow symbol.

    A small family of Chengda sheets keeps the ISO drawing in page space but
    leaves the upper-left connection note in a parent-local position.  The
    resulting note visibly overlaps the fixed north arrow.  The template's
    decoded circle is stable source geometry, so use its size to construct a
    clearance envelope and move the complete note block together.  This is
    deliberately collision-gated; ordinary connection notes are untouched.
    """

    if not template_circles:
        return {}
    compass = max(template_circles, key=lambda item: float(item["radius"]))
    center_x = float(compass["center_x"])
    center_y = float(compass["center_y"])
    radius = float(compass["radius"])
    exclusion = (
        center_x - radius * 1.65,
        center_y - radius * 1.60,
        center_x + radius * 2.20,
        center_y + radius * 1.70,
    )

    def is_connection_line(text: str) -> bool:
        return bool(
            text == "SEE ISO"
            or re.fullmatch(r"\d{6}", text)
            or re.fullmatch(r"\d{2}(?:-[A-Za-z0-9_]+){3,}", text)
            or re.fullmatch(r"(?:E|N|EL)\s+[+-]?\d+", text)
        )

    grouped: dict[tuple[int, int, int], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        text = str(record.get("text", "")).strip()
        if (
            not is_connection_line(text)
            or abs(float(record.get("direction_x", 0.0)) - 1.0) > 0.05
            or abs(float(record.get("direction_y", 0.0))) > 0.05
        ):
            continue
        grouped[
            (
                round(float(record["x"]) * SHEET_UNIT / 4),
                int(record["style_ref"]),
                int(record.get("format_ref", 0)),
            )
        ].append(record)

    offsets: dict[tuple[int, int], tuple[float, float]] = {}
    for candidates in grouped.values():
        ordered = sorted(candidates, key=lambda item: float(item["y"]), reverse=True)
        clusters: list[list[dict[str, object]]] = []
        for record in ordered:
            y = float(record["y"]) * SHEET_UNIT
            if not clusters or abs(float(clusters[-1][-1]["y"]) * SHEET_UNIT - y) > 180:
                clusters.append([record])
            else:
                clusters[-1].append(record)
        for cluster in clusters:
            texts = {str(record["text"]).strip() for record in cluster}
            if (
                len(cluster) < 5
                or "SEE ISO" not in texts
                or not any(
                    re.fullmatch(r"\d{2}(?:-[A-Za-z0-9_]+){3,}", text)
                    for text in texts
                )
                or sum(
                    bool(re.fullmatch(r"(?:E|N|EL)\s+[+-]?\d+", text))
                    for text in texts
                ) < 2
            ):
                continue
            source_boxes = [
                box
                for record in cluster
                if (box := psm_bbox(psm, int(record["graphic_ref"]))) is not None
            ]
            if not source_boxes:
                continue
            median_height = median(box[3] - box[1] for box in source_boxes)
            block_left = min(box[0] for box in source_boxes)
            block_right = max(box[2] for box in source_boxes)
            block_bottom = min(
                (psm_bbox(psm, int(record["graphic_ref"])) or (
                    float(record["x"]) * SHEET_UNIT,
                    float(record["y"]) * SHEET_UNIT - median_height * 0.20,
                    float(record["x"]) * SHEET_UNIT,
                    float(record["y"]) * SHEET_UNIT + median_height * 0.80,
                ))[1]
                for record in cluster
            )
            block_top = max(
                (psm_bbox(psm, int(record["graphic_ref"])) or (
                    float(record["x"]) * SHEET_UNIT,
                    float(record["y"]) * SHEET_UNIT - median_height * 0.20,
                    float(record["x"]) * SHEET_UNIT,
                    float(record["y"]) * SHEET_UNIT + median_height * 0.80,
                ))[3]
                for record in cluster
            )
            block = (block_left, block_bottom, block_right, block_top)
            if not (
                block[0] <= exclusion[2]
                and block[2] >= exclusion[0]
                and block[1] <= exclusion[3]
                and block[3] >= exclusion[1]
            ):
                continue
            dx = exclusion[2] - block[0]
            dy = exclusion[1] - block[3]
            if dx <= 0 or dy >= 0 or dx > radius * 4 or abs(dy) > radius * 5:
                continue
            for record in cluster:
                offsets[(int(record["graphic_ref"]), int(record["text_start"]))] = (
                    dx,
                    dy,
                )
    return offsets


def revision_cloud_connection_text_offsets(
    records: list[dict[str, object]],
    cloud_groups: list[list[dict[str, float | int]]],
    psm: bytes,
) -> dict[tuple[int, int], tuple[float, float]]:
    """Recover the smallest parent offset that keeps a ``CONN TO`` block in its cloud.

    Some Chengda sheets store these notes in a cloud-local coordinate space.
    The ordinary Sheet anchor is then close to the cloud's outer scallop, even
    though the issued plot applies a parent translation before drawing it.
    Ownership is intentionally strict: a common x/style/format text block must
    contain ``CONN TO``, a connection description and coordinate lines, and
    must already overlap one and only one closed revision cloud.  This keeps
    unrelated free notes from being attracted to a nearby revision cloud.
    """

    cloud_boxes: list[tuple[float, float, float, float]] = []
    for group in cloud_groups:
        bounds = [
            (
                float(arc["center_x"]) - float(arc["radius"]),
                float(arc["center_y"]) - float(arc["radius"]),
                float(arc["center_x"]) + float(arc["radius"]),
                float(arc["center_y"]) + float(arc["radius"]),
            )
            for arc in group
        ]
        if bounds:
            cloud_boxes.append(
                (
                    min(box[0] for box in bounds),
                    min(box[1] for box in bounds),
                    max(box[2] for box in bounds),
                    max(box[3] for box in bounds),
                )
            )

    def is_note_line(text: str) -> bool:
        return bool(
            text == "CONN TO"
            or "NPD" in text
            or "LATERAL" in text
            or re.fullmatch(r".+\s+in\s+BE\s+Undefined", text, re.IGNORECASE)
            or re.fullmatch(r"(?:E|N|EL)\s+[+-]?\d+", text)
        )

    grouped: dict[tuple[int, int, int], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        text = str(record.get("text", "")).strip()
        if (
            not is_note_line(text)
            or abs(float(record.get("direction_x", 0.0)) - 1.0) > 0.05
            or abs(float(record.get("direction_y", 0.0))) > 0.05
        ):
            continue
        grouped[
            (
                round(float(record["x"]) * SHEET_UNIT / 4),
                int(record["style_ref"]),
                int(record.get("format_ref", 0)),
            )
        ].append(record)

    offsets: dict[tuple[int, int], tuple[float, float]] = {}
    for candidates in grouped.values():
        ordered = sorted(candidates, key=lambda item: float(item["y"]), reverse=True)
        clusters: list[list[dict[str, object]]] = []
        for record in ordered:
            y = float(record["y"]) * SHEET_UNIT
            if not clusters or abs(float(clusters[-1][-1]["y"]) * SHEET_UNIT - y) > 180:
                clusters.append([record])
            else:
                clusters[-1].append(record)
        for cluster in clusters:
            texts = {str(record["text"]).strip() for record in cluster}
            coordinate_count = sum(
                bool(re.fullmatch(r"(?:E|N|EL)\s+[+-]?\d+", text))
                for text in texts
            )
            if (
                len(cluster) < 5
                or "CONN TO" not in texts
                or coordinate_count < 2
                or not any(
                    "NPD" in text
                    or "LATERAL" in text
                    or re.fullmatch(r".+\s+in\s+BE\s+Undefined", text, re.IGNORECASE)
                    for text in texts
                )
            ):
                continue

            glyph_boxes = [
                box
                for record in cluster
                if (box := psm_bbox(psm, int(record["graphic_ref"]))) is not None
                and box[2] - box[0] <= 1600
                and box[3] - box[1] <= 320
            ]
            if not glyph_boxes:
                continue
            median_height = median(box[3] - box[1] for box in glyph_boxes)
            median_char_width = median(
                (box[2] - box[0]) / max(1, len(str(record["text"]).strip()))
                for record in cluster
                if (box := psm_bbox(psm, int(record["graphic_ref"]))) is not None
                and box[2] - box[0] <= 1600
                and box[3] - box[1] <= 320
            )
            estimated_boxes: list[tuple[float, float, float, float]] = []
            for record in cluster:
                box = psm_bbox(psm, int(record["graphic_ref"]))
                if box is None or box[2] - box[0] > 1600 or box[3] - box[1] > 320:
                    x = float(record["x"]) * SHEET_UNIT
                    baseline = float(record["y"]) * SHEET_UNIT
                    width = max(
                        median_height * 0.7,
                        median_char_width * len(str(record["text"]).strip()),
                    )
                    box = (x, baseline - median_height * 0.20, x + width, baseline + median_height * 0.80)
                estimated_boxes.append(tuple(float(value) for value in box))
            block = (
                min(box[0] for box in estimated_boxes),
                min(box[1] for box in estimated_boxes),
                max(box[2] for box in estimated_boxes),
                max(box[3] for box in estimated_boxes),
            )
            matching_clouds = [
                cloud
                for cloud in cloud_boxes
                if block[0] <= cloud[2]
                and block[2] >= cloud[0]
                and block[1] <= cloud[3]
                and block[3] >= cloud[1]
                and block[2] - block[0] <= (cloud[2] - cloud[0]) * 1.35
                and block[3] - block[1] <= (cloud[3] - cloud[1]) * 1.10
            ]
            if len(matching_clouds) != 1:
                continue
            cloud = matching_clouds[0]
            # Do not centre the block.  The issued drawing keeps the author's
            # original alignment and applies only the missing parent-container
            # translation.  Centring is visibly wrong for a long connection
            # description because it pushes the text into nearby design notes.
            inset_x = min(75.0, (cloud[2] - cloud[0]) * 0.10)
            inset_y = min(50.0, (cloud[3] - cloud[1]) * 0.07)
            if (
                block[0] >= cloud[0] + inset_x
                and block[2] <= cloud[2] - inset_x
                and block[1] >= cloud[1] + inset_y
                and block[3] <= cloud[3] - inset_y
            ):
                continue
            inner_left = cloud[0] + inset_x
            inner_right = cloud[2] - inset_x
            inner_bottom = cloud[1] + inset_y
            inner_top = cloud[3] - inset_y

            dx = 0.0
            if block[0] < inner_left:
                dx = inner_left - block[0]
            if block[2] + dx > inner_right:
                # Prefer the smaller correction if an exceptionally long line
                # cannot satisfy both bounds at once.
                right_dx = inner_right - block[2]
                if dx == 0.0 or abs(right_dx) < abs(dx):
                    dx = right_dx

            dy = 0.0
            if block[1] < inner_bottom:
                dy = inner_bottom - block[1]
            if block[3] + dy > inner_top:
                top_dy = inner_top - block[3]
                if dy == 0.0 or abs(top_dy) < abs(dy):
                    dy = top_dy
            if abs(dx) > (cloud[2] - cloud[0]) * 0.45 or abs(dy) > (cloud[3] - cloud[1]) * 0.45:
                continue
            for record in cluster:
                offsets[(int(record["graphic_ref"]), int(record["text_start"]))] = (dx, dy)
    return offsets


def pcf_cut_piece_specs(pcf_path: Path | None) -> list[tuple[int, float]]:
    """Return unique included PIPE cut lengths and bores from a PCF."""

    if pcf_path is None or not pcf_path.is_file():
        return []
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in pcf_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line and not line[0].isspace():
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)

    result: list[tuple[int, float]] = []
    seen: set[tuple[str, int, int]] = set()
    for block in blocks:
        if block[0].strip().upper() != "PIPE":
            continue
        payload = "\n".join(block)
        if re.search(r"^\s*MATERIAL-LIST\s+EXCLUDE\s*$", payload, re.MULTILINE):
            continue
        length_match = re.search(
            r"^\s*CUT-PIECE-LENGTH\s+([+-]?\d+(?:\.\d+)?)\s*$",
            payload,
            re.MULTILINE,
        )
        endpoint_match = re.search(
            r"^\s*END-POINT\s+[+-]?\d+(?:\.\d+)?\s+[+-]?\d+(?:\.\d+)?\s+"
            r"[+-]?\d+(?:\.\d+)?\s+([+-]?\d+(?:\.\d+)?)",
            payload,
            re.MULTILINE,
        )
        if length_match is None or endpoint_match is None:
            continue
        length = int(round(float(length_match.group(1))))
        bore = float(endpoint_match.group(1))
        uci_match = re.search(r"^\s*UCI\s+(.+?)\s*$", payload, re.MULTILINE)
        key = (uci_match.group(1).strip() if uci_match else "", length, round(bore * 1000))
        if key in seen:
            continue
        seen.add(key)
        result.append((length, bore))
    return result


def cut_pipe_table_text_adjustments(
    records: list[dict[str, object]],
    psm: bytes,
    pcf_path: Path | None,
) -> tuple[dict[tuple[int, int], tuple[float, float]], set[tuple[int, int]]]:
    """Reflow stale compound CUT PIPE LENGTH children using PCF evidence.

    The Shape2D stream can retain the previous table's child cells after a
    cut-piece list is rebuilt.  Visible ``<n>`` markers in the drawing define
    the active piece numbers; PCF PIPE cut length/bore pairs then select the
    corresponding table value records.  No literal drawing number, length or
    piece number is special-cased.
    """

    specs = pcf_cut_piece_specs(pcf_path)
    if not specs:
        return {}, set()
    header = next(
        (record for record in records if str(record.get("text", "")).strip() == "CUT PIPE LENGTH"),
        None,
    )
    if header is None:
        return {}, set()
    header_box = psm_bbox(psm, int(header["graphic_ref"]))
    if header_box is None:
        return {}, set()
    header_bottom = float(header_box[1])
    header_left = float(header_box[0])

    def local_box(record: dict[str, object]) -> tuple[float, float, float, float] | None:
        box = psm_bbox(psm, int(record["graphic_ref"]))
        if box is None:
            return None
        if not (
            header_left - 200 <= box[0] <= header_left + 2300
            and header_bottom - 900 <= box[1] < header_bottom - 120
        ):
            return None
        return tuple(float(value) for value in box)

    table_records = [record for record in records if local_box(record) is not None]
    table_keys = {
        (int(record["graphic_ref"]), int(record["text_start"])) for record in table_records
    }
    table_piece_records: dict[int, dict[str, object]] = {}
    for record in table_records:
        match = re.fullmatch(r"<(\d+)>", str(record["text"]).strip())
        if match:
            table_piece_records[int(match.group(1))] = record
    visible_piece_numbers = sorted(
        {
            int(match.group(1))
            for record in records
            if (match := re.fullmatch(r"<(\d+)>", str(record.get("text", "")).strip()))
            and (int(record["graphic_ref"]), int(record["text_start"])) not in table_keys
        }
    )
    if (
        not visible_piece_numbers
        or len(visible_piece_numbers) != len(specs)
        or any(number not in table_piece_records for number in visible_piece_numbers)
    ):
        return {}, set()
    # A normal table already has exactly the same active rows as the drawing
    # markers.  Leave it byte-for-byte positioned by SHA; the repair below is
    # only for a compound table that demonstrably retained extra stale cells.
    if set(table_piece_records) == set(visible_piece_numbers):
        return {}, set()

    numeric_records = [
        record
        for record in table_records
        if re.fullmatch(r"\d+", str(record["text"]).strip())
    ]
    length_records: dict[int, dict[str, object]] = {}
    for length, _bore in specs:
        candidates = [record for record in numeric_records if int(str(record["text"]).strip()) == length]
        if len(candidates) != 1:
            return {}, set()
        length_records[length] = candidates[0]

    # Associate each visible drawing marker with a PCF cut piece through the
    # nearby, parallel length dimension on the drawing.  Graphic-reference
    # proximity is not topology: after a compound table rebuild its refs can
    # make a short branch look like the long main pipe.  Direction plus local
    # dimension evidence remains stable across that rebuild.
    diagram_piece_records: dict[int, dict[str, object]] = {}
    for record in records:
        match = re.fullmatch(r"<(\d+)>", str(record.get("text", "")).strip())
        key = (int(record["graphic_ref"]), int(record["text_start"]))
        box = psm_bbox(psm, int(record["graphic_ref"]))
        if match and key not in table_keys and box is not None and box[0] < header_left - 300:
            diagram_piece_records[int(match.group(1))] = record
    if any(number not in diagram_piece_records for number in visible_piece_numbers):
        return {}, set()

    drawing_dimensions: list[tuple[dict[str, object], int, tuple[float, float]]] = []
    for record in records:
        text = str(record.get("text", "")).strip()
        if not re.fullmatch(r"\d+", text):
            continue
        box = psm_bbox(psm, int(record["graphic_ref"]))
        if box is None or box[0] >= header_left - 300:
            continue
        direction = (float(record["direction_x"]), float(record["direction_y"]))
        magnitude = math.hypot(*direction)
        if not 0.8 <= magnitude <= 1.2:
            continue
        drawing_dimensions.append((record, int(text), direction))

    edges: list[tuple[float, int, tuple[int, float]]] = []
    for number, marker in diagram_piece_records.items():
        marker_box = psm_bbox(psm, int(marker["graphic_ref"]))
        if marker_box is None:
            continue
        marker_center = (
            (marker_box[0] + marker_box[2]) / 2,
            (marker_box[1] + marker_box[3]) / 2,
        )
        marker_direction = (
            float(marker["direction_x"]),
            float(marker["direction_y"]),
        )
        marker_magnitude = math.hypot(*marker_direction)
        for spec in specs:
            length, _bore = spec
            tolerance = max(6.0, length * 0.005)
            candidates: list[float] = []
            for dimension, value, direction in drawing_dimensions:
                if abs(value - length) > tolerance:
                    continue
                direction_magnitude = math.hypot(*direction)
                parallel = abs(
                    marker_direction[0] * direction[0]
                    + marker_direction[1] * direction[1]
                ) / max(1e-9, marker_magnitude * direction_magnitude)
                if parallel < 0.97:
                    continue
                dimension_box = psm_bbox(psm, int(dimension["graphic_ref"]))
                if dimension_box is None:
                    continue
                dimension_center = (
                    (dimension_box[0] + dimension_box[2]) / 2,
                    (dimension_box[1] + dimension_box[3]) / 2,
                )
                candidates.append(
                    math.dist(marker_center, dimension_center)
                    + abs(value - length) * 50.0
                )
            if candidates:
                edges.append((min(candidates), number, spec))

    assigned: dict[int, tuple[int, float]] = {}
    used_specs: set[tuple[int, float]] = set()
    for _cost, number, spec in sorted(edges):
        if number in assigned or spec in used_specs:
            continue
        assigned[number] = spec
        used_specs.add(spec)
    if set(assigned) != set(visible_piece_numbers) or len(used_specs) != len(specs):
        return {}, set()

    piece_headers = sorted(
        (record for record in records if str(record.get("text", "")).strip() == "PIECE"),
        key=lambda record: float(record["x"]),
    )
    length_headers = sorted(
        (record for record in records if str(record.get("text", "")).strip() == "LENGTH"),
        key=lambda record: float(record["x"]),
    )
    npd_headers = sorted(
        (record for record in records if str(record.get("text", "")).strip() == "NPD"),
        key=lambda record: float(record["x"]),
    )
    piece_headers = [record for record in piece_headers if local_box(record) is None and psm_bbox(psm, int(record["graphic_ref"])) and abs(psm_bbox(psm, int(record["graphic_ref"]))[1] - header_bottom) < 180]
    length_headers = [record for record in length_headers if psm_bbox(psm, int(record["graphic_ref"])) and abs(psm_bbox(psm, int(record["graphic_ref"]))[1] - header_bottom) < 180]
    npd_headers = [record for record in npd_headers if psm_bbox(psm, int(record["graphic_ref"])) and abs(psm_bbox(psm, int(record["graphic_ref"]))[1] - header_bottom) < 180]
    if len(piece_headers) < 2 or len(length_headers) < 2 or len(npd_headers) < 2:
        return {}, set()
    piece_x = [float(record["x"]) * SHEET_UNIT for record in piece_headers[:2]]
    length_x = [float(record["x"]) * SHEET_UNIT for record in length_headers[:2]]
    npd_x = [float(record["x"]) * SHEET_UNIT for record in npd_headers[:2]]
    row_ys = sorted(
        {round(float(record["y"]) * SHEET_UNIT, 3) for record in table_piece_records.values()},
        reverse=True,
    )
    if not row_ys:
        return {}, set()
    row_step = median(a - b for a, b in zip(row_ys, row_ys[1:])) if len(row_ys) > 1 else 52.0
    if not 20 <= row_step <= 160:
        row_step = 52.0

    def bore_value(text: str) -> float | None:
        text = text.strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return float(text)
        match = re.fullmatch(r"(?:(\d+)\.)?(\d+)/(\d+)", text)
        if match:
            return float(match.group(1) or 0) + int(match.group(2)) / int(match.group(3))
        return None

    available_npd = [
        record
        for record in table_records
        if (value := bore_value(str(record["text"]))) is not None
        and any(abs(value - bore) <= 0.001 for _, bore in specs)
    ]
    chosen_keys: set[tuple[int, int]] = set()
    offsets: dict[tuple[int, int], tuple[float, float]] = {}
    used_npd: set[tuple[int, int]] = set()
    for index, number in enumerate(visible_piece_numbers):
        length, bore = assigned[number]
        column = index % 2
        row = index // 2
        target_y = row_ys[0] - row * row_step
        piece_record = table_piece_records[number]
        length_record = length_records[length]
        npd_candidates = [
            record
            for record in available_npd
            if (int(record["graphic_ref"]), int(record["text_start"])) not in used_npd
            and (value := bore_value(str(record["text"]))) is not None
            and abs(value - bore) <= 0.001
        ]
        if not npd_candidates:
            return {}, set()
        npd_record = min(
            npd_candidates,
            key=lambda record: abs(int(record["graphic_ref"]) - int(length_record["graphic_ref"])),
        )
        used_npd.add((int(npd_record["graphic_ref"]), int(npd_record["text_start"])))
        for record, column_anchors in (
            (piece_record, piece_x),
            (length_record, length_x),
            (npd_record, npd_x),
        ):
            key = (int(record["graphic_ref"]), int(record["text_start"]))
            chosen_keys.add(key)
            source_x = float(record["x"]) * SHEET_UNIT
            source_column = min(
                range(2), key=lambda candidate: abs(source_x - column_anchors[candidate])
            )
            offsets[key] = (
                column_anchors[column] - column_anchors[source_column],
                target_y - float(record["y"]) * SHEET_UNIT,
            )

    suppressible = {
        (int(record["graphic_ref"]), int(record["text_start"]))
        for record in table_records
        if re.fullmatch(r"<\d+>|\d+(?:\.\d+)?|(?:(?:\d+)\.)?\d+/\d+", str(record["text"]).strip())
    }
    return offsets, suppressible - chosen_keys


def filled_line_arrowheads(
    data: bytes,
    segments: list[tuple[float, float, float, float, int, int]],
) -> list[dict[str, object]]:
    """Decode filled triangular arrowheads owned by direct line graphics.

    These compact ``0x13`` contours are distinct from the Sheet221 logo
    outlines: their owner is a visible ``0x18/0x32`` line parent and their
    three closed edges meet one of that line's endpoints.
    """

    lines_by_owner: dict[int, list[tuple[float, float, float, float, int, int]]] = defaultdict(list)
    for segment in segments:
        lines_by_owner[int(segment[4])].append(segment)
    result: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x13\x00"), data):
        start = match.start()
        if start + 34 > len(data):
            continue
        record_size = struct.unpack_from("<I", data, start + 2)[0]
        end = start + 2 + record_size
        if not 120 <= record_size <= 512 or end > len(data):
            continue
        child_ref = struct.unpack_from("<I", data, start + 6)[0]
        owner_ref = struct.unpack_from("<I", data, start + 10)[0]
        style_ref = struct.unpack_from("<H", data, start + 20)[0]
        contour_count = struct.unpack_from("<I", data, start + 24)[0]
        if owner_ref not in lines_by_owner or contour_count != 1:
            continue
        position = start + 28
        try:
            segment_count = struct.unpack_from("<I", data, position)[0]
            position += 4
            flags = data[position : position + 2]
            position += 2
            if segment_count != 3 or flags != b"\x02\x01":
                continue
            edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
            for _ in range(3):
                if data[position] != 0x67:
                    edges = []
                    break
                position += 1
                x1, y1, x2, y2 = struct.unpack_from("<4d", data, position)
                position += 32
                edges.append(
                    (
                        (x1 * SHEET_UNIT, y1 * SHEET_UNIT),
                        (x2 * SHEET_UNIT, y2 * SHEET_UNIT),
                    )
                )
        except (IndexError, struct.error):
            continue
        if len(edges) != 3:
            continue
        points = [edges[0][0], edges[0][1], edges[1][1]]
        if (
            math.dist(edges[0][1], edges[1][0]) > 2
            or math.dist(edges[1][1], edges[2][0]) > 2
            or math.dist(edges[2][1], edges[0][0]) > 2
            or max(x for x, _ in points) - min(x for x, _ in points) > 200
            or max(y for _, y in points) - min(y for _, y in points) > 200
        ):
            continue
        center = (
            sum(point[0] for point in points) / 3,
            sum(point[1] for point in points) / 3,
        )
        owned_lines = lines_by_owner[owner_ref]
        if min(
            math.dist(center, (x * SHEET_UNIT, y * SHEET_UNIT))
            for x1, y1, x2, y2, _, _ in owned_lines
            for x, y in ((x1, y1), (x2, y2))
        ) > 150:
            continue
        result.append(
            {
                "child_ref": child_ref,
                "owner_ref": owner_ref,
                "style_ref": style_ref,
                "points": points,
                "center": center,
            }
        )
    return result


def revision_cloud_leader_endpoint_offsets(
    records: list[dict[str, object]],
    text_offsets: dict[tuple[int, int], tuple[float, float]],
    arrowheads: list[dict[str, object]],
    segments: list[tuple[float, float, float, float, int, int]],
    psm: bytes,
) -> dict[int, tuple[int, float, float]]:
    """Tie a moved cloud note to its source leader without moving the arrow tip."""

    arrows_by_owner = {int(item["owner_ref"]): item for item in arrowheads}
    adjustments: dict[int, tuple[int, float, float]] = {}
    for record in records:
        key = (int(record["graphic_ref"]), int(record.get("text_start", -1)))
        offset = text_offsets.get(key)
        text = str(record.get("text", "")).strip()
        if offset is None or not re.fullmatch(r"\d{2}(?:-[A-Za-z0-9_]+){3,}", text):
            continue
        bbox = psm_bbox(psm, int(record["graphic_ref"]))
        if bbox is None:
            continue
        note_endpoint = (bbox[2], (bbox[1] + bbox[3]) / 2)
        for x1, y1, x2, y2, owner_ref, child_ref in segments:
            arrow = arrows_by_owner.get(int(owner_ref))
            if arrow is None:
                continue
            endpoints = [
                (x1 * SHEET_UNIT, y1 * SHEET_UNIT),
                (x2 * SHEET_UNIT, y2 * SHEET_UNIT),
            ]
            arrow_center = tuple(float(value) for value in arrow["center"])
            arrow_index = min(
                range(2), key=lambda index: math.dist(endpoints[index], arrow_center)
            )
            note_index = 1 - arrow_index
            if math.dist(endpoints[note_index], note_endpoint) <= 100:
                adjustments[int(child_ref)] = (note_index, offset[0], offset[1])
    return adjustments


def plain_connection_leader_endpoint_offsets(
    records: list[dict[str, object]],
    text_offsets: dict[tuple[int, int], tuple[float, float]],
    segments: list[tuple[float, float, float, float, int, int]],
    psm: bytes,
) -> dict[int, tuple[int, float, float]]:
    """Move the note end of an unarrowed connection leader with its text.

    Connection leaders at page boundaries are not always serialized with a
    filled arrowhead.  A unique line endpoint immediately beside the ISO line
    number's right edge is sufficient structural evidence; the remote endpoint
    remains attached to the piping connection point.
    """

    adjustments: dict[int, tuple[int, float, float]] = {}
    for record in records:
        key = (int(record["graphic_ref"]), int(record.get("text_start", -1)))
        offset = text_offsets.get(key)
        text = str(record.get("text", "")).strip()
        if offset is None or not re.fullmatch(r"\d{2}(?:-[A-Za-z0-9_]+){3,}", text):
            continue
        bbox = psm_bbox(psm, int(record["graphic_ref"]))
        if bbox is None:
            continue
        note_endpoint = (bbox[2], (bbox[1] + bbox[3]) / 2)
        candidates: list[tuple[float, int, int]] = []
        for x1, y1, x2, y2, _owner_ref, child_ref in segments:
            endpoints = [
                (x1 * SHEET_UNIT, y1 * SHEET_UNIT),
                (x2 * SHEET_UNIT, y2 * SHEET_UNIT),
            ]
            length = math.dist(endpoints[0], endpoints[1])
            if not 120 <= length <= 1400:
                continue
            for endpoint_index, endpoint in enumerate(endpoints):
                distance = math.dist(endpoint, note_endpoint)
                if distance <= 100:
                    candidates.append((distance, int(child_ref), endpoint_index))
        if len(candidates) != 1:
            continue
        _distance, child_ref, endpoint_index = candidates[0]
        adjustments[child_ref] = (endpoint_index, offset[0], offset[1])
    return adjustments


def composite_segments(
    data: bytes,
    odd_parent_refs: set[int] | None = None,
) -> list[tuple[float, float, float, float, int, int]]:
    """Inventory type-5 child bounds stored in Shape2D composite records.

    The four uint16 values in a ``0x7B`` child entry are its page-space
    bounding box, not the endpoints of a visible line. Keep this decoder for
    record inventory and topology work, but never add its returned diagonals
    to the rendered line layer. Visible child geometry must come from the
    child's own primitive record (for example ``0x18/0x32``).
    """

    segments: list[tuple[float, float, float, float, int, int]] = []
    # Odd-aligned type-5 composites include still-unresolved backing/layout
    # frames in these files.  Admit one only when its declared parent is a
    # page-local dynamic-UCI graphic, which is direct SHA visibility evidence.
    # Even-aligned records retain the established compatibility behaviour.
    records = composite_records(data, alignment=0)
    if odd_parent_refs:
        records.extend(
            (parent_ref, children)
            for parent_ref, children in composite_records(data, alignment=1)
            if parent_ref in odd_parent_refs
        )
    for parent_ref, children in records:
        for child_ref, left, bottom, right, top, primitive_type in children:
            if primitive_type != 5 or (left == right and bottom == top):
                continue
            # Composite endpoints are stored at twice the page-coordinate
            # resolution, unlike the normalized float coordinates used by
            # ordinary Sheet segments. Convert them all the way back to the
            # renderer's normalized Sheet space before SVG multiplies by
            # SHEET_UNIT.
            segments.append(
                (
                    left / (2 * SHEET_UNIT),
                    bottom / (2 * SHEET_UNIT),
                    right / (2 * SHEET_UNIT),
                    top / (2 * SHEET_UNIT),
                    parent_ref,
                    child_ref,
                )
            )
    return segments


def text_anchor_matches_local_extent(
    anchor_x: float,
    anchor_y: float,
    bbox: tuple[float, float, float, float],
) -> bool:
    """Return whether a Sheet text anchor and PSM glyph extent are local peers.

    PSM supplies glyph size, but its lower-left corner is not the text
    baseline. Style ids vary between physical Sheet streams, so locality is a
    stronger cross-page test than a hard-coded style allow-list.
    """

    left, bottom, right, top = bbox
    height = max(1.0, top - bottom)
    if right - left > 4000.0 or height > 400.0:
        return False
    return (
        left - max(420.0, height * 5.0) <= anchor_x <= right + max(240.0, height * 3.0)
        and bottom - max(240.0, height * 3.0) <= anchor_y <= top + max(320.0, height * 4.0)
    )


def is_injected_weld_label_ref(graphic_ref: int) -> bool:
    """Identify the reserved text-reference range written by our weld injector."""

    # The injector reserves one 0x1000-wide block per physical page:
    # F01xxx for page 1, F02xxx for page 2, and so on.  These references must
    # never be matched to an unrelated source PSM object.
    return 0x00F01000 <= graphic_ref < 0x00F10000


def injected_weld_label_stored_length(graphic_ref: int) -> int:
    """Return the UTF-16 character count encoded by the SHA injector."""

    return 5 + ((int(graphic_ref) & 0x0F00) >> 8)


def injected_weld_label_anchor(
    graphic_ref: int,
    anchor_x: float,
    anchor_y: float,
    bbox: tuple[float, float, float, float],
    marker_center: tuple[float, float] | None = None,
) -> tuple[float, float, str]:
    """Recover the intended baseline of a synthetic weld label.

    Sheet6 borrows a direct-baseline style, while later physical sheets use a
    local-offset text template.  ``pack_text_record`` stores those later
    anchors with the style offset inverted; the synthesized fallback bbox
    therefore recovers the exact intended left edge and vertical centre.
    """

    left, bottom, right, top = bbox
    height = top - bottom
    if marker_center is not None:
        center_x, center_y = marker_center
        return (
            center_x,
            center_y,
            "sha-injected-weld-marker-center",
        )

    page = (graphic_ref - 0x00F00000) // 0x1000
    if page <= 1:
        return anchor_x, anchor_y, "sha-injected-weld-label-anchor"
    # The injector's direct baseline convention is centre_y - 0.30 * height.
    # Since bottom = centre_y - 0.50 * height, this is bottom + 0.20 * height.
    return left, bottom + height * 0.20, "sha-injected-weld-label-local-style-inverse"


def injected_weld_marker_centers(data: bytes) -> dict[int, tuple[float, float]]:
    """Recover marker centres from the 16 closed segments before each label."""

    centers: dict[int, tuple[float, float]] = {}
    line_record_size = struct.calcsize("<I7H4d")
    for record in text_records(data):
        graphic_ref = int(record["graphic_ref"])
        if not is_injected_weld_label_ref(graphic_ref):
            continue
        object_start = int(record["text_start"]) - 24
        marker_start = object_start - 16 * line_record_size
        if marker_start < 0:
            continue
        points: list[tuple[float, float]] = []
        valid = True
        for index in range(16):
            start = marker_start + index * line_record_size
            _, zero_a, parent_ref, zero_b, zero_c, style_ref, zero_d = struct.unpack_from(
                "<7H", data, start + 4
            )
            if (
                zero_a
                or zero_b
                or zero_c
                or zero_d
                or parent_ref != 1414
                or style_ref != 245
            ):
                valid = False
                break
            x1, y1, x2, y2 = struct.unpack_from("<4d", data, start + 18)
            if not all(0 <= value <= 1 for value in (x1, y1, x2, y2)):
                valid = False
                break
            points.extend(((x1 * SHEET_UNIT, y1 * SHEET_UNIT), (x2 * SHEET_UNIT, y2 * SHEET_UNIT)))
        if valid and points:
            centers[graphic_ref] = (
                (min(point[0] for point in points) + max(point[0] for point in points)) / 2,
                (min(point[1] for point in points) + max(point[1] for point in points)) / 2,
            )
    return centers


def injected_weld_geometry_child_refs(data: bytes) -> tuple[set[int], set[int]]:
    """Return synthetic leader and circular-marker child references.

    The injector appends one leader followed by sixteen closed boundary
    segments immediately before each reserved weld-label record. Identifying
    that exact record group lets the renderer style only injected markup red,
    without changing native SHA geometry that happens to share the line style.
    """

    leaders: set[int] = set()
    markers: set[int] = set()
    line_record_size = struct.calcsize("<I7H4d")
    group_size = 17
    for record in text_records(data):
        graphic_ref = int(record["graphic_ref"])
        if not is_injected_weld_label_ref(graphic_ref):
            continue
        text_object_start = int(record["text_start"]) - 24
        group_start = text_object_start - group_size * line_record_size
        if group_start < 0:
            continue
        child_refs: list[int] = []
        valid = True
        for index in range(group_size):
            start = group_start + index * line_record_size
            child_ref = struct.unpack_from("<I", data, start)[0]
            _, zero_a, parent_ref, zero_b, zero_c, style_ref, zero_d = struct.unpack_from(
                "<7H", data, start + 4
            )
            if (
                zero_a
                or zero_b
                or zero_c
                or zero_d
                or parent_ref != 1414
                or style_ref != 245
            ):
                valid = False
                break
            child_refs.append(child_ref)
        if valid:
            leaders.add(child_refs[0])
            markers.update(child_refs[1:])
    return leaders, markers


def injected_weld_marker_circles(data: bytes) -> list[tuple[int, float, float, float]]:
    """Recover one exact SVG circle from each injected 16-edge boundary."""

    circles: list[tuple[int, float, float, float]] = []
    line_record_size = struct.calcsize("<I7H4d")
    for record in text_records(data):
        graphic_ref = int(record["graphic_ref"])
        if not is_injected_weld_label_ref(graphic_ref):
            continue
        text_object_start = int(record["text_start"]) - 24
        marker_start = text_object_start - 16 * line_record_size
        if marker_start < 0:
            continue
        vertices: list[tuple[float, float]] = []
        valid = True
        for index in range(16):
            start = marker_start + index * line_record_size
            _, zero_a, parent_ref, zero_b, zero_c, style_ref, zero_d = struct.unpack_from(
                "<7H", data, start + 4
            )
            if (
                zero_a
                or zero_b
                or zero_c
                or zero_d
                or parent_ref != 1414
                or style_ref != 245
            ):
                valid = False
                break
            x1, y1, _, _ = struct.unpack_from("<4d", data, start + 18)
            vertices.append((x1 * SHEET_UNIT, y1 * SHEET_UNIT))
        if not valid or not vertices:
            continue
        center_x = (min(x for x, _ in vertices) + max(x for x, _ in vertices)) / 2
        center_y = (min(y for _, y in vertices) + max(y for _, y in vertices)) / 2
        radius = sum(math.hypot(x - center_x, y - center_y) for x, y in vertices) / len(vertices)
        circles.append((graphic_ref, center_x, center_y, radius))
    return circles


def psm_ellipses(data: bytes, psm: bytes) -> list[tuple[int, tuple[int, int, int, int]]]:
    """Read the observed Shape2D ellipse record and its SHA PSM envelope."""

    ellipses: list[tuple[int, tuple[int, int, int, int]]] = []
    signature = b"\x59\x00\x2b\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        ref = struct.unpack_from("<I", data, match.start() + 6)[0]
        bbox = psm_bbox(psm, ref)
        if bbox is not None:
            left, bottom, right, top = bbox
            # The 59/2B signature is also used by page/layout containers.
            # Their PSM envelopes can span thousands of page units and are
            # not drawn ellipses.  Actual ISO instrument/connection symbols
            # are local objects; rendering the containers creates spurious
            # page-sized circles absent from the source ISO.
            if right - left > 1000 or top - bottom > 1000:
                continue
            ellipses.append((ref, bbox))
    return ellipses


def ellipse_anchors(data: bytes) -> dict[int, tuple[float, float]]:
    """Read the Shape2D anchor for observed 59/2B ellipse-like primitives.

    PSM stores an object's rendered envelope.  For the tiny weld/connection
    symbols this envelope is in a local layout space, while the primitive
    itself carries the page-space centre at byte offset 24.  The second
    reference is the graphic id used by dynamic UCI attributes.
    """

    anchors: dict[int, tuple[float, float]] = {}
    signature = b"\x59\x00\x2b\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        if start + 40 > len(data):
            continue
        graphic_ref = struct.unpack_from("<I", data, start + 10)[0]
        x, y = struct.unpack_from("<dd", data, start + 24)
        if 0.01 <= x <= 1 and 0.01 <= y <= PAGE_HEIGHT / SHEET_UNIT:
            anchors[graphic_ref] = (x * SHEET_UNIT, y * SHEET_UNIT)
    return anchors


def ellipse_primitive_anchors(data: bytes) -> dict[int, tuple[float, float]]:
    """Read page-space centres keyed by an ellipse primitive's own reference.

    Instrument bubbles use the first reference in the observed 59/2B record;
    unlike the tiny UCI weld symbols, their PSM envelope is in a shifted local
    layout space.  Keeping this mapping separate avoids changing weld logic.
    """

    anchors: dict[int, tuple[float, float]] = {}
    signature = b"\x59\x00\x2b\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        if start + 40 > len(data):
            continue
        primitive_ref = struct.unpack_from("<I", data, start + 6)[0]
        x, y = struct.unpack_from("<dd", data, start + 24)
        if 0.01 <= x <= 1 and 0.01 <= y <= PAGE_HEIGHT / SHEET_UNIT:
            anchors[primitive_ref] = (x * SHEET_UNIT, y * SHEET_UNIT)
    return anchors


def instrument_tag_records(
    data: bytes,
    circles: list[dict[str, object]],
    style_data: bytes,
) -> list[dict[str, object]]:
    """Associate split instrument-tag text with its direct Sheet circle.

    Instrument bubbles store each displayed line as an independent text
    object.  The common parent relation is not exposed through PSM, but every
    line's page-space anchor lies inside the direct 0x59 circle.  This is a
    stronger association than attempting to reconstruct a tag from nearby
    free annotation text.
    """

    instrument_circles = [circle for circle in circles if float(circle["radius"]) > 60.0]
    grouped: dict[int, list[dict[str, object]]] = defaultdict(list)
    for record in text_records(data):
        # Weld labels injected into the SHA use a reserved graphic-reference
        # range.  A layout candidate may legitimately place one over an
        # existing instrument bubble; spatial containment must not change its
        # semantic type or the generic weld renderer will later omit it.
        if is_injected_weld_label_ref(int(record["graphic_ref"])):
            continue
        text = str(record["text"]).strip()
        if not re.fullmatch(r"[A-Z0-9-]{3,16}", text):
            continue
        x = float(record["x"]) * SHEET_UNIT
        y = float(record["y"]) * SHEET_UNIT
        circle = next(
            (
                candidate
                for candidate in instrument_circles
                if math.hypot(
                    x - float(candidate["center_x"]),
                    y - float(candidate["center_y"]),
                )
                <= float(candidate["radius"]) * 0.95
            ),
            None,
        )
        if circle is None:
            continue
        format_ref = int(record.get("format_ref", 0))
        grouped[int(circle["child_ref"])].append(
            {
                **record,
                "x_page": x,
                "y_page": y,
                "circle": circle,
                # Instrument tags are a semantic bubble symbol.  Their compact
                # format id resolves to a smaller generic Arial run than the
                # plotted tag in the reference ISO.  The bubble diameter is
                # the stable source measurement across physical sheets.
                "font_size": float(circle["radius"]) * 0.45,
            }
        )

    result: list[dict[str, object]] = []
    for circle in sorted(instrument_circles, key=lambda item: -float(item["center_x"])):
        records = sorted(
            grouped.get(int(circle["child_ref"]), []),
            key=lambda item: -float(item["y_page"]),
        )
        if not records:
            continue
        font_size = float(records[0]["font_size"])
        center_y = float(circle["center_y"])
        for index, record in enumerate(records):
            record["y_page"] = center_y + (
                (len(records) - 1) / 2 - index
            ) * font_size
        result.extend(records)
    return result


def template_line_segments(data: bytes) -> list[tuple[float, float, float, float, int, int]]:
    """Decode the shared title-block line record used by Sheet221.

    Unlike Sheet6 primitives, this template record begins with a fixed opcode
    and has its x1/y1/x2/y2 doubles at byte offset 24.
    """

    segments: list[tuple[float, float, float, float, int, int]] = []
    signature = b"\x18\x00\x32\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        if start + 56 > len(data):
            continue
        child_ref = struct.unpack_from("<I", data, start + 6)[0]
        template_ref = struct.unpack_from("<I", data, start + 10)[0]
        x1, y1, x2, y2 = struct.unpack_from("<4d", data, start + 24)
        if not all(-0.03 <= value <= 1.05 for value in (x1, y1, x2, y2)):
            continue
        length = math.hypot(x2 - x1, y2 - y1) * SHEET_UNIT
        if length < 4:
            continue
        segments.append((x1, y1, x2, y2, template_ref, child_ref))
    return segments


def template_circle_primitives(data: bytes) -> list[dict[str, object]]:
    """Decode direct centre/radius circles owned by the shared template.

    The Chengda north-arrow ring is a ``0x59/0x2B`` Shape2D primitive.  It
    has no PSM glyph envelope: its own record is the complete geometry, with
    centre/radius doubles at byte 24.  Validating the declared record length,
    template owner and finite page coordinates avoids treating layout
    containers that share the opcode as visible circles.
    """

    circles: list[dict[str, object]] = []
    signature = b"\x59\x00\x2b\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        record_end = start + 6 + 0x2B
        if record_end > len(data):
            continue
        child_ref, owner_ref, object_group = struct.unpack_from("<III", data, start + 6)
        reserved, style_ref, reserved_tail = struct.unpack_from("<HHH", data, start + 18)
        center_x, center_y, radius = struct.unpack_from("<3d", data, start + 24)
        if (
            reserved
            or reserved_tail
            or not all(math.isfinite(value) for value in (center_x, center_y, radius))
            or not (0 <= center_x <= 1 and 0 <= center_y <= PAGE_HEIGHT / SHEET_UNIT)
            or not (0 < radius <= 0.1)
        ):
            continue
        circles.append(
            {
                "child_ref": child_ref,
                "owner_ref": owner_ref,
                "object_group": object_group,
                "style_ref": style_ref,
                "center_x": center_x * SHEET_UNIT,
                "center_y": center_y * SHEET_UNIT,
                "radius": radius * SHEET_UNIT,
            }
        )
    return circles


def template_polyline_paths(data: bytes) -> list[dict[str, object]]:
    """Decode counted ``0x84`` polylines used by the template stroke font.

    Chengda's weld-legend words are split between ordinary ``0x18/0x32``
    two-point lines and variable-length ``0x84`` point strings.  The latter
    are polylines, not Bezier controls: a four-point record, for example,
    draws the three sides of a stroke-font C.  The record length is exactly
    ``24 + point_count * 16`` after the six-byte opcode/length prefix.
    """

    paths: list[dict[str, object]] = []
    signature = b"\x84\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        if start + 30 > len(data):
            continue
        record_size = struct.unpack_from("<I", data, start + 2)[0]
        record_end = start + 6 + record_size
        if record_end > len(data):
            continue
        child_ref, owner_ref, object_group = struct.unpack_from("<III", data, start + 6)
        reserved, style_ref, reserved_tail = struct.unpack_from("<HHH", data, start + 18)
        point_count = struct.unpack_from("<I", data, start + 24)[0]
        path_flags = data[start + 28 : start + 30]
        if (
            reserved
            or reserved_tail
            or not 2 <= point_count <= 1000
            or record_size != 24 + point_count * 16
            or path_flags[0] not in {1, 2, 3}
            or path_flags[1] != 1
        ):
            continue
        values = struct.unpack_from(f"<{point_count * 2}d", data, start + 30)
        if not all(math.isfinite(value) and -0.03 <= value <= 1.05 for value in values):
            continue
        points = [
            (values[index] * SHEET_UNIT, values[index + 1] * SHEET_UNIT)
            for index in range(0, len(values), 2)
        ]
        paths.append(
            {
                "child_ref": child_ref,
                "owner_ref": owner_ref,
                "object_group": object_group,
                "style_ref": style_ref,
                "point_count": point_count,
                "flags": path_flags,
                "points": points,
            }
        )
    return paths


def template_closed_triangles(
    data: bytes,
    circles: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Recover closed three-edge arrowheads inside template circles.

    Shape2D stores the six north-arrow heads as three independent line
    primitives under one parent per head.  The source PDF fills those closed
    contours.  Detecting the topology and requiring every vertex to lie in a
    decoded template circle preserves that fill semantics without relying on
    the arrowheads' page coordinates.
    """

    circles = template_circle_primitives(data) if circles is None else circles
    styles = template_line_style_refs(data)
    grouped: dict[int, list[tuple[float, float, float, float, int, int]]] = defaultdict(list)
    for x1, y1, x2, y2, parent_ref, child_ref in template_line_segments(data):
        style_ref = styles.get(child_ref)
        if style_ref is not None:
            grouped[parent_ref].append(
                (
                    x1 * SHEET_UNIT,
                    y1 * SHEET_UNIT,
                    x2 * SHEET_UNIT,
                    y2 * SHEET_UNIT,
                    child_ref,
                    style_ref,
                )
            )

    result: list[dict[str, object]] = []
    for parent_ref, edges in grouped.items():
        if len(edges) != 3:
            continue
        vertices: dict[tuple[float, float], tuple[float, float]] = {}
        adjacency: dict[tuple[float, float], list[tuple[float, float]]] = defaultdict(list)
        edge_styles = {edge[5] for edge in edges}
        if len(edge_styles) != 1:
            continue
        for x1, y1, x2, y2, _, _ in edges:
            first = (round(x1, 5), round(y1, 5))
            second = (round(x2, 5), round(y2, 5))
            vertices[first] = (x1, y1)
            vertices[second] = (x2, y2)
            adjacency[first].append(second)
            adjacency[second].append(first)
        if len(vertices) != 3 or any(len(neighbours) != 2 for neighbours in adjacency.values()):
            continue
        if not any(
            all(
                math.hypot(x - float(circle["center_x"]), y - float(circle["center_y"]))
                <= float(circle["radius"]) * 1.05
                for x, y in vertices.values()
            )
            for circle in circles
        ):
            continue
        first = next(iter(vertices))
        second = adjacency[first][0]
        third = next(vertex for vertex in adjacency[second] if vertex != first)
        result.append(
            {
                "parent_ref": parent_ref,
                "child_refs": [edge[4] for edge in edges],
                "style_ref": edge_styles.pop(),
                "points": [vertices[first], vertices[second], vertices[third]],
            }
        )
    return result


def template_fill_style_colors(style_data: bytes) -> dict[int, str]:
    """Decode fill colours used by Shape2D free-outline styles.

    The observed ``0x30/0x5A`` style record stores its style id at byte 20
    and an OLE ``COLORREF`` at byte 44.  ``0x00FFFFFF`` is used by this SHA
    producer as the default monochrome foreground sentinel; the plotted PDF
    confirms that it is black, while ordinary values retain COLORREF's
    little-endian red/green/blue byte order.
    """

    colors: dict[int, str] = {}
    signature = b"\x30\x00\x5a\x00"
    for match in re.finditer(re.escape(signature), style_data):
        start = match.start()
        if start + 48 > len(style_data):
            continue
        style_ref = struct.unpack_from("<I", style_data, start + 20)[0]
        packed = struct.unpack_from("<I", style_data, start + 44)[0]
        if not 1 <= style_ref <= 0xFFFF or packed > 0xFFFFFF:
            continue
        if packed == 0xFFFFFF:
            colors[style_ref] = "#000000"
            continue
        red = packed & 0xFF
        green = (packed >> 8) & 0xFF
        blue = (packed >> 16) & 0xFF
        colors[style_ref] = f"#{red:02x}{green:02x}{blue:02x}"
    return colors


def template_filled_paths(data: bytes, style_data: bytes) -> list[dict[str, object]]:
    """Decode validated ``0x13`` filled free-outline records from a template.

    Chengda's coloured emblem and its Chinese/English wordmark are stored as
    vector outlines in ``Sheet221``.  Each record owns one or more contours.
    A ``0x67`` item is a two-point edge; a ``0x68`` item is a counted polygon.
    The record length, owner/group ids, contour flags and fixed trailer make a
    substantially stronger boundary check than scanning plausible doubles.
    """

    colors = template_fill_style_colors(style_data)
    result: list[dict[str, object]] = []
    trailer = bytes.fromhex("ea 8c a0 39 59 3e 29 46") * 2 + b"\x01"
    for match in re.finditer(re.escape(b"\x13\x00"), data):
        start = match.start()
        if start + 34 > len(data):
            continue
        record_size = struct.unpack_from("<I", data, start + 2)[0]
        end = start + 2 + record_size
        if not 34 < record_size <= 200_000 or end > len(data):
            continue
        child_ref = struct.unpack_from("<I", data, start + 6)[0]
        owner_ref, object_group = struct.unpack_from("<II", data, start + 10)
        style_ref = struct.unpack_from("<I", data, start + 20)[0]
        contour_count = struct.unpack_from("<I", data, start + 24)[0]
        if (
            owner_ref != 221
            or object_group != 7075
            or style_ref not in colors
            or not 1 <= contour_count <= 100
            or data[end - len(trailer) : end] != trailer
        ):
            continue

        position = start + 28
        subpaths: list[list[tuple[float, float]]] = []
        valid = True
        try:
            for _ in range(contour_count):
                segment_count = struct.unpack_from("<I", data, position)[0]
                position += 4
                if (
                    not 1 <= segment_count <= 10_000
                    or data[position : position + 2] not in {b"\x02\x01", b"\x04\x01"}
                ):
                    valid = False
                    break
                position += 2
                points: list[tuple[float, float]] = []
                for _ in range(segment_count):
                    opcode = data[position]
                    position += 1
                    if opcode == 0x67:
                        x1, y1, x2, y2 = struct.unpack_from("<4d", data, position)
                        position += 32
                        if not points:
                            points.append((x1, y1))
                        elif math.hypot(points[-1][0] - x1, points[-1][1] - y1) > 2 / SHEET_UNIT:
                            valid = False
                            break
                        points.append((x2, y2))
                    elif opcode == 0x68:
                        point_count = struct.unpack_from("<I", data, position)[0]
                        position += 4
                        if (
                            not 3 <= point_count <= 20_000
                            or data[position : position + 2]
                            not in {b"\x02\x01", b"\x03\x01"}
                        ):
                            valid = False
                            break
                        position += 2
                        values = struct.unpack_from(
                            "<" + "d" * (point_count * 2), data, position
                        )
                        position += point_count * 16
                        points.extend(zip(values[0::2], values[1::2]))
                    else:
                        valid = False
                        break
                if not valid:
                    break
                if len(points) < 3 or not all(
                    math.isfinite(value) and -0.03 <= value <= 1.05
                    for point in points
                    for value in point
                ):
                    valid = False
                    break
                subpaths.append(
                    [(x * SHEET_UNIT, y * SHEET_UNIT) for x, y in points]
                )
        except (IndexError, struct.error):
            valid = False
        if not valid or position != end - len(trailer):
            continue
        all_points = [point for subpath in subpaths for point in subpath]
        xs = [point[0] for point in all_points]
        ys = [point[1] for point in all_points]
        result.append(
            {
                "child_ref": child_ref,
                "owner_ref": owner_ref,
                "object_group": object_group,
                "style_ref": style_ref,
                "fill": colors[style_ref],
                "subpaths": subpaths,
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
            }
        )
    return result


def unique_line_segments(
    segments: list[tuple[float, float, float, float, int, int]],
) -> list[tuple[float, float, float, float, int, int]]:
    """Remove the same primitive decoded through overlapping record scanners."""

    result: list[tuple[float, float, float, float, int, int]] = []
    seen: set[tuple[tuple[float, float], tuple[float, float], int, int]] = set()
    for segment in segments:
        x1, y1, x2, y2, parent_ref, child_ref = segment
        endpoint_a = (round(x1, 8), round(y1, 8))
        endpoint_b = (round(x2, 8), round(y2, 8))
        start, end = sorted((endpoint_a, endpoint_b))
        key = (start, end, parent_ref, child_ref)
        if key in seen:
            continue
        seen.add(key)
        result.append(segment)
    return result


def template_line_object_groups(data: bytes) -> dict[int, int]:
    """Return validated local object groups for ``18/32`` graphic refs.

    A subset of later-Sheet line graphics has an adjacent ``0x13/0xAC``
    relation record.  Its graphic reference at byte 10 and local group id at
    byte 14 are both 32-bit values.  The relation is provenance only: it does
    not encode visibility and must not be used to suppress lines.
    """

    groups: dict[int, int] = {}
    signature = b"\x13\x00\xAC\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        if start + 18 > len(data):
            continue
        graphic_ref = struct.unpack_from("<I", data, start + 10)[0]
        group_ref = struct.unpack_from("<I", data, start + 14)[0]
        if 1 <= graphic_ref <= 0xFFFF and 1 <= group_ref <= 0xFFFF:
            groups[graphic_ref] = group_ref
    return groups


def discover_template_stream(streams: dict[str, bytes]) -> tuple[str, bytes]:
    """Find the shared template Sheet from its record families, not its id."""

    candidates: list[tuple[tuple[int, int, int, int], str, bytes]] = []
    for name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", name) or len(data) <= 1024:
            continue
        circle_records = template_circle_primitives(data)
        circles = len(circle_records)
        polylines = len(template_polyline_paths(data))
        closed_triangles = len(template_closed_triangles(data, circle_records))
        # Counted polylines also occur in ordinary drawing content.  A shared
        # Chengda template is identified by the conjunction of a compass
        # circle, its closed three-edge arrowheads and the dense stroke-font
        # path set, not by whichever Sheet merely has the most polylines.
        template_signature = int(
            circles >= 1 and closed_triangles >= 4 and polylines >= 10
        )
        if not template_signature:
            continue
        score = (template_signature, closed_triangles, polylines, len(data))
        candidates.append((score, name, data))
    if not candidates:
        return "", b""
    _, name, data = max(candidates, key=lambda item: item[0])
    return name, data


def template_images(streams: dict[str, bytes]) -> list[dict[str, object]]:
    """Read BMP template image instances stored in SHA JSite streams."""

    images: list[dict[str, object]] = []
    _, template = discover_template_stream(streams)
    resources = sorted(
        (
            int(match.group(1)),
            stream_name,
        )
        for stream_name, payload in streams.items()
        if (match := re.fullmatch(r"JSite(\d+)/CONTENTS", stream_name))
        and payload.startswith(b"BM")
    )
    for resource_id, stream_name in resources:
        offset = template.find(struct.pack("<I", resource_id))
        bitmap = streams.get(stream_name)
        if offset < 72 or bitmap is None or not bitmap.startswith(b"BM"):
            continue
        height = struct.unpack_from("<d", template, offset - 72)[0]
        x = struct.unpack_from("<d", template, offset - 40)[0]
        y = struct.unpack_from("<d", template, offset - 32)[0]
        width = struct.unpack_from("<d", template, offset - 24)[0]
        if not all(0 < value < 1 for value in (x, y, width, height)):
            continue
        images.append(
            {
                "resource_id": resource_id,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "href": "data:image/bmp;base64," + base64.b64encode(bitmap).decode("ascii"),
            }
        )
    return images


def nearby_text_psm_bbox(
    data: bytes,
    psm: bytes,
    text_start: int,
    anchor_x: float,
    anchor_y: float,
) -> tuple[int, tuple[float, float, float, float]] | None:
    """Resolve a text object's sibling PSM reference from its local record header."""

    candidates: list[tuple[float, int, tuple[float, float, float, float]]] = []
    for offset in range(max(0, text_start - 40), max(0, text_start - 19), 2):
        graphic_ref = struct.unpack_from("<I", data, offset)[0]
        if not graphic_ref:
            continue
        for bbox in psm_bboxes(psm, graphic_ref):
            left, bottom, right, top = bbox
            width, height = right - left, top - bottom
            dx = max(left - anchor_x, 0.0, anchor_x - right)
            dy = max(bottom - anchor_y, 0.0, anchor_y - top)
            distance = math.hypot(dx, dy)
            if height <= 400 and width <= PAGE_WIDTH and distance <= 250:
                candidates.append(
                    (distance + 0.01 * (width + height), graphic_ref, bbox)
                )
    if not candidates:
        return None
    _, graphic_ref, bbox = min(candidates, key=lambda item: item[0])
    return graphic_ref, bbox


def style_format_metrics(style_data: bytes) -> dict[int, tuple[str, float]]:
    """Resolve Shape2D text-format ids to the declared font family and size.

    Template text records store a compact run-format id (for example ``0xDB``)
    rather than a font height beside every string.  StyleCluster keeps that id
    next to an ``Arial-Z``/``Courier New-Z`` record whose height is the double
    28 bytes before the family name.  Keeping this relation avoids treating a
    PSM glyph envelope as a CSS font size.
    """

    family_records: list[tuple[int, str, float]] = []
    for match in re.finditer(rb"(?:(?:[\x20-\x7e]\x00){3,})", style_data):
        text = match.group().decode("utf-16le")
        if text not in {
            "Arial-Z",
            "Arial,P",
            "Courier New-Z",
            "Courier New,\\",
            "SimHei-Z",
        }:
            continue
        if match.start() < 28:
            continue
        ratio = struct.unpack_from("<d", style_data, match.start() - 28)[0]
        if 0.001 <= ratio <= 0.02:
            family_records.append((match.start(), text, ratio))

    resolved: dict[int, tuple[str, float]] = {}
    for format_ref in range(0x20, 0x200):
        candidates: list[tuple[int, str, float]] = []
        needle = struct.pack("<I", format_ref)
        for occurrence in re.finditer(re.escape(needle), style_data):
            for family_offset, family, ratio in family_records:
                distance = abs(family_offset - occurrence.start())
                if distance <= 80:
                    candidates.append((distance, family, ratio))
        if candidates:
            _, family, ratio = min(candidates)
            resolved[format_ref] = (family, ratio)
    return resolved


def css_font_family(style_family: str | None, *, template_panel: bool = False) -> str:
    """Map a Shape2D font-family record to a deterministic SVG family list."""

    if style_family:
        # SmartSketch's Chengda ``Arial-Z`` face is a regular condensed plot
        # font.  Treating ``-Z`` as an ignorable suffix selected broad Arial,
        # making the project/client titles appear artificially bold.
        if style_family == "Arial-Z":
            return "'Arial Narrow', 'Arial Narrow MT', Arial, sans-serif"
        family = style_family.removesuffix("-Z").removesuffix(",P").removesuffix(",\\")
        if family == "Courier New":
            return "'Courier New', Courier, monospace"
        if family == "Arial":
            return "Arial, Helvetica, sans-serif"
        if family == "Arial Narrow":
            return "'Arial Narrow', 'Arial Narrow MT', Arial, sans-serif"
        if family == "SimHei":
            return "SimHei, STHeiti, 'PingFang SC', 'Noto Sans CJK SC', sans-serif"
    return "Arial, Helvetica, sans-serif" if template_panel else "'Courier New', Courier, monospace"


def is_chengda_feature_strip_value(
    style_ref: int,
    format_ref: int,
    normalized_x: float,
    normalized_y: float,
) -> bool:
    """Identify physical-Sheet values in Chengda's lower characteristic strip.

    Across the complete 000200FW unit, all 1,950 records using this structural
    pair occur in Sheet6's bottom strip.  Retaining the position guard prevents
    a coincidentally reused id outside the title block from being restyled.
    """

    return (
        style_ref == CHENGDA_FEATURE_VALUE_STYLE_REF
        and format_ref == CHENGDA_FEATURE_VALUE_FORMAT_REF
        and 0.0 <= normalized_x <= 0.30
        and 0.0 <= normalized_y <= 0.04
    )


def is_chengda_feature_strip_template_text(
    format_ref: int,
    normalized_x: float,
    normalized_y: float,
) -> bool:
    """Identify fixed labels in the lower characteristic-data strip."""

    return (
        format_ref in {0x138, 0x13A}
        and 0.0 <= normalized_x <= 0.30
        and 0.0 <= normalized_y <= 0.04
    )


def is_chengda_review_panel_text(
    format_ref: int,
    normalized_x: float,
    normalized_y: float,
) -> bool:
    """Identify the Document Review Result panel without matching revision headers."""

    return (
        format_ref == CHENGDA_REVIEW_PANEL_FORMAT_REF
        and 0.30 <= normalized_x <= 0.38
        and 0.035 <= normalized_y <= 0.085
    )


def is_chengda_revision_header(
    format_ref: int,
    normalized_x: float,
    normalized_y: float,
) -> bool:
    """Identify the seven-column revision-table heading row."""

    return (
        format_ref == CHENGDA_REVIEW_PANEL_FORMAT_REF
        and 0.03 <= normalized_x <= 0.30
        and 0.050 <= normalized_y <= 0.057
    )


def is_chengda_approval_label(
    format_ref: int,
    normalized_x: float,
    normalized_y: float,
) -> bool:
    """Identify Approved-by/Date rows above the weld legend structurally."""

    return (
        format_ref == CHENGDA_REVIEW_PANEL_FORMAT_REF
        and 0.30 <= normalized_x <= 0.33
        and 0.038 <= normalized_y < 0.050
    )


def is_chengda_legend_title(
    format_ref: int,
    normalized_x: float,
    normalized_y: float,
) -> bool:
    """Identify the centred title of the lower-right weld legend."""

    return (
        format_ref == 0x138
        and 0.30 <= normalized_x <= 0.35
        and 0.030 <= normalized_y <= 0.040
    )


def is_chengda_project_doc_label(
    format_ref: int,
    normalized_x: float,
    normalized_y: float,
) -> bool:
    """Identify the Project Doc Number label above the drawing-number cells."""

    return (
        format_ref == 0x145
        and 0.40 <= normalized_x <= 0.45
        and 0.035 <= normalized_y <= 0.050
    )


def template_css_font_family(format_ref: int, style_family: str | None) -> str:
    """Resolve Chengda template faces using SHA format semantics.

    ``Arial-Z`` is a SmartSketch style label, not a declaration that every
    format uses Arial Narrow.  The reference PDF's embedded outlines prove
    that the title, feature strip and grid formats use regular/italic Arial,
    while the three-line confidentiality notice uses Calibri.  Revision-table
    formats that genuinely use Arial Narrow continue through the generic map.
    """

    if format_ref == 0x14C:
        return "Calibri, Arial, sans-serif"
    if format_ref in {0x21, 0x138, 0x140, 0x149, 0x156}:
        return "Arial, Helvetica, sans-serif"
    return css_font_family(style_family, template_panel=True)


def template_text_render_x(
    anchor_x: float,
    bbox: tuple[int, int, int, int],
    text_anchor: str,
    format_ref: int,
) -> tuple[float, str]:
    """Choose a template label's x coordinate from its explicit SHA evidence.

    A normal Shape2D insertion point lies inside its PSM glyph envelope (or is
    its aligned start/centre). A few fixed labels instead expose a sibling
    transform whose x lies completely outside the glyph envelope. In that
    record family the PSM left edge is the only object-local source position.
    """

    left, _, right, _ = bbox
    # Format 0xCD is the observed footer-label run whose PSM is in the
    # physical page coordinate system while its transform is a sibling/end
    # anchor. Other template runs use their transform x; their PSM x values
    # are local/sibling extents and caused UNIT/AREA/SHEET to drift left.
    if format_ref == 0xCD and text_anchor == "start" and not left <= anchor_x <= right:
        return float(left), "sha-psm-left-plus-style-size-and-psm-width"
    return anchor_x, "sha-text-anchor-plus-style-size-and-psm-width"


def template_text_alignment(
    format_ref: int,
    anchor_x: float | None = None,
    anchor_y: float | None = None,
    template_segments: list[tuple[float, float, float, float, int, int]] | None = None,
) -> str:
    """Return horizontal alignment from the template grid and run format."""

    if anchor_x is not None and anchor_y is not None and template_segments:
        vertical_boundaries = sorted(
            {
                x1 * SHEET_UNIT
                for x1, y1, x2, y2, _, _ in template_segments
                if abs(x1 - x2) <= 1e-8
                and min(y1, y2) * SHEET_UNIT - 2 <= anchor_y
                <= max(y1, y2) * SHEET_UNIT + 2
            }
        )
        left = max((x for x in vertical_boundaries if x < anchor_x), default=None)
        right = min((x for x in vertical_boundaries if x > anchor_x), default=None)
        if left is not None and right is not None and right - left >= 80:
            cell_width = right - left
            if abs(anchor_x - (left + right) / 2) <= cell_width * 0.08:
                return "middle"

    # These run formats are reused across several independent labels whose
    # Shape2D insertion x is the cell centre. The relation is format-level,
    # not a special case for any label string.
    return "middle" if format_ref in {0xD2, 0xDD, 0xEA} else "start"


def template_text_after_edge(
    anchor_x: float,
    anchor_y: float,
    fallback_y: float,
    template_segments: list[tuple[float, float, float, float, int, int]] | None,
) -> tuple[float, str]:
    """Use the lower border of the containing template cell as after-edge."""

    if template_segments:
        horizontal_boundaries = sorted(
            {
                y1 * SHEET_UNIT
                for x1, y1, x2, y2, _, _ in template_segments
                if abs(y1 - y2) <= 1e-8
                and min(x1, x2) * SHEET_UNIT - 2 <= anchor_x
                <= max(x1, x2) * SHEET_UNIT + 2
            }
        )
        lower = max((y for y in horizontal_boundaries if y < anchor_y), default=None)
        upper = min((y for y in horizontal_boundaries if y > anchor_y), default=None)
        if lower is not None and upper is not None and upper - lower >= 80:
            return lower, "sha-cell-lower-border-after-edge"
    return fallback_y, "sha-psm-bottom-baseline"


def revision_text_baseline(
    anchor_y: float,
    bbox: tuple[int, int, int, int] | None,
) -> tuple[float, float | None, str]:
    """Use a bound revision field's local PSM top when it is a close peer."""

    if bbox is not None:
        _, bottom, _, top = bbox
        height = top - bottom
        if abs(top - anchor_y) <= max(18.0, height * 0.25):
            # PSM height is a line envelope, not the CSS/ISOGEN font size.
            # Retain the bound-field font ratio and use only its top as the
            # source baseline.
            return float(top), None, "sha-bound-graphic-psm-top"
    return anchor_y, None, "sha-bound-expression-anchor"


def psm_text_extent_is_local(
    text: str,
    bbox: tuple[int, int, int, int],
    declared_font_height: float,
    *,
    rotated: bool,
) -> bool:
    """Reject a page/container PSM box using the run's declared font size."""

    if rotated:
        return True
    left, bottom, right, top = bbox
    width, height = right - left, top - bottom
    return (
        height <= max(320.0, declared_font_height * 4.0)
        and width <= max(1200.0, len(text) * declared_font_height * 2.5)
    )


def select_text_psm_bbox(
    psm: bytes,
    graphic_ref: int,
    text: str,
    anchor_x: float,
    anchor_y: float,
    declared_font_height: float | None,
    *,
    rotated: bool,
) -> tuple[int, int, int, int] | None:
    """Choose the object-local PSM envelope when a reference is repeated.

    Some body-text references collide with earlier page/container entries in
    ``PSMcluster0``.  The text anchor and the short-token size bound identify
    the later glyph envelope without relying on a material-number value.
    """

    boxes = psm_bboxes(psm, graphic_ref)
    if not boxes:
        return None

    candidates: list[tuple[int, int, int, int]] = []
    for box in boxes:
        left, bottom, right, top = box
        width, height = right - left, top - bottom
        if len(text) <= 10 and (
            height > 800
            or width
            > max(
                1200.0,
                len(text) * (declared_font_height or max(42.0, height)) * 4.0,
            )
        ):
            continue
        if (
            declared_font_height is not None
            and not psm_text_extent_is_local(
                text,
                box,
                declared_font_height,
                rotated=rotated,
            )
        ):
            continue
        candidates.append(box)
    if not candidates:
        return boxes[0]

    def score(box: tuple[int, int, int, int]) -> float:
        left, bottom, right, top = box
        dx = max(left - anchor_x, 0.0, anchor_x - right)
        dy = max(bottom - anchor_y, 0.0, anchor_y - top)
        # Prefer a nearby local glyph envelope over a containing page object.
        return math.hypot(dx, dy) + 0.02 * ((right - left) + (top - bottom))

    return min(candidates, key=score)


def template_text_records(
    data: bytes, psm: bytes, style_data: bytes = b""
) -> list[dict[str, object]]:
    """Select fixed Sheet221 labels with trustworthy PSM extents."""

    records: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    metrics = style_format_metrics(style_data)
    template_circles = template_circle_primitives(data)
    # Format 0xA1 inherits the north-arrow annotation face through a
    # StyleCluster parent instead of declaring a directly adjacent family
    # record.  Its 3.5e-3 plotted height is the inherited template format,
    # shared by all six compass labels.
    inherited_metrics = {0xA1: ("Arial", 0.0035)}
    for record in text_records(data):
        text = str(record["text"]).strip()
        ref = int(record["graphic_ref"])
        style_ref = int(record["style_ref"])
        format_ref = int(record.get("format_ref", 0))
        normalized_x, normalized_y = float(record["x"]), float(record["y"])
        direction_magnitude = math.hypot(
            float(record["direction_x"]), float(record["direction_y"])
        )
        format_metric = metrics.get(format_ref) or inherited_metrics.get(format_ref)
        if (
            not text
            or len(text) > 500
            or "<?xml" in text
            or not all(character.isprintable() for character in text)
            or format_metric is None
            or not (0 <= normalized_x <= 1 and 0 <= normalized_y <= PAGE_HEIGHT / SHEET_UNIT)
            or not (
                0.0005 <= direction_magnitude <= 0.02
                or 0.8 <= direction_magnitude <= 1.2
            )
        ):
            continue
        # Some template refs occur repeatedly in PSMcluster0. The first match
        # can be a page/container envelope, while a later record is the small
        # glyph extent. Use the direct Sheet text anchor to choose only among
        # plausible title-block glyph boxes.
        x, y = normalized_x * SHEET_UNIT, normalized_y * SHEET_UNIT
        resolved_psm = nearby_text_psm_bbox(
            data, psm, int(record["text_start"]), x, y
        )
        if resolved_psm is None:
            # The north-arrow direction labels deliberately have no local
            # PSM text envelope. Their shared compound-style marker and
            # inherited run format provide a strict structural fallback;
            # random binary strings do not satisfy this relation.
            if not (
                format_ref in inherited_metrics
                and 1 <= len(text) <= 4
                and text.isalpha()
                and any(
                    math.hypot(
                        x - float(circle["center_x"]),
                        y - float(circle["center_y"]),
                    )
                    <= float(circle["radius"]) * 1.6
                    for circle in template_circles
                )
            ):
                continue
            family, ratio = format_metric
            font_size = ratio * SHEET_UNIT
            width = max(font_size * 0.55, len(text) * font_size * 0.62)
            records.append(
                {
                    "text": text,
                    "graphic_ref": ref,
                    "bbox": (x, y - font_size, x + width, y),
                    "x": normalized_x,
                    "y": normalized_y,
                    "style_ref": style_ref,
                    "format_ref": format_ref,
                    "psm_is_cell": False,
                    "font_family": family,
                    "font_size": font_size,
                    "without_psm": True,
                    "mapping": "sha-text-anchor-plus-inherited-template-format",
                }
            )
            continue
        psm_ref, bbox = resolved_psm
        key = (psm_ref, text)
        if key not in seen:
            seen.add(key)
            records.append(
                {
                    "text": text,
                    "graphic_ref": psm_ref,
                    "bbox": bbox,
                    "x": normalized_x,
                    "y": normalized_y,
                    "style_ref": style_ref,
                    "format_ref": format_ref,
                    "psm_is_cell": (
                        psm_ref != ref
                        or style_ref > 0xFFFF
                        or direction_magnitude >= 0.8
                    ),
                }
            )
    for record in records:
        format_metric = metrics.get(int(record["format_ref"])) or inherited_metrics.get(
            int(record["format_ref"])
        )
        if format_metric is not None:
            record["font_family"], ratio = format_metric
            record["font_size"] = ratio * SHEET_UNIT
        if record.get("psm_is_cell") and int(record["format_ref"]) in {
            0x21,
            0x140,
            0x145,
            0x149,
            0x14F,
        }:
            # These PSM extents describe the containing title-block cell, not
            # a glyph envelope.  Chengda's template uses roughly 90% of the
            # cell height for the nominal text height; the large owner title
            # is capped by the adjacent reference drawing at 88 sheet units.
            left, bottom, right, top = record["bbox"]
            cell_fill = 11.0 / 12.0 if int(record["format_ref"]) == 0x21 else 0.90
            record["font_size"] = min(88.0, max(18.0, (top - bottom) * cell_fill))
        normalized_x = float(record["x"])
        normalized_y = float(record["y"])
        format_ref = int(record["format_ref"])
        if format_ref == CHENGDA_PROJECT_TITLE_FORMAT_REF:
            record["font_size"] = float(record["font_size"]) * CHENGDA_PROJECT_TITLE_FONT_SCALE
        elif format_ref == CHENGDA_CLIENT_TITLE_FORMAT_REF:
            record["font_size"] = float(record["font_size"]) * CHENGDA_CLIENT_TITLE_FONT_SCALE
        elif is_chengda_review_panel_text(format_ref, normalized_x, normalized_y):
            record["font_size"] = float(record["font_size"]) * CHENGDA_REVIEW_PANEL_FONT_SCALE
        if int(record["format_ref"]) in {0x21, 0x149}:
            record["font_style"] = "italic"
    return records


def template_grid_zone_labels(
    records: list[dict[str, object]],
    segments: list[tuple[float, float, float, float, int, int]],
) -> list[dict[str, object]]:
    """Place all four frame-zone label runs at their template-cell centres."""

    result: list[dict[str, object]] = []
    for record in records:
        text = str(record["text"]).strip()
        is_number = text in set("123456")
        is_letter = text in set("ABCDEFGHI")
        if not (is_number or is_letter) or int(record.get("format_ref", 0)) != 0x156:
            continue
        anchor_x = float(record["x"]) * SHEET_UNIT
        anchor_y = float(record["y"]) * SHEET_UNIT
        if is_number:
            side = "left" if anchor_x < SHEET_UNIT / 2 else "right"
        elif anchor_y < PAGE_HEIGHT / 2:
            side = "bottom"
        else:
            side = "top"
        verticals = sorted(
            {
                x1 * SHEET_UNIT
                for x1, y1, x2, y2, _, _ in segments
                if abs(x1 - x2) <= 1e-8
                and min(y1, y2) * SHEET_UNIT <= anchor_y <= max(y1, y2) * SHEET_UNIT
            }
        )
        horizontals = sorted(
            {
                y1 * SHEET_UNIT
                for x1, y1, x2, y2, _, _ in segments
                if abs(y1 - y2) <= 1e-8
                and min(x1, x2) * SHEET_UNIT <= anchor_x <= max(x1, x2) * SHEET_UNIT
            }
        )
        left = max((value for value in verticals if value < anchor_x), default=None)
        right = min((value for value in verticals if value > anchor_x), default=None)
        bottom = max((value for value in horizontals if value < anchor_y), default=None)
        top = min((value for value in horizontals if value > anchor_y), default=None)
        if None in {left, right, bottom, top}:
            continue
        result.append(
            {
                **record,
                "side": side,
                "render_x": (float(left) + float(right)) / 2,
                "render_y": (float(bottom) + float(top)) / 2,
                "render_font_size": float(record["font_size"]),
            }
        )
    side_order = {"left": 0, "right": 1, "bottom": 2, "top": 3}
    return sorted(result, key=lambda item: (side_order[str(item["side"])], str(item["text"])))


def template_multiline_text_records(
    data: bytes, psm: bytes, style_data: bytes = b""
) -> list[dict[str, object]]:
    """Decode multiline payloads inside their owning ``0x4D`` text record."""

    metrics = style_format_metrics(style_data)
    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x4d\x00"), data):
        record_start = match.start()
        if record_start + 38 > len(data):
            continue
        record_size = struct.unpack_from("<I", data, record_start + 2)[0]
        record_end = record_start + 6 + record_size
        if not 32 <= record_size <= 100_000 or record_end > len(data):
            continue
        format_ref = struct.unpack_from("<H", data, record_start + 20)[0]
        if format_ref not in metrics:
            continue

        payload: tuple[int, int, str] | None = None
        # Shape2D inserts zero, one or two property pairs before the UTF-16
        # count.  All observed 0x4D multiline variants therefore place the
        # count at record offsets 32 or 36.  In either case the payload and
        # its transform must remain inside this record's declared boundary.
        for count_offset in (record_start + 32, record_start + 36):
            if count_offset + 2 > record_end:
                continue
            length = struct.unpack_from("<H", data, count_offset)[0]
            text_start = count_offset + 2
            text_end = text_start + length * 2
            if not 5 <= length <= 1000 or text_end > record_end:
                continue
            try:
                text = data[text_start:text_end].decode("utf-16le")
            except UnicodeDecodeError:
                continue
            if "\r" not in text and "\n" not in text:
                continue
            if text.lstrip().startswith("<?xml") or not all(
                character.isprintable() or character in "\r\n\t"
                for character in text
            ):
                continue
            payload = (text_start, text_end, text)
            break
        if payload is None:
            continue
        text_start, text_end, text = payload

        transform = None
        for transform_offset in range(text_end, record_end - 31, 2):
            values = struct.unpack_from("<4d", data, transform_offset)
            if not all(math.isfinite(value) for value in values):
                continue
            x, y, direction_x, direction_y = values
            if not (1e-6 <= x <= 1 and 1e-6 <= y <= PAGE_HEIGHT / SHEET_UNIT):
                continue
            magnitude = math.hypot(direction_x, direction_y)
            if 0.0005 <= magnitude <= 0.02 or 0.8 <= magnitude <= 1.2:
                transform = values
                break
        if transform is None:
            continue
        x, y, direction_x, direction_y = transform
        anchor_x, anchor_y = x * SHEET_UNIT, y * SHEET_UNIT
        resolved_psm = nearby_text_psm_bbox(data, psm, text_start, anchor_x, anchor_y)
        if resolved_psm is None:
            continue
        graphic_ref, bbox = resolved_psm
        family, ratio = metrics[format_ref]
        lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        left, bottom, right, top = bbox
        alignment = (
            "middle"
            if abs(anchor_x - (left + right) / 2) <= (right - left) * 0.10
            else "start"
        )
        per_line_height = (top - bottom) / max(1, len(lines))
        records.append(
            {
                "text": "\n".join(lines),
                "x": x,
                "y": y,
                "direction_x": direction_x,
                "direction_y": direction_y,
                "graphic_ref": graphic_ref,
                "bbox": bbox,
                "format_ref": format_ref,
                "font_family": family,
                "font_size": max(ratio * SHEET_UNIT, per_line_height * 0.81),
                "alignment": alignment,
            }
        )
    return records


def _resolve_tagged_value(
    streams: dict[str, bytes], stream_name: str, selector: str
) -> tuple[str, int | None, str] | None:
    payload = streams.get(f"TaggedTxtData/{stream_name}")
    if payload is None:
        return None
    root = None
    # SmartPlant/SmartSketch writes these XML streams without an encoding
    # declaration.  Most pages are ASCII/UTF-8, but a single Chinese value
    # makes the complete stream GB18030 (for example RevisionNumbers contains
    # ``云``).  Rejecting that one byte sequence used to discard every bound
    # TitleArea field, including otherwise plain-ASCII drawing numbers and
    # titles.  Decode strictly in the two encodings actually emitted; never
    # use replacement characters because they could silently corrupt a field.
    for encoding in ("utf-8", "gb18030"):
        try:
            root = ET.fromstring(payload.decode(encoding))
            break
        except (UnicodeDecodeError, ET.ParseError):
            continue
    if root is None:
        return None

    field = selector.rsplit("/", 1)[-1]
    if stream_name != "Revision":
        node = root.find(field)
        value = "" if node is None else (node.text or "").strip()
        return value, None, field

    revision_records = root.findall("RevisionRecord")
    if not revision_records:
        return None
    record_index: int | None = None
    direct = re.search(r"RevisionRecord\[(\d+)\]", selector)
    if direct:
        record_index = int(direct.group(1)) - 1
    else:
        last_offset = re.search(r"position\(\)=\(last\(\)-(\d+)\)", selector)
        early_position = re.search(r"last\(\)\s*<=\s*5.*?position\(\)=(\d+)", selector)
        if len(revision_records) > 5 and last_offset:
            record_index = len(revision_records) - 1 - int(last_offset.group(1))
        elif early_position:
            record_index = int(early_position.group(1)) - 1
    if record_index is None or not 0 <= record_index < len(revision_records):
        return None
    node = revision_records[record_index].find(field)
    return ("" if node is None else (node.text or "").strip()), record_index, field


def template_bound_text(
    data: bytes,
    streams: dict[str, bytes],
    psm: bytes = b"",
) -> list[dict[str, object]]:
    """Resolve fixed-template bindings against the SHA TaggedTxtData streams."""

    records: list[dict[str, object]] = []
    metrics = style_format_metrics(streams.get("StyleCluster", b""))
    for record in text_records(data):
        expression = str(record["text"])
        if not expression.startswith("<?xml"):
            continue
        try:
            binding_xml = ET.fromstring(expression)
        except ET.ParseError:
            continue
        binding = binding_xml.find(".//intstgxml")
        if binding is None:
            continue
        stream_name = binding.attrib.get("stream", "")
        selector = binding.attrib.get("select", "")
        resolved = _resolve_tagged_value(streams, stream_name, selector)
        if resolved is None:
            continue
        text, record_index, field = resolved
        if not text:
            continue
        x, y = float(record["x"]), float(record["y"])
        anchor_x, anchor_y = x * SHEET_UNIT, y * SHEET_UNIT
        resolved_psm = nearby_text_psm_bbox(
            data, psm, int(record["text_start"]), anchor_x, anchor_y
        )
        if resolved_psm is None:
            continue
        graphic_ref, bbox = resolved_psm
        left, bottom, right, top = bbox
        format_ref = int(record.get("format_ref", 0))
        format_metric = metrics.get(format_ref)
        # The bound TitleArea and Revision records use Chengda's narrow title
        # font and occupy about 7/8 of the local PSM cell.  Large cells are
        # capped at the common 12.26 pt title/revision hierarchy observed in
        # the corresponding project PDF.
        if stream_name == "TitleArea" and field == "Sheet":
            font_size = 0.003606 * SHEET_UNIT
        else:
            font_size = min(
                0.004325 * SHEET_UNIT,
                max(18.0, (top - bottom) * 0.875),
            )
        records.append(
            {
                "text": text,
                "x": (left + right) / 2,
                "y": (bottom + top) / 2,
                "field": field,
                "stream": stream_name,
                "record_index": record_index,
                "graphic_ref": graphic_ref,
                "psm_bbox": bbox,
                "font_size": font_size,
                "format_ref": format_ref,
                "font_family": "Arial Narrow",
            }
        )
    # Some template revisions contain two bindings to the same TitleArea
    # field.  Keep the binding with the wider source cell; the narrower one is
    # an obsolete overlapping cell, not a second printable value.
    deduplicated: dict[tuple[str, str, int | None, str], dict[str, object]] = {}
    for record in records:
        key = (
            str(record["stream"]),
            str(record["field"]),
            record.get("record_index"),
            str(record["text"]),
        )
        previous = deduplicated.get(key)
        if previous is None:
            deduplicated[key] = record
            continue
        old_box = previous["psm_bbox"]
        new_box = record["psm_bbox"]
        if new_box[2] - new_box[0] > old_box[2] - old_box[0]:
            deduplicated[key] = record
    completed = list(deduplicated.values())

    # A revision value can be present in TaggedTxtData even when this edition
    # of the title-block template omitted one of the repeated binding objects.
    # Reconstruct only such missing, non-empty values from the same column in
    # another visible revision row and the target row's own PSM bounds.  This
    # keeps the data source in SHA while avoiding revision-specific literals.
    revision_payload = streams.get("TaggedTxtData/Revision")
    if revision_payload:
        revision_root = None
        for encoding in ("utf-8", "gb18030"):
            try:
                revision_root = ET.fromstring(revision_payload.decode(encoding))
                break
            except (UnicodeDecodeError, ET.ParseError):
                continue
        if revision_root is not None:
            xml_rows = revision_root.findall("RevisionRecord")
            revision_records = [
                record for record in completed if record.get("stream") == "Revision"
            ]
            visible_indices = sorted(
                {
                    int(record["record_index"])
                    for record in revision_records
                    if record.get("record_index") is not None
                }
            )
            existing = {
                (int(record["record_index"]), str(record["field"]))
                for record in revision_records
                if record.get("record_index") is not None
            }
            sources_by_field: dict[str, list[dict[str, object]]] = defaultdict(list)
            for record in revision_records:
                sources_by_field[str(record["field"])].append(record)

            for record_index in visible_indices:
                if not 0 <= record_index < len(xml_rows):
                    continue
                target_row = [
                    record
                    for record in revision_records
                    if int(record.get("record_index", -1)) == record_index
                ]
                if not target_row:
                    continue
                row_bottom = median(float(record["psm_bbox"][1]) for record in target_row)
                row_top = median(float(record["psm_bbox"][3]) for record in target_row)
                for node in xml_rows[record_index]:
                    field = node.tag
                    text = (node.text or "").strip()
                    if not text or (record_index, field) in existing:
                        continue
                    candidates = sources_by_field.get(field, [])
                    if not candidates:
                        continue
                    source = min(
                        candidates,
                        key=lambda item: abs(int(item.get("record_index", 0)) - record_index),
                    )
                    left, _, right, _ = source["psm_bbox"]
                    bbox = (float(left), row_bottom, float(right), row_top)
                    synthesized = {
                        **source,
                        "text": text,
                        "record_index": record_index,
                        "psm_bbox": bbox,
                        "x": (float(left) + float(right)) / 2,
                        "y": (row_bottom + row_top) / 2,
                        "mapping": "sha-revision-value-plus-peer-column-and-target-row",
                    }
                    completed.append(synthesized)
                    revision_records.append(synthesized)
                    existing.add((record_index, field))
    return completed


def revision_signature_records(
    revision_records: list[dict[str, object]],
    template_segments: list[tuple[float, float, float, float, int, int]] | None = None,
    signature_dir: Path | None = None,
) -> list[dict[str, object]]:
    """Resolve latest-row signatures from SHA revision personnel fields.

    SHA stores the responsible-person identifiers and revision cells, while
    the original plotter adds signatures from external user-profile images.
    Project assets are therefore selected by the latest SHA Revision record,
    not inferred from an unrelated embedded logo resource.
    """

    fields = ("RevisedBy", "CheckedBy", "ApprovedBy")
    relevant = [record for record in revision_records if record.get("field") in fields]
    if not relevant:
        return []
    latest_index = max(int(record.get("record_index", -1)) for record in relevant)
    dimensions = {
        "XZS": (242.0, 118.0),
        "PJ": (134.0, 118.0),
        "ML": (175.0, 118.0),
        # 100200 reference PDF page 66 image rectangles converted into this
        # SHA page-unit scale. The former values enlarged them about 1.6x.
        "ZBT": (299.0, 118.0),
        "LHJ": (138.0, 81.0),
        "LKF": (226.0, 73.0),
    }
    assets = (
        Path(signature_dir).resolve()
        if signature_dir is not None
        else ASSET_DIR / "signatures"
    )
    profile: dict[str, dict[str, object]] = {}
    signature_scale = 1.0
    multi_signature_gap: float | None = None
    profile_path = assets / "profile.json"
    if profile_path.is_file():
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        signature_scale = float(payload.get("signature_scale", 1.0))
        if signature_scale <= 0:
            raise ValueError(f"签名缩放比例必须大于0：{profile_path}")
        if "multi_signature_gap" in payload:
            multi_signature_gap = float(payload["multi_signature_gap"])
            if multi_signature_gap < 0:
                raise ValueError(f"双签名间距不能小于0：{profile_path}")
        profile = {
            str(key): dict(value)
            for key, value in dict(payload.get("people", {})).items()
            if isinstance(value, dict)
        }
    by_field = {
        str(record["field"]): record
        for record in relevant
        if int(record.get("record_index", -1)) == latest_index
    }
    result: list[dict[str, object]] = []
    for field in fields:
        record = by_field.get(field)
        if record is None:
            continue
        person = str(record["text"]).strip()
        person_keys = [item.strip() for item in re.split(r"[\\/]", person) if item.strip()]
        signature_assets: list[dict[str, object]] = []
        for person_key in person_keys:
            settings = profile.get(person_key, {})
            manual_asset = assets / f"{person_key}-manual.png"
            configured_name = str(settings.get("file", f"{person_key}.png"))
            configured_asset = assets / configured_name
            asset = manual_asset if manual_asset.is_file() else configured_asset
            configured_dimensions = (
                float(settings["width"]),
                float(settings["height"]),
            ) if "width" in settings and "height" in settings else None
            person_dimensions = configured_dimensions or dimensions.get(person_key)
            if person_dimensions is None or not asset.is_file():
                continue
            width, height = (
                float(person_dimensions[0]) * signature_scale,
                float(person_dimensions[1]) * signature_scale,
            )
            layout_width = float(settings.get("layout_width", person_dimensions[0])) * signature_scale
            signature_assets.append(
                {
                    "person_key": person_key,
                    "asset": asset,
                    "width": width,
                    "height": height,
                    # A manually re-cropped signature can need a corrected
                    # optical size without moving its neighbour in a shared
                    # revision cell.  ``layout_width`` preserves the original
                    # allocation while the image remains centred in it.
                    "layout_width": layout_width,
                    "slot_width": float(settings.get("slot_width", person_dimensions[0])),
                    "offset_x": float(settings.get("offset_x", 0.0)),
                    "offset_y": float(settings.get("offset_y", 0.0)),
                    "flip_180": bool(settings.get("flip_180", asset != manual_asset)),
                }
            )
        if not signature_assets:
            continue
        cell_left, cell_bottom, cell_right, cell_top = record["psm_bbox"]
        signature_row_bottom = float(cell_top)
        signature_row_top: float | None = None
        if template_segments:
            cell_centre_x = (float(cell_left) + float(cell_right)) / 2
            boundaries = sorted(
                {
                    y1 * SHEET_UNIT
                    for x1, y1, x2, y2, _, _ in template_segments
                    if abs(y1 - y2) <= 1e-8
                    and min(x1, x2) * SHEET_UNIT - 2 <= cell_centre_x
                    <= max(x1, x2) * SHEET_UNIT + 2
                }
            )
            lower = min(
                (value for value in boundaries if value >= float(cell_top) - 3.0),
                default=None,
            )
            if lower is not None:
                upper = min(
                    (value for value in boundaries if value > lower + 20.0),
                    default=None,
                )
                if upper is not None:
                    signature_row_bottom = lower
                    signature_row_top = upper
        if signature_row_top is None:
            # Fall back to the median revision-row pitch.  The signatures are
            # placed in the dedicated row immediately above the latest
            # revision, never in the revision-0 personnel cells themselves.
            row_centres = sorted(
                {
                    (float(item["psm_bbox"][1]) + float(item["psm_bbox"][3])) / 2
                    for item in relevant
                    if str(item.get("field")) == field
                }
            )
            pitches = [
                right - left
                for left, right in zip(row_centres, row_centres[1:])
                if right - left > 20.0
            ]
            pitch = median(pitches) if pitches else float(cell_top) - float(cell_bottom)
            signature_row_top = signature_row_bottom + pitch
        row_centre = (signature_row_bottom + signature_row_top) / 2
        compact_multi_signature = (
            len(signature_assets) > 1 and multi_signature_gap is not None
        )
        if compact_multi_signature:
            total_slot_width = (
                sum(float(item["layout_width"]) for item in signature_assets)
                + multi_signature_gap * (len(signature_assets) - 1)
            )
        else:
            total_slot_width = sum(float(item["slot_width"]) for item in signature_assets)
        cursor_x = (float(cell_left) + float(cell_right) - total_slot_width) / 2
        for item in signature_assets:
            width = float(item["width"])
            height = float(item["height"])
            slot_width = (
                float(item["layout_width"])
                if compact_multi_signature
                else float(item["slot_width"])
            )
            asset = Path(item["asset"])
            result.append(
                {
                    **record,
                    "person": person,
                    "person_key": str(item["person_key"]),
                    "width": width,
                    "height": height,
                    "x": cursor_x + (slot_width - width) / 2 + float(item["offset_x"]),
                    "y": row_centre - height / 2 + float(item["offset_y"]),
                    "signature_row": (signature_row_bottom, signature_row_top),
                    "flip_180": bool(item["flip_180"]),
                    "href": "data:image/png;base64," + base64.b64encode(asset.read_bytes()).decode("ascii"),
                }
            )
            cursor_x += slot_width
            if compact_multi_signature:
                cursor_x += multi_signature_gap
    return result


def template_notes(data: bytes, style_data: bytes = b"") -> list[dict[str, object]]:
    """Read the fixed note lines with their Sheet221 anchors."""

    notes: list[dict[str, object]] = []
    for record in text_records(data):
        text = str(record["text"]).strip()
        if not text.startswith(("1. FOR HOLD", "P1.", "2. PIPE ROUTING", "3. FOR STRESS", "d3. FOR STRESS")):
            continue
        x, y = float(record["x"]), float(record["y"])
        if 0 <= x <= 1 and 0 <= y <= 1:
            notes.append(
                {
                    "text": text.removeprefix("d3.").replace("P1.", "1."),
                    "x": x,
                    "y": y,
                    "format_ref": int(record.get("format_ref", 0)),
                }
            )
    metrics = style_format_metrics(style_data)
    for record in notes:
        format_metric = metrics.get(int(record["format_ref"]))
        if format_metric is not None:
            record["font_family"], ratio = format_metric
            record["font_size"] = ratio * SHEET_UNIT
    return notes


def template_anchor_labels(
    data: bytes, psm: bytes, style_data: bytes = b""
) -> list[dict[str, object]]:
    """Return fixed template labels and their known Shape2D layout data."""

    labels: list[dict[str, object]] = []
    for record in text_records(data):
        text = str(record["text"]).strip()
        x, y = float(record["x"]), float(record["y"])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        if text == "PIPING  ISOMETRIC":
            # The text record carries object 0x1252, while its actual PSM
            # envelope is sibling graphic 0x0E77. Both are direct Sheet221
            # records: the anchor is the insertion point and PSM supplies the
            # rendered glyph metrics.
            labels.append(
                {
                    "text": text,
                    "x": x,
                    "y": y,
                    "font_family": "Arial, sans-serif",
                    "anchor": "middle",
                    "psm_bbox": psm_bbox(psm, 0x0E77),
                    "psm_graphic_ref": 0x0E77,
                    "format_ref": int(record.get("format_ref", 0)),
                    "baseline_from_psm": True,
                }
            )
        elif text == "BANC PANDA3 PROJECT":
            # This delayed-transform record stores its PSM sibling 28 bytes
            # before the decoded text start. The sibling supplies the title's
            # actual width while StyleCluster supplies its font size.
            psm_ref = (
                struct.unpack_from("<I", data, int(record["text_start"]) - 28)[0]
                if int(record["text_start"]) >= 28
                else 0
            )
            labels.append(
                {
                    "text": text,
                    "x": x,
                    "y": y,
                    "font_size": 0.007 * SHEET_UNIT,
                    "font_family": "Arial, sans-serif",
                    "anchor": "start",
                    "psm_bbox": psm_bbox(psm, psm_ref) if psm_ref else None,
                    "psm_graphic_ref": psm_ref,
                    "format_ref": int(record.get("format_ref", 0)),
                    "physical_page_baseline": True,
                }
            )
        elif text.lstrip("+") in {
            "T.EN Chemical Engineering (Tianjin) Co,.LTD",
            "Bluestar Adisseo Nanjing Co,.LTD",
        }:
            clean_text = text.lstrip("+")
            # These company-name records share their visible metrics with
            # nearby PSM siblings, just like the large title. Their local text
            # records retain the true baseline insertion point.
            psm_ref = {
                "T.EN Chemical Engineering (Tianjin) Co,.LTD": 0x0BB0,
                "Bluestar Adisseo Nanjing Co,.LTD": 0x057F,
            }[clean_text]
            labels.append(
                {
                    "text": clean_text,
                    "x": x,
                    "y": y,
                    "font_family": "Arial, sans-serif",
                    "anchor": "start",
                    "psm_bbox": psm_bbox(psm, psm_ref),
                    "psm_graphic_ref": psm_ref,
                }
            )
    metrics = style_format_metrics(style_data)
    for record in labels:
        format_metric = metrics.get(int(record.get("format_ref", 0)))
        if format_metric is not None:
            record["font_family"], ratio = format_metric
            record["font_size"] = ratio * SHEET_UNIT
    return labels


def template_unicode_labels(data: bytes, psm: bytes, style_data: bytes) -> list[dict[str, object]]:
    """Read length-prefixed UTF-16 template strings, including Chinese text."""

    labels: list[dict[str, object]] = []
    for offset in range(0, len(data) - 8, 2):
        length = struct.unpack_from("<H", data, offset)[0]
        end = offset + 2 + length * 2
        if not 2 <= length <= 100 or end + 32 > len(data):
            continue
        try:
            text = data[offset + 2 : end].decode("utf-16le").strip()
        except UnicodeDecodeError:
            continue
        if not any("\u4e00" <= char <= "\u9fff" for char in text):
            continue
        if not all(
            char in " -_.,()（）/:&+" or char.isascii() and char.isalnum() or "\u4e00" <= char <= "\u9fff"
            for char in text
        ):
            continue
        # Shape2D stores the transform immediately after some strings and at
        # +24 bytes after others. Locate the first valid x/y/direction group
        # instead of treating the intervening object header as coordinates.
        transform = next(
            (
                struct.unpack_from("<dddd", data, transform_offset)
                for transform_offset in range(end, min(end + 33, len(data) - 32), 8)
                if 0.001 <= struct.unpack_from("<d", data, transform_offset)[0] <= 1
                and 0.001 <= struct.unpack_from("<d", data, transform_offset + 8)[0] <= 1
                and 0.8 <= math.hypot(
                    struct.unpack_from("<d", data, transform_offset + 16)[0],
                    struct.unpack_from("<d", data, transform_offset + 24)[0],
                ) <= 1.2
            ),
            None,
        )
        if transform is None:
            continue
        x, y, direction_x, direction_y = transform
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0.8 <= math.hypot(direction_x, direction_y) <= 1.2):
            continue
        # This title's object points to style 0x00D5.  Its StyleCluster entry
        # is SimHei-Z with a 0.004 page-unit height (67.2 page units).
        simhei = style_data.find("SimHei-Z".encode("utf-16le"))
        size_ratio = 0.004
        if simhei >= 28:
            nearby = [
                struct.unpack_from("<d", style_data, offset)[0]
                for offset in range(simhei - 64, simhei)
                if 0.001 <= struct.unpack_from("<d", style_data, offset)[0] <= 0.01
            ]
            if nearby:
                size_ratio = min(nearby, key=lambda value: abs(value - 0.004))
        # The text record and PSM object can be siblings. Select the nearby
        # source reference whose PSM envelope best agrees with this SHA anchor.
        candidates = []
        for ref_offset in range(max(0, offset - 64), offset, 2):
            graphic_ref = struct.unpack_from("<I", data, ref_offset)[0]
            for bbox in psm_bboxes(psm, graphic_ref):
                left, bottom, right, top = bbox
                score = abs(x * SHEET_UNIT - left) + 3 * abs(y * SHEET_UNIT - top)
                candidates.append((score, graphic_ref, bbox))
        _, graphic_ref, bbox = min(candidates, default=(float("inf"), 0, None), key=lambda item: item[0])
        if bbox is not None:
            left, bottom, right, top = bbox
            expected_height = size_ratio * SHEET_UNIT
            # A nearby PSM ref can be a title-block/container object rather
            # than this UTF-16 label. Accept its envelope only when it is
            # plausible for the decoded SimHei glyph metric.
            if (
                not 0.45 * expected_height <= top - bottom <= 4.0 * expected_height
                or right - left > max(1200, len(text) * expected_height * 3.0)
            ):
                graphic_ref, bbox = 0, None
        labels.append(
            {
                "text": text,
                "x": x,
                "y": y,
                "font_size": size_ratio * SHEET_UNIT,
                "font_family": 'SimHei, STHeiti, "PingFang SC", "Noto Sans CJK SC", sans-serif',
                "style_ref": "0x00D5",
                "graphic_ref": graphic_ref,
                "bbox": bbox,
            }
        )
    return labels


def component_layer_lines(component_svg: Path, graphics: dict[int, list[str]]) -> list[str]:
    """Reuse the SHA-UCI vector layer while retaining its component identity."""

    content = component_svg.read_text(encoding="utf-8")
    lines: list[str] = []
    for element in re.findall(r"<line\b[^>]*/>", content):
        graphic = re.search(r'data-graphic="0x([0-9A-Fa-f]+)"', element)
        if graphic is None:
            continue
        ref = int(graphic.group(1), 16)
        # Only retain graphics still referenced by this SHA's current dynamic
        # attribute table. The prior layer can contain stale page artefacts.
        if ref not in graphics:
            continue
        ucis = ",".join(sorted(set(graphics.get(ref, []))))
        semantic = f' data-uci="{html.escape(ucis)}"' if ucis else ""
        # The reusable layer already has provenance attributes. Strip them
        # before adding the current SHA provenance so XML remains valid.
        clean = re.sub(r'\sdata-(?:layer|uci)="[^"]*"', "", element)
        lines.append(clean[:-2] + f' data-layer="uci-component"{semantic}/>')
    return lines


def counted_text_payload_is_record_local(
    data: bytes, count_offset: int, declared_count: int
) -> bool:
    """Validate a swallowed printable count against its owning 0x4D record."""

    text_end = count_offset + 2 + declared_count * 2
    for record_start in range(max(0, count_offset - 48), count_offset):
        if data[record_start : record_start + 2] != b"\x4d\x00":
            continue
        if record_start + 6 > len(data):
            continue
        record_size = struct.unpack_from("<I", data, record_start + 2)[0]
        record_end = record_start + 6 + record_size
        local_offset = count_offset - record_start
        if (
            24 <= local_offset <= 48
            and local_offset % 2 == 0
            and text_end <= record_end <= len(data)
        ):
            return True
    return False


def text_records(data: bytes) -> list[dict[str, object]]:
    """Read text plus its adjacent Shape2D graphic/style references."""

    records: list[dict[str, object]] = []
    injected_text_ranges: list[tuple[int, int]] = []
    # Synthetic weld labels have a reserved graphic reference whose page-local
    # bits encode the UTF-16 character count, then four transform doubles. Parse that
    # structure directly: the generic printable scan can otherwise swallow a
    # count byte or mistake unrelated source bytes for a synthetic label.
    for signature in re.finditer(rb"\xF0\x00", data):
        object_start = signature.start() - 2
        text_start = object_start + 24
        graphic_ref = struct.unpack_from("<I", data, object_start)[0]
        if not is_injected_weld_label_ref(graphic_ref):
            continue
        text_end = text_start + injected_weld_label_stored_length(graphic_ref) * 2
        if object_start < 0 or text_end + 32 > len(data):
            continue
        try:
            decoded = data[text_start:text_end].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        if not decoded or any(
            character != " " and not character.isprintable()
            for character in decoded
        ):
            continue
        x, y, direction_x, direction_y = struct.unpack_from("<dddd", data, text_end)
        if (
            not all(
                math.isfinite(value)
                for value in (x, y, direction_x, direction_y)
            )
            or not (-1 < x < 2 and -1 < y < 2)
            or not (0.8 <= math.hypot(direction_x, direction_y) <= 1.2)
        ):
            continue
        injected_text_ranges.append((text_start, text_end))
        records.append(
            {
                "text": decoded.strip(),
                "graphic_ref": graphic_ref,
                "style_ref": struct.unpack_from("<I", data, text_start - 16)[0],
                "x": x,
                "y": y,
                "direction_x": direction_x,
                "direction_y": direction_y,
                "format_ref": 0,
                "text_start": text_start,
            }
        )

    structured_unicode_ranges: list[tuple[int, int]] = []
    # Ordinary Shape2D 0x4D records carry a counted UTF-16LE string at byte
    # 30.  The historical printable-ASCII scan stops before characters such
    # as the degree sign, then reads the remaining glyph bytes as transform
    # data and discards the whole record.  Decode only structurally bounded
    # non-ASCII records here; the established scanner below continues to
    # handle the producer variants with extra property pairs.
    for match in re.finditer(re.escape(b"\x4d\x00"), data):
        record_start = match.start()
        if record_start + 30 > len(data):
            continue
        record_size = struct.unpack_from("<I", data, record_start + 2)[0]
        record_end = record_start + 2 + record_size
        declared_count = struct.unpack_from("<H", data, record_start + 28)[0]
        text_start = record_start + 30
        text_end = text_start + declared_count * 2
        if (
            not 1 <= declared_count <= 240
            or text_end + 32 > record_end
            or record_end > len(data)
        ):
            continue
        try:
            decoded = data[text_start:text_end].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        if (
            not any(ord(character) > 0x7E for character in decoded)
            or any(not character.isprintable() for character in decoded)
        ):
            continue
        x, y, direction_x, direction_y = struct.unpack_from("<dddd", data, text_end)
        if (
            not all(math.isfinite(value) for value in (x, y, direction_x, direction_y))
            or not (-1 < x < 2 and -1 < y < 2)
            or not (0.8 <= math.hypot(direction_x, direction_y) <= 1.2)
        ):
            continue
        structured_unicode_ranges.append((text_start, text_end))
        records.append(
            {
                "text": decoded.strip(),
                "graphic_ref": struct.unpack_from("<I", data, record_start + 6)[0],
                "style_ref": struct.unpack_from("<I", data, record_start + 14)[0],
                "x": x,
                "y": y,
                "direction_x": direction_x,
                "direction_y": direction_y,
                "format_ref": struct.unpack_from("<I", data, record_start + 20)[0],
                "text_start": text_start,
            }
        )

    for match in re.finditer(rb"(?:(?:[\x20-\x7e]\x00){1,})", data):
        if match.start() < 24 or match.end() + 32 > len(data):
            continue
        # A Shape2D text object starts with record type 0x004D followed by a
        # uint32 record size.  When the low size byte is printable (0x44 in
        # this file), the generic UTF-16 scan sees the header itself as
        # ``MD``.  Reject only structurally valid record headers; a real word
        # beginning with those letters will not declare a bounded record.
        if data[match.start() : match.start() + 2] == b"\x4d\x00":
            record_size = struct.unpack_from("<I", data, match.start() + 2)[0]
            if (
                32 <= record_size <= 100_000
                and match.start() + 6 + record_size <= len(data)
            ):
                continue
        if any(
            text_start - 2 <= match.start() < text_end
            for text_start, text_end in injected_text_ranges + structured_unicode_ranges
        ):
            continue
        # Shape2D stores a uint16 UTF-16 character count immediately before
        # some strings. Counts 32..126 look like one printable UTF-16 glyph
        # (for example 0x21 appears as "!") and can be swallowed by the
        # printable-text scan. Recover the real text start before resolving
        # the adjacent graphic/style references.
        text_start = match.start()
        decoded = match.group().decode("utf-16le", errors="replace")
        text_end = match.end()
        declared_count = struct.unpack_from("<H", data, match.start())[0]
        if declared_count == len(decoded) - 1 and counted_text_payload_is_record_local(
            data, match.start(), declared_count
        ):
            text_start += 2
            decoded = decoded[1:]
        elif match.start() >= 2:
            prefixed_count = struct.unpack_from("<H", data, match.start() - 2)[0]
            if 1 <= prefixed_count <= len(decoded):
                declared_text = decoded[:prefixed_count]
                if all(character.isprintable() for character in declared_text):
                    decoded = declared_text
                    text_end = match.start() + prefixed_count * 2
        graphic_ref = struct.unpack_from("<I", data, text_start - 24)[0]
        # Native graphic object identifiers in the generic Sheet/template
        # text stream are 16-bit.  A larger value means that this permissive
        # UTF-16 scan started inside an unrelated binary record (typically it
        # manifests as a stray single character laid over real text).  Weld
        # annotations use a reserved high-id range, but those records are
        # decoded by the structured pass above and excluded from this scan.
        if graphic_ref > 0xFFFF:
            continue
        x, y, direction_x, direction_y = struct.unpack_from("<dddd", data, text_end)
        # Some template strings carry property pairs before their transform.
        if abs(x) + abs(y) < 1e-100:
            for offset in range(text_end + 4, min(len(data) - 32, text_end + 144), 4):
                candidate_x, candidate_y, candidate_dx, candidate_dy = struct.unpack_from("<dddd", data, offset)
                if (
                    1e-6 <= candidate_x <= 1
                    and 1e-6 <= candidate_y <= 1
                    and 0.8 <= math.hypot(candidate_dx, candidate_dy) <= 1.2
                ):
                    x, y, direction_x, direction_y = candidate_x, candidate_y, candidate_dx, candidate_dy
                    break
        if not (-1 < x < 2 and -1 < y < 2):
            continue
        # Template records carry a compact text-run format in the high word
        # of one of the property dwords immediately before the UTF-16 count.
        # Ordinary Sheet records use larger object ids in the same slots, so
        # constrain this to the observed StyleCluster format-id range.
        format_ref = 0
        for delta in range(12, 33, 4):
            if text_start < delta:
                continue
            value = struct.unpack_from("<I", data, text_start - delta)[0]
            candidate = value >> 16
            if 0x20 <= candidate < 0x200:
                format_ref = candidate
                break
        records.append(
            {
                "text": decoded.strip(),
                "graphic_ref": graphic_ref,
                "style_ref": struct.unpack_from("<I", data, text_start - 16)[0],
                "x": x,
                "y": y,
                "direction_x": direction_x,
                "direction_y": direction_y,
                "format_ref": format_ref,
                "text_start": text_start,
            }
        )
    records.sort(key=lambda record: int(record["text_start"]))
    return records


def intersects_bbox(
    x1: float, y1: float, x2: float, y2: float, bbox: tuple[int, int, int, int]
) -> bool:
    """Use a conservative bounding-box test for the undecoded Shape2D hierarchy."""

    left, bottom, right, top = bbox
    return max(x1, x2) >= left and min(x1, x2) <= right and max(y1, y2) >= bottom and min(y1, y2) <= top


def sheet_rectangles(
    segments: list[tuple[float, float, float, float, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Recover closed axis-aligned callout frames from four SHA line records."""

    horizontal = []
    vertical = []
    for x1, y1, x2, y2, _, _ in segments:
        ax, ay, bx, by = (round(x1 * SHEET_UNIT), round(y1 * SHEET_UNIT), round(x2 * SHEET_UNIT), round(y2 * SHEET_UNIT))
        if ay == by:
            horizontal.append((ay, min(ax, bx), max(ax, bx)))
        elif ax == bx:
            vertical.append((ax, min(ay, by), max(ay, by)))
    rectangles: set[tuple[int, int, int, int]] = set()
    for bottom, left, right in horizontal:
        for top, other_left, other_right in horizontal:
            if top <= bottom or (left, right) != (other_left, other_right):
                continue
            for x in (left, right):
                if (x, bottom, top) not in vertical:
                    break
            else:
                if 40 <= right - left <= 1500 and 40 <= top - bottom <= 500:
                    rectangles.add((left, bottom, right, top))
    return sorted(rectangles)


def sheet_revision_triangle_records(
    segments: list[tuple[float, float, float, float, int, int]],
) -> list[dict[str, object]]:
    """Recover and normalize large closed revision-marker composites.

    Arrowheads use the same three-line topology but are much smaller.  The
    physical revision markers are the closed, three-vertex groups whose page
    extents are roughly one body-text line high in both directions.  Their
    child-line coordinates can carry the compound symbol's local rotation;
    that transform is not the intended plotted orientation.  Preserve the
    source centre and side length, then reconstruct the marker as an upright
    equilateral triangle, which is the semantic Shape2D revision symbol.
    """

    grouped: dict[int, list[tuple[float, float, float, float, int]]] = defaultdict(list)
    for x1, y1, x2, y2, parent_ref, child_ref in segments:
        grouped[parent_ref].append(
            (
                x1 * SHEET_UNIT,
                y1 * SHEET_UNIT,
                x2 * SHEET_UNIT,
                y2 * SHEET_UNIT,
                child_ref,
            )
        )
    triangles: list[dict[str, object]] = []
    for parent_ref, parent_lines in grouped.items():
        # Older Chengda SHAs give every revision triangle its own composite
        # parent.  Some later units instead put several unrelated page
        # primitives beneath one shared Sheet parent (commonly Sheet6 ref 6).
        # Split each parent into endpoint-connected components before testing
        # the three-edge topology so those markers remain independently
        # identifiable without relying on their page coordinates.
        endpoint_edges: dict[tuple[float, float], list[int]] = defaultdict(list)
        normalized_lines: list[
            tuple[float, float, float, float, int, tuple[float, float], tuple[float, float]]
        ] = []
        for index, (x1, y1, x2, y2, child_ref) in enumerate(parent_lines):
            first = (round(x1, 3), round(y1, 3))
            second = (round(x2, 3), round(y2, 3))
            normalized_lines.append((x1, y1, x2, y2, child_ref, first, second))
            endpoint_edges[first].append(index)
            endpoint_edges[second].append(index)
        components: list[list[tuple[float, float, float, float, int]]] = []
        visited: set[int] = set()
        for start_index in range(len(normalized_lines)):
            if start_index in visited:
                continue
            pending = [start_index]
            component_indices: list[int] = []
            while pending:
                index = pending.pop()
                if index in visited:
                    continue
                visited.add(index)
                component_indices.append(index)
                *_, first, second = normalized_lines[index]
                for endpoint in (first, second):
                    pending.extend(
                        neighbour
                        for neighbour in endpoint_edges[endpoint]
                        if neighbour not in visited
                    )
            components.append(
                [parent_lines[index] for index in component_indices]
            )

        for lines in components:
            if len(lines) != 3:
                continue
            vertices: dict[tuple[float, float], int] = defaultdict(int)
            for x1, y1, x2, y2, _ in lines:
                vertices[(round(x1, 3), round(y1, 3))] += 1
                vertices[(round(x2, 3), round(y2, 3))] += 1
            if len(vertices) != 3 or set(vertices.values()) != {2}:
                continue
            xs = [point[0] for point in vertices]
            ys = [point[1] for point in vertices]
            left, bottom, right, top = min(xs), min(ys), max(xs), max(ys)
            width, height = right - left, top - bottom
            # Chengda page-level revision symbols are owned by the shared
            # Sheet parent (ref 6).  A small subset is plotted at roughly
            # 350 page units wide, while ordinary component-local triangles
            # remain below the established 320-unit ceiling.  Widen only the
            # structurally identified Sheet-parent family so arrowheads and
            # triangular component geometry cannot enter this semantic layer.
            max_width = 400 if parent_ref == 6 else 320
            max_height = 340 if parent_ref == 6 else 320
            if 90 <= width <= max_width and 90 <= height <= max_height:
                side_lengths = [
                    math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2, _ in lines
                ]
                side = sum(side_lengths) / len(side_lengths)
                center_x = sum(xs) / 3
                center_y = sum(ys) / 3
                normalized_height = side * math.sqrt(3) / 2
                child_refs = tuple(sorted(line[4] for line in lines))
                points = (
                    (center_x, center_y + normalized_height * 2 / 3),
                    (center_x - side / 2, center_y - normalized_height / 3),
                    (center_x + side / 2, center_y - normalized_height / 3),
                )
                triangles.append(
                    {
                        "parent_ref": parent_ref,
                        "child_refs": child_refs,
                        "triangle_key": child_refs,
                        "source_bbox": (float(left), float(bottom), float(right), float(top)),
                        "center": (center_x, center_y),
                        "side": side,
                        "height": normalized_height,
                        "points": points,
                    }
                )
    return sorted(triangles, key=lambda triangle: int(triangle["parent_ref"]))


def sheet_revision_triangles(
    segments: list[tuple[float, float, float, float, int, int]],
) -> list[tuple[float, float, float, float]]:
    """Compatibility view of revision-marker source bounding boxes."""

    return [
        triangle["source_bbox"]  # type: ignore[misc]
        for triangle in sheet_revision_triangle_records(segments)
    ]


def sheet_revision_embedded_labels(
    data: bytes,
    triangles: list[dict[str, object]],
) -> dict[int, str]:
    """Decode the short text record serialized before a 0x4A revision group.

    Some Chengda sheets store the revision digit as a compact ``0x07`` text
    run immediately before the triangle composite instead of as an ordinary
    Sheet text object. The run declares a UTF-16 character count followed by
    the actual revision string.
    """

    result: dict[int, str] = {}
    group_signature = b"\x7b\x00\x4a\x00\x00\x00"
    for triangle in triangles:
        parent_ref = int(triangle["parent_ref"])
        signature = group_signature + struct.pack("<I", parent_ref)
        group_start = data.find(signature)
        if group_start < 0:
            continue
        window_start = max(0, group_start - 160)
        for offset in range(group_start - 6, window_start - 1, -2):
            if data[offset : offset + 2] != b"\x07\x00":
                continue
            count = struct.unpack_from("<H", data, offset + 2)[0]
            if not 1 <= count <= 4 or offset + 4 + count * 2 > group_start:
                continue
            label = data[offset + 4 : offset + 4 + count * 2].decode(
                "utf-16le", errors="ignore"
            )
            if re.fullmatch(r"\d+[A-Z]?", label):
                result[parent_ref] = label
                break
    return result


def rectangles_by_parent_ref(
    segments: list[tuple[float, float, float, float, int, int]]
) -> dict[int, tuple[int, int, int, int]]:
    """Recover closed axis-aligned rectangles together with their SHA parent ref."""

    grouped: dict[int, list[tuple[int, int, int, int]]] = defaultdict(list)
    for x1, y1, x2, y2, parent_ref, _ in segments:
        ax, ay, bx, by = (
            round(x1 * SHEET_UNIT),
            round(y1 * SHEET_UNIT),
            round(x2 * SHEET_UNIT),
            round(y2 * SHEET_UNIT),
        )
        if (ay == by and ax != bx) or (ax == bx and ay != by):
            grouped[parent_ref].append((ax, ay, bx, by))
    rectangles: dict[int, tuple[int, int, int, int]] = {}
    for parent_ref, lines in grouped.items():
        horizontal = [line for line in lines if line[1] == line[3]]
        vertical = [line for line in lines if line[0] == line[2]]
        if len(horizontal) != 2 or len(vertical) != 2:
            continue
        xs = sorted({line[0] for line in vertical})
        ys = sorted({line[1] for line in horizontal})
        if len(xs) != 2 or len(ys) != 2:
            continue
        left, right = xs
        bottom, top = ys
        if not (40 <= right - left <= 1500 and 40 <= top - bottom <= 500):
            continue
        required = {
            tuple(sorted(((left, bottom), (right, bottom)))),
            tuple(sorted(((left, top), (right, top)))),
            tuple(sorted(((left, bottom), (left, top)))),
            tuple(sorted(((right, bottom), (right, top)))),
        }
        actual = {
            tuple(sorted(((line[0], line[1]), (line[2], line[3]))))
            for line in lines
        }
        if required <= actual:
            rectangles[parent_ref] = (left, bottom, right, top)
    return rectangles


def rectangles_by_text_style_sequence(
    segments: list[tuple[float, float, float, float, int, int]]
) -> dict[int, tuple[int, int, int, int]]:
    """Recover frames whose four edge styles immediately follow a text ref.

    Some support/component callouts use a different Sheet relationship from
    marker boxes: their four line records share a parent graphic, while their
    *style* references are ``text_ref + 1`` through ``text_ref + 4``.  This is
    a direct SHA sequence, not a geometric proximity inference.
    """

    grouped: dict[int, list[tuple[int, int, int, int, int]]] = defaultdict(list)
    for x1, y1, x2, y2, parent_ref, style_ref in segments:
        grouped[parent_ref].append(
            (
                round(x1 * SHEET_UNIT),
                round(y1 * SHEET_UNIT),
                round(x2 * SHEET_UNIT),
                round(y2 * SHEET_UNIT),
                style_ref,
            )
        )
    frames: dict[int, tuple[int, int, int, int]] = {}
    for lines in grouped.values():
        # Composite child coordinates are quantised, so nominally horizontal
        # and vertical frame edges can differ by one or two page units.
        horizontal = [line for line in lines if abs(line[1] - line[3]) <= 2 and line[0] != line[2]]
        vertical = [line for line in lines if abs(line[0] - line[2]) <= 2 and line[1] != line[3]]
        # A single SHA parent can carry several adjacent frames, so inspect
        # every horizontal-pair/vertical-pair combination rather than
        # requiring the parent to contain exactly four lines.
        for lower in horizontal:
            left, right = sorted((lower[0], lower[2]))
            bottom = lower[1]
            for upper in horizontal:
                upper_left, upper_right = sorted((upper[0], upper[2]))
                if (
                    upper is lower
                    or abs(upper_left - left) > 2
                    or abs(upper_right - right) > 2
                    or upper[1] <= bottom
                ):
                    continue
                top = upper[1]
                if not (40 <= right - left <= 1500 and 40 <= top - bottom <= 500):
                    continue
                left_edge = next(
                    (
                        line
                        for line in vertical
                        if abs(line[0] - left) <= 2
                        and abs(min(line[1], line[3]) - bottom) <= 2
                        and abs(max(line[1], line[3]) - top) <= 2
                    ),
                    None,
                )
                right_edge = next(
                    (
                        line
                        for line in vertical
                        if abs(line[0] - right) <= 2
                        and abs(min(line[1], line[3]) - bottom) <= 2
                        and abs(max(line[1], line[3]) - top) <= 2
                    ),
                    None,
                )
                if left_edge is None or right_edge is None:
                    continue
                styles = sorted((lower[4], upper[4], left_edge[4], right_edge[4]))
                if styles == list(range(styles[0], styles[0] + 4)):
                    frames[styles[0] - 1] = (left, bottom, right, top)
    return frames


def style_fallbacks(data: bytes, psm: bytes) -> dict[int, tuple[float, float, float, float]]:
    """Learn text placement offsets from SHA objects that have a PSM extent.

    PSM is an optional rendered extent, while every text object has a Shape2D
    anchor.  The per-style medians let us render labels that do not have a PSM
    record (notably coordinate and connection notes) in the same local style.
    """

    samples: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for record in text_records(data):
        text = str(record["text"]).strip()
        x, y = float(record["x"]), float(record["y"])
        bbox = psm_bbox(psm, int(record["graphic_ref"]))
        if not text or bbox is None or not (0 <= x <= 1 and 0 <= y <= PAGE_HEIGHT / SHEET_UNIT):
            continue
        left, bottom, right, top = bbox
        samples[int(record["style_ref"])].append(
            (left - x * SHEET_UNIT, bottom - y * SHEET_UNIT, top - bottom, (right - left) / max(1, len(text)))
        )
    return {
        style: tuple(sorted(values, key=lambda value: value[index])[len(values) // 2][index] for index in range(4))
        for style, values in samples.items()
        if len(values) >= 2
    }


def bounded_style_fallbacks(data: bytes, psm: bytes) -> dict[int, tuple[tuple[float, float, float, float], int]]:
    """Return local text metrics supported by ordinary, non-container PSM boxes.

    A short real Sheet label can occasionally resolve to a page-level PSM
    container.  Only use a same-style replacement when several peer labels on
    that *same Sheet* have normal glyph-height envelopes; this excludes binary
    false positives and prevents a page-level container from poisoning the
    recovered metrics.
    """

    samples: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for record in text_records(data):
        text = str(record["text"]).strip()
        x, y = float(record["x"]), float(record["y"])
        bbox = psm_bbox(psm, int(record["graphic_ref"]))
        if not text or bbox is None or not (0 <= x <= 1 and 0 <= y <= PAGE_HEIGHT / SHEET_UNIT):
            continue
        left, bottom, right, top = bbox
        glyph_height = top - bottom
        if not 30 <= glyph_height <= 320:
            continue
        samples[int(record["style_ref"])].append(
            (left - x * SHEET_UNIT, bottom - y * SHEET_UNIT, glyph_height, (right - left) / max(1, len(text)))
        )
    return {
        style: (tuple(median(value[index] for value in values) for index in range(4)), len(values))
        for style, values in samples.items()
    }


def merged_style_fallbacks(sheets: dict[str, bytes], psm: bytes) -> dict[int, tuple[float, float, float, float]]:
    merged: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for data in sheets.values():
        for style, values in style_fallbacks(data, psm).items():
            merged[style].append(values)
        for record in text_records(data):
            style = int(record["style_ref"])
            text = str(record["text"]).strip()
            x, y = float(record["x"]), float(record["y"])
            bbox = psm_bbox(psm, int(record["graphic_ref"]))
            if not text or bbox is None or not (0 <= x <= 1 and 0 <= y <= PAGE_HEIGHT / SHEET_UNIT):
                continue
            left, bottom, right, top = bbox
            merged[style].append(
                (left - x * SHEET_UNIT, bottom - y * SHEET_UNIT, top - bottom, (right - left) / max(1, len(text)))
            )
    return {
        style: tuple(median(value[index] for value in samples) for index in range(4))
        for style, samples in merged.items()
        if samples
    }


def merged_format_fallbacks(
    sheets: dict[str, bytes], psm: bytes
) -> dict[tuple[int, int, bool], tuple[float, float, float, float]]:
    """Learn PSM text metrics without mixing distinct text-run formats."""

    samples: dict[tuple[int, int, bool], list[tuple[float, float, float, float]]] = defaultdict(list)
    for data in sheets.values():
        for record in text_records(data):
            text = str(record["text"]).strip()
            x, y = float(record["x"]), float(record["y"])
            bbox = psm_bbox(psm, int(record["graphic_ref"]))
            direction_x, direction_y = float(record["direction_x"]), float(record["direction_y"])
            rotated = abs(direction_y) > 0.1 or abs(direction_x - 1) > 0.1
            if (
                not text
                or bbox is None
                or not (0 <= x <= 1 and 0 <= y <= PAGE_HEIGHT / SHEET_UNIT)
            ):
                continue
            left, bottom, right, top = bbox
            if rotated:
                font_height, text_length = rotated_text_extent(
                    right - left,
                    top - bottom,
                    math.degrees(math.atan2(direction_y, direction_x)),
                )
                char_width = text_length / max(1, len(text))
            else:
                font_height = top - bottom
                char_width = (right - left) / max(1, len(text))
            # Page-level PSM containers are not glyph samples. Keep the model
            # bounded to the ordinary text sizes observed in this page system;
            # larger declared formats can still be reconstructed directly
            # from StyleCluster instead of contaminating peer medians.
            if not 30.0 <= font_height <= 320.0 or not 1.0 <= char_width <= 320.0:
                continue
            key = (int(record["style_ref"]), int(record.get("format_ref", 0)), rotated)
            samples[key].append(
                (left - x * SHEET_UNIT, bottom - y * SHEET_UNIT, font_height, char_width)
            )
    return {
        key: tuple(median(value[index] for value in values) for index in range(4))
        for key, values in samples.items()
        if values
    }


def render(
    sha_path: Path,
    output_path: Path,
    wanted_page: int,
    debug_boxes: bool,
    manifest_path: Path | None,
    component_layer: Path | None,
    sheet_stream: str | None = None,
    project_profile: str = "000200fw",
    signature_dir: Path | None = None,
    pcf_path: Path | None = None,
) -> None:
    streams = read_sha_streams(sha_path)
    sheets = {
        name: data
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name)
        and (
            len(data) > 1024
            or (name == "Sheet6" and bool(text_objects(data)))
        )
    }
    if sheet_stream is not None:
        if sheet_stream not in sheets:
            raise ValueError(f"Sheet stream {sheet_stream!r} was not found in {sha_path.name}")
        selected_name = sheet_stream
    else:
        selected_name = next(
            (
                name
                for name, data in sheets.items()
                if logical_page(text_objects(data))
                and logical_page(text_objects(data))[0] == wanted_page
            ),
            None,
        )
    if selected_name is None:
        raise ValueError(f"ISO page {wanted_page} was not found in {sha_path.name}")

    dynamic = dynamic_graphics(streams.get("Unclustered Dynamic Attributes", b""))
    psm = streams.get("PSMcluster0", b"")
    sheet = sheets[selected_name]
    # Sheet6 carries the shared physical-page declaration in the supplied
    # multi-sheet SHAs. A later Sheet may only contain local graphics records.
    shared_viewbox = declared_sheet_viewbox(sheets.get("Sheet6", b""))
    if shared_viewbox is None:
        shared_viewbox = next(
            (viewbox for data in sheets.values() if (viewbox := declared_sheet_viewbox(data)) is not None),
            None,
        )
    view_x, view_y, view_width, view_height = sheet_viewbox(sheet, shared_viewbox)
    page_width_mm = round(view_width / SHEET_UNIT * 1000)
    page_height_mm = round(view_height / SHEET_UNIT * 1000)
    graphics: dict[int, list[str]] = defaultdict(list)
    for uci, records in dynamic.items():
        for record in records:
            ref = int(record["graphic_ref"])
            if struct.pack("<I", ref) in sheet:
                graphics[ref].append(uci)
    uci_by_object_ref: dict[int, list[str]] = defaultdict(list)
    for graphic_ref, ucis in graphics.items():
        uci_by_object_ref[graphic_ref & 0xFFFF].extend(ucis)
    uci_regions = [
        {"graphic_ref": ref, "uci": uci, "bbox": bbox}
        for ref, ucis in graphics.items()
        if (bbox := psm_bbox(psm, ref)) is not None
        for uci in sorted(set(ucis))
    ]
    component_lines = component_layer_lines(component_layer, graphics) if component_layer else []
    _template_name, template_data = discover_template_stream(streams)
    template_segments = template_line_segments(template_data)
    template_circles = template_circle_primitives(template_data)
    template_polylines = template_polyline_paths(template_data)
    template_arrowheads = template_closed_triangles(
        template_data, template_circles
    )
    style_data = streams.get("StyleCluster", b"")
    template_style_by_child = template_line_style_refs(template_data)
    template_width_by_style = resolved_line_style_widths(style_data)
    template_dash_by_style = line_style_dasharrays(style_data)
    format_metrics = style_format_metrics(style_data)
    images = template_images(streams)
    filled_paths = template_filled_paths(template_data, style_data)
    template_text = template_text_records(
        template_data, psm, streams.get("StyleCluster", b"")
    )
    from chengda_template_owned_page import (
        is_template_owned_page, plot_text_records, render_plot_content,
        bound_title_geometry, revision_text_geometry,
    )
    template_owned = is_template_owned_page(streams, selected_name, _template_name)
    owned_text = []
    if template_owned:
        owned_text = plot_text_records(template_data, psm, style_data, sys.modules[__name__])
        owned_keys = {(r['text'], round(r['x'], 9), round(r['y'], 9)) for r in owned_text}
        template_text = [r for r in template_text if
                         (r['text'], round(r['x'], 9), round(r['y'], 9)) not in owned_keys]
        template_circles = [r for r in template_circles if float(r['radius']) > 30]
    grid_zone_labels = template_grid_zone_labels(template_text, template_segments)
    grid_zone_keys = {
        (int(record["graphic_ref"]), str(record["text"])) for record in grid_zone_labels
    }
    template_text = [
        record
        for record in template_text
        if (int(record["graphic_ref"]), str(record["text"])) not in grid_zone_keys
    ]
    multiline_text = template_multiline_text_records(
        template_data, psm, streams.get("StyleCluster", b"")
    )
    multiline_first_lines = {
        str(record["text"]).splitlines()[0].strip()
        for record in multiline_text
        if str(record["text"]).splitlines()
    }
    template_text = [
        record
        for record in template_text
        if str(record["text"]).strip() not in multiline_first_lines
    ]
    revision_text = template_bound_text(
        template_data,
        streams,
        psm,
    )
    latest_revision_record = max(
        (
            record
            for record in revision_text
            if record.get("field") == "MajorRev_ForRevise"
            and record.get("record_index") is not None
        ),
        key=lambda record: int(record["record_index"]),
        default=None,
    )
    latest_revision_label = (
        str(latest_revision_record["text"])
        if latest_revision_record is not None
        and re.fullmatch(r"\d+[A-Z]?", str(latest_revision_record["text"]))
        else ""
    )
    signature_records = revision_signature_records(
        revision_text,
        template_segments,
        signature_dir,
    )
    notes = template_notes(template_data, streams.get("StyleCluster", b""))
    anchor_labels = template_anchor_labels(
        template_data, psm, streams.get("StyleCluster", b"")
    )
    unicode_labels = template_unicode_labels(
        template_data, psm, streams.get("StyleCluster", b"")
    )

    elements = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_width_mm}mm" height="{page_height_mm}mm" '
        f'data-page-width-mm="{page_width_mm}" data-page-height-mm="{page_height_mm}" '
        f'viewBox="{view_x:.3f} {view_y:.3f} {view_width:.3f} {view_height:.3f}" preserveAspectRatio="xMidYMid meet">',
        "<metadata>"
        + html.escape(json.dumps({"uci_regions": uci_regions}, ensure_ascii=False))
        + "</metadata>",
        '<rect width="100%" height="100%" fill="white"/>',
        '<g id="sha-uci-regions" fill="none" stroke="none" visibility="hidden">',
    ]
    # Preserve every page UCI in the SVG, including symbols whose internal
    # Shape2D paths have not been decoded into lines or text yet.
    for region in uci_regions:
        left, bottom, right, top = region["bbox"]
        elements.append(
            f'<rect x="{left}" y="{PAGE_HEIGHT - top}" width="{right - left}" height="{top - bottom}" '
            f'data-uci="{html.escape(str(region["uci"]))}" '
            f'data-graphic="0x{int(region["graphic_ref"]):08X}" '
            'data-mapping="direct-sha-dynamic-attribute"/>'
        )
    elements.append("</g>")
    if component_lines:
        elements.append(
            '<g id="sha-uci-component-geometry" fill="none" stroke="#17202a" stroke-width="8" '
            'stroke-linecap="square" shape-rendering="geometricPrecision">'
        )
        elements.extend(component_lines)
        elements.append("</g>")
    if template_segments:
        elements.append(
            '<g id="sha-template-geometry" fill="none" stroke="#17202a" stroke-width="8" '
            'stroke-linecap="square" shape-rendering="geometricPrecision">'
        )
        for x1, y1, x2, y2, template_ref, child_ref in template_segments:
            line_style = template_style_by_child.get(child_ref, 0)
            stroke_width = template_width_by_style.get(line_style, 0.00025 * SHEET_UNIT)
            dash = template_dash_by_style.get(line_style)
            dash_attr = (
                f' stroke-dasharray="{" ".join(f"{value:.1f}" for value in dash)}"'
                if dash
                else ""
            )
            elements.append(
                f'<line x1="{x1 * SHEET_UNIT:.1f}" y1="{svg_y(y1 * SHEET_UNIT):.1f}" '
                f'x2="{x2 * SHEET_UNIT:.1f}" y2="{svg_y(y2 * SHEET_UNIT):.1f}" '
                f'stroke-width="{stroke_width:.1f}"{dash_attr} '
                f'data-layer="sha-template" data-graphic="0x{template_ref:08X}" '
                f'data-child="0x{child_ref:08X}" data-style="0x{line_style:04X}"/>'
            )
        elements.append("</g>")
    for image in images:
        width = float(image["width"]) * SHEET_UNIT
        height = float(image["height"]) * SHEET_UNIT
        x = float(image["x"]) * SHEET_UNIT
        y = PAGE_HEIGHT - (float(image["y"]) * SHEET_UNIT + height)
        elements.append(
            f'<image x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
            f'href="{image["href"]}" data-layer="sha-template-image" '
            f'data-resource="JSite{int(image["resource_id"])}"/>'
        )
    if filled_paths:
        elements.append(
            '<g id="sha-template-filled-paths" stroke="none" '
            'shape-rendering="geometricPrecision">'
        )
        for record in filled_paths:
            path_parts: list[str] = []
            for subpath in record["subpaths"]:
                first_x, first_y = subpath[0]
                path_parts.append(f"M {first_x:.3f} {svg_y(first_y):.3f}")
                path_parts.extend(
                    f"L {x:.3f} {svg_y(y):.3f}" for x, y in subpath[1:]
                )
                path_parts.append("Z")
            elements.append(
                f'<path d="{" ".join(path_parts)}" fill="{record["fill"]}" '
                'fill-rule="evenodd" '
                f'data-layer="sha-template-filled-path" '
                f'data-child="0x{int(record["child_ref"]):08X}" '
                f'data-style="0x{int(record["style_ref"]):04X}"/>'
            )
        elements.append("</g>")
    if template_circles:
        elements.append(
            '<g id="sha-template-circles" fill="none" stroke="#17202a" '
            'shape-rendering="geometricPrecision">'
        )
        for record in template_circles:
            style_ref = int(record["style_ref"])
            stroke_width = template_width_by_style.get(
                style_ref, 0.00025 * SHEET_UNIT
            )
            elements.append(
                f'<circle cx="{float(record["center_x"]):.1f}" '
                f'cy="{svg_y(float(record["center_y"])):.1f}" '
                f'r="{float(record["radius"]):.1f}" stroke-width="{stroke_width:.1f}" '
                'data-layer="sha-template-circle" '
                f'data-child="0x{int(record["child_ref"]):08X}" '
                f'data-style="0x{style_ref:04X}"/>'
            )
        elements.append("</g>")
    if template_arrowheads:
        elements.append('<g id="sha-template-filled-arrowheads" stroke="none" fill="#17202a">')
        for record in template_arrowheads:
            points = " ".join(
                f'{x:.3f},{svg_y(y):.3f}' for x, y in record["points"]
            )
            elements.append(
                f'<polygon points="{points}" fill="#17202a" '
                'data-layer="sha-template-filled-arrowhead" '
                f'data-graphic="0x{int(record["parent_ref"]):08X}"/>'
            )
        elements.append("</g>")
    if template_polylines:
        elements.append(
            '<g id="sha-template-polylines" fill="none" stroke="#17202a" '
            'stroke-linecap="round" stroke-linejoin="round" '
            'shape-rendering="geometricPrecision">'
        )
        for record in template_polylines:
            points = " ".join(
                f'{x:.3f},{svg_y(y):.3f}' for x, y in record["points"]
            )
            style_ref = int(record["style_ref"])
            stroke_width = template_width_by_style.get(
                style_ref, 0.00025 * SHEET_UNIT
            )
            elements.append(
                f'<polyline points="{points}" stroke-width="{stroke_width:.1f}" '
                'data-layer="sha-template-polyline" '
                f'data-child="0x{int(record["child_ref"]):08X}" '
                f'data-style="0x{style_ref:04X}"/>'
            )
        elements.append("</g>")
    if grid_zone_labels:
        elements.append(
            '<g id="sha-grid-zone-labels" fill="#17202a" stroke="none" '
            'font-family="Arial, Helvetica, sans-serif" '
            'text-rendering="geometricPrecision">'
        )
        for record in grid_zone_labels:
            grid_y = svg_y(float(record["render_y"]))
            elements.append(
                f'<text x="{float(record["render_x"]):.2f}" '
                f'y="{grid_y:.2f}" '
                f'font-size="{float(record["render_font_size"]) * CHENGDA_GRID_ZONE_FONT_SCALE:.2f}" '
                'text-anchor="middle" dominant-baseline="central" '
                'font-family="Arial, Helvetica, sans-serif" '
                f'transform="translate(0 {grid_y:.2f}) scale(1 {CHENGDA_GRID_ZONE_GLYPH_Y_SCALE:.4f}) '
                f'translate(0 {-grid_y:.2f})" '
                f'data-layer="sha-grid-zone-label" data-side="{record["side"]}" '
                f'data-graphic="0x{int(record["graphic_ref"]):04X}" '
                'data-mapping="sha-template-cell-centre-plus-local-cap-height">'
                f'{html.escape(str(record["text"]))}</text>'
            )
        elements.append("</g>")
    if template_text:
        # Template records use the same sans-serif StyleCluster family as the
        # decoded static title labels.  Keep this separate from the ISO body,
        # whose engineering annotations are an observed fixed-pitch family.
        elements.append('<g id="sha-template-text" fill="#17202a" font-family="Arial, Helvetica, sans-serif" text-rendering="geometricPrecision">')
        for record in template_text:
            left, bottom, right, top = record["bbox"]
            psm_is_cell = bool(record.get("psm_is_cell", False))
            height = (
                float(record.get("font_size", max(18.0, (top - bottom) * 0.82)))
                if psm_is_cell
                else max(36, float(record.get("font_size", top - bottom)))
            )
            format_ref = int(record.get("format_ref", 0))
            anchor_x = float(record["x"]) * SHEET_UNIT
            anchor_y = float(record["y"]) * SHEET_UNIT
            if psm_is_cell:
                if abs(anchor_x - left) <= max(2.0, (right - left) * 0.08):
                    anchor = "start"
                    render_x = float(left)
                    mapping = "sha-local-psm-cell-left-plus-derived-font-size"
                else:
                    anchor = "middle"
                    render_x = (left + right) / 2
                    mapping = "sha-local-psm-cell-centre-plus-derived-font-size"
                render_y = svg_y((bottom + top) / 2)
                length_attr = ""
                baseline_attr = 'dominant-baseline="central"'
            else:
                anchor = template_text_alignment(
                    format_ref,
                    anchor_x,
                    anchor_y,
                    template_segments,
                )
                render_x, mapping = template_text_render_x(
                    anchor_x,
                    record["bbox"],
                    anchor,
                    format_ref,
                )
                render_y = svg_y(anchor_y)
                length_attr = (
                    ""
                    if record.get("without_psm")
                    else f'textLength="{right - left}" lengthAdjust="spacingAndGlyphs"'
                )
                baseline_attr = (
                    'dominant-baseline="text-before-edge"'
                    if record.get("without_psm")
                    else 'dominant-baseline="text-after-edge"'
                )
            review_panel_text = is_chengda_review_panel_text(
                format_ref,
                float(record["x"]),
                float(record["y"]),
            )
            revision_header = is_chengda_revision_header(
                format_ref,
                float(record["x"]),
                float(record["y"]),
            )
            approval_label = is_chengda_approval_label(
                format_ref,
                float(record["x"]),
                float(record["y"]),
            )
            legend_title = is_chengda_legend_title(
                format_ref,
                float(record["x"]),
                float(record["y"]),
            )
            project_doc_label = is_chengda_project_doc_label(
                format_ref,
                float(record["x"]),
                float(record["y"]),
            )
            if review_panel_text and not psm_is_cell:
                # These records store their insertion point at the centre of
                # the PSM glyph run.  Treating it as a left edge moves the
                # heading and the right-column labels half a run to the right.
                anchor = "middle"
                render_x = anchor_x - 6.0
                mapping = "sha-review-panel-centre-anchor-plus-psm-width"
                if float(record["x"]) >= 0.35 and float(record["y"]) >= 0.065:
                    # The upper-right review label's PSM envelope includes
                    # layout space back to its checkbox.  Its Shape2D point
                    # is the actual text start; centring/stretching the run
                    # makes it cover that checkbox and renders it too wide.
                    anchor = "start"
                    render_x = float(left) + 2.0 * height
                    length_attr = (
                        f'textLength="{(right - left) - 2.0 * height}" '
                        'lengthAdjust="spacingAndGlyphs"'
                    )
                    mapping = "sha-review-panel-right-column-psm-left-plus-two-em"
            elif review_panel_text and psm_is_cell:
                # The lower-right label has a true glyph envelope rather than
                # a whole-cell box. Fit the Arial-Z outlines to that envelope
                # and retain the envelope centre with its observed inset.
                render_x -= 8.0
                length_attr = (
                    f'textLength="{right - left}" lengthAdjust="spacingAndGlyphs"'
                )
                mapping = "sha-review-panel-local-psm-glyph-envelope"
            if review_panel_text and not psm_is_cell:
                review_y = float(record["y"])
                if review_y >= 0.075:
                    # The heading is intentionally high in its cell in the
                    # issued drawing, but Chromium's after-edge puts it hard
                    # against the top rule without this plotter-em offset.
                    render_y += 8.0
                elif review_y >= 0.05:
                    # Checkbox labels share one baseline family.  Align their
                    # ink vertically with the boxes; do not move the lower
                    # Approved-by/Date rows or the local-PSM Hold label.
                    render_y += 12.0
            if approval_label:
                # Keep the SHA row geometry and compensate only for the
                # browser/plotter after-edge metric difference.
                render_y += CHENGDA_APPROVAL_LABEL_Y_OFFSET
            if revision_header and anchor == "start":
                # Authorized stores the same cell-centre insertion point as
                # the other six headings but lacks their centre flag.
                anchor = "middle"
                mapping = "sha-revision-header-cell-centre-plus-psm-width"
            if format_ref == CHENGDA_PROJECT_TITLE_FORMAT_REF:
                # Arial-Z's larger plotter em rises farther above the stored
                # after-edge than Chromium's Arial Narrow metrics.  Preserve
                # the SHA edge and compensate once for this format family.
                render_x -= 18.0
                render_y += 12.0
            if format_ref == CHENGDA_CLIENT_TITLE_FORMAT_REF:
                render_x -= 12.0
                render_y -= 15.0
                # Keep the native Arial-Z tracking. Width-forcing methods
                # collapse the two visible word spaces in Chromium output.
                length_attr = ""
            font_family = template_css_font_family(
                format_ref, str(record.get("font_family", ""))
            )
            font_style_attr = (
                f'font-style="{record["font_style"]}" '
                if record.get("font_style")
                else ""
            )
            if not psm_is_cell and anchor == "middle" and height >= 0.0075 * SHEET_UNIT:
                # Large centred cell titles use the lower border of their
                # containing SHA template cell as text-after-edge.  The PSM
                # bottom is a local glyph metric and sits above that border.
                baseline_y, baseline_mapping = template_text_after_edge(
                    anchor_x,
                    anchor_y,
                    float(bottom),
                    template_segments,
                )
                render_y = svg_y(baseline_y)
                mapping += f"-plus-{baseline_mapping}"
            mapping = str(record.get("mapping", mapping))
            font_weight = "400"
            glyph_x_scale = 1.0
            glyph_y_scale = 1.0
            if is_chengda_feature_strip_template_text(
                format_ref,
                float(record["x"]),
                float(record["y"]),
            ):
                glyph_y_scale = CHENGDA_FEATURE_STRIP_GLYPH_Y_SCALE
            elif format_ref == 0x140:
                glyph_y_scale = CHENGDA_FOOTER_LABEL_GLYPH_Y_SCALE
            elif revision_header:
                glyph_y_scale = CHENGDA_REVISION_HEADER_GLYPH_Y_SCALE
            elif legend_title:
                glyph_y_scale = CHENGDA_LEGEND_TITLE_GLYPH_Y_SCALE
            if project_doc_label:
                glyph_x_scale = CHENGDA_PROJECT_DOC_LABEL_GLYPH_X_SCALE
            glyph_transform_attr = (
                f'transform="translate({render_x:.1f} {render_y:.1f}) '
                f'scale({glyph_x_scale:.4f} {glyph_y_scale:.4f}) '
                f'translate({-render_x:.1f} {-render_y:.1f})" '
                if glyph_x_scale != 1.0 or glyph_y_scale != 1.0
                else ""
            )
            elements.append(
                f'<text x="{render_x:.1f}" '
                f'y="{render_y:.1f}" font-size="{height:.1f}" '
                f'{length_attr} '
                # The Shape2D anchor is the true baseline/alignment point.
                # PSM contributes glyph width only; its position may be in a
                # sibling/local coordinate space for template objects.
                f'{baseline_attr} '
                f'text-anchor="{anchor}" '
                f'font-family="{font_family}" '
                f'font-weight="{font_weight}" '
                f'{glyph_transform_attr}'
                f'{font_style_attr}'
                f'data-layer="sha-template-text" data-graphic="0x{int(record["graphic_ref"]):04X}" '
                f'data-style="0x{int(record.get("style_ref", 0)):04X}" '
                f'data-format="0x{int(record.get("format_ref", 0)):02X}" '
                f'data-mapping="{mapping}">'
                f'{html.escape(str(record["text"]))}</text>'
            )
        elements.append("</g>")
    if signature_records:
        elements.append('<g id="sha-revision-signatures">')
        for record in signature_records:
            signature_x = float(record["x"])
            signature_width = float(record["width"])
            signature_height = float(record["height"])
            signature_svg_y = svg_y(float(record["y"]) + signature_height)
            # Automatically extracted project assets are stored 180 degrees
            # from final plot orientation. A manually cleaned *-manual.png is
            # supplied upright and must be embedded without that transform.
            if bool(record.get("flip_180", True)):
                flip_translate_x = 2 * signature_x + signature_width
                flip_translate_y = 2 * signature_svg_y + signature_height
                transform = (
                    f' transform="translate({flip_translate_x:.1f} '
                    f'{flip_translate_y:.1f}) scale(-1 -1)"'
                )
            else:
                transform = ""
            elements.append(
                f'<image x="{signature_x:.1f}" '
                f'y="{signature_svg_y:.1f}" '
                f'width="{signature_width:.1f}" height="{signature_height:.1f}" '
                f'{transform} '
                f'href="{record["href"]}" data-layer="sha-revision-signature" '
                f'data-person="{html.escape(str(record["person"]))}" '
                f'data-field="{record["field"]}" '
                'data-mapping="sha-latest-revision-person-plus-dedicated-signature-row-and-project-asset"/>'
            )
        elements.append("</g>")
    if revision_text or multiline_text or notes or anchor_labels or unicode_labels:
        elements.append('<g id="sha-template-bound-text" fill="#17202a" font-family="Arial, Helvetica, sans-serif" text-rendering="geometricPrecision">')
        for record in revision_text:
            x = float(record["x"])
            y = svg_y(float(record["y"]))
            font_size = float(record["font_size"])
            family = css_font_family(str(record.get("font_family", "")), template_panel=True)
            owned_title = bound_title_geometry(record, template_segments) if template_owned else None
            baseline_attribute = 'dominant-baseline="central" '
            bound_anchor = 'middle'
            if owned_title:
                family = owned_title['family']
                x = owned_title['x']
                y = svg_y(owned_title['y'])
                bound_anchor = 'start'
                baseline_attribute = ''
                baseline_attribute += f'letter-spacing="{owned_title["letter_spacing"]:.5f}" '
            elements.append(
                f'<text x="{x:.1f}" y="{y:.1f}" font-size="{font_size:.1f}" text-anchor="{bound_anchor}" '
                f'{baseline_attribute}'
                f'font-family="{family}" '
                f'data-layer="sha-template-revision" data-field="{record["field"]}" '
                f'data-graphic="0x{int(record.get("graphic_ref", 0)):04X}" '
                f'data-stream="{html.escape(str(record.get("stream", "")))}" '
                'data-mapping="sha-tagged-text-plus-local-psm-cell">'
                f'{html.escape(str(record["text"]))}</text>'
            )
        for record in multiline_text:
            font_size = float(record["font_size"])
            family = template_css_font_family(
                int(record.get("format_ref", 0)),
                str(record.get("font_family", "")),
            )
            lines = str(record["text"]).splitlines()
            alignment = str(record.get("alignment", "start"))
            left, bottom, right, top = record["bbox"]
            row_height = (top - bottom) / max(1, len(lines))
            line_ys = [
                svg_y(top - (index + 0.5) * row_height)
                for index in range(len(lines))
            ]
            if alignment == "middle":
                x = (left + right) / 2
                y = svg_y((bottom + top) / 2)
                anchor_attribute = ' text-anchor="middle"'
                mapping = "sha-psm-cell-equal-rows-plus-centred-alignment"
            else:
                x = float(record["x"]) * SHEET_UNIT
                y = line_ys[0]
                anchor_attribute = ""
                mapping = "sha-psm-cell-equal-rows-plus-source-x-alignment"
            tspans = "".join(
                f'<tspan x="{x:.1f}" y="{line_y:.1f}" dominant-baseline="central">'
                f'{html.escape(line)}</tspan>'
                for line, line_y in zip(lines, line_ys)
            )
            elements.append(
                f'<text x="{x:.1f}" y="{y:.1f}" font-size="{font_size:.1f}" '
                f'font-family="{family}" font-weight="400"{anchor_attribute} '
                'data-layer="sha-template-multiline" '
                f'data-graphic="0x{int(record["graphic_ref"]):04X}" '
                f'data-format="0x{int(record["format_ref"]):02X}" '
                f'data-mapping="{mapping}">{tspans}</text>'
            )
        for record in notes:
            x, y = float(record["x"]) * SHEET_UNIT, svg_y(float(record["y"]) * SHEET_UNIT)
            font_size = float(record.get("font_size", 40))
            elements.append(
                f'<text x="{x:.1f}" y="{y:.1f}" font-size="{font_size:.1f}" '
                f'data-format="0x{int(record.get("format_ref", 0)):02X}" '
                'data-layer="sha-template-note" data-mapping="sha-text-anchor-plus-stylecluster-size">'
                f'{html.escape(str(record["text"]))}</text>'
            )
        for record in anchor_labels:
            x = float(record["x"]) * SHEET_UNIT
            bbox = record.get("psm_bbox")
            if bbox is not None:
                left, bottom, right, top = bbox
                if record.get("baseline_from_psm"):
                    y = svg_y(bottom)
                    baseline = ' dominant-baseline="text-after-edge"'
                elif record.get("physical_page_baseline"):
                    y = view_y + view_height - float(record["y"]) * SHEET_UNIT
                    baseline = ""
                else:
                    y = svg_y(float(record["y"]) * SHEET_UNIT)
                    baseline = ""
                font_size = float(record.get("font_size", top - bottom))
                text_length = right - left
                metric_attributes = (
                    f' font-size="{font_size:.1f}" textLength="{text_length:.1f}" '
                    'lengthAdjust="spacingAndGlyphs" '
                    f'{baseline} '
                    f'data-psm-graphic="0x{int(record["psm_graphic_ref"]):04X}" '
                    'data-mapping="sha-text-alignment-plus-style-size-and-sibling-psm-width"'
                )
            else:
                y = svg_y(float(record["y"]) * SHEET_UNIT)
                metric_attributes = (
                    f' font-size="{float(record["font_size"]):.1f}" '
                    'data-mapping="sha-stylecluster-font-ratio"'
                )
            elements.append(
                f'<text x="{x:.1f}" y="{y:.1f}"{metric_attributes} '
                f'font-family="{record["font_family"]}" text-anchor="{record["anchor"]}" '
                'data-layer="sha-template-anchor-label">'
                f'{html.escape(str(record["text"]))}</text>'
            )
        for record in unicode_labels:
            bbox = record["bbox"]
            if bbox is not None:
                left, bottom, right, top = bbox
                # This template title follows the same local-x PSM offset
                # observed in BOM text. Its Sheet anchor is the true text
                # insertion x; retain the PSM envelope for y, size and width.
                x, y = float(record["x"]) * SHEET_UNIT, svg_y(bottom)
                font_size, text_length = top - bottom, right - left
                baseline = ' dominant-baseline="text-after-edge"'
                mapping = "sha-text-anchor-x-plus-psm-envelope"
            else:
                x, y = float(record["x"]) * SHEET_UNIT, svg_y(float(record["y"]) * SHEET_UNIT)
                font_size, text_length = float(record["font_size"]), None
                baseline = ""
                mapping = "sha-text-anchor-fallback"
            length_attr = f' textLength="{text_length}" lengthAdjust="spacingAndGlyphs"' if text_length else ""
            elements.append(
                f'<text x="{x:.1f}" y="{y:.1f}" font-size="{font_size:.1f}"{length_attr}{baseline} '
                f'font-family="{html.escape(str(record["font_family"]), quote=True)}" '
                f'data-style="{record["style_ref"]}" '
                f'data-layer="sha-template-unicode-label" data-mapping="{mapping}">'
                f'{html.escape(str(record["text"]))}</text>'
            )
        elements.append("</g>")
    if debug_boxes:
        elements.append('<g fill="none" stroke="#bf6b00" stroke-width="12" stroke-opacity="0.62">')
        for ref in sorted(graphics):
            bbox = psm_bbox(psm, ref)
            if bbox is None:
                continue
            left, bottom, right, top = bbox
            elements.append(
                f'<rect x="{left}" y="{PAGE_HEIGHT - top}" '
                f'width="{right - left}" height="{top - bottom}" data-graphic="0x{ref:04X}"/>'
            )
        elements.append("</g>")
    # Sheet6 uses both its ordinary two-point record and the 18/32 record
    # family.  The latter carries much of the detailed component geometry
    # (flanges, valves and instrument symbols), not only template graphics.
    ordinary_segments = line_segments(sheet)
    alternate_segments = template_line_segments(sheet)
    # ``0x7B`` child entries carry bounding boxes, not visible line endpoints.
    # Drawing their lower-left-to-upper-right diagonals created the stray
    # arrows and short lines seen around otherwise empty ISO regions. Actual
    # child primitives are already decoded by the ordinary/alternate layers.
    composite_inventory = composite_segments(sheet, set(graphics))
    composite: list[tuple[float, float, float, float, int, int]] = []
    segments = unique_line_segments(ordinary_segments + alternate_segments)
    alternate_object_groups = template_line_object_groups(sheet)
    style_width_by_ref = line_style_widths(style_data)
    sheet_line_width_by_child = sheet_line_widths(sheet, style_data)
    sheet_line_width_by_child.update(template_line_widths(sheet, style_data))
    rectangles = sheet_rectangles(segments)
    revision_triangle_records = sheet_revision_triangle_records(segments)
    revision_embedded_labels = sheet_revision_embedded_labels(
        sheet, revision_triangle_records
    )
    revision_triangle_child_refs = {
        int(child_ref)
        for triangle in revision_triangle_records
        for child_ref in triangle["child_refs"]
    }
    rectangles_by_ref = rectangles_by_parent_ref(segments)
    rectangles_by_text_ref = rectangles_by_text_style_sequence(segments)
    composite_frames_by_text_ref = rectangles_by_text_style_sequence(composite)
    # The 18/32 record family can carry an offset backing rectangle for a
    # callout whose actual visible frame is already present in type-5
    # composite children.  Both share ``text_ref + 5`` but only the composite
    # sequence aligns to the direct Sheet text/PSM evidence.  Suppress those
    # exact duplicate 18/32 frame edges, while retaining every other 18/32
    # component/detail segment.
    alternate_rectangle_parents = rectangles_by_parent_ref(alternate_segments)
    duplicate_alternate_frame_edges = {
        (parent_ref, child_ref)
        for _, _, _, _, parent_ref, child_ref in alternate_segments
        if parent_ref in alternate_rectangle_parents
        and parent_ref - 5 in composite_frames_by_text_ref
    }
    fallback_styles = merged_style_fallbacks(sheets, psm)
    fallback_formats = merged_format_fallbacks(sheets, psm)
    local_bounded_styles = bounded_style_fallbacks(sheet, psm)
    sheet_circles = template_circle_primitives(sheet)
    sheet_circle_child_refs = {int(circle["child_ref"]) for circle in sheet_circles}
    instrument_tags = instrument_tag_records(sheet, sheet_circles, style_data)
    instrument_tag_keys = {
        (int(record["graphic_ref"]), str(record["text"]).strip())
        for record in instrument_tags
    }
    rendered_revision_keys: set[tuple[int, ...]] = set()
    # The direct 0x59 record owns the plotted centre and radius.  PSM is only
    # an envelope and can be absent or expressed in a sibling coordinate
    # space, so do not render a second PSM ellipse for the same primitive.
    ellipses = [
        ellipse
        for ellipse in psm_ellipses(sheet, psm)
        if int(ellipse[0]) not in sheet_circle_child_refs
    ]
    ellipse_anchor_by_ref = ellipse_anchors(sheet)
    ellipse_primitive_anchor_by_ref = ellipse_primitive_anchors(sheet)
    ellipse_text_offsets: list[tuple[tuple[int, int, int, int], float, float]] = []
    for ref, (left, bottom, right, top) in ellipses:
        anchor = ellipse_primitive_anchor_by_ref.get(ref)
        if anchor is not None:
            ellipse_text_offsets.append((
                (left, bottom, right, top),
                anchor[0] - (left + right) / 2,
                anchor[1] - (bottom + top) / 2,
            ))
    direct_arc_candidates = standalone_arcs(sheet)
    direct_arcs = [
        arc for arc in direct_arc_candidates if standalone_arc_is_connected(arc, segments)
    ]
    revision_cloud_groups = revision_cloud_arc_groups(direct_arc_candidates)
    revision_cloud_arcs = [arc for group in revision_cloud_groups for arc in group]
    sheet_text_records = text_records(sheet)
    cut_pipe_text_offsets, suppressed_cut_pipe_text = cut_pipe_table_text_adjustments(
        sheet_text_records,
        psm,
        pcf_path,
    )
    candidate_cloud_text_offsets = revision_cloud_text_offsets(
        sheet_text_records, revision_cloud_groups, psm
    )
    direct_connection_cloud_offsets = revision_cloud_connection_text_offsets(
        sheet_text_records,
        revision_cloud_groups,
        psm,
    )
    candidate_compass_text_offsets = compass_collision_connection_text_offsets(
        sheet_text_records,
        psm,
        template_circles,
    )
    line_arrowheads = filled_line_arrowheads(sheet, segments)
    cloud_leader_endpoint_offsets = revision_cloud_leader_endpoint_offsets(
        sheet_text_records,
        candidate_cloud_text_offsets,
        line_arrowheads,
        segments,
        psm,
    )
    compass_leader_endpoint_offsets = plain_connection_leader_endpoint_offsets(
        sheet_text_records,
        candidate_compass_text_offsets,
        segments,
        psm,
    )
    # A nearby cloud is not sufficient ownership evidence: many 201600 pages
    # contain small revision clouds whose x-span happens to intersect an
    # unrelated connection note.  Keep an inferred text translation only
    # when the same source group also has a decoded leader whose note endpoint
    # moves by that exact translation.
    leader_confirmed_offsets = {
        (float(dx), float(dy))
        for _, dx, dy in cloud_leader_endpoint_offsets.values()
    }
    cloud_text_offsets = {
        key: offset
        for key, offset in candidate_cloud_text_offsets.items()
        if offset in leader_confirmed_offsets
    }
    cloud_text_offsets.update(direct_connection_cloud_offsets)
    compass_confirmed_offsets = {
        (float(dx), float(dy))
        for _, dx, dy in compass_leader_endpoint_offsets.values()
    }
    cloud_text_offsets.update(
        {
            key: offset
            for key, offset in candidate_compass_text_offsets.items()
            if offset in compass_confirmed_offsets
        }
    )
    cloud_leader_endpoint_offsets = {
        child_ref: adjustment
        for child_ref, adjustment in cloud_leader_endpoint_offsets.items()
        if (float(adjustment[1]), float(adjustment[2])) in leader_confirmed_offsets
        and (float(adjustment[1]), float(adjustment[2]))
        in set(cloud_text_offsets.values())
    }
    cloud_leader_endpoint_offsets.update(
        {
            child_ref: adjustment
            for child_ref, adjustment in compass_leader_endpoint_offsets.items()
            if (float(adjustment[1]), float(adjustment[2]))
            in compass_confirmed_offsets
            and (float(adjustment[1]), float(adjustment[2]))
            in set(cloud_text_offsets.values())
        }
    )
    cloud_leader_owner_refs = {
        int(owner_ref)
        for *_, owner_ref, child_ref in segments
        if int(child_ref) in cloud_leader_endpoint_offsets
    }
    vector_refs = (
        {ref for *_, ref, child_ref in segments}
        | {child_ref for *_, ref, child_ref in segments}
        | {int(arc["owner_ref"]) for arc in direct_arcs}
        | {int(arc["primitive_ref"]) for arc in direct_arcs}
    )
    def is_visible_connection_point(region: dict[str, object]) -> bool:
        """Reject tiny PSM/UCI records that are not attached to visible geometry.

        Shape2D also stores small non-drawing records in the UCI/PSM layers.
        A visible weld/connection dot must be local to a decoded pipe or
        component segment; otherwise it is not emitted as ISO geometry.
        """

        if not segments:
            return False
        left, bottom, right, top = region["bbox"]
        ref = int(region["graphic_ref"])
        center_x, center_y = ellipse_anchor_by_ref.get(
            ref, ((left + right) / 2, (bottom + top) / 2)
        )
        return min(
            point_segment_distance(
                center_x,
                center_y,
                x1 * SHEET_UNIT,
                y1 * SHEET_UNIT,
                x2 * SHEET_UNIT,
                y2 * SHEET_UNIT,
            )
            for x1, y1, x2, y2, _, _ in segments
        ) <= 80

    connection_points = [
        region
        for region in uci_regions
        if int(region["graphic_ref"]) not in vector_refs
        and (region["bbox"][2] - region["bbox"][0]) <= 45
        and (region["bbox"][3] - region["bbox"][1]) <= 45
        and is_visible_connection_point(region)
    ]
    # Type 6 in a 0x7B composite is a container/inventory classification, not
    # sufficient evidence that the visible child primitive is a circular arc.
    # In particular, crossing-casing symbols store their frame and hatch as
    # ordinary two-point 0x18/0x32 records beneath type-6 composite children.
    # Replacing those explicit endpoints with a half ellipse turns every hatch
    # line into the incorrect fan-shaped geometry.  Retain the composite
    # bounds for diagnostics, but render decoded two-point records as lines;
    # true arcs are emitted below only from the independently decoded
    # centre/radius/angle 0x61/0x3B record family.
    arcs = composite_arcs(sheet)
    manifest_segments: list[dict[str, object]] = []
    manifest_arcs: list[dict[str, object]] = []
    injected_leader_child_refs, injected_marker_child_refs = injected_weld_geometry_child_refs(sheet)
    injected_marker_circles = injected_weld_marker_circles(sheet)
    elements.append('<g fill="none" stroke="#17202a" stroke-width="8" stroke-linecap="square" shape-rendering="geometricPrecision">')
    for x1, y1, x2, y2, ref, child_ref in segments:
        if (ref, child_ref) in duplicate_alternate_frame_edges:
            continue
        # The three child lines are the uncomposed revision-symbol geometry.
        # Emit the normalized compound marker once below instead of plotting
        # its source-local rotation as three independent strokes.
        if child_ref in revision_triangle_child_refs:
            continue
        # The SHA copy retains sixteen boundary segments for structural
        # verification. The PDF/SVG renderer replaces that polygonal
        # approximation with one mathematically circular primitive below.
        if child_ref in injected_marker_child_refs:
            continue
        page_x1, page_y1 = x1 * SHEET_UNIT, y1 * SHEET_UNIT
        page_x2, page_y2 = x2 * SHEET_UNIT, y2 * SHEET_UNIT
        leader_adjustment = cloud_leader_endpoint_offsets.get(int(child_ref))
        if leader_adjustment is not None:
            endpoint_index, leader_dx, leader_dy = leader_adjustment
            if endpoint_index == 0:
                page_x1, page_y1 = page_x1 + leader_dx, page_y1 + leader_dy
            else:
                page_x2, page_y2 = page_x2 + leader_dx, page_y2 + leader_dy
        exact_ucis = set(uci_by_object_ref.get(ref, [])) | set(graphics.get(child_ref, []))
        spatial_ucis = {
            str(region["uci"])
            for region in uci_regions
            if intersects_bbox(page_x1, page_y1, page_x2, page_y2, region["bbox"])
        }
        ucis = sorted(exact_ucis | spatial_ucis)
        uci = ",".join(ucis)
        basis = "direct_object_ref" if exact_ucis else "psm_bbox_spatial" if spatial_ucis else "sheet_decoration"
        manifest_segments.append(
            {
                "graphic_ref_low16": f"0x{ref:04X}",
                "child_primitive_ref": f"0x{child_ref:08X}",
                "sheet_points_normalized": [[x1, y1], [x2, y2]],
                "revision_cloud_note_endpoint_adjustment_page_units": (
                    [leader_adjustment[0], leader_adjustment[1], leader_adjustment[2]]
                    if leader_adjustment is not None
                    else None
                ),
                "uci_candidates": ucis,
                "mapping_basis": basis,
                "stroke_width_page_units": round(sheet_line_width_by_child.get(child_ref, 8.0), 4),
                "local_object_group": (
                    f"0x{alternate_object_groups[ref]:04X}"
                    if ref in alternate_object_groups
                    else None
                ),
            }
        )
        semantic = f' data-uci="{html.escape(uci)}"' if uci else ' data-layer="sheet-decoration"'
        injected_style = ""
        if child_ref in injected_leader_child_refs:
            semantic = ' data-layer="sha-injected-weld-leader"'
            injected_style = f' stroke="{WELD_CALLOUT_COLOR}"'
        elif child_ref in injected_marker_child_refs:
            semantic = ' data-layer="sha-injected-weld-marker" data-marker-shape="circle"'
            injected_style = f' stroke="{WELD_CALLOUT_COLOR}"'
        object_group = (
            f' data-local-group="0x{alternate_object_groups[ref]:04X}"'
            if ref in alternate_object_groups
            else ""
        )
        width = (
            WELD_CALLOUT_STROKE_WIDTH
            if child_ref in injected_leader_child_refs or child_ref in injected_marker_child_refs
            else sheet_line_width_by_child.get(child_ref)
        )
        width_attr = f' stroke-width="{width:.4f}"' if width is not None else ""
        if child_ref in injected_leader_child_refs:
            leader_length = math.hypot(page_x2 - page_x1, page_y2 - page_y1)
            if leader_length > WELD_CALLOUT_HALO_ROOT_GAP:
                halo_fraction = WELD_CALLOUT_HALO_ROOT_GAP / leader_length
                halo_x1 = page_x1 + (page_x2 - page_x1) * halo_fraction
                halo_y1 = page_y1 + (page_y2 - page_y1) * halo_fraction
                elements.append(
                    f'<line x1="{halo_x1:.1f}" y1="{svg_y(halo_y1):.1f}" '
                    f'x2="{page_x2:.1f}" y2="{svg_y(page_y2):.1f}" '
                    f'data-layer="sha-injected-weld-leader-halo" '
                    f'stroke="#ffffff" stroke-width="{WELD_CALLOUT_HALO_WIDTH:.4f}" '
                    'stroke-linecap="round"/>'
                )
        elements.append(
            f'<line x1="{page_x1:.1f}" y1="{svg_y(page_y1):.1f}" '
            f'x2="{page_x2:.1f}" y2="{svg_y(page_y2):.1f}" '
            f'data-graphic="0x{ref:04X}" data-child="0x{child_ref:08X}"{width_attr}{semantic}{object_group}{injected_style}/>'
        )
    for arrowhead in line_arrowheads:
        if int(arrowhead["owner_ref"]) not in cloud_leader_owner_refs:
            continue
        points = " ".join(
            f"{float(x):.1f},{svg_y(float(y)):.1f}"
            for x, y in arrowhead["points"]
        )
        elements.append(
            f'<polygon points="{points}" fill="#17202a" stroke="none" '
            f'data-layer="sha-filled-line-arrowhead" '
            f'data-owner="0x{int(arrowhead["owner_ref"]):04X}" '
            f'data-child="0x{int(arrowhead["child_ref"]):08X}" '
            'data-mapping="sha-closed-filled-contour-owned-by-line"/>'
        )
    revision_triangle_parent_counts = Counter(
        int(triangle["parent_ref"]) for triangle in revision_triangle_records
    )
    for triangle in revision_triangle_records:
        parent_ref = int(triangle["parent_ref"])
        child_refs = tuple(int(ref) for ref in triangle["child_refs"])
        points = tuple(triangle["points"])
        source_widths = [
            sheet_line_width_by_child[child_ref]
            for child_ref in child_refs
            if child_ref in sheet_line_width_by_child
        ]
        stroke_width = sum(source_widths) / len(source_widths) if source_widths else 8.0
        svg_points = " ".join(
            f"{float(x):.1f},{svg_y(float(y)):.1f}" for x, y in points
        )
        elements.append(
            f'<polygon points="{svg_points}" fill="none" stroke="#17202a" '
            f'stroke-width="{stroke_width:.4f}" stroke-linejoin="miter" '
            f'data-layer="sha-revision-triangle" data-graphic="0x{parent_ref:04X}" '
            'data-mapping="sha-revision-symbol-normalized-from-centroid-and-side-length"/>'
        )
    for arc in direct_arcs:
        primitive_ref = int(arc["primitive_ref"])
        owner_ref = int(arc["owner_ref"])
        center_x = float(arc["center_x"])
        center_y = float(arc["center_y"])
        radius = float(arc["radius"])
        start_angle = float(arc["start_angle"])
        end_angle = float(arc["end_angle"])
        direction = int(arc["direction"])
        start_x, start_y, end_x, end_y, large_arc, svg_sweep = standalone_arc_geometry(arc)
        exact_ucis = set(uci_by_object_ref.get(owner_ref, [])) | set(graphics.get(primitive_ref, []))
        spatial_ucis = {
            str(region["uci"])
            for region in uci_regions
            if intersects_bbox(
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
                region["bbox"],
            )
        }
        ucis = sorted(exact_ucis | spatial_ucis)
        uci = ",".join(ucis)
        basis = "direct_object_ref" if exact_ucis else "psm_bbox_spatial" if spatial_ucis else "sheet_decoration"
        semantic = f' data-uci="{html.escape(uci)}"' if uci else ' data-layer="sheet-decoration"'
        width = style_width_by_ref.get(int(arc["style_ref"]))
        width_attr = f' stroke-width="{width:.4f}"' if width is not None else ""
        local_group_attr = f' data-local-group="0x{int(arc["local_object_group"]):04X}"'
        elements.append(
            f'<path d="M {start_x:.1f} {svg_y(start_y):.1f} A {radius:.1f} {radius:.1f} '
            f'0 {large_arc} {svg_sweep} {end_x:.1f} {svg_y(end_y):.1f}" '
            f'data-graphic="0x{owner_ref:04X}" data-child="0x{primitive_ref:08X}"'
            f'{width_attr}{semantic}{local_group_attr} data-mapping="sha-61-3b-center-radius-angles"/>'
        )
        manifest_arcs.append(
            {
                "graphic_ref_low16": f"0x{owner_ref:04X}",
                "child_primitive_ref": f"0x{primitive_ref:08X}",
                "local_object_group": f"0x{int(arc['local_object_group']):04X}",
                "style_ref": f"0x{int(arc['style_ref']):04X}",
                "center_page_units": [center_x, center_y],
                "radius_page_units": radius,
                "angles_radians": [start_angle, end_angle],
                "direction": direction,
                "endpoints_page_units": [[start_x, start_y], [end_x, end_y]],
                "svg_flags": {"large_arc": large_arc, "sweep": svg_sweep},
                "uci_candidates": ucis,
                "mapping_basis": basis,
                "stroke_width_page_units": round(width if width is not None else 8.0, 4),
            }
        )
    for arc in revision_cloud_arcs:
        primitive_ref = int(arc["primitive_ref"])
        owner_ref = int(arc["owner_ref"])
        center_x = float(arc["center_x"])
        center_y = float(arc["center_y"])
        radius = float(arc["radius"])
        start_x, start_y, end_x, end_y, large_arc, svg_sweep = standalone_arc_geometry(arc)
        exact_ucis = set(uci_by_object_ref.get(owner_ref, [])) | set(
            graphics.get(primitive_ref, [])
        )
        spatial_ucis = {
            str(region["uci"])
            for region in uci_regions
            if intersects_bbox(
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
                region["bbox"],
            )
        }
        ucis = sorted(exact_ucis | spatial_ucis)
        semantic = (
            f' data-uci="{html.escape(",".join(ucis))}"' if ucis else ""
        )
        # Chengda's revision-cloud plot style uses the standard 0.25 mm pen.
        # Its Shape2D style inherits the ordinary 0.35 mm line family, while
        # the reference PDF applies this semantic plot override.
        width = 0.00025 * SHEET_UNIT
        width_attr = f' stroke-width="{width:.4f}"'
        elements.append(
            f'<path d="M {start_x:.1f} {svg_y(start_y):.1f} A {radius:.1f} {radius:.1f} '
            f'0 {large_arc} {svg_sweep} {end_x:.1f} {svg_y(end_y):.1f}" '
            f'data-graphic="0x{owner_ref:04X}" data-child="0x{primitive_ref:08X}"'
            f'{width_attr}{semantic} data-layer="sha-revision-cloud" '
            f'data-owner="0x{owner_ref:04X}" '
            f'data-local-group="0x{int(arc["local_object_group"]):04X}" '
            'data-mapping="sha-closed-multi-arc-revision-cloud"/>'
        )
        manifest_arcs.append(
            {
                "primitive_ref": f"0x{primitive_ref:08X}",
                "owner_ref": f"0x{owner_ref:08X}",
                "center_page_units": [center_x, center_y],
                "radius_page_units": radius,
                "start_angle_radians": float(arc["start_angle"]),
                "end_angle_radians": float(arc["end_angle"]),
                "direction": int(arc["direction"]),
                "uci_candidates": ucis,
                "mapping_basis": "sha_closed_multi_arc_revision_cloud",
            }
        )
    elements.append("</g>")
    if injected_marker_circles:
        elements.append(
            f'<g id="sha-injected-weld-markers" fill="none" '
            f'stroke="{WELD_CALLOUT_COLOR}" stroke-width="{WELD_CALLOUT_STROKE_WIDTH:.4f}" '
            'shape-rendering="geometricPrecision">'
        )
        for label_ref, center_x, center_y, radius in injected_marker_circles:
            elements.append(
                f'<circle cx="{center_x:.1f}" cy="{svg_y(center_y):.1f}" r="{radius:.1f}" '
                f'data-layer="sha-injected-weld-marker" data-marker-shape="circle" '
                f'data-label-ref="0x{label_ref:08X}" stroke="{WELD_CALLOUT_COLOR}"/>'
            )
        elements.append("</g>")
    if sheet_circles:
        elements.append('<g id="sha-sheet-circles" stroke="#17202a" shape-rendering="geometricPrecision">')
        for circle in sheet_circles:
            child_ref = int(circle["child_ref"])
            owner_ref = int(circle["owner_ref"])
            center_x = float(circle["center_x"])
            center_y = float(circle["center_y"])
            radius = float(circle["radius"])
            style_ref = int(circle["style_ref"])
            if radius <= 30.0:
                layer = "sha-weld-point"
                fill = "#17202a"
                stroke_width = 0.0
            else:
                layer = "sha-instrument-bubble"
                fill = "none"
                stroke_width = template_width_by_style.get(style_ref, 8.0)
            elements.append(
                f'<circle cx="{center_x:.1f}" cy="{svg_y(center_y):.1f}" r="{radius:.1f}" '
                f'fill="{fill}" stroke-width="{stroke_width:.4f}" '
                f'data-layer="{layer}" data-graphic="0x{owner_ref:04X}" '
                f'data-child="0x{child_ref:08X}" data-style="0x{style_ref:04X}" '
                'data-mapping="sha-59-circle-center-plus-radius"/>'
            )
        elements.append("</g>")
    if instrument_tags:
        elements.append(
            '<g id="sha-instrument-tags" fill="#17202a" stroke="none" '
            'font-family="\'Arial Narrow\', Arial, sans-serif" '
            'text-rendering="geometricPrecision">'
        )
        for record in instrument_tags:
            circle = record["circle"]
            center_x = float(circle["center_x"])
            y = float(record["y_page"])
            elements.append(
                f'<text x="{center_x:.1f}" y="{svg_y(y):.1f}" '
                f'font-size="{float(record["font_size"]):.4f}" '
                'text-anchor="middle" dominant-baseline="central" '
                f'data-layer="sha-instrument-tag" '
                f'data-graphic="0x{int(record["graphic_ref"]):04X}" '
                f'data-bubble="0x{int(circle["child_ref"]):08X}" '
                'data-mapping="sha-text-anchor-inside-direct-instrument-circle">'
                f'{html.escape(str(record["text"]).strip())}</text>'
            )
        elements.append("</g>")
    if connection_points:
        elements.append('<g id="sha-connection-points" fill="#17202a" stroke="none">')
        for region in connection_points:
            left, bottom, right, top = region["bbox"]
            ref = int(region["graphic_ref"])
            center_x, center_y = ellipse_anchor_by_ref.get(
                ref, ((left + right) / 2, (bottom + top) / 2)
            )
            elements.append(
                f'<circle cx="{center_x:.1f}" cy="{PAGE_HEIGHT - center_y:.1f}" '
                f'r="{min(right - left, top - bottom) / 2:.1f}" '
                f'data-layer="sha-connection-point" data-uci="{html.escape(str(region["uci"]))}" '
                f'data-mapping="{"sha-ellipse-anchor" if ref in ellipse_anchor_by_ref else "sha-psm-micro-uci"}"/>'
            )
        elements.append("</g>")
    if ellipses:
        elements.append('<g id="sha-ellipse-geometry" fill="none" stroke="#17202a" stroke-width="8">')
        for ref, (left, bottom, right, top) in ellipses:
            center_x, center_y = ellipse_primitive_anchor_by_ref.get(
                ref, ((left + right) / 2, (bottom + top) / 2)
            )
            elements.append(
                f'<ellipse cx="{center_x:.1f}" cy="{PAGE_HEIGHT - center_y:.1f}" '
                f'rx="{(right - left) / 2:.1f}" ry="{(top - bottom) / 2:.1f}" '
                f'data-layer="sha-ellipse" data-graphic="0x{ref:04X}" '
                f'data-mapping="{"sha-primitive-anchor-plus-psm-size" if ref in ellipse_primitive_anchor_by_ref else "sha-psm-envelope"}"/>'
            )
        elements.append("</g>")
    # StyleCluster names the engineering-annotation family as Courier New.
    # A generic ``monospace`` maps to a browser-dependent substitute and
    # changes the measured aspect ratio of dimensions and callout labels.
    elements.append('<g fill="#17202a" font-family="\'Courier New\', Courier, monospace" text-rendering="geometricPrecision">')
    emitted_text: set[tuple[int, str, float, float]] = set()
    manifest_text: list[dict[str, object]] = []
    inferred_marker_boxes = 0
    injected_marker_center_by_ref = injected_weld_marker_centers(sheet)
    preferred_text_by_identity: dict[tuple[object, ...], str] = {}
    for candidate in sheet_text_records if project_profile == "100200" else []:
        candidate_text = str(candidate["text"]).strip()
        identity = (
            int(candidate["graphic_ref"]),
            int(candidate["style_ref"]),
            float(candidate["x"]),
            float(candidate["y"]),
            float(candidate["direction_x"]),
            float(candidate["direction_y"]),
        )
        previous = preferred_text_by_identity.get(identity, "")
        if not previous or (
            len(candidate_text) > len(previous)
            and candidate_text.startswith(previous)
        ):
            preferred_text_by_identity[identity] = candidate_text
    for obj in sheet_text_records:
        text = str(obj["text"]).strip()
        ref = int(obj["graphic_ref"])
        source_text_key = (ref, int(obj.get("text_start", -1)))
        if source_text_key in suppressed_cut_pipe_text:
            continue
        identity = (
            ref,
            int(obj["style_ref"]),
            float(obj["x"]),
            float(obj["y"]),
            float(obj["direction_x"]),
            float(obj["direction_y"]),
        )
        if project_profile == "100200" and text != preferred_text_by_identity.get(identity, text):
            continue
        # A zero graphic reference is a binary scanner false positive rather
        # than a drawable SHA text object.  The same scanner false-positive
        # run can occasionally produce the literal ``a;`` with coincidentally
        # plausible coordinates and a non-zero reference.  ``a;`` is not a
        # drawable ISO label in the Chengda sources, so reject both signatures
        # before a random binary record becomes visible page text.
        if project_profile == "100200" and (ref == 0 or text == "a;"):
            continue
        injected_label = is_injected_weld_label_ref(ref)
        if (ref, text) in instrument_tag_keys:
            continue
        emitted_identity = (
            ref,
            text,
            round(float(obj["x"]), 9),
            round(float(obj["y"]), 9),
        )
        if not text or len(text) > 240 or emitted_identity in emitted_text:
            continue
        # Text records are scanned from a binary Sheet stream.  A few object
        # headers can resemble very short text (for example ``{f``) and point
        # at a page-sized PSM container.  They are not visible ISO labels.
        # Imperial support/component labels legitimately include inch marks,
        # for example ``SD010 1/2\"``.  Keep the filter for binary false
        # positives but accept printable ISO characters, including ``=`` in
        # multiline BOM descriptions such as ``L = 100 MM``.
        if not re.fullmatch(
            r"(?:[A-Za-z0-9 +./()_:%*=,;\\\"'&°-]+|<\d+>)", text
        ):
            continue
        emitted_text.add(emitted_identity)
        direction_x, direction_y = float(obj["direction_x"]), float(obj["direction_y"])
        direction_length = math.hypot(direction_x, direction_y)
        if not 0.8 <= direction_length <= 1.2:
            continue
        rotated_for_key = abs(direction_y) > 0.1 or abs(direction_x - 1) > 0.1
        style_ref = int(obj["style_ref"])
        format_ref = int(obj.get("format_ref", 0))
        declared_metric = format_metrics.get(format_ref)
        raw_anchor_x = float(obj["x"]) * SHEET_UNIT
        raw_anchor_y = float(obj["y"]) * SHEET_UNIT
        revision_triangle = next(
            (
                triangle
                for triangle in revision_triangle_records
                if float(triangle["source_bbox"][0]) - 12 <= raw_anchor_x <= float(triangle["source_bbox"][2]) + 12
                and float(triangle["source_bbox"][1]) - 12 <= raw_anchor_y <= float(triangle["source_bbox"][3]) + 12
                and re.fullmatch(r"\d+[A-Z]?", text)
            ),
            None,
        )
        declared_font_height = declared_metric[1] * SHEET_UNIT if declared_metric is not None else None
        bbox = (
            None
            if injected_label
            else select_text_psm_bbox(
                psm,
                ref,
                text,
                raw_anchor_x,
                raw_anchor_y,
                declared_font_height,
                rotated=rotated_for_key,
            )
        )
        rejected_psm_container = False
        if bbox is not None and declared_metric is not None:
            if not psm_text_extent_is_local(
                text,
                bbox,
                float(declared_font_height),
                rotated=rotated_for_key,
            ):
                bbox = None
                rejected_psm_container = True
        synthesized_psm_fallback = bbox is None
        if bbox is None and revision_triangle is not None:
            center_x, center_y = revision_triangle["center"]
            triangle_side = float(revision_triangle["side"])
            triangle_height = float(revision_triangle["height"])
            glyph_height = triangle_height * 0.50
            glyph_width = min(
                triangle_side * 0.72,
                glyph_height * 0.72 * len(text),
            )
            bbox = (
                float(center_x) - glyph_width / 2,
                float(center_y) - glyph_height / 2,
                float(center_x) + glyph_width / 2,
                float(center_y) + glyph_height / 2,
            )
        if bbox is None:
            # A style id can span several unrelated text-run formats. Prefer
            # exact format/orientation peers, then the run's StyleCluster
            # declaration. Only records without a decoded format may use the
            # older style-only fallback.
            fallback = fallback_formats.get((style_ref, format_ref, rotated_for_key))
            if fallback is None and declared_metric is not None:
                _, ratio = declared_metric
                font_height = ratio * SHEET_UNIT
                family = css_font_family(declared_metric[0])
                advance_ratio = 0.60 if "Courier New" in family else 0.69
                fallback = (0.0, -font_height, font_height, font_height * advance_ratio)
            if fallback is None and format_ref == 0:
                fallback = fallback_styles.get(style_ref)
            if fallback is None or not re.fullmatch(
                r"(?:[A-Za-z0-9 +./()_:%*=,;\\\"'&°-]+|<\d+>)", text
            ):
                continue
            dx, dy, font_height, char_width = fallback
            left = float(obj["x"]) * SHEET_UNIT + dx
            bottom = float(obj["y"]) * SHEET_UNIT + dy
            height = max(36, font_height)
            width = max(height * 0.7, char_width * len(text))
            right = left + width
            top = bottom + height
            bbox = (round(left), round(bottom), round(right), round(top))
        else:
            left, bottom, right, top = bbox
            height = max(42, top - bottom)
            width = right - left
        anchor_x, anchor_y = float(obj["x"]) * SHEET_UNIT, float(obj["y"]) * SHEET_UNIT
        cloud_text_adjustment = cloud_text_offsets.get(
            (ref, int(obj.get("text_start", -1)))
        )
        if cloud_text_adjustment is not None:
            cloud_dx, cloud_dy = cloud_text_adjustment
            left, right = left + cloud_dx, right + cloud_dx
            bottom, top = bottom + cloud_dy, top + cloud_dy
            anchor_x, anchor_y = anchor_x + cloud_dx, anchor_y + cloud_dy
        cut_pipe_text_adjustment = cut_pipe_text_offsets.get(source_text_key)
        if cut_pipe_text_adjustment is not None:
            cut_dx, cut_dy = cut_pipe_text_adjustment
            left, right = left + cut_dx, right + cut_dx
            bottom, top = bottom + cut_dy, top + cut_dy
            anchor_x, anchor_y = anchor_x + cut_dx, anchor_y + cut_dy
        # The physical A1 template occupies the SHA page's right-hand panel.
        # Its direct Sheet records form a separate style cluster from the
        # Courier New ISO body: for example RHO uses 0x95C4/0x9608 in the title
        # block and BOM, versus 0x9609 on the pipe drawing. Use the SHA anchor
        # region to retain that family even though the numeric style ids vary
        # between pages.
        in_template_panel = anchor_x >= SHEET_UNIT * 0.55
        feature_strip_value = is_chengda_feature_strip_value(
            style_ref,
            format_ref,
            float(obj["x"]),
            float(obj["y"]),
        )
        format_metric = format_metrics.get(int(obj.get("format_ref", 0)))
        source_family = format_metric[0] if format_metric is not None else None
        if feature_strip_value:
            font_family_attr = (
                ' font-family="\'Arial Narrow\', \'Arial Narrow MT\', Arial, sans-serif"'
            )
        else:
            font_family_attr = (
                f' font-family="{css_font_family(source_family, template_panel=in_template_panel)}"'
                if source_family is not None or in_template_panel
                else ""
            )
        marker_box = bool(BOXED_MARKER_RE.fullmatch(text))
        direct_revision_text = bool(
            int(obj["style_ref"]) == 8
            and int(obj.get("format_ref", 0)) in {0x165, 0x166, 0x167}
            and re.fullmatch(r"\d+[A-Z]?", text)
        )
        boxed_reference_text = bool(re.fullmatch(r"(?:PS-N\d+-\d+|PANDA\d+-\d+-\d+)", text))
        support_box_text = bool(re.fullmatch(r'SD\d+(?:\s+\d+(?:/\d+)?")?', text))
        boxed_numeric_text = bool(re.fullmatch(r"\d{1,3}", text))
        # A text ref can coincidentally precede four composite children.  The
        # sequence is a frame relation only for observed boxed categories,
        # never for free annotations such as ``SEE ISO``.
        style_sequence_frame = (
            rectangles_by_text_ref.get(ref)
            if (
                not injected_label
                and (
                    marker_box
                    or boxed_reference_text
                    or support_box_text
                    or boxed_numeric_text
                )
            )
            else None
        )
        if style_sequence_frame is not None:
            direct_frame = style_sequence_frame
        elif marker_box or boxed_reference_text:
            direct_frame = rectangles_by_ref.get(ref + 5)
        else:
            direct_frame = None
        frame_candidates = [
            frame
            for frame in rectangles
            if frame[0] - 12 <= anchor_x <= frame[2] + 12
            and frame[1] - 12 <= anchor_y <= frame[3] + 12
        ] if not injected_label and (marker_box or boxed_reference_text) else []
        if direct_frame is not None:
            # The observed Sheet record sequence stores the closed-frame
            # parent five ids after many marker/reference text graphics.
            # Support labels alternatively encode their four edge styles as
            # text-ref + 1..4. Both are direct SHA relationships and are
            # stronger than overlap.
            anchored_frame = direct_frame
        elif frame_candidates:
            # Nested/overlapping SHA frames are common around support tags.
            # Selecting the first sorted rectangle can put a long PS-N label
            # into its neighbouring short Sxx frame.  For a normal PSM glyph
            # envelope, frame width is direct source evidence of the intended
            # cell; use centre distance only for page/container envelopes.
            psm_is_container = height > 320 or width > height * max(2, len(text)) * 2.2
            if psm_is_container:
                anchored_frame = min(
                    frame_candidates,
                    key=lambda frame: abs((frame[0] + frame[2]) / 2 - anchor_x)
                    + abs((frame[1] + frame[3]) / 2 - anchor_y),
                )
            else:
                anchored_frame = min(
                    frame_candidates,
                    key=lambda frame: (
                        abs(math.log(max(0.01, (frame[2] - frame[0]) / max(width, 1.0)))),
                        abs((frame[0] + frame[2]) / 2 - anchor_x)
                        + abs((frame[1] + frame[3]) / 2 - anchor_y),
                    ),
                )
        else:
            anchored_frame = None
        replaced_psm_container = False
        replaced_psm_container_with_style = False
        # Some boxed support/reference labels point at a page-scale PSM parent
        # envelope rather than their own glyph extent. The Sheet text anchor
        # and a directly decoded closed frame establish a local SHA-only
        # replacement boundary, so preserve the label instead of rejecting it
        # as a container false positive.
        if anchored_frame is not None and (height > 320 or width > height * max(2, len(text)) * 2.2):
            left, bottom, right, top = anchored_frame
            height = top - bottom
            width = right - left
            replaced_psm_container = True
        elif (
            (height > 320 or width > height * max(2, len(text)) * 2.2)
            # Restrict style reconstruction to the upper/right template area.
            # Page-number/title-block fields and short BOM values frequently
            # share the same PSM-container symptom but are not safe to infer.
            and len(text) >= 8
            and anchor_x >= view_x + view_width * 0.50
            and anchor_y >= view_y + view_height * 0.15
        ):
            # A few BOM/template records share their text style with ordinary
            # PSM-backed rows but point at a page-level parent envelope. The
            # repeated same-style samples provide a SHA-derived baseline and
            # glyph metrics, so recover this narrow case from the Sheet anchor
            # instead of omitting a real title/field.
            fallback = fallback_styles.get(int(obj["style_ref"]))
            if fallback is not None:
                dx, dy, font_height, char_width = fallback
                left = anchor_x + dx
                bottom = anchor_y + dy
                height = max(36, font_height)
                width = max(height * 0.7, char_width * len(text))
                right, top = left + width, bottom + height
                replaced_psm_container_with_style = True
        elif (
            # A few real dimension values and short ISO annotations use a
            # page-level PSM parent. Their own Sheet text record is valid, and
            # at least three local same-style glyph envelopes establish a
            # bounded SHA-only replacement. Keep this narrower than the BOM
            # recovery above so one-character binary false positives remain
            # rejected by the short-token guard.
            (height > 800 or width > height * max(2, len(text)) * 2.2)
            and 3 <= len(text) <= 10
            and ref <= 0xFFFF
            and int(obj["style_ref"]) <= 0xFFFF
            and int(obj["style_ref"]) in local_bounded_styles
            and local_bounded_styles[int(obj["style_ref"])][1] >= 3
        ):
            (dx, dy, font_height, char_width), _ = local_bounded_styles[int(obj["style_ref"])]
            left = anchor_x + dx
            bottom = anchor_y + dy
            height = max(36, font_height)
            width = max(height * 0.7, char_width * len(text))
            right, top = left + width, bottom + height
            replaced_psm_container_with_style = True
        ellipse_adjustment = None
        if not injected_label:
            for ellipse_bbox, dx, dy in ellipse_text_offsets:
                e_left, e_bottom, e_right, e_top = ellipse_bbox
                if e_left <= left and right <= e_right and e_bottom <= bottom and top <= e_top:
                    left, bottom, right, top = left + dx, bottom + dy, right + dx, top + dy
                    ellipse_adjustment = [dx, dy]
                    break
        angle = math.degrees(math.atan2(direction_y, direction_x))
        rotated = abs(direction_y) > 0.1 or abs(direction_x - 1) > 0.1
        # Short Sheet tokens (dimension values, callout ids and binary false
        # positives) cannot legitimately occupy a page-scale glyph envelope.
        # This distinguishes real rotated dimensions from a PSM reference to
        # their parent/component container.
        if len(text) <= 10 and height > 800:
            continue
        # Rotated/compound text uses a different transform record.  Until that
        # transform is decoded, suppress only implausible horizontal extents.
        if not rotated and (height > 320 or width > height * max(2, len(text)) * 2.2):
            continue
        text_ucis = sorted(
            {
                str(region["uci"])
                for region in uci_regions
                if intersects_bbox(left, bottom, right, top, region["bbox"])
            }
        )
        semantic = f' data-uci="{html.escape(",".join(text_ucis))}"' if text_ucis else ""
        insulation_code = bool(re.fullmatch(r"CI\d+", text))
        # Only ISO component-marker codes are centred in an enclosing frame.
        # Other labels can share a nearby leader/frame anchor while their PSM
        # envelope is the actual location (e.g. INSUL:/CI30/CI50).
        source_frame = anchored_frame
        if insulation_code:
            # CIxx text follows its immediately preceding local rectangle
            # graphic, not the text-anchor coordinate system. This relation is
            # directly observed in Sheet records (e.g. text 0x621 -> box 0x61B).
            source_frame = next(
                (rectangles_by_ref[ref - delta] for delta in range(1, 9) if ref - delta in rectangles_by_ref),
                None,
            )
        normalized_x, normalized_y = float(obj["x"]), float(obj["y"])
        is_north_marker = (
            text == "N"
            and not rotated
            and 0.015 <= normalized_x <= 0.025
            and 0.55 <= normalized_y <= 0.59
        )
        uses_sha_text_anchor = (
            ellipse_adjustment is None
            and source_frame is None
            and not rotated
            and text != "N"
            and not direct_revision_text
            and (
                injected_label
                or synthesized_psm_fallback
                or in_template_panel
                or feature_strip_value
                or text_anchor_matches_local_extent(
                    anchor_x,
                    anchor_y,
                    (left, bottom, right, top),
                )
            )
        )
        if revision_triangle is not None:
            triangle_center_x, triangle_center_y = revision_triangle["center"]
            font_size = float(revision_triangle["height"]) * 0.50
            revision_family = "'Arial Narrow', 'Arial Narrow MT', Arial, sans-serif"
            revision_baseline = 'dominant-baseline="central" '
            owned_geometry = revision_text_geometry(obj, style_data, sys.modules[__name__]) if template_owned else None
            if owned_geometry:
                triangle_center_x, triangle_center_y, font_size, revision_family = owned_geometry
                revision_baseline = ''
            rendered_revision_keys.add(
                tuple(int(ref) for ref in revision_triangle["triangle_key"])
            )
            elements.append(
                f'<text x="{float(triangle_center_x):.1f}" y="{svg_y(float(triangle_center_y)):.1f}" '
                f'font-size="{font_size:.4f}" text-anchor="middle" {revision_baseline}'
                f'font-family="{revision_family}"'
                f'{semantic} data-layer="sha-revision-triangle-text" '
                f'data-style="0x{int(obj["style_ref"]):04X}" '
                'data-mapping="sha-revision-symbol-centroid-plus-side-scale">'
                f'{html.escape(text)}</text>'
            )
        elif is_north_marker:
            # The north frame/arrow is already direct Sheet6 geometry.  Its
            # PSM text envelope lies in a different local coordinate space,
            # while the text transform anchor lies inside the true frame.
            source_frame = None
        if marker_box and source_frame is None and not injected_label:
            # These ISO component callout codes are conventionally boxed.  The
            # PSM extent provides the source layout when the template border is
            # a still-undecoded composite rectangle.
            margin = max(12, round(height * 0.18))
            elements.append(
                f'<rect x="{left - margin}" y="{PAGE_HEIGHT - top - margin}" '
                f'width="{width + margin * 2}" height="{height + margin * 2}" '
                f'fill="none" stroke="#17202a" stroke-width="8" data-layer="inferred-marker-frame"/>'
            )
            inferred_marker_boxes += 1
        # The PSM rectangle is the rendered text extent, so it preserves the
        # producer's per-object font hierarchy without decoding StyleCluster.
        if revision_triangle is not None:
            # Already emitted above from the closed-triangle centre.  Do not
            # also pass the same source record through the generic PSM branch.
            pass
        elif is_north_marker:
            elements.append(
                f'<text x="{anchor_x:.1f}" y="{svg_y(anchor_y):.1f}" font-size="{max(72, min(112, height)):.1f}"{font_family_attr} '
                f'data-layer="sha-north-label" data-style="0x{int(obj["style_ref"]):04X}">{html.escape(text)}</text>'
            )
        elif source_frame is not None and rotated and not replaced_psm_container:
            # A framed material/item number can still be rotated. The former
            # frame-first branch discarded its -90° transform and placed the
            # horizontal glyph beside an empty frame.
            font_size, text_length = rotated_text_extent(width, height, angle)
            x, y = anchor_x, svg_y(anchor_y)
            elements.append(
                f'<text x="{x:.1f}" y="{y:.1f}" font-size="{font_size:.1f}"{font_family_attr} '
                f'textLength="{text_length:.1f}" lengthAdjust="spacingAndGlyphs" '
                f'transform="rotate({-angle:.3f} {x:.1f} {y:.1f})"{semantic} '
                f'data-style="0x{int(obj["style_ref"]):04X}" '
                'data-frame-mapping="sha-rotated-text-anchor-plus-psm-size-with-closed-frame">'
                f'{html.escape(text)}</text>'
            )
        elif source_frame is not None and not replaced_psm_container:
            # A normal PSM envelope contains the real glyph metrics, while the
            # Sheet text transform contains the actual insertion point.  The
            # neighbouring rectangle is only the frame relation; centring the
            # text inside it discards both source parameters and made support
            # labels such as S17 visibly too small.
            elements.append(
                f'<text x="{anchor_x:.1f}" y="{svg_y(anchor_y):.1f}" '
                f'font-size="{height:.1f}" textLength="{width:.1f}"{font_family_attr} '
                f'lengthAdjust="spacingAndGlyphs"{semantic} '
                f'data-style="0x{int(obj["style_ref"]):04X}" '
                'data-frame-mapping="sha-text-anchor-plus-psm-size-with-closed-frame">'
                f'{html.escape(text)}</text>'
            )
        elif source_frame is not None:
            frame_left, frame_bottom, frame_right, frame_top = source_frame
            frame_width, frame_height = frame_right - frame_left, frame_top - frame_bottom
            # Keep the PSM glyph aspect ratio. The earlier prototype stretched
            # every code to the full cell width, making boxed callouts visibly
            # too wide compared with the SHA text object.
            if insulation_code:
                # The PSM envelope holds real CIxx glyph metrics; only its
                # position is in the adjacent rectangle's local space.
                font_size, text_length = height, width
            else:
                font_size = frame_height * 0.82
                text_length = min(frame_width * 0.92, font_size * width / height)
            elements.append(
                f'<text x="{(frame_left + frame_right) / 2:.1f}" '
                f'y="{svg_y((frame_bottom + frame_top) / 2):.1f}" '
                f'font-size="{font_size:.1f}" textLength="{text_length:.1f}"{font_family_attr} '
                f'text-anchor="middle" dominant-baseline="middle" lengthAdjust="spacingAndGlyphs"{semantic} '
                f'data-style="0x{int(obj["style_ref"]):04X}" '
                f'data-frame-mapping="sha-closed-rectangle">{html.escape(text)}</text>'
            )
        elif uses_sha_text_anchor:
            # These verified styles are used for BOM, free ISO annotations,
            # dimensions and title-block values. Their Sheet text transform is the source
            # baseline; PSM supplies only glyph size and width. Using the PSM
            # lower edge moves text left and downward.
            render_anchor_x, render_anchor_y = anchor_x, anchor_y
            if injected_label:
                render_anchor_x, render_anchor_y, anchor_mapping = injected_weld_label_anchor(
                    ref,
                    anchor_x,
                    anchor_y,
                    (left, bottom, right, top),
                    injected_marker_center_by_ref.get(ref),
                )
            else:
                if feature_strip_value:
                    anchor_mapping = "sha-feature-strip-value-anchor-plus-psm-size"
                else:
                    anchor_mapping = (
                        "sha-format-fallback-replaces-psm-container" if rejected_psm_container
                        else "sha-text-anchor-plus-psm-size"
                    )
            center_attributes = (
                ' text-anchor="middle" dominant-baseline="central"'
                if anchor_mapping == "sha-injected-weld-marker-center"
                else ""
            )
            if injected_label:
                rendered_height = WELD_CALLOUT_FONT_HEIGHT
                weld_style = f' fill="{WELD_CALLOUT_COLOR}" font-weight="700"'
                rendered_font_family_attr = (
                    ' font-family="SimHei, Microsoft YaHei, Arial, sans-serif"'
                )
                text_extent_attributes = ""
            else:
                if feature_strip_value:
                    # This SHA format declares one text height for the whole
                    # strip. Individual PSM boxes differ by one quantisation
                    # step, so use the same-format page median rather than
                    # allowing Water/P22/P&ID values to acquire different
                    # nominal sizes.
                    feature_format = fallback_formats.get(
                        (style_ref, format_ref, False)
                    )
                    source_height = (
                        feature_format[2]
                        if feature_format is not None
                        else height
                    )
                    rendered_height = (
                        source_height * CHENGDA_FEATURE_VALUE_HEIGHT_SCALE
                    )
                else:
                    rendered_height = height
                rendered_width = width
                weld_style = ""
                rendered_font_family_attr = font_family_attr
                text_extent_attributes = (
                    f' textLength="{rendered_width}" lengthAdjust="spacing"'
                    if feature_strip_value
                    else f' textLength="{rendered_width}" lengthAdjust="spacingAndGlyphs"'
                )
            render_svg_y = svg_y(render_anchor_y)
            feature_transform_attr = (
                f' transform="translate(0 {render_svg_y:.1f}) '
                f'scale(1 {CHENGDA_FEATURE_STRIP_GLYPH_Y_SCALE:.4f}) '
                f'translate(0 {-render_svg_y:.1f})"'
                if feature_strip_value
                else ""
            )
            elements.append(
                f'<text x="{render_anchor_x:.1f}" y="{render_svg_y:.1f}" '
                f'font-size="{rendered_height}"{text_extent_attributes}'
                f'{center_attributes}{rendered_font_family_attr}{feature_transform_attr}'
                f'{semantic}{weld_style} '
                f'data-style="0x{int(obj["style_ref"]):04X}" '
                f'data-mapping="{anchor_mapping}">'
                f'{html.escape(text)}</text>'
            )
        elif rotated:
            font_size, text_length = rotated_text_extent(width, height, angle)
            x, y = float(obj["x"]) * SHEET_UNIT, svg_y(float(obj["y"]) * SHEET_UNIT)
            elements.append(
                f'<text x="{x:.1f}" y="{y:.1f}" font-size="{font_size:.1f}"{font_family_attr} '
                f'textLength="{text_length:.1f}" lengthAdjust="spacingAndGlyphs" '
                f'transform="rotate({-angle:.3f} {x:.1f} {y:.1f})"{semantic} '
                f'data-style="0x{int(obj["style_ref"]):04X}" '
                'data-mapping="sha-text-anchor-plus-rotated-psm-projection">'
                f'{html.escape(text)}</text>'
            )
        else:
            elements.append(
                f'<text x="{left}" y="{PAGE_HEIGHT - bottom}" '
                f'font-size="{height}" textLength="{width}"{font_family_attr} '
                f'lengthAdjust="spacingAndGlyphs" dominant-baseline="text-after-edge"{semantic} '
                f'data-style="0x{int(obj["style_ref"]):04X}">'
                f'{html.escape(text)}</text>'
            )
        manifest_text.append(
            {
                "graphic_ref": f"0x{ref:08X}",
                "style_ref": f"0x{int(obj['style_ref']):08X}",
                "text": text,
                "sheet_anchor_normalized": [float(obj["x"]), float(obj["y"])],
                "direction": [direction_x, direction_y],
                "psm_bbox_page_units": [left, bottom, right, top],
                "uci_candidates": text_ucis,
                "inferred_marker_box": marker_box,
                "source_frame_page_units": list(source_frame) if source_frame else None,
                "ellipse_anchor_adjustment_page_units": ellipse_adjustment,
                "revision_cloud_text_adjustment_page_units": (
                    list(cloud_text_adjustment)
                    if cloud_text_adjustment is not None
                    else None
                ),
                "cut_pipe_table_adjustment_page_units": (
                    list(cut_pipe_text_adjustment)
                    if cut_pipe_text_adjustment is not None
                    else None
                ),
                "position_mapping": (
                    "sha-closed-frame-replaces-psm-container" if replaced_psm_container
                    else "sha-style-fallback-replaces-psm-container" if replaced_psm_container_with_style
                    else "sha-format-fallback-replaces-psm-container" if rejected_psm_container
                    else "sha-revision-triangle" if revision_triangle is not None
                    else "sha-north-anchor" if is_north_marker
                    else "sha-injected-weld-label-anchor" if injected_label
                    else "sha-text-anchor-plus-psm-size" if uses_sha_text_anchor
                    else "sha-ellipse-anchor-offset" if ellipse_adjustment is not None
                    else "sha-psm-envelope"
                ),
            }
        )
    for triangle in revision_triangle_records:
        parent_ref = int(triangle["parent_ref"])
        triangle_key = tuple(int(ref) for ref in triangle["triangle_key"])
        if triangle_key in rendered_revision_keys:
            continue
        # A shared Sheet parent can own several revision triangles, while the
        # compact embedded label is associated with only one of them.  Direct
        # text has already marked its triangle as rendered above; an unmatched
        # sibling must use the latest revision-stream value, not reuse the
        # other triangle's embedded label.
        label = (
            latest_revision_label
            if revision_triangle_parent_counts[parent_ref] > 1
            else revision_embedded_labels.get(parent_ref) or latest_revision_label
        )
        if not label:
            continue
        center_x, center_y = triangle["center"]
        font_size = float(triangle["height"]) * 0.50
        mapping = (
            "sha-revision-composite-embedded-text"
            if parent_ref in revision_embedded_labels
            else "sha-revision-stream-latest-record"
        )
        elements.append(
            f'<text x="{float(center_x):.1f}" y="{svg_y(float(center_y)):.1f}" '
            f'font-size="{font_size:.4f}" text-anchor="middle" dominant-baseline="central" '
            'font-family="\'Arial Narrow\', \'Arial Narrow MT\', Arial, sans-serif" '
            f'data-layer="sha-revision-triangle-text" data-style="0x0000" '
            f'data-mapping="{mapping}">{html.escape(label)}</text>'
        )
    elements.append("</g>")
    watermark_x = view_x + view_width * CHENGDA_FIN_WATERMARK_X_RATIO
    watermark_y = view_y + view_height * CHENGDA_FIN_WATERMARK_Y_RATIO
    watermark_font_size = view_height * CHENGDA_FIN_WATERMARK_FONT_RATIO
    elements.append(
        f'<text x="{watermark_x:.3f}" y="{watermark_y:.3f}" '
        f'font-size="{watermark_font_size:.3f}" '
        'font-family="Helvetica, Arial, sans-serif" font-weight="400" '
        'font-kerning="none" '
        'style="font-kerning:none;font-feature-settings:\'kern\' 0" '
        f'fill="#808080" fill-opacity="{CHENGDA_FIN_WATERMARK_OPACITY:g}" '
        f'transform="rotate({CHENGDA_FIN_WATERMARK_ANGLE:.3f} '
        f'{watermark_x:.3f} {watermark_y:.3f})" '
        'data-layer="chengda-fin-watermark" '
        'data-mapping="reference-pdf-page-relative-watermark">'
        f'{CHENGDA_FIN_WATERMARK_TEXT}</text>'
    )
    template_owned_summary = None
    if template_owned:
        extra_elements, template_owned_summary = render_plot_content(
            template_data, psm, style_data, owned_text, sys.modules[__name__]
        )
        elements.extend(extra_elements)
    elements.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(elements), encoding="utf-8")
    if manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "source_sha": str(sha_path),
                    "template_owned_page": template_owned_summary,
                    "sheet_stream": selected_name,
                    "logical_page": wanted_page,
                    "coordinate_system": {
                        "sheet_points_normalized": "Shape2D square workspace, x/y normalized to 0..1",
                        "psm_bbox_page_units": f"page units, {PAGE_WIDTH} x {PAGE_HEIGHT}",
                        "svg": "top-left page origin; y has been inverted from Shape2D",
                        "visible_sheet_viewbox": [view_x, view_y, view_width, view_height],
                    },
                    "uci_mapping_notice": (
                        "All page UCI regions come from direct SHA dynamic-attribute references found in the Sheet. "
                        "Individual lines and text are associated either by exact object reference or by PSM bounding-box "
                        "spatial overlap; spatial matches remain candidates until the complete Shape2D hierarchy is decoded."
                    ),
                    "uci_regions": uci_regions,
                    "uci_component_layer": {
                        "source": str(component_layer) if component_layer else None,
                        "line_count": len(component_lines),
                    },
                    "sha_template_geometry": {
                        "source_stream": "Sheet221",
                        "line_count": len(template_segments),
                    },
                    "sha_template_filled_paths": {
                        "source_stream": "Sheet221",
                        "path_count": len(filled_paths),
                        "styles": sorted(
                            {
                                f"0x{int(record['style_ref']):04X}": str(record["fill"])
                                for record in filled_paths
                            }.items()
                        ),
                        "objects": [
                            {
                                "child_ref": f"0x{int(record['child_ref']):08X}",
                                "style_ref": f"0x{int(record['style_ref']):04X}",
                                "fill": record["fill"],
                                "bbox_page_units": list(record["bbox"]),
                            }
                            for record in filled_paths
                        ],
                    },
                    "sha_template_images": [
                        {
                            "resource_id": image["resource_id"],
                            "x": image["x"],
                            "y": image["y"],
                            "width": image["width"],
                            "height": image["height"],
                        }
                        for image in images
                    ],
                    "sha_template_text_count": len(template_text),
                    "sha_template_revision_count": len(revision_text),
                    "sha_template_note_count": len(notes),
                    "fin_watermark": {
                        "text": CHENGDA_FIN_WATERMARK_TEXT,
                        "position_ratio": [
                            CHENGDA_FIN_WATERMARK_X_RATIO,
                            CHENGDA_FIN_WATERMARK_Y_RATIO,
                        ],
                        "font_size_ratio": CHENGDA_FIN_WATERMARK_FONT_RATIO,
                        "angle_degrees": CHENGDA_FIN_WATERMARK_ANGLE,
                        "opacity": CHENGDA_FIN_WATERMARK_OPACITY,
                        "fill": "#808080",
                        "mapping_basis": "issued-reference-PDF page-relative watermark",
                    },
                    "sha_connection_point_count": len(connection_points),
                    "sha_connection_points": [
                        {
                            "uci": str(region["uci"]),
                            "graphic_ref": f"0x{int(region['graphic_ref']):08X}",
                            "psm_bbox_page_units": list(region["bbox"]),
                            "anchor_page_units": list(
                                ellipse_anchor_by_ref.get(
                                    int(region["graphic_ref"]),
                                    (
                                        (region["bbox"][0] + region["bbox"][2]) / 2,
                                        (region["bbox"][1] + region["bbox"][3]) / 2,
                                    ),
                                )
                            ),
                            "mapping_basis": (
                                "sha-ellipse-anchor-plus-geometry-topology"
                                if int(region["graphic_ref"]) in ellipse_anchor_by_ref
                                else "sha-psm-micro-uci-plus-geometry-topology"
                            ),
                        }
                        for region in connection_points
                    ],
                    "inferred_marker_box_count": inferred_marker_boxes,
                    "segments": manifest_segments,
                    "standalone_arcs": manifest_arcs,
                    "text": manifest_text,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"Sheet stream: {selected_name}")
    print(f"Graphic bounding boxes: {len(graphics)}")
    print(f"Decoded vector segments: {len(segments)}")
    print(f"Composite child bounds retained as inventory only: {len(composite_inventory)}")
    print(f"Decoded composite arcs: {len(arcs)}")
    print(f"Decoded standalone centre-radius arcs: {len(direct_arcs)}")
    print(f"Decoded revision-cloud arcs: {len(revision_cloud_arcs)}")
    unclassified_arc_count = (
        len(direct_arc_candidates) - len(direct_arcs) - len(revision_cloud_arcs)
    )
    if unclassified_arc_count:
        print(
            "Rejected disconnected standalone arc candidates: "
            f"{unclassified_arc_count}"
        )
    print(f"UCI component layer segments: {len(component_lines)}")
    print(f"SHA template segments: {len(template_segments)}")
    print(f"SHA template filled paths: {len(filled_paths)}")
    print(f"SHA template images: {len(images)}")
    print(f"SHA template text: {len(template_text)}")
    print(f"SHA template revision fields: {len(revision_text)}")
    print(f"SHA template note lines: {len(notes)}")
    print(f"SHA connection points: {len(connection_points)}")
    print(f"Inferred component marker boxes: {inferred_marker_boxes}")
    print(f"Text objects: {len(manifest_text)}")
    print(f"SVG: {output_path}")
    if manifest_path:
        print(f"Manifest: {manifest_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--sheet-stream", help="Render this physical SHA Sheet stream instead of selecting by logical page.")
    parser.add_argument(
        "--project-profile",
        choices=("000200fw", "100200"),
        default="000200fw",
        help="Freeze unit-specific SHA interpretation rules.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--signature-dir",
        type=Path,
        help="Unit-specific signature PNG and profile.json directory.",
    )
    parser.add_argument(
        "--pcf",
        type=Path,
        help="Optional matching PCF used to validate and reflow stale CUT PIPE LENGTH cells.",
    )
    parser.add_argument("--debug-boxes", action="store_true")
    parser.add_argument("--manifest", type=Path, help="Write the SHA-to-SVG traceability manifest as JSON.")
    parser.add_argument(
        "--component-layer",
        type=Path,
        help="Earlier SHA-UCI SVG vector layer to preserve beneath Sheet6 page geometry.",
    )
    args = parser.parse_args()
    render(
        args.sha,
        args.output,
        args.page,
        args.debug_boxes,
        args.manifest,
        args.component_layer,
        args.sheet_stream,
        args.project_profile,
        args.signature_dir,
        args.pcf,
    )


if __name__ == "__main__":
    main()
