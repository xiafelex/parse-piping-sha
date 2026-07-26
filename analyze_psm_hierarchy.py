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

from analyze_iso_split import dynamic_graphics, read_sha_streams


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


def parse_tseg_spacemap0(data: bytes) -> dict[str, object]:
    """Parse the fully consumed mixed node layout in ``PSMspacemap/0x00000000``.

    A standard record uses the same `<4H>` header plus `<IH>` children as the
    0x8000 map. A compact record uses a `<3H>` header, children, and a trailing
    uint16. Where both layouts have the same byte length, choose the one whose
    child relations best match the observed relation-code family shared with
    the fully decoded 0x8000 map. The final compact node omits its trailing
    uint16 and ends exactly at the stream boundary.
    """

    if len(data) < 12 or data[:4] != b"tseg":
        raise ValueError("not a tseg stream")
    offset = 12
    nodes: list[dict[str, object]] = []
    while offset < len(data):
        if offset + 6 > len(data):
            raise ValueError(f"truncated node header at {offset}")
        node_id, node_type, child_count = struct.unpack_from("<3H", data, offset)
        if child_count > 500:
            raise ValueError(f"invalid child count at {offset}: {child_count}")
        compact_child_offset = offset + 6
        compact_end = compact_child_offset + child_count * 6
        compact_with_trailer_end = compact_end + 2
        if compact_end > len(data):
            raise ValueError(f"truncated compact node at {offset}")

        compact_children = [
            {"ref": child_ref, "relation": relation}
            for child_ref, relation in (
                struct.unpack_from("<IH", data, compact_child_offset + index * 6)
                for index in range(child_count)
            )
        ]
        standard_end = offset + 8 + child_count * 6
        standard_children: list[dict[str, int]] = []
        if standard_end <= len(data):
            standard_children = [
                {"ref": child_ref, "relation": relation}
                for child_ref, relation in (
                    struct.unpack_from("<IH", data, offset + 8 + index * 6)
                    for index in range(child_count)
                )
            ]
        known_relations = {181, 182, 183, 184, 190, 201}
        standard_score = sum(child["relation"] in known_relations for child in standard_children)
        compact_score = sum(child["relation"] in known_relations for child in compact_children)
        # Standard records are used only when they carry positive evidence.
        # This avoids mistaking compact zero-relations for a valid tie.
        if standard_score > 0 and standard_score >= compact_score:
            repeated_count = struct.unpack_from("<H", data, offset + 6)[0]
            child_offset = offset + 8
            end = standard_end
            trailing_value = None
            layout = "standard"
            children = standard_children
        else:
            child_offset = compact_child_offset
            if compact_with_trailer_end <= len(data):
                end = compact_with_trailer_end
                trailing_value = struct.unpack_from("<H", data, compact_end)[0]
                layout = "compact-with-trailer"
            elif compact_end == len(data):
                end = compact_end
                trailing_value = None
                layout = "terminal-compact"
            else:
                raise ValueError(f"truncated compact trailer at {offset}")
            repeated_count = None
            children = compact_children
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "child_count": child_count,
                "repeated_count": repeated_count,
                "trailing_value": trailing_value,
                "layout": layout,
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


def sheet_header_identities(streams: dict[str, bytes]) -> list[dict[str, int | str]]:
    """Read the observed unaligned Shape2D Sheet identity header fields.

    Starting at byte 4, populated Sheet streams in this sample store
    ``<I H I I I>``: declared bytes, record family, page kind, an object ref,
    and the stream's cluster ref.  The two refs are intentionally named by
    their storage role here; their complete semantic names remain unproven.
    """

    identities: list[dict[str, int | str]] = []
    for name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", name) or len(data) < 22:
            continue
        declared_bytes, record_family, page_kind, object_ref, cluster_ref = struct.unpack_from("<IHIII", data, 4)
        if data[:4] != b"D\xf5\x90l" or record_family != 0x3D:
            continue
        identities.append(
            {
                "stream": name,
                "declared_bytes": declared_bytes,
                "record_family": record_family,
                "page_kind": page_kind,
                "header_object_ref": object_ref,
                "cluster_ref": cluster_ref,
            }
        )
    return sorted(identities, key=lambda item: int(item["cluster_ref"]))


def local_child_sheet_links(
    nodes: list[dict[str, object]], sheet_headers: list[dict[str, int | str]]
) -> list[dict[str, object]]:
    """Report local child refs that land in a Sheet header's observed id span.

    The span ends two ids after ``header_object_ref``.  In the examined file,
    that catches repeatable ``cluster_ref + 1/+3/+6`` links for two full Sheet
    streams.  This is a namespace association, not a primitive classification.
    """

    counts: Counter[tuple[str, int, int]] = Counter()
    for node in nodes:
        for child in node["children"]:
            ref = int(child["ref"])
            for header in sheet_headers:
                start = int(header["cluster_ref"])
                end = int(header["header_object_ref"]) + 2
                if start <= ref <= end:
                    counts[(str(header["stream"]), ref, int(child["relation"]))] += 1
    return [
        {
            "sheet": sheet,
            "local_child_ref": ref,
            "offset_from_sheet_cluster_ref": ref
            - next(int(header["cluster_ref"]) for header in sheet_headers if header["stream"] == sheet),
            "relation": relation,
            "occurrences": occurrences,
        }
        for (sheet, ref, relation), occurrences in sorted(counts.items())
    ]


def parse_segment_table(data: bytes) -> dict[str, object]:
    """Decode the complete structural framing of the tiny observed ``stab`` table."""

    if len(data) < 8 or data[:4] != b"stab":
        return {"recognized": False}
    count = struct.unpack_from("<I", data, 4)[0]
    if len(data) != 8 + count:
        return {
            "recognized": True,
            "fully_consumed": False,
            "declared_count": count,
            "payload_bytes": len(data) - 8,
        }
    return {
        "recognized": True,
        "fully_consumed": True,
        "declared_count": count,
        "values": list(data[8:]),
        "semantics": "unresolved",
    }


def parse_short_tseg_u16_list(data: bytes) -> dict[str, object]:
    """Decode the complete framing of short tseg streams as uint16 payloads."""

    if len(data) < 8 or data[:4] != b"tseg" or (len(data) - 4) % 2:
        return {"recognized": False}
    words = list(struct.unpack_from("<" + str((len(data) - 4) // 2) + "H", data, 4))
    return {
        "recognized": True,
        "fully_consumed": True,
        "header_u16": words[:2],
        "payload_u16": words[2:],
        "semantics": "unresolved",
    }


def bbox_tag_summary(
    records: list[dict[str, object]], nodes: list[dict[str, object]], dynamic_refs: set[int]
) -> dict[str, dict[str, object]]:
    """Summarize record tags without treating them as semantic object classes."""

    node_type_by_id = {int(node["id"]): int(node["type"]) for node in nodes}
    output: dict[str, dict[str, object]] = {}
    for tag in sorted({int(record["tag"]) for record in records}):
        tagged = [record for record in records if int(record["tag"]) == tag]
        node_types = Counter(
            node_type_by_id[int(record["graphic_ref"])]
            for record in tagged
            if int(record["graphic_ref"]) in node_type_by_id
        )
        output[str(tag)] = {
            "record_count": len(tagged),
            "dynamic_attribute_graphic_count": sum(
                int(record["graphic_ref"]) in dynamic_refs for record in tagged
            ),
            "tseg_node_type_counts": {str(node_type): count for node_type, count in sorted(node_types.items())},
        }
    return output


def bbox_record_sheet_namespaces(
    records: list[dict[str, object]], sheet_headers: list[dict[str, int | str]], dynamic_refs: set[int]
) -> list[dict[str, object]]:
    """Group PSM record ids by the preceding registered Sheet cluster id.

    In this file, record ids fill intervals immediately after a Sheet's
    ``cluster_ref`` and before the next Sheet's cluster ref. This establishes
    an owning *storage namespace*, not a rendered-page membership claim.
    """

    result: list[dict[str, object]] = []
    for index, header in enumerate(sheet_headers):
        start = int(header["cluster_ref"])
        end = int(sheet_headers[index + 1]["cluster_ref"]) if index + 1 < len(sheet_headers) else None
        members = [
            record
            for record in records
            if int(record["graphic_ref"]) >= start and (end is None or int(record["graphic_ref"]) < end)
        ]
        result.append(
            {
                "sheet": header["stream"],
                "graphic_ref_interval": [start, end],
                "record_count": len(members),
                "tag_counts": {str(tag): count for tag, count in sorted(Counter(int(record["tag"]) for record in members).items())},
                "dynamic_attribute_graphic_count": sum(
                    int(record["graphic_ref"]) in dynamic_refs for record in members
                ),
            }
        )
    return result


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
    spacemap0 = parse_tseg_spacemap0(space_streams["PSMspacemap/0x00000000"])
    bbox_index = parse_psm_bbox_record_runs(streams.get("PSMcluster0", b""))
    sheet_headers = sheet_header_identities(streams)
    child_sheet_links = local_child_sheet_links(hierarchy["nodes"], sheet_headers)
    dynamic_refs = {
        int(record["graphic_ref"])
        for records in dynamic_graphics(streams.get("Unclustered Dynamic Attributes", b"")).values()
        for record in records
    }
    relation_counts = Counter(
        child["relation"]
        for node in hierarchy["nodes"]
        for child in node["children"]
    )
    spacemap0_relation_counts = Counter(
        child["relation"]
        for node in spacemap0["nodes"]
        for child in node["children"]
    )
    return {
        "source_sha": str(sha_path),
        "validated": {
            "cluster_registry_names": utf16_strings(streams.get("PSMclustertable", b"")),
            "root_registry_names": utf16_strings(streams.get("PSMroots", b"")),
            "spacemap": main_name,
            "tseg": hierarchy,
            "spacemap0": {
                "header_u32": spacemap0["header_u32"],
                "node_count": spacemap0["node_count"],
                "fully_consumed": spacemap0["fully_consumed"],
                "layout_counts": dict(Counter(str(node["layout"]) for node in spacemap0["nodes"])),
                "node_type_counts": {
                    str(node_type): count
                    for node_type, count in sorted(Counter(int(node["type"]) for node in spacemap0["nodes"]).items())
                },
                "relation_code_counts": {str(key): value for key, value in sorted(spacemap0_relation_counts.items())},
                "terminal_node": spacemap0["nodes"][-1],
            },
            "psmcluster0_bbox_runs": {
                "record_size": bbox_index["record_size"],
                "run_count": len(bbox_index["runs"]),
                "record_count": len(bbox_index["records"]),
                "runs": bbox_index["runs"],
            },
            "psmcluster0_tag_summary": bbox_tag_summary(bbox_index["records"], hierarchy["nodes"], dynamic_refs),
            "sheet_header_identities": sheet_headers,
            "psmcluster0_record_sheet_namespaces": bbox_record_sheet_namespaces(
                bbox_index["records"], sheet_headers, dynamic_refs
            ),
            "local_child_sheet_namespace_links": child_sheet_links,
            "segment_table": parse_segment_table(streams.get("PSMsegmenttable", b"")),
            "short_spacemap_u16_lists": {
                name: parse_short_tseg_u16_list(space_streams[name])
                for name in (
                    "PSMspacemap/0x00002000",
                    "PSMspacemap/0x00004000",
                    "PSMspacemap/0x00006000",
                )
                if name in space_streams
            },
            "relation_code_counts": {str(key): value for key, value in sorted(relation_counts.items())},
        },
        "not_yet_decoded": {
            name: partial_tseg_summary(data)
            for name, data in space_streams.items()
            if name not in {
                main_name,
                "PSMspacemap/0x00000000",
                "PSMspacemap/0x00002000",
                "PSMspacemap/0x00004000",
                "PSMspacemap/0x00006000",
            }
        },
        "other_psm_streams": {
            name: len(data)
            for name, data in streams.items()
            if name.startswith("PSM") and name not in space_streams and name not in {"PSMclustertable", "PSMroots"}
        },
        "confidence_notice": (
            "Node and child-reference boundaries for PSMspacemap/0x00008000 are validated by full stream consumption. "
            "Some local child references repeatably land in Sheet header id spans, but relation-code semantics and links "
            "from those local references to PSMcluster0/Sheet primitives remain unresolved. PSMcluster0 tag values are "
            "structurally summarized only and must not be treated as component classes."
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
