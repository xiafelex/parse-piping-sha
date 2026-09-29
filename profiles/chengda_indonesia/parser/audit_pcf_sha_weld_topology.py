#!/usr/bin/env python3
"""Audit PCF weld-port topology against the current SHA weld mapping.

This module deliberately does not move a callout.  It records the engineering
topology that a later semantic matcher must satisfy and exposes cases where a
WELD UCI is absent, expands to several SHA graphics, or is only supported by a
standalone ellipse without component-port evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from analyze_iso_split import (
    forward_owned_dynamic_graphics,
    read_sha_streams,
)
from inject_sha_weld_callouts import point_segment_distance
from chengda_template_owned_page import mapping_sheet_streams as sheet_streams
from sha_to_svg_prototype import (
    SHEET_UNIT,
    ellipse_anchors,
    line_segments,
    psm_bbox,
    sheet_line_widths,
    template_line_segments,
    template_line_widths,
)


TOP_LEVEL_RE = re.compile(r"^[A-Z][A-Z0-9-]*$")
POINT_RE = re.compile(
    r"^\s+(END-POINT|BRANCH(\d+)-POINT)\s+"
    r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)"
    r"(?:\s+([-\d.]+))?(?:\s+(\S+))?"
)


@dataclass(frozen=True)
class Port:
    role: str
    coordinate: tuple[float, float, float]
    bore: float | None
    end_preparation: str


@dataclass
class Component:
    kind: str
    start_line: int
    component_identifier: str
    master_component_identifier: str
    uci: str
    weld_number: str
    ports: list[Port]


def strict_pcf_text(path: Path) -> tuple[str, str]:
    source = path.read_bytes()
    if all(value < 0x80 for value in source):
        return source.decode("ascii", errors="strict"), "ascii"
    successes: list[tuple[str, str]] = []
    for codec in ("utf-8-sig", "gb18030"):
        try:
            successes.append((source.decode(codec, errors="strict"), codec))
        except UnicodeDecodeError:
            pass
    if not successes:
        raise UnicodeError(f"{path}: neither UTF-8 nor GB18030 decodes strictly")
    return successes[0]


def field(block: list[str], name: str) -> str:
    prefix = f"{name} "
    for raw in block:
        stripped = raw.strip()
        if stripped.startswith(prefix):
            return stripped.split(maxsplit=1)[1]
    return ""


def coordinate_key(
    coordinate: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(round(value, 3) for value in coordinate)


def parse_components(path: Path) -> tuple[list[Component], str]:
    text, codec = strict_pcf_text(path)
    lines = text.splitlines()
    starts: list[int] = []
    for index, raw in enumerate(lines):
        if raw == "MATERIALS":
            break
        if TOP_LEVEL_RE.fullmatch(raw):
            starts.append(index)
    components: list[Component] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        ports: list[Port] = []
        endpoint_index = 0
        for raw in block:
            match = POINT_RE.match(raw)
            if not match:
                continue
            point_kind = match.group(1)
            if point_kind == "END-POINT":
                endpoint_index += 1
                role = f"END-{endpoint_index}"
            else:
                role = f"BRANCH-{match.group(2)}"
            bore = float(match.group(6)) if match.group(6) is not None else None
            ports.append(
                Port(
                    role=role,
                    coordinate=tuple(
                        float(match.group(index)) for index in (3, 4, 5)
                    ),
                    bore=bore,
                    end_preparation=match.group(7) or "",
                )
            )
        identifier = field(block, "COMPONENT-IDENTIFIER")
        uci = field(block, "UCI")
        # Ignore header/special records that happen to use a top-level keyword
        # but do not participate in component-port topology.
        if not ports and not identifier and not uci:
            continue
        components.append(
            Component(
                kind=lines[start],
                start_line=start + 1,
                component_identifier=identifier,
                master_component_identifier=field(
                    block, "MASTER-COMPONENT-IDENTIFIER"
                ),
                uci=uci,
                weld_number=field(block, "REPEAT-WELD-IDENTIFIER"),
                ports=ports,
            )
        )
    return components, codec


def current_point(
    weld: Component,
    map_by_uci: dict[str, dict[str, object]],
) -> tuple[float, float] | None:
    row = map_by_uci.get(weld.uci)
    if not row or row.get("page") in (None, 999999):
        return None
    x, y = row.get("sha_x"), row.get("sha_y")
    if x in (None, "") or y in (None, ""):
        return None
    try:
        x_value, y_value = float(x), float(y)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x_value) or not math.isfinite(y_value):
        return None
    return x_value, y_value


def audit_topology(
    pcf_path: Path,
    sha_path: Path,
    weld_map_path: Path,
) -> dict[str, object]:
    components, codec = parse_components(pcf_path)
    weld_map = json.loads(weld_map_path.read_text(encoding="utf-8"))
    map_rows = weld_map["welds"]
    map_by_uci = {str(row["uci"]): row for row in map_rows}

    welds = [
        component
        for component in components
        if component.kind == "WELD"
        and component.weld_number
        and component.weld_number != "0"
    ]
    non_welds = [
        component
        for component in components
        if component.kind != "WELD" and component.ports
    ]
    welds_at: dict[tuple[float, float, float], list[Component]] = defaultdict(list)
    components_at: dict[
        tuple[float, float, float], list[tuple[Component, Port]]
    ] = defaultdict(list)
    for weld in welds:
        if weld.ports:
            welds_at[coordinate_key(weld.ports[0].coordinate)].append(weld)
    for component in non_welds:
        for port in component.ports:
            components_at[coordinate_key(port.coordinate)].append(
                (component, port)
            )

    streams = read_sha_streams(sha_path)
    dynamic = forward_owned_dynamic_graphics(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    psm = streams.get("PSMcluster0", b"")
    style_data = streams.get("StyleCluster", b"")
    anchors_by_ref: dict[int, list[dict[str, object]]] = defaultdict(list)
    thick_segments_by_page: dict[
        int, list[tuple[float, float, float, float]]
    ] = {}
    for sheet in sheet_streams(streams, None):
        for graphic_ref, (x, y) in ellipse_anchors(sheet.data).items():
            anchors_by_ref[int(graphic_ref)].append(
                {
                    "page": sheet.page,
                    "sha_point": [round(x, 3), round(y, 3)],
                }
            )
        segments = line_segments(sheet.data) + template_line_segments(
            sheet.data
        )
        width_by_child = sheet_line_widths(sheet.data, style_data)
        width_by_child.update(
            template_line_widths(sheet.data, style_data)
        )
        thick_segments_by_page[sheet.page] = [
            (
                x1 * SHEET_UNIT,
                y1 * SHEET_UNIT,
                x2 * SHEET_UNIT,
                y2 * SHEET_UNIT,
            )
            for x1, y1, x2, y2, _, child_ref in segments
            if float(width_by_child.get(child_ref, 8.0)) >= 8.0
        ]

    weld_rows: list[dict[str, object]] = []
    weld_row_by_number: dict[str, dict[str, object]] = {}
    for weld in sorted(
        welds,
        key=lambda component: (
            int(component.weld_number)
            if component.weld_number.isdigit()
            else math.inf,
            component.weld_number,
        ),
    ):
        port = weld.ports[0] if weld.ports else None
        coordinate = coordinate_key(port.coordinate) if port else None
        neighbors = []
        if coordinate is not None:
            for component, neighbor_port in components_at.get(coordinate, []):
                neighbors.append(
                    {
                        "kind": component.kind,
                        "component_identifier": component.component_identifier,
                        "uci": component.uci,
                        "port_role": neighbor_port.role,
                        "bore": neighbor_port.bore,
                        "end_preparation": neighbor_port.end_preparation,
                    }
                )
        dynamic_records = dynamic.get(weld.uci, [])
        mapped = map_by_uci.get(weld.uci, {})
        augmented = (
            mapped.get("augmentation_source") == "PCF-AUGMENTED"
        )
        dynamic_evidence = []
        for record in dynamic_records:
            graphic_ref = int(record["graphic_ref"])
            dynamic_evidence.append(
                {
                    "graphic_ref": graphic_ref,
                    "space_ref": int(record["space_ref"]),
                    "record_offset": int(record["record_offset"]),
                    "reference_record_offset": int(
                        record.get(
                            "reference_record_offset",
                            record["record_offset"],
                        )
                    ),
                    "ellipse_anchors": anchors_by_ref.get(graphic_ref, []),
                    "psm_bbox": (
                        list(bbox)
                        if (bbox := psm_bbox(psm, graphic_ref)) is not None
                        else None
                    ),
                }
            )
        risk_flags: list[str] = []
        if not dynamic_records:
            if augmented:
                risk_flags.append(
                    "PCF_AUGMENTED_NO_NATIVE_SHA_POINT_EXPECTED"
                )
            else:
                risk_flags.append(
                    "WELD_UCI_HAS_NO_SHA_DYNAMIC_RECORD"
                )
        elif len(dynamic_records) > 1:
            risk_flags.append("WELD_UCI_HAS_MULTIPLE_SHA_DYNAMIC_RECORDS")
        if mapped.get("page") in (None, 999999) or mapped.get("graphic_ref") is None:
            risk_flags.append("CURRENT_SHA_POINT_UNMAPPED")
        if len(neighbors) != 2:
            risk_flags.append("PCF_WELD_NEIGHBOR_COUNT_NOT_TWO")
        if not augmented and mapped.get("point_evidence") not in {
            "sheet-ellipse-anchor-forward-owned",
            "pcf-topology-pipe-end",
            "pcf-topology-component-end",
        }:
            risk_flags.append("NATIVE_UCI_POINT_NOT_YET_TOPOLOGY_VERIFIED")
        branch_context = any(
            str(neighbor["port_role"]).startswith("BRANCH-")
            for neighbor in neighbors
        )
        row = {
            "weld_number": weld.weld_number,
            "component_identifier": weld.component_identifier,
            "master_component_identifier": weld.master_component_identifier,
            "uci": weld.uci,
            "pcf_coordinate": list(coordinate) if coordinate else None,
            "bore": port.bore if port else None,
            "neighbors": neighbors,
            "branch_context": branch_context,
            "current_sha_mapping": {
                "page": mapped.get("page"),
                "graphic_ref": mapped.get("graphic_ref"),
                "sha_point": (
                    [mapped.get("sha_x"), mapped.get("sha_y")]
                    if current_point(weld, map_by_uci) is not None
                    else None
                ),
                "point_evidence": mapped.get("point_evidence", ""),
            },
            "sha_dynamic_record_count": len(dynamic_records),
            "sha_dynamic_evidence": dynamic_evidence,
            "pcf_augmented": augmented,
            "risk_flags": risk_flags,
            "semantic_status": (
                "PCF_AUGMENTED_DRAWING_RULE"
                if augmented
                else
                "PCF_TOPOLOGY_PIPE_END_VERIFIED"
                if mapped.get("point_evidence")
                in {
                    "pcf-topology-pipe-end",
                    "pcf-topology-component-end",
                }
                else
                "UNRESOLVED"
                if "CURRENT_SHA_POINT_UNMAPPED" in risk_flags
                else "FORWARD_OWNED_SHA_ELLIPSE_VERIFIED"
            ),
        }
        weld_rows.append(row)
        weld_row_by_number[weld.weld_number] = row

    junctions: list[dict[str, object]] = []
    branch_path_weld_numbers: set[str] = set()
    for junction in non_welds:
        if len(junction.ports) < 3:
            continue
        port_rows = []
        for port in junction.ports:
            key = coordinate_key(port.coordinate)
            direct_welds = welds_at.get(key, [])
            outward = [
                component
                for component, _ in components_at.get(key, [])
                if component.component_identifier
                != junction.component_identifier
            ]
            outward_rows = []
            for component in outward:
                other_ports = [
                    candidate
                    for candidate in component.ports
                    if coordinate_key(candidate.coordinate) != key
                ]
                next_welds = []
                for other_port in other_ports:
                    next_welds.extend(
                        weld.weld_number
                        for weld in welds_at.get(
                            coordinate_key(other_port.coordinate), []
                        )
                    )
                outward_rows.append(
                    {
                        "kind": component.kind,
                        "component_identifier": component.component_identifier,
                        "uci": component.uci,
                        "next_weld_numbers": sorted(
                            set(next_welds),
                            key=lambda value: (
                                int(value) if value.isdigit() else math.inf,
                                value,
                            ),
                        ),
                    }
                )
                if port.role.startswith("BRANCH-"):
                    branch_path_weld_numbers.update(
                        weld.weld_number for weld in direct_welds
                    )
                    branch_path_weld_numbers.update(next_welds)
            port_rows.append(
                {
                    "port_role": port.role,
                    "coordinate": list(key),
                    "bore": port.bore,
                    "direct_weld_numbers": [
                        weld.weld_number for weld in direct_welds
                    ],
                    "outward_components": outward_rows,
                }
            )
        junctions.append(
            {
                "kind": junction.kind,
                "component_identifier": junction.component_identifier,
                "uci": junction.uci,
                "ports": port_rows,
            }
        )

    for row in weld_rows:
        row["path_context"] = (
            "BRANCH"
            if str(row["weld_number"]) in branch_path_weld_numbers
            else "MAIN_OR_UNRESOLVED"
        )

    two_port_spans: list[dict[str, object]] = []
    valid_ratios: list[float] = []
    for component in non_welds:
        if component.kind != "PIPE" or len(component.ports) != 2:
            continue
        first_key = coordinate_key(component.ports[0].coordinate)
        second_key = coordinate_key(component.ports[1].coordinate)
        first_welds = welds_at.get(first_key, [])
        second_welds = welds_at.get(second_key, [])
        if len(first_welds) != 1 or len(second_welds) != 1:
            continue
        first_weld, second_weld = first_welds[0], second_welds[0]
        pcf_length = math.dist(first_key, second_key)
        first_point = current_point(first_weld, map_by_uci)
        second_point = current_point(second_weld, map_by_uci)
        sha_distance = (
            math.dist(first_point, second_point)
            if first_point is not None and second_point is not None
            else None
        )
        first_map = map_by_uci.get(first_weld.uci, {})
        second_map = map_by_uci.get(second_weld.uci, {})
        first_page = first_map.get("page")
        second_page = second_map.get("page")
        centreline_coverage: float | None = None
        if (
            first_point is not None
            and second_point is not None
            and first_page not in (None, 999999)
            and first_page == second_page
        ):
            source_segments = thick_segments_by_page.get(
                int(first_page), []
            )
            samples = [
                (
                    first_point[0]
                    + index / 12.0
                    * (second_point[0] - first_point[0]),
                    first_point[1]
                    + index / 12.0
                    * (second_point[1] - first_point[1]),
                )
                for index in range(1, 12)
            ]
            if source_segments and samples:
                covered = sum(
                    min(
                        point_segment_distance(
                            x, y, x1, y1, x2, y2
                        )
                        for x1, y1, x2, y2 in source_segments
                    )
                    <= 35.0
                    for x, y in samples
                )
                centreline_coverage = covered / len(samples)
        ratio = (
            sha_distance / pcf_length
            if sha_distance is not None and pcf_length > 1e-9
            else None
        )
        if ratio is not None and math.isfinite(ratio):
            valid_ratios.append(ratio)
        span = {
            "kind": component.kind,
            "component_identifier": component.component_identifier,
            "uci": component.uci,
            "weld_numbers": [
                first_weld.weld_number,
                second_weld.weld_number,
            ],
            "pcf_length": round(pcf_length, 3),
            "current_sha_distance": (
                round(sha_distance, 3) if sha_distance is not None else None
            ),
            "display_to_pcf_ratio": round(ratio, 6) if ratio is not None else None,
            "same_page_straight_pipe_centerline_coverage": (
                round(centreline_coverage, 3)
                if centreline_coverage is not None
                else None
            ),
            "risk_flags": [],
        }
        if first_point is None or second_point is None:
            span["risk_flags"].append("SPAN_HAS_UNMAPPED_WELD_ENDPOINT")
        elif (
            centreline_coverage is not None
            and centreline_coverage < 0.55
        ):
            span["risk_flags"].append(
                "CURRENT_SHA_WELD_PAIR_NOT_ON_ONE_PIPE_CENTERLINE"
            )
            for weld_number in span["weld_numbers"]:
                row = weld_row_by_number.get(str(weld_number))
                if row is not None and (
                    "ADJACENT_PIPE_CENTERLINE_MISMATCH"
                    not in row["risk_flags"]
                ):
                    row["risk_flags"].append(
                        "ADJACENT_PIPE_CENTERLINE_MISMATCH"
                    )
        two_port_spans.append(span)

    median_ratio = statistics.median(valid_ratios) if valid_ratios else None

    semantic_matching_ready = all(
        row["semantic_status"]
        in {
            "FORWARD_OWNED_SHA_ELLIPSE_VERIFIED",
            "PCF_AUGMENTED_DRAWING_RULE",
            "PCF_TOPOLOGY_PIPE_END_VERIFIED",
        }
        for row in weld_rows
    )
    summary = {
        "numbered_weld_count": len(weld_rows),
        "current_mapped_count": sum(
            row["current_sha_mapping"]["sha_point"] is not None
            for row in weld_rows
        ),
        "pcf_augmented_weld_count": sum(
            bool(row["pcf_augmented"]) for row in weld_rows
        ),
        "uci_no_dynamic_record_count": sum(
            "WELD_UCI_HAS_NO_SHA_DYNAMIC_RECORD" in row["risk_flags"]
            for row in weld_rows
        ),
        "uci_multiple_dynamic_record_count": sum(
            "WELD_UCI_HAS_MULTIPLE_SHA_DYNAMIC_RECORDS"
            in row["risk_flags"]
            for row in weld_rows
        ),
        "topology_unverified_native_point_count": sum(
            "NATIVE_UCI_POINT_NOT_YET_TOPOLOGY_VERIFIED"
            in row["risk_flags"]
            for row in weld_rows
        ),
        "adjacent_pipe_centerline_mismatch_weld_count": sum(
            "ADJACENT_PIPE_CENTERLINE_MISMATCH" in row["risk_flags"]
            for row in weld_rows
        ),
        "junction_count": len(junctions),
        "two_port_span_count": len(two_port_spans),
        "pipeline_median_display_to_pcf_ratio": (
            round(median_ratio, 6) if median_ratio is not None else None
        ),
        "semantic_matching_ready": semantic_matching_ready,
        "semantic_gate": (
            "All numbered native welds use the ellipse reference serialized "
            "immediately before their UCI record; PCF-AUGMENTED welds use "
            "the documented pipe-centreline drawing rule; missing native "
            "WELD UCIs use a unique PCF PIPE endpoint oriented and snapped "
            "to that PIPE's decoded SHA centreline."
            if semantic_matching_ready
            else
            "At least one numbered weld lacks corrected SHA ellipse or "
            "documented PCF-AUGMENTED evidence; do not publish."
        ),
    }
    return {
        "pcf": str(pcf_path),
        "sha": str(sha_path),
        "weld_map": str(weld_map_path),
        "pcf_codec": codec,
        "purpose": (
            "audit corrected forward-owned SHA references against PCF "
            "topology; no callout positions are changed"
        ),
        "summary": summary,
        "junction_neighborhoods": junctions,
        "two_port_component_spans": two_port_spans,
        "welds": weld_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcf", type=Path)
    parser.add_argument("sha", type=Path)
    parser.add_argument("weld_map", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = audit_topology(args.pcf, args.sha, args.weld_map)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
