#!/usr/bin/env python3
"""Recover conservative PSM-to-physical-Sheet links from SHA evidence.

Only a complete chain is promoted to ``direct``:
dynamic graphic_ref -> validated PSM 0x8000 node id -> node child ref ->
decoded primitive child ref in the physical Sheet selected by its local-id
interval.  Header-range membership alone remains a candidate and is reported
separately.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path

from analyze_composite_primitives import parse_composites
from analyze_iso_split import read_sha_streams
from analyze_psm_hierarchy import (
    bounded_dynamic_graphics_by_uci,
    parse_61_pipe_arc_records,
    parse_dynamic_attribute_property_records,
    parse_tseg_nodes,
)
from sha_to_svg_prototype import (
    composite_segments,
    composite_visible_children_by_type0,
    line_segments,
    template_line_segments,
)


def sheet_headers(sheets: dict[str, bytes]) -> list[tuple[int, str]]:
    """Read the observed physical-Sheet local-id interval starts at byte 14."""

    values = [
        (struct.unpack_from("<I", data, 14)[0], name)
        for name, data in sheets.items()
        if len(data) >= 18
    ]
    return sorted(values)


def sheet_for_local_ref(local_ref: int, starts: list[tuple[int, str]]) -> str | None:
    """Resolve a local ref to its preceding physical Sheet interval start."""

    owner = None
    for start, name in starts:
        if start > local_ref:
            break
        owner = name
    return owner


def decoded_child_ref_families(data: bytes) -> dict[int, set[str]]:
    """Return decoded primitive families keyed by exact Sheet child reference.

    A PSM relation can hit a non-drawing composite range child.  Keeping the
    family alongside the exact numeric match prevents that linkage from being
    mistaken for a visible segment merely because it is structurally direct.
    """

    refs: dict[int, set[str]] = defaultdict(set)
    for *_, child_ref in line_segments(data):
        refs[int(child_ref)].add("ordinary-sheet-line")
    for *_, child_ref in template_line_segments(data):
        refs[int(child_ref)].add("18-32-line")
    for *_, child_ref in composite_segments(data):
        refs[int(child_ref)].add("composite-type-5-visible-segment")
    for composite in parse_composites(data):
        for child in composite["children"]:
            child_type = int(child["type"])
            refs[int(child["ref"])].add(f"composite-type-{child_type}")
    for arc in parse_61_pipe_arc_records(data):
        refs[int(arc["primitive_ref"])].add("61-pipe-arc")
    return refs


def analyze(sha_path: Path) -> dict[str, object]:
    streams = read_sha_streams(sha_path)
    sheets = {
        name: data
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name) and len(data) > 1024
    }
    hierarchy_data = streams.get("PSMspacemap/0x00008000")
    if hierarchy_data is None:
        return {
            "source_sha": str(sha_path),
            "scope": "SHA-only PSM hierarchy audit.",
            "status": "not-applicable-0x8000-spacemap-absent",
            "direct_link_count": 0,
            "candidate_interval_link_count": 0,
            "relation_statistics": {},
            "direct_link_primitive_family_counts": {},
            "direct_type0_visible_sibling_count": 0,
            "direct_links": [],
            "candidate_interval_links": [],
        }
    try:
        hierarchy = parse_tseg_nodes(hierarchy_data)
    except ValueError as error:
        return {
            "source_sha": str(sha_path),
            "scope": "SHA-only PSM hierarchy audit.",
            "status": "not-applicable-0x8000-spacemap-layout-unvalidated",
            "parse_error": str(error),
            "direct_link_count": 0,
            "candidate_interval_link_count": 0,
            "relation_statistics": {},
            "direct_link_primitive_family_counts": {},
            "direct_type0_visible_sibling_count": 0,
            "direct_links": [],
            "candidate_interval_links": [],
        }
    nodes = {int(node["id"]): node for node in hierarchy["nodes"]}
    starts = sheet_headers(sheets)
    refs_by_sheet = {name: decoded_child_ref_families(data) for name, data in sheets.items()}
    direct: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    relation_stats: Counter[str] = Counter()
    direct_family_counts: Counter[str] = Counter()
    direct_type0_visible_sibling_count = 0

    dynamic = bounded_dynamic_graphics_by_uci(
        parse_dynamic_attribute_property_records(
            streams.get("Unclustered Dynamic Attributes", b"")
        )
    )
    for uci, records in dynamic.items():
        for dynamic in records:
            graphic_ref = int(dynamic["graphic_ref"])
            node = nodes.get(graphic_ref)
            if node is None:
                continue
            for child in node["children"]:
                local_ref = int(child["ref"])
                relation = int(child["relation"])
                sheet = sheet_for_local_ref(local_ref, starts)
                if sheet is None:
                    continue
                entry = {
                    "uci": uci,
                    "graphic_ref": f"0x{graphic_ref:08X}",
                    "psm_node_type": int(node["type"]),
                    "sheet_stream": sheet,
                    "sheet_child_ref": f"0x{local_ref:08X}",
                    "relation_code": relation,
                }
                primitive_families = sorted(refs_by_sheet[sheet].get(local_ref, set()))
                if primitive_families:
                    entry["mapping_confidence"] = "direct-psm-node-to-decoded-sheet-child"
                    entry["sheet_primitive_families"] = primitive_families
                    type0_siblings = sorted(
                        composite_visible_children_by_type0(sheets[sheet]).get(local_ref, set())
                    )
                    if type0_siblings:
                        entry["same_composite_visible_sibling_child_refs"] = [
                            f"0x{ref:08X}" for ref in type0_siblings
                        ]
                        direct_type0_visible_sibling_count += len(type0_siblings)
                    direct.append(entry)
                    direct_family_counts.update(primitive_families)
                    relation_stats[f"relation_{relation}_direct"] += 1
                else:
                    entry["mapping_confidence"] = "candidate-psm-node-child-in-sheet-id-interval"
                    candidates.append(entry)
                    relation_stats[f"relation_{relation}_interval_only"] += 1
    return {
        "source_sha": str(sha_path),
        "status": "validated-0x8000-spacemap",
        "scope": (
            "SHA-only PSM hierarchy audit. Direct rows require an exact decoded Sheet child reference; "
            "header interval membership alone is not treated as visible geometry."
        ),
        "sheet_local_id_starts": [
            {"sheet_stream": name, "local_id_start": f"0x{start:08X}"}
            for start, name in starts
        ],
        "direct_link_count": len(direct),
        "candidate_interval_link_count": len(candidates),
        "relation_statistics": dict(sorted(relation_stats.items())),
        "direct_link_primitive_family_counts": dict(sorted(direct_family_counts.items())),
        "direct_type0_visible_sibling_count": direct_type0_visible_sibling_count,
        "direct_links": direct,
        "candidate_interval_links": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "direct_link_count": report["direct_link_count"],
                "candidate_interval_link_count": report["candidate_interval_link_count"],
                "relation_statistics": report["relation_statistics"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
