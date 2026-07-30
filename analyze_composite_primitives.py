#!/usr/bin/env python3
"""Audit Shape2D composite-child patterns without inventing geometry.

The current SHA renderer has direct evidence for composite child type 5
(two-point line) and type 6 (arc bounds). Type 0 is deliberately not
rendered; types 2, 10, 11, 16 and 21 are aliases of independently decoded
line/arc primitives. This tool groups every composite parent by its ordered child
type signature and records only SHA-derived relationships: bounding-box
overlap, endpoint equality, and numeric links to the validated 0x8000 PSM
node table.  It is intended to find a repeatable subtype pattern before any
new renderer rule is proposed.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from collections import Counter
from pathlib import Path

from analyze_iso_split import read_sha_streams
from analyze_psm_hierarchy import (
    parse_18_32_layer_bindings,
    parse_4d_text_layer_bindings,
    parse_59_2b_page_layer_bindings,
    parse_61_pipe_arc_records,
    parse_tseg_nodes,
)


CHILD_SIZE = 14
CHILD_START = 34


def parse_composites(data: bytes) -> list[dict[str, object]]:
    """Return structurally bounded ``0x7B`` composite records from one Sheet."""

    result: list[dict[str, object]] = []
    # Shape2D child fields are uint16-aligned.  Searching every byte admits
    # false ``0x7B 0x00`` signatures from opaque payloads and corrupts the
    # primitive inventory before any semantic analysis begins.
    for start in range(0, len(data) - CHILD_START, 2):
        if data[start : start + 2] != b"\x7b\x00":
            continue
        if start + CHILD_START > len(data):
            continue
        record_length = struct.unpack_from("<I", data, start + 2)[0]
        child_count = struct.unpack_from("<I", data, start + 22)[0]
        end = start + CHILD_START + child_count * CHILD_SIZE
        if (
            not 1 <= child_count <= 100
            or record_length != 32 + child_count * CHILD_SIZE
            or end > len(data)
            or start + 6 + record_length > len(data)
        ):
            continue
        # The record length is at +2. The actual composite object reference
        # begins at +6, and is the field reused by text-group secondary refs.
        parent_ref = struct.unpack_from("<I", data, start + 6)[0]
        sheet_ref = struct.unpack_from("<I", data, start + 10)[0]
        children: list[dict[str, int]] = []
        for index in range(child_count):
            child_ref, left, bottom, right, top, primitive_type = struct.unpack_from(
                "<I5H", data, start + CHILD_START + index * CHILD_SIZE
            )
            children.append(
                {
                    "ref": child_ref,
                    "left": left,
                    "bottom": bottom,
                    "right": right,
                    "top": top,
                    "type": primitive_type,
                }
            )
        result.append(
            {
                "offset": start,
                "record_length": record_length,
                "parent_ref": parent_ref,
                # Compatibility name for existing type-2/11/16 evidence:
                # this is the same composite object reference, not +2 length.
                "linked_ref": parent_ref,
                "sheet_ref": sheet_ref,
                "children": children,
            }
        )
    return result


def intersects(first: dict[str, int], second: dict[str, int]) -> bool:
    """Return whether child coordinate envelopes overlap in composite space."""

    return not (
        first["right"] < second["left"]
        or second["right"] < first["left"]
        or first["top"] < second["bottom"]
        or second["top"] < first["bottom"]
    )


def same_endpoint(first: dict[str, int], second: dict[str, int]) -> bool:
    """Check endpoint equality without assuming either child is a line."""

    first_points = {(first["left"], first["bottom"]), (first["right"], first["top"])}
    second_points = {(second["left"], second["bottom"]), (second["right"], second["top"])}
    return bool(first_points & second_points)


def same_segment(first: dict[str, int], second: dict[str, int]) -> bool:
    """Return true only when two children carry the same unordered endpoints."""

    first_points = {(first["left"], first["bottom"]), (first["right"], first["top"])}
    second_points = {(second["left"], second["bottom"]), (second["right"], second["top"])}
    return first_points == second_points


def equals_sibling_union(child: dict[str, int], siblings: list[dict[str, int]]) -> bool:
    """Check whether a child is exactly the bounding range of its siblings."""

    if not siblings:
        return False
    return (
        child["left"] == min(sibling["left"] for sibling in siblings)
        and child["bottom"] == min(sibling["bottom"] for sibling in siblings)
        and child["right"] == max(sibling["right"] for sibling in siblings)
        and child["top"] == max(sibling["top"] for sibling in siblings)
    )


def type_zero_has_contiguous_sibling_range(
    child: dict[str, int], children: list[dict[str, int]], child_index: int
) -> bool:
    """Return whether a type-0 child bounds any contiguous sibling range.

    Composite range headers can bound a subset of their siblings rather than
    the entire record. This tests only exact double-page-unit equality and
    does not create a new visible primitive from the header.
    """

    for start in range(len(children)):
        for end in range(start + 1, len(children) + 1):
            if start <= child_index < end:
                continue
            if equals_sibling_union(child, children[start:end]):
                return True
    return False


def sheet_summary(
    sheet_name: str,
    data: bytes,
    psm_node_ids: set[int],
) -> dict[str, object]:
    """Summarize one Sheet while keeping all relationships evidence-only."""

    composites = parse_composites(data)
    # Keep all structurally valid 18/32 records, including short strokes and
    # zero-length point records. The renderer filters those separately, but
    # composite children use them as auxiliary metadata targets.
    alternate_by_child = {
        int(record["child_ref"]): int(record["graphic_ref"])
        for record in parse_18_32_layer_bindings(data)
    }
    pipe_arcs_by_primitive = {
        int(record["primitive_ref"]): int(record["graphic_ref"])
        for record in parse_61_pipe_arc_records(data)
    }
    circles_by_primitive = {
        int(record["primitive_ref"]): int(record["graphic_ref"])
        for record in parse_59_2b_page_layer_bindings(data)
    }
    text_by_child_ref = {
        int(record["child_ref"]): record for record in parse_4d_text_layer_bindings(data)
    }
    text_children_by_secondary_ref: dict[int, set[int]] = {}
    for text in text_by_child_ref.values():
        text_children_by_secondary_ref.setdefault(int(text["secondary_ref"]), set()).add(
            int(text["child_ref"])
        )
    child_types: Counter[int] = Counter()
    signature_counts: Counter[str] = Counter()
    pair_overlap: Counter[str] = Counter()
    pair_endpoint: Counter[str] = Counter()
    pair_same_segment: Counter[str] = Counter()
    unknown_bounds = Counter()
    psm_ref_types: Counter[int] = Counter()
    auxiliary_alternate = Counter()
    type_zero_roles = Counter()
    unknown_examples: list[dict[str, object]] = []

    for composite in composites:
        children = composite["children"]
        assert isinstance(children, list)
        types = [int(child["type"]) for child in children]
        child_types.update(types)
        signature_counts["-".join(str(value) for value in types)] += 1
        for index, child in enumerate(children):
            child_type = int(child["type"])
            if child_type == 0:
                parent_text_children = text_children_by_secondary_ref.get(
                    int(composite["parent_ref"]), set()
                )
                if int(child["ref"]) in parent_text_children:
                    type_zero_roles["text-group-range-companion"] += 1
                elif type_zero_has_contiguous_sibling_range(child, children, index):
                    type_zero_roles["contiguous-sibling-range-header"] += 1
                else:
                    type_zero_roles["unclassified"] += 1
            if child_type in {2, 10, 11, 16, 21}:
                alternate = alternate_by_child.get(int(child["ref"]))
                if alternate is not None:
                    auxiliary_alternate[f"type_{child_type}_child_ref_matches_18_32"] += 1
                    if int(alternate) == int(composite["linked_ref"]):
                        auxiliary_alternate[f"type_{child_type}_linked_ref_matches_18_32_object_ref"] += 1
                elif child_type in {2, 10, 11, 16, 21} and int(child["ref"]) in pipe_arcs_by_primitive:
                    auxiliary_alternate[f"type_{child_type}_child_ref_matches_61_pipe_arc"] += 1
                    if (
                        pipe_arcs_by_primitive[int(child["ref"])]
                        == int(composite["linked_ref"])
                    ):
                        auxiliary_alternate[
                            f"type_{child_type}_linked_ref_matches_61_pipe_arc_graphic_ref"
                        ] += 1
                elif child_type == 2 and int(child["ref"]) in circles_by_primitive:
                    auxiliary_alternate["type_2_child_ref_matches_59_circle"] += 1
                    if circles_by_primitive[int(child["ref"])] == int(composite["linked_ref"]):
                        auxiliary_alternate[
                            "type_2_linked_ref_matches_59_circle_graphic_ref"
                        ] += 1
                else:
                    auxiliary_alternate[f"type_{child_type}_no_18_32_child_ref_match"] += 1
            if int(child["ref"]) in psm_node_ids:
                psm_ref_types[child_type] += 1
            if child_type not in {0, 10, 11, 16, 21}:
                continue
            siblings = [sibling for sibling in children if sibling is not child]
            if equals_sibling_union(child, siblings):
                unknown_bounds[str(child_type)] += 1
            overlap_types = sorted({int(sibling["type"]) for sibling in siblings if intersects(child, sibling)})
            endpoint_types = sorted({int(sibling["type"]) for sibling in siblings if same_endpoint(child, sibling)})
            for sibling_type in overlap_types:
                pair_overlap[f"{child_type}->{sibling_type}"] += 1
            for sibling_type in endpoint_types:
                pair_endpoint[f"{child_type}->{sibling_type}"] += 1
            for sibling in siblings:
                if same_segment(child, sibling):
                    pair_same_segment[f"{child_type}->{int(sibling['type'])}"] += 1
            if len(unknown_examples) < 12:
                unknown_examples.append(
                    {
                        "parent_ref": f"0x{int(composite['parent_ref']):08X}",
                        "linked_ref": f"0x{int(composite['linked_ref']):08X}",
                        "child_index": index,
                        "child_ref": f"0x{int(child['ref']):08X}",
                        "type": child_type,
                        "bbox_double_page_units": [
                            int(child["left"]), int(child["bottom"]), int(child["right"]), int(child["top"])
                        ],
                        "sibling_types": types,
                        "overlaps_types": overlap_types,
                        "shares_endpoint_with_types": endpoint_types,
                        "same_segment_as_sibling_types": sorted(
                            {int(sibling["type"]) for sibling in siblings if same_segment(child, sibling)}
                        ),
                        "equals_union_bbox_of_all_siblings": equals_sibling_union(child, siblings),
                        "numeric_id_in_validated_psm_0x8000": int(child["ref"]) in psm_node_ids,
                    }
                )
    return {
        "sheet": sheet_name,
        "composite_parent_count": len(composites),
        "child_type_counts": {str(key): value for key, value in sorted(child_types.items())},
        "ordered_child_type_signatures": dict(signature_counts.most_common(30)),
        "unknown_child_overlap_pairs": dict(sorted(pair_overlap.items())),
        "unknown_child_shared_endpoint_pairs": dict(sorted(pair_endpoint.items())),
        "unknown_child_identical_endpoint_pairs": dict(sorted(pair_same_segment.items())),
        "unknown_children_equal_to_sibling_union_bbox": dict(sorted(unknown_bounds.items())),
        "child_refs_matching_validated_psm_node_ids": {str(key): value for key, value in sorted(psm_ref_types.items())},
        "auxiliary_types_to_18_32_reference_links": dict(sorted(auxiliary_alternate.items())),
        "type_0_role_counts": dict(sorted(type_zero_roles.items())),
        "unknown_child_examples": unknown_examples,
    }


def analyze(sha_path: Path) -> dict[str, object]:
    streams = read_sha_streams(sha_path)
    hierarchy_data = streams.get("PSMspacemap/0x00008000")
    psm_node_ids: set[int] = set()
    hierarchy_notice = "PSM 0x8000 absent; no numeric node cross-check was performed."
    if hierarchy_data is not None:
        try:
            hierarchy = parse_tseg_nodes(hierarchy_data)
            psm_node_ids = {int(node["id"]) for node in hierarchy["nodes"]}
            hierarchy_notice = (
                "Numeric child-ref to PSM node-id matches are inventory evidence only; "
                "they do not prove a parent-child geometry relation."
            )
        except ValueError as error:
            hierarchy_notice = f"PSM 0x8000 has an unvalidated variant: {error}"
    sheets = {
        name: data
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name) and len(data) > 1024
    }
    page_reports = [sheet_summary(name, sheets[name], psm_node_ids) for name in sorted(sheets)]
    total_types: Counter[str] = Counter()
    total_overlap: Counter[str] = Counter()
    total_endpoint: Counter[str] = Counter()
    total_same_segment: Counter[str] = Counter()
    total_bounds: Counter[str] = Counter()
    total_psm: Counter[str] = Counter()
    total_auxiliary_alternate: Counter[str] = Counter()
    total_type_zero_roles: Counter[str] = Counter()
    for page in page_reports:
        total_types.update(page["child_type_counts"])
        total_overlap.update(page["unknown_child_overlap_pairs"])
        total_endpoint.update(page["unknown_child_shared_endpoint_pairs"])
        total_same_segment.update(page["unknown_child_identical_endpoint_pairs"])
        total_bounds.update(page["unknown_children_equal_to_sibling_union_bbox"])
        total_psm.update(page["child_refs_matching_validated_psm_node_ids"])
        total_auxiliary_alternate.update(page["auxiliary_types_to_18_32_reference_links"])
        total_type_zero_roles.update(page["type_0_role_counts"])
    return {
        "source_sha": str(sha_path),
        "scope": (
            "SHA-only composite pattern inventory. This report neither assigns a Shape2D semantic "
            "to child types 0/11/16 nor emits geometry from them."
        ),
        "psm_cross_check_notice": hierarchy_notice,
        "totals": {
            "child_type_counts": dict(sorted(total_types.items())),
            "unknown_child_overlap_pairs": dict(sorted(total_overlap.items())),
            "unknown_child_shared_endpoint_pairs": dict(sorted(total_endpoint.items())),
            "unknown_child_identical_endpoint_pairs": dict(sorted(total_same_segment.items())),
            "unknown_children_equal_to_sibling_union_bbox": dict(sorted(total_bounds.items())),
            "child_refs_matching_validated_psm_node_ids": dict(sorted(total_psm.items())),
            "auxiliary_types_to_18_32_reference_links": dict(sorted(total_auxiliary_alternate.items())),
            "type_0_role_counts": dict(sorted(total_type_zero_roles.items())),
        },
        "sheets": page_reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
