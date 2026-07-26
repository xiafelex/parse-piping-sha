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
    return {
        "header_u32": list(struct.unpack_from("<2I", data, 4)),
        "node_count": len(nodes),
        "nodes": nodes,
        "fully_consumed": offset == len(data),
    }


def parse_psm_bbox_record_runs(data: bytes) -> dict[str, object]:
    """Index observed contiguous ``PSMcluster0`` `<I5H>` envelope runs.

    This deliberately requires at least three consecutive plausible 14-byte
    records before accepting a run.  It is stronger than searching raw bytes
    for an id, but it still does not establish the final uint16's semantics.
    """

    def valid_at(offset: int) -> bool:
        if offset + 14 > len(data):
            return False
        graphic_ref, left, bottom, right, top, tag = struct.unpack_from("<I5H", data, offset)
        return (
            1 <= graphic_ref < 0x100000
            and left < right <= 16800
            and bottom < top <= 11880
            and tag <= 20
        )

    runs: list[dict[str, int]] = []
    records: list[dict[str, object]] = []
    offset = 0
    while offset + 42 <= len(data):
        if not (valid_at(offset) and valid_at(offset + 14) and valid_at(offset + 28)):
            offset += 1
            continue
        start = offset
        count = 0
        while valid_at(offset):
            graphic_ref, left, bottom, right, top, tag = struct.unpack_from("<I5H", data, offset)
            records.append(
                {
                    "graphic_ref": graphic_ref,
                    "bbox": [left, bottom, right, top],
                    "tag": tag,
                    "offset": offset,
                }
            )
            count += 1
            offset += 14
        runs.append({"offset": start, "record_count": count})
    return {"record_size": 14, "runs": runs, "records": records}


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
    hierarchy = parse_tseg_nodes(main)
    bbox_index = parse_psm_bbox_record_runs(streams.get("PSMcluster0", b""))
    relation_counts = Counter(
        child["relation"]
        for node in hierarchy["nodes"]
        for child in node["children"]
    )
    return {
        "source_sha": str(sha_path),
        "validated": {
            "cluster_registry_names": utf16_strings(streams.get("PSMclustertable", b"")),
            "root_registry_names": utf16_strings(streams.get("PSMroots", b"")),
            "spacemap": main_name,
            "tseg": hierarchy,
            "psmcluster0_bbox_runs": {
                "record_size": bbox_index["record_size"],
                "run_count": len(bbox_index["runs"]),
                "record_count": len(bbox_index["records"]),
                "runs": bbox_index["runs"],
            },
            "relation_code_counts": {str(key): value for key, value in sorted(relation_counts.items())},
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
            "Node and child-reference boundaries for PSMspacemap/0x00008000 are validated by full stream consumption. "
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
    print(f"Validated nodes: {result['validated']['tseg']['node_count']}")


if __name__ == "__main__":
    main()
