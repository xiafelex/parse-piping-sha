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
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from statistics import median

from analyze_iso_split import dynamic_graphics, read_sha_streams
from analyze_sha_pages import logical_page, text_objects

PAGE_WIDTH = 16800
PAGE_HEIGHT = 11880
SHEET_UNIT = 16800
BOXED_MARKER_RE = re.compile(r"^(?:[FGBS]\d+(?:-[A-Z0-9]+)?)(?:\s+[FGBS]\d+(?:-[A-Z0-9]+)?)*$|^T\d+-\d+$")


def psm_bbox(psm: bytes, graphic_ref: int) -> tuple[int, int, int, int] | None:
    """Read the observed PSM entry: graphic id followed by five uint16 values."""

    needle = struct.pack("<I", graphic_ref)
    for match in re.finditer(re.escape(needle), psm):
        offset = match.start() + 4
        if offset + 10 > len(psm):
            continue
        left, bottom, right, top, _ = struct.unpack_from("<5H", psm, offset)
        if left < right <= PAGE_WIDTH and bottom < top <= PAGE_HEIGHT:
            return left, bottom, right, top
    return None


def psm_bboxes(psm: bytes, graphic_ref: int) -> list[tuple[int, int, int, int]]:
    """Return every valid PSM envelope for one graphic reference.

    A few SHA files repeat a reference in PSMcluster0.  The first envelope can
    be a page/container record while a later one is the actual text extent, so
    consumers with an independent SHA anchor must be able to choose between
    all source candidates.
    """

    boxes: list[tuple[int, int, int, int]] = []
    needle = struct.pack("<I", graphic_ref)
    for match in re.finditer(re.escape(needle), psm):
        offset = match.start() + 4
        if offset + 10 > len(psm):
            continue
        left, bottom, right, top, _ = struct.unpack_from("<5H", psm, offset)
        if left < right <= PAGE_WIDTH and bottom < top <= PAGE_HEIGHT:
            box = (left, bottom, right, top)
            if box not in boxes:
                boxes.append(box)
    return boxes


def text_psm_bbox(
    psm: bytes, graphic_ref: int, anchor_x: float, anchor_y: float
) -> tuple[int, int, int, int] | None:
    """Choose the SHA PSM glyph box nearest a direct Sheet text anchor.

    Low graphic references can recur in PSMcluster0.  For text, the Sheet
    transform supplies a stronger source relation than PSM scan order: a real
    glyph box touches or lies near that anchor while a colliding page/container
    box is usually remote.  This remains entirely SHA-derived.
    """

    candidates = psm_bboxes(psm, graphic_ref)
    if not candidates:
        return None
    page_x, page_y = anchor_x * SHEET_UNIT, anchor_y * SHEET_UNIT

    def score(box: tuple[int, int, int, int]) -> tuple[float, int]:
        left, bottom, right, top = box
        dx = max(left - page_x, 0.0, page_x - right)
        dy = max(bottom - page_y, 0.0, page_y - top)
        return math.hypot(dx, dy), (right - left) * (top - bottom)

    return min(candidates, key=score)


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
    signature = b"\x2e\x00\x36\x00"
    for match in re.finditer(re.escape(signature), style_data):
        start = match.start()
        if start + 48 > len(style_data):
            continue
        style_ref = struct.unpack_from("<I", style_data, start + 20)[0]
        width_ratio = struct.unpack_from("<d", style_data, start + 40)[0]
        if 1 <= style_ref <= 1000 and 0.00001 <= width_ratio <= 0.01:
            widths[style_ref] = width_ratio * SHEET_UNIT
    return widths


def sheet_line_widths(data: bytes, style_data: bytes) -> dict[int, float]:
    """Return SHA page-unit stroke widths keyed by Sheet child primitive id."""

    style_widths = line_style_widths(style_data)
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

    style_widths = line_style_widths(style_data)
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


def composite_arcs(data: bytes) -> dict[int, tuple[float, float, float, float]]:
    """Read type-6 child primitives and their Shape2D page-space bounding boxes."""

    arcs: dict[int, tuple[float, float, float, float]] = {}
    for start in range(0, len(data) - 40, 2):
        if data[start : start + 2] != b"\x7b\x00":
            continue
        count = struct.unpack_from("<I", data, start + 22)[0]
        if not 1 <= count <= 100 or start + 34 + count * 14 > len(data):
            continue
        for index in range(count):
            child_ref, left, bottom, right, top, primitive_type = struct.unpack_from(
                "<I5H", data, start + 34 + index * 14
            )
            if primitive_type == 6 and left < right and bottom < top:
                # Composite records use a coordinate scale of two page units.
                arcs[child_ref] = (left / 2, bottom / 2, right / 2, top / 2)
    return arcs


def composite_segments(data: bytes) -> list[tuple[float, float, float, float, int, int]]:
    """Decode type-5 child primitives stored in Shape2D composite records.

    Unlike ordinary Sheet line records, these children store endpoints as four
    uint16 values at double page resolution. They contain component outlines
    such as reducer and flange details that are absent from the simple line
    layer.
    """

    segments: list[tuple[float, float, float, float, int, int]] = []
    for start in range(0, len(data) - 40, 2):
        if data[start : start + 2] != b"\x7b\x00":
            continue
        count = struct.unpack_from("<I", data, start + 22)[0]
        if not 1 <= count <= 100 or start + 34 + count * 14 > len(data):
            continue
        parent_ref = struct.unpack_from("<I", data, start + 2)[0]
        for index in range(count):
            child_ref, left, bottom, right, top, primitive_type = struct.unpack_from(
                "<I5H", data, start + 34 + index * 14
            )
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


def template_images(streams: dict[str, bytes]) -> list[dict[str, object]]:
    """Read the two BMP template image instances stored in SHA JSite streams."""

    images: list[dict[str, object]] = []
    template = streams.get("Sheet221", b"")
    resources = ((690, "JSite690/CONTENTS"), (1402, "JSite1402/CONTENTS"))
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


def template_text_records(data: bytes, psm: bytes) -> list[dict[str, object]]:
    """Select fixed Sheet221 labels with trustworthy PSM extents."""

    records: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for record in text_records(data):
        text = str(record["text"]).strip()
        ref = int(record["graphic_ref"])
        if not (0x900 <= ref <= 0x1200) or "<?xml" in text:
            continue
        # Some template refs occur repeatedly in PSMcluster0. The first match
        # can be a page/container envelope, while a later record is the small
        # glyph extent. Use the direct Sheet text anchor to choose only among
        # plausible title-block glyph boxes.
        candidates = [
            box
            for box in psm_bboxes(psm, ref)
            if box[2] - box[0] <= 1400 and box[3] - box[1] <= 240
        ]
        if not candidates:
            continue
        x, y = float(record["x"]) * SHEET_UNIT, float(record["y"]) * SHEET_UNIT
        bbox = min(
            candidates,
            key=lambda box: abs(x - box[0]) + 3 * abs(y - box[1]),
        )
        if not text or len(text) > 80:
            continue
        key = (ref, text)
        if key not in seen:
            seen.add(key)
            records.append(
                {
                    "text": text,
                    "graphic_ref": ref,
                    "bbox": bbox,
                    "x": float(record["x"]),
                    "y": float(record["y"]),
                }
            )
    return records


def template_bound_text(data: bytes, revision_data: bytes) -> list[dict[str, object]]:
    """Resolve Sheet221 revision bindings using the SHA Revision XML stream."""

    revision = ET.fromstring(revision_data.decode("utf-8"))
    current = revision.find("RevisionRecord")
    if current is None:
        return []
    values = {child.tag: child.text or "" for child in current}
    records: list[dict[str, object]] = []
    pattern = rb'<\x00\?\x00x\x00m\x00l\x00.*?<\x00/\x00b\x00o\x00d\x00y\x00>\x00'
    for match in re.finditer(pattern, data):
        expression = match.group().decode("utf-16le", errors="ignore")
        field = re.search(r"/RevisionRecord\[[^]]+\]/([^\"<]+)", expression)
        if field is None or match.end() + 32 > len(data):
            continue
        # Only the current record (1+0 or last()-0) has a value in this SHA.
        if "1+0" not in expression and "last()-0" not in expression:
            continue
        text = values.get(field.group(1), "")
        if not text:
            continue
        x, y = struct.unpack_from("<2d", data, match.end())
        if 0 <= x <= 1 and 0 <= y <= 1:
            records.append({"text": text, "x": x, "y": y, "field": field.group(1)})
    return records


def template_notes(data: bytes) -> list[dict[str, object]]:
    """Read the fixed note lines with their Sheet221 anchors."""

    notes: list[dict[str, object]] = []
    for record in text_records(data):
        text = str(record["text"]).strip()
        if not (text.startswith(("P1.", "2. PIPE ROUTING", "3. FOR STRESS", "d3. FOR STRESS"))):
            continue
        x, y = float(record["x"]), float(record["y"])
        if 0 <= x <= 1 and 0 <= y <= 1:
            notes.append({"text": text.removeprefix("d3.").replace("P1.", "1."), "x": x, "y": y})
    return notes


def template_anchor_labels(data: bytes, psm: bytes) -> list[dict[str, object]]:
    """Return fixed template labels and their known Shape2D layout data."""

    labels: list[dict[str, object]] = []
    for record in text_records(data):
        text = str(record["text"]).strip()
        x, y = float(record["x"]), float(record["y"])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        if text == "PIPING  ISOMETRIC":
            # The text record carries object 0x1252, while its actual PSM
            # envelope is sibling graphic 0x0E77. A few drawings reuse that
            # low graphic reference in PSMcluster0 for a page-sized container.
            # The smaller SHA PSM envelope is the stable label glyph box; do
            # not let the colliding container turn this title into giant text.
            candidates = psm_bboxes(psm, 0x0E77)
            glyph_bbox = min(
                candidates,
                key=lambda box: (box[2] - box[0]) * (box[3] - box[1]),
                default=None,
            )
            labels.append(
                {
                    "text": text,
                    "x": x,
                    "y": y,
                    "font_family": "Arial, sans-serif",
                    "anchor": "start",
                    "psm_bbox": glyph_bbox,
                    "psm_graphic_ref": 0x0E77,
                }
            )
        elif text == "BANC PANDA3 PROJECT":
            labels.append({"text": text, "x": x, "y": y, "font_size": 0.007 * SHEET_UNIT, "font_family": "Arial, sans-serif", "anchor": "start"})
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
                    "psm_bbox": text_psm_bbox(psm, psm_ref, x, y),
                    "psm_graphic_ref": psm_ref,
                }
            )
        elif text.lstrip("%") == "NEW OR MODIFICATION OF EXISTING PIPE.":
            labels.append({"text": text.lstrip("%"), "x": x, "y": y, "font_size": 0.0028 * SHEET_UNIT, "font_family": "Arial, sans-serif", "anchor": "start"})
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


def text_records(data: bytes) -> list[dict[str, object]]:
    """Read text plus its adjacent Shape2D graphic/style references."""

    records: list[dict[str, object]] = []
    for match in re.finditer(rb"(?:(?:[\x20-\x7e]\x00){1,})", data):
        if match.start() < 24 or match.end() + 32 > len(data):
            continue
        # Shape2D stores a uint16 UTF-16 character count immediately before
        # some strings. Counts 32..126 look like one printable UTF-16 glyph
        # (for example 0x21 appears as "!") and can be swallowed by the
        # printable-text scan. Recover the real text start before resolving
        # the adjacent graphic/style references.
        text_start = match.start()
        decoded = match.group().decode("utf-16le")
        declared_count = struct.unpack_from("<H", data, match.start())[0]
        if declared_count == len(decoded) - 1:
            text_start += 2
            decoded = decoded[1:]
        x, y, direction_x, direction_y = struct.unpack_from("<dddd", data, match.end())
        # Some template strings carry property pairs before their transform.
        if abs(x) + abs(y) < 1e-100:
            for offset in range(match.end() + 4, min(len(data) - 32, match.end() + 144), 4):
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
        records.append(
            {
                "text": decoded.strip(),
                "graphic_ref": struct.unpack_from("<I", data, text_start - 24)[0],
                "style_ref": struct.unpack_from("<I", data, text_start - 16)[0],
                "x": x,
                "y": y,
                "direction_x": direction_x,
                "direction_y": direction_y,
            }
        )
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


def render(
    sha_path: Path,
    output_path: Path,
    wanted_page: int,
    debug_boxes: bool,
    manifest_path: Path | None,
    component_layer: Path | None,
    sheet_stream: str | None = None,
    anchor_left_free_text: bool = False,
    anchor_left_free_text_prefixes: tuple[str, ...] = (),
) -> None:
    streams = read_sha_streams(sha_path)
    sheets = {
        name: data
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name) and len(data) > 1024
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
    template_segments = template_line_segments(streams.get("Sheet221", b""))
    images = template_images(streams)
    template_text = template_text_records(streams.get("Sheet221", b""), psm)
    revision_text = template_bound_text(streams.get("Sheet221", b""), streams.get("TaggedTxtData/Revision", b"<Revision/>"))
    notes = template_notes(streams.get("Sheet221", b""))
    anchor_labels = template_anchor_labels(streams.get("Sheet221", b""), psm)
    unicode_labels = template_unicode_labels(
        streams.get("Sheet221", b""), psm, streams.get("StyleCluster", b"")
    )

    elements = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_WIDTH}" height="{PAGE_HEIGHT}" '
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
            elements.append(
                f'<line x1="{x1 * SHEET_UNIT:.1f}" y1="{svg_y(y1 * SHEET_UNIT):.1f}" '
                f'x2="{x2 * SHEET_UNIT:.1f}" y2="{svg_y(y2 * SHEET_UNIT):.1f}" '
                f'data-layer="sha-template" data-graphic="0x{template_ref:08X}" '
                f'data-child="0x{child_ref:08X}"/>'
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
    if template_text:
        # Template records use the same sans-serif StyleCluster family as the
        # decoded static title labels.  Keep this separate from the ISO body,
        # whose engineering annotations are an observed fixed-pitch family.
        elements.append('<g id="sha-template-text" fill="#17202a" font-family="Arial, Helvetica, sans-serif" text-rendering="geometricPrecision">')
        for record in template_text:
            left, bottom, right, top = record["bbox"]
            height = max(36, top - bottom)
            elements.append(
                f'<text x="{float(record["x"]) * SHEET_UNIT:.1f}" y="{PAGE_HEIGHT - bottom}" font-size="{height}" '
                f'textLength="{right - left}" lengthAdjust="spacingAndGlyphs" '
                # PSM gives the glyph envelope rather than a font baseline.
                # Aligning its lower edge prevents title-block rows drifting
                # vertically when SVG substitutes the producer's font.
                'dominant-baseline="text-after-edge" '
                f'data-layer="sha-template-text" data-graphic="0x{int(record["graphic_ref"]):04X}" '
                'data-mapping="sha-text-anchor-x-plus-psm-envelope">'
                f'{html.escape(str(record["text"]))}</text>'
            )
        elements.append("</g>")
    if revision_text or notes or anchor_labels or unicode_labels:
        elements.append('<g id="sha-template-bound-text" fill="#17202a" font-family="Arial, Helvetica, sans-serif" text-rendering="geometricPrecision">')
        for record in revision_text:
            x, y = float(record["x"]) * SHEET_UNIT, svg_y(float(record["y"]) * SHEET_UNIT)
            elements.append(
                f'<text x="{x:.1f}" y="{y:.1f}" font-size="{0.005 * SHEET_UNIT:.1f}" text-anchor="middle" '
                f'data-layer="sha-template-revision" data-field="{record["field"]}" '
                'data-mapping="sha-stylecluster-font-ratio">'
                f'{html.escape(str(record["text"]))}</text>'
            )
        for record in notes:
            x, y = float(record["x"]) * SHEET_UNIT, svg_y(float(record["y"]) * SHEET_UNIT)
            elements.append(
                # NOTES and its three rows share StyleCluster 0x1252; the
                # decoded NOTES PSM record establishes its 40 page-unit size.
                f'<text x="{x:.1f}" y="{y:.1f}" font-size="40" data-layer="sha-template-note">'
                f'{html.escape(str(record["text"]))}</text>'
            )
        for record in anchor_labels:
            x = float(record["x"]) * SHEET_UNIT
            bbox = record.get("psm_bbox")
            if bbox is not None:
                left, bottom, right, top = bbox
                y = svg_y(float(record["y"]) * SHEET_UNIT)
                font_size = top - bottom
                text_length = right - left
                metric_attributes = (
                    f' font-size="{font_size:.1f}" textLength="{text_length:.1f}" '
                    'lengthAdjust="spacingAndGlyphs" '
                    f'data-psm-graphic="0x{int(record["psm_graphic_ref"]):04X}" '
                    'data-mapping="sha-text-anchor-plus-sibling-psm-size"'
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
    composite = composite_segments(sheet)
    segments = ordinary_segments + alternate_segments + composite
    render_segments = (
        [(segment, "ordinary") for segment in ordinary_segments]
        + [(segment, "alternate") for segment in alternate_segments]
        + [(segment, "composite") for segment in composite]
    )
    # Sheet6 commonly serializes the same primitive twice: once as an ordinary
    # two-point record and once in the 18/32 family.  Keep the ordinary record
    # for an exact coordinate match and retain 18/32-only component detail.
    # This is source de-duplication, not a visual tolerance or PDF-derived
    # adjustment.
    def geometry_key(segment: tuple[float, float, float, float, int, int]) -> tuple[tuple[float, float], tuple[float, float]]:
        x1, y1, x2, y2, _, _ = segment
        first = (round(x1, 8), round(y1, 8))
        second = (round(x2, 8), round(y2, 8))
        return (first, second) if first <= second else (second, first)

    ordinary_geometry = {geometry_key(segment) for segment in ordinary_segments}
    alternate_object_groups = template_line_object_groups(sheet)
    style_data = streams.get("StyleCluster", b"")
    sheet_line_width_by_child = sheet_line_widths(sheet, style_data)
    sheet_line_width_by_child.update(template_line_widths(sheet, style_data))
    rectangles = sheet_rectangles(segments)
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
    local_bounded_styles = bounded_style_fallbacks(sheet, psm)
    ellipses = psm_ellipses(sheet, psm)
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
    vector_refs = {ref for *_, ref, child_ref in segments} | {child_ref for *_, ref, child_ref in segments}
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
    arcs = composite_arcs(sheet)
    manifest_segments: list[dict[str, object]] = []
    elements.append('<g fill="none" stroke="#17202a" stroke-width="8" stroke-linecap="square" shape-rendering="geometricPrecision">')
    for (x1, y1, x2, y2, ref, child_ref), family in render_segments:
        if (
            family == "alternate"
            and (
                (ref, child_ref) in duplicate_alternate_frame_edges
                or geometry_key((x1, y1, x2, y2, ref, child_ref)) in ordinary_geometry
            )
        ):
            continue
        page_x1, page_y1 = x1 * SHEET_UNIT, y1 * SHEET_UNIT
        page_x2, page_y2 = x2 * SHEET_UNIT, y2 * SHEET_UNIT
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
        object_group = (
            f' data-local-group="0x{alternate_object_groups[ref]:04X}"'
            if ref in alternate_object_groups
            else ""
        )
        width = sheet_line_width_by_child.get(child_ref)
        width_attr = f' stroke-width="{width:.4f}"' if width is not None else ""
        if child_ref in arcs:
            left, bottom, right, top = arcs[child_ref]
            x_start, y_start = page_x1, svg_y(page_y1)
            x_end, y_end = page_x2, svg_y(page_y2)
            elements.append(
                f'<path d="M {x_start:.1f} {y_start:.1f} A {(right - left) / 2:.1f} '
                f'{(top - bottom) / 2:.1f} 0 0 1 {x_end:.1f} {y_end:.1f}" '
                f'data-graphic="0x{ref:04X}" data-child="0x{child_ref:08X}"{width_attr}{semantic}{object_group}/>'
            )
        else:
            elements.append(
                f'<line x1="{page_x1:.1f}" y1="{svg_y(page_y1):.1f}" '
                f'x2="{page_x2:.1f}" y2="{svg_y(page_y2):.1f}" '
                f'data-graphic="0x{ref:04X}" data-child="0x{child_ref:08X}"{width_attr}{semantic}{object_group}/>'
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
    sheet_text_records = text_records(sheet)
    starred_right_title_labels = {
        str(record["text"]).strip()[1:-1]
        for record in sheet_text_records
        if str(record["text"]).strip().startswith("*")
        and str(record["text"]).strip().endswith("*")
        and abs(float(record["direction_y"])) > 0.8
        and float(record["x"]) >= 0.80
    }
    emitted_text: set[tuple[int, str]] = set()
    manifest_text: list[dict[str, object]] = []
    inferred_marker_boxes = 0
    for obj in sheet_text_records:
        text = str(obj["text"]).strip()
        ref = int(obj["graphic_ref"])
        if not text or len(text) > 120 or (ref, text) in emitted_text:
            continue
        # Text records are scanned from a binary Sheet stream.  A few object
        # headers can resemble very short text (for example ``{f``) and point
        # at a page-sized PSM container.  They are not visible ISO labels.
        # Imperial support/component labels legitimately include inch marks,
        # for example ``SD010 1/2\"``.  Keep the filter for binary false
        # positives but accept that printable ISO character.
        if not re.fullmatch(r"[A-Za-z0-9 +./()_:%*,\"'&-]+", text):
            continue
        if (
            text in starred_right_title_labels
            and abs(float(obj["direction_y"])) > 0.8
            and float(obj["x"]) >= 0.80
        ):
            # Shape2D retains a plain source field alongside the final
            # asterisk-wrapped title-block drawing number. The former often
            # points at a page container and is not independently visible.
            continue
        bbox = psm_bbox(psm, ref)
        has_direct_psm_bbox = bbox is not None
        emitted_text.add((ref, text))
        direction_x, direction_y = float(obj["direction_x"]), float(obj["direction_y"])
        direction_length = math.hypot(direction_x, direction_y)
        if not 0.8 <= direction_length <= 1.2:
            continue
        # The right-edge title-block drawing number is a genuine Sheet text
        # record written as ``*<drawing id>*``. In four observed SHA files its
        # PSM low reference collides with a page container. Its direct anchor
        # and vertical direction identify the intended local glyph box without
        # applying this potentially unsafe choice to ordinary ISO annotations.
        if (
            text.startswith("*")
            and text.endswith("*")
            and abs(direction_y) > 0.8
            and float(obj["x"]) >= 0.80
        ):
            bbox = text_psm_bbox(psm, ref, float(obj["x"]), float(obj["y"]))
        if bbox is None:
            # No PSM extent: derive its displayed baseline from peer records
            # using the same SHA StyleCluster reference.
            fallback = fallback_styles.get(int(obj["style_ref"]))
            if fallback is None or not re.fullmatch(r"[A-Za-z0-9 +./()_\"-]+", text):
                continue
            dx, dy, font_height, char_width = fallback
            left = float(obj["x"]) * SHEET_UNIT + dx
            bottom = float(obj["y"]) * SHEET_UNIT + dy
            height = max(36, font_height)
            width = max(height * 0.7, char_width * len(text))
            bbox = (round(left), round(bottom), round(left + width), round(bottom + height))
        else:
            left, bottom, right, top = bbox
            height = max(42, top - bottom)
            width = right - left
        # Retain the original PSM metrics.  Later SHA-only container recovery
        # can replace the temporary envelope, and must never qualify as a
        # free-text anchor correction.
        original_psm_width = width
        original_psm_height = height
        anchor_x, anchor_y = float(obj["x"]) * SHEET_UNIT, float(obj["y"]) * SHEET_UNIT
        # The physical A1 template occupies the SHA page's right-hand panel.
        # Its direct Sheet records form a separate style cluster from the
        # Courier New ISO body: for example RHO uses 0x95C4/0x9608 in the title
        # block and BOM, versus 0x9609 on the pipe drawing. Use the SHA anchor
        # region to retain that family even though the numeric style ids vary
        # between pages.
        in_template_panel = anchor_x >= SHEET_UNIT * 0.55
        font_family_attr = ' font-family="Arial, Helvetica, sans-serif"' if in_template_panel else ""
        marker_box = bool(BOXED_MARKER_RE.fullmatch(text))
        boxed_reference_text = bool(re.fullmatch(r"(?:PS-N\d+-\d+|PANDA\d+-\d+-\d+)", text))
        support_box_text = bool(re.fullmatch(r'SD\d+(?:\s+\d+(?:/\d+)?")?', text))
        boxed_numeric_text = bool(re.fullmatch(r"\d{1,3}", text))
        # A text ref can coincidentally precede four composite children.  The
        # sequence is a frame relation only for observed boxed categories,
        # never for free annotations such as ``SEE ISO``.
        style_sequence_frame = (
            rectangles_by_text_ref.get(ref)
            if marker_box or boxed_reference_text or support_box_text or boxed_numeric_text
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
        ] if marker_box or boxed_reference_text else []
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
        is_north_marker = text == "N" and int(obj["style_ref"]) == 0xE74
        known_anchor_style = (
            int(obj["style_ref"]) in {0x0585, 0x0586, 0x0897, 0x0E74}
            and ellipse_adjustment is None
            and source_frame is None
            and not rotated
            and text != "N"
        )
        # Experimental left-ISO rule: some ordinary free annotations retain a
        # stable direct Sheet insertion point while their PSM glyph box is
        # offset into a local layout space. Keep PSM glyph dimensions but use
        # the SHA anchor only for the narrowly evidenced offset band. This is
        # opt-in until visual QA confirms it across the corpus.
        left_free_anchor_candidate = (
            anchor_left_free_text
            and (
                not anchor_left_free_text_prefixes
                or any(text.upper().startswith(prefix) for prefix in anchor_left_free_text_prefixes)
            )
            and anchor_x < view_x + view_width * 0.55
            and ellipse_adjustment is None
            and source_frame is None
            and not rotated
            and not marker_box
            and not boxed_reference_text
            and not support_box_text
            and not boxed_numeric_text
            and not insulation_code
            and text != "N"
            and not replaced_psm_container
            and not replaced_psm_container_with_style
            and has_direct_psm_bbox
            and original_psm_height <= 320
            and original_psm_width <= original_psm_height * max(2, len(text)) * 2.2
            and -260 <= left - anchor_x <= -40
            and -280 <= bottom - anchor_y <= -50
        )
        uses_sha_text_anchor = known_anchor_style or left_free_anchor_candidate
        if is_north_marker:
            # The north frame/arrow is already direct Sheet6 geometry.  Its
            # PSM text envelope lies in a different local coordinate space,
            # while the text transform anchor lies inside the true frame.
            source_frame = None
        if marker_box and source_frame is None:
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
        if is_north_marker:
            elements.append(
                f'<text x="{anchor_x:.1f}" y="{svg_y(anchor_y):.1f}" font-size="{max(72, min(112, height)):.1f}"{font_family_attr} '
                f'data-layer="sha-north-label" data-style="0x{int(obj["style_ref"]):04X}">{html.escape(text)}</text>'
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
            elements.append(
                f'<text x="{anchor_x:.1f}" y="{svg_y(anchor_y):.1f}" '
                f'font-size="{height}" textLength="{width}" lengthAdjust="spacingAndGlyphs"{font_family_attr}{semantic} '
                f'data-style="0x{int(obj["style_ref"]):04X}" data-mapping="sha-text-anchor-plus-psm-size">'
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
                "position_mapping": (
                    "sha-closed-frame-replaces-psm-container" if replaced_psm_container
                    else "sha-style-fallback-replaces-psm-container" if replaced_psm_container_with_style
                    else "sha-left-free-anchor-plus-psm-size" if left_free_anchor_candidate
                    else "sha-text-anchor-plus-psm-size" if uses_sha_text_anchor
                    else "sha-ellipse-anchor-offset" if ellipse_adjustment is not None
                    else "sha-psm-envelope"
                ),
            }
        )
    elements.extend(["</g>", "</svg>"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(elements), encoding="utf-8")
    if manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "source_sha": str(sha_path),
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
    print(f"Decoded composite arcs: {len(arcs)}")
    print(f"UCI component layer segments: {len(component_lines)}")
    print(f"SHA template segments: {len(template_segments)}")
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
        "--anchor-left-free-text",
        action="store_true",
        help="Experimental: use direct SHA anchors for eligible left-side free annotation text.",
    )
    parser.add_argument(
        "--anchor-left-free-text-prefix",
        action="append",
        default=[],
        metavar="PREFIX",
        help="Limit the experimental left-text rule to an uppercase text prefix; may be repeated.",
    )
    parser.add_argument("--output", type=Path, required=True)
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
        args.anchor_left_free_text,
        tuple(prefix.upper() for prefix in args.anchor_left_free_text_prefix),
    )


if __name__ == "__main__":
    main()
