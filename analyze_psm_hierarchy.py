#!/usr/bin/env python3
"""Extract currently validated Shape2D PSM hierarchy evidence from a SHA file.

This does not invent geometry. It decodes the fully validated `tseg` node table
in `PSMspacemap/0x00008000` and inventories the remaining PSM streams for the
next decoding stage.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from collections import Counter
from pathlib import Path

from analyze_iso_split import read_sha_streams


def utf16_strings(data: bytes) -> list[str]:
    """Return printable UTF-16LE strings embedded in a binary registry stream."""

    return [match.group().decode("utf-16le") for match in re.finditer(rb"(?:(?:[\x20-\x7e]\x00){3,})", data)]


def parse_tseg_nodes(data: bytes) -> dict[str, object]:
    """Parse the observed full node-table layout used by spacemap 0x8000.

    Header: `b"tseg"` + 8 bytes. Each node is `<4H>` followed by `count`
    child entries of `<IH>`: a local child reference and a relation code.
    The table is accepted only when it consumes the complete stream.
    """

    if len(data) < 12 or data[:4] != b"tseg":
        raise ValueError("not a tseg stream")
    # Two observed layouts share the ``tseg`` marker. The original layout has
    # two uint32 header values and starts its node table at byte 12. Compact
    # exports instead begin with ``<node_count, 1>`` then a root id, a uint16
    # child count, and that many uint16 local child refs before the same node
    # table. Detect the compact header from its bounded index and require full
    # node-table consumption below.
    compact_node_count, compact_flag = struct.unpack_from("<2H", data, 4)
    compact_root: dict[str, object] | None = None
    if compact_flag == 1 and len(data) >= 12:
        root_id, root_child_count = struct.unpack_from("<2H", data, 8)
        compact_offset = 12 + root_child_count * 2
        if root_child_count <= 500 and compact_offset <= len(data):
            compact_root = {
                "id": root_id,
                "child_refs": list(struct.unpack_from(f"<{root_child_count}H", data, 12)),
            }
            offset = compact_offset
        else:
            offset = 12
    else:
        offset = 12
    nodes: list[dict[str, object]] = []
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError(f"truncated node header at {offset}")
        node_id, node_type, child_count, repeated_count = struct.unpack_from("<4H", data, offset)
        end = offset + 8 + child_count * 6
        if child_count > 500 or end > len(data):
            raise ValueError(f"invalid node at {offset}: child_count={child_count}")
        children = [
            {"ref": child_ref, "relation": relation}
            for child_ref, relation in (
                struct.unpack_from("<IH", data, offset + 8 + index * 6)
                for index in range(child_count)
            )
        ]
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "child_count": child_count,
                "repeated_count": repeated_count,
                "children": children,
            }
        )
        offset = end
    if compact_root is not None and len(nodes) != compact_node_count:
        raise ValueError(f"compact node count mismatch: header={compact_node_count}, parsed={len(nodes)}")
    return {
        "header_u32": list(struct.unpack_from("<2I", data, 4)),
        "layout": "compact-root-index" if compact_root is not None else "standard",
        "compact_root": compact_root,
        "node_count": len(nodes),
        "nodes": nodes,
        "fully_consumed": offset == len(data),
    }


def partial_tseg_summary(data: bytes) -> dict[str, object]:
    """Summarize a tseg stream without claiming its trailing layout is decoded."""

    if len(data) < 12 or data[:4] != b"tseg":
        return {"recognized": False}
    offset = 12
    nodes = 0
    shapes: Counter[str] = Counter()
    try:
        while offset + 8 <= len(data):
            _, node_type, child_count, repeated_count = struct.unpack_from("<4H", data, offset)
            end = offset + 8 + child_count * 6
            if child_count > 500 or end > len(data):
                break
            nodes += 1
            # Do not assign semantics to the fourth field in a partial map:
            # this stream changes layout later and that field stops behaving
            # like a repeat count. Keep only a compact structural summary.
            shapes[f"type={node_type},children={child_count}"] += 1
            offset = end
    except struct.error:
        pass
    return {
        "recognized": True,
        "header_u32": list(struct.unpack_from("<2I", data, 4)),
        "tentative_node_shaped_records_before_layout_change": nodes,
        "bytes_after_tentative_scan": len(data) - offset,
        "node_shape_counts": dict(shapes.most_common(20)),
    }


def analyze(sha_path: Path) -> dict[str, object]:
    streams = read_sha_streams(sha_path)
    space_streams = {name: data for name, data in streams.items() if name.startswith("PSMspacemap/")}
    main_name = "PSMspacemap/0x00008000"
    main = space_streams.get(main_name)
    if main is None:
        raise ValueError(f"{main_name} is absent")
    try:
        hierarchy = parse_tseg_nodes(main)
        relation_counts = Counter(
            child["relation"]
            for node in hierarchy["nodes"]
            for child in node["children"]
        )
        main_result: dict[str, object] = {"layout": "validated-full-node-table", "tseg": hierarchy}
        relation_result: dict[str, int] = {str(key): value for key, value in sorted(relation_counts.items())}
    except ValueError as error:
        # Different Shape2D exports can use another compact layout under the
        # same stream name. Keep it explicitly inventory-only rather than
        # inventing node/child boundaries from a parser that did not consume
        # the source stream safely.
        main_result = {
            "layout": "unvalidated-variant",
            "parse_error": str(error),
            "partial_summary": partial_tseg_summary(main),
        }
        relation_result = {}
    return {
        "source_sha": str(sha_path),
        "validated": {
            "cluster_registry_names": utf16_strings(streams.get("PSMclustertable", b"")),
            "root_registry_names": utf16_strings(streams.get("PSMroots", b"")),
            "spacemap": main_name,
            "main_spacemap": main_result,
            "relation_code_counts": relation_result,
        },
        "not_yet_decoded": {
            name: partial_tseg_summary(data)
            for name, data in space_streams.items()
            if name != main_name
        },
        "other_psm_streams": {
            name: len(data)
            for name, data in streams.items()
            if name.startswith("PSM") and name not in space_streams and name not in {"PSMclustertable", "PSMroots"}
        },
        "confidence_notice": (
            "The 0x00008000 table is marked validated only when its node framing consumes the full source stream. "
            "Relation-code semantics and links from local child references to PSMcluster0/Sheet primitives remain unresolved."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PSM hierarchy report: {args.output}")
    main_map = result["validated"]["main_spacemap"]
    if main_map["layout"] == "validated-full-node-table":
        print(f"Validated nodes: {main_map['tseg']['node_count']}")
    else:
        print("PSM hierarchy layout: unvalidated variant")


if __name__ == "__main__":
    main()
