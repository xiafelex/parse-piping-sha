#!/usr/bin/env python3
"""Map numbered Chengda PCF welds to native or projected SHA points."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from analyze_iso_split import forward_owned_dynamic_graphics, read_sha_streams
from audit_pcf_sha_weld_topology import audit_topology
from inject_sha_weld_callouts import sheet_streams
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
from sha_to_svg_prototype import SHEET_UNIT, line_segments, template_line_segments


TOKEN_RE = re.compile(r"\d+|\D+")


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
    for position, (start, _) in enumerate(starts):
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


def _sha_two_sided_component_junctions(
    sha_path: Path,
    source_lines: list[str],
    welds: list[WeldBlock],
) -> dict[str, dict[str, object]]:
    """Locate WELDs at a unique common endpoint of both adjacent components."""

    components_at: dict[
        tuple[float, float, float], list[tuple[str, str, str]]
    ] = defaultdict(list)
    starts = [
        (index, line)
        for index, line in enumerate(source_lines)
        if TOP_LEVEL.fullmatch(line) and line != "MATERIALS"
    ]
    for position, (start, kind) in enumerate(starts):
        if kind == "WELD":
            continue
        end = starts[position + 1][0] if position + 1 < len(starts) else len(source_lines)
        block = "\n".join(source_lines[start:end])
        uci_match = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        component_match = re.search(r"COMPONENT-IDENTIFIER\s+(\S+)", block)
        if uci_match is None:
            continue
        for match in POINT_RE.finditer(block):
            point = tuple(float(match.group(index)) for index in range(2, 5))
            components_at[_coordinate_key(point)].append(
                (
                    kind,
                    uci_match.group(1),
                    component_match.group(1) if component_match else "",
                )
            )

    adjacent_by_weld: dict[str, list[tuple[str, str, str]]] = {}
    target_ucis: set[str] = set()
    for weld in welds:
        if weld.endpoint is None:
            continue
        adjacent = list(
            dict.fromkeys(components_at.get(_coordinate_key(weld.endpoint), []))
        )
        if len(adjacent) < 2:
            continue
        adjacent_by_weld[weld.uci] = adjacent
        target_ucis.update(uci for _, uci, _ in adjacent)
    if not adjacent_by_weld:
        return {}

    streams = read_sha_streams(sha_path)
    dynamic = forward_owned_dynamic_graphics(
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
                midpoints = {
                    (
                        round((item[1][0] + item[2][0]) / 2, 1),
                        round((item[1][1] + item[2][1]) / 2, 1),
                    )
                    for item in best
                }
                if len(midpoints) == 1:
                    x, y = next(iter(midpoints))
                else:
                    # A flange face is commonly two short parallel strokes
                    # straddling the centreline endpoint of the adjoining
                    # elbow/pipe.  Equal nearest gaps then produce two
                    # midpoints, while the opposite component contributes
                    # one identical centreline endpoint.  That shared-side
                    # endpoint is the engineering interface location.
                    left_points = {item[1] for item in best}
                    right_points = {item[2] for item in best}
                    if len(left_points) == 1:
                        x, y = next(iter(left_points))
                    elif len(right_points) == 1:
                        x, y = next(iter(right_points))
                    else:
                        continue
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

    endpoint_inputs = [_synthetic_table_weld(record) for record in pending_records]
    endpoint_points = sha_implicit_pipe_end_weld_points(
        sha_path,
        source_lines,
        pcf_welds + endpoint_inputs,
        known_points,
        include_unnumbered=True,
    )
    candidates: list[tuple[object, WeldBlock, dict[str, object], str]] = []
    recovered_rows: set[int] = set()
    for record, weld in zip(pending_records, endpoint_inputs):
        point = endpoint_points.get(weld.uci)
        if point is None:
            continue
        candidates.append((record, weld, point, "construction-table-pcf-topology-endpoint"))
        recovered_rows.add(int(record.excel_row))

    component_ucis = _component_ucis_at_coordinates(source_lines)
    interior_inputs: list[tuple[object, WeldBlock]] = []
    for record in pending_records:
        if int(record.excel_row) in recovered_rows:
            continue
        coordinate = tuple(float(value) for value in record.coordinate)
        pipe_candidates: list[tuple[object, float]] = []
        for pipe in pipes:
            distance, fraction = _point_segment_projection(
                coordinate, pipe.endpoint_a, pipe.endpoint_b
            )
            if distance <= pipe_axis_tolerance and 0.02 < fraction < 0.98:
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

    interior_points = sha_virtual_weld_points(
        sha_path,
        [weld for _, weld in interior_inputs],
        known_points,
        {},
    )
    for record, weld in interior_inputs:
        point = interior_points.get(weld.uci)
        if point is not None:
            candidates.append(
                (record, weld, point, "construction-table-collinear-pipe-run")
            )

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
                sha_path, source_lines, topology_target_welds
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
                    round(float(point["sha_x"]), 2),
                    round(float(point["sha_y"]), 2),
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
            for group in conflict_groups:
                replacements = [
                    two_sided_points.get(uci) or alternatives.get(uci)
                    for uci in group
                ]
                if any(point is None for point in replacements):
                    continue
                replacement_roots = {
                    (
                        int(point["page"]),
                        round(float(point["sha_x"]), 2),
                        round(float(point["sha_y"]), 2),
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
