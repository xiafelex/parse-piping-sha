#!/usr/bin/env python3
"""Resolve Chengda management tables to SHA geometry and overlay SVG tags."""

from __future__ import annotations

import json
import math
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from chengda_management_table import ManagementInput
from chengda_spool_table import SpoolInput
from chengda_callout_text import weld_label_scale


MANAGEMENT_COLOR = "#0066ff"
MANAGEMENT_FONT_FAMILY = "SimHei, Microsoft YaHei, Arial, sans-serif"
MANAGEMENT_FONT_HEIGHT = 88.0
MANAGEMENT_TEXT_PADDING_X = 18.0
MANAGEMENT_TEXT_PADDING_Y = 12.0
SPOOL_COLOR = "#ff0000"
SPOOL_FONT_HEIGHT = 64.0
SPOOL_INNER_FRAME_GAP = 14.0
PAGE_HEIGHT = 11880.0
SHEET_UNIT = 16800.0


@dataclass(frozen=True)
class ManagementTarget:
    kind: str
    number: str
    sheet_id: str
    page: int
    ucis: tuple[str, ...]
    anchor: tuple[float, float]
    mapping_method: str
    confidence: str
    graphic_refs: tuple[int, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.number}"


def forward_owned_uci_regions(
    sha_path: Path,
    sheet_stream: str,
) -> list[dict[str, object]]:
    """Return page-local UCI envelopes with corrected forward ownership.

    Chengda's dynamic attribute trailer precedes the record that names its
    owning PCF UCI. The renderer keeps the legacy same-record view for
    forensic compatibility, while management-component identity must use the
    same corrected ownership already proven by weld mapping.
    """

    from analyze_iso_split import forward_owned_dynamic_graphics, read_sha_streams
    from sha_to_svg_prototype import psm_bbox

    streams = read_sha_streams(sha_path)
    if sheet_stream not in streams:
        raise ValueError(f"SHA缺少页面流：{sheet_stream}")
    sheet = streams[sheet_stream]
    psm = streams.get("PSMcluster0", b"")
    dynamic = forward_owned_dynamic_graphics(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    # Management components need the stream's first UCI as well. Its owner
    # reference is serialized in the 20-byte prefix immediately before the
    # first marker, while the generic forward-owned reader intentionally keeps
    # weld mapping on its already verified topology fallback for this edge.
    dynamic_data = streams.get("Unclustered Dynamic Attributes", b"")
    first_marker = dynamic_data.find(b"PipeLine Info\x00")
    first_start = first_marker - 5
    if first_marker >= 5 and first_start >= 20:
        next_marker = dynamic_data.find(b"PipeLine Info\x00", first_marker + 1)
        first_end = next_marker - 5 if next_marker >= 5 else len(dynamic_data)
        first_record = dynamic_data[first_start:first_end]
        first_uci = re.search(
            rb"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
            rb"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}",
            first_record,
        )
        prefix_space_ref = struct.unpack_from("<I", dynamic_data, first_start - 20)[0]
        prefix_graphic_ref = struct.unpack_from("<I", dynamic_data, first_start - 8)[0]
        if first_uci and prefix_space_ref and prefix_graphic_ref:
            uci = first_uci.group().decode()
            dynamic.setdefault(uci, []).insert(
                0,
                {
                    "record_offset": first_start,
                    "reference_record_offset": first_start - 20,
                    "space_ref": prefix_space_ref,
                    "graphic_ref": prefix_graphic_ref,
                },
            )
    regions: list[dict[str, object]] = []
    owner_sheet = sheet
    template_sheet = streams.get("Sheet221", b"")
    dynamic_refs = {
        int(record["graphic_ref"])
        for records in dynamic.values()
        for record in records
    }
    sheet_match_count = sum(
        ref.to_bytes(4, "little") in sheet for ref in dynamic_refs
    )
    template_match_count = sum(
        ref.to_bytes(4, "little") in template_sheet for ref in dynamic_refs
    )
    # Injected text can incidentally contain one small integer that equals a
    # dynamic reference. Select the stream with the stronger ownership set,
    # rather than treating any byte hit as sufficient page evidence.
    if template_match_count > sheet_match_count:
        owner_sheet = template_sheet
    for uci, records in dynamic.items():
        for record in records:
            graphic_ref = int(record["graphic_ref"])
            if graphic_ref.to_bytes(4, "little") not in owner_sheet:
                continue
            bbox = psm_bbox(psm, graphic_ref)
            if bbox is None:
                continue
            regions.append(
                {
                    "graphic_ref": graphic_ref,
                    "uci": uci.upper(),
                    "bbox": list(bbox),
                    "ownership": "forward-owned-dynamic-attribute",
                }
            )
    return regions


def _bbox_center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _interface_between_boxes(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float]:
    first_cx, first_cy = _bbox_center(first)
    second_cx, second_cy = _bbox_center(second)
    if first[2] < second[0]:
        y = (max(first[1], second[1]) + min(first[3], second[3])) / 2
        if max(first[1], second[1]) > min(first[3], second[3]):
            y = (first_cy + second_cy) / 2
        return (first[2] + second[0]) / 2, y
    if second[2] < first[0]:
        return _interface_between_boxes(second, first)
    if first[3] < second[1]:
        x = (max(first[0], second[0]) + min(first[2], second[2])) / 2
        if max(first[0], second[0]) > min(first[2], second[2]):
            x = (first_cx + second_cx) / 2
        return x, (first[3] + second[1]) / 2
    if second[3] < first[1]:
        return _interface_between_boxes(second, first)
    # A compact envelope beside a much larger component is often the flange
    # face/container itself.  When direct child strokes are unavailable,
    # retaining that compact decoded envelope is safer than putting the root
    # halfway across the empty gap between component centres.
    return (first_cx + second_cx) / 2, (first_cy + second_cy) / 2


def _flange_interface_between_boxes(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Use decoded envelope overlap only for a managed flange face."""

    first_cx, first_cy = _bbox_center(first)
    second_cx, second_cy = _bbox_center(second)
    overlap_left = max(first[0], second[0])
    overlap_bottom = max(first[1], second[1])
    overlap_right = min(first[2], second[2])
    overlap_top = min(first[3], second[3])
    if overlap_left <= overlap_right and overlap_bottom <= overlap_top:
        return (
            (overlap_left + overlap_right) / 2,
            (overlap_bottom + overlap_top) / 2,
        )
    first_area = max(1.0, (first[2] - first[0]) * (first[3] - first[1]))
    second_area = max(1.0, (second[2] - second[0]) * (second[3] - second[1]))
    first_compact = max(first[2] - first[0], first[3] - first[1]) <= 180.0
    second_compact = max(second[2] - second[0], second[3] - second[1]) <= 180.0
    if first_compact and first_area <= second_area * 0.20:
        return first_cx, first_cy
    if second_compact and second_area <= first_area * 0.20:
        return second_cx, second_cy
    return _interface_between_boxes(first, second)


def _pcf_pipe_projection(
    pcf_path: Path,
    coordinate: tuple[float, float, float],
) -> tuple[str, float] | None:
    lines = Path(pcf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    candidates: list[tuple[float, str, float]] = []
    for position, start in enumerate(starts):
        if lines[start].strip() not in {"PIPE", "PIPE-FIXED"}:
            continue
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        points = [
            tuple(float(value) for value in match)
            for match in re.findall(
                r"END-POINT\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                block,
            )
        ]
        uci = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        if len(points) < 2 or uci is None:
            continue
        first, second = points[:2]
        delta = tuple(second[index] - first[index] for index in range(3))
        length_squared = sum(value * value for value in delta)
        if length_squared <= 1e-12:
            continue
        offset = tuple(coordinate[index] - first[index] for index in range(3))
        fraction = sum(offset[index] * delta[index] for index in range(3)) / length_squared
        clamped = max(0.0, min(1.0, fraction))
        projected = tuple(first[index] + clamped * delta[index] for index in range(3))
        distance = math.dist(coordinate, projected)
        candidates.append((distance, uci.group(1).upper(), clamped))
    if not candidates:
        return None
    distance, uci, fraction = min(candidates)
    # A support coordinate belongs on its pipe axis. Avoid projecting an
    # unrelated nearby support onto a remote run when the PCF topology is bad.
    if distance > 5.0:
        return None
    return uci, fraction


def _pcf_component_centre(
    pcf_path: Path,
    coordinate: tuple[float, float, float],
    tolerance: float = 0.05,
) -> tuple[str, str] | None:
    """Return the unique fitting whose engineering centre is the support point."""

    lines = Path(pcf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    candidates: list[tuple[str, str]] = []
    for position, start in enumerate(starts):
        kind = lines[start].strip()
        if kind in {
            "PIPE",
            "PIPE-FIXED",
            "WELD",
            "SUPPORT",
            "BOLT",
            "GASKET",
            "FLOW-ARROW",
        }:
            continue
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        uci = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        centre = re.search(
            r"CENTRE-POINT\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
            block,
        )
        if uci is None:
            continue
        if centre is not None:
            point = tuple(float(centre.group(index)) for index in range(1, 4))
        else:
            endpoints = [
                tuple(float(value) for value in match)
                for match in re.findall(
                    r"END-POINT\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                    block,
                )
            ]
            if len(endpoints) != 2:
                continue
            point = tuple(
                (endpoints[0][index] + endpoints[1][index]) / 2.0
                for index in range(3)
            )
        if math.dist(point, coordinate) <= tolerance:
            candidates.append((kind, uci.group(1).upper()))
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else None


def _pcf_component_endpoints_by_uci(
    pcf_path: Path,
    component_kind: str | tuple[str, ...] | None,
) -> dict[str, list[tuple[float, float, float]]]:
    """Return explicit PCF component endpoints keyed by UCI."""

    lines = Path(pcf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    result: dict[str, list[tuple[float, float, float]]] = {}
    allowed_kinds = (
        {component_kind}
        if isinstance(component_kind, str)
        else (set(component_kind) if component_kind is not None else None)
    )
    for position, start in enumerate(starts):
        kind = lines[start].strip()
        if allowed_kinds is not None and kind not in allowed_kinds:
            continue
        if kind in {"WELD", "SUPPORT", "BOLT", "GASKET", "FLOW-ARROW"}:
            continue
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        uci = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        if uci is None:
            continue
        endpoints = [
            tuple(float(value) for value in match)
            for match in re.findall(
                r"END-POINT\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                block,
            )
        ]
        if endpoints:
            result[uci.group(1).upper()] = endpoints
    return result


def _pcf_component_records_by_uci(
    pcf_path: Path,
) -> dict[str, tuple[str, list[tuple[float, float, float]]]]:
    """Return each explicit PCF component kind and its geometric endpoints."""

    lines = Path(pcf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    result: dict[str, tuple[str, list[tuple[float, float, float]]]] = {}
    for position, start in enumerate(starts):
        kind = lines[start].strip()
        if kind in {"WELD", "SUPPORT", "BOLT", "GASKET", "FLOW-ARROW"}:
            continue
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        uci = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        if uci is None:
            continue
        endpoints = [
            tuple(float(value) for value in match)
            for match in re.findall(
                r"END-POINT\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                block,
            )
        ]
        if endpoints:
            result[uci.group(1).upper()] = (kind, endpoints)
    return result


def _pcf_component_ports_by_uci(
    pcf_path: Path,
) -> dict[str, list[tuple[float, float, float]]]:
    """Return END-POINT plus explicit BRANCHn-POINT ports for fittings."""

    lines = Path(pcf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    result: dict[str, list[tuple[float, float, float]]] = {}
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        uci = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        if uci is None:
            continue
        ports = [
            tuple(float(value) for value in match)
            for match in re.findall(
                r"(?:END-POINT|BRANCH\d*-POINT)\s+"
                r"([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                block,
            )
        ]
        if ports:
            result[uci.group(1).upper()] = ports
    return result


def _pcf_fpfl_records_by_uci(
    pcf_path: Path,
) -> dict[str, dict[str, object]]:
    """Return explicit flanged-pipe pieces used for ordered chain recovery."""

    lines = Path(pcf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    result: dict[str, dict[str, object]] = {}
    for position, start in enumerate(starts):
        kind = lines[start].strip()
        if kind != "PIPE-FIXED":
            continue
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        if re.search(r"\bSKEY\s+FPFL\b", block) is None:
            continue
        uci = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        endpoints = [
            tuple(float(value) for value in match)
            for match in re.findall(
                r"END-POINT\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                block,
            )
        ]
        if uci is None or len(endpoints) != 2:
            continue
        cut_length = re.search(r"\bCUT-PIECE-LENGTH\s+([-+\d.]+)", block)
        result[uci.group(1).upper()] = {
            "kind": kind,
            "endpoints": endpoints,
            "cut_length": float(cut_length.group(1)) if cut_length else None,
        }
    return result


def _pcf_multiport_fitting_interfaces(
    pcf_path: Path,
) -> dict[str, list[dict[str, object]]]:
    """Return flanged fitting ports with their adjacent component and bolt length."""

    lines = Path(pcf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    blocks: list[dict[str, object]] = []
    for position, start in enumerate(starts):
        kind = lines[start].strip()
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        uci_match = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        endpoints = [
            tuple(float(value) for value in match)
            for match in re.findall(
                r"(?:END-POINT|BRANCH\d+-POINT)\s+"
                r"([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                block,
            )
        ]
        coordinate_match = re.search(
            r"\bCO-ORDS\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
            block,
        )
        bolt_length_match = re.search(r"\bBOLT-LENGTH\s+([-+\d.]+)", block)
        blocks.append({
            "kind": kind,
            "uci": uci_match.group(1).upper() if uci_match else "",
            "endpoints": endpoints,
            "coordinate": (
                tuple(float(coordinate_match.group(index)) for index in range(1, 4))
                if coordinate_match
                else None
            ),
            "bolt_length": (
                float(bolt_length_match.group(1)) if bolt_length_match else None
            ),
        })

    auxiliary_kinds = {"GASKET", "BOLT", "SUPPORT", "WELD", "FLOW-ARROW"}
    result: dict[str, list[dict[str, object]]] = {}
    for fitting in blocks:
        kind = str(fitting["kind"])
        ports = list(fitting["endpoints"])
        fitting_uci = str(fitting["uci"])
        if (
            not fitting_uci
            or len(ports) < 2
            or kind in auxiliary_kinds
            or kind in {"PIPE", "PIPE-FIXED", "PIPE-BLOCK-FIXED", "FLANGE", "LAPJOINT-STUBEND"}
        ):
            continue
        interfaces: list[dict[str, object]] = []
        for port in ports:
            gaskets = [
                block
                for block in blocks
                if block["kind"] == "GASKET"
                and any(math.dist(port, endpoint) <= 5.0 for endpoint in block["endpoints"])
            ]
            if len(gaskets) != 1:
                interfaces = []
                break
            gasket = gaskets[0]
            gasket_points = list(gasket["endpoints"])
            other_point = max(gasket_points, key=lambda point: math.dist(port, point))
            adjacent = [
                block
                for block in blocks
                if block["kind"] not in auxiliary_kinds
                and block["uci"]
                and block["uci"] != fitting_uci
                and any(
                    math.dist(other_point, endpoint) <= 5.0
                    for endpoint in block["endpoints"]
                )
            ]
            adjacent_ucis = {str(block["uci"]) for block in adjacent}
            # A flanged fitting port at a page/line termination legitimately
            # has its gasket and bolt but no following PCF component.  Keep
            # that physical port with an empty adjacent UCI so a second,
            # proven fitting face can orient it in SHA.  Multiple adjacent
            # components remain ambiguous and invalidate the fitting.
            if len(adjacent_ucis) > 1:
                interfaces = []
                break
            bolts = [
                block
                for block in blocks
                if block["kind"] == "BOLT"
                and block["coordinate"] is not None
                and (
                    math.dist(port, block["coordinate"]) <= 5.0
                    or math.dist(other_point, block["coordinate"]) <= 5.0
                )
            ]
            bolt_lengths = {
                float(block["bolt_length"])
                for block in bolts
                if block["bolt_length"] is not None
            }
            if len(bolt_lengths) > 1:
                interfaces = []
                break
            interfaces.append({
                "port": port,
                "adjacent_uci": next(iter(adjacent_ucis), ""),
                "bolt_length": next(iter(bolt_lengths)) if bolt_lengths else None,
            })
        if len(interfaces) == len(ports):
            result[fitting_uci] = interfaces
    return result


def _bbox_gap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    dx = max(first[0] - second[2], second[0] - first[2], 0.0)
    dy = max(first[1] - second[3], second[1] - first[3], 0.0)
    return math.hypot(dx, dy)


def _direct_graphic_interface_anchor(
    trace: dict[str, object],
    first_graphic_ref: int,
    second_graphic_ref: int,
) -> tuple[float, float] | None:
    """Locate a component interface from the actual SHA strokes.

    A PSM graphic's bounding box may include stems, operators, or annotation
    strokes far away from its physical flange face. For adjacent components,
    the two pipe-outline endpoint pairs nearest each other define that face.
    """

    def points_for(graphic_ref: int) -> list[tuple[float, float]]:
        wanted = graphic_ref & 0xFFFF
        points: set[tuple[float, float]] = set()
        for segment in trace.get("segments", []):
            if segment.get("mapping_basis") != "direct_object_ref":
                continue
            raw_ref = segment.get("graphic_ref_low16")
            try:
                actual = int(str(raw_ref), 0) & 0xFFFF
            except (TypeError, ValueError):
                continue
            if actual != wanted:
                continue
            for raw_point in segment.get("sheet_points_normalized", [])[:2]:
                point = (
                    float(raw_point[0]) * SHEET_UNIT,
                    float(raw_point[1]) * SHEET_UNIT,
                )
                points.add(
                    (round(point[0], 5), round(point[1], 5))
                )
        return sorted(points)

    first_points = points_for(first_graphic_ref)
    second_points = points_for(second_graphic_ref)
    if len(first_points) < 2 or len(second_points) < 2:
        return None

    nearest_second = {
        first: min(second_points, key=lambda second: math.dist(first, second))
        for first in first_points
    }
    nearest_first = {
        second: min(first_points, key=lambda first: math.dist(first, second))
        for second in second_points
    }
    pairs: list[tuple[float, tuple[float, float]]] = []
    for first, second in nearest_second.items():
        if nearest_first.get(second) != first:
            continue
        distance = math.dist(first, second)
        midpoint = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
        pairs.append((distance, midpoint))
    if len(pairs) < 2:
        return None

    minimum = min(distance for distance, _midpoint in pairs)
    nearby = [
        pair for pair in pairs
        if pair[0] <= max(12.0, minimum * 2.25 + 2.0)
    ]
    if len(nearby) < 2:
        return None

    # When a centreline is also present, the two outline pairs are the
    # nearby candidates whose midpoints are furthest apart.
    choices: list[tuple[float, float, tuple[float, float], tuple[float, float]]] = []
    for index, (first_distance, first_midpoint) in enumerate(nearby):
        for second_distance, second_midpoint in nearby[index + 1:]:
            separation = math.dist(first_midpoint, second_midpoint)
            if 8.0 <= separation <= 400.0:
                choices.append((
                    separation,
                    -(first_distance + second_distance),
                    first_midpoint,
                    second_midpoint,
                ))
    if not choices:
        return None
    _separation, _distance_score, first_midpoint, second_midpoint = max(choices)
    return (
        (first_midpoint[0] + second_midpoint[0]) / 2,
        (first_midpoint[1] + second_midpoint[1]) / 2,
    )


def _opposite_graphic_port_anchor(
    trace: dict[str, object],
    graphic_ref: int,
    known_port: tuple[float, float],
) -> tuple[float, float] | None:
    """Return the far physical face of one visible two-port component."""

    wanted = graphic_ref & 0xFFFF
    points: set[tuple[float, float]] = set()
    for segment in trace.get("segments", []):
        if segment.get("mapping_basis") != "direct_object_ref":
            continue
        try:
            actual = int(str(segment.get("graphic_ref_low16")), 0) & 0xFFFF
        except (TypeError, ValueError):
            continue
        if actual != wanted:
            continue
        points.update(
            (
                round(float(raw[0]) * SHEET_UNIT, 5),
                round(float(raw[1]) * SHEET_UNIT, 5),
            )
            for raw in segment.get("sheet_points_normalized", [])[:2]
        )
    if len(points) < 4:
        return None

    centre = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )
    axis = (centre[0] - known_port[0], centre[1] - known_port[1])
    axis_length = math.hypot(*axis)
    if axis_length <= 1e-6:
        return None
    unit = (axis[0] / axis_length, axis[1] / axis_length)
    projections = [
        ((point[0] - known_port[0]) * unit[0] + (point[1] - known_port[1]) * unit[1], point)
        for point in points
    ]
    minimum = min(value for value, _point in projections)
    maximum = max(value for value, _point in projections)
    span = maximum - minimum
    if span <= 30.0:
        return None
    far_points = [
        point for value, point in projections
        if value >= maximum - span * 0.10
    ]
    if not 2 <= len(far_points) <= 8:
        return None
    return (
        sum(point[0] for point in far_points) / len(far_points),
        sum(point[1] for point in far_points) / len(far_points),
    )


def _owned_flange_faces(trace: dict[str, object], graphic_ref: int) -> list[tuple[float, float]]:
    """Recover small closed flange parallelograms, including dashed edges.

    Ownership comes from the exact graphic ref, not the spatial UCI hint.
    Join collinear fragments before checking four closed edges; a pipe axis
    or a support stroke alone cannot qualify as a flange face.
    """
    groups: list[dict[str, object]] = []
    for segment in trace.get("segments", []):
        if int(str(segment.get("graphic_ref_low16", "0")), 0) != (graphic_ref & 0xFFFF):
            continue
        raw = segment.get("sheet_points_normalized", [])
        if len(raw) != 2:
            continue
        a, b = [tuple(float(v) * SHEET_UNIT for v in p) for p in raw]
        length = math.dist(a, b)
        if length < 2:
            continue
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        if ux < -0.001 or (abs(ux) <= 0.001 and uy < 0):
            ux, uy = -ux, -uy
        match = None
        for group in groups:
            vx, vy = group["axis"]
            if abs(ux * vy - uy * vx) < 0.035 and max(
                abs(-vy * p[0] + vx * p[1] - group["offset"]) for p in (a, b)
            ) < 2.0:
                match = group
                break
        if match is None:
            match = {"axis": (ux, uy), "offset": -uy * a[0] + ux * a[1], "intervals": []}
            groups.append(match)
        vx, vy = match["axis"]
        match["intervals"].append(sorted([vx * p[0] + vy * p[1] for p in (a, b)]))
    edges = []
    for group in groups:
        intervals = []
        for lo, hi in sorted(group["intervals"]):
            if intervals and lo - intervals[-1][1] <= 19.0:
                intervals[-1][1] = max(hi, intervals[-1][1])
            else:
                intervals.append([lo, hi])
        ux, uy = group["axis"]
        d = group["offset"]
        for lo, hi in intervals:
            if 4 <= hi - lo <= 180:
                edges.append(((ux * lo - uy * d, uy * lo + ux * d),
                              (ux * hi - uy * d, uy * hi + ux * d)))
    faces = []
    for i, (a, b) in enumerate(edges):
        if math.dist(a, b) < 30:
            continue
        for c, d in edges[i + 1:]:
            if math.dist(c, d) < 30:
                continue
            if math.dist(a, d) + math.dist(b, c) < math.dist(a, c) + math.dist(b, d):
                c, d = d, c
            if not (7 <= math.dist(a, c) <= 32 and 7 <= math.dist(b, d) <= 32):
                continue
            def closed(p, q):
                for x, y in edges:
                    length = math.dist(x, y)
                    off = max(abs((y[0] - x[0]) * (v[1] - x[1]) -
                                  (y[1] - x[1]) * (v[0] - x[0])) / length for v in (p, q))
                    if off < 2.5 and min(max(math.dist(p, x), math.dist(q, y)),
                                        max(math.dist(p, y), math.dist(q, x))) < 12:
                        return True
                return False
            if closed(a, c) and closed(b, d):
                centre = (sum(p[0] for p in (a, b, c, d)) / 4,
                          sum(p[1] for p in (a, b, c, d)) / 4)
                if all(math.dist(centre, old) > 5 for old in faces):
                    faces.append(centre)
    return faces


def _support_coordinate_anchor(
    record: ManagementInput,
    visible_regions: list[dict[str, object]],
    trace: dict[str, object],
    pcf_path: Path | None,
) -> tuple[tuple[float, float], tuple[int, ...], str] | None:
    if record.coordinate is None or pcf_path is None:
        return None
    pipe_projection = _pcf_pipe_projection(pcf_path, record.coordinate)
    if pipe_projection is None:
        component = _pcf_component_centre(pcf_path, record.coordinate)
        if component is None:
            return None
        _kind, component_uci = component
        component_regions = [
            region
            for region in trace.get("uci_regions", [])
            if str(region.get("uci", "")).upper() == component_uci
        ]
        if not component_regions:
            return None
        boxes = [
            tuple(float(value) for value in region["bbox"])
            for region in component_regions
        ]
        union = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
        refs = tuple(
            sorted({int(region["graphic_ref"]) for region in component_regions})
        )
        return _bbox_center(union), refs, "support-coordinate-component-centre"
    pipe_uci, fraction = pipe_projection
    pipe_segments = [
        segment
        for segment in trace.get("segments", [])
        if pipe_uci in {str(uci).upper() for uci in segment.get("uci_candidates", [])}
        and segment.get("mapping_basis") == "direct_object_ref"
    ]
    if not pipe_segments:
        pipe_segments = [
            segment
            for segment in trace.get("segments", [])
            if pipe_uci in {str(uci).upper() for uci in segment.get("uci_candidates", [])}
        ]
    if not pipe_segments:
        return None
    segment = max(
        pipe_segments,
        key=lambda item: math.dist(*[
            tuple(float(value) for value in point)
            for point in item["sheet_points_normalized"][:2]
        ]),
    )
    first, second = [
        (float(point[0]) * SHEET_UNIT, float(point[1]) * SHEET_UNIT)
        for point in segment["sheet_points_normalized"][:2]
    ]
    anchor = (
        first[0] + fraction * (second[0] - first[0]),
        first[1] + fraction * (second[1] - first[1]),
    )
    if not visible_regions:
        return anchor, (), "support-coordinate-pipe-projection"
    closest = min(
        visible_regions,
        key=lambda region: math.dist(anchor, _bbox_center(tuple(float(v) for v in region["bbox"]))),
    )
    return anchor, (int(closest["graphic_ref"]),), "support-coordinate-pipe-projection"


def resolve_management_targets(
    records: list[ManagementInput],
    trace: dict[str, object],
    pcf_path: Path | None = None,
    weld_map: dict[str, object] | None = None,
    project_profile: str = "000200fw",
) -> tuple[list[ManagementTarget], list[dict[str, object]]]:
    by_uci: dict[str, list[dict[str, object]]] = {}
    for region in trace.get("uci_regions", []):
        uci = str(region.get("uci", "")).upper()
        if uci:
            by_uci.setdefault(uci, []).append(region)

    mapped: list[ManagementTarget] = []
    unresolved: list[dict[str, object]] = []
    for record in records:
        confidence = "high"
        region_groups = [by_uci.get(uci.upper(), []) for uci in record.ucis]
        visible = [region for group in region_groups for region in group]
        if record.kind == "support":
            all_members_visible = bool(region_groups) and all(region_groups)
            repeated_same_uci = any(len(group) > 1 for group in region_groups)
            needs_coordinate = (
                not visible or not all_members_visible or repeated_same_uci
            )
            support_projection = (
                _support_coordinate_anchor(record, visible, trace, pcf_path)
                if needs_coordinate
                else None
            )
            if support_projection is not None:
                anchor, refs, projection_method = support_projection
                if not visible:
                    method = f"{projection_method}-no-sha-uci"
                elif not all_members_visible:
                    method = f"{projection_method}-partial-sha-uci"
                else:
                    method = projection_method
            elif not visible or not all_members_visible:
                weld_candidates: list[dict[str, object]] = []
                if record.coordinate is not None and weld_map is not None:
                    for row in weld_map.get("welds", []):
                        endpoint = row.get("pcf_endpoint")
                        if (
                            int(row.get("page", -1)) != int(record.page)
                            or row.get("graphic_ref") is None
                            or row.get("sha_x") is None
                            or row.get("sha_y") is None
                            or not isinstance(endpoint, list)
                            or len(endpoint) < 3
                        ):
                            continue
                        if math.dist(
                            record.coordinate,
                            tuple(float(value) for value in endpoint[:3]),
                        ) <= 5.0:
                            weld_candidates.append(row)
                weld_roots = {
                    (
                        int(row["graphic_ref"]),
                        round(float(row["sha_x"]), 3),
                        round(float(row["sha_y"]), 3),
                    )
                    for row in weld_candidates
                }
                if len(weld_roots) == 1:
                    graphic_ref, x, y = next(iter(weld_roots))
                    anchor = (x, y)
                    refs = (graphic_ref,)
                    method = "support-coordinate-unique-weld-root"
                    confidence = "review"
                elif visible:
                    boxes = [
                        tuple(float(value) for value in region["bbox"])
                        for region in visible
                    ]
                    best = max(
                        boxes,
                        key=lambda box: sum(
                            math.dist(_bbox_center(box), _bbox_center(other)) <= 450.0
                            for other in boxes
                        ),
                    )
                    nearby = [
                        box
                        for box in boxes
                        if math.dist(_bbox_center(best), _bbox_center(box)) <= 450.0
                    ]
                    if len(nearby) != len(boxes):
                        unresolved.append({
                            "kind": record.kind,
                            "number": record.number,
                            "ucis": list(record.ucis),
                            "coordinate": (
                                list(record.coordinate)
                                if record.coordinate
                                else None
                            ),
                            "reason": (
                                "support UCI unavailable and coordinate projection failed"
                            ),
                            "source_rows": list(record.source_rows),
                        })
                        continue
                    union = (
                        min(box[0] for box in nearby),
                        min(box[1] for box in nearby),
                        max(box[2] for box in nearby),
                        max(box[3] for box in nearby),
                    )
                    anchor = _bbox_center(union)
                    refs = tuple(
                        sorted(
                            {
                                int(region["graphic_ref"])
                                for region in visible
                                if tuple(float(value) for value in region["bbox"])
                                in nearby
                            }
                        )
                    )
                    method = "support-partial-uci-cluster"
                    confidence = "review"
                else:
                    unresolved.append({
                        "kind": record.kind,
                        "number": record.number,
                        "ucis": list(record.ucis),
                        "coordinate": list(record.coordinate) if record.coordinate else None,
                        "reason": "support UCI unavailable and coordinate projection failed",
                        "source_rows": list(record.source_rows),
                    })
                    continue
            else:
                boxes = [tuple(float(value) for value in region["bbox"]) for region in visible]
                best = max(
                    boxes,
                    key=lambda box: sum(
                        math.dist(_bbox_center(box), _bbox_center(other)) <= 450.0
                        for other in boxes
                    ),
                )
                nearby = [
                    box for box in boxes
                    if math.dist(_bbox_center(best), _bbox_center(box)) <= 450.0
                ]
                union = (
                    min(box[0] for box in nearby), min(box[1] for box in nearby),
                    max(box[2] for box in nearby), max(box[3] for box in nearby),
                )
                anchor = _bbox_center(union)
                refs = tuple(sorted({
                    int(region["graphic_ref"])
                    for region in visible
                    if tuple(float(value) for value in region["bbox"]) in nearby
                }))
                method = "support-uci-cluster"
        elif len(record.ucis) == 2:
            if not all(region_groups):
                unresolved.append({
                    "kind": record.kind,
                    "number": record.number,
                    "ucis": list(record.ucis),
                    "reason": "paired UCI not visible on this SHA page",
                    "source_rows": list(record.source_rows),
                })
                continue
            first = tuple(float(value) for value in region_groups[0][0]["bbox"])
            second = tuple(float(value) for value in region_groups[1][0]["bbox"])
            anchor = _interface_between_boxes(first, second)
            refs = tuple(int(group[0]["graphic_ref"]) for group in region_groups)
            method = "paired-uci-interface"
        else:
            if not visible:
                unresolved.append({
                    "kind": record.kind,
                    "number": record.number,
                    "ucis": list(record.ucis),
                    "coordinate": list(record.coordinate) if record.coordinate else None,
                    "reason": "UCI not visible on this SHA page",
                    "source_rows": list(record.source_rows),
                })
                continue
            boxes = [tuple(float(value) for value in region["bbox"]) for region in visible]
            union = (
                min(box[0] for box in boxes), min(box[1] for box in boxes),
                max(box[2] for box in boxes), max(box[3] for box in boxes),
            )
            anchor = _bbox_center(union)
            refs = tuple(sorted({int(region["graphic_ref"]) for region in visible}))
            method = "direct-uci-region"
        mapped.append(
            ManagementTarget(
                record.kind, record.number, record.sheet_id, record.page,
                record.ucis, anchor, method, confidence, refs,
            )
        )

    if project_profile == "000200fw":
        # Frozen 000200FW behaviour: recover only one strictly remaining
        # FLANGE, using the original interface-weld rule.
        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]
        input_flanges = [record for record in records if record.kind == "flange"]
        mapped_flanges = [target for target in mapped if target.kind == "flange"]
        if (
            pcf_path is not None
            and weld_map is not None
            and len(unresolved_flanges) == 1
            and len(mapped_flanges) + 1 == len(input_flanges)
        ):
            missing = unresolved_flanges[0]
            missing_record = next(
                (
                    record
                    for record in input_flanges
                    if record.number == missing["number"]
                    and list(record.ucis) == missing["ucis"]
                ),
                None,
            )
            flange_endpoints = _pcf_component_endpoints_by_uci(pcf_path, "FLANGE")
            endpoints = [
                endpoint
                for uci in missing["ucis"]
                for endpoint in flange_endpoints.get(str(uci).upper(), [])
            ]
            candidates = [
                row
                for row in weld_map.get("welds", [])
                if int(row.get("page", -1))
                == int(missing_record.page if missing_record else -1)
                and row.get("sha_x") is not None
                and row.get("sha_y") is not None
                and row.get("pcf_endpoint") is not None
                and any(
                    math.dist(
                        tuple(float(value) for value in row["pcf_endpoint"][:3]),
                        endpoint,
                    )
                    <= 5.0
                    for endpoint in endpoints
                )
            ]
            if missing_record is not None and len(candidates) == 1:
                row = candidates[0]
                mapped.append(
                    ManagementTarget(
                        missing_record.kind,
                        missing_record.number,
                        missing_record.sheet_id,
                        missing_record.page,
                        missing_record.ucis,
                        (float(row["sha_x"]), float(row["sha_y"])),
                        "unique-pcf-flange-interface-weld",
                        "high",
                        (int(row["graphic_ref"]),),
                    )
                )
                unresolved.remove(missing)
        return mapped, unresolved

    # 100200 construction profile: SHA exports can omit flange-family UCIs
    # (especially LAPJOINT-STUBEND).
    # even though the PCF retains the component and its interface weld.  A
    # unique weld whose PCF endpoint exactly coincides with an explicit
    # component endpoint is direct topology evidence, not a nearest-symbol
    # guess, and is safe to resolve independently for multiple components.
    unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]
    input_flanges = [record for record in records if record.kind == "flange"]

    # One flanged fitting UCI can legitimately own one management number per
    # physical flange face.  This includes three-port TEFL fittings as well as
    # two-port ELFL elbows.  Some table rows repeat only the fitting UCI while
    # others contain fitting+adjacent PIPE-FIXED UCIs.  Treat all such rows as
    # one port-assignment problem: explicit adjacent UCIs take precedence,
    # bolt signatures split otherwise identical ports, and a sole remaining
    # port is inferred only after every directly proven port is assigned.
    # The component envelope centre is never a valid flange-face anchor.
    if pcf_path is not None:
        fitting_interfaces = _pcf_multiport_fitting_interfaces(pcf_path)
        fitting_records_by_uci: dict[str, list[ManagementInput]] = {}
        for record in input_flanges:
            for raw_uci in record.ucis:
                fitting_uci = raw_uci.upper()
                if fitting_uci in fitting_interfaces:
                    fitting_records_by_uci.setdefault(fitting_uci, []).append(record)
        for fitting_uci, raw_fitting_records in fitting_records_by_uci.items():
            fitting_records = list({record.key: record for record in raw_fitting_records}.values())
            interfaces = fitting_interfaces.get(fitting_uci, [])
            fitting_regions = by_uci.get(fitting_uci, [])
            mapped_keys_before_fitting = {target.key for target in mapped}
            if (
                len(fitting_records) < 2
                or len(interfaces) != len(fitting_records)
                or len(fitting_regions) != 1
            ):
                continue
            assignments: dict[str, dict[str, object]] = {}
            used_ports: set[int] = set()
            # Rows naming their adjacent component are topological facts and
            # must reserve their ports before single-UCI/remainder rows.
            assignment_order = sorted(
                fitting_records,
                key=lambda record: (len(record.ucis) < 2, record.number.casefold()),
            )
            for record in assignment_order:
                other_ucis = {
                    uci.upper() for uci in record.ucis if uci.upper() != fitting_uci
                }
                explicit = [
                    (index, interface)
                    for index, interface in enumerate(interfaces)
                    if index not in used_ports
                    and str(interface["adjacent_uci"]).upper() in other_ucis
                ]
                if len(explicit) == 1:
                    port_index, interface = explicit[0]
                    used_ports.add(port_index)
                    assignments[record.key] = interface
                    continue
                if other_ucis:
                    assignments = {}
                    break
                bolt_spec = record.attribute("螺栓规格")
                length_match = re.search(r"[Xx×]\s*([-+\d.]+)\s*$", bolt_spec)
                wanted_length = float(length_match.group(1)) if length_match else None
                candidates = [
                    (index, interface)
                    for index, interface in enumerate(interfaces)
                    if index not in used_ports
                    and (
                        (
                            wanted_length is None
                            and interface["bolt_length"] is None
                        )
                        or (
                            wanted_length is not None
                            and interface["bolt_length"] is not None
                            and abs(wanted_length - float(interface["bolt_length"])) <= 0.1
                        )
                    )
                ]
                # If a bolt signature is absent/non-unique, a final single
                # unused port is still an exact remainder of this fitting.
                if len(candidates) != 1:
                    candidates = [
                        (index, interface)
                        for index, interface in enumerate(interfaces)
                        if index not in used_ports
                    ]
                if len(candidates) != 1:
                    assignments = {}
                    break
                port_index, interface = candidates[0]
                used_ports.add(port_index)
                assignments[record.key] = interface
            if len(assignments) != len(fitting_records):
                continue

            fitting_region = fitting_regions[0]
            interface_anchors: dict[str, tuple[float, float]] = {}
            interface_refs: dict[str, tuple[int, ...]] = {}
            missing_adjacent_region = False
            direct_anchor_count = 0
            for record in fitting_records:
                adjacent_uci = str(assignments[record.key]["adjacent_uci"]).upper()
                adjacent_regions = by_uci.get(adjacent_uci, [])
                if len(adjacent_regions) != 1:
                    missing_adjacent_region = True
                    continue
                adjacent_region = adjacent_regions[0]
                anchor = _direct_graphic_interface_anchor(
                    trace,
                    int(fitting_region["graphic_ref"]),
                    int(adjacent_region["graphic_ref"]),
                )
                if anchor is not None:
                    direct_anchor_count += 1
                else:
                    fitting_box = tuple(
                        float(value) for value in fitting_region["bbox"]
                    )
                    adjacent_box = tuple(
                        float(value) for value in adjacent_region["bbox"]
                    )
                    # Only refine already established visible rows with the
                    # compact/overlap flange envelope rule.  If this fitting
                    # solve is also recovering a hidden management row, keep
                    # the established generic interface basis so later
                    # opposite-port topology retains its original priority.
                    if all(
                        item.key in mapped_keys_before_fitting
                        for item in fitting_records
                    ):
                        anchor = _flange_interface_between_boxes(
                            fitting_box,
                            adjacent_box,
                        )
                    else:
                        anchor = _interface_between_boxes(
                            fitting_box,
                            adjacent_box,
                        )
                interface_anchors[record.key] = anchor
                interface_refs[record.key] = tuple(sorted({
                    int(fitting_region["graphic_ref"]),
                    int(adjacent_region["graphic_ref"]),
                }))
            # A two-port elbow may end at this drawing/page and therefore have
            # no visible adjacent component on one side.  Once the other face
            # is proven directly, derive the far face from the elbow's own SHA
            # strokes; this is the same geometric principle used for hidden
            # flanged fittings, not a bbox offset.
            if len(interfaces) == 2 and len(interface_anchors) == 1:
                missing_record = next(
                    record for record in fitting_records
                    if record.key not in interface_anchors
                )
                known_anchor = next(iter(interface_anchors.values()))
                opposite = _opposite_graphic_port_anchor(
                    trace,
                    int(fitting_region["graphic_ref"]),
                    known_anchor,
                )
                if opposite is not None:
                    interface_anchors[missing_record.key] = opposite
                    interface_refs[missing_record.key] = (
                        int(fitting_region["graphic_ref"]),
                    )
            if len(interface_anchors) != len(fitting_records):
                continue

            replaced_keys = set(assignments)
            mapped = [target for target in mapped if target.key not in replaced_keys]
            unresolved = [
                row for row in unresolved
                if f"{row['kind']}:{row['number']}" not in replaced_keys
            ]
            for record in fitting_records:
                mapped.append(
                    ManagementTarget(
                        record.kind,
                        record.number,
                        record.sheet_id,
                        record.page,
                        record.ucis,
                        interface_anchors[record.key],
                        "pcf-multiport-fitting-bolt-interface",
                        "high",
                        interface_refs[record.key],
                    )
                )

        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]

    # EP3D can regenerate the UCIs of every internal PIPE-FIXED/FPFL piece
    # while retaining the same straight, ordered flange-pipe chain in SHA.
    # Reconcile such a chain only when its PCF management graph is an
    # unbranched path, one endpoint UCI still agrees with SHA, and the SHA
    # exposes one unique, same-family geometric path of the required length.
    # This is deliberately separate from ordinary flange and fitting rules.
    if pcf_path is not None and unresolved_flanges:
        fpfl_records = _pcf_fpfl_records_by_uci(pcf_path)
        fpfl_edges: list[tuple[ManagementInput, str, str]] = []
        fpfl_adjacency: dict[str, set[str]] = {}
        for record in input_flanges:
            if len(record.ucis) != 2:
                continue
            first, second = (uci.upper() for uci in record.ucis)
            if first not in fpfl_records or second not in fpfl_records:
                continue
            fpfl_edges.append((record, first, second))
            fpfl_adjacency.setdefault(first, set()).add(second)
            fpfl_adjacency.setdefault(second, set()).add(first)

        remaining_nodes = set(fpfl_adjacency)
        while remaining_nodes:
            first_node = next(iter(remaining_nodes))
            component: set[str] = set()
            pending = [first_node]
            while pending:
                node = pending.pop()
                if node in component:
                    continue
                component.add(node)
                pending.extend(fpfl_adjacency.get(node, set()) - component)
            remaining_nodes -= component
            component_edges = [
                edge for edge in fpfl_edges if edge[1] in component and edge[2] in component
            ]
            endpoints = [
                node for node in component if len(fpfl_adjacency.get(node, set())) == 1
            ]
            if (
                len(component) < 2
                or len(component_edges) != len(component) - 1
                or len(endpoints) != 2
                or any(len(fpfl_adjacency.get(node, set())) > 2 for node in component)
            ):
                continue
            visible_nodes = [node for node in component if len(by_uci.get(node, [])) == 1]
            if len(visible_nodes) != 1 or visible_nodes[0] not in endpoints:
                continue
            seed_node = visible_nodes[0]

            pcf_order = [seed_node]
            previous_node: str | None = None
            while len(pcf_order) < len(component):
                candidates = fpfl_adjacency[pcf_order[-1]] - (
                    {previous_node} if previous_node is not None else set()
                )
                if len(candidates) != 1:
                    pcf_order = []
                    break
                next_node = next(iter(candidates))
                previous_node, next_node_previous = pcf_order[-1], next_node
                pcf_order.append(next_node_previous)
            if len(pcf_order) != len(component):
                continue

            seed_match = re.match(r"\{([0-9A-F]{8})-", seed_node)
            seed_family = seed_match.group(1) if seed_match else None
            sha_candidates: list[dict[str, object]] = []
            for uci, regions in by_uci.items():
                if len(regions) != 1:
                    continue
                family_match = re.match(r"\{([0-9A-F]{8})-", uci)
                if seed_family is not None and (
                    family_match is None or family_match.group(1) != seed_family
                ):
                    continue
                sha_candidates.append(regions[0])
            seed_region = by_uci[seed_node][0]
            region_by_ref = {
                int(region["graphic_ref"]): region
                for region in sha_candidates
                if region.get("graphic_ref") is not None
            }
            seed_ref = int(seed_region["graphic_ref"])
            if seed_ref not in region_by_ref:
                continue
            region_adjacency: dict[int, set[int]] = {
                ref: set() for ref in region_by_ref
            }
            region_items = list(region_by_ref.items())
            for index, (left_ref, left_region) in enumerate(region_items):
                left_box = tuple(float(value) for value in left_region["bbox"])
                for right_ref, right_region in region_items[index + 1:]:
                    right_box = tuple(float(value) for value in right_region["bbox"])
                    if _bbox_gap(left_box, right_box) <= 1.0:
                        region_adjacency[left_ref].add(right_ref)
                        region_adjacency[right_ref].add(left_ref)

            sha_path = [seed_ref]
            previous_ref: int | None = None
            while len(sha_path) < len(component):
                candidates = region_adjacency.get(sha_path[-1], set()) - set(sha_path)
                if len(candidates) != 1:
                    sha_path = []
                    break
                next_ref = next(iter(candidates))
                previous_ref, next_ref_previous = sha_path[-1], next_ref
                sha_path.append(next_ref_previous)
            if len(sha_path) != len(component):
                continue

            centres = [
                _bbox_center(tuple(float(value) for value in region_by_ref[ref]["bbox"]))
                for ref in sha_path
            ]
            vectors = [
                (right[0] - left[0], right[1] - left[1])
                for left, right in zip(centres, centres[1:])
            ]
            if any(math.hypot(*vector) <= 1e-6 for vector in vectors):
                continue
            first_vector = vectors[0]
            first_length = math.hypot(*first_vector)
            if any(
                (first_vector[0] * vector[0] + first_vector[1] * vector[1])
                / (first_length * math.hypot(*vector))
                < 0.94
                for vector in vectors[1:]
            ):
                continue

            sha_region_by_pcf_uci = {
                uci: region_by_ref[ref]
                for uci, ref in zip(pcf_order, sha_path)
            }
            unresolved_lookup = {
                (str(row["number"]), tuple(str(uci).upper() for uci in row["ucis"])): row
                for row in unresolved
                if row["kind"] == "flange"
            }
            for record, first, second in component_edges:
                missing = unresolved_lookup.get(
                    (record.number, tuple(uci.upper() for uci in record.ucis))
                )
                if missing is None:
                    continue
                first_region = sha_region_by_pcf_uci[first]
                second_region = sha_region_by_pcf_uci[second]
                first_box = tuple(float(value) for value in first_region["bbox"])
                second_box = tuple(float(value) for value in second_region["bbox"])
                mapped.append(
                    ManagementTarget(
                        record.kind,
                        record.number,
                        record.sheet_id,
                        record.page,
                        record.ucis,
                        _interface_between_boxes(first_box, second_box),
                        "pcf-sha-fpfl-chain-order-reconciliation",
                        "review",
                        tuple(sorted({
                            int(first_region["graphic_ref"]),
                            int(second_region["graphic_ref"]),
                        })),
                    )
                )
                unresolved.remove(missing)

        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]

    # A partly regenerated FPFL chain may retain two or more consecutive PCF
    # UCIs while every following PIPE-FIXED UCI is replaced.  The retained
    # centres establish both the visual axis and its direction.  Recover only
    # a unique, contiguous SHA window of the exact PCF node count; unrelated
    # collinear pipe before/after that window is thereby excluded.  Interfaces
    # touching a retained node are left to the stronger visible-component
    # rules below, while hidden-to-hidden interfaces use this ordered proof.
    if pcf_path is not None and unresolved_flanges:
        fpfl_records = _pcf_fpfl_records_by_uci(pcf_path)
        edge_records: list[tuple[ManagementInput, str, str]] = []
        pcf_adjacency: dict[str, set[str]] = {}
        for record in input_flanges:
            if len(record.ucis) != 2:
                continue
            first, second = (uci.upper() for uci in record.ucis)
            if first not in fpfl_records or second not in fpfl_records:
                continue
            edge_records.append((record, first, second))
            pcf_adjacency.setdefault(first, set()).add(second)
            pcf_adjacency.setdefault(second, set()).add(first)

        pipe_regions = {
            int(region["graphic_ref"]): region
            for regions in by_uci.values()
            for region in regions
            if str(region.get("uci", "")).upper().startswith("{0001388C-")
            and region.get("graphic_ref") is not None
        }
        remaining_nodes = set(pcf_adjacency)
        unresolved_lookup = {
            (str(row["number"]), tuple(str(uci).upper() for uci in row["ucis"])): row
            for row in unresolved
            if row["kind"] == "flange"
        }
        while remaining_nodes:
            pending = [next(iter(remaining_nodes))]
            component: set[str] = set()
            while pending:
                node = pending.pop()
                if node in component:
                    continue
                component.add(node)
                pending.extend(pcf_adjacency.get(node, set()) - component)
            remaining_nodes -= component
            endpoints = [
                node for node in component
                if len(pcf_adjacency.get(node, set()) & component) == 1
            ]
            if len(endpoints) != 2 or any(
                len(pcf_adjacency.get(node, set()) & component) > 2
                for node in component
            ):
                continue
            pcf_order = [endpoints[0]]
            while len(pcf_order) < len(component):
                candidates = (
                    pcf_adjacency[pcf_order[-1]] & component
                ) - set(pcf_order)
                if len(candidates) != 1:
                    pcf_order = []
                    break
                pcf_order.append(next(iter(candidates)))
            if not pcf_order:
                continue
            landmarks = {
                index: int(by_uci[uci][0]["graphic_ref"])
                for index, uci in enumerate(pcf_order)
                if len(by_uci.get(uci, [])) == 1
                and int(by_uci[uci][0]["graphic_ref"]) in pipe_regions
            }
            if len(landmarks) < 2:
                continue
            landmark_indices = sorted(landmarks)
            first_index, second_index = landmark_indices[:2]
            first_center = _bbox_center(tuple(
                float(value) for value in pipe_regions[landmarks[first_index]]["bbox"]
            ))
            second_center = _bbox_center(tuple(
                float(value) for value in pipe_regions[landmarks[second_index]]["bbox"]
            ))
            axis = (
                second_center[0] - first_center[0],
                second_center[1] - first_center[1],
            )
            axis_length = math.hypot(*axis)
            if axis_length <= 1e-6:
                continue
            unit = (axis[0] / axis_length, axis[1] / axis_length)
            projected: list[tuple[float, int]] = []
            # The direct FPFL centre sequence is extremely straight.  A
            # 24-unit perpendicular envelope tolerates PSM rounding while
            # excluding parallel pipe runs and title-block graphics.
            for ref, region in pipe_regions.items():
                center = _bbox_center(tuple(float(value) for value in region["bbox"]))
                offset = (center[0] - first_center[0], center[1] - first_center[1])
                along = offset[0] * unit[0] + offset[1] * unit[1]
                perpendicular = abs(offset[0] * unit[1] - offset[1] * unit[0])
                if perpendicular <= 24.0:
                    projected.append((along, ref))
            projected.sort()
            refs = [ref for _along, ref in projected]
            solutions: set[tuple[int, ...]] = set()
            for start in range(len(refs) - len(pcf_order) + 1):
                candidate = tuple(refs[start:start + len(pcf_order)])
                if all(candidate[index] == ref for index, ref in landmarks.items()):
                    solutions.add(candidate)
            if len(solutions) != 1:
                continue
            resolved_refs = next(iter(solutions))
            region_by_pcf = {
                uci: pipe_regions[ref]
                for uci, ref in zip(pcf_order, resolved_refs)
            }
            visible_pcf_nodes = set(landmarks.values())
            for record, first, second in edge_records:
                if first not in component or second not in component:
                    continue
                missing = unresolved_lookup.get(
                    (record.number, tuple(uci.upper() for uci in record.ucis))
                )
                if missing is None:
                    continue
                first_region = region_by_pcf[first]
                second_region = region_by_pcf[second]
                # Preserve the stronger direct/opposite-port rule for the
                # boundary between an original visible node and a regenerated
                # one.  This ordered rule is for hidden PIPE-FIXED pairs.
                if (
                    int(first_region["graphic_ref"]) in visible_pcf_nodes
                    or int(second_region["graphic_ref"]) in visible_pcf_nodes
                ):
                    continue
                anchor = _direct_graphic_interface_anchor(
                    trace,
                    int(first_region["graphic_ref"]),
                    int(second_region["graphic_ref"]),
                )
                if anchor is None:
                    anchor = _interface_between_boxes(
                        tuple(float(value) for value in first_region["bbox"]),
                        tuple(float(value) for value in second_region["bbox"]),
                    )
                mapped.append(ManagementTarget(
                    record.kind,
                    record.number,
                    record.sheet_id,
                    record.page,
                    record.ucis,
                    anchor,
                    "pcf-sha-fpfl-anchored-subpath-reconciliation",
                    "review",
                    tuple(sorted({
                        int(first_region["graphic_ref"]),
                        int(second_region["graphic_ref"]),
                    })),
                ))
                unresolved.remove(missing)

        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]

    # Some drawings regenerate every UCI in an otherwise unchanged FPFL
    # chain. Recover such a chain only when a visible PCF terminal neighbour
    # touches one and only one SHA pipe path with exactly the same node count.
    # A chain with a split-count mismatch (for example 20 PCF pieces versus
    # 23 SHA graphics) intentionally remains unresolved.
    if pcf_path is not None and unresolved_flanges:
        fpfl_records = _pcf_fpfl_records_by_uci(pcf_path)
        component_records = _pcf_component_records_by_uci(pcf_path)
        edge_records: list[tuple[ManagementInput, str, str]] = []
        pcf_adjacency: dict[str, set[str]] = {}
        for record in input_flanges:
            if len(record.ucis) != 2:
                continue
            first, second = (uci.upper() for uci in record.ucis)
            if first not in fpfl_records or second not in fpfl_records:
                continue
            edge_records.append((record, first, second))
            pcf_adjacency.setdefault(first, set()).add(second)
            pcf_adjacency.setdefault(second, set()).add(first)

        pipe_regions = {
            int(region["graphic_ref"]): region
            for regions in by_uci.values()
            for region in regions
            if str(region.get("uci", "")).upper().startswith("{0001388C-")
            and region.get("graphic_ref") is not None
        }
        sha_adjacency: dict[int, set[int]] = {ref: set() for ref in pipe_regions}
        sha_items = list(pipe_regions.items())
        for index, (left_ref, left_region) in enumerate(sha_items):
            left_box = tuple(float(value) for value in left_region["bbox"])
            for right_ref, right_region in sha_items[index + 1:]:
                right_box = tuple(float(value) for value in right_region["bbox"])
                if _bbox_gap(left_box, right_box) <= 1.0:
                    sha_adjacency[left_ref].add(right_ref)
                    sha_adjacency[right_ref].add(left_ref)

        def graph_components(adjacency: dict[object, set[object]]) -> list[set[object]]:
            remaining = set(adjacency)
            result: list[set[object]] = []
            while remaining:
                pending = [next(iter(remaining))]
                group: set[object] = set()
                while pending:
                    node = pending.pop()
                    if node in group:
                        continue
                    group.add(node)
                    pending.extend(adjacency.get(node, set()) - group)
                remaining -= group
                result.append(group)
            return result

        def ordered_path(
            adjacency: dict[object, set[object]],
            nodes: set[object],
        ) -> list[object]:
            if len(nodes) < 2:
                return []
            ends = [
                node for node in nodes
                if len(adjacency.get(node, set()) & nodes) == 1
            ]
            if len(ends) != 2 or any(
                len(adjacency.get(node, set()) & nodes) > 2 for node in nodes
            ):
                return []
            path = [ends[0]]
            while len(path) < len(nodes):
                candidates = (adjacency[path[-1]] & nodes) - set(path)
                if len(candidates) != 1:
                    return []
                path.append(next(iter(candidates)))
            return path

        unresolved_lookup = {
            (str(row["number"]), tuple(str(uci).upper() for uci in row["ucis"])): row
            for row in unresolved
            if row["kind"] == "flange"
        }
        for raw_component in graph_components(pcf_adjacency):
            pcf_nodes = {str(node) for node in raw_component}
            if any(by_uci.get(node) for node in pcf_nodes):
                continue
            raw_pcf_order = ordered_path(pcf_adjacency, set(pcf_nodes))
            if not raw_pcf_order:
                continue
            pcf_order = [str(node) for node in raw_pcf_order]
            solutions: set[tuple[tuple[str, ...], tuple[int, ...], bool]] = set()
            for terminal_index in (0, len(pcf_order) - 1):
                terminal_uci = pcf_order[terminal_index]
                terminal_endpoints = component_records.get(terminal_uci, ("", []))[1]
                external_visible_ucis = {
                    uci
                    for uci, (_kind, endpoints) in component_records.items()
                    if uci not in pcf_nodes
                    and len(by_uci.get(uci, [])) == 1
                    and any(
                        math.dist(first, second) <= 5.0
                        for first in terminal_endpoints
                        for second in endpoints
                    )
                }
                oriented_pcf = (
                    pcf_order if terminal_index == 0 else list(reversed(pcf_order))
                )
                for seed_uci in external_visible_ucis:
                    seed_region = by_uci[seed_uci][0]
                    seed_ref = int(seed_region["graphic_ref"])
                    seed_box = tuple(float(value) for value in seed_region["bbox"])
                    touching_refs = [
                        ref
                        for ref, region in pipe_regions.items()
                        if ref != seed_ref
                        and _bbox_gap(
                            seed_box,
                            tuple(float(value) for value in region["bbox"]),
                        ) <= 1.0
                    ]
                    for first_ref in touching_refs:
                        for raw_sha_nodes in graph_components(sha_adjacency):
                            sha_nodes = {int(node) for node in raw_sha_nodes}
                            if first_ref not in sha_nodes or len(sha_nodes) < len(oriented_pcf):
                                continue
                            raw_sha_order = ordered_path(sha_adjacency, set(sha_nodes))
                            sha_order = [int(node) for node in raw_sha_order]
                            if not sha_order:
                                continue
                            if sha_order[-1] == first_ref:
                                sha_order.reverse()
                            if sha_order[0] != first_ref:
                                continue
                            solutions.add((
                                tuple(oriented_pcf),
                                tuple(sha_order[:len(oriented_pcf)]),
                                len(sha_order) > len(oriented_pcf),
                            ))
            if len(solutions) != 1:
                continue
            resolved_pcf_order, resolved_sha_order, is_partial_subpath = next(iter(solutions))
            region_by_pcf = {
                uci: pipe_regions[ref]
                for uci, ref in zip(resolved_pcf_order, resolved_sha_order)
            }
            for record, first, second in edge_records:
                if first not in pcf_nodes or second not in pcf_nodes:
                    continue
                missing = unresolved_lookup.get(
                    (record.number, tuple(uci.upper() for uci in record.ucis))
                )
                if missing is None:
                    continue
                first_region = region_by_pcf[first]
                second_region = region_by_pcf[second]
                anchor = _direct_graphic_interface_anchor(
                    trace,
                    int(first_region["graphic_ref"]),
                    int(second_region["graphic_ref"]),
                )
                if anchor is None:
                    anchor = _interface_between_boxes(
                        tuple(float(value) for value in first_region["bbox"]),
                        tuple(float(value) for value in second_region["bbox"]),
                    )
                mapped.append(
                    ManagementTarget(
                        record.kind,
                        record.number,
                        record.sheet_id,
                        record.page,
                        record.ucis,
                        anchor,
                        (
                            "pcf-sha-fpfl-terminal-seed-reconciliation"
                            if not is_partial_subpath
                            else "pcf-sha-fpfl-anchored-subpath-reconciliation"
                        ),
                        "review",
                        tuple(sorted({
                            int(first_region["graphic_ref"]),
                            int(second_region["graphic_ref"]),
                        })),
                    )
                )
                unresolved.remove(missing)

        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]

    # A flanged fitting can own two or more managed flange interfaces while a
    # material-excluded continuation pipe has no dynamic UCI on the SHA page.
    # If another managed flange already proves one port of the same visible
    # fitting, PCF topology can assign the missing flange to a distinct port.
    # The renderer's legacy same-record UCI association is deliberately used
    # only as the otherwise-unowned neighbouring *geometry*; it is never used
    # as component identity.  Literal FLANGE/LAPJOINT and ordinary pipe blocks
    # are excluded because their apparent "opposite port" is not a flanged
    # fitting interface and is unsafe to infer this way.
    if pcf_path is not None and unresolved_flanges:
        component_records = _pcf_component_records_by_uci(pcf_path)
        legacy_by_uci: dict[str, list[dict[str, object]]] = {}
        for region in trace.get("legacy_uci_regions", []):
            uci = str(region.get("uci", "")).upper()
            if uci:
                legacy_by_uci.setdefault(uci, []).append(region)
        excluded_opposite_port_kinds = {
            "PIPE",
            "PIPE-FIXED",
            "PIPE-BLOCK-FIXED",
            "FLANGE",
            "LAPJOINT-STUBEND",
        }

        for missing in list(unresolved_flanges):
            if len(missing["ucis"]) != 2:
                continue
            missing_record = next(
                (
                    record
                    for record in input_flanges
                    if record.number == missing["number"]
                    and list(record.ucis) == missing["ucis"]
                ),
                None,
            )
            if missing_record is None:
                continue
            visible_ucis = [
                str(uci).upper()
                for uci in missing["ucis"]
                if by_uci.get(str(uci).upper())
            ]
            if len(visible_ucis) != 1:
                continue
            fitting_uci = visible_ucis[0]
            hidden_uci = next(
                str(uci).upper()
                for uci in missing["ucis"]
                if str(uci).upper() != fitting_uci
            )
            fitting_record = component_records.get(fitting_uci)
            hidden_record = component_records.get(hidden_uci)
            if fitting_record is None or hidden_record is None:
                continue
            fitting_kind, fitting_endpoints = fitting_record
            _hidden_kind, hidden_endpoints = hidden_record
            if (
                fitting_kind in excluded_opposite_port_kinds
                or len(fitting_endpoints) < 2
            ):
                continue
            hidden_ports = {
                index
                for index, endpoint in enumerate(fitting_endpoints)
                if any(math.dist(endpoint, other) <= 5.0 for other in hidden_endpoints)
            }
            if len(hidden_ports) != 1:
                continue
            hidden_port = next(iter(hidden_ports))

            peer_targets = [
                target
                for target in mapped
                if target.kind == "flange"
                and target.number != missing_record.number
                and fitting_uci in {uci.upper() for uci in target.ucis}
            ]
            occupied_ports: set[int] = set()
            peer_anchors: list[tuple[float, float]] = []
            for peer in peer_targets:
                peer_other_endpoints = [
                    endpoint
                    for uci in peer.ucis
                    if uci.upper() != fitting_uci
                    for _kind, endpoints in [component_records.get(uci.upper(), ("", []))]
                    for endpoint in endpoints
                ]
                peer_ports = {
                    index
                    for index, endpoint in enumerate(fitting_endpoints)
                    if any(
                        math.dist(endpoint, other) <= 5.0
                        for other in peer_other_endpoints
                    )
                }
                if len(peer_ports) == 1:
                    occupied_ports.update(peer_ports)
                    peer_anchors.append(peer.anchor)
            if not occupied_ports or hidden_port in occupied_ports:
                continue

            fitting_regions = by_uci.get(fitting_uci, [])
            fitting_refs = {
                int(region["graphic_ref"])
                for region in fitting_regions
                if region.get("graphic_ref") is not None
            }
            legacy_candidates = [
                region
                for region in legacy_by_uci.get(fitting_uci, [])
                if region.get("bbox") is not None
                and int(region.get("graphic_ref", -1)) not in fitting_refs
            ]
            if len(fitting_regions) != 1 or len(legacy_candidates) != 1:
                continue
            fitting_box = tuple(
                float(value) for value in fitting_regions[0]["bbox"]
            )
            neighbour_box = tuple(
                float(value) for value in legacy_candidates[0]["bbox"]
            )
            anchor = _interface_between_boxes(fitting_box, neighbour_box)
            # Reject a legacy box that merely reproduces the already mapped
            # port.  A distinct plotted port must be visibly separated.
            if peer_anchors and min(math.dist(anchor, peer) for peer in peer_anchors) <= 30.0:
                continue
            refs = tuple(sorted(fitting_refs | {int(legacy_candidates[0]["graphic_ref"])}))
            mapped.append(
                ManagementTarget(
                    missing_record.kind,
                    missing_record.number,
                    missing_record.sheet_id,
                    missing_record.page,
                    missing_record.ucis,
                    anchor,
                    "pcf-fitting-opposite-port-legacy-neighbour",
                    "review",
                    refs,
                )
            )
            unresolved.remove(missing)

        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]

    # A two-port component may retain its own SHA UCI while the component on
    # the managed flange's other side is material-excluded or regenerated.
    # When the visible component's opposite PCF port has one independently
    # visible neighbour, that known interface orients the SHA graphic and its
    # far physical face is the unique managed flange root.
    multiport_review_candidates: list[dict[str, object]] = []
    for target in mapped:
        if (
            target.kind != "flange"
            or target.mapping_method != "pcf-multiport-fitting-bolt-interface"
            or len(target.ucis) != 2
        ):
            continue
        # Re-evaluate only when the table's primary/owner UCI is the visible
        # component and the paired continuation is absent.  Reversing this
        # direction would replace a valid fitting-port solution merely
        # because its upstream neighbour is hidden.
        if (
            not by_uci.get(target.ucis[0].upper())
            or by_uci.get(target.ucis[1].upper())
        ):
            continue
        record = next(
            (item for item in input_flanges if item.key == target.key),
            None,
        )
        multiport_review_candidates.append({
            "kind": target.kind,
            "number": target.number,
            "ucis": list(target.ucis),
            "source_rows": list(record.source_rows) if record else [],
            "reason": "review stronger visible-component topology",
        })
    if pcf_path is not None and (unresolved_flanges or multiport_review_candidates):
        component_records = _pcf_component_records_by_uci(pcf_path)
        component_ports = _pcf_component_ports_by_uci(pcf_path)
        safe_two_port_kinds = {"PIPE", "PIPE-FIXED", "ELBOW"}
        for missing in list(unresolved_flanges) + multiport_review_candidates:
            if len(missing["ucis"]) != 2:
                continue
            missing_record = next(
                (
                    record
                    for record in input_flanges
                    if record.number == missing["number"]
                    and list(record.ucis) == missing["ucis"]
                ),
                None,
            )
            if missing_record is None:
                continue
            visible_ucis = [
                str(uci).upper()
                for uci in missing["ucis"]
                if len(by_uci.get(str(uci).upper(), [])) == 1
            ]
            if len(visible_ucis) != 1:
                continue
            visible_uci = visible_ucis[0]
            hidden_uci = next(
                str(uci).upper()
                for uci in missing["ucis"]
                if str(uci).upper() != visible_uci
            )
            visible_record = component_records.get(visible_uci)
            hidden_record = component_records.get(hidden_uci)
            if visible_record is None or hidden_record is None:
                continue
            visible_kind, visible_endpoints = visible_record
            _hidden_kind, hidden_endpoints = hidden_record
            if len(visible_endpoints) != 2:
                continue
            target_ports = {
                index
                for index, endpoint in enumerate(visible_endpoints)
                if any(math.dist(endpoint, other) <= 5.0 for other in hidden_endpoints)
            }
            if len(target_ports) != 1:
                continue
            target_port_index = next(iter(target_ports))
            known_port_index = 1 - target_port_index
            if visible_kind not in safe_two_port_kinds:
                if visible_kind != "TEE":
                    continue
                all_ports = component_ports.get(visible_uci, [])
                known_endpoint = visible_endpoints[known_port_index]
                target_endpoint = visible_endpoints[target_port_index]
                distances = sorted(
                    (math.dist(known_endpoint, port), port)
                    for port in all_ports
                    if math.dist(known_endpoint, port) > 5.0
                )
                if (
                    len(distances) < 2
                    or math.dist(distances[-1][1], target_endpoint) > 5.0
                    or distances[-1][0] - distances[-2][0] <= 5.0
                ):
                    continue
            known_endpoint = visible_endpoints[known_port_index]
            neighbour_ucis = {
                uci
                for uci, (_kind, endpoints) in component_records.items()
                if uci not in {visible_uci, hidden_uci}
                and any(math.dist(known_endpoint, endpoint) <= 5.0 for endpoint in endpoints)
                and len(by_uci.get(uci, [])) == 1
            }
            if len(neighbour_ucis) != 1:
                continue
            neighbour_uci = next(iter(neighbour_ucis))
            visible_region = by_uci[visible_uci][0]
            neighbour_region = by_uci[neighbour_uci][0]
            known_anchor = _direct_graphic_interface_anchor(
                trace,
                int(visible_region["graphic_ref"]),
                int(neighbour_region["graphic_ref"]),
            )
            if known_anchor is None:
                # This point is used only to orient the visible two-port
                # graphic. The emitted root still comes from its real SHA
                # strokes, so a composite without direct child references can
                # safely use the two envelopes for orientation only.
                known_anchor = _interface_between_boxes(
                    tuple(float(value) for value in visible_region["bbox"]),
                    tuple(float(value) for value in neighbour_region["bbox"]),
                )
            anchor = _opposite_graphic_port_anchor(
                trace,
                int(visible_region["graphic_ref"]),
                known_anchor,
            )
            if anchor is None or math.dist(anchor, known_anchor) <= 30.0:
                continue
            mapped.append(
                ManagementTarget(
                    missing_record.kind,
                    missing_record.number,
                    missing_record.sheet_id,
                    missing_record.page,
                    missing_record.ucis,
                    anchor,
                    "pcf-visible-component-opposite-port",
                    "review",
                    tuple(sorted({
                        int(visible_region["graphic_ref"]),
                        int(neighbour_region["graphic_ref"]),
                    })),
                )
            )
            mapped = [
                target
                for target in mapped
                if target.key == missing_record.key
                and target.mapping_method == "pcf-visible-component-opposite-port"
                or target.key != missing_record.key
            ]
            if missing in unresolved:
                unresolved.remove(missing)

        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]

    # A single-UCI managed flange can itself be absent from SHA while one of
    # its adjacent two-port components remains visible. If that neighbour's
    # other PCF port is independently visible, orient its real SHA strokes and
    # use the opposite face. This does not attempt whole-page UCI replacement.
    if pcf_path is not None and unresolved_flanges:
        component_records = _pcf_component_records_by_uci(pcf_path)
        safe_neighbour_kinds = {"PIPE", "PIPE-FIXED", "ELBOW", "VALVE"}
        for missing in list(unresolved_flanges):
            if len(missing["ucis"]) != 1:
                continue
            missing_record = next(
                (
                    record
                    for record in input_flanges
                    if record.number == missing["number"]
                    and list(record.ucis) == missing["ucis"]
                ),
                None,
            )
            missing_uci = str(missing["ucis"][0]).upper()
            hidden_record = component_records.get(missing_uci)
            if missing_record is None or hidden_record is None:
                continue
            _hidden_kind, hidden_endpoints = hidden_record
            visible_neighbours = [
                (uci, kind, endpoints)
                for uci, (kind, endpoints) in component_records.items()
                if uci != missing_uci
                and kind in safe_neighbour_kinds
                and len(endpoints) == 2
                and len(by_uci.get(uci, [])) == 1
                and any(
                    math.dist(first, second) <= 5.0
                    for first in hidden_endpoints
                    for second in endpoints
                )
            ]
            if len(visible_neighbours) != 1:
                continue
            neighbour_uci, _neighbour_kind, neighbour_endpoints = visible_neighbours[0]
            target_ports = {
                index
                for index, endpoint in enumerate(neighbour_endpoints)
                if any(math.dist(endpoint, other) <= 5.0 for other in hidden_endpoints)
            }
            if len(target_ports) != 1:
                continue
            known_endpoint = neighbour_endpoints[1 - next(iter(target_ports))]
            peer_ucis = {
                uci
                for uci, (_kind, endpoints) in component_records.items()
                if uci not in {missing_uci, neighbour_uci}
                and len(by_uci.get(uci, [])) == 1
                and any(math.dist(known_endpoint, endpoint) <= 5.0 for endpoint in endpoints)
            }
            if len(peer_ucis) != 1:
                continue
            peer_uci = next(iter(peer_ucis))
            neighbour_region = by_uci[neighbour_uci][0]
            peer_region = by_uci[peer_uci][0]
            known_anchor = _direct_graphic_interface_anchor(
                trace,
                int(neighbour_region["graphic_ref"]),
                int(peer_region["graphic_ref"]),
            )
            if known_anchor is None:
                known_anchor = _interface_between_boxes(
                    tuple(float(value) for value in neighbour_region["bbox"]),
                    tuple(float(value) for value in peer_region["bbox"]),
                )
            anchor = _opposite_graphic_port_anchor(
                trace,
                int(neighbour_region["graphic_ref"]),
                known_anchor,
            )
            if anchor is None or math.dist(anchor, known_anchor) <= 30.0:
                continue
            mapped.append(
                ManagementTarget(
                    missing_record.kind,
                    missing_record.number,
                    missing_record.sheet_id,
                    missing_record.page,
                    missing_record.ucis,
                    anchor,
                    "pcf-hidden-component-visible-neighbour-opposite-port",
                    "review",
                    tuple(sorted({
                        int(neighbour_region["graphic_ref"]),
                        int(peer_region["graphic_ref"]),
                    })),
                )
            )
            unresolved.remove(missing)

        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]

    if pcf_path is not None and weld_map is not None and unresolved_flanges:
        all_component_endpoints = _pcf_component_endpoints_by_uci(pcf_path, None)
        # A semicolon flange row names the two components on either side of
        # its gasket.  They are frequently PIPE-FIXED/ELBOW rather than literal
        # FLANGE blocks and one or both UCIs can be absent from the SHA page.
        # Their one shared PCF endpoint plus one mapped weld root is sufficient
        # direct topology evidence for the gasket location.
        for missing in list(unresolved_flanges):
            if len(missing["ucis"]) != 2:
                continue
            missing_record = next(
                (
                    record
                    for record in input_flanges
                    if record.number == missing["number"]
                    and list(record.ucis) == missing["ucis"]
                ),
                None,
            )
            if missing_record is None:
                continue
            first_endpoints = all_component_endpoints.get(
                str(missing["ucis"][0]).upper(), []
            )
            second_endpoints = all_component_endpoints.get(
                str(missing["ucis"][1]).upper(), []
            )
            shared = {
                tuple(round(value, 3) for value in first)
                for first in first_endpoints
                if any(math.dist(first, second) <= 5.0 for second in second_endpoints)
            }
            if len(shared) != 1:
                continue
            interface = next(iter(shared))
            candidates = [
                row
                for row in weld_map.get("welds", [])
                if int(row.get("page", -1)) == int(missing_record.page)
                and row.get("graphic_ref") is not None
                and row.get("sha_x") is not None
                and row.get("sha_y") is not None
                and isinstance(row.get("pcf_endpoint"), list)
                and len(row["pcf_endpoint"]) >= 3
                and math.dist(
                    tuple(float(value) for value in row["pcf_endpoint"][:3]),
                    interface,
                ) <= 5.0
            ]
            roots = {
                (
                    int(row["graphic_ref"]),
                    round(float(row["sha_x"]), 3),
                    round(float(row["sha_y"]), 3),
                )
                for row in candidates
            }
            if len(roots) != 1:
                continue
            graphic_ref, x, y = next(iter(roots))
            mapped.append(
                ManagementTarget(
                    missing_record.kind,
                    missing_record.number,
                    missing_record.sheet_id,
                    missing_record.page,
                    missing_record.ucis,
                    (x, y),
                    "pcf-paired-component-interface-weld",
                    "high",
                    (graphic_ref,),
                )
            )
            unresolved.remove(missing)

        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]
        flange_endpoints = _pcf_component_endpoints_by_uci(
            pcf_path, ("FLANGE", "LAPJOINT-STUBEND")
        )
        for missing in list(unresolved_flanges):
            missing_record = next(
                (
                    record
                    for record in input_flanges
                    if record.number == missing["number"]
                    and list(record.ucis) == missing["ucis"]
                ),
                None,
            )
            endpoints = [
                endpoint
                for uci in missing["ucis"]
                for endpoint in flange_endpoints.get(str(uci).upper(), [])
            ]
            candidates = [
                row
                for row in weld_map.get("welds", [])
                if int(row.get("page", -1)) == int(missing_record.page if missing_record else -1)
                and row.get("sha_x") is not None
                and row.get("sha_y") is not None
                and row.get("pcf_endpoint") is not None
                and any(
                    math.dist(
                        tuple(float(value) for value in row["pcf_endpoint"][:3]),
                        endpoint,
                    )
                    <= 5.0
                    for endpoint in endpoints
                )
            ]
            candidate_keys = {
                (row.get("page"), row.get("graphic_ref"), row.get("sha_x"), row.get("sha_y"))
                for row in candidates
            }
            if missing_record is not None and len(candidate_keys) == 1 and candidates:
                row = candidates[0]
                mapped.append(
                    ManagementTarget(
                        missing_record.kind,
                        missing_record.number,
                        missing_record.sheet_id,
                        missing_record.page,
                        missing_record.ucis,
                        (float(row["sha_x"]), float(row["sha_y"])),
                        "pcf-component-interface-weld",
                        "high",
                        (int(row["graphic_ref"]),),
                    )
                )
                unresolved.remove(missing)

        # A lap-joint/stub-end UCI is sometimes not serialized on the current
        # SHA page and its pipe-side endpoint is a page continuation rather
        # than an explicit weld.  In that case the interface-weld rule above
        # has no candidate.  Recover it only when one flange record remains
        # unresolved and its PCF component shares exactly one endpoint with
        # exactly one already mapped flange record.  Both labels then belong
        # to the same physical flange assembly, so the mapped peer's SHA root
        # is the only topology-proven anchor.  Keep this result reviewable.
        unresolved_flanges = [row for row in unresolved if row["kind"] == "flange"]
        mapped_flanges = [target for target in mapped if target.kind == "flange"]
        if (
            len(unresolved_flanges) == 1
            and len(mapped_flanges) + 1 == len(input_flanges)
        ):
            missing = unresolved_flanges[0]
            missing_record = next(
                (
                    record
                    for record in input_flanges
                    if record.number == missing["number"]
                    and list(record.ucis) == missing["ucis"]
                ),
                None,
            )
            missing_endpoints = [
                endpoint
                for uci in missing["ucis"]
                for endpoint in flange_endpoints.get(str(uci).upper(), [])
            ]
            adjacent: list[ManagementTarget] = []
            for target in mapped_flanges:
                target_endpoints = [
                    endpoint
                    for uci in target.ucis
                    for endpoint in flange_endpoints.get(str(uci).upper(), [])
                ]
                shared = {
                    (round(left[0], 3), round(left[1], 3), round(left[2], 3))
                    for left in missing_endpoints
                    if any(math.dist(left, right) <= 5.0 for right in target_endpoints)
                }
                if len(shared) == 1:
                    adjacent.append(target)
            if missing_record is not None and len(adjacent) == 1:
                peer = adjacent[0]
                mapped.append(
                    ManagementTarget(
                        missing_record.kind,
                        missing_record.number,
                        missing_record.sheet_id,
                        missing_record.page,
                        missing_record.ucis,
                        peer.anchor,
                        "unique-remaining-adjacent-flange-pair",
                        "review",
                        peer.graphic_refs,
                    )
                )
                unresolved.remove(missing)

    # Refine a terminal flange and any support located on the same flanged
    # pipe as one PCF/SHA port problem.  In some exports the forward-owned UCI
    # region is the *next* flange face while the legacy region is the terminal
    # face.  A single-UCI terminal row plus a paired row on the same two-port
    # PIPE-FIXED proves both ends; support coordinates can then be interpolated
    # between those real flange roots instead of an arbitrarily oriented SHA
    # line primitive.
    if pcf_path is not None:
        component_records = _pcf_component_records_by_uci(pcf_path)
        legacy_by_uci: dict[str, list[dict[str, object]]] = {}
        for region in trace.get("legacy_uci_regions", []):
            uci = str(region.get("uci", "")).upper()
            if uci:
                legacy_by_uci.setdefault(uci, []).append(region)
        record_by_key = {record.key: record for record in records}
        # Refine visible pairs only AFTER topology/remainder passes have
        # finished: changing the initial roots changes their orientation.
        for index, target in enumerate(mapped):
            if target.kind != "flange" or target.mapping_method != "paired-uci-interface":
                continue
            groups = [by_uci.get(uci.upper(), []) for uci in target.ucis]
            if len(groups) != 2 or any(len(group) != 1 for group in groups):
                continue
            anchor = _direct_graphic_interface_anchor(
                trace, int(groups[0][0]["graphic_ref"]), int(groups[1][0]["graphic_ref"])
            )
            if anchor is not None:
                mapped[index] = ManagementTarget(
                    target.kind, target.number, target.sheet_id, target.page,
                    target.ucis, anchor, "paired-uci-direct-sha-interface",
                    target.confidence, target.graphic_refs,
                )
        target_by_key = {target.key: target for target in mapped}

        for target in list(mapped):
            record = record_by_key.get(target.key)
            if (
                target.kind != "flange"
                or record is None
                or len(record.ucis) != 1
            ):
                continue
            owner_uci = record.ucis[0].upper()
            component = component_records.get(owner_uci)
            peers = [
                peer
                for peer in mapped
                if peer.kind == "flange"
                and peer.key != target.key
                and owner_uci in {uci.upper() for uci in peer.ucis}
                and len(peer.ucis) == 2
            ]
            legacy = legacy_by_uci.get(owner_uci, [])
            if (
                component is None
                or component[0] != "PIPE-FIXED"
                or len(component[1]) != 2
                or len(peers) != 1
                or len(legacy) != 1
            ):
                continue
            owners = by_uci.get(owner_uci, [])
            if len(owners) != 1:
                continue
            owner_ref = int(owners[0]["graphic_ref"])
            # This export labels some exact owner-ref strokes as decoration
            # or spatial geometry. Include them only for this proven owner;
            # never substitute the legacy association's neighbouring object.
            owner_trace = dict(trace)
            owner_trace["segments"] = [
                dict(segment, mapping_basis="direct_object_ref")
                for segment in trace.get("segments", [])
                if int(str(segment.get("graphic_ref_low16", "0")), 0) == (owner_ref & 0xFFFF)
            ]
            legacy_anchor = _opposite_graphic_port_anchor(
                owner_trace, owner_ref, peers[0].anchor
            )
            if legacy_anchor is None:
                continue
            if math.dist(legacy_anchor, peers[0].anchor) <= 30.0:
                continue
            replacement = ManagementTarget(
                target.kind,
                target.number,
                target.sheet_id,
                target.page,
                target.ucis,
                legacy_anchor,
                "pcf-fpfl-terminal-owned-graphic-port",
                "high",
                (owner_ref,),
            )
            mapped[mapped.index(target)] = replacement
            target_by_key[target.key] = replacement

        # Build the two SHA port anchors of each PIPE-FIXED from its single
        # terminal flange and paired neighbouring flange records.
        pipe_port_anchors: dict[
            str, dict[int, tuple[float, float]]
        ] = {}
        for owner_uci, (kind, endpoints) in component_records.items():
            if kind != "PIPE-FIXED" or len(endpoints) != 2:
                continue
            related_records = [
                record
                for record in records
                if record.kind == "flange"
                and owner_uci in {uci.upper() for uci in record.ucis}
                and record.key in target_by_key
            ]
            anchors: dict[int, tuple[float, float]] = {}
            singles: list[ManagementInput] = []
            for record in related_records:
                if len(record.ucis) == 1:
                    singles.append(record)
                    continue
                other_ucis = [
                    uci.upper() for uci in record.ucis if uci.upper() != owner_uci
                ]
                other_endpoints = [
                    point
                    for uci in other_ucis
                    for point in component_records.get(uci, ("", []))[1]
                ]
                ports = {
                    index
                    for index, endpoint in enumerate(endpoints)
                    if any(math.dist(endpoint, other) <= 5.0 for other in other_endpoints)
                }
                if len(ports) == 1:
                    anchors[next(iter(ports))] = target_by_key[record.key].anchor
            if len(anchors) == 1 and len(singles) == 1:
                remaining = 1 - next(iter(anchors))
                anchors[remaining] = target_by_key[singles[0].key].anchor
            if len(anchors) == 2:
                pipe_port_anchors[owner_uci] = anchors

        for target in list(mapped):
            if target.kind != "support" or not target.mapping_method.startswith(
                "support-coordinate-pipe-projection"
            ):
                continue
            record = record_by_key.get(target.key)
            if record is None or record.coordinate is None:
                continue
            visible_supports = {
                int(region["graphic_ref"]): region
                for uci in record.ucis
                for region in by_uci.get(uci.upper(), [])
            }
            if len(visible_supports) == 1:
                region = next(iter(visible_supports.values()))
                mapped[mapped.index(target)] = ManagementTarget(
                    target.kind, target.number, target.sheet_id, target.page,
                    target.ucis, _bbox_center(tuple(float(v) for v in region["bbox"])),
                    "support-unique-visible-member", "high",
                    (int(region["graphic_ref"]),),
                )
                continue
            projection = _pcf_pipe_projection(pcf_path, record.coordinate)
            if projection is None:
                continue
            owner_uci, fraction = projection
            anchors = pipe_port_anchors.get(owner_uci)
            if anchors is None:
                continue
            first, second = anchors[0], anchors[1]
            anchor = (
                first[0] + fraction * (second[0] - first[0]),
                first[1] + fraction * (second[1] - first[1]),
            )
            replacement = ManagementTarget(
                target.kind,
                target.number,
                target.sheet_id,
                target.page,
                target.ucis,
                anchor,
                "support-coordinate-between-proven-fpfl-ports",
                "high",
                target.graphic_refs,
            )
            mapped[mapped.index(target)] = replacement
    # Closed physical flange outlines are stronger than a component envelope.
    # Apply this at the end so topology inference retains its established
    # inputs. Limit it to unresolved envelope roots; previously proven graphic
    # or topology roots are not displaced.
    if pcf_path is not None:
        component_ports = _pcf_component_ports_by_uci(pcf_path)
        component_records = _pcf_component_records_by_uci(pcf_path)
        face_cache: dict[int, list[tuple[float, float]]] = {}

        def faces_for(region):
            ref = int(region["graphic_ref"])
            if ref not in face_cache:
                face_cache[ref] = _owned_flange_faces(trace, ref)
            return face_cache[ref]

        for target in list(mapped):
            if target.kind != "flange":
                continue
            anchor = None
            refs = target.graphic_refs
            if target.mapping_method == "paired-uci-interface" and len(target.ucis) == 2:
                groups = [by_uci.get(uci.upper(), []) for uci in target.ucis]
                if any(len(group) != 1 for group in groups):
                    continue
                candidates = []
                for index in (0, 1):
                    region, other = groups[index][0], groups[1 - index][0]
                    box = tuple(float(v) for v in other["bbox"])
                    near = [face for face in faces_for(region)
                            if _bbox_gap((face[0], face[1], face[0], face[1]), box) <= 40]
                    if len(near) == 1:
                        candidates.append(near[0])
                if candidates and all(math.dist(candidates[0], p) < 40 for p in candidates):
                    anchor = tuple(sum(p[i] for p in candidates) / len(candidates) for i in (0, 1))
                method = "paired-uci-closed-flange-face"
            elif target.mapping_method == "direct-uci-region" and len(target.ucis) == 1:
                uci = target.ucis[0].upper()
                regions = by_uci.get(uci, [])
                kind = component_records.get(uci, ("", []))[0]
                if len(regions) != 1 or kind not in {"PIPE-FIXED", "TEE", "ELBOW"}:
                    continue
                faces = faces_for(regions[0])
                if len(faces) not in (2, 3) or len(component_ports.get(uci, [])) != len(faces):
                    continue
                peers = [peer for peer in mapped if peer.kind == "flange"
                         and peer.key != target.key and uci in {v.upper() for v in peer.ucis}]
                occupied = set()
                for peer in peers:
                    close = [index for index, face in enumerate(faces) if math.dist(peer.anchor, face) <= 80]
                    if len(close) == 1:
                        occupied.add(close[0])
                remaining = [face for index, face in enumerate(faces) if index not in occupied]
                if len(remaining) == 1 and len(occupied) == len(faces) - 1:
                    anchor = remaining[0]
                method = "pcf-unique-remaining-owned-flange-face"
            if anchor is not None:
                mapped[mapped.index(target)] = ManagementTarget(
                    target.kind, target.number, target.sheet_id, target.page,
                    target.ucis, anchor, method, "high", refs,
                )
    return mapped, unresolved


def _point_segment_projection(
    point: tuple[float, float],
    segment: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    x, y = point
    x1, y1, x2, y2 = segment
    dx, dy = x2 - x1, y2 - y1
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-9:
        return x1, y1, math.hypot(x - x1, y - y1), 0.0
    fraction = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length_squared))
    px, py = x1 + fraction * dx, y1 + fraction * dy
    return px, py, math.hypot(x - px, y - py), fraction


def _spool_component_anchor(
    sha_path: Path,
    sheet_stream: str,
    weld_anchor: tuple[float, float],
    adjacent_component_ucis: set[str] | None = None,
) -> tuple[tuple[float, float], str]:
    """Move a mapped weld root onto one incident component line.

    The spool table identifies a shop-weld-connected component but does not
    carry the PCF/SHA UCI.  A mapped weld point is already topology verified.
    The longest decoded line incident on that point is direct page evidence
    for one of its neighbouring components, so move a short distance along
    that line and use the resulting point as the spool-label root.
    """

    from analyze_iso_split import read_sha_streams
    from sha_to_svg_prototype import line_segments, template_line_segments

    streams = read_sha_streams(sha_path)
    if sheet_stream not in streams:
        return weld_anchor, "mapped-weld-root-no-sheet"
    sheet = streams[sheet_stream]
    uci_by_ref: dict[int, set[str]] = {}
    for region in forward_owned_uci_regions(sha_path, sheet_stream):
        uci_by_ref.setdefault(int(region["graphic_ref"]) & 0xFFFF, set()).add(
            str(region["uci"]).upper()
        )
    allowed_ucis = {uci.upper() for uci in (adjacent_component_ucis or set())}
    candidates: list[
        tuple[int, float, float, tuple[float, float, float, float], float]
    ] = []
    for x1, y1, x2, y2, owner_ref, child_ref in (
        line_segments(sheet) + template_line_segments(sheet)
    ):
        segment = (x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
        length = math.hypot(segment[2] - segment[0], segment[3] - segment[1])
        if length < 55.0:
            continue
        _px, _py, distance, fraction = _point_segment_projection(weld_anchor, segment)
        if distance <= 36.0:
            owned_ucis = uci_by_ref.get(int(owner_ref) & 0xFFFF, set()) | uci_by_ref.get(
                int(child_ref) & 0xFFFF, set()
            )
            if allowed_ucis and owned_ucis & allowed_ucis:
                ownership_rank = 0
            elif owned_ucis:
                ownership_rank = 1
            else:
                ownership_rank = 2
            candidates.append(
                (ownership_rank, distance, -min(length, 1600.0), segment, fraction)
            )
    if not candidates:
        return weld_anchor, "mapped-weld-root-no-incident-line"
    ownership_rank, _distance, _negative_length, segment, fraction = min(candidates)
    x1, y1, x2, y2 = segment
    length = math.hypot(x2 - x1, y2 - y1)
    available_to_first = fraction * length
    available_to_second = (1.0 - fraction) * length
    if available_to_second >= available_to_first:
        direction_x, direction_y = (x2 - x1) / length, (y2 - y1) / length
        available = available_to_second
    else:
        direction_x, direction_y = (x1 - x2) / length, (y1 - y2) / length
        available = available_to_first
    travel = min(150.0, max(55.0, available * 0.35))
    travel = min(travel, max(0.0, available - 8.0))
    if travel <= 8.0:
        return weld_anchor, "mapped-weld-root-short-incident-line"
    if ownership_rank == 0 and allowed_ucis:
        method = "mapped-shop-weld-pcf-adjacent-component-line"
    elif ownership_rank <= 1:
        method = "mapped-shop-weld-uci-owned-component-line"
    else:
        method = "mapped-shop-weld-unowned-geometric-fallback"
    return (
        weld_anchor[0] + direction_x * travel,
        weld_anchor[1] + direction_y * travel,
    ), method


def resolve_spool_targets(
    records: list[SpoolInput],
    weld_map: dict[str, object],
    sha_path: Path,
    sheet_stream: str,
    page: int,
    pcf_path: Path | None = None,
) -> tuple[list[ManagementTarget], list[dict[str, object]]]:
    """Resolve one component-level callout for every selected spool."""

    mapped: list[ManagementTarget] = []
    unresolved: list[dict[str, object]] = []
    welds_by_excel_row: dict[int, list[dict[str, object]]] = {}
    for weld in weld_map.get("welds", []):
        excel_row = weld.get("construction_excel_row")
        if excel_row is None:
            continue
        welds_by_excel_row.setdefault(int(excel_row), []).append(weld)
    component_ports = None
    if pcf_path is not None and pcf_path.is_file():
        from chengda_weld_mapping import _component_ports_at_coordinates

        component_ports = _component_ports_at_coordinates(
            pcf_path.read_text(errors="replace").splitlines()
        )
    for record in records:
        candidates: list[dict[str, object]] = []
        for excel_row in record.source_rows:
            candidates.extend(
                weld
                for weld in welds_by_excel_row.get(excel_row, [])
                if int(weld.get("page", -1)) == page
                and weld.get("confidence_status") == "mapped"
                and weld.get("sha_x") is not None
                and weld.get("sha_y") is not None
                and weld.get("graphic_ref") is not None
            )
        if not candidates:
            unresolved.append(
                {
                    "kind": "spool",
                    "number": record.number,
                    "ucis": [],
                    "reason": "管段关联焊口未找到唯一SHA位置",
                    "source_rows": list(record.all_source_rows),
                }
            )
            continue
        # Source-row order already prefers shop welds.  Preserve it while
        # rejecting multiple decoded copies of the same physical point.
        row_order = {excel_row: index for index, excel_row in enumerate(record.source_rows)}
        candidates.sort(
            key=lambda weld: (
                row_order.get(int(weld.get("construction_excel_row", -1)), 999999),
                float(weld.get("distance_to_geometry") or 0.0),
            )
        )
        weld = candidates[0]
        weld_anchor = (float(weld["sha_x"]), float(weld["sha_y"]))
        adjacent_component_ucis: set[str] = set()
        endpoint = weld.get("pcf_endpoint")
        if component_ports is not None and isinstance(endpoint, list) and len(endpoint) >= 3:
            from chengda_weld_mapping import _component_ports_near_coordinate

            adjacent_component_ucis = {
                uci
                for _kind, uci, _component_id, _port_kind in _component_ports_near_coordinate(
                    component_ports, endpoint, tolerance=0.1
                )
            }
        anchor, method = _spool_component_anchor(
            sha_path,
            sheet_stream,
            weld_anchor,
            adjacent_component_ucis,
        )
        mapped.append(
            ManagementTarget(
                kind="spool",
                number=record.number,
                sheet_id=record.sheet_id,
                page=record.page,
                ucis=(),
                anchor=anchor,
                mapping_method=method,
                confidence=(
                    "high"
                    if method in {
                        "mapped-shop-weld-pcf-adjacent-component-line",
                        "mapped-shop-weld-uci-owned-component-line",
                    }
                    else "review"
                ),
                graphic_refs=(int(weld["graphic_ref"]),),
            )
        )
    return mapped, unresolved


def label_box(number: str, kind: str = "") -> tuple[float, float]:
    font_height = SPOOL_FONT_HEIGHT if kind == "spool" else MANAGEMENT_FONT_HEIGHT
    if kind == "spool":
        character_em = sum(
            0.34 if char in "-_." else 0.56 if char.isdigit() else 0.62
            for char in number
        )
        text_width = font_height * character_em
    else:
        text_width = font_height * 0.68 * len(number)
    width = max(font_height * 0.7, text_width)
    width += 2 * MANAGEMENT_TEXT_PADDING_X
    height = font_height + 2 * MANAGEMENT_TEXT_PADDING_Y
    if kind == "spool":
        width += 2 * SPOOL_INNER_FRAME_GAP
        height += 2 * SPOOL_INNER_FRAME_GAP
    return width, height


def plan_unified_page_layout(
    sha_path: Path,
    page: int,
    sheet_stream: str,
    weld_rows: list[dict[str, object]],
    management_targets: list[ManagementTarget],
    layout_audit: dict[str, object] | None = None,
) -> tuple[list[object], dict[str, tuple[float, float]]]:
    """Jointly place weld, management and spool tags on one physical page.

    Weld roots and component-management roots are semantically resolved by
    their existing proven pipelines.  Only their visual placement is shared:
    every marker and leader participates in one compatibility graph, so a
    valid strict result has no cross-family marker overlap or leader crossing.
    """

    from analyze_iso_split import read_sha_streams
    from inject_sha_weld_callouts import (
        WELD_CALLOUT_MARKER_RADIUS,
        PointTarget,
        arc_obstacle_segments,
        candidate_options,
        infer_layout_region,
        nearest_segment_guide,
        solve_page_layout,
        solve_page_layout_in_preferred_bands,
        source_obstacle_boxes,
        source_semantic_boxes,
    )
    from sha_to_svg_prototype import (
        declared_sheet_viewbox,
        line_segments,
        sheet_viewbox,
        template_line_segments,
    )

    if not weld_rows and not management_targets:
        if layout_audit is not None:
            layout_audit.update(
                {
                    "layout_mode": "strict",
                    "layout_scope": "weld-management-spool-joint",
                    "weld_count": 0,
                    "management_count": 0,
                    "spool_count": 0,
                }
            )
        return [], {}

    streams = read_sha_streams(sha_path)
    if sheet_stream not in streams:
        raise ValueError(f"SHA缺少页面流：{sheet_stream}")
    sheet = streams[sheet_stream]
    psm = streams.get("PSMcluster0", b"")
    template = streams.get("Sheet221", b"")
    inherited = declared_sheet_viewbox(streams.get("Sheet6", b""))
    viewbox = sheet_viewbox(sheet, inherited)
    region = infer_layout_region(template, viewbox)
    page_geometry = sheet
    if not (line_segments(sheet) or template_line_segments(sheet)) and template:
        page_geometry = template
    drawing_segments = line_segments(page_geometry) + template_line_segments(page_geometry)
    shared_template_segments = (
        [] if page_geometry is template else template_line_segments(template)
    )
    source_segments = (
        drawing_segments
        + arc_obstacle_segments(page_geometry, drawing_segments)
        + shared_template_segments
    )
    strict_source_boxes = source_obstacle_boxes(page_geometry, psm, gap=0.0)
    compact_weld_boxes = source_obstacle_boxes(page_geometry, psm, gap=-40.0)
    leader_boxes = source_obstacle_boxes(page_geometry, psm, gap=5.0)
    if page_geometry is not template:
        strict_source_boxes.extend(source_obstacle_boxes(template, psm, gap=0.0))
        compact_weld_boxes.extend(source_obstacle_boxes(template, psm, gap=-40.0))
        leader_boxes.extend(source_obstacle_boxes(template, psm, gap=5.0))
    semantic_boxes = tuple(
        source_semantic_boxes(page_geometry)
        + ([] if page_geometry is template else source_semantic_boxes(template))
    )

    specs: list[dict[str, object]] = []
    for index, row in enumerate(weld_rows):
        specs.append(
            {
                "family": "weld",
                "key": f"weld:{index}",
                "number": str(row.get("weld_number", "")),
                "anchor": (float(row["sha_x"]), float(row["sha_y"])),
                "graphic_ref": int(row["graphic_ref"]),
            }
        )
    for target in management_targets:
        specs.append(
            {
                "family": target.kind,
                "key": f"management:{target.key}",
                "number": target.number,
                "anchor": target.anchor,
                "graphic_ref": (
                    target.graphic_refs[0] if target.graphic_refs else len(specs) + 1
                ),
            }
        )

    points = [
        PointTarget(
            graphic_ref=int(spec["graphic_ref"]),
            uci=str(spec["key"]),
            center_x=float(spec["anchor"][0]),
            center_y=float(spec["anchor"][1]),
            page=page,
        )
        for spec in specs
    ]
    guides = [nearest_segment_guide(point, drawing_segments)[1] for point in points]
    candidate_limit = 96 if len(points) <= 24 else 48

    def build_options(compact: bool, best_effort: bool) -> list[list[object]]:
        options_by_point: list[list[object]] = []
        for spec, point, normal in zip(specs, points, guides):
            family = str(spec["family"])
            if family == "weld":
                width = height = None
                marker_radius = WELD_CALLOUT_MARKER_RADIUS
                marker_shape = "circle"
                gap = 60.0
                source_boxes = compact_weld_boxes if compact else strict_source_boxes
            else:
                width, height = label_box(str(spec["number"]), family)
                marker_radius = max(width, height) / 2.0
                marker_shape = "rectangle"
                gap = 45.0
                source_boxes = strict_source_boxes
            options_by_point.append(
                candidate_options(
                    point=point,
                    source_segments=source_segments,
                    source_boxes=source_boxes,
                    leader_boxes=leader_boxes,
                    region=region,
                    marker_radius=marker_radius,
                    marker_width=width,
                    marker_height=height,
                    gap=gap,
                    normal=normal,
                    marker_shape=marker_shape,
                    max_candidates=candidate_limit,
                    semantic_boxes=semantic_boxes,
                    peer_points=tuple(points),
                    best_effort=best_effort,
                    allow_fine_fallback=not best_effort,
                )
            )
        return options_by_point

    preferred_ranges = [
        (
            (350.0, 850.0)
            if str(spec["family"]) == "weld"
            else (450.0, 1000.0)
            if str(spec["family"]) == "spool"
            else (400.0, 900.0)
        )
        for spec in specs
    ]

    placements = None
    selected_mode = ""
    last_error: Exception | None = None
    for mode, compact, best_effort in (
        ("strict", False, False),
        ("compact", True, False),
        ("best-effort", True, True),
    ):
        try:
            options_by_point = build_options(compact, best_effort)
            if any(not options for options in options_by_point):
                raise ValueError("No locally valid unified-layout candidate")
            if best_effort:
                placements = solve_page_layout(
                    points,
                    options_by_point,
                    node_limit=10_000,
                    allow_conflicts=True,
                )
            else:
                placements = solve_page_layout_in_preferred_bands(
                    points,
                    options_by_point,
                    preferred_ranges,
                    node_limit=180_000 if not compact else 10_000,
                    layout_audit=layout_audit,
                )
            selected_mode = mode
            break
        except ValueError as exc:
            last_error = exc
    if placements is None:
        raise ValueError(f"联合页面布局失败：{last_error}")

    weld_count = len(weld_rows)
    weld_placements = list(placements[:weld_count])
    management_placements = {
        target.key: (placement.center_x, placement.center_y)
        for target, placement in zip(
            management_targets, placements[weld_count:]
        )
    }
    if layout_audit is not None:
        layout_audit.update(
            {
                "layout_mode": selected_mode,
                "layout_scope": "weld-management-spool-joint",
                "weld_count": weld_count,
                "management_count": sum(
                    target.kind != "spool" for target in management_targets
                ),
                "spool_count": sum(
                    target.kind == "spool" for target in management_targets
                ),
            }
        )
    return weld_placements, management_placements


def overlay_unified_weld_callouts(
    svg_path: Path,
    weld_rows: list[dict[str, object]],
    placements: list[object],
) -> None:
    """Replace preliminary injected weld graphics with joint-layout graphics."""

    if len(weld_rows) != len(placements):
        raise ValueError("焊口联合布局结果数量不一致")
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    tree = ET.parse(svg_path)
    root = tree.getroot()
    removable_layers = {
        "sha-injected-weld-leader",
        "sha-injected-weld-leader-halo",
        "sha-injected-weld-marker",
    }
    for parent in root.iter():
        for child in list(parent):
            mapping = str(child.attrib.get("data-mapping", ""))
            if (
                child.attrib.get("data-layer") in removable_layers
                or mapping.startswith("sha-injected-weld")
            ):
                parent.remove(child)

    namespace = "{http://www.w3.org/2000/svg}"
    group = ET.SubElement(
        root,
        namespace + "g",
        {
            "id": "sha-unified-weld-callouts",
            "fill": "none",
            "stroke": "#ff0000",
            "stroke-width": "10",
        },
    )
    for index, (row, placement) in enumerate(zip(weld_rows, placements)):
        root_x = float(row["sha_x"])
        root_y = float(row["sha_y"])
        leader_x = float(placement.leader_x)
        leader_y = float(placement.leader_y)
        center_x = float(placement.center_x)
        center_y = float(placement.center_y)
        radius = float(placement.marker_radius or 120.0)
        length = math.hypot(leader_x - root_x, leader_y - root_y)
        if length > 24.0:
            fraction = 24.0 / length
            ET.SubElement(
                group,
                namespace + "line",
                {
                    "x1": f"{root_x + (leader_x - root_x) * fraction:.1f}",
                    "y1": f"{PAGE_HEIGHT - (root_y + (leader_y - root_y) * fraction):.1f}",
                    "x2": f"{leader_x:.1f}",
                    "y2": f"{PAGE_HEIGHT - leader_y:.1f}",
                    "stroke": "#ffffff",
                    "stroke-width": "24",
                    "stroke-linecap": "round",
                    "data-layer": "sha-injected-weld-leader-halo",
                    "data-layout-scope": "weld-management-spool-joint",
                },
            )
        ET.SubElement(
            group,
            namespace + "line",
            {
                "x1": f"{root_x:.1f}",
                "y1": f"{PAGE_HEIGHT - root_y:.1f}",
                "x2": f"{leader_x:.1f}",
                "y2": f"{PAGE_HEIGHT - leader_y:.1f}",
                "data-layer": "sha-injected-weld-leader",
                "data-layout-scope": "weld-management-spool-joint",
            },
        )
        ET.SubElement(
            group,
            namespace + "circle",
            {
                "cx": f"{center_x:.1f}",
                "cy": f"{PAGE_HEIGHT - center_y:.1f}",
                "r": f"{radius:.1f}",
                "fill": "none",
                "data-layer": "sha-injected-weld-marker",
                "data-marker-shape": "circle",
                "data-layout-scope": "weld-management-spool-joint",
            },
        )
        text = ET.SubElement(
            group,
            namespace + "text",
            {
                "x": f"{center_x:.1f}",
                "y": f"{PAGE_HEIGHT - center_y:.1f}",
                "fill": "#ff0000",
                "stroke": "none",
                "font-size": f"{88.0 * weld_label_scale(str(row['weld_number'])):.3f}",
                "font-family": "SimHei, Microsoft YaHei, Arial, sans-serif",
                "font-weight": "700",
                "text-anchor": "middle",
                "dominant-baseline": "central",
                "data-mapping": "sha-injected-weld-label-anchor-unified-layout",
                "data-layout-scope": "weld-management-spool-joint",
                "data-unified-index": str(index),
            },
        )
        text.text = str(row["weld_number"])
    tree.write(svg_path, encoding="utf-8", xml_declaration=True)


def plan_management_layout(
    sha_path: Path,
    page: int,
    sheet_stream: str,
    targets: list[ManagementTarget],
    layout_audit: dict[str, object] | None = None,
) -> dict[str, tuple[float, float]]:
    """Place management tags with the proven weld straight-leader solver."""

    if not targets:
        return {}
    from analyze_iso_split import read_sha_streams
    from inject_sha_weld_callouts import (
        PointTarget,
        arc_obstacle_segments,
        candidate_options,
        infer_layout_region,
        nearest_segment_guide,
        solve_page_layout,
        source_obstacle_boxes,
        source_semantic_boxes,
    )
    from sha_to_svg_prototype import (
        declared_sheet_viewbox,
        line_segments,
        sheet_viewbox,
        template_line_segments,
    )

    streams = read_sha_streams(sha_path)
    if sheet_stream not in streams:
        raise ValueError(f"SHA缺少页面流：{sheet_stream}")
    sheet = streams[sheet_stream]
    psm = streams.get("PSMcluster0", b"")
    template = streams.get("Sheet221", b"")
    inherited = declared_sheet_viewbox(streams.get("Sheet6", b""))
    viewbox = sheet_viewbox(sheet, inherited)
    region = infer_layout_region(template, viewbox)
    drawing_segments = line_segments(sheet) + template_line_segments(sheet)
    source_segments = (
        drawing_segments
        + arc_obstacle_segments(sheet, drawing_segments)
        + template_line_segments(template)
    )
    source_boxes = source_obstacle_boxes(sheet, psm, gap=0.0)
    source_boxes.extend(source_obstacle_boxes(template, psm, gap=0.0))
    leader_boxes = source_obstacle_boxes(sheet, psm, gap=5.0)
    leader_boxes.extend(source_obstacle_boxes(template, psm, gap=5.0))
    semantic_boxes = tuple(source_semantic_boxes(sheet) + source_semantic_boxes(template))
    points = [
        PointTarget(
            graphic_ref=(target.graphic_refs[0] if target.graphic_refs else index + 1),
            uci=target.key,
            center_x=target.anchor[0],
            center_y=target.anchor[1],
            page=page,
        )
        for index, target in enumerate(targets)
    ]
    options_by_point = []
    best_effort_used = False
    for target, point in zip(targets, points):
        width, height = label_box(target.number, target.kind)
        normal = nearest_segment_guide(point, drawing_segments)[1]
        options = candidate_options(
                point=point,
                source_segments=source_segments,
                source_boxes=source_boxes,
                leader_boxes=leader_boxes,
                region=region,
                marker_radius=max(width, height) / 2.0,
                marker_width=width,
                marker_height=height,
                gap=45.0,
                normal=normal,
                marker_shape="rectangle",
                max_candidates=96 if len(points) <= 24 else 48,
                semantic_boxes=semantic_boxes,
                peer_points=tuple(points),
            )
        if not options:
            best_effort_used = True
            options = candidate_options(
                point=point,
                source_segments=source_segments,
                source_boxes=source_boxes,
                leader_boxes=leader_boxes,
                region=region,
                marker_radius=max(width, height) / 2.0,
                marker_width=width,
                marker_height=height,
                gap=45.0,
                normal=normal,
                marker_shape="rectangle",
                max_candidates=48,
                semantic_boxes=semantic_boxes,
                peer_points=tuple(points),
                best_effort=True,
            )
        options_by_point.append(options)
    try:
        placements = solve_page_layout(
            points,
            options_by_point,
            allow_conflicts=best_effort_used,
        )
    except ValueError as exc:
        if best_effort_used or "No zero-crossing global layout" not in str(exc):
            raise
        best_effort_used = True
        placements = solve_page_layout(
            points,
            options_by_point,
            allow_conflicts=True,
        )
    if layout_audit is not None:
        layout_audit["layout_mode"] = (
            "best-effort" if best_effort_used else "strict"
        )
    return {
        target.key: (placement.center_x, placement.center_y)
        for target, placement in zip(targets, placements)
    }


def _rectangle_edge(
    source: tuple[float, float], center: tuple[float, float], width: float, height: float
) -> tuple[float, float]:
    dx, dy = source[0] - center[0], source[1] - center[1]
    if abs(dx) + abs(dy) <= 1e-9:
        return center
    scale = 1.0 / max(abs(dx) / (width / 2), abs(dy) / (height / 2))
    return center[0] + dx * scale, center[1] + dy * scale


def overlay_management_callouts(
    svg_path: Path,
    targets: list[ManagementTarget],
    placements: dict[str, tuple[float, float]],
) -> None:
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    tree = ET.parse(svg_path)
    root = tree.getroot()
    namespace = "{http://www.w3.org/2000/svg}"
    group = ET.SubElement(root, namespace + "g", {"id": "sha-management-callouts"})
    for target in targets:
        if target.key not in placements:
            continue
        center = placements[target.key]
        width, height = label_box(target.number, target.kind)
        edge = _rectangle_edge(target.anchor, center, width, height)
        color = SPOOL_COLOR if target.kind == "spool" else MANAGEMENT_COLOR
        common = {
            "data-management-kind": target.kind,
            "data-management-number": target.number,
            "data-mapping": target.mapping_method,
        }
        ET.SubElement(group, namespace + "line", {
            "x1": f"{target.anchor[0]:.1f}", "y1": f"{PAGE_HEIGHT-target.anchor[1]:.1f}",
            "x2": f"{edge[0]:.1f}", "y2": f"{PAGE_HEIGHT-edge[1]:.1f}",
            "stroke": color, "stroke-width": "8", "fill": "none",
            "data-layer": "sha-management-leader", **common,
        })
        ET.SubElement(group, namespace + "rect", {
            "x": f"{center[0]-width/2:.1f}", "y": f"{PAGE_HEIGHT-(center[1]+height/2):.1f}",
            "width": f"{width:.1f}", "height": f"{height:.1f}",
            "stroke": color, "stroke-width": "8", "fill": "none",
            "data-layer": "sha-management-marker", **common,
        })
        if target.kind == "spool":
            ET.SubElement(group, namespace + "rect", {
                "x": f"{center[0]-width/2+SPOOL_INNER_FRAME_GAP:.1f}",
                "y": f"{PAGE_HEIGHT-(center[1]+height/2)+SPOOL_INNER_FRAME_GAP:.1f}",
                "width": f"{width-2*SPOOL_INNER_FRAME_GAP:.1f}",
                "height": f"{height-2*SPOOL_INNER_FRAME_GAP:.1f}",
                "stroke": color, "stroke-width": "8", "fill": "none",
                "data-layer": "sha-spool-marker-inner", **common,
            })
        text = ET.SubElement(group, namespace + "text", {
            "x": f"{center[0]:.1f}", "y": f"{PAGE_HEIGHT-center[1]:.1f}",
            "fill": color,
            "font-size": f"{(SPOOL_FONT_HEIGHT if target.kind == 'spool' else MANAGEMENT_FONT_HEIGHT):.1f}",
            "font-family": MANAGEMENT_FONT_FAMILY, "font-weight": "700",
            "text-anchor": "middle", "dominant-baseline": "central",
            "data-layer": "sha-management-label", **common,
        })
        text.text = target.number
    tree.write(svg_path, encoding="utf-8", xml_declaration=True)


def write_management_audit(
    path: Path,
    targets: list[ManagementTarget],
    unresolved: list[dict[str, object]],
) -> None:
    payload = {
        "mapped_count": len(targets),
        "unresolved_count": len(unresolved),
        "callouts": [asdict(target) for target in targets],
        "unresolved": unresolved,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
