#!/usr/bin/env python3
"""Map numbered Chengda PCF welds to native or projected SHA points."""

from __future__ import annotations

import json
import math
import os
import re
import struct
import tempfile
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from analyze_iso_split import forward_owned_dynamic_graphics, read_sha_streams
from audit_pcf_sha_weld_topology import audit_topology
from inject_sha_weld_callouts import infer_layout_region
from chengda_template_owned_page import mapping_sheet_streams as sheet_streams
from number_pcf_welds import (
    POINT_RE,
    TOP_LEVEL,
    WeldBlock,
    long_pipe_drawing_overrides,
    number_welds,
    parse_pipe_components,
    parse_weld_blocks,
    sha_implicit_pipe_end_weld_points,
    sha_virtual_weld_points,
    sha_weld_points,
)
from sha_to_svg_prototype import (
    SHEET_UNIT,
    declared_sheet_viewbox,
    ellipse_anchors,
    line_segments,
    sheet_viewbox,
    template_line_segments,
)


TOKEN_RE = re.compile(r"\d+|\D+")
COMPONENT_PORT_RE = re.compile(
    r"(END-POINT|BRANCH1-POINT|CENTRE-POINT)\s+"
    r"([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)"
)
CONNECTION_REFERENCE_RE = re.compile(
    r"^\s*CONNECTION-REFERENCE\s+(?:[^$\s]+\$)?([^\s]+)\s*$",
    re.IGNORECASE,
)
NON_CONNECTING_BLOCKS = {
    "WELD",
    "SUPPORT",
    "BOLT",
    "FLOW-ARROW",
    "END-CONNECTION-PIPELINE",
    "END-POSITION-CLOSED",
}


def _dynamic_graphics_including_first_record(
    dynamic_data: bytes,
) -> dict[str, list[dict[str, object]]]:
    """Decode forward-owned UCI graphics, including the serialized first owner.

    Chengda stores the first dynamic owner's references in the 20-byte prefix
    before the first ``PipeLine Info`` record.  The ordinary forward-owned
    decoder intentionally starts with the following record, so implicit
    two-component interfaces must restore this first owner explicitly.
    """

    dynamic = forward_owned_dynamic_graphics(dynamic_data)
    first_marker = dynamic_data.find(b"PipeLine Info\x00")
    first_start = first_marker - 5
    if first_marker < 5 or first_start < 20:
        return dynamic
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
    if first_uci is None or not prefix_space_ref or not prefix_graphic_ref:
        return dynamic
    uci = first_uci.group().decode()
    first_graphic = {
        "record_offset": first_start,
        "reference_record_offset": first_start - 20,
        "space_ref": prefix_space_ref,
        "graphic_ref": prefix_graphic_ref,
    }
    records = dynamic.setdefault(uci, [])
    if not any(
        int(record.get("graphic_ref", 0)) == prefix_graphic_ref
        for record in records
    ):
        records.insert(0, first_graphic)
    return dynamic


@dataclass(frozen=True)
class WeldMapSummary:
    numbered_weld_count: int
    callout_target_count: int
    unmapped_numbered_count: int
    ambiguous_count: int


def _coordinate_key(point: Iterable[object]) -> tuple[float, float, float]:
    values = list(point)
    return tuple(round(float(values[index]), 3) for index in range(3))


def _point_segment_projection(
    point: tuple[float, float, float],
    endpoint_a: tuple[float, float, float, float],
    endpoint_b: tuple[float, float, float, float],
) -> tuple[float, float]:
    vector = tuple(endpoint_b[index] - endpoint_a[index] for index in range(3))
    length_squared = sum(value * value for value in vector)
    if length_squared <= 1e-12:
        return float("inf"), 0.0
    fraction = sum(
        (point[index] - endpoint_a[index]) * vector[index]
        for index in range(3)
    ) / length_squared
    projected = tuple(
        endpoint_a[index] + fraction * vector[index] for index in range(3)
    )
    return math.dist(point, projected), fraction


def _synthetic_table_weld(record: object) -> WeldBlock:
    coordinate = tuple(float(value) for value in record.coordinate)
    return WeldBlock(
        start=-1,
        end=-1,
        lines=[],
        uci=f"{{CONSTRUCTION-TABLE-{record.excel_row}}}",
        component_identifier="",
        master_component_identifier="",
        skey="",
        category="",
        endpoint=(coordinate[0], coordinate[1], coordinate[2], 0.0),
        description="",
        repeat_weld_identifier=str(record.weld_number),
        augmentation_source="",
        augmentation_mode="",
        owner_uci="",
        owner_fraction=None,
        neighbor_uci_a="",
        neighbor_uci_b="",
    )


def _component_ucis_at_coordinates(
    source_lines: list[str],
) -> dict[tuple[float, float, float], set[str]]:
    result: dict[tuple[float, float, float], set[str]] = defaultdict(set)
    starts = [
        (index, line)
        for index, line in enumerate(source_lines)
        if TOP_LEVEL.fullmatch(line) and line != "MATERIALS"
    ]
    for position, (start, kind) in enumerate(starts):
        if kind in NON_CONNECTING_BLOCKS:
            continue
        end = starts[position + 1][0] if position + 1 < len(starts) else len(source_lines)
        block = "\n".join(source_lines[start:end])
        uci_match = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        if uci_match is None:
            continue
        uci = uci_match.group(1)
        for match in POINT_RE.finditer(block):
            point = tuple(float(match.group(index)) for index in range(2, 5))
            result[_coordinate_key(point)].add(uci)
    return result


def _component_ports_at_coordinates(
    source_lines: list[str],
) -> dict[tuple[float, float, float], list[tuple[str, str, str, str]]]:
    """Return physical PCF connection ports, including both OLET interfaces.

    Most component connections use END-POINT/BRANCH1-POINT.  Chengda OLETs
    encode the run-side interface as CENTRE-POINT, so that point is a port for
    OLET only; a bend/elbow centre is geometry, not a connection interface.
    """

    result: dict[
        tuple[float, float, float], list[tuple[str, str, str, str]]
    ] = defaultdict(list)
    starts = [
        (index, line)
        for index, line in enumerate(source_lines)
        if TOP_LEVEL.fullmatch(line) and line != "MATERIALS"
    ]
    for position, (start, kind) in enumerate(starts):
        if kind in NON_CONNECTING_BLOCKS:
            continue
        end = starts[position + 1][0] if position + 1 < len(starts) else len(source_lines)
        block = "\n".join(source_lines[start:end])
        uci_match = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        component_match = re.search(r"COMPONENT-IDENTIFIER\s+(\S+)", block)
        if uci_match is None:
            continue
        for match in COMPONENT_PORT_RE.finditer(block):
            port_kind = match.group(1)
            if port_kind == "CENTRE-POINT" and kind != "OLET":
                continue
            point = tuple(float(match.group(index)) for index in range(2, 5))
            member = (
                kind,
                uci_match.group(1),
                component_match.group(1) if component_match else "",
                port_kind,
            )
            if member not in result[_coordinate_key(point)]:
                result[_coordinate_key(point)].append(member)
    return result


def _component_ports_near_coordinate(
    ports: dict[
        tuple[float, float, float], list[tuple[str, str, str, str]]
    ],
    coordinate: Iterable[object],
    tolerance: float = 0.1,
) -> list[tuple[str, str, str, str]]:
    expected = tuple(float(value) for value in list(coordinate)[:3])
    matched: list[tuple[str, str, str, str]] = []
    for point, members in ports.items():
        if max(abs(point[index] - expected[index]) for index in range(3)) > tolerance:
            continue
        for member in members:
            if member not in matched:
                matched.append(member)
    return matched


def _connection_topology_source_lines(
    pcf_path: Path,
    source_lines: list[str],
) -> list[str]:
    """Add only PCFs explicitly referenced by this drawing's connections.

    A threaded branch at an ISO boundary can have its nipple in the current
    PCF and the mating tee port in a referenced PCF.  The SHA still carries
    both UCI-owned graphics, so loading the declared neighbour restores the
    missing second side without searching unrelated lines.
    """

    referenced_stems = list(
        dict.fromkeys(
            match.group(1).strip()
            for line in source_lines
            if (match := CONNECTION_REFERENCE_RE.match(line)) is not None
        )
    )
    if not referenced_stems:
        return source_lines

    merged = list(source_lines)
    casefold_index: dict[str, Path] | None = None
    for stem in referenced_stems:
        referenced_stem = Path(stem).stem
        referenced_path = pcf_path.parent / f"{referenced_stem}.pcf"
        if not referenced_path.is_file():
            if casefold_index is None:
                casefold_index = {
                    candidate.stem.casefold(): candidate
                    for candidate in pcf_path.parent.glob("*.pcf")
                    if candidate.resolve() != pcf_path.resolve()
                }
            referenced_path = casefold_index.get(referenced_stem.casefold())
        if referenced_path is None:
            continue
        referenced_lines, _ = parse_weld_blocks(referenced_path)
        merged.extend(referenced_lines)
    return merged


def _unique_interface_point(
    best: list[
        tuple[
            float,
            tuple[float, float],
            tuple[float, float],
        ]
    ],
    cross_section_tolerance: float = 60.0,
) -> tuple[float, float] | None:
    """Reduce one component-interface cross section to one drawing point."""

    midpoint_by_key = {
        (
            round((item[1][0] + item[2][0]) / 2, 1),
            round((item[1][1] + item[2][1]) / 2, 1),
        ): (
            (item[1][0] + item[2][0]) / 2,
            (item[1][1] + item[2][1]) / 2,
        )
        for item in best
    }
    midpoints = set(midpoint_by_key)
    if len(midpoints) == 1:
        return next(iter(midpoints))
    # A flange face can contribute two short parallel strokes while the
    # opposite component contributes one identical centreline endpoint.
    left_points = {item[1] for item in best}
    right_points = {item[2] for item in best}
    if len(left_points) == 1:
        point = next(iter(left_points))
        return round(point[0], 1), round(point[1], 1)
    if len(right_points) == 1:
        point = next(iter(right_points))
        return round(point[0], 1), round(point[1], 1)
    # Double-line pipe and elbow graphics expose both outline edges at one
    # engineering interface.  Collapse only a compact cluster; separated
    # clusters remain ambiguous and are left for manual review.
    cluster_points = list(midpoint_by_key.values())
    # Threaded Chengda joints can expose the two pipe outlines plus the
    # centreline as three coincident component endpoints.  Their nominal
    # 60-page-unit cross section is serialized as 60.48 units, just beyond
    # the ordinary compact-cluster tolerance.  Accept only the much stricter
    # signature of three collinear, equally spaced points and use its centre.
    if len(cluster_points) == 3:
        x_span = max(point[0] for point in cluster_points) - min(
            point[0] for point in cluster_points
        )
        y_span = max(point[1] for point in cluster_points) - min(
            point[1] for point in cluster_points
        )
        axis = 0 if x_span >= y_span else 1
        ordered = sorted(cluster_points, key=lambda point: point[axis])
        outer_span = math.dist(ordered[0], ordered[2])
        midpoint = (
            (ordered[0][0] + ordered[2][0]) / 2,
            (ordered[0][1] + ordered[2][1]) / 2,
        )
        if (
            outer_span <= cross_section_tolerance * 1.1
            and math.dist(ordered[1], midpoint) <= 1.0
        ):
            return round(ordered[1][0], 1), round(ordered[1][1], 1)
    if max(
        math.dist(first, second)
        for first in cluster_points
        for second in cluster_points
    ) <= cross_section_tolerance:
        return (
            round(sum(point[0] for point in cluster_points) / len(cluster_points), 1),
            round(sum(point[1] for point in cluster_points) / len(cluster_points), 1),
        )
    return None


def _sha_two_sided_component_junctions(
    sha_path: Path,
    source_lines: list[str],
    welds: list[WeldBlock],
) -> dict[str, dict[str, object]]:
    """Locate WELDs at a unique common endpoint of both adjacent components."""

    components_at = _component_ports_at_coordinates(source_lines)

    adjacent_by_weld: dict[str, list[tuple[str, str, str]]] = {}
    target_ucis: set[str] = set()
    for weld in welds:
        if weld.endpoint is None:
            continue
        adjacent = list(
            dict.fromkeys(
                (kind, uci, component_id)
                for kind, uci, component_id, _port_kind in (
                    _component_ports_near_coordinate(
                        components_at, weld.endpoint, tolerance=0.1
                    )
                )
            )
        )
        if len(adjacent) < 2:
            continue
        adjacent_by_weld[weld.uci] = adjacent
        target_ucis.update(uci for _, uci, _ in adjacent)
    if not adjacent_by_weld:
        return {}

    streams = read_sha_streams(sha_path)
    dynamic = _dynamic_graphics_including_first_record(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    groups_by_page: dict[
        int, dict[int, list[tuple[float, float, float, float]]]
    ] = {}
    stream_by_page: dict[int, str] = {}
    for sheet in sheet_streams(streams, None):
        groups: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
        for x1, y1, x2, y2, parent_ref, _ in (
            line_segments(sheet.data) + template_line_segments(sheet.data)
        ):
            groups[int(parent_ref) & 0xFFFF].append(
                (
                    x1 * SHEET_UNIT,
                    y1 * SHEET_UNIT,
                    x2 * SHEET_UNIT,
                    y2 * SHEET_UNIT,
                )
            )
        groups_by_page[sheet.page] = groups
        stream_by_page[sheet.page] = sheet.stream_name

    geometry: dict[str, dict[int, list[tuple[float, float]]]] = {}
    geometry_refs: dict[str, dict[int, set[int]]] = {}
    for uci in target_ucis:
        dynamic_refs = {
            int(record["graphic_ref"]) & 0xFFFF
            for record in dynamic.get(uci, [])
        }
        page_points: dict[int, list[tuple[float, float]]] = {}
        page_refs: dict[int, set[int]] = {}
        for page, groups in groups_by_page.items():
            exact_refs = sorted(ref for ref in groups if ref in dynamic_refs)
            selected_refs = exact_refs
            if not selected_refs and dynamic_refs:
                ranked = sorted(
                    (
                        min(abs(((ref - dynamic_ref + 0x8000) & 0xFFFF) - 0x8000) for dynamic_ref in dynamic_refs),
                        ref,
                    )
                    for ref in groups
                )
                if ranked and ranked[0][0] <= 12:
                    best_delta = ranked[0][0]
                    selected_refs = [ref for delta, ref in ranked if delta == best_delta]
            points = {
                (round(x, 3), round(y, 3))
                for ref in selected_refs
                for segment in groups[ref]
                for x, y in ((segment[0], segment[1]), (segment[2], segment[3]))
            }
            if points:
                page_points[page] = sorted(points)
                page_refs[page] = set(selected_refs)
        geometry[uci] = page_points
        geometry_refs[uci] = page_refs

    resolved: dict[str, dict[str, object]] = {}
    for weld in welds:
        adjacent = adjacent_by_weld.get(weld.uci)
        if not adjacent:
            continue
        # MASTER-COMPONENT-IDENTIFIER names the owner on many Chengda WELDs.
        # Prefer pairs containing it, but retain all pairs when the field is
        # absent or not unique.
        pairs = [
            (left, right)
            for index, left in enumerate(adjacent)
            for right in adjacent[index + 1 :]
        ]
        master_pairs = [
            pair
            for pair in pairs
            if weld.master_component_identifier
            and any(item[2] == weld.master_component_identifier for item in pair)
        ]
        if master_pairs:
            pairs = master_pairs
        junctions: list[tuple[int, float, float, float, str, str, set[int]]] = []
        for left, right in pairs:
            left_uci, right_uci = left[1], right[1]
            common_pages = set(geometry.get(left_uci, {})) & set(
                geometry.get(right_uci, {})
            )
            for page in common_pages:
                distances = sorted(
                    (
                        math.dist(left_point, right_point),
                        left_point,
                        right_point,
                    )
                    for left_point in geometry[left_uci][page]
                    for right_point in geometry[right_uci][page]
                )
                if not distances or distances[0][0] > 60.0:
                    continue
                best_gap = distances[0][0]
                best = [item for item in distances if item[0] <= best_gap + 1.0]
                interface_point = _unique_interface_point(best)
                if interface_point is None:
                    continue
                x, y = interface_point
                refs = geometry_refs[left_uci][page] | geometry_refs[right_uci][page]
                junctions.append((page, x, y, best_gap, left_uci, right_uci, refs))
        unique = {
            (page, x, y): (page, x, y, gap, left, right, refs)
            for page, x, y, gap, left, right, refs in junctions
        }
        if len(unique) != 1:
            continue
        page, x, y, gap, left_uci, right_uci, refs = next(iter(unique.values()))
        resolved[weld.uci] = {
            "uci": weld.uci,
            "graphic_ref": 0xC0000000
            | (zlib.crc32(weld.uci.encode("ascii")) & 0x0FFFFFFF),
            "page": page,
            "sheet_stream": stream_by_page[page],
            "sha_x": x,
            "sha_y": y,
            "distance_to_geometry": round(gap / 2, 3),
            "point_evidence": "pcf-topology-two-sided-component-junction",
            "topology_rule": "unique common SHA endpoint of both adjacent PCF components",
            "topology_adjacent_ucis": [left_uci, right_uci],
            "topology_convergence_gap": round(gap, 3),
            "topology_graphic_refs_low16": [f"0x{ref:04X}" for ref in sorted(refs)],
        }
    return resolved


def _sha_opposite_reducer_face_points(
    sha_path: Path,
    source_lines: list[str],
    welds: list[WeldBlock],
    known_points: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Resolve a reducer-side weld from the already known opposite face.

    A duplicated pipe UCI can make an implicit pipe-end weld inherit the SHA
    root of a different joint.  Reducers carry both end-face strokes in their
    own SHA graphic, so their two PCF ports plus the mapped weld at one port
    provide a stronger, component-local solution for the other port.
    """

    ports = _component_ports_at_coordinates(source_lines)
    welds_at: dict[tuple[float, float, float], list[WeldBlock]] = defaultdict(list)
    for weld in welds:
        if weld.endpoint is not None:
            welds_at[_coordinate_key(weld.endpoint)].append(weld)

    reducer_ports: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    reducer_kinds: dict[str, str] = {}
    for coordinate, members in ports.items():
        for kind, uci, _component_id, _port_kind in members:
            if not kind.startswith("REDUCER"):
                continue
            reducer_kinds[uci] = kind
            if coordinate not in reducer_ports[uci]:
                reducer_ports[uci].append(coordinate)

    streams = read_sha_streams(sha_path)
    dynamic = _dynamic_graphics_including_first_record(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    page_segments: dict[int, dict[int, list[tuple[float, float, float, float]]]] = {}
    stream_by_page: dict[int, str] = {}
    for sheet in sheet_streams(streams, None):
        groups: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
        for x1, y1, x2, y2, parent_ref, _child_ref in (
            line_segments(sheet.data) + template_line_segments(sheet.data)
        ):
            groups[int(parent_ref) & 0xFFFF].append(
                (x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT)
            )
        page_segments[sheet.page] = groups
        stream_by_page[sheet.page] = sheet.stream_name

    resolved: dict[str, dict[str, object]] = {}
    for weld in welds:
        if weld.endpoint is None:
            continue
        endpoint_key = _coordinate_key(weld.endpoint)
        adjacent_reducers = [
            (kind, uci)
            for kind, uci, _component_id, _port_kind in ports.get(endpoint_key, [])
            if kind.startswith("REDUCER")
        ]
        if len(adjacent_reducers) != 1:
            continue
        reducer_kind, reducer_uci = adjacent_reducers[0]
        component_ports = reducer_ports.get(reducer_uci, [])
        if len(component_ports) != 2:
            continue
        opposite_ports = [point for point in component_ports if point != endpoint_key]
        if len(opposite_ports) != 1:
            continue
        opposite_weld_points = [
            known_points[other.uci]
            for other in welds_at.get(opposite_ports[0], [])
            if other.uci in known_points
            and known_points[other.uci].get("page") not in (None, 999999)
        ]
        if len(opposite_weld_points) != 1:
            continue
        known = opposite_weld_points[0]
        page = int(known["page"])
        known_xy = (float(known["sha_x"]), float(known["sha_y"]))

        dynamic_refs = {
            int(record["graphic_ref"]) & 0xFFFF
            for record in dynamic.get(reducer_uci, [])
        }
        segments = [
            segment
            for ref in dynamic_refs
            for segment in page_segments.get(page, {}).get(ref, [])
        ]
        parallel_pairs: list[
            tuple[float, tuple[float, float], tuple[float, float], set[int]]
        ] = []
        for left_index, left in enumerate(segments):
            left_vector = (left[2] - left[0], left[3] - left[1])
            left_length = math.hypot(*left_vector)
            if left_length <= 1.0:
                continue
            left_mid = ((left[0] + left[2]) / 2, (left[1] + left[3]) / 2)
            for right_index in range(left_index + 1, len(segments)):
                right = segments[right_index]
                right_vector = (right[2] - right[0], right[3] - right[1])
                right_length = math.hypot(*right_vector)
                if right_length <= 1.0:
                    continue
                cosine = abs(
                    (left_vector[0] * right_vector[0] + left_vector[1] * right_vector[1])
                    / (left_length * right_length)
                )
                if cosine < 0.985:
                    continue
                right_mid = ((right[0] + right[2]) / 2, (right[1] + right[3]) / 2)
                separation = math.dist(left_mid, right_mid)
                if separation <= 10.0:
                    continue
                score = min(math.dist(known_xy, left_mid), math.dist(known_xy, right_mid))
                parallel_pairs.append(
                    (score, left_mid, right_mid, set(dynamic_refs))
                )
        if not parallel_pairs:
            continue
        score, first_mid, second_mid, refs = min(parallel_pairs, key=lambda item: item[0])
        if score > 100.0:
            continue
        target = second_mid if math.dist(known_xy, first_mid) <= math.dist(known_xy, second_mid) else first_mid
        resolved[weld.uci] = {
            "uci": weld.uci,
            "graphic_ref": 0xD0000000
            | (zlib.crc32(weld.uci.encode("ascii")) & 0x0FFFFFFF),
            "page": page,
            "sheet_stream": stream_by_page[page],
            "sha_x": round(target[0], 1),
            "sha_y": round(target[1], 1),
            "distance_to_geometry": 0.0,
            "point_evidence": "pcf-topology-opposite-reducer-face",
            "topology_rule": "opposite SHA face of the same two-port reducer",
            "topology_component_kind": reducer_kind,
            "topology_component_uci": reducer_uci,
            "topology_graphic_refs_low16": [f"0x{ref:04X}" for ref in sorted(refs)],
        }
    return resolved


def _connected_collinear_pipe_run(
    owner: object,
    pipes: list[object],
    axis_tolerance: float = 0.05,
) -> tuple[list[object], tuple[float, float, float], tuple[float, float, float]] | None:
    same_uci = [pipe for pipe in pipes if pipe.uci == owner.uci]
    run: list[object] = []
    pending = [owner]
    seen: set[tuple[str, tuple[float, float, float], tuple[float, float, float]]] = set()
    while pending:
        pipe = pending.pop()
        identity = (
            pipe.component_identifier,
            _coordinate_key(pipe.endpoint_a),
            _coordinate_key(pipe.endpoint_b),
        )
        if identity in seen:
            continue
        seen.add(identity)
        run.append(pipe)
        ends = {_coordinate_key(pipe.endpoint_a), _coordinate_key(pipe.endpoint_b)}
        for candidate in same_uci:
            candidate_identity = (
                candidate.component_identifier,
                _coordinate_key(candidate.endpoint_a),
                _coordinate_key(candidate.endpoint_b),
            )
            if candidate_identity in seen:
                continue
            if (
                _coordinate_key(candidate.endpoint_a) in ends
                or _coordinate_key(candidate.endpoint_b) in ends
            ):
                pending.append(candidate)

    unique_points = sorted(
        {
            _coordinate_key(endpoint)
            for pipe in run
            for endpoint in (pipe.endpoint_a, pipe.endpoint_b)
        }
    )
    if len(unique_points) < 2:
        return None
    start, end = max(
        (
            (left, right)
            for index, left in enumerate(unique_points)
            for right in unique_points[index + 1 :]
        ),
        key=lambda pair: (math.dist(pair[0], pair[1]), pair),
    )
    if any(
        _point_segment_projection(point, (*start, 0.0), (*end, 0.0))[0]
        > axis_tolerance
        for point in unique_points
    ):
        return None
    return run, start, end


def _support_bracketed_pipe_chain(
    source_lines: list[str],
    sha_path: Path,
    owner_uci: str,
    run_start: tuple[float, float, float],
    run_end: tuple[float, float, float],
    chain_records: list[object],
) -> dict[int, dict[str, object]]:
    """Place a proven straight-pipe chain only between UCI-matched supports.

    Isometric lengths are schematic, so the physical-to-drawing scale is
    checked and interpolated separately between each adjacent support pair.
    UCI-owned pipe strokes must trace that support line; a long, parallel
    dimension line is never accepted as the weld leader's source.
    """

    from chengda_management_callouts import forward_owned_uci_regions

    supports: list[tuple[str, float]] = []
    run_length = math.dist(run_start, run_end)
    if run_length <= 0:
        return {}
    for index, line in enumerate(source_lines):
        if line != "SUPPORT":
            continue
        end = index + 1
        while end < len(source_lines) and not TOP_LEVEL.fullmatch(source_lines[end]):
            end += 1
        block = "\n".join(source_lines[index:end])
        uci_match = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        point_match = re.search(
            r"\bCO-ORDS\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)", block
        )
        if uci_match is None or point_match is None:
            continue
        point = tuple(float(point_match.group(part)) for part in (1, 2, 3))
        distance, fraction = _point_segment_projection(
            point, (*run_start, 0.0), (*run_end, 0.0)
        )
        if distance <= 0.05 and 0.0 < fraction < 1.0:
            supports.append((uci_match.group(1).upper(), fraction))
    if len(supports) < 3 or len({uci for uci, _ in supports}) != len(supports):
        return {}

    streams = read_sha_streams(sha_path)
    result: dict[int, dict[str, object]] = {}
    valid_pages = 0
    for sheet in sheet_streams(streams, None):
        regions = forward_owned_uci_regions(sha_path, sheet.stream_name)
        owner_refs = {
            int(region["graphic_ref"])
            for region in regions
            if str(region.get("uci", "")).upper() == owner_uci.upper()
        }
        if not owner_refs:
            continue
        by_uci: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for region in regions:
            box = region.get("bbox")
            if isinstance(box, list) and len(box) == 4:
                by_uci[str(region.get("uci", "")).upper()].append(
                    ((float(box[0]) + float(box[2])) / 2,
                     (float(box[1]) + float(box[3])) / 2)
                )
        anchors = sorted(
            (fraction, by_uci[uci][0], uci)
            for uci, fraction in supports
            if len(by_uci.get(uci, [])) == 1
        )
        if len(anchors) < 3:
            continue
        first_xy, last_xy = anchors[0][1], anchors[-1][1]
        support_span = math.dist(first_xy, last_xy)
        if support_span < 500.0 or (anchors[-1][0] - anchors[0][0]) * run_length < 30000.0:
            continue
        support_direction = (
            (last_xy[0] - first_xy[0]) / support_span,
            (last_xy[1] - first_xy[1]) / support_span,
        )
        if any(
            abs((xy[0] - first_xy[0]) * support_direction[1]
                - (xy[1] - first_xy[1]) * support_direction[0]) > 15.0
            for _, xy, _ in anchors
        ):
            continue

        owner_intervals: list[tuple[float, float]] = []
        aligned_intervals: list[tuple[float, float]] = []
        for x1, y1, x2, y2, _ref, _style in (
            line_segments(sheet.data) + template_line_segments(sheet.data)
        ):
            start = (x1 * SHEET_UNIT, y1 * SHEET_UNIT)
            end = (x2 * SHEET_UNIT, y2 * SHEET_UNIT)
            length = math.dist(start, end)
            if length < 60.0:
                continue
            direction = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
            if abs(direction[0] * support_direction[0]
                   + direction[1] * support_direction[1]) < 0.997:
                continue
            if max(
                abs((xy[0] - first_xy[0]) * support_direction[1]
                    - (xy[1] - first_xy[1]) * support_direction[0])
                for xy in (start, end)
            ) > 12.0:
                continue
            projections = sorted(
                (xy[0] - first_xy[0]) * support_direction[0]
                + (xy[1] - first_xy[1]) * support_direction[1]
                for xy in (start, end)
            )
            interval = (projections[0], projections[1])
            if interval[1] < -10.0 or interval[0] > support_span + 10.0:
                continue
            aligned_intervals.append(interval)
            if int(_ref) in owner_refs:
                owner_intervals.append(interval)
        if not owner_intervals:
            continue
        owner_intervals.sort()
        covered_until = owner_intervals[0][1]
        if owner_intervals[0][0] > 10.0:
            continue
        for low, high in owner_intervals[1:]:
            if low > covered_until + 180.0:
                break
            covered_until = max(covered_until, high)
        if covered_until < support_span - 10.0:
            continue
        projected = [
            (
                fraction,
                (xy[0] - first_xy[0]) * support_direction[0]
                + (xy[1] - first_xy[1]) * support_direction[1],
                uci,
            )
            for fraction, xy, uci in anchors
        ]
        steps = [projected[i + 1][1] - projected[i][1]
                 for i in range(len(projected) - 1)]
        if not (all(step > 20.0 for step in steps)
                or all(step < -20.0 for step in steps)):
            continue
        page_result: dict[int, dict[str, object]] = {}
        for record in chain_records:
            point = tuple(float(value) for value in record.coordinate)
            distance, fraction = _point_segment_projection(
                point, (*run_start, 0.0), (*run_end, 0.0)
            )
            if distance > 0.05:
                break
            brackets = [
                (left, right)
                for left, right in zip(projected, projected[1:])
                if left[0] < fraction < right[0]
            ]
            if len(brackets) != 1:
                break
            left, right = brackets[0]
            local_fraction = (fraction - left[0]) / (right[0] - left[0])
            along = left[1] + local_fraction * (right[1] - left[1])
            if not 0.0 < along < support_span:
                break
            if not any(low - 5.0 <= along <= high + 5.0
                       for low, high in aligned_intervals):
                break
            page_result[int(record.excel_row)] = {
                "uci": f"{{CONSTRUCTION-TABLE-{record.excel_row}}}",
                "graphic_ref": 0xD0000000
                | (zlib.crc32(f"{{CONSTRUCTION-TABLE-{record.excel_row}}}".encode("ascii")) & 0x0FFFFFFF),
                "page": sheet.page,
                "sheet_stream": sheet.stream_name,
                "sha_x": round(first_xy[0] + along * support_direction[0], 3),
                "sha_y": round(first_xy[1] + along * support_direction[1], 3),
                "distance_to_geometry": 0.0,
                "pipe_axis": [list(first_xy), list(last_xy)],
                "manual_review_required": True,
                "manual_review_reason": "材料唯一码链及相邻支架UCI分段定位推断",
                "point_evidence": "construction-table-support-bracketed-pipe-chain",
                "support_bracket_ucis": [left[2], right[2]],
                "topology_rule": (
                    "complete linear material-instance chain + PCF run length + "
                    "3D weld positions + unique UCI-matched support brackets + "
                    "single parallel SHA pipe axis"
                ),
            }
        if len(page_result) == len(chain_records):
            valid_pages += 1
            result = page_result
    return result if valid_pages == 1 else {}


def add_construction_virtual_targets(
    payload: dict[str, object],
    records: list[object],
    pcf_path: Path,
    sha_path: Path,
    coordinate_tolerance: float = 1.0,
    pipe_axis_tolerance: float = 0.05,
) -> dict[str, object]:
    """Add only uniquely proven non-PCF table weld targets to a 100200 map."""

    output = dict(payload)
    base_rows = list(payload.get("welds", []))

    def has_pcf_weld(record: object) -> bool:
        expected = tuple(float(value) for value in record.coordinate)
        for row in base_rows:
            endpoint = row.get("pcf_endpoint")
            if not isinstance(endpoint, list) or len(endpoint) < 3:
                continue
            if max(abs(float(endpoint[index]) - expected[index]) for index in range(3)) <= coordinate_tolerance:
                return True
        return False

    pending_records = [record for record in records if not has_pcf_weld(record)]
    coordinate_counts = Counter(_coordinate_key(record.coordinate) for record in pending_records)
    pending_records = [
        record
        for record in pending_records
        if coordinate_counts[_coordinate_key(record.coordinate)] == 1
    ]
    source_lines, pcf_welds = parse_weld_blocks(pcf_path)
    pipes = parse_pipe_components(source_lines)
    known_points = {
        str(row.get("uci")): dict(row)
        for row in base_rows
        if row.get("uci") and row.get("graphic_ref") is not None
    }

    def unique_sha_root_at(
        coordinate: tuple[float, float, float],
    ) -> tuple[int, float, float, str] | None:
        roots: dict[tuple[int, float, float], set[str]] = defaultdict(set)
        for row in base_rows:
            endpoint = row.get("pcf_endpoint")
            if not isinstance(endpoint, list) or len(endpoint) < 3:
                continue
            if row.get("graphic_ref") is None or row.get("page") in (None, 999999):
                continue
            try:
                # PCF generators commonly round a page continuation endpoint
                # a few millimetres differently from the adjacent pipe run.
                # Five millimetres is still a strict topology tolerance here:
                # the resulting SHA root and UCI must remain unique.
                if max(
                    abs(float(endpoint[index]) - coordinate[index])
                    for index in range(3)
                ) > max(coordinate_tolerance, 5.0):
                    continue
                root = (
                    int(row["page"]),
                    round(float(row["sha_x"]), 3),
                    round(float(row["sha_y"]), 3),
                )
            except (KeyError, TypeError, ValueError):
                continue
            roots[root].add(str(row.get("uci", "")))
        # Earlier rules in this same pass may already have recovered an
        # adjacent virtual split.  Treat that topology-validated point exactly
        # like a native base root so a longer material chain can be resolved
        # incrementally from its newly proven neighbour.
        for candidate_record, _weld, point, _rule in candidates:
            try:
                candidate_coordinate = tuple(
                    float(value) for value in candidate_record.coordinate
                )
                if max(
                    abs(candidate_coordinate[index] - coordinate[index])
                    for index in range(3)
                ) > max(coordinate_tolerance, 5.0):
                    continue
                if point.get("graphic_ref") is None or point.get("page") in (None, 999999):
                    continue
                root = (
                    int(point["page"]),
                    round(float(point["sha_x"]), 3),
                    round(float(point["sha_y"]), 3),
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            roots[root].add(str(point.get("uci", "")))
        if len(roots) != 1:
            return None
        physical_root, ucis = next(iter(roots.items()))
        # Multiple PCF records can legitimately describe the two sides of one
        # plotted junction.  Ambiguity is geometric, not the count of UCIs at
        # the exact same SHA root; preserve one stable UCI only as orientation
        # evidence for the downstream virtual-pipe mapper.
        return (*physical_root, sorted(ucis)[0] if ucis else "")

    candidates: list[tuple[object, WeldBlock, dict[str, object], str]] = []
    recovered_rows: set[int] = set()

    # An exact PIPE-to-OLET/TEE-STUB junction is stronger than the generic
    # PIPE-end rule below.  Resolving the pipe end first can orient a repeated
    # long-pipe UCI toward the remote page endpoint, consume the OLET weld's
    # position and in turn displace the neighbouring internal split.  Let the
    # two independently owned component graphics prove this interface first.
    topology_source_lines = _connection_topology_source_lines(pcf_path, source_lines)
    early_component_ports = _component_ports_at_coordinates(topology_source_lines)
    early_interface_inputs: list[tuple[object, WeldBlock]] = []
    for record in pending_records:
        members = _component_ports_near_coordinate(
            early_component_ports,
            record.coordinate,
            tolerance=coordinate_tolerance,
        )
        distinct_ucis = {member[1] for member in members}
        if (
            len(distinct_ucis) == 2
            and any(member[0] in {"OLET", "TEE-STUB"} for member in members)
            and any(member[0] in {"PIPE", "PIPE-FIXED"} for member in members)
        ):
            early_interface_inputs.append((record, _synthetic_table_weld(record)))
    early_interface_points = (
        _sha_two_sided_component_junctions(
            sha_path,
            topology_source_lines,
            [weld for _, weld in early_interface_inputs],
        )
        if early_interface_inputs
        else {}
    )
    for record, weld in early_interface_inputs:
        point = early_interface_points.get(weld.uci)
        if point is None:
            continue
        candidates.append(
            (record, weld, point, "construction-table-component-interface")
        )
        recovered_rows.add(int(record.excel_row))

    endpoint_inputs = [
        _synthetic_table_weld(record)
        for record in pending_records
        if int(record.excel_row) not in recovered_rows
    ]
    endpoint_points = sha_implicit_pipe_end_weld_points(
        sha_path,
        source_lines,
        pcf_welds + endpoint_inputs,
        known_points,
        include_unnumbered=True,
    )
    endpoint_records = [
        record
        for record in pending_records
        if int(record.excel_row) not in recovered_rows
    ]
    for record, weld in zip(endpoint_records, endpoint_inputs):
        point = endpoint_points.get(weld.uci)
        if point is None:
            continue
        candidates.append((record, weld, point, "construction-table-pcf-topology-endpoint"))
        recovered_rows.add(int(record.excel_row))

    component_ucis = _component_ucis_at_coordinates(source_lines)
    interior_inputs: list[tuple[object, WeldBlock]] = []
    interior_overrides: dict[str, dict[str, object]] = {}
    interior_contexts: dict[int, dict[str, object]] = {}
    metre_units = {"米", "m", "meter", "metre", "meters", "metres"}
    for record in pending_records:
        if int(record.excel_row) in recovered_rows:
            continue
        coordinate = tuple(float(value) for value in record.coordinate)
        pipe_candidates: list[tuple[object, float]] = []
        for pipe in pipes:
            distance, fraction = _point_segment_projection(
                coordinate, pipe.endpoint_a, pipe.endpoint_b
            )
            if distance <= pipe_axis_tolerance and 0.0 < fraction < 1.0:
                pipe_candidates.append((pipe, fraction))
        if len(pipe_candidates) != 1:
            continue
        owner, _ = pipe_candidates[0]
        run_result = _connected_collinear_pipe_run(
            owner, pipes, axis_tolerance=pipe_axis_tolerance
        )
        if run_result is None:
            continue
        _, run_start, run_end = run_result
        run_distance, run_fraction = _point_segment_projection(
            coordinate, (*run_start, 0.0), (*run_end, 0.0)
        )
        if run_distance > pipe_axis_tolerance or not 0.0 < run_fraction < 1.0:
            continue
        neighbor_a_candidates = sorted(
            uci
            for uci in component_ucis.get(_coordinate_key(run_start), set())
            if uci != owner.uci
        )
        neighbor_b_candidates = sorted(
            uci
            for uci in component_ucis.get(_coordinate_key(run_end), set())
            if uci != owner.uci
        )
        neighbor_a = (
            neighbor_a_candidates[0] if len(neighbor_a_candidates) == 1 else ""
        )
        neighbor_b = (
            neighbor_b_candidates[0] if len(neighbor_b_candidates) == 1 else ""
        )
        start_root = unique_sha_root_at(run_start)
        end_root = unique_sha_root_at(run_end)
        interior_contexts[int(record.excel_row)] = {
            "owner": owner,
            "run_start": run_start,
            "run_end": run_end,
            "run_fraction": run_fraction,
            "start_root": start_root,
            "end_root": end_root,
        }
        if not 0.02 < run_fraction < 0.98:
            code_a = str(getattr(record, "material_code_a", "")).strip()
            code_b = str(getattr(record, "material_code_b", "")).strip()
            unit_a = str(getattr(record, "unit_a", "")).strip().casefold()
            unit_b = str(getattr(record, "unit_b", "")).strip().casefold()
            quantity_a = getattr(record, "quantity_a", None)
            quantity_b = getattr(record, "quantity_b", None)
            run_length = math.dist(run_start, run_end)
            endpoint_distances = sorted(
                (
                    math.dist(coordinate, run_start),
                    math.dist(coordinate, run_end),
                )
            )
            table_lengths = sorted(
                (
                    float(quantity_a or 0) * 1000.0,
                    float(quantity_b or 0) * 1000.0,
                )
            )
            strict_two_anchor_split = (
                bool(code_a)
                and code_a == code_b
                and unit_a in metre_units
                and unit_b in metre_units
                and quantity_a is not None
                and quantity_b is not None
                and float(quantity_a) > 0
                and float(quantity_b) > 0
                and abs(sum(table_lengths) - run_length) <= 10.0
                and max(
                    abs(table_lengths[index] - endpoint_distances[index])
                    for index in range(2)
                )
                <= 10.0
                and start_root is not None
                and end_root is not None
                and start_root[0] == end_root[0]
                and start_root[1:3] != end_root[1:3]
            )
            if not strict_two_anchor_split:
                continue
        # Explicit WELD blocks and page END-CONNECTIONs are not returned by
        # the component-neighbour lookup.  Their already proven SHA weld root
        # is nevertheless valid endpoint/orientation evidence for this pipe
        # run.  Use its UCI only when the root is unique.
        if not neighbor_a and start_root is not None:
            neighbor_a = start_root[3]
        if not neighbor_b and end_root is not None:
            neighbor_b = end_root[3]
        if not neighbor_a and not neighbor_b:
            continue
        weld = _synthetic_table_weld(record)
        weld.augmentation_source = "PCF-AUGMENTED"
        weld.augmentation_mode = "TABLE-COLLINEAR-PIPE-RUN"
        weld.master_component_identifier = owner.component_identifier
        weld.owner_uci = owner.uci
        weld.owner_fraction = run_fraction
        weld.neighbor_uci_a = neighbor_a
        weld.neighbor_uci_b = neighbor_b
        interior_inputs.append((record, weld))
        if (
            start_root is not None
            and end_root is not None
            and start_root[0] == end_root[0]
            and start_root[1:3] != end_root[1:3]
        ):
            interior_overrides[weld.uci] = {
                "mode": "PCF-ENDPOINT-BRACKET",
                "endpoint_bracket": {
                    "page": start_root[0],
                    "start": [start_root[1], start_root[2]],
                    "end": [end_root[1], end_root[2]],
                },
            }

    interior_points = sha_virtual_weld_points(
        sha_path,
        [weld for _, weld in interior_inputs],
        known_points,
        interior_overrides,
    )
    for record, weld in interior_inputs:
        point = interior_points.get(weld.uci)
        if point is not None:
            candidates.append(
                (record, weld, point, "construction-table-collinear-pipe-run")
            )
            recovered_rows.add(int(record.excel_row))

    # Review-only inference for a terminal pipe split.  Some exported PCFs
    # contain one unsplit PIPE ending at the page boundary, so only the weld
    # at the fitting end has a native SHA root.  The construction table can
    # still prove the chain when both F8 sides are pipe measured in metres,
    # share one material code, and one material-instance ID is also present
    # on exactly one already mapped neighbouring weld (for example F7).
    # Require the two table lengths to reproduce the complete PCF run and use
    # their ratio only on a unique dominant SHA segment incident to that root.
    material_chain_inputs: list[tuple[object, WeldBlock, dict[str, object]]] = []
    streams_for_material_chain: dict[str, bytes] | None = None
    sheets_for_material_chain: dict[int, object] = {}
    for record in pending_records:
        if int(record.excel_row) in recovered_rows:
            continue
        context = interior_contexts.get(int(record.excel_row))
        if context is None:
            continue
        code_a = str(getattr(record, "material_code_a", "")).strip()
        code_b = str(getattr(record, "material_code_b", "")).strip()
        unit_a = str(getattr(record, "unit_a", "")).strip().casefold()
        unit_b = str(getattr(record, "unit_b", "")).strip().casefold()
        quantity_a = getattr(record, "quantity_a", None)
        quantity_b = getattr(record, "quantity_b", None)
        uci_a = str(getattr(record, "material_uci_a", "")).strip()
        uci_b = str(getattr(record, "material_uci_b", "")).strip()
        if (
            not code_a
            or code_a != code_b
            or unit_a not in metre_units
            or unit_b not in metre_units
            or quantity_a is None
            or quantity_b is None
            or float(quantity_a) <= 0
            or float(quantity_b) <= 0
            or not uci_a
            or not uci_b
            or uci_a == uci_b
        ):
            continue
        material_links: list[tuple[object, str, tuple[int, float, float, str]]] = []
        target_ucis = {uci_a, uci_b}
        for peer in records:
            if int(peer.excel_row) == int(record.excel_row):
                continue
            shared = target_ucis & {
                str(getattr(peer, "material_uci_a", "")).strip(),
                str(getattr(peer, "material_uci_b", "")).strip(),
            }
            if len(shared) != 1:
                continue
            root = unique_sha_root_at(tuple(float(value) for value in peer.coordinate))
            if root is not None:
                material_links.append((peer, next(iter(shared)), root))
        # The strongest split-pipe case has mapped weld roots on both sides
        # of the missing table weld.  Material-instance IDs identify which
        # pipe length belongs to each side; 3D distances must reproduce both
        # lengths and the target must lie between the two peer coordinates.
        # Interpolate only between those two already proven SHA roots, never
        # from an unrelated longest line near either endpoint.
        if len(material_links) == 2:
            first_link, second_link = material_links
            first_peer, first_uci, first_root = first_link
            second_peer, second_uci, second_root = second_link
            first_coordinate = tuple(float(value) for value in first_peer.coordinate)
            second_coordinate = tuple(float(value) for value in second_peer.coordinate)
            target_coordinate = tuple(float(value) for value in record.coordinate)
            first_distance = math.dist(first_coordinate, target_coordinate)
            second_distance = math.dist(second_coordinate, target_coordinate)
            peer_distance = math.dist(first_coordinate, second_coordinate)
            length_by_uci = {uci_a: float(quantity_a), uci_b: float(quantity_b)}
            strict_two_sided_bracket = (
                first_uci != second_uci
                and {first_uci, second_uci} == {uci_a, uci_b}
                and first_root[0] == second_root[0]
                and first_root[1:3] != second_root[1:3]
                and abs(first_distance - length_by_uci[first_uci] * 1000.0) <= 10.0
                and abs(second_distance - length_by_uci[second_uci] * 1000.0) <= 10.0
                and abs(first_distance + second_distance - peer_distance) <= 10.0
            )
            if strict_two_sided_bracket and peer_distance > 0:
                fraction = first_distance / peer_distance
                sha_x = first_root[1] + (second_root[1] - first_root[1]) * fraction
                sha_y = first_root[2] + (second_root[2] - first_root[2]) * fraction
                weld = _synthetic_table_weld(record)
                owner = context["owner"]
                weld.augmentation_source = "PCF-AUGMENTED"
                weld.augmentation_mode = "TABLE-MATERIAL-CHAIN-TWO-SIDED-BRACKET"
                weld.master_component_identifier = owner.component_identifier
                weld.owner_uci = owner.uci
                weld.owner_fraction = float(context["run_fraction"])
                point = {
                    "uci": weld.uci,
                    "graphic_ref": 0xD0000000
                    | (zlib.crc32(weld.uci.encode("ascii")) & 0x0FFFFFFF),
                    "page": first_root[0],
                    "sheet_stream": sheets_for_material_chain.get(first_root[0]).stream_name
                    if sheets_for_material_chain and sheets_for_material_chain.get(first_root[0])
                    else "",
                    "sha_x": round(sha_x, 3),
                    "sha_y": round(sha_y, 3),
                    "distance_to_geometry": 0.0,
                    "pipe_axis": [list(first_root[1:3]), list(second_root[1:3])],
                    "pipe_visible_segment": [list(first_root[1:3]), list(second_root[1:3])],
                    "manual_review_required": True,
                    "manual_review_reason": "两侧已映射焊口及材料管长比例推断",
                    "point_evidence": "construction-table-material-chain-two-sided-bracket",
                    "topology_rule": (
                        "two mapped peer weld roots + two material-instance IDs + "
                        "two-sided 3D length validation"
                    ),
                }
                # Resolve the stream name lazily without changing the proven
                # page/root coordinates above.
                if not point["sheet_stream"]:
                    if streams_for_material_chain is None:
                        streams_for_material_chain = read_sha_streams(sha_path)
                        sheets_for_material_chain = {
                            sheet.page: sheet
                            for sheet in sheet_streams(streams_for_material_chain, None)
                        }
                    sheet = sheets_for_material_chain.get(first_root[0])
                    if sheet is None:
                        continue
                    point["sheet_stream"] = sheet.stream_name
                candidates.append(
                    (
                        record,
                        weld,
                        point,
                        "construction-table-material-chain-two-sided-bracket",
                    )
                )
                recovered_rows.add(int(record.excel_row))
                continue
        if len(material_links) != 1:
            continue
        peer, shared_uci, root = material_links[0]
        run_start = context["run_start"]
        run_end = context["run_end"]
        peer_coordinate = tuple(float(value) for value in peer.coordinate)
        target_coordinate = tuple(float(value) for value in record.coordinate)
        root_at_start = math.dist(peer_coordinate, run_start) <= 5.0
        root_at_end = math.dist(peer_coordinate, run_end) <= 5.0
        if root_at_start == root_at_end:
            continue
        shared_length_m = float(quantity_a if shared_uci == uci_a else quantity_b)
        other_length_m = float(quantity_b if shared_uci == uci_a else quantity_a)
        run_length = math.dist(run_start, run_end)
        if abs(run_length - (shared_length_m + other_length_m) * 1000.0) > 10.0:
            continue
        if abs(math.dist(peer_coordinate, target_coordinate) - shared_length_m * 1000.0) > 10.0:
            continue
        if streams_for_material_chain is None:
            streams_for_material_chain = read_sha_streams(sha_path)
            sheets_for_material_chain = {
                sheet.page: sheet
                for sheet in sheet_streams(streams_for_material_chain, None)
            }
        sheet = sheets_for_material_chain.get(root[0])
        if sheet is None:
            continue
        root_xy = (root[1], root[2])
        incident: list[tuple[float, tuple[float, float], tuple[float, float], int]] = []
        for x1, y1, x2, y2, graphic_ref, _ in (
            line_segments(sheet.data) + template_line_segments(sheet.data)
        ):
            first = (x1 * SHEET_UNIT, y1 * SHEET_UNIT)
            second = (x2 * SHEET_UNIT, y2 * SHEET_UNIT)
            first_distance = math.dist(root_xy, first)
            second_distance = math.dist(root_xy, second)
            if min(first_distance, second_distance) > 3.0:
                continue
            near, far = (first, second) if first_distance <= second_distance else (second, first)
            incident.append((math.dist(first, second), near, far, int(graphic_ref)))
        owner = context["owner"]
        dynamic = _dynamic_graphics_including_first_record(
            streams_for_material_chain.get("Unclustered Dynamic Attributes", b"")
        )
        owner_refs = {
            int(item["graphic_ref"]) & 0xFFFF
            for uci, items in dynamic.items()
            if uci.upper() == str(owner.uci).upper()
            for item in items
        }
        owned_incident = [
            item for item in incident if (item[3] & 0xFFFF) in owner_refs
        ]
        if owned_incident:
            incident = owned_incident
        incident.sort(key=lambda item: item[0], reverse=True)
        if not incident or incident[0][0] < 100.0:
            continue
        if len(incident) > 1 and incident[0][0] < incident[1][0] * 2.0:
            continue
        _, _, far, graphic_ref = incident[0]
        fraction = shared_length_m / (shared_length_m + other_length_m)
        sha_x = root_xy[0] + (far[0] - root_xy[0]) * fraction
        sha_y = root_xy[1] + (far[1] - root_xy[1]) * fraction
        weld = _synthetic_table_weld(record)
        weld.augmentation_source = "PCF-AUGMENTED"
        weld.augmentation_mode = "TABLE-MATERIAL-CHAIN-TERMINAL-PIPE"
        weld.master_component_identifier = owner.component_identifier
        weld.owner_uci = owner.uci
        weld.owner_fraction = float(context["run_fraction"])
        point = {
            "uci": weld.uci,
            "graphic_ref": 0xD0000000
            | (zlib.crc32(weld.uci.encode("ascii")) & 0x0FFFFFFF),
            "page": root[0],
            "sheet_stream": sheet.stream_name,
            "sha_x": round(sha_x, 3),
            "sha_y": round(sha_y, 3),
            "distance_to_geometry": 0.0,
            "pipe_axis": [list(root_xy), list(far)],
            "pipe_visible_segment": [list(root_xy), list(far)],
            "material_chain_peer_weld": str(peer.weld_number),
            "material_chain_shared_uci": shared_uci,
            "material_chain_lengths_m": [shared_length_m, other_length_m],
            "manual_review_required": True,
            "manual_review_reason": "材料唯一码链及两侧管长比例推断",
            "point_evidence": "construction-table-material-chain-length-ratio",
            "topology_rule": (
                "unique mapped peer weld + shared pipe material instance + "
                "terminal PCF run length ratio + dominant SHA segment"
            ),
        }
        material_chain_inputs.append((record, weld, point))
        recovered_rows.add(int(record.excel_row))

    for record, weld, point in material_chain_inputs:
        candidates.append(
            (
                record,
                weld,
                point,
                "construction-table-material-chain-terminal-pipe",
            )
        )

    # Multi-hop pipe material chain.  A long shop pipe may contain two or
    # more consecutive construction splits while only the terminal fitting
    # weld has a native SHA root.  Prove a linear chain from material-instance
    # IDs and lengths, validate every cumulative position against the table's
    # 3D coordinate on the same complete PCF run, then distribute the roots
    # along the single dominant SHA segment incident to the terminal weld.
    chain_groups: dict[
        tuple[tuple[float, float, float], tuple[float, float, float]],
        list[object],
    ] = defaultdict(list)
    for record in pending_records:
        row = int(record.excel_row)
        if row in recovered_rows or row not in interior_contexts:
            continue
        code_a = str(getattr(record, "material_code_a", "")).strip()
        code_b = str(getattr(record, "material_code_b", "")).strip()
        unit_a = str(getattr(record, "unit_a", "")).strip().casefold()
        unit_b = str(getattr(record, "unit_b", "")).strip().casefold()
        quantity_a = getattr(record, "quantity_a", None)
        quantity_b = getattr(record, "quantity_b", None)
        uci_a = str(getattr(record, "material_uci_a", "")).strip()
        uci_b = str(getattr(record, "material_uci_b", "")).strip()
        if (
            not code_a
            or code_a != code_b
            or unit_a not in metre_units
            or unit_b not in metre_units
            or quantity_a is None
            or quantity_b is None
            or float(quantity_a) <= 0
            or float(quantity_b) <= 0
            or not uci_a
            or not uci_b
            or uci_a == uci_b
        ):
            continue
        context = interior_contexts[row]
        endpoints = sorted(
            (
                _coordinate_key(context["run_start"]),
                _coordinate_key(context["run_end"]),
            )
        )
        chain_groups[(endpoints[0], endpoints[1])].append(record)

    for chain_records in chain_groups.values():
        if len(chain_records) < 2:
            continue
        first_context = interior_contexts[int(chain_records[0].excel_row)]
        run_start = first_context["run_start"]
        run_end = first_context["run_end"]
        run_length = math.dist(run_start, run_end)
        graph: dict[str, list[tuple[str, object]]] = defaultdict(list)
        node_lengths: dict[str, float] = {}
        consistent = True
        for record in chain_records:
            uci_a = str(record.material_uci_a).strip()
            uci_b = str(record.material_uci_b).strip()
            for uci, quantity in (
                (uci_a, float(record.quantity_a)),
                (uci_b, float(record.quantity_b)),
            ):
                previous = node_lengths.get(uci)
                if previous is not None and abs(previous - quantity) > 0.01:
                    consistent = False
                node_lengths[uci] = quantity
            graph[uci_a].append((uci_b, record))
            graph[uci_b].append((uci_a, record))
        if (
            not consistent
            or any(len(neighbors) > 2 for neighbors in graph.values())
            or len(graph) != len(chain_records) + 1
            or abs(sum(node_lengths.values()) * 1000.0 - run_length) > 10.0
        ):
            continue

        anchors: list[tuple[str, object, tuple[int, float, float, str], bool]] = []
        for peer in records:
            if peer in chain_records:
                continue
            peer_ucis = {
                str(getattr(peer, "material_uci_a", "")).strip(),
                str(getattr(peer, "material_uci_b", "")).strip(),
            }
            shared = set(graph) & peer_ucis
            if len(shared) != 1:
                continue
            peer_coordinate = tuple(float(value) for value in peer.coordinate)
            root_at_start = math.dist(peer_coordinate, run_start) <= 5.0
            root_at_end = math.dist(peer_coordinate, run_end) <= 5.0
            if root_at_start == root_at_end:
                continue
            root = unique_sha_root_at(peer_coordinate)
            if root is not None:
                anchors.append((next(iter(shared)), peer, root, root_at_start))
        anchor_keys = {
            (node, root[0], root[1], root[2], root_at_start)
            for node, _peer, root, root_at_start in anchors
        }
        if len(anchor_keys) != 1:
            # When neither terminal weld is visible, a complete straight-pipe
            # material chain can still be placed if several independently
            # UCI-matched supports bracket *every* internal split on one SHA
            # pipe stroke. Never extrapolate beyond the support range.
            support_points = _support_bracketed_pipe_chain(
                source_lines,
                sha_path,
                str(first_context["owner"].uci),
                run_start,
                run_end,
                chain_records,
            )
            if len(support_points) == len(chain_records):
                for record in chain_records:
                    weld = _synthetic_table_weld(record)
                    context = interior_contexts[int(record.excel_row)]
                    owner = context["owner"]
                    weld.augmentation_source = "PCF-AUGMENTED"
                    weld.augmentation_mode = "TABLE-MATERIAL-CHAIN-SUPPORT-BRACKETED"
                    weld.master_component_identifier = owner.component_identifier
                    weld.owner_uci = owner.uci
                    weld.owner_fraction = float(context["run_fraction"])
                    candidates.append((
                        record,
                        weld,
                        support_points[int(record.excel_row)],
                        "construction-table-material-chain-support-bracketed-pipe",
                    ))
                    recovered_rows.add(int(record.excel_row))
            continue
        anchor_node, peer, root, root_at_start = anchors[0]
        if len(graph[anchor_node]) != 1:
            continue

        ordered: list[tuple[object, float]] = []
        visited_records: set[int] = set()
        current = anchor_node
        cumulative_m = 0.0
        while True:
            cumulative_m += node_lengths[current]
            next_edges = [
                (neighbor, record)
                for neighbor, record in graph[current]
                if int(record.excel_row) not in visited_records
            ]
            if not next_edges:
                break
            if len(next_edges) != 1:
                ordered = []
                break
            neighbor, record = next_edges[0]
            visited_records.add(int(record.excel_row))
            ordered.append((record, cumulative_m))
            current = neighbor
        if len(ordered) != len(chain_records):
            continue

        anchor_coordinate = run_start if root_at_start else run_end
        if any(
            abs(
                math.dist(
                    anchor_coordinate,
                    tuple(float(value) for value in record.coordinate),
                )
                - cumulative_m * 1000.0
            )
            > 10.0
            for record, cumulative_m in ordered
        ):
            continue

        if streams_for_material_chain is None:
            streams_for_material_chain = read_sha_streams(sha_path)
            sheets_for_material_chain = {
                sheet.page: sheet
                for sheet in sheet_streams(streams_for_material_chain, None)
            }
        sheet = sheets_for_material_chain.get(root[0])
        if sheet is None:
            continue
        root_xy = (root[1], root[2])
        incident: list[
            tuple[float, tuple[float, float], tuple[float, float], int]
        ] = []
        for x1, y1, x2, y2, graphic_ref, _ in (
            line_segments(sheet.data) + template_line_segments(sheet.data)
        ):
            first = (x1 * SHEET_UNIT, y1 * SHEET_UNIT)
            second = (x2 * SHEET_UNIT, y2 * SHEET_UNIT)
            first_distance = math.dist(root_xy, first)
            second_distance = math.dist(root_xy, second)
            if min(first_distance, second_distance) > 3.0:
                continue
            near, far = (
                (first, second)
                if first_distance <= second_distance
                else (second, first)
            )
            incident.append(
                (math.dist(first, second), near, far, int(graphic_ref))
            )
        owner = first_context["owner"]
        dynamic = _dynamic_graphics_including_first_record(
            streams_for_material_chain.get("Unclustered Dynamic Attributes", b"")
        )
        owner_refs = {
            int(item["graphic_ref"]) & 0xFFFF
            for uci, items in dynamic.items()
            if uci.upper() == str(owner.uci).upper()
            for item in items
        }
        owned_incident = [
            item for item in incident if (item[3] & 0xFFFF) in owner_refs
        ]
        if owned_incident:
            incident = owned_incident
        incident.sort(key=lambda item: item[0], reverse=True)
        if not incident or incident[0][0] < 100.0:
            continue
        if len(incident) > 1 and incident[0][0] < incident[1][0] * 2.0:
            continue
        initial_length, _, far, graphic_ref = incident[0]

        # This rule is deliberately local to cumulative material-instance
        # chains.  A long plotted PIPE can be serialized as several collinear
        # strokes under one SHA parent (supports and flow arrows leave small
        # gaps).  Using only the first incident stroke compresses every
        # engineering fraction into the elbow-side stub.  Extend only along
        # the already-proven, unique incident direction and the exact same
        # parent graphic; no other virtual-weld rule uses this recovery.
        direction_x = (far[0] - root_xy[0]) / initial_length
        direction_y = (far[1] - root_xy[1]) / initial_length
        aligned_segments: list[
            tuple[float, float, tuple[float, float], tuple[float, float]]
        ] = []
        for x1, y1, x2, y2, candidate_ref, _ in (
            line_segments(sheet.data) + template_line_segments(sheet.data)
        ):
            if int(candidate_ref) != graphic_ref:
                continue
            first = (x1 * SHEET_UNIT, y1 * SHEET_UNIT)
            second = (x2 * SHEET_UNIT, y2 * SHEET_UNIT)
            length = math.dist(first, second)
            if length < 80.0:
                continue
            vx = (second[0] - first[0]) / length
            vy = (second[1] - first[1]) / length
            if abs(vx * direction_x + vy * direction_y) < 0.992:
                continue
            offsets = [
                abs(
                    (point[0] - root_xy[0]) * direction_y
                    - (point[1] - root_xy[1]) * direction_x
                )
                for point in (first, second)
            ]
            if max(offsets) > 12.0:
                continue
            projections = [
                (point[0] - root_xy[0]) * direction_x
                + (point[1] - root_xy[1]) * direction_y
                for point in (first, second)
            ]
            low, high = min(projections), max(projections)
            if high < -10.0:
                continue
            aligned_segments.append((low, high, first, second))

        connected_segments: list[
            tuple[float, float, tuple[float, float], tuple[float, float]]
        ] = []
        connected_high = initial_length
        remaining = sorted(aligned_segments, key=lambda item: (item[0], item[1]))
        while True:
            admitted = [
                item
                for item in remaining
                if item[0] <= connected_high + 180.0 and item[1] >= -10.0
            ]
            if not admitted:
                break
            new_high = max(connected_high, *(item[1] for item in admitted))
            connected_segments.extend(admitted)
            admitted_ids = {id(item) for item in admitted}
            remaining = [item for item in remaining if id(item) not in admitted_ids]
            if new_high <= connected_high + 1e-6:
                break
            connected_high = new_high

        axis_mode = "single-incident-stroke"
        if connected_high >= initial_length * 2.0:
            far = (
                root_xy[0] + connected_high * direction_x,
                root_xy[1] + connected_high * direction_y,
            )
            axis_mode = "same-parent-connected-collinear-chain"

        for record, cumulative_m in ordered:
            fraction = cumulative_m * 1000.0 / run_length
            sha_x = root_xy[0] + (far[0] - root_xy[0]) * fraction
            sha_y = root_xy[1] + (far[1] - root_xy[1]) * fraction
            if axis_mode == "same-parent-connected-collinear-chain":
                snap_choices: list[tuple[float, float, float]] = []
                for _low, _high, first, second in connected_segments:
                    dx, dy = second[0] - first[0], second[1] - first[1]
                    denominator = dx * dx + dy * dy
                    ratio = (
                        0.0
                        if denominator <= 1e-12
                        else max(
                            0.0,
                            min(
                                1.0,
                                ((sha_x - first[0]) * dx + (sha_y - first[1]) * dy)
                                / denominator,
                            ),
                        )
                    )
                    snapped_x = first[0] + ratio * dx
                    snapped_y = first[1] + ratio * dy
                    snap_choices.append(
                        (
                            math.hypot(sha_x - snapped_x, sha_y - snapped_y),
                            snapped_x,
                            snapped_y,
                        )
                    )
                if snap_choices:
                    snap_distance, snapped_x, snapped_y = min(snap_choices)
                    if snap_distance <= 180.0:
                        sha_x, sha_y = snapped_x, snapped_y
            weld = _synthetic_table_weld(record)
            context = interior_contexts[int(record.excel_row)]
            owner = context["owner"]
            weld.augmentation_source = "PCF-AUGMENTED"
            weld.augmentation_mode = "TABLE-MATERIAL-CHAIN-CUMULATIVE"
            weld.master_component_identifier = owner.component_identifier
            weld.owner_uci = owner.uci
            weld.owner_fraction = float(context["run_fraction"])
            point = {
                "uci": weld.uci,
                "graphic_ref": 0xD0000000
                | (zlib.crc32(weld.uci.encode("ascii")) & 0x0FFFFFFF),
                "page": root[0],
                "sheet_stream": sheet.stream_name,
                "sha_x": round(sha_x, 3),
                "sha_y": round(sha_y, 3),
                "distance_to_geometry": 0.0,
                "pipe_axis": [list(root_xy), list(far)],
                "pipe_visible_segment": [list(root_xy), list(far)],
                "material_chain_peer_weld": str(peer.weld_number),
                "material_chain_anchor_uci": anchor_node,
                "material_chain_cumulative_m": cumulative_m,
                "material_chain_total_m": run_length / 1000.0,
                "material_chain_sha_axis_mode": axis_mode,
                "material_chain_sha_axis_length": math.dist(root_xy, far),
                "manual_review_required": True,
                "manual_review_reason": "连续材料唯一码链及累计管长比例推断",
                "point_evidence": "construction-table-material-chain-cumulative-ratio",
                "topology_rule": (
                    "unique terminal mapped weld + linear material-instance chain + "
                    "complete PCF run length + cumulative 3D coordinate validation"
                ),
            }
            candidates.append(
                (
                    record,
                    weld,
                    point,
                    "construction-table-material-chain-cumulative-pipe",
                )
            )
            recovered_rows.add(int(record.excel_row))

    # One residual edge at the terminal end of an otherwise proven material
    # chain.  Earlier rules intentionally consume the well-bracketed internal
    # splits first.  That used to leave the final split unresolved because the
    # remaining-record cumulative rule requires at least two edges.  Recover
    # only the narrow case where the complete (recovered + residual) table
    # chain is a single linear pipe run, exactly one residual edge touches a
    # leaf, and earlier recovered roots prove one unique full SHA pipe axis.
    complete_chain_groups: dict[
        tuple[tuple[float, float, float], tuple[float, float, float]],
        list[object],
    ] = defaultdict(list)
    for record in pending_records:
        row = int(record.excel_row)
        context = interior_contexts.get(row)
        if context is None:
            continue
        code_a = str(getattr(record, "material_code_a", "")).strip()
        code_b = str(getattr(record, "material_code_b", "")).strip()
        unit_a = str(getattr(record, "unit_a", "")).strip().casefold()
        unit_b = str(getattr(record, "unit_b", "")).strip().casefold()
        quantity_a = getattr(record, "quantity_a", None)
        quantity_b = getattr(record, "quantity_b", None)
        uci_a = str(getattr(record, "material_uci_a", "")).strip()
        uci_b = str(getattr(record, "material_uci_b", "")).strip()
        if (
            not code_a
            or code_a != code_b
            or unit_a not in metre_units
            or unit_b not in metre_units
            or quantity_a is None
            or quantity_b is None
            or float(quantity_a) <= 0
            or float(quantity_b) <= 0
            or not uci_a
            or not uci_b
            or uci_a == uci_b
        ):
            continue
        endpoints = sorted(
            (
                _coordinate_key(context["run_start"]),
                _coordinate_key(context["run_end"]),
            )
        )
        complete_chain_groups[(endpoints[0], endpoints[1])].append(record)

    pending_row_ids = {int(record.excel_row) for record in pending_records}
    for seed_records in complete_chain_groups.values():
        seed_context = interior_contexts[int(seed_records[0].excel_row)]
        seed_start = seed_context["run_start"]
        seed_end = seed_context["run_end"]
        # A previously mapped internal split can originate from an explicit
        # PCF WELD and therefore is absent from pending_records.  Reattach
        # such table rows only when their coordinate is on this exact run and
        # their material-instance edge is connected to the seed component.
        eligible_on_run: list[object] = []
        for record in records:
            code_a = str(getattr(record, "material_code_a", "")).strip()
            code_b = str(getattr(record, "material_code_b", "")).strip()
            unit_a = str(getattr(record, "unit_a", "")).strip().casefold()
            unit_b = str(getattr(record, "unit_b", "")).strip().casefold()
            quantity_a = getattr(record, "quantity_a", None)
            quantity_b = getattr(record, "quantity_b", None)
            uci_a = str(getattr(record, "material_uci_a", "")).strip()
            uci_b = str(getattr(record, "material_uci_b", "")).strip()
            if (
                not code_a
                or code_a != code_b
                or unit_a not in metre_units
                or unit_b not in metre_units
                or quantity_a is None
                or quantity_b is None
                or float(quantity_a) <= 0
                or float(quantity_b) <= 0
                or not uci_a
                or not uci_b
                or uci_a == uci_b
            ):
                continue
            coordinate = tuple(float(value) for value in record.coordinate)
            distance, fraction = _point_segment_projection(
                coordinate, (*seed_start, 0.0), (*seed_end, 0.0)
            )
            if distance <= pipe_axis_tolerance and 0.0 < fraction < 1.0:
                eligible_on_run.append(record)
        connected_nodes = {
            str(getattr(record, "material_uci_a", "")).strip()
            for record in seed_records
        } | {
            str(getattr(record, "material_uci_b", "")).strip()
            for record in seed_records
        }
        complete_records = list(seed_records)
        while True:
            additions = [
                record
                for record in eligible_on_run
                if record not in complete_records
                and connected_nodes
                & {
                    str(getattr(record, "material_uci_a", "")).strip(),
                    str(getattr(record, "material_uci_b", "")).strip(),
                }
            ]
            if not additions:
                break
            complete_records.extend(additions)
            for record in additions:
                connected_nodes.update(
                    {
                        str(record.material_uci_a).strip(),
                        str(record.material_uci_b).strip(),
                    }
                )
        residual = [
            record
            for record in complete_records
            if int(record.excel_row) in pending_row_ids
            and int(record.excel_row) not in recovered_rows
        ]
        if len(residual) != 1 or len(complete_records) < 2:
            continue
        target_record = residual[0]
        target_row = int(target_record.excel_row)
        target_context = interior_contexts[target_row]
        run_start = target_context["run_start"]
        run_end = target_context["run_end"]
        run_length = math.dist(run_start, run_end)

        graph: dict[str, list[tuple[str, object]]] = defaultdict(list)
        node_lengths: dict[str, float] = {}
        consistent = True
        for record in complete_records:
            uci_a = str(record.material_uci_a).strip()
            uci_b = str(record.material_uci_b).strip()
            for uci, quantity in (
                (uci_a, float(record.quantity_a)),
                (uci_b, float(record.quantity_b)),
            ):
                previous = node_lengths.get(uci)
                if previous is not None and abs(previous - quantity) > 0.01:
                    consistent = False
                node_lengths[uci] = quantity
            graph[uci_a].append((uci_b, record))
            graph[uci_b].append((uci_a, record))
        target_uci_a = str(target_record.material_uci_a).strip()
        target_uci_b = str(target_record.material_uci_b).strip()
        target_leaf_count = sum(
            len(graph[uci]) == 1 for uci in (target_uci_a, target_uci_b)
        )
        if (
            not consistent
            or len(graph) != len(complete_records) + 1
            or any(len(neighbors) > 2 for neighbors in graph.values())
            or sum(len(neighbors) == 1 for neighbors in graph.values()) != 2
            or target_leaf_count != 1
            or abs(sum(node_lengths.values()) * 1000.0 - run_length) > 10.0
        ):
            continue

        # Every edge coordinate must occupy its cumulative station on the
        # same 3D run.  Sorting by PCF fraction also proves that the residual
        # target is beyond all previously recovered internal splits toward
        # one terminal endpoint.
        ordered_fractions: list[tuple[float, object]] = []
        for record in complete_records:
            coordinate = tuple(float(value) for value in record.coordinate)
            distance, fraction = _point_segment_projection(
                coordinate, (*run_start, 0.0), (*run_end, 0.0)
            )
            if distance > pipe_axis_tolerance or not 0.0 < fraction < 1.0:
                ordered_fractions = []
                break
            ordered_fractions.append((fraction, record))
        if not ordered_fractions:
            continue
        ordered_fractions.sort(key=lambda item: item[0])
        if target_record not in {
            ordered_fractions[0][1], ordered_fractions[-1][1]
        }:
            continue

        recovered_points = [
            (record, point)
            for record, _weld, point, _rule in candidates
            if record in complete_records
            and int(record.excel_row) in recovered_rows
            and isinstance(point.get("pipe_axis"), list)
            and len(point["pipe_axis"]) == 2
        ]
        chain_candidate_points = {
            int(record.excel_row): point for record, point in recovered_points
        }
        complete_coordinates = [
            tuple(float(value) for value in record.coordinate)
            for record in complete_records
        ]
        for row in base_rows:
            endpoint = row.get("pcf_endpoint")
            if not isinstance(endpoint, list) or len(endpoint) < 3:
                continue
            try:
                endpoint_coordinate = tuple(float(endpoint[index]) for index in range(3))
            except (TypeError, ValueError):
                continue
            if not any(
                max(abs(endpoint_coordinate[index] - coordinate[index]) for index in range(3))
                <= max(coordinate_tolerance, 5.0)
                for coordinate in complete_coordinates
            ):
                continue
            if (
                isinstance(row.get("pipe_axis"), list)
                and len(row["pipe_axis"]) == 2
            ):
                recovered_points.append((target_record, row))
        axis_candidates: list[
            tuple[float, int, tuple[float, float], tuple[float, float]]
        ] = []
        for _record, point in recovered_points:
            try:
                first = tuple(float(value) for value in point["pipe_axis"][0])
                second = tuple(float(value) for value in point["pipe_axis"][1])
                page = int(point["page"])
            except (KeyError, TypeError, ValueError):
                continue
            if len(first) != 2 or len(second) != 2:
                continue
            length = math.dist(first, second)
            if length >= 500.0:
                axis_candidates.append((length, page, first, second))
        if not axis_candidates:
            continue
        _, page, axis_first, axis_second = max(axis_candidates)
        axis_length = math.dist(axis_first, axis_second)

        # Include native endpoint welds as independent orientation evidence,
        # but accept roots only when their physical SHA location is unique.
        material_nodes = set(graph)
        anchors: dict[
            tuple[float, int, float, float],
            tuple[float, int, float, float],
        ] = {}
        for peer in records:
            peer_nodes = {
                str(getattr(peer, "material_uci_a", "")).strip(),
                str(getattr(peer, "material_uci_b", "")).strip(),
            }
            if not (material_nodes & peer_nodes):
                continue
            coordinate = tuple(float(value) for value in peer.coordinate)
            distance, fraction = _point_segment_projection(
                coordinate, (*run_start, 0.0), (*run_end, 0.0)
            )
            if distance > max(pipe_axis_tolerance, 5.0) or not -0.001 <= fraction <= 1.001:
                continue
            root = unique_sha_root_at(coordinate)
            if root is None or root[0] != page:
                continue
            key = (round(fraction, 6), root[0], root[1], root[2])
            anchors[key] = (fraction, root[0], root[1], root[2])
        anchor_values = list(anchors.values())
        if len({round(item[0], 6) for item in anchor_values}) < 2:
            continue

        def orientation_error(
            first: tuple[float, float], second: tuple[float, float]
        ) -> float:
            errors = []
            for fraction, _page, sha_x, sha_y in anchor_values:
                expected_x = first[0] + (second[0] - first[0]) * fraction
                expected_y = first[1] + (second[1] - first[1]) * fraction
                # Native weld symbols can sit slightly inside a pipe end;
                # cap their influence while retaining decisive direction.
                errors.append(min(math.hypot(sha_x - expected_x, sha_y - expected_y), 400.0))
            return sum(errors)

        forward_error = orientation_error(axis_first, axis_second)
        reverse_error = orientation_error(axis_second, axis_first)
        # A native endpoint marker may be inset by several hundred page units
        # while an internal virtual root lies exactly on the full pipe axis.
        # The capped aggregate still gives a stable direction; requiring a
        # percentage of the entire long axis would incorrectly reject that
        # common terminal-symbol geometry.
        if abs(forward_error - reverse_error) < 50.0:
            continue
        oriented_start, oriented_end = (
            (axis_first, axis_second)
            if forward_error < reverse_error
            else (axis_second, axis_first)
        )
        target_fraction = float(target_context["run_fraction"])

        # The longest merged SHA axis can be parallel to, but offset from,
        # the actual plotted pipe centreline (support/dimension strokes often
        # occupy the merged-axis line).  For a short terminal redistribution,
        # recover the real centreline from the already proven weld roots and
        # their PCF stations before moving any point.
        needs_joint_distribution = min(
            target_fraction, 1.0 - target_fraction
        ) * axis_length < 180.0
        if needs_joint_distribution:
            fit_samples: list[tuple[float, float, float]] = []
            for chain_record in complete_records:
                existing_point = chain_candidate_points.get(
                    int(chain_record.excel_row)
                )
                if existing_point is None:
                    continue
                coordinate = tuple(
                    float(value) for value in chain_record.coordinate
                )
                distance, fraction = _point_segment_projection(
                    coordinate, (*run_start, 0.0), (*run_end, 0.0)
                )
                if distance <= pipe_axis_tolerance:
                    fit_samples.append(
                        (
                            fraction,
                            float(existing_point["sha_x"]),
                            float(existing_point["sha_y"]),
                        )
                    )
            candidate_fraction_count = len(
                {round(item[0], 6) for item in fit_samples}
            )
            candidate_axis_offsets = [
                abs(
                    (sha_x - oriented_start[0])
                    * (oriented_end[1] - oriented_start[1])
                    - (sha_y - oriented_start[1])
                    * (oriented_end[0] - oriented_start[0])
                )
                / axis_length
                for _fraction, sha_x, sha_y in fit_samples
            ]
            fit_actual_centreline = (
                candidate_fraction_count < 2
                or (
                    candidate_axis_offsets
                    and max(candidate_axis_offsets) > 12.0
                )
            )
            if len({round(item[0], 6) for item in fit_samples}) < 2:
                fit_samples = [
                    (fraction, sha_x, sha_y)
                    for fraction, _page, sha_x, sha_y in anchor_values
                ]
            mean_fraction = sum(item[0] for item in fit_samples) / len(fit_samples)
            denominator = sum(
                (item[0] - mean_fraction) ** 2 for item in fit_samples
            )
            if denominator <= 1e-9:
                continue
            mean_x = sum(item[1] for item in fit_samples) / len(fit_samples)
            mean_y = sum(item[2] for item in fit_samples) / len(fit_samples)
            slope_x = sum(
                (fraction - mean_fraction) * (sha_x - mean_x)
                for fraction, sha_x, _sha_y in fit_samples
            ) / denominator
            slope_y = sum(
                (fraction - mean_fraction) * (sha_y - mean_y)
                for fraction, _sha_x, sha_y in fit_samples
            ) / denominator
            fitted_start = (
                mean_x - slope_x * mean_fraction,
                mean_y - slope_y * mean_fraction,
            )
            fitted_end = (
                fitted_start[0] + slope_x,
                fitted_start[1] + slope_y,
            )
            fitted_length = math.dist(fitted_start, fitted_end)
            fitted_residual = max(
                math.hypot(
                    sha_x - (fitted_start[0] + slope_x * fraction),
                    sha_y - (fitted_start[1] + slope_y * fraction),
                )
                for fraction, sha_x, sha_y in fit_samples
            )
            old_dx = oriented_end[0] - oriented_start[0]
            old_dy = oriented_end[1] - oriented_start[1]
            parallel = abs(
                (old_dx * slope_x + old_dy * slope_y)
                / (axis_length * fitted_length)
            )
            if fit_actual_centreline:
                if (
                    fitted_length < 500.0
                    or fitted_residual > 80.0
                    or parallel < 0.992
                ):
                    continue
                oriented_start, oriented_end = fitted_start, fitted_end
                axis_length = fitted_length

        if streams_for_material_chain is None:
            streams_for_material_chain = read_sha_streams(sha_path)
            sheets_for_material_chain = {
                sheet.page: sheet
                for sheet in sheet_streams(streams_for_material_chain, None)
            }
        sheet = sheets_for_material_chain.get(page)
        if sheet is None:
            continue

        axis_dx = oriented_end[0] - oriented_start[0]
        axis_dy = oriented_end[1] - oriented_start[1]
        axis_unit_x = axis_dx / axis_length
        axis_unit_y = axis_dy / axis_length
        all_sheet_segments = (
            line_segments(sheet.data) + template_line_segments(sheet.data)
        )
        proven_pipe_parent_refs: set[int] = set()
        original_visible_segments = [
            point.get("pipe_visible_segment")
            for point in chain_candidate_points.values()
            if isinstance(point.get("pipe_visible_segment"), list)
            and len(point["pipe_visible_segment"]) == 2
        ]
        for x1, y1, x2, y2, graphic_ref, _child_ref in all_sheet_segments:
            first = (x1 * SHEET_UNIT, y1 * SHEET_UNIT)
            second = (x2 * SHEET_UNIT, y2 * SHEET_UNIT)
            for visible in original_visible_segments:
                visible_first = tuple(float(value) for value in visible[0])
                visible_second = tuple(float(value) for value in visible[1])
                same_direction = (
                    math.dist(first, visible_first) <= 2.0
                    and math.dist(second, visible_second) <= 2.0
                )
                reverse_direction = (
                    math.dist(first, visible_second) <= 2.0
                    and math.dist(second, visible_first) <= 2.0
                )
                if same_direction or reverse_direction:
                    proven_pipe_parent_refs.add(int(graphic_ref) & 0xFFFF)
                    break

        visible_axis_segments: list[
            tuple[tuple[float, float], tuple[float, float]]
        ] = []
        all_fitted_axis_segments: list[
            tuple[tuple[float, float], tuple[float, float]]
        ] = []
        for x1, y1, x2, y2, graphic_ref, _child_ref in all_sheet_segments:
            first = (x1 * SHEET_UNIT, y1 * SHEET_UNIT)
            second = (x2 * SHEET_UNIT, y2 * SHEET_UNIT)
            length = math.dist(first, second)
            if length < 40.0:
                continue
            vx = (second[0] - first[0]) / length
            vy = (second[1] - first[1]) / length
            if abs(vx * axis_unit_x + vy * axis_unit_y) < 0.992:
                continue
            offsets = [
                abs(
                    (point[0] - oriented_start[0]) * axis_unit_y
                    - (point[1] - oriented_start[1]) * axis_unit_x
                )
                for point in (first, second)
            ]
            if max(offsets) > 12.0:
                continue
            projections = [
                (point[0] - oriented_start[0]) * axis_unit_x
                + (point[1] - oriented_start[1]) * axis_unit_y
                for point in (first, second)
            ]
            if max(projections) < -10.0 or min(projections) > axis_length + 10.0:
                continue
            all_fitted_axis_segments.append((first, second))
            if (
                not proven_pipe_parent_refs
                or (int(graphic_ref) & 0xFFFF) in proven_pipe_parent_refs
            ):
                visible_axis_segments.append((first, second))
        if not all_fitted_axis_segments:
            continue

        def snap_to_visible_axis(
            point_x: float, point_y: float
        ) -> tuple[float, float, tuple[tuple[float, float], tuple[float, float]]] | None:
            def choices_for(
                segments: list[
                    tuple[tuple[float, float], tuple[float, float]]
                ]
            ) -> list[tuple[float, float, float, tuple[tuple[float, float], tuple[float, float]]]]:
                choices = []
                for first, second in segments:
                    dx = second[0] - first[0]
                    dy = second[1] - first[1]
                    denominator = dx * dx + dy * dy
                    fraction = (
                        0.0
                        if denominator <= 1e-12
                        else max(
                            0.0,
                            min(
                                1.0,
                                ((point_x - first[0]) * dx + (point_y - first[1]) * dy)
                                / denominator,
                            ),
                        )
                    )
                    snapped_x = first[0] + fraction * dx
                    snapped_y = first[1] + fraction * dy
                    choices.append(
                        (
                            math.hypot(point_x - snapped_x, point_y - snapped_y),
                            snapped_x,
                            snapped_y,
                            (first, second),
                        )
                    )
                return choices

            # Once the centreline itself has been fitted from proven weld
            # roots, every segment in this narrow offset band is safer than
            # privileging one historical parent ref: a long pipe can change
            # SHA parent at supports/page-detail breaks.  Choose the closest
            # segment on that fitted centreline across the full chain.
            choices = choices_for(all_fitted_axis_segments)
            if not choices:
                return None
            distance, snapped_x, snapped_y, segment = min(choices)
            if distance > 180.0:
                return None
            return snapped_x, snapped_y, segment

        # Solve the terminal split together with all previously recovered
        # splits on this material chain.  A 0.2/0.3 m final shop piece can be
        # only a few page units long at the true engineering scale, causing
        # its marker to collapse onto the conventional end weld.  Reserve one
        # marker-clear visual interval for that terminal piece and apply one
        # affine remap to every internal split in the remaining chain.  Runs
        # whose true terminal interval is already readable (for example a
        # 6 m final piece) remain completely unchanged.
        minimum_terminal_interval = 180.0
        terminal_at_start = target_fraction < 0.5
        true_terminal_interval = (
            target_fraction if terminal_at_start else 1.0 - target_fraction
        ) * axis_length
        joint_distribution = true_terminal_interval < minimum_terminal_interval
        minimum_fraction = min(0.20, minimum_terminal_interval / axis_length)

        def drawing_fraction(engineering_fraction: float) -> float:
            if not joint_distribution:
                return engineering_fraction
            if terminal_at_start:
                if engineering_fraction <= target_fraction:
                    return minimum_fraction * engineering_fraction / target_fraction
                return minimum_fraction + (
                    (engineering_fraction - target_fraction)
                    * (1.0 - minimum_fraction)
                    / (1.0 - target_fraction)
                )
            terminal_limit = 1.0 - minimum_fraction
            if engineering_fraction >= target_fraction:
                return terminal_limit + (
                    (engineering_fraction - target_fraction)
                    * minimum_fraction
                    / (1.0 - target_fraction)
                )
            return engineering_fraction * terminal_limit / target_fraction

        if joint_distribution:
            for chain_record in complete_records:
                existing_point = chain_candidate_points.get(
                    int(chain_record.excel_row)
                )
                if existing_point is None:
                    continue
                coordinate = tuple(
                    float(value) for value in chain_record.coordinate
                )
                distance, engineering_fraction = _point_segment_projection(
                    coordinate, (*run_start, 0.0), (*run_end, 0.0)
                )
                if distance > pipe_axis_tolerance:
                    continue
                visual_fraction = drawing_fraction(engineering_fraction)
                projected_x = (
                    oriented_start[0]
                    + (oriented_end[0] - oriented_start[0]) * visual_fraction
                )
                projected_y = (
                    oriented_start[1]
                    + (oriented_end[1] - oriented_start[1]) * visual_fraction
                )
                snapped = snap_to_visible_axis(projected_x, projected_y)
                if snapped is None:
                    continue
                snapped_x, snapped_y, snapped_segment = snapped
                existing_point["sha_x"] = round(snapped_x, 3)
                existing_point["sha_y"] = round(snapped_y, 3)
                existing_point["pipe_axis"] = [
                    list(oriented_start), list(oriented_end)
                ]
                existing_point["pipe_visible_segment"] = [
                    list(snapped_segment[0]), list(snapped_segment[1])
                ]
                existing_point["drawing_owner_fraction"] = visual_fraction
                existing_point["drawing_distribution_mode"] = (
                    "TERMINAL-CHAIN-JOINT-DISTRIBUTION"
                )
                # drawing_distribution_group is reserved for the legacy
                # equal-spacing verifier (rank/count/target_3d contract).
                # This rule is cumulative-length constrained, not equal.
                existing_point["drawing_distribution_group"] = ""
                existing_point["terminal_chain_engineering_fraction"] = (
                    engineering_fraction
                )
                existing_point["terminal_chain_minimum_interval"] = (
                    minimum_terminal_interval
                )

        target_drawing_fraction = drawing_fraction(target_fraction)
        sha_x = oriented_start[0] + (
            oriented_end[0] - oriented_start[0]
        ) * target_drawing_fraction
        sha_y = oriented_start[1] + (
            oriented_end[1] - oriented_start[1]
        ) * target_drawing_fraction
        target_snapped = snap_to_visible_axis(sha_x, sha_y)
        if target_snapped is None:
            continue
        sha_x, sha_y, target_visible_segment = target_snapped
        if any(
            math.hypot(sha_x - root_x, sha_y - root_y) < 20.0
            for _fraction, _page, root_x, root_y in anchor_values
        ):
            continue

        weld = _synthetic_table_weld(target_record)
        owner = target_context["owner"]
        weld.augmentation_source = "PCF-AUGMENTED"
        weld.augmentation_mode = "TABLE-MATERIAL-CHAIN-TERMINAL-CONTINUATION"
        weld.master_component_identifier = owner.component_identifier
        weld.owner_uci = owner.uci
        weld.owner_fraction = target_fraction
        point = {
            "uci": weld.uci,
            "graphic_ref": 0xD0000000
            | (zlib.crc32(weld.uci.encode("ascii")) & 0x0FFFFFFF),
            "page": page,
            "sheet_stream": sheet.stream_name,
            "sha_x": round(sha_x, 3),
            "sha_y": round(sha_y, 3),
            "distance_to_geometry": 0.0,
            "pipe_axis": [list(oriented_start), list(oriented_end)],
            "pipe_visible_segment": [
                list(target_visible_segment[0]),
                list(target_visible_segment[1]),
            ],
            "material_chain_total_m": run_length / 1000.0,
            "material_chain_target_fraction": target_fraction,
            "drawing_owner_fraction": target_drawing_fraction,
            "drawing_distribution_mode": (
                "TERMINAL-CHAIN-JOINT-DISTRIBUTION"
                if joint_distribution
                else ""
            ),
            "drawing_distribution_group": (
                ""
            ),
            "terminal_chain_minimum_interval": (
                minimum_terminal_interval if joint_distribution else None
            ),
            "manual_review_required": True,
            "manual_review_reason": "已验证直管材料链末端单剩余分段推断",
            "point_evidence": (
                "construction-table-material-chain-terminal-continuation"
            ),
            "topology_rule": (
                "single residual leaf edge + complete linear material chain + "
                "unique oriented full SHA pipe axis"
            ),
        }
        candidates.append(
            (
                target_record,
                weld,
                point,
                "construction-table-material-chain-terminal-continuation",
            )
        )
        recovered_rows.add(target_row)

    # Cross-sheet ISO audit exception.  The target drawing can own the ISO
    # marker while the EP3D coordinate/WELD lives in a referenced neighbour.
    # Some target PCFs encode that connection as an UCI-less TEE-STUB, so UCI
    # convergence is impossible.  Use the last unused native weld ellipse
    # only when this PCF explicitly references the source sheet and both the
    # unresolved table row and unused ellipse are globally unique on the page.
    referenced_stems = {
        Path(match.group(1).strip()).stem.casefold()
        for line in source_lines
        if (match := CONNECTION_REFERENCE_RE.match(line)) is not None
    }
    cross_sheet_pending = [
        record
        for record in pending_records
        if int(record.excel_row) not in recovered_rows
        and str(getattr(record, "source_sheet_id", "")).strip()
        and str(getattr(record, "source_sheet_id", "")).casefold()
        != str(getattr(record, "sheet_id", "")).casefold()
        and Path(str(getattr(record, "source_sheet_id", ""))).stem.casefold()
        in referenced_stems
    ]
    if len(cross_sheet_pending) == 1:
        record = cross_sheet_pending[0]
        target = tuple(float(value) for value in record.coordinate)
        # Locate an UCI-less OLET/TEE-STUB whose branch port is the cross-page
        # weld.  Its centre lies on a same-UCI pipe run in the target PCF.
        starts = [
            (index, line)
            for index, line in enumerate(source_lines)
            if TOP_LEVEL.fullmatch(line) and line != "MATERIALS"
        ]
        olet_centres: list[tuple[float, float, float]] = []
        for position, (start, kind) in enumerate(starts):
            if kind not in {"OLET", "TEE-STUB"}:
                continue
            end = starts[position + 1][0] if position + 1 < len(starts) else len(source_lines)
            block = "\n".join(source_lines[start:end])
            branch_match = re.search(
                r"BRANCH1-POINT\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                block,
            )
            centre_match = re.search(
                r"CENTRE-POINT\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                block,
            )
            if branch_match is None or centre_match is None:
                continue
            branch = tuple(float(branch_match.group(index)) for index in range(1, 4))
            if math.dist(branch, target) <= coordinate_tolerance:
                olet_centres.append(
                    tuple(float(centre_match.group(index)) for index in range(1, 4))
                )
        if len(olet_centres) == 1:
            centre = olet_centres[0]
            centre_pipes = [
                pipe
                for pipe in pipes
                if min(
                    math.dist(centre, tuple(pipe.endpoint_a[:3])),
                    math.dist(centre, tuple(pipe.endpoint_b[:3])),
                )
                <= coordinate_tolerance
            ]
            owner_ucis = {pipe.uci for pipe in centre_pipes if pipe.uci}
            if len(owner_ucis) == 1 and centre_pipes:
                owner = centre_pipes[0]
                run_result = _connected_collinear_pipe_run(
                    owner, pipes, axis_tolerance=pipe_axis_tolerance
                )
                if run_result is not None:
                    run_pipes, run_start, run_end = run_result
                    # Order the PCF blocks from run_start to run_end.
                    ordered_pipes: list[object] = []
                    current = run_start
                    unused_pipes = list(run_pipes)
                    while unused_pipes:
                        matches = [
                            pipe
                            for pipe in unused_pipes
                            if min(
                                math.dist(current, tuple(pipe.endpoint_a[:3])),
                                math.dist(current, tuple(pipe.endpoint_b[:3])),
                            )
                            <= coordinate_tolerance
                        ]
                        if len(matches) != 1:
                            ordered_pipes = []
                            break
                        pipe = matches[0]
                        unused_pipes.remove(pipe)
                        ordered_pipes.append(pipe)
                        endpoint_a = tuple(pipe.endpoint_a[:3])
                        endpoint_b = tuple(pipe.endpoint_b[:3])
                        current = (
                            endpoint_b
                            if math.dist(current, endpoint_a) <= coordinate_tolerance
                            else endpoint_a
                        )
                    boundary_index = next(
                        (
                            index
                            for index in range(1, len(ordered_pipes))
                            if min(
                                math.dist(centre, tuple(ordered_pipes[index - 1].endpoint_a[:3])),
                                math.dist(centre, tuple(ordered_pipes[index - 1].endpoint_b[:3])),
                            )
                            <= coordinate_tolerance
                            and min(
                                math.dist(centre, tuple(ordered_pipes[index].endpoint_a[:3])),
                                math.dist(centre, tuple(ordered_pipes[index].endpoint_b[:3])),
                            )
                            <= coordinate_tolerance
                        ),
                        None,
                    )
                    start_root = unique_sha_root_at(run_start)
                    end_root = unique_sha_root_at(run_end)
                    if (
                        boundary_index is not None
                        and start_root is not None
                        and end_root is not None
                        and start_root[0] == end_root[0]
                    ):
                        streams = read_sha_streams(sha_path)
                        dynamic = _dynamic_graphics_including_first_record(
                            streams.get("Unclustered Dynamic Attributes", b"")
                        )
                        owner_refs = {
                            int(item["graphic_ref"]) & 0xFFFF
                            for item in dynamic.get(owner.uci, [])
                        }
                        sheet = next(
                            (
                                item
                                for item in sheet_streams(streams, None)
                                if item.page == start_root[0]
                            ),
                            None,
                        )
                        if sheet is not None:
                            segments = line_segments(sheet.data) + template_line_segments(sheet.data)
                            points_by_ref: dict[int, set[tuple[float, float]]] = defaultdict(set)
                            for x1, y1, x2, y2, graphic_ref, _width in segments:
                                ref = int(graphic_ref) & 0xFFFF
                                if ref in owner_refs:
                                    points_by_ref[ref].update(
                                        {
                                            (round(x1 * SHEET_UNIT, 3), round(y1 * SHEET_UNIT, 3)),
                                            (round(x2 * SHEET_UNIT, 3), round(y2 * SHEET_UNIT, 3)),
                                        }
                                    )
                            # Traverse UCI-owned SHA groups from the known run
                            # start.  Shared group endpoints correspond to PCF
                            # block boundaries even when schematic lengths are
                            # deliberately non-proportional.
                            root_xy = (start_root[1], start_root[2])
                            ref_order: list[int] = []
                            candidates_at_start = [
                                ref
                                for ref, points in points_by_ref.items()
                                if min((math.dist(root_xy, point) for point in points), default=math.inf) <= 5.0
                            ]
                            if len(candidates_at_start) == 1:
                                ref_order.append(candidates_at_start[0])
                                while len(ref_order) < len(points_by_ref):
                                    neighbors = [
                                        ref
                                        for ref in points_by_ref
                                        if ref not in ref_order
                                        and points_by_ref[ref_order[-1]] & points_by_ref[ref]
                                    ]
                                    if len(neighbors) != 1:
                                        ref_order = []
                                        break
                                    ref_order.append(neighbors[0])
                            if (
                                len(ref_order) == len(ordered_pipes)
                                and 0 < boundary_index < len(ref_order)
                            ):
                                common = (
                                    points_by_ref[ref_order[boundary_index - 1]]
                                    & points_by_ref[ref_order[boundary_index]]
                                )
                                if len(common) == 1:
                                    centre_xy = next(iter(common))
                                    incident: list[
                                        tuple[float, tuple[float, float], tuple[float, float], int]
                                    ] = []
                                    for x1, y1, x2, y2, graphic_ref, _width in segments:
                                        ref = int(graphic_ref) & 0xFFFF
                                        if ref in owner_refs:
                                            continue
                                        first = (x1 * SHEET_UNIT, y1 * SHEET_UNIT)
                                        second = (x2 * SHEET_UNIT, y2 * SHEET_UNIT)
                                        first_gap = math.dist(centre_xy, first)
                                        second_gap = math.dist(centre_xy, second)
                                        if min(first_gap, second_gap) > 5.0:
                                            continue
                                        near, far = (
                                            (first, second)
                                            if first_gap <= second_gap
                                            else (second, first)
                                        )
                                        incident.append((math.dist(first, second), near, far, ref))
                                    incident.sort(key=lambda item: item[0], reverse=True)
                                    topology_lines = _connection_topology_source_lines(
                                        pcf_path, source_lines
                                    )
                                    topology_pipes = parse_pipe_components(topology_lines)
                                    branch_pipes = [
                                        pipe
                                        for pipe in topology_pipes
                                        if pipe.uci != owner.uci
                                        and min(
                                            math.dist(target, tuple(pipe.endpoint_a[:3])),
                                            math.dist(target, tuple(pipe.endpoint_b[:3])),
                                        )
                                        <= coordinate_tolerance
                                    ]
                                    if (
                                        incident
                                        and incident[0][0] >= 100.0
                                        and (len(incident) == 1 or incident[0][0] >= incident[1][0] * 2.0)
                                        and len(branch_pipes) == 1
                                    ):
                                        branch_pipe = branch_pipes[0]
                                        branch_pipe_length = math.dist(
                                            tuple(branch_pipe.endpoint_a[:3]),
                                            tuple(branch_pipe.endpoint_b[:3]),
                                        )
                                        olet_length = math.dist(centre, target)
                                        total_length = olet_length + branch_pipe_length
                                        if total_length > 0:
                                            _length, _near, far, graphic_ref = incident[0]
                                            fraction = olet_length / total_length
                                            sha_x = centre_xy[0] + (far[0] - centre_xy[0]) * fraction
                                            sha_y = centre_xy[1] + (far[1] - centre_xy[1]) * fraction
                                            weld = _synthetic_table_weld(record)
                                            point = {
                                                "uci": weld.uci,
                                                "graphic_ref": 0xD0000000
                                                | (zlib.crc32(weld.uci.encode("ascii")) & 0x0FFFFFFF),
                                                "page": sheet.page,
                                                "sheet_stream": sheet.stream_name,
                                                "sha_x": round(sha_x, 3),
                                                "sha_y": round(sha_y, 3),
                                                "distance_to_geometry": 0.0,
                                                "pipe_axis": [list(centre_xy), list(far)],
                                                "pipe_visible_segment": [list(centre_xy), list(far)],
                                                "manual_review_required": True,
                                                "manual_review_reason": "跨图UCI缺失支管接口按两侧长度比例推断",
                                                "point_evidence": "cross-sheet-uci-less-olet-length-ratio",
                                                "topology_rule": (
                                                    "referenced source sheet + UCI-less OLET branch + "
                                                    "ordered same-UCI run graphics + unique dominant SHA branch"
                                                ),
                                                "cross_sheet_source": str(record.source_sheet_id),
                                                "cross_sheet_lengths": [olet_length, branch_pipe_length],
                                            }
                                            candidates.append(
                                                (
                                                    record,
                                                    weld,
                                                    point,
                                                    "construction-table-cross-sheet-uci-less-olet",
                                                )
                                            )
                                            recovered_rows.add(int(record.excel_row))
        cross_sheet_pending = [
            record
            for record in cross_sheet_pending
            if int(record.excel_row) not in recovered_rows
        ]
    if len(cross_sheet_pending) == 1:
        streams = read_sha_streams(sha_path)
        used_roots = {
            (
                int(point.get("page", 999999)),
                round(float(point["sha_x"]), 2),
                round(float(point["sha_y"]), 2),
            )
            for point in list(base_rows)
            + [candidate[2] for candidate in candidates]
            if point.get("graphic_ref") is not None
            and point.get("page") not in (None, 999999)
            and point.get("sha_x") is not None
            and point.get("sha_y") is not None
        }
        unused: list[tuple[object, int, tuple[float, float]]] = []
        for sheet in sheet_streams(streams, None):
            segments = line_segments(sheet.data) + template_line_segments(sheet.data)
            for graphic_ref, anchor in ellipse_anchors(sheet.data).items():
                root = (
                    sheet.page,
                    round(float(anchor[0]), 2),
                    round(float(anchor[1]), 2),
                )
                if root in used_roots:
                    continue
                nearest = min(
                    _point_segment_projection(
                        (float(anchor[0]), float(anchor[1]), 0.0),
                        (x1 * SHEET_UNIT, y1 * SHEET_UNIT, 0.0, 0.0),
                        (x2 * SHEET_UNIT, y2 * SHEET_UNIT, 0.0, 0.0),
                    )[0]
                    for x1, y1, x2, y2, _ref, _width in segments
                ) if segments else float("inf")
                if nearest <= 80.0:
                    unused.append((sheet, int(graphic_ref), anchor))
        if len(unused) == 1:
            record = cross_sheet_pending[0]
            sheet, graphic_ref, anchor = unused[0]
            weld = _synthetic_table_weld(record)
            point = {
                "uci": weld.uci,
                "graphic_ref": graphic_ref,
                "page": sheet.page,
                "sheet_stream": sheet.stream_name,
                "sha_x": round(float(anchor[0]), 3),
                "sha_y": round(float(anchor[1]), 3),
                "distance_to_geometry": 0.0,
                "manual_review_required": True,
                "manual_review_reason": "跨图连接且本页唯一未占用焊点图元",
                "point_evidence": "cross-sheet-unique-unused-native-weld-anchor",
                "topology_rule": (
                    "target PCF explicitly references source sheet + one unresolved "
                    "cross-sheet ISO weld + one unused native SHA weld ellipse"
                ),
                "cross_sheet_source": str(record.source_sheet_id),
            }
            candidates.append(
                (
                    record,
                    weld,
                    point,
                    "construction-table-cross-sheet-unique-unused-weld-anchor",
                )
            )
            recovered_rows.add(int(record.excel_row))

    # A valid PCF connection does not require an explicit WELD block.  Build
    # synthetic welds for exact, unique component interfaces and let the two
    # UCI-owned SHA geometries prove their common plotted endpoint.  OLET is
    # handled twice: CENTRE-POINT for the run side and BRANCH1-POINT for the
    # branch side.
    component_ports = _component_ports_at_coordinates(topology_source_lines)
    interface_inputs: list[tuple[object, WeldBlock]] = []
    for record in pending_records:
        if int(record.excel_row) in recovered_rows:
            continue
        members = _component_ports_near_coordinate(
            component_ports,
            record.coordinate,
            tolerance=coordinate_tolerance,
        )
        distinct_ucis = {member[1] for member in members}
        if len(distinct_ucis) != 2:
            continue
        # At least one side must be a real fitting/component rather than two
        # duplicated PIPE blocks sharing a coordinate.
        if not any(member[0] not in {"PIPE", "PIPE-FIXED"} for member in members):
            continue
        weld = _synthetic_table_weld(record)
        weld.augmentation_source = "PCF-TOPOLOGY"
        weld.augmentation_mode = "TABLE-COMPONENT-INTERFACE"
        interface_inputs.append((record, weld))

    interface_points = (
        _sha_two_sided_component_junctions(
            sha_path,
            topology_source_lines,
            [weld for _, weld in interface_inputs],
        )
        if interface_inputs
        else {}
    )
    for record, weld in interface_inputs:
        point = interface_points.get(weld.uci)
        if point is not None:
            candidates.append(
                (
                    record,
                    weld,
                    point,
                    "construction-table-component-interface",
                )
            )

    # A decoded topology endpoint can exist in the raw Sheet coordinate space
    # while being outside the physical drawing viewport selected by the SHA
    # template.  Such a point is not visible and must remain unresolved rather
    # than aborting annotation with an out-of-frame callout.
    sha_streams = read_sha_streams(sha_path)
    inherited_viewbox = declared_sheet_viewbox(sha_streams.get("Sheet6", b""))
    visible_drawing_by_page: dict[int, tuple[float, float, float, float]] = {}
    for target in sheet_streams(sha_streams, None):
        viewbox = sheet_viewbox(target.data, inherited_viewbox)
        visible_drawing_by_page[int(target.page)] = infer_layout_region(
            sha_streams.get("Sheet221", b""), viewbox
        ).drawing

    visible_candidates: list[tuple[object, WeldBlock, dict[str, object], str]] = []
    rejected_outside_drawing_count = 0
    for item in candidates:
        point = item[2]
        bounds = visible_drawing_by_page.get(int(point.get("page", 999999)))
        if bounds is None:
            rejected_outside_drawing_count += 1
            continue
        x = float(point["sha_x"])
        y = float(point["sha_y"])
        if not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]):
            rejected_outside_drawing_count += 1
            continue
        visible_candidates.append(item)
    candidates = visible_candidates

    roots = Counter(
        (
            int(point.get("page", 999999)),
            round(float(point["sha_x"]), 2),
            round(float(point["sha_y"]), 2),
        )
        for _, _, point, _ in candidates
    )
    accepted: list[dict[str, object]] = []
    rejected_collision_count = 0
    for record, weld, point, rule in candidates:
        root = (
            int(point.get("page", 999999)),
            round(float(point["sha_x"]), 2),
            round(float(point["sha_y"]), 2),
        )
        if roots[root] != 1:
            rejected_collision_count += 1
            continue
        row = dict(point)
        row.update(
            {
                "uci": weld.uci,
                "component_identifier": "",
                "master_component_identifier": weld.master_component_identifier,
                "repeat_weld_identifier": "0",
                "pcf_endpoint": list(weld.endpoint) if weld.endpoint else None,
                "weld_number": "",
                "weld_type": "",
                "mapping_method": rule,
                "confidence_status": "mapped",
                "construction_virtual_target": True,
                "construction_virtual_rule": rule,
            }
        )
        accepted.append(row)

    # Keep an internal pipe split distinct from a nearby fitting interface.
    # A long PCF run may reuse one PIPE UCI across two blocks; global owner
    # interpolation can then collapse the last split onto an OLET/TEE-STUB
    # interface even though the engineering coordinates are distinct.  When
    # the exact interface and the next split on the same side are both proven
    # on this page, interpolate only within that local bracket using the PCF
    # distance ratio.  This preserves the real, sometimes very short, final
    # pipe piece without inventing a minimum visual spacing.
    interface_rows: list[tuple[dict[str, object], set[str]]] = []
    for row in accepted:
        if row.get("construction_virtual_rule") != "construction-table-component-interface":
            continue
        endpoint = row.get("pcf_endpoint")
        if not isinstance(endpoint, list) or len(endpoint) < 3:
            continue
        members = _component_ports_near_coordinate(
            component_ports, endpoint[:3], tolerance=coordinate_tolerance
        )
        pipe_ucis = {
            member[1]
            for member in members
            if member[0] in {"PIPE", "PIPE-FIXED"}
        }
        if pipe_ucis:
            interface_rows.append((row, pipe_ucis))

    split_rows = [
        row
        for row in accepted
        if row.get("construction_virtual_rule") == "construction-table-collinear-pipe-run"
        and row.get("owner_uci")
    ]
    for row in split_rows:
        endpoint = row.get("pcf_endpoint")
        if not isinstance(endpoint, list) or len(endpoint) < 3:
            continue
        target_3d = tuple(float(value) for value in endpoint[:3])
        target_2d = (float(row["sha_x"]), float(row["sha_y"]))
        owner_uci = str(row["owner_uci"])
        nearby_interfaces = [
            interface
            for interface, pipe_ucis in interface_rows
            if owner_uci in pipe_ucis
            and int(interface.get("page", 999999)) == int(row.get("page", 999999))
            and math.dist(target_3d, tuple(float(v) for v in interface["pcf_endpoint"][:3]))
            <= 1000.0
            and math.dist(
                target_2d,
                (float(interface["sha_x"]), float(interface["sha_y"])),
            )
            < 80.0
        ]
        if len(nearby_interfaces) != 1:
            continue
        interface = nearby_interfaces[0]
        interface_3d = tuple(float(value) for value in interface["pcf_endpoint"][:3])
        direction = tuple(target_3d[index] - interface_3d[index] for index in range(3))
        direction_length = math.sqrt(sum(value * value for value in direction))
        if direction_length <= 1e-9:
            continue
        peers: list[tuple[float, dict[str, object]]] = []
        for peer in split_rows:
            if peer is row or str(peer.get("owner_uci")) != owner_uci:
                continue
            peer_endpoint = peer.get("pcf_endpoint")
            if not isinstance(peer_endpoint, list) or len(peer_endpoint) < 3:
                continue
            peer_3d = tuple(float(value) for value in peer_endpoint[:3])
            peer_vector = tuple(peer_3d[index] - interface_3d[index] for index in range(3))
            projection = sum(
                peer_vector[index] * direction[index] for index in range(3)
            ) / (direction_length * direction_length)
            projected = tuple(
                interface_3d[index] + projection * direction[index]
                for index in range(3)
            )
            if projection <= 1.0 or math.dist(peer_3d, projected) > pipe_axis_tolerance:
                continue
            peers.append((math.dist(peer_3d, target_3d), peer))
        if not peers:
            continue
        _distance, peer = min(peers, key=lambda item: item[0])
        peer_3d = tuple(float(value) for value in peer["pcf_endpoint"][:3])
        denominator = math.dist(peer_3d, interface_3d)
        if denominator <= 1e-9:
            continue
        fraction = math.dist(peer_3d, target_3d) / denominator
        if not 0.0 < fraction < 1.0:
            continue
        peer_2d = (float(peer["sha_x"]), float(peer["sha_y"]))
        interface_2d = (float(interface["sha_x"]), float(interface["sha_y"]))
        row["sha_x"] = round(
            peer_2d[0] + fraction * (interface_2d[0] - peer_2d[0]), 3
        )
        row["sha_y"] = round(
            peer_2d[1] + fraction * (interface_2d[1] - peer_2d[1]), 3
        )
        row["point_evidence"] = "pcf-pipe-local-interface-bracket"
        row["local_interface_bracket"] = {
            "peer": [round(peer_2d[0], 3), round(peer_2d[1], 3)],
            "interface": [round(interface_2d[0], 3), round(interface_2d[1], 3)],
            "fraction": round(fraction, 8),
        }

    output["welds"] = base_rows + accepted
    output["construction_virtual_target_summary"] = {
        "input_without_pcf_weld": len(pending_records),
        "endpoint_topology_count": sum(
            row["construction_virtual_rule"]
            == "construction-table-pcf-topology-endpoint"
            for row in accepted
        ),
        "collinear_pipe_run_count": sum(
            row["construction_virtual_rule"]
            == "construction-table-collinear-pipe-run"
            for row in accepted
        ),
        "component_interface_count": sum(
            row["construction_virtual_rule"]
            == "construction-table-component-interface"
            for row in accepted
        ),
        "rejected_outside_drawing_count": rejected_outside_drawing_count,
        "material_chain_review_count": sum(
            row["construction_virtual_rule"]
            == "construction-table-material-chain-terminal-pipe"
            for row in accepted
        ),
        "material_chain_cumulative_review_count": sum(
            row["construction_virtual_rule"]
            == "construction-table-material-chain-cumulative-pipe"
            for row in accepted
        ),
        "material_chain_support_bracketed_review_count": sum(
            row["construction_virtual_rule"]
            == "construction-table-material-chain-support-bracketed-pipe"
            for row in accepted
        ),
        "rejected_duplicate_root_count": rejected_collision_count,
    }
    return output


def natural_weld_key(value: str) -> tuple[list[tuple[int, object]], str]:
    parts = [
        (1, int(token)) if token.isdigit() else (0, token.casefold())
        for token in TOKEN_RE.findall(value)
    ]
    return parts, value


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _finite_json_value(value: object) -> object:
    if isinstance(value, float) and (value == float("inf") or value == float("-inf")):
        return None
    if isinstance(value, list):
        return [_finite_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _finite_json_value(item) for key, item in value.items()}
    return value


def build_weld_map(
    pcf_path: Path,
    sha_path: Path,
    output_path: Path,
    distance_threshold: float = 80.0,
    include_unnumbered_topology: bool = False,
) -> WeldMapSummary:
    source_lines, welds = parse_weld_blocks(pcf_path)
    pipes = parse_pipe_components(source_lines)
    overrides = long_pipe_drawing_overrides(welds, pipes)
    points = sha_weld_points(sha_path, distance_threshold)
    points.update(sha_virtual_weld_points(sha_path, welds, points, overrides))
    points.update(
        sha_implicit_pipe_end_weld_points(
            sha_path,
            source_lines,
            welds,
            points,
            include_unnumbered=include_unnumbered_topology,
            allow_convergent_candidates=include_unnumbered_topology,
        )
    )
    if include_unnumbered_topology:
        topology_source_lines = _connection_topology_source_lines(
            pcf_path, source_lines
        )
        preliminary_roots: dict[tuple[int, float, float], list[str]] = defaultdict(list)
        weld_ucis = {weld.uci for weld in welds}
        for uci, point in points.items():
            if uci in weld_ucis and point.get("page") not in (None, 999999):
                preliminary_roots[
                    (
                        int(point["page"]),
                        round(float(point["sha_x"]), 2),
                        round(float(point["sha_y"]), 2),
                    )
                ].append(uci)
        topology_target_ucis = {
            weld.uci for weld in welds if weld.uci not in points
        } | {
            uci
            for ucis in preliminary_roots.values()
            if len(ucis) > 1
            for uci in ucis
        }
        topology_target_welds = [
            weld for weld in welds if weld.uci in topology_target_ucis
        ]
        two_sided_points = (
            _sha_two_sided_component_junctions(
                sha_path, topology_source_lines, topology_target_welds
            )
            if topology_target_welds
            else {}
        )
        for uci, point in two_sided_points.items():
            if uci not in points:
                points[uci] = point
        roots: dict[tuple[int, float, float], list[str]] = defaultdict(list)
        for uci, point in points.items():
            if uci not in weld_ucis or point.get("page") in (None, 999999):
                continue
            roots[
                (
                    int(point["page"]),
                    round(float(point["sha_x"]), 1),
                    round(float(point["sha_y"]), 1),
                )
            ].append(uci)
        conflict_groups = [ucis for ucis in roots.values() if len(ucis) > 1]
        if conflict_groups:
            conflict_ucis = {uci for group in conflict_groups for uci in group}
            topology_known = {
                uci: point for uci, point in points.items() if uci not in conflict_ucis
            }
            alternatives = sha_implicit_pipe_end_weld_points(
                sha_path,
                source_lines,
                welds,
                topology_known,
                include_unnumbered=True,
                allow_convergent_candidates=True,
            )
            reducer_face_points = _sha_opposite_reducer_face_points(
                sha_path,
                topology_source_lines,
                welds,
                topology_known,
            )
            for group in conflict_groups:
                replacements = [
                    reducer_face_points.get(uci)
                    or two_sided_points.get(uci)
                    or alternatives.get(uci)
                    for uci in group
                ]
                if any(point is None for point in replacements):
                    continue
                replacement_roots = {
                    (
                        int(point["page"]),
                        round(float(point["sha_x"]), 1),
                        round(float(point["sha_y"]), 1),
                    )
                    for point in replacements
                    if point is not None
                }
                if len(replacement_roots) != len(group):
                    continue
                for uci, replacement in zip(group, replacements):
                    replacement = dict(replacement)
                    replacement["native_shared_root_replaced"] = True
                    replacement["native_shared_root_group_size"] = len(group)
                    points[uci] = replacement
    numbered = number_welds(welds, points, prefix="", use_repeat_id=True)


    for row in numbered:
        repeat_id = str(row.get("repeat_weld_identifier", "")).strip()
        row["weld_number"] = repeat_id if repeat_id and repeat_id != "0" else ""
        row["mapping_method"] = str(row.get("point_evidence", ""))
        row["confidence_status"] = (
            "mapped"
            if row.get("page") not in (None, 999999) and row.get("graphic_ref") is not None
            else "unmapped"
        )

    actual_numbers = [str(row["weld_number"]) for row in numbered if row["weld_number"]]
    duplicates = sorted(
        (number for number, count in Counter(actual_numbers).items() if count > 1),
        key=natural_weld_key,
    )
    if duplicates:
        raise ValueError(f"PCF焊口号重复：{duplicates}")
    callouts = [row for row in numbered if row["weld_number"] and row["confidence_status"] == "mapped"]
    unmapped = [row for row in numbered if row["weld_number"] and row["confidence_status"] == "unmapped"]
    unresolved_types = [row for row in callouts if row.get("weld_type") not in {"S", "F"}]
    if unresolved_types:
        raise ValueError(f"存在无法判定S/F的已编号焊口：{len(unresolved_types)}")

    payload: dict[str, object] = {
        "pcf": str(pcf_path),
        "sha": str(sha_path),
        "number_source": "Chengda weld table original 焊点编号",
        "marker_rule": "F=square; S=circle",
        "leader_rule": "one direct straight line",
        "welds": _finite_json_value(numbered),
    }
    atomic_write_json(output_path, payload)
    topology = audit_topology(pcf_path, sha_path, output_path)
    payload["topology_summary"] = topology.get("summary", {})
    payload["summary"] = {
        "numbered_weld_count": len(actual_numbers),
        "callout_target_count": len(callouts),
        "unmapped_numbered_count": len(unmapped),
        "ambiguous_count": 0,
    }
    atomic_write_json(output_path, payload)
    return WeldMapSummary(
        numbered_weld_count=len(actual_numbers),
        callout_target_count=len(callouts),
        unmapped_numbered_count=len(unmapped),
        ambiguous_count=0,
    )
