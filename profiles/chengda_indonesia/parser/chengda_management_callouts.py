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


MANAGEMENT_COLOR = "#0066ff"
MANAGEMENT_FONT_FAMILY = "SimHei, Microsoft YaHei, Arial, sans-serif"
MANAGEMENT_FONT_HEIGHT = 88.0
MANAGEMENT_TEXT_PADDING_X = 18.0
MANAGEMENT_TEXT_PADDING_Y = 12.0
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
    for uci, records in dynamic.items():
        for record in records:
            graphic_ref = int(record["graphic_ref"])
            if graphic_ref.to_bytes(4, "little") not in sheet:
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
    return (first_cx + second_cx) / 2, (first_cy + second_cy) / 2


def _pcf_pipe_projection(
    pcf_path: Path,
    coordinate: tuple[float, float, float],
) -> tuple[str, float] | None:
    lines = Path(pcf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    candidates: list[tuple[float, str, float]] = []
    for position, start in enumerate(starts):
        if lines[start].strip() != "PIPE":
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


def _pcf_component_endpoints_by_uci(
    pcf_path: Path,
    component_kind: str | tuple[str, ...],
) -> dict[str, list[tuple[float, float, float]]]:
    """Return explicit PCF component endpoints keyed by UCI."""

    lines = Path(pcf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    result: dict[str, list[tuple[float, float, float]]] = {}
    allowed_kinds = {component_kind} if isinstance(component_kind, str) else set(component_kind)
    for position, start in enumerate(starts):
        if lines[start].strip() not in allowed_kinds:
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


def _support_coordinate_anchor(
    record: ManagementInput,
    visible_regions: list[dict[str, object]],
    trace: dict[str, object],
    pcf_path: Path | None,
) -> tuple[tuple[float, float], tuple[int, ...]] | None:
    if record.coordinate is None or pcf_path is None:
        return None
    pipe_projection = _pcf_pipe_projection(pcf_path, record.coordinate)
    if pipe_projection is None:
        return None
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
        return anchor, ()
    closest = min(
        visible_regions,
        key=lambda region: math.dist(anchor, _bbox_center(tuple(float(v) for v in region["bbox"]))),
    )
    return anchor, (int(closest["graphic_ref"]),)


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
                anchor, refs = support_projection
                if not visible:
                    method = "support-coordinate-pipe-projection-no-sha-uci"
                elif not all_members_visible:
                    method = "support-coordinate-pipe-projection-partial-sha-uci"
                else:
                    method = "support-coordinate-pipe-projection"
            elif not visible or not all_members_visible:
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
                record.ucis, anchor, method, "high", refs,
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
    if pcf_path is not None and weld_map is not None and unresolved_flanges:
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
    return mapped, unresolved


def label_box(number: str) -> tuple[float, float]:
    width = max(MANAGEMENT_FONT_HEIGHT * 0.7, MANAGEMENT_FONT_HEIGHT * 0.68 * len(number))
    return width + 2 * MANAGEMENT_TEXT_PADDING_X, MANAGEMENT_FONT_HEIGHT + 2 * MANAGEMENT_TEXT_PADDING_Y


def plan_management_layout(
    sha_path: Path,
    page: int,
    sheet_stream: str,
    targets: list[ManagementTarget],
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
    for target, point in zip(targets, points):
        width, height = label_box(target.number)
        # The existing solver accepts a radial marker envelope. A half-diagonal
        # fully encloses the final tight rectangle, so all accepted layouts
        # remain collision-free after the rectangular SVG is emitted.
        envelope_radius = math.hypot(width / 2, height / 2)
        normal = nearest_segment_guide(point, drawing_segments)[1]
        options_by_point.append(
            candidate_options(
                point=point,
                source_segments=source_segments,
                source_boxes=source_boxes,
                leader_boxes=leader_boxes,
                region=region,
                marker_radius=envelope_radius,
                gap=45.0,
                normal=normal,
                marker_shape="circle",
                max_candidates=96 if len(points) <= 24 else 48,
                semantic_boxes=semantic_boxes,
                peer_points=tuple(points),
            )
        )
    placements = solve_page_layout(points, options_by_point)
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
        width, height = label_box(target.number)
        edge = _rectangle_edge(target.anchor, center, width, height)
        common = {
            "data-management-kind": target.kind,
            "data-management-number": target.number,
            "data-mapping": target.mapping_method,
        }
        ET.SubElement(group, namespace + "line", {
            "x1": f"{target.anchor[0]:.1f}", "y1": f"{PAGE_HEIGHT-target.anchor[1]:.1f}",
            "x2": f"{edge[0]:.1f}", "y2": f"{PAGE_HEIGHT-edge[1]:.1f}",
            "stroke": MANAGEMENT_COLOR, "stroke-width": "8", "fill": "none",
            "data-layer": "sha-management-leader", **common,
        })
        ET.SubElement(group, namespace + "rect", {
            "x": f"{center[0]-width/2:.1f}", "y": f"{PAGE_HEIGHT-(center[1]+height/2):.1f}",
            "width": f"{width:.1f}", "height": f"{height:.1f}",
            "stroke": MANAGEMENT_COLOR, "stroke-width": "8", "fill": "white",
            "data-layer": "sha-management-marker", **common,
        })
        text = ET.SubElement(group, namespace + "text", {
            "x": f"{center[0]:.1f}", "y": f"{PAGE_HEIGHT-center[1]:.1f}",
            "fill": MANAGEMENT_COLOR, "font-size": f"{MANAGEMENT_FONT_HEIGHT:.1f}",
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
