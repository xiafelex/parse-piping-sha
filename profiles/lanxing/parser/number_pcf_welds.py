#!/usr/bin/env python3
"""Assign stable weld numbers in a PCF and map them to SHA weld dots by UCI.

The numbering source of truth becomes the PCF copy.  SHA is used only to
determine which weld appears on which ISO page and where its 2D point lies on
the plotted drawing.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import zlib
from dataclasses import dataclass, replace
from pathlib import Path

from analyze_iso_split import (
    forward_owned_dynamic_graphics,
    read_sha_streams,
)
from inject_sha_weld_callouts import point_segment_distance, sheet_streams
from sha_to_svg_prototype import (
    SHEET_UNIT,
    composite_segments,
    ellipse_anchors,
    line_segments,
    psm_bbox,
    sheet_line_widths,
    template_line_segments,
    template_line_widths,
)


TOP_LEVEL = re.compile(r"^[A-Z][A-Z0-9-]*$")
POINT_RE = re.compile(r"(END-POINT|BRANCH1-POINT)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)")


@dataclass
class WeldBlock:
    start: int
    end: int
    lines: list[str]
    uci: str
    component_identifier: str
    master_component_identifier: str
    skey: str
    category: str
    endpoint: tuple[float, float, float, float] | None
    description: str
    repeat_weld_identifier: str
    augmentation_source: str
    augmentation_mode: str
    owner_uci: str
    owner_fraction: float | None
    neighbor_uci_a: str
    neighbor_uci_b: str


@dataclass
class PipeComponent:
    component_identifier: str
    uci: str
    endpoint_a: tuple[float, float, float, float]
    endpoint_b: tuple[float, float, float, float]


def parse_weld_blocks(pcf_path: Path) -> tuple[list[str], list[WeldBlock]]:
    lines = pcf_path.read_text(errors="replace").splitlines()
    starts = [
        (index, line)
        for index, line in enumerate(lines)
        if TOP_LEVEL.fullmatch(line) and line != "MATERIALS"
    ]

    welds: list[WeldBlock] = []
    for index, (start, kind) in enumerate(starts):
        if kind != "WELD":
            continue
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        block_lines = lines[start:end]
        uci = ""
        component_identifier = ""
        master_component_identifier = ""
        skey = ""
        category = ""
        endpoint = None
        description = ""
        repeat_weld_identifier = ""
        augmentation_source = ""
        augmentation_mode = ""
        owner_uci = ""
        owner_fraction = None
        neighbor_uci_a = ""
        neighbor_uci_b = ""
        for raw in block_lines:
            stripped = raw.strip()
            if stripped.startswith("UCI "):
                uci = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("COMPONENT-IDENTIFIER "):
                component_identifier = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("MASTER-COMPONENT-IDENTIFIER "):
                master_component_identifier = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("SKEY "):
                skey = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("CATEGORY "):
                category = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("WELD-ATTRIBUTE8 "):
                description = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("REPEAT-WELD-IDENTIFIER "):
                repeat_weld_identifier = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("WELD-ATTRIBUTE90 "):
                augmentation_source = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("WELD-ATTRIBUTE91 "):
                augmentation_mode = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("WELD-ATTRIBUTE92 "):
                owner_uci = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("WELD-ATTRIBUTE93 "):
                try:
                    owner_fraction = float(stripped.split(maxsplit=1)[1])
                except ValueError:
                    owner_fraction = None
            elif stripped.startswith("WELD-ATTRIBUTE94 "):
                neighbor_uci_a = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("WELD-ATTRIBUTE95 "):
                neighbor_uci_b = stripped.split(maxsplit=1)[1]
            else:
                match = POINT_RE.fullmatch(stripped)
                if match and match.group(1) == "END-POINT" and endpoint is None:
                    endpoint = tuple(float(match.group(i)) for i in range(2, 6))
        if not uci:
            continue
        welds.append(
            WeldBlock(
                start=start,
                end=end,
                lines=block_lines,
                uci=uci,
                component_identifier=component_identifier,
                master_component_identifier=master_component_identifier,
                skey=skey,
                category=category,
                endpoint=endpoint,
                description=description,
                repeat_weld_identifier=repeat_weld_identifier,
                augmentation_source=augmentation_source,
                augmentation_mode=augmentation_mode,
                owner_uci=owner_uci,
                owner_fraction=owner_fraction,
                neighbor_uci_a=neighbor_uci_a,
                neighbor_uci_b=neighbor_uci_b,
            )
        )
    return lines, welds


def parse_pipe_components(lines: list[str]) -> list[PipeComponent]:
    starts = [
        (index, line)
        for index, line in enumerate(lines)
        if TOP_LEVEL.fullmatch(line) and line != "MATERIALS"
    ]
    pipes: list[PipeComponent] = []
    for index, (start, kind) in enumerate(starts):
        if kind not in {"PIPE", "PIPE-FIXED"}:
            continue
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        identifier = re.search(r"COMPONENT-IDENTIFIER\s+(\S+)", block)
        uci = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        points = [
            (
                float(match.group(2)),
                float(match.group(3)),
                float(match.group(4)),
                float(match.group(5)),
            )
            for match in POINT_RE.finditer(block)
            if match.group(1) == "END-POINT"
        ]
        if identifier and uci and len(points) >= 2:
            pipes.append(
                PipeComponent(
                    component_identifier=identifier.group(1),
                    uci=uci.group(1),
                    endpoint_a=points[0],
                    endpoint_b=points[1],
                )
            )
    return pipes


def long_pipe_drawing_overrides(
    welds: list[WeldBlock],
    pipes: list[PipeComponent],
) -> dict[str, dict[str, object]]:
    """Distribute a consecutive synthetic weld group over one long pipe run.

    EP3D can represent a divided long run as several near-equal PIPE blocks
    separated by very short connector components.  Endpoint-created WELDs then
    occur in close pairs at those connectors.  For an ISO drawing, a group of
    four or more consecutive generated weld numbers is clearer when placed at
    near-equal cumulative-length stations across the complete straight run.
    PCF engineering coordinates remain unchanged; this function supplies only
    an explicit drawing-position override.
    """

    pipe_by_id = {pipe.component_identifier: pipe for pipe in pipes}
    entries: list[tuple[WeldBlock, PipeComponent]] = []
    for weld in welds:
        if (
            weld.augmentation_source != "PCF-AUGMENTED"
            or weld.augmentation_mode != "COMPONENT-ENDPOINT"
        ):
            continue
        pipe = pipe_by_id.get(weld.master_component_identifier)
        if pipe is not None and pipe.uci == weld.owner_uci:
            entries.append((weld, pipe))
    if len(entries) < 4:
        return {}

    def vector(pipe: PipeComponent) -> tuple[float, float, float]:
        return tuple(
            pipe.endpoint_b[index] - pipe.endpoint_a[index]
            for index in range(3)
        )

    def length(pipe: PipeComponent) -> float:
        return math.sqrt(sum(value * value for value in vector(pipe)))

    def point_line_distance(
        point: tuple[float, float, float, float],
        pipe: PipeComponent,
    ) -> float:
        direction = vector(pipe)
        magnitude = length(pipe)
        relative = tuple(point[index] - pipe.endpoint_a[index] for index in range(3))
        cross = (
            relative[1] * direction[2] - relative[2] * direction[1],
            relative[2] * direction[0] - relative[0] * direction[2],
            relative[0] * direction[1] - relative[1] * direction[0],
        )
        return math.sqrt(sum(value * value for value in cross)) / magnitude

    def same_run(first: PipeComponent, second: PipeComponent) -> bool:
        first_length, second_length = length(first), length(second)
        if min(first_length, second_length) < 3000.0:
            return False
        if abs(first.endpoint_a[3] - second.endpoint_a[3]) > 1e-6:
            return False
        first_vector, second_vector = vector(first), vector(second)
        parallel = abs(
            sum(first_vector[index] * second_vector[index] for index in range(3))
            / (first_length * second_length)
        )
        if parallel < 0.9999:
            return False
        if max(
            point_line_distance(second.endpoint_a, first),
            point_line_distance(second.endpoint_b, first),
        ) > 5.0:
            return False
        endpoint_gap = min(
            math.dist(first_point[:3], second_point[:3])
            for first_point in (first.endpoint_a, first.endpoint_b)
            for second_point in (second.endpoint_a, second.endpoint_b)
        )
        return endpoint_gap <= 250.0

    adjacency: dict[int, set[int]] = {index: set() for index in range(len(entries))}
    for first_index, (_, first_pipe) in enumerate(entries):
        for second_index in range(first_index + 1, len(entries)):
            second_pipe = entries[second_index][1]
            if first_pipe.component_identifier == second_pipe.component_identifier or same_run(
                first_pipe, second_pipe
            ):
                adjacency[first_index].add(second_index)
                adjacency[second_index].add(first_index)

    components: list[list[int]] = []
    unseen = set(adjacency)
    while unseen:
        stack = [unseen.pop()]
        component: list[int] = []
        while stack:
            current = stack.pop()
            component.append(current)
            neighbors = adjacency[current] & unseen
            unseen.difference_update(neighbors)
            stack.extend(neighbors)
        components.append(component)

    overrides: dict[str, dict[str, object]] = {}
    for component in components:
        group = [entries[index] for index in component]
        unique_pipes = {
            pipe.component_identifier: pipe for _, pipe in group
        }
        if len(group) < 4 or len(unique_pipes) < 3 or len(group) != len(unique_pipes) + 1:
            continue
        numbers: list[int] = []
        for weld, _ in group:
            if not weld.repeat_weld_identifier.isdigit():
                break
            numbers.append(int(weld.repeat_weld_identifier))
        else:
            ordered_numbers = sorted(numbers)
            if ordered_numbers != list(
                range(ordered_numbers[0], ordered_numbers[0] + len(ordered_numbers))
            ):
                continue
            pipe_lengths = [length(pipe) for pipe in unique_pipes.values()]
            if max(pipe_lengths) / min(pipe_lengths) > 1.5:
                continue

            seed = next(iter(unique_pipes.values()))
            seed_length = length(seed)
            direction = tuple(value / seed_length for value in vector(seed))
            origin = seed.endpoint_a[:3]

            def projection(point: tuple[float, float, float, float]) -> float:
                return sum(
                    (point[index] - origin[index]) * direction[index]
                    for index in range(3)
                )

            run_low = min(
                projection(point)
                for pipe in unique_pipes.values()
                for point in (pipe.endpoint_a, pipe.endpoint_b)
            )
            run_high = max(
                projection(point)
                for pipe in unique_pipes.values()
                for point in (pipe.endpoint_a, pipe.endpoint_b)
            )
            ordered_group = sorted(
                group, key=lambda item: int(item[0].repeat_weld_identifier)
            )
            original_positions = [
                projection((*weld.endpoint[:3], weld.endpoint[3]))
                for weld, _ in ordered_group
                if weld.endpoint is not None
            ]
            if len(original_positions) != len(ordered_group):
                continue
            increasing = original_positions[-1] >= original_positions[0]
            run_start, run_end = (
                (run_low, run_high) if increasing else (run_high, run_low)
            )
            group_id = (
                f"LONG-PIPE-{ordered_numbers[0]}-{ordered_numbers[-1]}-"
                + "-".join(sorted(unique_pipes))
            )
            proposed: dict[str, dict[str, object]] = {}
            valid = True
            for rank, (weld, owner_pipe) in enumerate(ordered_group, start=1):
                target_projection = run_start + rank / (len(group) + 1) * (
                    run_end - run_start
                )
                target = tuple(
                    origin[index] + target_projection * direction[index]
                    for index in range(3)
                )
                owner_vector = vector(owner_pipe)
                owner_length_squared = sum(value * value for value in owner_vector)
                fraction = sum(
                    (target[index] - owner_pipe.endpoint_a[index])
                    * owner_vector[index]
                    for index in range(3)
                ) / owner_length_squared
                if not -0.01 <= fraction <= 1.01:
                    valid = False
                    break
                fraction = max(0.0, min(1.0, fraction))
                owner_projection_a = projection(owner_pipe.endpoint_a)
                owner_projection_b = projection(owner_pipe.endpoint_b)
                run_span = run_end - run_start
                owner_station_a = (
                    owner_projection_a - run_start
                ) / run_span
                owner_station_b = (
                    owner_projection_b - run_start
                ) / run_span
                proposed[weld.uci] = {
                    "owner_fraction": fraction,
                    "mode": "LONG-PIPE-EQUAL-DISTRIBUTION",
                    "group_id": group_id,
                    "rank": rank,
                    "count": len(group),
                    "target_3d": [round(value, 6) for value in target],
                    "original_owner_fraction": weld.owner_fraction,
                    "owner_run_order": min(owner_station_a, owner_station_b),
                    "owner_forward_with_run": owner_station_b >= owner_station_a,
                }
            if valid:
                overrides.update(proposed)
    return overrides


def sha_weld_points(sha_path: Path, distance_threshold: float) -> dict[str, dict[str, object]]:
    streams = read_sha_streams(sha_path)
    sheets = sheet_streams(streams, None)
    dynamic = forward_owned_dynamic_graphics(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    graphic_to_uci = {
        int(record["graphic_ref"]): uci
        for uci, records in dynamic.items()
        for record in records
    }
    # Shape2D ellipse anchors are the strongest direct evidence for a native
    # weld dot.  Do not require a tiny PSM envelope: some valid ellipse refs
    # share a low-16 identifier with a large container and PSM lookup then
    # returns the container bounds (thousands of page units), previously
    # dropping otherwise exact, zero-distance weld points.
    graphic_regions = list(graphic_to_uci.items())

    by_uci: dict[str, dict[str, object]] = {}
    for sheet in sheets:
        sheet_name = sheet.stream_name
        data = sheet.data
        page_number = sheet.page
        segments = line_segments(data) + template_line_segments(data) + composite_segments(data)
        # The ellipse primitive carries the true page-space centre for the
        # micro weld dot; PSM's tiny envelope can be in a displaced layout
        # coordinate system.
        anchors = ellipse_anchors(data)
        for graphic_ref, uci in graphic_regions:
            # PSM is container-global; without a matching ellipse primitive
            # this region is not evidence that the weld exists on this page.
            if graphic_ref not in anchors:
                continue
            center_x, center_y = anchors[graphic_ref]
            best = min(
                point_segment_distance(center_x, center_y, x1 * 16800, y1 * 16800, x2 * 16800, y2 * 16800)
                for x1, y1, x2, y2, _, _ in segments
            )
            if best > distance_threshold:
                continue
            candidate = {
                "uci": uci,
                "graphic_ref": graphic_ref,
                "page": page_number,
                "sheet_stream": sheet_name,
                "sha_x": round(center_x, 3),
                "sha_y": round(center_y, 3),
                "distance_to_geometry": round(best, 3),
                "point_evidence": (
                    "sheet-ellipse-anchor-forward-owned"
                ),
            }
            current = by_uci.get(uci)
            if current is None or (
                page_number,
                center_y,
                center_x,
            ) < (
                int(current["page"]),
                float(current["sha_y"]),
                float(current["sha_x"]),
            ):
                by_uci[uci] = candidate
    return by_uci


def sha_implicit_pipe_end_weld_points(
    sha_path: Path,
    source_lines: list[str],
    welds: list[WeldBlock],
    known_points: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Recover a missing native WELD at a uniquely owned PCF PIPE endpoint.

    Some EP3D SHAs omit the WELD UCI/dot while retaining the adjacent PIPE and
    fitting graphics.  This rule is deliberately narrower than proximity
    matching: the numbered WELD coordinate must equal exactly one PIPE
    endpoint, the opposite PIPE end or the non-pipe endpoint component must
    provide orientation evidence, and the selected SHA point is snapped to
    that PIPE's decoded thick centreline.
    """

    def coordinate_key(
        point: tuple[float, float, float, float],
    ) -> tuple[float, float, float]:
        return tuple(round(float(point[index]), 3) for index in range(3))

    pipes = parse_pipe_components(source_lines)
    welds_at: dict[tuple[float, float, float], list[WeldBlock]] = {}
    for weld in welds:
        if weld.endpoint is not None:
            welds_at.setdefault(coordinate_key(weld.endpoint), []).append(weld)

    # Retain only engineering components with an explicit UCI and port at the
    # coordinate.  The PIPE owner itself is removed when selecting the
    # endpoint-side orientation component below.
    components_at: dict[
        tuple[float, float, float], list[tuple[str, str]]
    ] = {}
    two_port_components: list[
        tuple[str, str, list[tuple[float, float, float, float]]]
    ] = []
    starts = [
        (index, line)
        for index, line in enumerate(source_lines)
        if TOP_LEVEL.fullmatch(line) and line != "MATERIALS"
    ]
    for position, (start, kind) in enumerate(starts):
        if kind == "WELD":
            continue
        end = (
            starts[position + 1][0]
            if position + 1 < len(starts)
            else len(source_lines)
        )
        block = "\n".join(source_lines[start:end])
        uci_match = re.search(r"\bUCI\s+(\{[^}]+\})", block)
        if uci_match is None:
            continue
        uci = uci_match.group(1)
        explicit_endpoints = [
            tuple(float(match.group(index)) for index in range(1, 5))
            for match in re.finditer(
                r"END-POINT\s+([-\d.]+)\s+([-\d.]+)\s+"
                r"([-\d.]+)\s+([-\d.]+)",
                block,
            )
        ]
        if len(explicit_endpoints) == 2:
            two_port_components.append(
                (kind, uci, explicit_endpoints)
            )
        for point_match in POINT_RE.finditer(block):
            point = tuple(
                float(point_match.group(index)) for index in range(2, 6)
            )
            components_at.setdefault(coordinate_key(point), []).append(
                (kind, uci)
            )
        # A CAP can own a branch/tap port without an END-POINT at that
        # coordinate.  EP3D writes it as TAP-CONNECTION followed by CO-ORDS;
        # this is an explicit component port and therefore valid endpoint
        # orientation evidence (unlike arbitrary FLOW-ARROW/BOLT CO-ORDS).
        for tap_match in re.finditer(
            r"TAP-CONNECTION\s*\n\s*CO-ORDS\s+"
            r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)",
            block,
        ):
            point = tuple(
                float(tap_match.group(index)) for index in range(1, 5)
            )
            components_at.setdefault(coordinate_key(point), []).append(
                (kind, uci)
            )

    def endpoint_neighbor(
        coordinate: tuple[float, float, float, float],
        owner_uci: str,
    ) -> str:
        key = coordinate_key(coordinate)
        mapped_welds = [
            weld
            for weld in welds_at.get(key, [])
            if weld.uci in known_points
        ]
        if len(mapped_welds) == 1:
            return mapped_welds[0].uci
        non_owner_ucis = sorted(
            {
                uci
                for _, uci in components_at.get(key, [])
                if uci != owner_uci
            }
        )
        return non_owner_ucis[0] if len(non_owner_ucis) == 1 else ""

    def nearby_component_neighbor(
        coordinate: tuple[float, float, float, float],
        owner_uci: str,
        tolerance: float = 5.0,
    ) -> str:
        candidates = {
            uci
            for key, entries in components_at.items()
            if math.dist(key, coordinate_key(coordinate)) <= tolerance
            for _, uci in entries
            if uci and uci != owner_uci
        }
        return next(iter(candidates)) if len(candidates) == 1 else ""

    inferred_inputs: list[WeldBlock] = []
    for weld in welds:
        if (
            weld.uci in known_points
            or weld.endpoint is None
            or weld.repeat_weld_identifier.strip() in {"", "0"}
        ):
            continue
        candidates: list[WeldBlock] = []
        weld_key = coordinate_key(weld.endpoint)
        for pipe in pipes:
            for endpoint_index, endpoint in enumerate(
                (pipe.endpoint_a, pipe.endpoint_b)
            ):
                if coordinate_key(endpoint) != weld_key:
                    continue
                neighbor_a = endpoint_neighbor(pipe.endpoint_a, pipe.uci)
                neighbor_b = endpoint_neighbor(pipe.endpoint_b, pipe.uci)
                if not neighbor_a and not neighbor_b:
                    continue
                candidates.append(
                    replace(
                        weld,
                        augmentation_source="PCF-AUGMENTED",
                        augmentation_mode="TOPOLOGY-PIPE-END",
                        owner_uci=pipe.uci,
                        owner_fraction=float(endpoint_index),
                        neighbor_uci_a=neighbor_a,
                        neighbor_uci_b=neighbor_b,
                    )
                )
        # Terminal flanges and similar two-port items can carry the weld at
        # their component end without an intervening PIPE block.  Keep this
        # inference strict: the WELD must equal one explicit component port,
        # and the opposite port must have exactly one nearby PCF component
        # (small flange-face gaps of a few model units are normal).
        if not candidates:
            component_candidates: list[WeldBlock] = []
            for kind, owner_uci, endpoints in two_port_components:
                if kind in {"PIPE", "WELD"}:
                    continue
                matching_indices = [
                    index
                    for index, endpoint in enumerate(endpoints)
                    if coordinate_key(endpoint) == weld_key
                ]
                if len(matching_indices) != 1:
                    continue
                endpoint_index = matching_indices[0]
                other_index = 1 - endpoint_index
                other_neighbor = (
                    endpoint_neighbor(endpoints[other_index], owner_uci)
                    or nearby_component_neighbor(
                        endpoints[other_index], owner_uci
                    )
                )
                if not other_neighbor:
                    continue
                component_candidates.append(
                    replace(
                        weld,
                        augmentation_source="PCF-AUGMENTED",
                        augmentation_mode="TOPOLOGY-COMPONENT-END",
                        owner_uci=owner_uci,
                        owner_fraction=float(endpoint_index),
                        neighbor_uci_a=(
                            other_neighbor if endpoint_index == 1 else ""
                        ),
                        neighbor_uci_b=(
                            other_neighbor if endpoint_index == 0 else ""
                        ),
                    )
                )
            candidates.extend(component_candidates)
        if len(candidates) == 1:
            inferred_inputs.append(candidates[0])

    inferred = sha_virtual_weld_points(
        sha_path, inferred_inputs, known_points, {}
    )
    inferred_mode = {
        weld.uci: weld.augmentation_mode for weld in inferred_inputs
    }
    unresolved_component_ends = [
        weld
        for weld in inferred_inputs
        if (
            weld.augmentation_mode == "TOPOLOGY-COMPONENT-END"
            and weld.uci not in inferred
        )
    ]
    if unresolved_component_ends:
        streams = read_sha_streams(sha_path)
        dynamic = forward_owned_dynamic_graphics(
            streams.get("Unclustered Dynamic Attributes", b"")
        )
        already_used = {
            (int(point["page"]), int(point["graphic_ref"]))
            for point in known_points.values()
            if (
                point.get("page") not in (None, 999999)
                and point.get("graphic_ref") is not None
            )
        }
        for weld in unresolved_component_ends:
            candidates: list[
                tuple[
                    int,
                    str,
                    int,
                    float,
                    float,
                    float,
                    tuple[float, float, float, float, int, int],
                ]
            ] = []
            owner_refs = {
                int(record["graphic_ref"]) & 0xFFFF
                for record in dynamic.get(weld.owner_uci, [])
            }
            for sheet in sheet_streams(streams, None):
                segments = (
                    line_segments(sheet.data)
                    + template_line_segments(sheet.data)
                )
                owner_segments = [
                    segment
                    for segment in segments
                    if any(
                        abs(
                            (
                                (
                                    (int(segment[4]) & 0xFFFF)
                                    - owner_ref
                                    + 0x8000
                                )
                                & 0xFFFF
                            )
                            - 0x8000
                        )
                        <= 12
                        for owner_ref in owner_refs
                    )
                ]
                if not owner_segments:
                    continue
                for graphic_ref, (anchor_x, anchor_y) in ellipse_anchors(
                    sheet.data
                ).items():
                    if (sheet.page, int(graphic_ref)) in already_used:
                        continue
                    distance, nearest_segment = min(
                        (
                            (
                                point_segment_distance(
                                    anchor_x,
                                    anchor_y,
                                    x1 * SHEET_UNIT,
                                    y1 * SHEET_UNIT,
                                    x2 * SHEET_UNIT,
                                    y2 * SHEET_UNIT,
                                ),
                                segment,
                            )
                            for segment in owner_segments
                            for x1, y1, x2, y2, _, _ in [segment]
                        ),
                        key=lambda item: item[0],
                    )
                    if distance <= 25.0:
                        candidates.append(
                            (
                                sheet.page,
                                sheet.stream_name,
                                int(graphic_ref),
                                anchor_x,
                                anchor_y,
                                distance,
                                nearest_segment,
                            )
                        )
            # Requiring exactly one unused micro ellipse on the component's
            # own visible group keeps this a decoded SHA-anchor rule, not a
            # nearest-dot heuristic.
            if len(candidates) != 1:
                continue
            (
                page,
                sheet_stream,
                graphic_ref,
                anchor_x,
                anchor_y,
                distance,
                nearest_segment,
            ) = candidates[0]
            x1, y1, x2, y2, parent_ref, _ = nearest_segment
            inferred[weld.uci] = {
                "uci": weld.uci,
                "graphic_ref": graphic_ref,
                "page": page,
                "sheet_stream": sheet_stream,
                "sha_x": round(anchor_x, 3),
                "sha_y": round(anchor_y, 3),
                "distance_to_geometry": round(distance, 3),
                "distance_to_pipe_centerline": round(distance, 3),
                "owner_uci": weld.owner_uci,
                "owner_fraction": weld.owner_fraction,
                "drawing_owner_fraction": weld.owner_fraction,
                "orientation_neighbors": [
                    weld.neighbor_uci_a,
                    weld.neighbor_uci_b,
                ],
                "pipe_axis": [
                    [
                        round(x1 * SHEET_UNIT, 3),
                        round(y1 * SHEET_UNIT, 3),
                    ],
                    [
                        round(x2 * SHEET_UNIT, 3),
                        round(y2 * SHEET_UNIT, 3),
                    ],
                ],
                "pipe_visible_segment": [
                    [
                        round(x1 * SHEET_UNIT, 3),
                        round(y1 * SHEET_UNIT, 3),
                    ],
                    [
                        round(x2 * SHEET_UNIT, 3),
                        round(y2 * SHEET_UNIT, 3),
                    ],
                ],
                "pipe_graphic_ref_low16": (
                    f"0x{int(parent_ref) & 0xFFFF:04X}"
                ),
                "component_anchor_rule": (
                    "unique unused SHA ellipse on owner component group"
                ),
            }
    for uci, point in inferred.items():
        component_end = (
            inferred_mode.get(uci) == "TOPOLOGY-COMPONENT-END"
        )
        point["point_evidence"] = (
            "pcf-topology-component-end"
            if component_end
            else "pcf-topology-pipe-end"
        )
        point["topology_rule"] = (
            "unique PCF two-port component end + oriented SHA component axis"
            if component_end
            else "unique PCF PIPE endpoint + oriented SHA PIPE centreline"
        )
    return inferred


def _segment_bbox_intersects(
    segment: tuple[float, float, float, float, int, int],
    bbox: tuple[int, int, int, int],
) -> bool:
    x1, y1, x2, y2, _, _ = segment
    left, bottom, right, top = bbox
    page_x1, page_y1 = x1 * SHEET_UNIT, y1 * SHEET_UNIT
    page_x2, page_y2 = x2 * SHEET_UNIT, y2 * SHEET_UNIT
    return not (
        max(page_x1, page_x2) < left
        or min(page_x1, page_x2) > right
        or max(page_y1, page_y2) < bottom
        or min(page_y1, page_y2) > top
    )


def sha_virtual_weld_points(
    sha_path: Path,
    welds: list[WeldBlock],
    native_points: dict[str, dict[str, object]],
    drawing_overrides: dict[str, dict[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    """Locate PCF-added welds on an existing SHA component centreline.

    The source SHA predates these welds and therefore has no weld ellipse/UCI.
    The augmentation metadata identifies the original owner component UCI and
    the 3D fraction along it.  Neighbor UCIs orient the corresponding 2D SHA
    segment without assuming a global 3D-to-2D projection.
    """

    drawing_overrides = drawing_overrides or {}
    synthetic = [
        weld
        for weld in welds
        if weld.augmentation_source == "PCF-AUGMENTED"
        and weld.owner_uci
        and weld.owner_fraction is not None
        and 0.0 <= weld.owner_fraction <= 1.0
    ]
    if not synthetic:
        return {}
    target_ucis = {
        value
        for weld in synthetic
        for value in (weld.owner_uci, weld.neighbor_uci_a, weld.neighbor_uci_b)
        if value
    }
    streams = read_sha_streams(sha_path)
    sheets = sheet_streams(streams, None)
    dynamic = forward_owned_dynamic_graphics(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    psm = streams.get("PSMcluster0", b"")
    style_data = streams.get("StyleCluster", b"")

    # A PIPE's dynamic UCI graphic is a component container.  Its own short
    # strokes are annotation/detail graphics; the plotted pipe centreline is
    # stored in a nearby parent group (the supplied SHA files consistently use
    # dynamic_ref - 3).  Retain line width and parent-group provenance so a
    # thin dimension line can never win merely because it crosses the UCI PSM
    # box.
    owner_refs_by_page: dict[int, dict[str, set[int]]] = {}
    region_centres: dict[str, list[tuple[int, float, float]]] = {
        uci: [] for uci in target_ucis
    }
    global_region_centres: dict[str, list[tuple[float, float]]] = {
        uci: [] for uci in target_ucis
    }
    for uci in target_ucis:
        for record in dynamic.get(uci, []):
            bbox = psm_bbox(psm, int(record["graphic_ref"]))
            if bbox is None:
                continue
            left, bottom, right, top = bbox
            center_x, center_y = (
                (left + right) / 2,
                (bottom + top) / 2,
            )
            if (
                -200.0 <= center_x <= 14500.0
                and -200.0 <= center_y <= 10200.0
            ):
                global_region_centres.setdefault(uci, []).append(
                    (center_x, center_y)
                )
    groups_by_page: dict[int, dict[int, list[dict[str, object]]]] = {}
    all_segments_by_page: dict[
        int, list[tuple[float, float, float, float, int, int]]
    ] = {}
    stream_by_page: dict[int, str] = {}
    for sheet in sheets:
        # Composite child bounds are inventory boxes, not visible line
        # endpoints; admitting their diagonals would recreate the old false
        # geometry problem.
        segments = line_segments(sheet.data) + template_line_segments(sheet.data)
        all_segments_by_page[sheet.page] = segments
        stream_by_page[sheet.page] = sheet.stream_name
        width_by_child = sheet_line_widths(sheet.data, style_data)
        width_by_child.update(template_line_widths(sheet.data, style_data))
        groups: dict[int, list[dict[str, object]]] = {}
        for x1, y1, x2, y2, ref, child_ref in segments:
            page_x1, page_y1 = x1 * SHEET_UNIT, y1 * SHEET_UNIT
            page_x2, page_y2 = x2 * SHEET_UNIT, y2 * SHEET_UNIT
            length = math.hypot(page_x2 - page_x1, page_y2 - page_y1)
            groups.setdefault(ref & 0xFFFF, []).append(
                {
                    "x1": page_x1,
                    "y1": page_y1,
                    "x2": page_x2,
                    "y2": page_y2,
                    "length": length,
                    "width": float(width_by_child.get(child_ref, 8.0)),
                    "child_ref": child_ref,
                }
            )
        groups_by_page[sheet.page] = groups

        page_refs: dict[str, set[int]] = {}
        for uci in target_ucis:
            for record in dynamic.get(uci, []):
                graphic_ref = int(record["graphic_ref"])
                dynamic_ref = graphic_ref & 0xFFFF
                exact_reference = (
                    struct.pack("<I", graphic_ref) in sheet.data
                )
                related_parent = any(
                    abs(
                        (
                            (parent_ref - dynamic_ref + 0x8000)
                            & 0xFFFF
                        )
                        - 0x8000
                    )
                    <= 12
                    for parent_ref in groups
                )
                if not exact_reference and not related_parent:
                    continue
                page_refs.setdefault(uci, set()).add(dynamic_ref)
                bbox = psm_bbox(psm, graphic_ref)
                if bbox is not None:
                    left, bottom, right, top = bbox
                    center_x, center_y = (
                        (left + right) / 2,
                        (bottom + top) / 2,
                    )
                    if (
                        -200.0 <= center_x <= 14500.0
                        and -200.0 <= center_y <= 10200.0
                    ):
                        region_centres.setdefault(uci, []).append(
                            (sheet.page, center_x, center_y)
                        )
        owner_refs_by_page[sheet.page] = page_refs

    def signed_ref_delta(parent_ref: int, dynamic_ref: int) -> int:
        return ((parent_ref - dynamic_ref + 0x8000) & 0xFFFF) - 0x8000

    def pipe_axis(
        group: list[dict[str, object]],
    ) -> dict[str, object] | None:
        usable = [
            segment for segment in group if float(segment["length"]) >= 4.0
        ]
        if not usable:
            return None
        max_width = max(float(segment["width"]) for segment in usable)
        width_tolerance = max(0.20, max_width * 0.04)
        thick = [
            segment
            for segment in usable
            if float(segment["width"]) >= max_width - width_tolerance
        ]
        if not thick:
            return None
        seed = max(thick, key=lambda segment: float(segment["length"]))
        seed_length = float(seed["length"])
        # EXISTING PIPE is commonly encoded as many 10–20 unit dash
        # primitives under the PIPE's own dynamic graphic.  One dash is not
        # a useful axis, but the collinear series below is direct SHA
        # centreline evidence and can span hundreds of page units.
        origin_x, origin_y = float(seed["x1"]), float(seed["y1"])
        ux = (float(seed["x2"]) - origin_x) / seed_length
        uy = (float(seed["y2"]) - origin_y) / seed_length
        aligned: list[dict[str, object]] = []
        for segment in thick:
            length = float(segment["length"])
            if length < 4.0:
                continue
            vx = (float(segment["x2"]) - float(segment["x1"])) / length
            vy = (float(segment["y2"]) - float(segment["y1"])) / length
            if abs(vx * ux + vy * uy) < 0.985:
                continue
            offsets = [
                abs((float(segment[key_x]) - origin_x) * uy - (float(segment[key_y]) - origin_y) * ux)
                for key_x, key_y in (("x1", "y1"), ("x2", "y2"))
            ]
            if max(offsets) > 80.0:
                continue
            aligned.append(segment)
        if not aligned:
            aligned = [seed]
        projections = [
            (float(segment[key_x]) - origin_x) * ux
            + (float(segment[key_y]) - origin_y) * uy
            for segment in aligned
            for key_x, key_y in (("x1", "y1"), ("x2", "y2"))
        ]
        low, high = min(projections), max(projections)
        if high - low < 80.0:
            return None
        return {
            "x1": origin_x + low * ux,
            "y1": origin_y + low * uy,
            "x2": origin_x + high * ux,
            "y2": origin_y + high * uy,
            "length": high - low,
            "width": max_width,
            "segments": aligned,
        }

    def endpoint_score(
        x: float,
        y: float,
        neighbor_uci: str,
        page: int,
    ) -> float:
        if not neighbor_uci:
            return math.inf
        native = native_points.get(neighbor_uci)
        if native is not None and int(native["page"]) == page:
            return math.hypot(
                x - float(native["sha_x"]),
                y - float(native["sha_y"]),
            )
        centres = [
            (center_x, center_y)
            for center_page, center_x, center_y in region_centres.get(
                neighbor_uci, []
            )
            if center_page == page
        ]
        if not centres:
            # A component container can be serialized only in PSM while its
            # page owns a nearby low-16-bit parent group.  The exact UCI PSM
            # envelope is still valid orientation evidence when compared to
            # an already page-owned PIPE axis; it is never used to discover
            # the axis itself.
            centres = global_region_centres.get(neighbor_uci, [])
        if not centres:
            return math.inf
        return min(math.hypot(x - cx, y - cy) for cx, cy in centres)

    def closest_point(
        x: float,
        y: float,
        segment: dict[str, object],
    ) -> tuple[float, float, float]:
        x1, y1 = float(segment["x1"]), float(segment["y1"])
        x2, y2 = float(segment["x2"]), float(segment["y2"])
        dx, dy = x2 - x1, y2 - y1
        denominator = dx * dx + dy * dy
        if denominator <= 1e-12:
            return x1, y1, math.hypot(x - x1, y - y1)
        ratio = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / denominator))
        px, py = x1 + ratio * dx, y1 + ratio * dy
        return px, py, math.hypot(x - px, y - py)

    def owner_axis_inventory(
        weld: WeldBlock,
    ) -> list[dict[str, object]]:
        inventory: list[dict[str, object]] = []
        for page, refs_by_uci in owner_refs_by_page.items():
            groups = groups_by_page.get(page, {})
            for dynamic_ref in refs_by_uci.get(weld.owner_uci, set()):
                for parent_ref, group in groups.items():
                    delta = signed_ref_delta(parent_ref, dynamic_ref)
                    if abs(delta) > 64:
                        continue
                    axis = pipe_axis(group)
                    if axis is None:
                        continue
                    axis.update(
                        {
                            "page": page,
                            "parent_ref": parent_ref,
                            "dynamic_ref": dynamic_ref,
                            "ref_delta": delta,
                        }
                    )
                    inventory.append(axis)
        return inventory

    # A tee UCI can be omitted while both adjoining PIPE UCIs and their
    # visible centreline groups remain.  The common endpoint of those two
    # independently owned axes is direct SHA junction evidence.  Record the
    # selected endpoint as an orientation hint; do not search by arbitrary
    # spatial proximity to the requested PCF coordinate.
    inventories = {
        weld.uci: owner_axis_inventory(weld) for weld in synthetic
    }
    missing_neighbor_groups: dict[
        str, list[tuple[WeldBlock, str]]
    ] = {}
    for weld in synthetic:
        for side, neighbor in (
            ("a", weld.neighbor_uci_a),
            ("b", weld.neighbor_uci_b),
        ):
            if not neighbor:
                continue
            if neighbor in native_points or region_centres.get(neighbor):
                continue
            missing_neighbor_groups.setdefault(neighbor, []).append(
                (weld, side)
            )

    junction_hints: dict[
        str, tuple[int, float, float, str]
    ] = {}
    preferred_axes: dict[str, tuple[int, int, int]] = {}
    for members in missing_neighbor_groups.values():
        if len(members) != 2:
            continue
        (first_weld, first_side), (second_weld, second_side) = members
        choices: list[
            tuple[
                tuple[float, ...],
                dict[str, object],
                tuple[float, float],
                dict[str, object],
                tuple[float, float],
            ]
        ] = []
        for first_axis in inventories.get(first_weld.uci, []):
            for second_axis in inventories.get(second_weld.uci, []):
                if int(first_axis["page"]) != int(second_axis["page"]):
                    continue
                for first_endpoint in (
                    (float(first_axis["x1"]), float(first_axis["y1"])),
                    (float(first_axis["x2"]), float(first_axis["y2"])),
                ):
                    for second_endpoint in (
                        (
                            float(second_axis["x1"]),
                            float(second_axis["y1"]),
                        ),
                        (
                            float(second_axis["x2"]),
                            float(second_axis["y2"]),
                        ),
                    ):
                        gap = math.dist(first_endpoint, second_endpoint)
                        rank = (
                            gap,
                            0.0
                            if int(first_axis["ref_delta"]) == -3
                            else 1.0,
                            0.0
                            if int(second_axis["ref_delta"]) == -3
                            else 1.0,
                            -float(first_axis["width"]),
                            -float(second_axis["width"]),
                        )
                        choices.append(
                            (
                                rank,
                                first_axis,
                                first_endpoint,
                                second_axis,
                                second_endpoint,
                            )
                        )
        if not choices:
            continue
        rank, first_axis, first_endpoint, second_axis, second_endpoint = min(
            choices, key=lambda choice: choice[0]
        )
        if rank[0] > 300.0:
            continue
        page = int(first_axis["page"])
        hint_x = (first_endpoint[0] + second_endpoint[0]) / 2
        hint_y = (first_endpoint[1] + second_endpoint[1]) / 2
        junction_hints[first_weld.uci] = (
            page,
            hint_x,
            hint_y,
            first_side,
        )
        junction_hints[second_weld.uci] = (
            page,
            hint_x,
            hint_y,
            second_side,
        )
        preferred_axes[first_weld.uci] = (
            page,
            int(first_axis["parent_ref"]),
            int(first_axis["dynamic_ref"]),
        )
        preferred_axes[second_weld.uci] = (
            page,
            int(second_axis["parent_ref"]),
            int(second_axis["dynamic_ref"]),
        )

    result: dict[str, dict[str, object]] = {}
    selected_axes: dict[str, dict[str, object]] = {}
    for weld in synthetic:
        drawing_override = drawing_overrides.get(weld.uci, {})
        effective_fraction = float(
            drawing_override.get("owner_fraction", weld.owner_fraction)
        )
        candidates: list[
            tuple[tuple[float, ...], dict[str, object], bool]
        ] = []
        for page, refs_by_uci in owner_refs_by_page.items():
            groups = groups_by_page.get(page, {})
            for dynamic_ref in refs_by_uci.get(weld.owner_uci, set()):
                for parent_ref, group in groups.items():
                    delta = signed_ref_delta(parent_ref, dynamic_ref)
                    preferred_axis = preferred_axes.get(weld.uci)
                    if (
                        abs(delta) > 12
                        and preferred_axis
                        != (page, parent_ref, dynamic_ref)
                    ):
                        continue
                    axis = pipe_axis(group)
                    if axis is None:
                        continue
                    x1, y1 = float(axis["x1"]), float(axis["y1"])
                    x2, y2 = float(axis["x2"]), float(axis["y2"])
                    forward_terms = [
                        endpoint_score(x1, y1, weld.neighbor_uci_a, page),
                        endpoint_score(x2, y2, weld.neighbor_uci_b, page),
                    ]
                    reverse_terms = [
                        endpoint_score(x2, y2, weld.neighbor_uci_a, page),
                        endpoint_score(x1, y1, weld.neighbor_uci_b, page),
                    ]
                    junction_hint = junction_hints.get(weld.uci)
                    if (
                        junction_hint is not None
                        and junction_hint[0] == page
                    ):
                        _, hint_x, hint_y, hint_side = junction_hint
                        if hint_side == "a":
                            forward_terms[0] = math.hypot(
                                x1 - hint_x, y1 - hint_y
                            )
                            reverse_terms[0] = math.hypot(
                                x2 - hint_x, y2 - hint_y
                            )
                        else:
                            forward_terms[1] = math.hypot(
                                x2 - hint_x, y2 - hint_y
                            )
                            reverse_terms[1] = math.hypot(
                                x1 - hint_x, y1 - hint_y
                            )
                    finite_forward = [value for value in forward_terms if math.isfinite(value)]
                    finite_reverse = [value for value in reverse_terms if math.isfinite(value)]
                    if not finite_forward and not finite_reverse:
                        if abs(effective_fraction - 0.5) > 1e-9:
                            continue
                        orientation_score = 1e12
                        forward = True
                        evidence_rank = 1.0
                    else:
                        forward_score = sum(finite_forward)
                        reverse_score = sum(finite_reverse)
                        orientation_score = min(forward_score, reverse_score)
                        forward = forward_score <= reverse_score
                        evidence_rank = 0.0
                    axis.update(
                        {
                            "page": page,
                            "parent_ref": parent_ref,
                            "dynamic_ref": dynamic_ref,
                            "ref_delta": delta,
                        }
                    )
                    rank = (
                        0.0
                        if preferred_axes.get(weld.uci)
                        == (page, parent_ref, dynamic_ref)
                        else 1.0,
                        0.0 if delta == -3 else 1.0,
                        evidence_rank,
                        orientation_score,
                        -float(axis["width"]),
                        -float(axis["length"]),
                        abs(float(delta + 3)),
                    )
                    candidates.append((rank, axis, forward))
        # Some older SHA exports omit the PIPE dynamic UCI but preserve both
        # adjacent component UCIs.  In that case a visible thick axis whose
        # two endpoints are jointly oriented by those component regions is
        # still a strict topology match.
        if (
            not candidates
            and weld.neighbor_uci_a
            and weld.neighbor_uci_b
        ):
            for page, groups in groups_by_page.items():
                for parent_ref, group in groups.items():
                    axis = pipe_axis(group)
                    if axis is None or float(axis["width"]) < 8.0:
                        continue
                    x1, y1 = float(axis["x1"]), float(axis["y1"])
                    x2, y2 = float(axis["x2"]), float(axis["y2"])
                    forward_terms = [
                        endpoint_score(
                            x1, y1, weld.neighbor_uci_a, page
                        ),
                        endpoint_score(
                            x2, y2, weld.neighbor_uci_b, page
                        ),
                    ]
                    reverse_terms = [
                        endpoint_score(
                            x2, y2, weld.neighbor_uci_a, page
                        ),
                        endpoint_score(
                            x1, y1, weld.neighbor_uci_b, page
                        ),
                    ]
                    if not (
                        all(math.isfinite(value) for value in forward_terms)
                        or all(
                            math.isfinite(value)
                            for value in reverse_terms
                        )
                    ):
                        continue
                    forward_score = sum(forward_terms)
                    reverse_score = sum(reverse_terms)
                    forward = forward_score <= reverse_score
                    oriented_terms = (
                        forward_terms if forward else reverse_terms
                    )
                    if max(oriented_terms) > 1000.0:
                        continue
                    axis.update(
                        {
                            "page": page,
                            "parent_ref": parent_ref,
                            "dynamic_ref": parent_ref,
                            "ref_delta": 0,
                            "ownerless_topology_axis": True,
                        }
                    )
                    rank = (
                        2.0,
                        1.0,
                        0.0,
                        min(forward_score, reverse_score),
                        -float(axis["width"]),
                        -float(axis["length"]),
                        0.0,
                    )
                    candidates.append((rank, axis, forward))
        if not candidates:
            continue
        _, axis, forward = min(candidates, key=lambda item: item[0])
        selected_axes[weld.uci] = axis
        if forward:
            ax, ay = float(axis["x1"]), float(axis["y1"])
            bx, by = float(axis["x2"]), float(axis["y2"])
        else:
            ax, ay = float(axis["x2"]), float(axis["y2"])
            bx, by = float(axis["x1"]), float(axis["y1"])
        fraction = effective_fraction
        interpolated_x = ax + fraction * (bx - ax)
        interpolated_y = ay + fraction * (by - ay)
        snap_x, snap_y, pipe_distance, snap_segment = min(
            (
                (*closest_point(interpolated_x, interpolated_y, segment), segment)
                for segment in axis["segments"]
            ),
            key=lambda item: item[2],
        )
        # A flow arrow or support can interrupt the visible stroke.  Snap the
        # virtual weld to the nearest thick centreline primitive so the leader
        # always starts on ink, while retaining the unsnapped engineering
        # fraction in the audit fields below.
        sha_x, sha_y = snap_x, snap_y
        page = int(axis["page"])
        all_segments = all_segments_by_page[page]
        distance = min(
            point_segment_distance(
                sha_x,
                sha_y,
                x1 * SHEET_UNIT,
                y1 * SHEET_UNIT,
                x2 * SHEET_UNIT,
                y2 * SHEET_UNIT,
            )
            for x1, y1, x2, y2, _, _ in all_segments
        )
        result[weld.uci] = {
            "uci": weld.uci,
            "graphic_ref": 0xE0000000
            | (zlib.crc32(weld.uci.encode("ascii")) & 0x0FFFFFFF),
            "page": page,
            "sheet_stream": stream_by_page[page],
            "sha_x": round(sha_x, 3),
            "sha_y": round(sha_y, 3),
            "distance_to_geometry": round(distance, 3),
            "distance_to_pipe_centerline": 0.0,
            "point_evidence": "pcf-pipe-interpolation",
            "owner_uci": weld.owner_uci,
            "owner_fraction": weld.owner_fraction,
            "drawing_owner_fraction": fraction,
            "drawing_distribution_mode": str(
                drawing_override.get("mode", "")
            ),
            "drawing_distribution_group": str(
                drawing_override.get("group_id", "")
            ),
            "drawing_distribution_rank": drawing_override.get("rank"),
            "drawing_distribution_count": drawing_override.get("count"),
            "drawing_target_3d": drawing_override.get("target_3d"),
            "orientation_neighbors": [
                weld.neighbor_uci_a,
                weld.neighbor_uci_b,
            ],
            "pipe_axis": [
                [round(ax, 3), round(ay, 3)],
                [round(bx, 3), round(by, 3)],
            ],
            "pipe_visible_segment": [
                [
                    round(float(snap_segment["x1"]), 3),
                    round(float(snap_segment["y1"]), 3),
                ],
                [
                    round(float(snap_segment["x2"]), 3),
                    round(float(snap_segment["y2"]), 3),
                ],
            ],
            "pipe_graphic_ref_low16": f"0x{int(axis['parent_ref']):04X}",
            "pipe_dynamic_ref_low16": f"0x{int(axis['dynamic_ref']):04X}",
            "pipe_ref_delta": int(axis["ref_delta"]),
            "pipe_stroke_width_page_units": round(float(axis["width"]), 4),
            "unsnapped_fraction_point": [
                round(interpolated_x, 3),
                round(interpolated_y, 3),
            ],
            "snap_distance_to_visible_pipe": round(pipe_distance, 3),
        }

    # A long straight run may be split into multiple PIPE graphics and pages.
    # The SHA group axes are undirected, while a large neighboring component
    # bbox can make endpoint-UCI scoring choose the reverse direction.  Orient
    # each same-page chain by minimizing the gap at its actual pipe-to-pipe
    # joins.  This keeps consecutive equal-distribution stations consecutive
    # on the plotted centreline without changing any PCF coordinate.
    distribution_groups: dict[str, list[WeldBlock]] = {}
    for weld in synthetic:
        group_id = str(
            drawing_overrides.get(weld.uci, {}).get("group_id", "")
        )
        if group_id and weld.uci in result:
            distribution_groups.setdefault(group_id, []).append(weld)

    for group_welds in distribution_groups.values():
        owners: dict[str, dict[str, object]] = {}
        for weld in group_welds:
            override = drawing_overrides[weld.uci]
            axis = selected_axes[weld.uci]
            owners.setdefault(
                weld.owner_uci,
                {
                    "order": float(override["owner_run_order"]),
                    "forward_with_run": bool(
                        override["owner_forward_with_run"]
                    ),
                    "axis": axis,
                    "welds": [],
                },
            )["welds"].append(weld)
        ordered_owners = sorted(
            owners.values(), key=lambda owner: float(owner["order"])
        )

        # Solve each page-local consecutive subchain independently.  A
        # one-pipe page has no join evidence, so retain its earlier
        # neighbor-based orientation.
        chain_start = 0
        while chain_start < len(ordered_owners):
            page = int(ordered_owners[chain_start]["axis"]["page"])
            chain_end = chain_start + 1
            while (
                chain_end < len(ordered_owners)
                and int(ordered_owners[chain_end]["axis"]["page"]) == page
            ):
                chain_end += 1
            chain = ordered_owners[chain_start:chain_end]
            if len(chain) >= 2:
                best_orientations: tuple[bool, ...] | None = None
                best_gap = math.inf
                for mask in range(1 << len(chain)):
                    orientations = tuple(
                        bool(mask & (1 << index))
                        for index in range(len(chain))
                    )
                    gap = 0.0
                    for index in range(len(chain) - 1):
                        first_axis = chain[index]["axis"]
                        second_axis = chain[index + 1]["axis"]
                        first_forward = orientations[index]
                        second_forward = orientations[index + 1]
                        first_end = (
                            (
                                float(first_axis["x2"]),
                                float(first_axis["y2"]),
                            )
                            if first_forward
                            else (
                                float(first_axis["x1"]),
                                float(first_axis["y1"]),
                            )
                        )
                        second_start = (
                            (
                                float(second_axis["x1"]),
                                float(second_axis["y1"]),
                            )
                            if second_forward
                            else (
                                float(second_axis["x2"]),
                                float(second_axis["y2"]),
                            )
                        )
                        gap += math.dist(first_end, second_start)
                    if gap < best_gap:
                        best_gap = gap
                        best_orientations = orientations

                assert best_orientations is not None
                for owner, axis_forward_with_run in zip(
                    chain, best_orientations
                ):
                    axis = owner["axis"]
                    if axis_forward_with_run:
                        run_ax, run_ay = (
                            float(axis["x1"]),
                            float(axis["y1"]),
                        )
                        run_bx, run_by = (
                            float(axis["x2"]),
                            float(axis["y2"]),
                        )
                    else:
                        run_ax, run_ay = (
                            float(axis["x2"]),
                            float(axis["y2"]),
                        )
                        run_bx, run_by = (
                            float(axis["x1"]),
                            float(axis["y1"]),
                        )
                    for weld in owner["welds"]:
                        row = result[weld.uci]
                        fraction = float(row["drawing_owner_fraction"])
                        run_fraction = (
                            fraction
                            if owner["forward_with_run"]
                            else 1.0 - fraction
                        )
                        interpolated_x = run_ax + run_fraction * (
                            run_bx - run_ax
                        )
                        interpolated_y = run_ay + run_fraction * (
                            run_by - run_ay
                        )
                        snap_x, snap_y, pipe_distance, snap_segment = min(
                            (
                                (
                                    *closest_point(
                                        interpolated_x,
                                        interpolated_y,
                                        segment,
                                    ),
                                    segment,
                                )
                                for segment in axis["segments"]
                            ),
                            key=lambda item: item[2],
                        )
                        row["sha_x"] = round(snap_x, 3)
                        row["sha_y"] = round(snap_y, 3)
                        row["pipe_axis"] = [
                            [round(run_ax, 3), round(run_ay, 3)],
                            [round(run_bx, 3), round(run_by, 3)],
                        ]
                        row["pipe_visible_segment"] = [
                            [
                                round(float(snap_segment["x1"]), 3),
                                round(float(snap_segment["y1"]), 3),
                            ],
                            [
                                round(float(snap_segment["x2"]), 3),
                                round(float(snap_segment["y2"]), 3),
                            ],
                        ]
                        row["unsnapped_fraction_point"] = [
                            round(interpolated_x, 3),
                            round(interpolated_y, 3),
                        ]
                        row["snap_distance_to_visible_pipe"] = round(
                            pipe_distance, 3
                        )
                        row["drawing_axis_orientation"] = (
                            "SAME-PAGE-PIPE-CONTINUITY"
                        )
                        row["drawing_axis_join_gap"] = round(best_gap, 3)
            chain_start = chain_end
    return result


def number_welds(welds: list[WeldBlock], sha_points: dict[str, dict[str, object]], prefix: str, use_repeat_id: bool) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for weld in welds:
        sha = sha_points.get(weld.uci)
        endpoint = weld.endpoint or (math.inf, math.inf, math.inf, math.inf)
        category = weld.category.upper()
        skey = weld.skey.upper()
        if category == "FABRICATION":
            weld_type, weld_type_source = "S", "CATEGORY FABRICATION"
        elif category == "ERECTION":
            weld_type, weld_type_source = "F", "CATEGORY ERECTION"
        elif skey in {"WW", "WM"}:
            weld_type, weld_type_source = "S", f"SKEY {weld.skey}"
        elif skey in {"WS", "WMS", "WF", "WO"}:
            weld_type, weld_type_source = "F", f"SKEY {weld.skey}"
        else:
            weld_type, weld_type_source = "", "UNRESOLVED"
        rows.append(
            {
                "uci": weld.uci,
                "component_identifier": weld.component_identifier,
                "master_component_identifier": weld.master_component_identifier,
                "skey": weld.skey,
                "category": weld.category,
                "weld_type": weld_type,
                "weld_type_source": weld_type_source,
                "description": weld.description,
                "pcf_endpoint": list(endpoint),
                "page": sha["page"] if sha else 999999,
                # Retain the exact SHA spatial graphic selected for this PCF
                # weld.  UCI alone can legitimately recur on another ISO page.
                "graphic_ref": sha["graphic_ref"] if sha else None,
                "sha_x": sha["sha_x"] if sha else math.inf,
                "sha_y": sha["sha_y"] if sha else math.inf,
                "sheet_stream": sha["sheet_stream"] if sha else "",
                "distance_to_geometry": sha["distance_to_geometry"] if sha else None,
                "distance_to_pipe_centerline": (
                    sha.get("distance_to_pipe_centerline") if sha else None
                ),
                "point_evidence": sha["point_evidence"] if sha else "",
                "pipe_axis": sha.get("pipe_axis") if sha else None,
                "pipe_visible_segment": (
                    sha.get("pipe_visible_segment") if sha else None
                ),
                "pipe_graphic_ref_low16": (
                    sha.get("pipe_graphic_ref_low16") if sha else ""
                ),
                "pipe_dynamic_ref_low16": (
                    sha.get("pipe_dynamic_ref_low16") if sha else ""
                ),
                "pipe_ref_delta": sha.get("pipe_ref_delta") if sha else None,
                "pipe_stroke_width_page_units": (
                    sha.get("pipe_stroke_width_page_units") if sha else None
                ),
                "unsnapped_fraction_point": (
                    sha.get("unsnapped_fraction_point") if sha else None
                ),
                "snap_distance_to_visible_pipe": (
                    sha.get("snap_distance_to_visible_pipe") if sha else None
                ),
                "augmentation_source": weld.augmentation_source,
                "augmentation_mode": weld.augmentation_mode,
                "owner_uci": weld.owner_uci,
                "owner_fraction": weld.owner_fraction,
                "drawing_owner_fraction": (
                    sha.get("drawing_owner_fraction") if sha else None
                ),
                "drawing_distribution_mode": (
                    sha.get("drawing_distribution_mode") if sha else ""
                ),
                "drawing_distribution_group": (
                    sha.get("drawing_distribution_group") if sha else ""
                ),
                "drawing_distribution_rank": (
                    sha.get("drawing_distribution_rank") if sha else None
                ),
                "drawing_distribution_count": (
                    sha.get("drawing_distribution_count") if sha else None
                ),
                "drawing_target_3d": (
                    sha.get("drawing_target_3d") if sha else None
                ),
                "drawing_axis_orientation": (
                    sha.get("drawing_axis_orientation") if sha else ""
                ),
                "drawing_axis_join_gap": (
                    sha.get("drawing_axis_join_gap") if sha else None
                ),
                "neighbor_uci_a": weld.neighbor_uci_a,
                "neighbor_uci_b": weld.neighbor_uci_b,
                "repeat_weld_identifier": weld.repeat_weld_identifier,
            }
        )

    rows.sort(
        key=lambda item: (
            int(item["page"]),
            float(item["sha_y"]) if math.isfinite(float(item["sha_y"])) else math.inf,
            float(item["sha_x"]) if math.isfinite(float(item["sha_x"])) else math.inf,
            float(item["pcf_endpoint"][2]) if item["pcf_endpoint"] else math.inf,
            float(item["pcf_endpoint"][0]) if item["pcf_endpoint"] else math.inf,
            float(item["pcf_endpoint"][1]) if item["pcf_endpoint"] else math.inf,
            item["uci"],
        )
    )
    for index, row in enumerate(rows, start=1):
        repeat_id = str(row["repeat_weld_identifier"])
        row["weld_number"] = f"{prefix}{repeat_id}" if use_repeat_id and repeat_id and repeat_id != "0" else f"{prefix}{index:03d}"
    return rows


def write_numbered_pcf(source_lines: list[str], welds: list[WeldBlock], numbered: list[dict[str, object]], output_path: Path) -> None:
    weld_number_by_uci = {str(row["uci"]): str(row["weld_number"]) for row in numbered}
    result = list(source_lines)
    for weld in welds:
        weld_number = weld_number_by_uci[weld.uci]
        for index in range(weld.start, weld.end):
            if result[index].strip().startswith("WELD-REMARK-NUMBER"):
                result[index] = f"    WELD-REMARK-NUMBER    {weld_number}"
                break
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(result) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcf", type=Path, help="Source PCF file.")
    parser.add_argument("sha", type=Path, help="Matching SHA file from the same ISO.")
    parser.add_argument("--output-pcf", type=Path, required=True, help="Numbered PCF copy to write.")
    parser.add_argument("--output-map", type=Path, required=True, help="JSON weld map to write.")
    parser.add_argument("--prefix", default="S", help="Weld number prefix. Default: S")
    parser.add_argument("--use-repeat-id", action="store_true", help="Use existing REPEAT-WELD-IDENTIFIER values instead of renumbering.")
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=80.0,
        help="Maximum SHA point-to-geometry distance used when deciding whether a weld dot is visible on a page.",
    )
    args = parser.parse_args()

    source_lines, welds = parse_weld_blocks(args.pcf)
    drawing_overrides = long_pipe_drawing_overrides(
        welds, parse_pipe_components(source_lines)
    )
    sha_points = sha_weld_points(args.sha, args.distance_threshold)
    sha_points.update(
        sha_virtual_weld_points(
            args.sha, welds, sha_points, drawing_overrides
        )
    )
    sha_points.update(
        sha_implicit_pipe_end_weld_points(
            args.sha, source_lines, welds, sha_points
        )
    )
    numbered = number_welds(welds, sha_points, args.prefix, args.use_repeat_id)
    write_numbered_pcf(source_lines, welds, numbered, args.output_pcf)

    payload = {
        "pcf": str(args.pcf),
        "sha": str(args.sha),
        "prefix": args.prefix,
        "weld_count": len(numbered),
        "welds": numbered,
    }
    args.output_map.parent.mkdir(parents=True, exist_ok=True)
    args.output_map.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")

    mapped = sum(1 for row in numbered if row["page"] != 999999)
    print(f"Welds numbered: {len(numbered)}")
    print(f"Welds mapped to SHA page points: {mapped}")
    print(f"PCF copy: {args.output_pcf}")
    print(f"Weld map: {args.output_map}")


if __name__ == "__main__":
    main()
