#!/usr/bin/env python3
"""Extract currently validated Shape2D PSM hierarchy evidence from a SHA file.

This does not invent geometry. It decodes the fully validated `tseg` node table
in `PSMspacemap/0x00008000` and inventories the remaining PSM streams for the
next decoding stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import olefile

from analyze_iso_split import dynamic_graphics, read_sha_streams


def psm_envelopes(data: bytes, graphic_ref: int) -> list[list[int]]:
    """Return plausible PSMcluster0 envelopes for one local reference."""

    boxes: list[list[int]] = []
    needle = struct.pack("<I", graphic_ref)
    for match in re.finditer(re.escape(needle), data):
        offset = match.start() + 4
        if offset + 10 > len(data):
            continue
        left, bottom, right, top, _ = struct.unpack_from("<5H", data, offset)
        if left < right <= 16800 and bottom < top <= 11880:
            box = [left, bottom, right, top]
            if box not in boxes:
                boxes.append(box)
    return boxes


def parse_psmcluster_envelope_runs(data: bytes) -> dict[str, object]:
    """Inventory conservatively bounded contiguous ``<I5H>`` envelope runs.

    A raw reference search is useful for lookup, but does not itself prove a
    record boundary.  Here an entry is accepted only inside a run of at least
    three adjacent 14-byte records, with a local reference, valid page bounds,
    and a small trailing tag.  The tag remains opaque; this parser establishes
    only the repeated binary record family and its safe tag distribution.
    """

    def valid_at(offset: int) -> bool:
        if offset + 14 > len(data):
            return False
        ref, left, bottom, right, top, tag = struct.unpack_from("<I5H", data, offset)
        return 1 <= ref <= 0xFFFF and left < right <= 16800 and bottom < top <= 11880 and tag <= 0xFF

    runs: list[dict[str, object]] = []
    offset = 0
    while offset + 42 <= len(data):
        if not (valid_at(offset) and valid_at(offset + 14) and valid_at(offset + 28)):
            offset += 1
            continue
        start = offset
        records: list[dict[str, int]] = []
        while valid_at(offset):
            ref, left, bottom, right, top, tag = struct.unpack_from("<I5H", data, offset)
            records.append(
                {
                    "offset": offset,
                    "graphic_ref": ref,
                    "left": left,
                    "bottom": bottom,
                    "right": right,
                    "top": top,
                    "opaque_tag": tag,
                }
            )
            offset += 14
        runs.append({"offset": start, "record_count": len(records), "records": records})
    records = [record for run in runs for record in run["records"]]
    return {
        "layout": "conservative-contiguous-i5h-envelope-runs",
        "run_count": len(runs),
        "record_count": len(records),
        "opaque_tag_counts": dict(Counter(int(record["opaque_tag"]) for record in records)),
        "runs": runs,
        "semantic_limit": (
            "the trailing uint16 is an internal PSM record-family tag. Cross-family/layer evidence disproves "
            "treating it as a one-to-one Shape2D primitive type or page-layer id; it is not a visibility or "
            "drawing instruction"
        ),
    }


def psm_envelope_tag_provenance(
    streams: dict[str, bytes], envelope_runs: dict[str, object]
) -> dict[str, object]:
    """Cross-reference opaque envelope tags with independently bounded Sheets."""

    tags_by_ref: dict[int, list[int]] = {}
    for run in envelope_runs.get("runs", []):
        for record in run["records"]:
            tags_by_ref.setdefault(int(record["graphic_ref"]), []).append(int(record["opaque_tag"]))
    families = {
        "18_32_line_graphic_ref": (parse_18_32_layer_bindings, "graphic_ref"),
        "4d_text_child_ref": (parse_4d_text_layer_bindings, "child_ref"),
        "59_2b_ellipse_graphic_ref": (parse_59_2b_page_layer_bindings, "graphic_ref"),
        "61_arc_graphic_ref": (parse_61_pipe_arc_records, "graphic_ref"),
        "7b_composite_ref": (parse_7b_composite_headers, "composite_ref"),
    }
    evidence: dict[str, object] = {}
    for family, (parser, field) in families.items():
        source_refs = 0
        matched_refs = 0
        tag_counts: Counter[int] = Counter()
        for stream_name, data in streams.items():
            if not re.fullmatch(r"Sheet\d+", stream_name):
                continue
            for record in parser(data):
                ref = int(record[field])
                source_refs += 1
                if ref in tags_by_ref:
                    matched_refs += 1
                    tag_counts.update(tags_by_ref[ref])
        evidence[family] = {
            "source_record_count": source_refs,
            "run_bounded_psm_match_count": matched_refs,
            "opaque_tag_counts": dict(sorted(tag_counts.items())),
        }
    return {
        "families": evidence,
        "semantic_limit": (
            "cross-family frequencies prove an opaque PSM internal subtype correlation and disprove a one-to-one "
            "primitive-type or page-layer interpretation; tags are not component or visibility names"
        ),
    }


def psm_envelope_tag_layer_provenance(
    streams: dict[str, bytes],
    envelope_runs: dict[str, object],
    layer_name_by_ref: dict[int, str],
) -> dict[str, object]:
    """Summarize run-bounded line envelope tags by directly decoded Sheet layer."""

    tags_by_ref: dict[int, list[int]] = {}
    for run in envelope_runs.get("runs", []):
        for record in run["records"]:
            tags_by_ref.setdefault(int(record["graphic_ref"]), []).append(int(record["opaque_tag"]))
    counts: dict[str, Counter[int]] = {}
    matched_line_count = 0
    for stream_name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", stream_name):
            continue
        for record in parse_18_32_layer_bindings(data):
            layer = layer_name_by_ref.get(int(record["page_layer_ref"]))
            if layer is None:
                continue
            for tag in tags_by_ref.get(int(record["graphic_ref"]), []):
                counts.setdefault(layer, Counter())[tag] += 1
                matched_line_count += 1
    return {
        "run_bounded_18_32_line_match_count": matched_line_count,
        "opaque_tag_counts_by_direct_sheet_layer": {
            layer: dict(sorted(layer_counts.items()))
            for layer, layer_counts in sorted(counts.items())
        },
        "semantic_limit": (
            "a layer association is direct only for validated 18/32 line records; a tag being observed only in "
            "one layer does not make it that layer's exclusive semantic or a rendering rule"
        ),
    }


def parse_psmcluster_named_records(data: bytes) -> dict[str, object]:
    """Parse the repeatable named-record family embedded in ``PSMcluster0``.

    The family begins with ``0x0081`` and has an exact length equation:
    ``uint16 marker + uint32 record_length + 5 uint32 fields + UTF-16LE name
    + NUL``.  The final fixed field is the UTF-16 character count, making the
    record independently bounded without using text proximity.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x81\x00"), data):
        offset = match.start()
        if offset + 30 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        if not 32 <= record_length <= 300 or offset + record_length > len(data):
            continue
        name_char_count = struct.unpack_from("<I", data, offset + 26)[0]
        if record_length != 30 + 2 * (name_char_count + 1):
            continue
        name_end = offset + 30 + name_char_count * 2
        if data[name_end : offset + record_length] != b"\x00\x00":
            continue
        try:
            name = data[offset + 30 : name_end].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        if not name or not all(31 < ord(character) < 127 for character in name):
            continue
        object_ref, field_1, field_2, field_3, entry_id = struct.unpack_from("<5I", data, offset + 6)
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "object_ref": object_ref,
                "field_1": field_1,
                "field_2": field_2,
                "field_3": field_3,
                "entry_id": entry_id,
                "name": name,
            }
        )
    return {
        "layout": "validated-0x0081-named-records",
        "record_count": len(records),
        "field_value_summary": {
            "field_1_nonzero_count": sum(int(record["field_1"]) != 0 for record in records),
            "field_2_nonzero_count": sum(int(record["field_2"]) != 0 for record in records),
            "field_3_nonzero_count": sum(int(record["field_3"]) != 0 for record in records),
        },
        "named_layer_like_count": sum(
            bool(re.fullmatch(r"Level \d+", str(record["name"])))
            or str(record["name"])
            in {"PIPE", "FITTINGS", "WELDS", "DIMLINES", "MATLIST", "ISOTEXT", "SKETCHES", "NOZZLES", "Border", "FRAME"}
            for record in records
        ),
        "records": records,
    }


def parse_psmcluster_88_page_default_records(data: bytes) -> dict[str, object]:
    """Decode the separate ``0x0088`` page-level named-object family.

    It has the same self-sized UTF-16 framing as ``0x0081`` records but is
    stored once per physical Sheet in the audited files and names the page's
    ``Default`` object.  Keep it separate from the ordinary named-layer
    directory: its object ids participate in mixed PSM routing, not in the
    per-page visible-layer group.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x88\x00"), data):
        offset = match.start()
        if offset + 30 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        record_end = offset + 4 + record_length
        if not 32 <= record_length <= 300 or record_end > len(data):
            continue
        name_char_count = struct.unpack_from("<I", data, offset + 30)[0]
        if record_length != 30 + 2 * (name_char_count + 1):
            continue
        name_end = offset + 34 + name_char_count * 2
        if data[name_end:record_end] != b"\x00\x00":
            continue
        try:
            name = data[offset + 34 : name_end].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        if not name or not all(31 < ord(character) < 127 for character in name):
            continue
        object_ref, field_1, field_2, field_3, page_parent_ref, field_5 = struct.unpack_from(
            "<6I", data, offset + 6
        )
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "object_ref": object_ref,
                "field_1": field_1,
                "field_2": field_2,
                "field_3": field_3,
                "page_parent_ref": page_parent_ref,
                "field_5": field_5,
                "name": name,
            }
        )
    return {
        "layout": "validated-0x0088-page-default-named-object-records",
        "record_count": len(records),
        "default_name_record_count": sum(record["name"] == "Default" for record in records),
        "records": records,
        "semantic_limit": (
            "the name and per-page storage establish a page-level Default object; fields remain raw and do not "
            "establish visible-layer, viewport, or rendering semantics"
        ),
    }


def parse_psmcluster_42_page_layer_containers(data: bytes) -> dict[str, object]:
    """Decode bounded PSMcluster0 page containers that enumerate layer refs.

    The record length follows ``72 + 4 * member_count@26`` and its member
    vector begins at ``+30``.  Across audited physical pages the vector is an
    exact set match for that page's independently decoded ``0x0081`` named
    layer records. The fixed tail begins after that vector: its second u32 is
    a direct backlink to the page's separately stored ``0x0088 Default``
    object. This establishes a page-layer container role without assigning
    drawing or visibility semantics to the container itself.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x42\x00"), data):
        offset = match.start()
        if offset + 30 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        member_count = struct.unpack_from("<I", data, offset + 26)[0]
        members_end = offset + 30 + member_count * 4
        if (
            not 1 <= member_count <= 200
            or record_length != 72 + 4 * member_count
            or offset + record_length > len(data)
            or members_end > len(data)
        ):
            continue
        member_refs = list(struct.unpack_from(f"<{member_count}I", data, offset + 30))
        if not all(0 < member_ref <= 0xFFFF for member_ref in member_refs):
            continue
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "sheet_stream_id": struct.unpack_from("<I", data, offset + 18)[0],
                "member_anchor_ref": struct.unpack_from("<I", data, offset + 22)[0],
                "member_count": member_count,
                "member_refs": member_refs,
                # The remaining fixed 42 bytes are a control tail. Only its
                # second u32 has a cross-record identity proof so far.
                "container_control_prefix_raw": struct.unpack_from("<I", data, members_end)[0],
                "page_default_object_ref": struct.unpack_from("<I", data, members_end + 4)[0],
                "container_control_tail_raw_hex": data[members_end : offset + record_length].hex(),
            }
        )
    return {
        "layout": "validated-0x0042-page-layer-container",
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "member references prove page-layer grouping and page_default_object_ref proves a Default-object "
            "backlink only; the container is not a visible Sheet primitive, component, or rendering instruction"
        ),
    }


def validate_psmcluster_42_member_anchor_refs(
    page_layer_containers: list[dict[str, object]], named_records: list[dict[str, object]]
) -> dict[str, object]:
    """Resolve the in-vector layer anchor stored at ``0x0042 + 22``."""

    named_by_ref = {int(record["object_ref"]): record for record in named_records}
    records: list[dict[str, object]] = []
    for container in page_layer_containers:
        anchor_ref = int(container["member_anchor_ref"])
        anchor = named_by_ref.get(anchor_ref)
        member_refs = [int(value) for value in container["member_refs"]]
        records.append(
            {
                "object_ref": int(container["object_ref"]),
                "sheet_stream_id": int(container["sheet_stream_id"]),
                "member_anchor_ref": anchor_ref,
                "anchor_is_member": anchor_ref in member_refs,
                "anchor_member_index": member_refs.index(anchor_ref) if anchor_ref in member_refs else None,
                "anchor_name": str(anchor["name"]) if anchor is not None else None,
                "anchor_entry_id": int(anchor["entry_id"]) if anchor is not None else None,
            }
        )
    return {
        "layout": "0x0042-member-anchor-to-0x0081-named-layer-reference",
        "record_count": len(records),
        "member_anchor_match_count": sum(bool(record["anchor_is_member"]) for record in records),
        "all_anchors_are_members": bool(records)
        and all(bool(record["anchor_is_member"]) for record in records),
        "records": records,
        "semantic_limit": "the field is a verified in-container named-layer anchor, but it is not proven to mean active layer, visibility, print state, geometry, or component identity",
    }


def validate_psmcluster_88_default_parent_refs(
    page_default_records: list[dict[str, object]], named_records: list[dict[str, object]]
) -> dict[str, object]:
    """Resolve each page ``0x0088 Default`` object's named parent.

    ``page_parent_ref`` is a PSM object reference, not a Sheet stream id: it
    resolves to a separate ``0x0081`` named ``Default`` record.
    """

    named_by_ref = {int(record["object_ref"]): record for record in named_records}
    records: list[dict[str, object]] = []
    for record in page_default_records:
        parent_ref = int(record["page_parent_ref"])
        parent = named_by_ref.get(parent_ref)
        records.append(
            {
                "object_ref": int(record["object_ref"]),
                "page_parent_ref": parent_ref,
                "parent_name": str(parent["name"]) if parent is not None else None,
                "parent_entry_id": int(parent["entry_id"]) if parent is not None else None,
                "parent_is_named_default": parent is not None and str(parent["name"]) == "Default",
            }
        )
    return {
        "layout": "0x0088-page-default-to-0x0081-named-default-parent-reference",
        "record_count": len(records),
        "named_default_parent_match_count": sum(
            bool(record["parent_is_named_default"]) for record in records
        ),
        "all_parents_are_named_default": bool(records)
        and all(bool(record["parent_is_named_default"]) for record in records),
        "records": records,
        "semantic_limit": "the reference establishes Default-object containment only; it is not a Sheet stream id, visible layer, geometry, or component link",
    }


def validate_psmcluster_88_page_container_links(
    page_default_records: list[dict[str, object]],
    named_records: list[dict[str, object]],
    page_layer_containers: list[dict[str, object]],
) -> dict[str, object]:
    """Join ``0x0088 Default`` objects back to one page-layer container.

    The link is deliberately indirect and fully bounded:
    ``0x0088.page_parent_ref -> 0x0081 Default -> 0x0042.member_refs``.
    """

    named_by_ref = {int(record["object_ref"]): record for record in named_records}
    containers_by_member_ref: dict[int, list[dict[str, object]]] = {}
    for container in page_layer_containers:
        for member_ref in container["member_refs"]:
            containers_by_member_ref.setdefault(int(member_ref), []).append(container)
    records: list[dict[str, object]] = []
    for record in page_default_records:
        parent_ref = int(record["page_parent_ref"])
        parent = named_by_ref.get(parent_ref)
        containers = containers_by_member_ref.get(parent_ref, [])
        records.append(
            {
                "object_ref": int(record["object_ref"]),
                "parent_default_ref": parent_ref,
                "parent_is_named_default": parent is not None and str(parent["name"]) == "Default",
                "matching_page_container_count": len(containers),
                "page_container_object_ref": int(containers[0]["object_ref"])
                if len(containers) == 1
                else None,
                "sheet_stream_id": int(containers[0]["sheet_stream_id"])
                if len(containers) == 1
                else None,
            }
        )
    return {
        "layout": "0x0088-default-via-0x0081-default-to-unique-0x0042-page-container",
        "record_count": len(records),
        "unique_page_container_link_count": sum(
            bool(record["parent_is_named_default"])
            and int(record["matching_page_container_count"]) == 1
            for record in records
        ),
        "all_records_have_unique_page_container": bool(records)
        and all(
            bool(record["parent_is_named_default"])
            and int(record["matching_page_container_count"]) == 1
            for record in records
        ),
        "records": records,
        "semantic_limit": "the indirect link identifies a page/container association for Default objects only; it does not create a visible primitive, component, layer-membership for geometry, or rendering instruction",
    }


def validate_psmcluster_42_default_object_refs(
    page_layer_containers: list[dict[str, object]], page_default_records: list[dict[str, object]]
) -> dict[str, object]:
    """Validate the direct ``0x0042`` tail backlink to ``0x0088 Default``.

    This is independent of the named-layer route: ``0x0042`` stores the
    ``0x0088`` object id directly after its variable layer-member vector.
    The referenced Default object's named parent must also remain a member of
    the same container, keeping both representations in agreement.
    """

    defaults_by_ref = {int(record["object_ref"]): record for record in page_default_records}
    records: list[dict[str, object]] = []
    for container in page_layer_containers:
        default_ref = int(container["page_default_object_ref"])
        default = defaults_by_ref.get(default_ref)
        parent_ref = int(default["page_parent_ref"]) if default is not None else None
        records.append(
            {
                "page_container_object_ref": int(container["object_ref"]),
                "sheet_stream_id": int(container["sheet_stream_id"]),
                "page_default_object_ref": default_ref,
                "references_0088_default": default is not None and str(default["name"]) == "Default",
                "default_named_parent_ref": parent_ref,
                "default_named_parent_is_member": parent_ref in container["member_refs"]
                if parent_ref is not None
                else False,
            }
        )
    return {
        "layout": "0x0042-tail-direct-0x0088-default-backlink",
        "record_count": len(records),
        "direct_default_backlink_match_count": sum(
            bool(record["references_0088_default"])
            and bool(record["default_named_parent_is_member"])
            for record in records
        ),
        "all_containers_have_matching_default_backlink": bool(records)
        and all(
            bool(record["references_0088_default"])
            and bool(record["default_named_parent_is_member"])
            for record in records
        ),
        "records": records,
        "semantic_limit": (
            "the field is a direct page-Default object backlink. It does not establish Default visibility, "
            "a viewport, a Sheet primitive, component identity, or a rendering instruction"
        ),
    }


def parse_psmcluster_57_page_linked_control_records(data: bytes) -> dict[str, object]:
    """Decode the bounded page-linked ``0x0057`` PSMcluster0 family.

    Every verified form stores its object id at ``+6`` and the associated
    Sheet stream id at ``+22``. Compact 148/176-byte forms only name
    ``Default``/``DwgTemplate``. The 1756-byte shared-template form and the
    rare 1916-byte physical-page form also carry a serialized layer-state
    profile: bounded UTF-16 fragments include layer names such as ``PIPES``,
    ``DIMTEXT`` and ``WELDS``. This establishes page/template display-layer
    routing, but does not prove a viewport, visibility bit, or drawable
    geometry meaning for the remaining raw fields.
    """

    known_lengths = {
        148: "compact-page-control",
        176: "compact-template-page-control",
        1756: "template-layer-state-profile",
        1916: "extended-page-layer-state-profile",
    }
    layer_table_layouts = {
        # Both long forms end with a uint32 control value. The preceding
        # count includes that terminator, so it is one larger than the actual
        # number of variable-length name/state entries.
        1756: (138, 142),
        1916: (122, 126),
    }
    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x57\x00"), data):
        offset = match.start()
        if offset + 34 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        profile_kind = known_lengths.get(record_length)
        if profile_kind is None or offset + record_length > len(data):
            continue
        object_ref = struct.unpack_from("<I", data, offset + 6)[0]
        sheet_stream_id = struct.unpack_from("<I", data, offset + 22)[0]
        if not 0 < object_ref <= 0xFFFF or not 0 < sheet_stream_id <= 0xFFFF:
            continue
        layer_state_entries: list[dict[str, object]] = []
        layer_state_trailer_raw: int | None = None
        if record_length in layer_table_layouts:
            count_offset, entries_offset = layer_table_layouts[record_length]
            declared_item_count = struct.unpack_from("<I", data, offset + count_offset)[0]
            cursor = offset + entries_offset
            record_end = offset + record_length
            # The long form is self-delimiting: every entry is
            # <uint32 utf16_char_count><UTF-16 name><uint16 entry id>. The
            # cross-check below proves this value equals the same Sheet
            # 0x0081 named-layer entry_id; retain state_raw for report
            # compatibility while emitting the accurate entry_id_raw alias.
            for _ in range(max(declared_item_count - 1, 0)):
                if cursor + 6 > record_end - 4:
                    layer_state_entries = []
                    break
                char_count = struct.unpack_from("<I", data, cursor)[0]
                cursor += 4
                string_end = cursor + char_count * 2
                if not 0 < char_count <= 64 or string_end + 2 > record_end - 4:
                    layer_state_entries = []
                    break
                name = data[cursor:string_end].decode("utf-16le")
                state_raw = struct.unpack_from("<H", data, string_end)[0]
                layer_state_entries.append(
                    {"name": name, "state_raw": state_raw, "entry_id_raw": state_raw}
                )
                cursor = string_end + 2
            if layer_state_entries and cursor == record_end - 4:
                layer_state_trailer_raw = struct.unpack_from("<I", data, cursor)[0]
            else:
                layer_state_entries = []
        layer_state_names = [str(entry["name"]) for entry in layer_state_entries]
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "profile_kind": profile_kind,
                "object_ref": object_ref,
                "sheet_stream_id": sheet_stream_id,
                "field_18_raw": struct.unpack_from("<I", data, offset + 18)[0],
                "field_26_raw": struct.unpack_from("<I", data, offset + 26)[0],
                "field_30_raw": struct.unpack_from("<I", data, offset + 30)[0],
                "layer_state_names": layer_state_names,
                "layer_state_entries": layer_state_entries,
                "layer_state_trailer_raw": layer_state_trailer_raw,
            }
        )
    return {
        "layout": "bounded-0x0057-page-linked-control-and-layer-state-record",
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "fixed Sheet-stream backlink and bounded layer-name profile establish page/template display-layer "
            "routing only; raw fields do not establish a visible primitive, transform, visibility state, or "
            "component type"
        ),
    }


def validate_psmcluster_57_layer_state_profiles(
    named_records: list[dict[str, object]],
    page_layer_containers: list[dict[str, object]],
    page_linked_control_records: list[dict[str, object]],
) -> dict[str, object]:
    """Cross-check long ``0x0057`` layer tables against their page layers.

    The profile's own Sheet backlink chooses the relevant ``0x0042`` member
    vector. This avoids relying on position in PSMcluster0 or on a fixed
    template id. The stable omitted name ``05`` is reported as evidence, not
    promoted to a visibility rule.
    """

    named_by_ref = {
        int(record["object_ref"]): record
        for record in named_records
    }
    containers_by_sheet = {
        int(record["sheet_stream_id"]): record for record in page_layer_containers
    }
    profiles: list[dict[str, object]] = []
    for record in page_linked_control_records:
        entries = record.get("layer_state_entries", [])
        if not entries:
            continue
        sheet_id = int(record["sheet_stream_id"])
        container = containers_by_sheet.get(sheet_id)
        if container is None:
            continue
        member_records = {
            member_ref: named_by_ref[member_ref]
            for member_ref in container["member_refs"]
            if member_ref in named_by_ref
        }
        member_names = {str(member["name"]) for member in member_records.values()}
        member_entry_ids_by_name = {
            str(member["name"]): int(member["entry_id"])
            for member in member_records.values()
            if "entry_id" in member
        }
        profile_names = {str(entry["name"]) for entry in entries}
        missing_member_names = sorted(member_names - profile_names)
        extra_profile_names = sorted(profile_names - member_names)
        entry_id_checks = [
            {
                "name": str(entry["name"]),
                "profile_raw": int(entry["state_raw"]),
                "page_layer_entry_id": member_entry_ids_by_name.get(str(entry["name"])),
                "matches": int(entry["state_raw"])
                == member_entry_ids_by_name.get(str(entry["name"])),
            }
            for entry in entries
            if str(entry["name"]) in member_entry_ids_by_name
        ]
        profiles.append(
            {
                "object_ref": int(record["object_ref"]),
                "sheet_stream_id": sheet_id,
                "record_length": int(record["record_length"]),
                "profile_name_count": len(profile_names),
                "page_layer_name_count": len(member_names),
                "missing_page_layer_names": missing_member_names,
                "extra_profile_names": extra_profile_names,
                "entry_id_check_count": len(entry_id_checks),
                "matching_entry_id_count": sum(
                    bool(check["matches"]) for check in entry_id_checks
                ),
                "state_raw_equals_page_layer_entry_id": bool(entry_id_checks)
                and all(bool(check["matches"]) for check in entry_id_checks),
                "matches_page_layers_except_stable_05": (
                    not extra_profile_names and missing_member_names == ["05"]
                ),
            }
        )
    return {
        "layout": "0x0057-long-profile-to-0x0042-0x0081-page-layer-cross-check",
        "profile_count": len(profiles),
        "profiles": profiles,
        "semantic_limit": (
            "the profile enumerates the page's named layers except observed reserved name 05; each checked "
            "per-name value equals that layer's 0x0081 entry_id, so it is an ordering/index identity rather "
            "than a visible, hidden, frozen, or printable state"
        ),
    }


def parse_psmcluster_75_root_catalog(data: bytes) -> dict[str, object]:
    """Decode the fixed top-level ``0x0075`` PSMcluster0 catalog.

    This is a document catalog, not a page primitive. Its three bounded UTF-16
    byte-length entries identify the top-level SiteObjects, PreferenceSet, and
    Sheets collections. The small following ids are catalog entry ids; they
    are retained without claiming they are local graphic references.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x75\x00"), data):
        offset = match.start()
        if offset + 31 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        record_end = offset + 6 + record_length
        if record_length != 113 or record_end > len(data):
            continue
        root_ref = struct.unpack_from("<I", data, offset + 6)[0]
        catalog_tag = data[offset + 26]
        entry_count = struct.unpack_from("<I", data, offset + 27)[0]
        if root_ref == 0 or entry_count != 3:
            continue
        cursor = offset + 31
        entries: list[dict[str, object]] = []
        valid = True
        for _ in range(entry_count):
            if cursor + 4 > record_end:
                valid = False
                break
            name_byte_length = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4
            name_end = cursor + name_byte_length
            if (
                name_byte_length == 0
                or name_byte_length % 2
                or name_end + 4 > record_end
            ):
                valid = False
                break
            try:
                name = data[cursor:name_end].decode("utf-16le")
            except UnicodeDecodeError:
                valid = False
                break
            entry_id = struct.unpack_from("<I", data, name_end)[0]
            entries.append({"name": name, "catalog_entry_id": entry_id})
            cursor = name_end + 4
        if not valid or data[cursor:record_end] != b"\x00" * (record_end - cursor):
            continue
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "root_ref": root_ref,
                "catalog_tag": catalog_tag,
                "entry_count": entry_count,
                "entries": entries,
                "tail_zero_byte_count": record_end - cursor,
            }
        )
    return {
        "layout": "validated-0x0075-top-level-psmcluster-catalog",
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "the catalog identifies top-level document collections only; catalog entry ids are not Sheet local "
            "references, component ids, or drawing instructions"
        ),
    }


def parse_psmcluster_02_preference_index(data: bytes) -> dict[str, object]:
    """Decode the fixed-size ``0x0002`` PreferenceSet index container.

    The embedded entry fields use mixed byte order: the first two uint16
    values are stored big-endian and the final uint32 is little-endian. Their
    business meanings are not known, but the 25-entry bounded index is useful
    to positively exclude this application-preference block from drawing.
    """

    records: list[dict[str, object]] = []
    marker = b"\x02\x00\x00\x03\x00\x00"
    for match in re.finditer(re.escape(marker), data):
        offset = match.start()
        record_end = offset + 6 + 768
        if record_end > len(data):
            continue
        if data[offset + 6 : offset + 10] != b"\x00\x01\x00\x00":
            continue
        if data[offset + 10 : offset + 15] != b"\x00" * 5 or data[offset + 15] != 25:
            continue
        entries = []
        for index in range(25):
            entry_offset = offset + 16 + index * 8
            group_id = int.from_bytes(data[entry_offset : entry_offset + 2], "big")
            key_id = int.from_bytes(data[entry_offset + 2 : entry_offset + 4], "big")
            target_raw = struct.unpack_from("<I", data, entry_offset + 4)[0]
            entries.append(
                {"group_id_raw": group_id, "key_id_raw": key_id, "target_raw": target_raw}
            )
        records.append(
            {
                "offset": offset,
                "record_length": 768,
                "object_ref": 256,
                "entry_count": 25,
                "entries": entries,
            }
        )
    return {
        "layout": "validated-0x0002-preferenceset-fixed-index",
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "the index belongs to the application PreferenceSet space; mixed-endian ids and targets remain raw "
            "and are not component, UCI, Sheet-local, or rendering references"
        ),
    }


def parse_psmcluster_6c_default_style_bundles(data: bytes) -> dict[str, object]:
    """Decode the fixed document-default ``0x006C`` wrapper.

    Its payload embeds a bounded ``0x0088 Default`` object and a following
    ``0x0037`` companion style payload. The latter has stable raw appearance
    fields but is not independently framed, so it must not be treated as a
    Sheet primitive or a separate low-id PSM relation target.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x6c\x00\x74\x00\x00\x00"), data):
        offset = match.start()
        # This wrapper's length includes its four-byte length field but not
        # the uint16 marker. Two explicit zero bytes align the nested
        # 0x0088/0x0037 forms before the outer record ends.
        record_end = offset + 2 + 116
        default_offset = offset + 12
        style_offset = offset + 64
        if record_end > len(data):
            continue
        if (
            data[default_offset : default_offset + 2] != b"\x88\x00"
            or struct.unpack_from("<I", data, default_offset + 2)[0] != 46
            or data[style_offset : style_offset + 2] != b"\x37\x00"
            or struct.unpack_from("<I", data, style_offset + 2)[0] != 50
            or data[record_end - 2 : record_end] != b"\x00\x00"
        ):
            continue
        name = data[default_offset + 34 : default_offset + 48].decode("utf-16le")
        if name != "Default":
            continue
        records.append(
            {
                "offset": offset,
                "record_length": 116,
                "default_object_ref": struct.unpack_from("<I", data, default_offset + 6)[0],
                "default_parent_ref": struct.unpack_from("<I", data, default_offset + 22)[0],
                "default_name": name,
                "companion_style_ref": struct.unpack_from("<I", data, style_offset + 6)[0],
                "companion_style_raw_fields": [
                    struct.unpack_from("<I", data, style_offset + field_offset)[0]
                    for field_offset in (10, 14, 18, 22, 26, 30, 34, 38, 42, 46, 50)
                ],
            }
        )
    return {
        "layout": "validated-0x006c-document-default-and-companion-style-bundle",
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "the companion style's raw appearance fields are document defaults only; they do not identify a "
            "piping component, a Sheet primitive, a page transform, or a visibility rule"
        ),
    }


def parse_psmcluster_89_application_property_records(data: bytes) -> dict[str, object]:
    """Classify bounded ``0x0089`` PSMcluster0 application-property rows.

    This marker is namespace-local: it is not the `Unclustered Dynamic
    Attributes` 0x0089 reference footer. The record end is ``offset + 6 +
    record_length`` and known subtypes are identified only by complete marker
    strings within that bounded payload.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x89\x00"), data):
        offset = match.start()
        if offset + 10 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        record_end = offset + 6 + record_length
        if record_length not in {30, 40, 149, 245} or record_end > len(data):
            continue
        payload = data[offset:record_end]
        if b"_PastedGraphic\x00" in payload:
            subtype = "pasted-graphics-application-property"
        elif b"MSTN_GLOBALS\x00" in payload:
            subtype = "microstation-global-application-property"
        elif b"PipeLine Info\x00" in payload:
            subtype = "embedded-pipeline-info-application-property"
        elif b"_ISO" in payload:
            subtype = "compact-iso-application-property"
        else:
            continue
        property_payload_length = struct.unpack_from("<I", data, offset + 24)[0]

        # The outer application-property payload begins with a small binary
        # type slot and a NUL-prefixed ASCII application name.  The remaining
        # PipeLine Info payload contains property *labels*, not property values.
        property_payload = data[offset + 28:record_end]
        name_start = 3
        name_end = property_payload.find(b"\x00", name_start)
        application_name = (
            property_payload[name_start:name_end].decode("ascii", errors="replace")
            if name_end >= name_start
            else None
        )
        ascii_labels = [
            match.group().decode("ascii")
            for match in re.finditer(rb"[ -~]{3,}(?=\x00)", property_payload[name_end + 1 :] if name_end >= 0 else b"")
        ]
        property_labels = [label for label in ascii_labels if label != application_name]
        record = {
            "offset": offset,
            "record_length": record_length,
            "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
            "parent_object_ref": struct.unpack_from("<I", data, offset + 18)[0],
            "flags_raw": struct.unpack_from("<H", data, offset + 22)[0],
            "property_payload_length": property_payload_length,
            "property_payload_length_matches_record_length": record_length == 20 + property_payload_length,
            "application_name_type_raw": struct.unpack_from("<H", data, offset + 28)[0],
            "application_name": application_name,
            "payload_ascii_tokens": property_labels,
            "property_label_candidates": property_labels,
            "subtype": subtype,
        }
        if subtype == "microstation-global-application-property":
            try:
                source_index = property_labels.index("FileName")
                source_path = property_labels[source_index + 1]
            except (ValueError, IndexError):
                source_path = None
            if source_path and source_path.lower().endswith(".dgn"):
                record["source_dgn_path"] = source_path
        if subtype == "embedded-pipeline-info-application-property":
            record["property_schema_labels"] = property_labels
        records.append(record)
    return {
        "layout": "bounded-psmcluster0-0x0089-application-property-records",
        "record_count": len(records),
        "subtype_counts": dict(Counter(str(record["subtype"]) for record in records)),
        "records": records,
        "semantic_limit": (
            "this PSMcluster0 family is application metadata. PipeLine Info labels declare an application "
            "property schema, not the actual UCI/Fly Text values. It must not be joined to the dynamic-attribute "
            "0x0089 namespace or UCI without a separately bounded bridge"
        ),
    }


def parse_psmcluster_73_background_records(data: bytes) -> dict[str, object]:
    """Identify the fixed global ``Background`` site-object container."""

    prefix = (
        b"\x73\x00\x03\x02\x00\x00\x00\x01\x00\x00\x00\x0a\x00"
        + "Background".encode("utf-16le")
    )
    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(prefix), data):
        offset = match.start()
        record_end = offset + 6 + 515
        if record_end > len(data):
            continue
        sketch_offset = data.find("Sketch".encode("utf-16le"), offset + 34, record_end)
        nested_offset = next(
            (
                candidate
                for candidate in range(offset + 64, record_end - 6, 8)
                if data[candidate : candidate + 2] == b"\x76\x00"
            ),
            -1,
        )
        nested_length = (
            struct.unpack_from("<I", data, nested_offset + 2)[0]
            if nested_offset >= 0 and nested_offset + 6 <= record_end
            else None
        )
        if nested_length is not None and nested_offset + 6 + nested_length > record_end:
            nested_offset, nested_length = -1, None
        nested_relative_offset = nested_offset - offset if nested_offset >= 0 else None
        nested_sketch_name: str | None = None
        nested_document_identifier: str | None = None
        if nested_offset >= 0 and nested_length is not None:
            nested_end = nested_offset + 6 + nested_length
            # 0x0076 has two bounded UTF-16LE fields after its fixed header:
            # ``Sketch`` at +58 and an optional source-document identifier.
            if nested_offset + 62 <= nested_end:
                sketch_char_count = struct.unpack_from("<I", data, nested_offset + 58)[0]
                sketch_end = nested_offset + 62 + 2 * sketch_char_count
                if sketch_end <= nested_end:
                    nested_sketch_name = data[nested_offset + 62 : sketch_end].decode(
                        "utf-16le", errors="replace"
                    )
                    if sketch_end + 4 <= nested_end:
                        identifier_char_count = struct.unpack_from("<I", data, sketch_end)[0]
                        identifier_end = sketch_end + 4 + 2 * identifier_char_count
                        if identifier_end <= nested_end:
                            nested_document_identifier = data[sketch_end + 4 : identifier_end].decode(
                                "utf-16le", errors="replace"
                            )
        records.append(
            {
                "offset": offset,
                "record_length": 515,
                "object_ref": 256,
                "name": "Background",
                "sketch_name_offset": sketch_offset if sketch_offset >= 0 else None,
                "nested_76_offset": nested_offset if nested_offset >= 0 else None,
                "nested_76_relative_offset": nested_relative_offset,
                "nested_76_length": nested_length,
                "nested_76_sketch_name": nested_sketch_name,
                "nested_76_document_identifier": nested_document_identifier,
            }
        )
    return {
        "layout": "validated-0x0073-global-background-site-object",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "background/Sketch site metadata is not ISO pipe geometry or a component identity",
    }


def parse_psmcluster_64_zero_control_slots(data: bytes) -> dict[str, object]:
    """Locate the fixed all-zero document control slot.

    This is intentionally reported as a reserved slot rather than assigned a
    drawing role: its entire bounded payload is zero in every observed SHA.
    """

    signature = b"\x64\x00\x65\x00\x00\x00"
    total_length = 6 + 101
    records: list[dict[str, int]] = []
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        end = offset + total_length
        if end <= len(data) and data[offset + 6 : end] == b"\x00" * 101:
            records.append(
                {
                    "offset": offset,
                    "record_length": 101,
                    "total_byte_length": total_length,
                }
            )
    return {
        "layout": "fixed-0x0064-all-zero-document-control-slot",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "reserved zero-filled document control slot; it has no object reference, geometry, component identity, or drawing instruction",
    }


def parse_psmcluster_65_section_name_sites(data: bytes) -> dict[str, object]:
    """Decode the bounded Section1 output-sheet directory in the 0x0065 site.

    The named directory ends by overlapping the final ``s`` of ``Backgrounds``
    with the following 0x0073 Background marker. This makes the outer 0x0065
    length unsuitable as a record boundary, but the Section1 list itself has
    an independent count and per-name UTF-16 framing.
    """

    signature = b"\x65\x00\x72\x00\x00\x00"
    section_name = "Section1"
    encoded_name = section_name.encode("utf-16le")
    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        name_offset = data.find(encoded_name, offset + 70, min(len(data), offset + 116))
        if name_offset < 0:
            continue
        section_end = name_offset + len(encoded_name)
        if section_end + 9 > len(data) or data[section_end : section_end + 5] != b"\x02\x01\x00\x00\x00":
            continue
        sheet_count = struct.unpack_from("<I", data, section_end + 5)[0]
        if not 1 <= sheet_count <= 100:
            continue
        cursor = section_end + 9
        sheet_names: list[str] = []
        valid = True
        for index in range(sheet_count):
            if cursor + 2 > len(data):
                valid = False
                break
            name_count = struct.unpack_from("<H", data, cursor)[0]
            name_start = cursor + 2
            name_end = name_start + 2 * name_count
            if not 1 <= name_count <= 32 or name_end + 1 > len(data):
                valid = False
                break
            try:
                sheet_name = data[name_start:name_end].decode("utf-16le")
            except UnicodeDecodeError:
                valid = False
                break
            if sheet_name != f"Sheet{index + 1}" or data[name_end] != 1:
                valid = False
                break
            sheet_names.append(sheet_name)
            cursor = name_end + 1
        if not valid or cursor + 2 > len(data):
            continue
        backgrounds_count = struct.unpack_from("<H", data, cursor)[0]
        backgrounds_start = cursor + 2
        backgrounds_end = backgrounds_start + 2 * backgrounds_count
        if backgrounds_count != len("Backgrounds") or backgrounds_end > len(data):
            continue
        try:
            backgrounds_name = data[backgrounds_start:backgrounds_end].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        overlap_offset = backgrounds_end - 2
        if (
            backgrounds_name != "Backgrounds"
            or data[overlap_offset : overlap_offset + 4] != b"\x73\x00\x03\x02"
        ):
            continue
        records.append(
            {
                "offset": offset,
                "raw_length_field": 114,
                "section_name": section_name,
                "section_name_offset": name_offset,
                "section_name_relative_offset": name_offset - offset,
                "sheet_count": sheet_count,
                "sheet_names": sheet_names,
                "backgrounds_name": backgrounds_name,
                "background_marker_overlap_offset": overlap_offset,
            }
        )
    return {
        "layout": "0x0065-section1-output-sheet-directory-overlapping-0x0073-background",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "the Section1 ordered output-page names and Backgrounds overlap are decoded; outer 0x0065 numeric fields and any geometry meaning remain unproven",
    }


def validate_psmcluster_65_section_sheet_directory(
    section_records: list[dict[str, object]], page_layer_containers: list[dict[str, object]]
) -> dict[str, object]:
    """Map the Section1 ordinal list to ordered non-base page containers."""

    container_sheet_ids = [int(record["sheet_stream_id"]) for record in page_layer_containers]
    # Sheet6 is the first ISO output page and therefore corresponds to the
    # directory label Sheet1. Sheet221 alone is the shared template excluded
    # from the Section1 output-page list.
    output_sheet_ids = [sheet_id for sheet_id in container_sheet_ids if sheet_id != 221]
    records: list[dict[str, object]] = []
    for section in section_records:
        names = [str(name) for name in section["sheet_names"]]
        matches = len(names) == len(output_sheet_ids)
        records.append(
            {
                "section_name": str(section["section_name"]),
                "declared_sheet_count": int(section["sheet_count"]),
                "shared_template_sheet_ids": [sheet_id for sheet_id in container_sheet_ids if sheet_id == 221],
                "output_sheet_stream_ids": output_sheet_ids,
                "ordinal_to_sheet_stream": [
                    {"section_sheet_name": name, "sheet_stream_id": sheet_id}
                    for name, sheet_id in zip(names, output_sheet_ids)
                ],
                "declared_count_matches_nonbase_page_container_count": matches,
            }
        )
    return {
        "layout": "section1-ordinal-output-sheet-directory-to-0x0042-nonbase-page-containers",
        "record_count": len(records),
        "matching_record_count": sum(
            bool(record["declared_count_matches_nonbase_page_container_count"]) for record in records
        ),
        "records": records,
        "semantic_limit": "Section Sheet1..N names are ordinal directory labels, not OLE Sheet stream names, coordinates, component ids, or geometry",
    }


def parse_psmcluster_top_vfset_records(data: bytes) -> dict[str, object]:
    """Decode the fixed short PSMcluster0 record used by the TopVFSet root.

    The record is structurally bounded as ``0x0067 + uint32(20) + 5*uint32``.
    Its first uint32 is cross-linked to the `TopVFSet` reference in PSMroots;
    remaining fields are retained as raw constants rather than named styles.
    """

    records: list[dict[str, int]] = []
    for match in re.finditer(re.escape(b"\x67\x00\x14\x00\x00\x00"), data):
        offset = match.start()
        if offset + 26 > len(data):
            continue
        object_ref, field_1, field_2, role_raw, field_4 = struct.unpack_from("<5I", data, offset + 6)
        records.append(
            {
                "offset": offset,
                "record_length": 20,
                "object_ref": object_ref,
                "field_1_raw": field_1,
                "field_2_raw": field_2,
                "role_raw": role_raw,
                "field_4_raw": field_4,
            }
        )
    return {
        "layout": "bounded-0x0067-top-vfset-record",
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "the root-object link is direct; role_raw and remaining fields are not a component, page-layer, or "
            "drawing instruction"
        ),
    }


def link_psm_root_directory_entries(
    root_registry: dict[str, object] | None, cluster_registry: dict[str, object] | None
) -> None:
    """Attach PSMroot values that are exact PSMclustertable directory indexes."""

    if root_registry is None or cluster_registry is None:
        return
    entries_by_index: dict[int, list[dict[str, object]]] = {}
    for entry in cluster_registry.get("entries", []):
        entries_by_index.setdefault(int(entry["directory_index"]), []).append(entry)
    for root in root_registry.get("entries", []):
        root["cluster_directory_entry_matches"] = [
            {
                "directory_index": int(entry["directory_index"]),
                "stream_name": str(entry["name"]),
                "child_names": list(entry["child_names"]),
            }
            for entry in entries_by_index.get(int(root["root_ref"]), [])
        ]


def parse_18_32_layer_bindings(data: bytes) -> list[dict[str, object]]:
    """Decode the per-page layer reference carried by valid ``18/32`` lines.

    The fixed 50-byte line layout stores its child id at ``+6``, object id at
    ``+10``, page-layer object id at ``+14``, style id at ``+20`` and four
    normalized double coordinates at ``+24``.  The coordinate checks make the
    fixed signature independently bounded before it is joined to PSMcluster0.
    """

    bindings: list[dict[str, object]] = []
    signature = b"\x18\x00\x32\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 56 > len(data) or struct.unpack_from("<I", data, offset + 2)[0] != 50:
            continue
        x1, y1, x2, y2 = struct.unpack_from("<4d", data, offset + 24)
        if not all(-0.03 <= value <= 1.05 for value in (x1, y1, x2, y2)):
            continue
        bindings.append(
            {
                "offset": offset,
                "child_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "graphic_ref": struct.unpack_from("<I", data, offset + 10)[0],
                "page_layer_ref": struct.unpack_from("<I", data, offset + 14)[0],
                "style_ref": struct.unpack_from("<I", data, offset + 20)[0],
                "start": [x1, y1],
                "end": [x2, y2],
                "is_zero_length_point_record": x1 == x2 and y1 == y2,
            }
        )
    return bindings


def parse_4d_text_layer_bindings(data: bytes) -> list[dict[str, object]]:
    """Decode the bounded text record family headed by ``0x004d``.

    The normal layout has length ``60 + 2 * UTF16 character count`` and text
    at ``+30``. A bounded extended layout has length ``68 + 2 * count`` and
    text at ``+38``; it retains an eight-byte control prefix at ``+30``.
    Both carry the same page-layer reference at ``+14`` as later-Sheet
    ``18/32`` lines. The extension fields remain raw until independently
    decoded.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x4d\x00"), data):
        offset = match.start()
        if offset + 32 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        character_count = struct.unpack_from("<H", data, offset + 28)[0]
        if record_length == 60 + 2 * character_count:
            text_start = offset + 30
            layout = "normal-0x004d-text"
            extension_raw = None
        elif record_length == 68 + 2 * character_count:
            text_start = offset + 38
            layout = "extended-0x004d-text-with-eight-byte-prefix"
            extension_raw = data[offset + 30 : offset + 38].hex()
        else:
            continue
        end = offset + 6 + record_length
        if not 0 <= character_count <= 500 or end > len(data):
            continue
        text_end = text_start + 2 * character_count
        try:
            text = data[text_start:text_end].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        if not all(character.isprintable() for character in text):
            continue
        x, y, direction_x, direction_y = struct.unpack_from("<4d", data, text_end)
        if not (
            -0.03 <= x <= 1.05
            and -0.03 <= y <= 1.05
            and 0.8 <= (direction_x * direction_x + direction_y * direction_y) ** 0.5 <= 1.2
        ):
            continue
        record = {
                "offset": offset,
                "record_length": record_length,
                "layout": layout,
                "text": text,
                "child_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "secondary_ref": struct.unpack_from("<I", data, offset + 10)[0],
                "page_layer_ref": struct.unpack_from("<I", data, offset + 14)[0],
                "style_ref": struct.unpack_from("<I", data, offset + 20)[0],
                "x": x,
                "y": y,
                "direction": [direction_x, direction_y],
                "tail_flags_u32": struct.unpack_from("<I", data, text_end + 32)[0],
            }
        if extension_raw is not None:
            record["text_prefix_extension_raw_hex"] = extension_raw
            record["text_prefix_constant_raw"] = struct.unpack_from("<H", data, offset + 30)[0]
            record["explicit_font_style_ref"] = struct.unpack_from("<I", data, offset + 32)[0]
            record["text_prefix_character_count"] = struct.unpack_from("<H", data, offset + 36)[0]
            record["text_prefix_character_count_matches"] = (
                int(record["text_prefix_character_count"]) == character_count
            )
        # In the audited corpus this is the only non-zero tail flag: one
        # Sheet221 Chinese company-name template label per SHA. Keep the
        # observed marker separate from a general Unicode/font claim.
        if record["tail_flags_u32"] == 0x01000000:
            record["observed_non_ascii_template_text_flag"] = True
        records.append(record)
    return records


def parse_13_ac_layer_relations(data: bytes) -> list[dict[str, object]]:
    """Decode bounded ``0x0013/0x00ac`` line graphic range/layer records.

    The fixed 172-byte form carries three direction-preserving ``0x67`` line
    endpoint pairs, a shared anchor, and three ``18/32`` child references.
    A normalized extent is derived for range lookups, but endpoint order must
    not be discarded: reverse-line companions are common in real Sheets.
    """

    relations: list[dict[str, object]] = []
    signature = b"\x13\x00\xac\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if (
            offset + 178 > len(data)
            or struct.unpack_from("<I", data, offset + 2)[0] != 172
        ):
            continue
        segment_starts = (35, 68, 101)
        if (
            any(data[offset + segment_start - 1] != 0x67 for segment_start in segment_starts)
            or data[offset + 149] != 1
            or struct.unpack_from("<I", data, offset + 150)[0] != 3
            or any(struct.unpack_from("<H", data, offset + 158 + index * 8)[0] != 203 for index in range(3))
        ):
            continue
        segments = [
            list(struct.unpack_from("<4d", data, offset + segment_start))
            for segment_start in segment_starts
        ]
        start_x, start_y, end_x, end_y = segments[0]
        if not (
            all(
                math.isfinite(value) and -0.03 <= value <= 1.05
                for segment in segments
                for value in segment
            )
        ):
            continue
        anchor_x, anchor_y = struct.unpack_from("<2d", data, offset + 133)
        if not all(math.isfinite(value) and -0.03 <= value <= 1.05 for value in (anchor_x, anchor_y)):
            continue
        graphic_ref, page_layer_ref = struct.unpack_from("<II", data, offset + 10)
        if not 1 <= graphic_ref <= 0xFFFF or not 1 <= page_layer_ref <= 0xFFFF:
            continue
        relations.append(
            {
                "offset": offset,
                "record_length": 172,
                "primitive_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "graphic_ref": graphic_ref,
                "page_layer_ref": page_layer_ref,
                # These bytes are a fixed record-format marker throughout the
                # current corpus. They are not the 18/32 line's style ref.
                "header_field_20": struct.unpack_from("<I", data, offset + 20)[0],
                "header_field_24": struct.unpack_from("<I", data, offset + 24)[0],
                "header_field_28": struct.unpack_from("<I", data, offset + 28)[0],
                "format_flags_32": struct.unpack_from("<H", data, offset + 32)[0],
                "format_marker_34": data[offset + 34],
                "start": [start_x, start_y],
                "end": [end_x, end_y],
                "segments": [
                    {"start": segment[:2], "end": segment[2:]}
                    for segment in segments
                ],
                "anchor": [anchor_x, anchor_y],
                "member_child_refs": [
                    struct.unpack_from("<I", data, offset + 154 + index * 8)[0]
                    for index in range(3)
                ],
                "member_relation_code": 203,
                "member_flags": [
                    struct.unpack_from("<H", data, offset + 160 + index * 8)[0]
                    for index in range(3)
                ],
                "bounding_box": [
                    min(value for segment in segments for value in (segment[0], segment[2])),
                    min(value for segment in segments for value in (segment[1], segment[3])),
                    max(value for segment in segments for value in (segment[0], segment[2])),
                    max(value for segment in segments for value in (segment[1], segment[3])),
                ],
            }
        )
    return relations


def validate_13_ac_reverse_line_aliases(data: bytes) -> dict[str, object]:
    """Verify that 13/AC segment copies are reverse aliases of 18/32 lines."""

    lines_by_child_ref = {
        int(record["child_ref"]): record for record in parse_18_32_layer_bindings(data)
    }
    relation_records = parse_13_ac_layer_relations(data)
    member_count = 0
    reverse_match_count = 0
    forward_match_count = 0
    missing_line_count = 0
    member_flag_counts: Counter[int] = Counter()
    for relation in relation_records:
        for segment, child_ref, member_flag in zip(
            relation["segments"], relation["member_child_refs"], relation["member_flags"]
        ):
            member_count += 1
            member_flag_counts[int(member_flag)] += 1
            line = lines_by_child_ref.get(int(child_ref))
            if line is None:
                missing_line_count += 1
                continue
            if line["start"] == segment["end"] and line["end"] == segment["start"]:
                reverse_match_count += 1
            elif line["start"] == segment["start"] and line["end"] == segment["end"]:
                forward_match_count += 1
    return {
        "layout": "0x0013-0x00ac-three-reverse-18-32-line-aliases",
        "relation_record_count": len(relation_records),
        "member_count": member_count,
        "reverse_18_32_line_match_count": reverse_match_count,
        "forward_18_32_line_match_count": forward_match_count,
        "missing_18_32_line_match_count": missing_line_count,
        "member_flag_counts": {str(flag): count for flag, count in sorted(member_flag_counts.items())},
        "semantic_limit": (
            "13/AC stores reverse aliases and grouping/range metadata for already decoded 18/32 lines. "
            "member flags are retained as ordering metadata, not named as line styles or component types"
        ),
    }


def parse_7b_composite_headers(data: bytes) -> list[dict[str, object]]:
    """Decode bounded Shape2D composite headers without drawing children.

    ``0x007b`` records use ``record_length@2 = 32 + 14 * child_count@22``.
    Their composite object reference is at ``+6`` and the Sheet reference is
    at ``+10``.  Child payloads intentionally remain in the SVG decoder.
    """

    records: list[dict[str, int]] = []
    for offset in range(0, len(data) - 34, 2):
        if data[offset : offset + 2] != b"\x7b\x00":
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        child_count = struct.unpack_from("<I", data, offset + 22)[0]
        if (
            not 1 <= child_count <= 100
            or record_length != 32 + 14 * child_count
            or offset + 6 + record_length > len(data)
        ):
            continue
        child_refs = [
            struct.unpack_from("<I", data, offset + 34 + index * 14)[0]
            for index in range(child_count)
        ]
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "composite_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "sheet_ref": struct.unpack_from("<I", data, offset + 10)[0],
                "child_count": child_count,
                "child_refs": child_refs,
            }
        )
    return records


def validate_psmcluster_89_parent_object_links(
    streams: dict[str, bytes], application_property_records: list[dict[str, object]]
) -> dict[str, object]:
    """Resolve nonzero application-property parents through proven graphic slots.

    Application-property object references belong to PSMcluster0, whereas a
    nonzero parent can identify a graphical PSM object.  This validator keeps
    those namespaces distinct and only reports an attachment when the parent
    is independently present in a bounded PSM envelope and/or a bounded Sheet
    graphic field.
    """

    psm_data = streams.get("PSMcluster0", b"")
    envelope_refs = {
        int(record["graphic_ref"])
        for run in parse_psmcluster_envelope_runs(psm_data)["runs"]
        for record in run["records"]
    }
    sheet_hits_by_ref: dict[int, list[dict[str, object]]] = {}
    for sheet_name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", sheet_name):
            continue
        for family, parser, ref_field in (
            ("18_32_line", parse_18_32_layer_bindings, "graphic_ref"),
            ("4d_text", parse_4d_text_layer_bindings, "child_ref"),
            ("59_2b_ellipse", parse_59_2b_page_layer_bindings, "graphic_ref"),
            ("61_arc", parse_61_pipe_arc_records, "graphic_ref"),
            ("13_63_circle_companion", parse_13_63_circle_geometry, "graphic_ref"),
            ("7b_composite", parse_7b_composite_headers, "composite_ref"),
        ):
            for record in parser(data):
                ref = int(record[ref_field])
                sheet_hits_by_ref.setdefault(ref, []).append(
                    {"sheet_stream_id": sheet_name, "family": family}
                )

    records: list[dict[str, object]] = []
    for application in application_property_records:
        parent_ref = int(application["parent_object_ref"])
        sheet_hits = sheet_hits_by_ref.get(parent_ref, []) if parent_ref else []
        records.append(
            {
                "object_ref": int(application["object_ref"]),
                "subtype": str(application["subtype"]),
                "parent_object_ref": parent_ref,
                "parent_is_zero": parent_ref == 0,
                "parent_in_psm_envelope": parent_ref in envelope_refs if parent_ref else False,
                "parent_sheet_graphic_families": sorted({str(hit["family"]) for hit in sheet_hits}),
                "parent_sheet_stream_ids": sorted({str(hit["sheet_stream_id"]) for hit in sheet_hits}),
            }
        )
    nonzero_records = [record for record in records if not bool(record["parent_is_zero"])]
    return {
        "layout": "0x0089-application-property-parent-to-bounded-psm-and-sheet-graphic-links",
        "record_count": len(records),
        "nonzero_parent_record_count": len(nonzero_records),
        "nonzero_parent_psm_envelope_match_count": sum(
            bool(record["parent_in_psm_envelope"]) for record in nonzero_records
        ),
        "nonzero_parent_sheet_graphic_match_count": sum(
            bool(record["parent_sheet_graphic_families"]) for record in nonzero_records
        ),
        "records": records,
        "semantic_limit": (
            "a matching nonzero parent attaches an application-property object to a graphical PSM object. "
            "PipeLine Info labels remain schema labels, not the dynamic UCI/Fly Text values or an instruction "
            "to alter the linked geometry"
        ),
    }


def validate_psmcluster_89_pasted_graphic_jsite_links(
    streams: dict[str, bytes], application_property_records: list[dict[str, object]]
) -> dict[str, object]:
    """Resolve only exact `_PastedGraphic object_ref - 1 -> JSite` links."""

    jsite_ids = sorted(
        {
            int(match.group(1))
            for stream_name in streams
            for match in [re.fullmatch(r"JSite(\d+)/.*", stream_name)]
            if match is not None
        }
    )
    records: list[dict[str, object]] = []
    for application in application_property_records:
        if application["subtype"] != "pasted-graphics-application-property":
            continue
        object_ref = int(application["object_ref"])
        candidate_jsite_id = object_ref - 1
        matched = candidate_jsite_id in jsite_ids
        resource_prefix = f"JSite{candidate_jsite_id}/"
        resource_streams = sorted(
            stream_name for stream_name in streams if stream_name.startswith(resource_prefix)
        ) if matched else []
        records.append(
            {
                "object_ref": object_ref,
                "candidate_jsite_id": candidate_jsite_id,
                "exact_jsite_match": matched,
                "jsite_streams": resource_streams,
                "has_embedded_contents": f"JSite{candidate_jsite_id}/CONTENTS" in resource_streams,
            }
        )
    return {
        "layout": "_PastedGraphic-object-ref-minus-one-to-exact-JSite-resource",
        "record_count": len(records),
        "exact_jsite_match_count": sum(bool(record["exact_jsite_match"]) for record in records),
        "records": records,
        "semantic_limit": (
            "only an exact object_ref-1 JSite stream is a resource link. Other PastedGraphic object ids are "
            "unbound application objects, not inferred JSite resources, geometry, or components"
        ),
    }


def summarize_7b_composite_child_graphic_links(data: bytes) -> dict[str, object]:
    """Summarize proven ``0x7B`` child-reference links to Sheet geometry.

    A composite child stores a local bounding tuple and a raw child type, but
    its reference normally resolves to the child id of a same-Sheet ``18/32``
    line or ``61`` arc.  The composite's own object reference is then the same
    graphic reference carried by that resolved primitive.  This is a graphic
    grouping/backlink, not proof that every child-type value is a line, arc, or
    piping-component class: type 6, for example, occurs against both families.
    """

    line_graphics = {
        int(record["child_ref"]): int(record["graphic_ref"])
        for record in parse_18_32_layer_bindings(data)
    }
    arc_graphics = {
        int(record["primitive_ref"]): int(record["graphic_ref"])
        for record in parse_61_pipe_arc_records(data)
    }
    ellipse_graphics = {
        int(record["primitive_ref"]): int(record["graphic_ref"])
        for record in parse_59_2b_page_layer_bindings(data)
    }
    raw_child_types: Counter[int] = Counter()
    linked_families: Counter[str] = Counter()
    linked_by_raw_type: Counter[str] = Counter()
    unlinked_non_range_types: Counter[int] = Counter()
    range_direct_non_range_member_counts: Counter[int] = Counter()
    range_direct_nested_range_member_counts: Counter[int] = Counter()
    range_exact_direct_non_range_envelope_count = 0
    parent_graphic_ref_matches = 0
    composite_count = 0

    for offset in range(0, len(data) - 34, 2):
        if data[offset : offset + 2] != b"\x7b\x00":
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        child_count = struct.unpack_from("<I", data, offset + 22)[0]
        if (
            not 1 <= child_count <= 100
            or record_length != 32 + 14 * child_count
            or offset + 6 + record_length > len(data)
        ):
            continue
        composite_count += 1
        parent_graphic_ref = struct.unpack_from("<I", data, offset + 6)[0]
        children: list[tuple[int, int, int, int, int, int]] = []
        for index in range(child_count):
            child_ref, left, bottom, right, top, raw_type = struct.unpack_from(
                "<I5H", data, offset + 34 + index * 14
            )
            children.append((child_ref, left, bottom, right, top, raw_type))
            raw_child_types[raw_type] += 1
            if raw_type == 0:
                # Type zero has no direct geometry counterpart in the audited
                # corpus. It is retained as a composite-local range child.
                continue
            if child_ref in line_graphics:
                family = "18_32_line"
                target_graphic_ref = line_graphics[child_ref]
            elif child_ref in arc_graphics:
                family = "61_arc"
                target_graphic_ref = arc_graphics[child_ref]
            elif child_ref in ellipse_graphics:
                family = "59_2b_ellipse"
                target_graphic_ref = ellipse_graphics[child_ref]
            else:
                unlinked_non_range_types[raw_type] += 1
                continue
            linked_families[family] += 1
            linked_by_raw_type[f"type_{raw_type}->{family}"] += 1
            if target_graphic_ref == parent_graphic_ref:
                parent_graphic_ref_matches += 1

        for _, range_left, range_bottom, range_right, range_top, raw_type in children:
            if raw_type != 0:
                continue
            range_left, range_right = sorted((range_left, range_right))
            range_bottom, range_top = sorted((range_bottom, range_top))
            direct_non_range: list[tuple[int, int, int, int, int, int]] = []
            direct_ranges: list[tuple[int, int, int, int, int, int]] = []
            for child in children:
                _, left, bottom, right, top, child_type = child
                left, right = sorted((left, right))
                bottom, top = sorted((bottom, top))
                if (
                    range_left - 2 <= left <= right <= range_right + 2
                    and range_bottom - 2 <= bottom <= top <= range_top + 2
                ):
                    if child_type == 0:
                        direct_ranges.append(child)
                    else:
                        direct_non_range.append(child)
            # The range itself is included by the containment check. Exclude
            # that self match so this remains a direct-member inventory.
            direct_ranges = [
                child
                for child in direct_ranges
                if (child[1], child[2], child[3], child[4])
                != (range_left, range_bottom, range_right, range_top)
            ]
            range_direct_non_range_member_counts[len(direct_non_range)] += 1
            range_direct_nested_range_member_counts[len(direct_ranges)] += 1
            if direct_non_range:
                member_left = min(min(child[1], child[3]) for child in direct_non_range)
                member_bottom = min(min(child[2], child[4]) for child in direct_non_range)
                member_right = max(max(child[1], child[3]) for child in direct_non_range)
                member_top = max(max(child[2], child[4]) for child in direct_non_range)
                if (range_left, range_bottom, range_right, range_top) == (
                    member_left,
                    member_bottom,
                    member_right,
                    member_top,
                ):
                    range_exact_direct_non_range_envelope_count += 1

    linked_child_count = sum(linked_families.values())
    return {
        "layout": "bounded-7b-composite-child-to-same-sheet-graphic-backlinks",
        "composite_count": composite_count,
        "child_count": sum(raw_child_types.values()),
        "raw_child_type_counts": {str(value): count for value, count in sorted(raw_child_types.items())},
        "range_child_count_type_0": raw_child_types[0],
        "linked_child_count": linked_child_count,
        "linked_target_family_counts": dict(sorted(linked_families.items())),
        "linked_target_family_counts_by_raw_type": dict(sorted(linked_by_raw_type.items())),
        "parent_graphic_ref_exact_match_count": parent_graphic_ref_matches,
        "range_direct_non_range_member_count_distribution": {
            str(value): count for value, count in sorted(range_direct_non_range_member_counts.items())
        },
        "range_direct_nested_range_member_count_distribution": {
            str(value): count for value, count in sorted(range_direct_nested_range_member_counts.items())
        },
        "range_exact_direct_non_range_envelope_count": range_exact_direct_non_range_envelope_count,
        "unlinked_non_range_child_type_counts": {
            str(value): count for value, count in sorted(unlinked_non_range_types.items())
        },
        "semantic_limit": (
            "a resolved child is a same-Sheet graphic backlink. Raw child type and local bounds are not a "
            "component class, universal geometry kind, or replacement coordinate system for the linked primitive"
        ),
    }


def parse_59_2b_page_layer_bindings(data: bytes) -> list[dict[str, object]]:
    """Decode bounded 43-byte ellipse-like records with centre and radius."""

    records: list[dict[str, object]] = []
    signature = b"\x59\x00\x2b\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 49 > len(data) or struct.unpack_from("<I", data, offset + 2)[0] != 43:
            continue
        x, y, radius = struct.unpack_from("<3d", data, offset + 24)
        if not (-0.03 <= x <= 1.05 and -0.03 <= y <= 1.05 and 0 < radius < 0.1):
            continue
        records.append(
            {
                "offset": offset,
                "primitive_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "graphic_ref": struct.unpack_from("<I", data, offset + 10)[0],
                "page_layer_ref": struct.unpack_from("<I", data, offset + 14)[0],
                "style_ref": struct.unpack_from("<I", data, offset + 20)[0],
                "x": x,
                "y": y,
                "radius": radius,
            }
        )
    return records


def parse_61_pipe_arc_records(data: bytes) -> list[dict[str, object]]:
    """Decode the fixed-size circular arc family headed by ``0x0061``.

    The five doubles are centre x/y, radius, absolute start angle and absolute
    end angle. The final double is verified as an end angle, not a sweep.
    """

    records: list[dict[str, object]] = []
    signature = b"\x61\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 65 > len(data) or struct.unpack_from("<I", data, offset + 2)[0] != 59:
            continue
        center_x, center_y, radius, start_angle, end_angle = struct.unpack_from("<5d", data, offset + 24)
        if not (
            0 <= center_x <= 1
            and 0 <= center_y <= 1
            and 0 < radius < 0.1
            and all(math.isfinite(value) for value in (start_angle, end_angle))
        ):
            continue
        records.append(
            {
                "offset": offset,
                "primitive_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "graphic_ref": struct.unpack_from("<I", data, offset + 10)[0],
                "page_layer_ref": struct.unpack_from("<I", data, offset + 14)[0],
                "style_ref": struct.unpack_from("<I", data, offset + 20)[0],
                "center": [center_x, center_y],
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
            }
        )
    return records


def parse_13_63_circle_geometry(data: bytes) -> list[dict[str, object]]:
    """Decode bounded ``0x0013/0x0063`` single-circle companion records.

    The fixed 99-byte payload contains centre/radius/start/end angles, a
    repeated centre anchor, then one relation-209 child ref to the owning
    ``0x59/0x2B`` ellipse-like primitive.
    """

    records: list[dict[str, object]] = []
    signature = b"\x13\x00\x63\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if (
            offset + 105 > len(data)
            or struct.unpack_from("<I", data, offset + 2)[0] != 99
            or data[offset + 34] != 0x73
            or data[offset + 75] != 1
            or data[offset + 92] != 1
            or struct.unpack_from("<I", data, offset + 93)[0] != 1
            or struct.unpack_from("<H", data, offset + 101)[0] != 209
            or struct.unpack_from("<H", data, offset + 103)[0] != 5
        ):
            continue
        center_x, center_y, radius, start_angle, end_angle = struct.unpack_from("<5d", data, offset + 35)
        if not (
            0 <= center_x <= 1
            and 0 <= center_y <= 1
            and 0 < radius < 0.1
            and all(math.isfinite(value) for value in (start_angle, end_angle))
        ):
            continue
        anchor_x, anchor_y = struct.unpack_from("<2d", data, offset + 76)
        if not (
            math.isfinite(anchor_x)
            and math.isfinite(anchor_y)
            and abs(anchor_x - center_x) < 1e-12
            and abs(anchor_y - center_y) < 1e-12
        ):
            continue
        records.append(
            {
                "offset": offset,
                "primitive_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "graphic_ref": struct.unpack_from("<I", data, offset + 10)[0],
                "page_layer_ref": struct.unpack_from("<I", data, offset + 14)[0],
                "style_ref": struct.unpack_from("<I", data, offset + 20)[0],
                "center": [center_x, center_y],
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
                "anchor": [anchor_x, anchor_y],
                "member_child_ref": struct.unpack_from("<I", data, offset + 97)[0],
                "member_relation_code": 209,
                "member_flags": 5,
            }
        )
    return records


def parse_sheet221_template_special_records(data: bytes) -> dict[str, list[dict[str, object]]]:
    """Decode bounded Sheet221 bitmap wrappers and page-template paths.

    Sheet221 has its own physical-template header before the same normalized
    point-pair payload shape used by some StyleCluster resources. The 0x0084
    form is therefore a page-scale closed path, not a component vector and not
    interchangeable with StyleCluster-local 0x0084 symbol resources.
    """

    result: dict[str, list[dict[str, object]]] = {
        "bitmap_placement_wrappers_3d": [],
        "page_container_84": [],
    }
    for signature, record_length, key in (
        (b"\x3d\x00\xea\x00\x00\x00", 234, "bitmap_placement_wrappers_3d"),
        (b"\x84\x00\x68\x00\x00\x00", 104, "page_container_84"),
    ):
        for match in re.finditer(re.escape(signature), data):
            offset = match.start()
            if offset + 6 + record_length > len(data):
                continue
            record: dict[str, object] = {
                "offset": offset,
                "primitive_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "sheet_ref": struct.unpack_from("<I", data, offset + 10)[0],
                "page_layer_ref": struct.unpack_from("<I", data, offset + 14)[0],
                "header_field_20": struct.unpack_from("<I", data, offset + 20)[0],
            }
            if key == "bitmap_placement_wrappers_3d":
                record["jsite_resource_id"] = struct.unpack_from("<I", data, offset + 162)[0]
                origin_x, origin_y = struct.unpack_from("<2d", data, offset + 42)
                width, height = struct.unpack_from("<2d", data, offset + 82)
                record["placement_origin"] = [origin_x, origin_y]
                record["placement_size"] = [width, height]
                record["placement_bbox"] = [origin_x, origin_y, origin_x + width, origin_y + height]
                record["scale_raw"] = struct.unpack_from("<d", data, offset + 66)[0]
                record["placement_origin_repeat"] = list(struct.unpack_from("<2d", data, offset + 122))
                record["placement_width_repeat"] = struct.unpack_from("<d", data, offset + 138)[0]
                record["placement_inverse_aspect_ratio"] = struct.unpack_from("<d", data, offset + 154)[0]
                record["affine_matrix"] = list(struct.unpack_from("<4d", data, offset + 170))
                record["trailing_scale_repeat"] = struct.unpack_from("<d", data, offset + 218)[0]
                record["placement_geometry_reconciled"] = (
                    abs(origin_x - float(record["placement_origin_repeat"][0])) < 1e-12
                    and abs(origin_y - float(record["placement_origin_repeat"][1])) < 1e-12
                    and abs(width - float(record["placement_width_repeat"])) < 1e-12
                    and width != 0
                    and abs(height / width - float(record["placement_inverse_aspect_ratio"])) < 1e-12
                    and record["affine_matrix"] == [1.0, 0.0, 0.0, 1.0]
                    and abs(float(record["scale_raw"]) - float(record["trailing_scale_repeat"])) < 1e-12
                )
            else:
                points = [
                    list(struct.unpack_from("<2d", data, point_offset))
                    for point_offset in range(offset + 30, offset + 6 + record_length, 16)
                ]
                if (
                    len(points) != 5
                    or points[0] != points[-1]
                    or not all(
                        math.isfinite(value) and -0.01 <= value <= 1.01
                        for point in points
                        for value in point
                    )
                ):
                    continue
                record.update(
                    {
                        "local_style_or_flags_raw": struct.unpack_from("<I", data, offset + 24)[0],
                        "path_flags_hex": f"0x{struct.unpack_from('<H', data, offset + 28)[0]:04X}",
                        "points": points,
                        "closed": True,
                        "coordinate_space": "Sheet221-template-normalized",
                    }
                )
            result[key].append(record)
    return result


def parse_sheet_3d_placement_wrappers(data: bytes) -> list[dict[str, object]]:
    """Decode the bounded 234-byte ``0x003D`` page OLE-placement wrapper.

    The wrapper occurs in Sheet221 for embedded title-block bitmaps and also
    on physical Sheets for the contentless JSite559 placement. Its placement
    The placement origin/size are directly stored.  Several redundant slots
    are also now cross-validated: the repeated origin/width, inverse aspect
    ratio, identity affine matrix, and a repeated raw scale value.  The
    engineering meaning of the raw scale and trailing origin remains open.
    """

    records: list[dict[str, object]] = []
    signature = b"\x3d\x00\xea\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 240 > len(data):
            continue
        origin_x, origin_y = struct.unpack_from("<2d", data, offset + 42)
        width, height = struct.unpack_from("<2d", data, offset + 82)
        origin_repeat = list(struct.unpack_from("<2d", data, offset + 122))
        width_repeat = struct.unpack_from("<d", data, offset + 138)[0]
        inverse_aspect_ratio = struct.unpack_from("<d", data, offset + 154)[0]
        affine_matrix = list(struct.unpack_from("<4d", data, offset + 170))
        scale_raw = struct.unpack_from("<d", data, offset + 66)[0]
        trailing_scale_repeat = struct.unpack_from("<d", data, offset + 218)[0]
        records.append(
            {
                "offset": offset,
                "record_length": 234,
                "primitive_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "sheet_ref": struct.unpack_from("<I", data, offset + 10)[0],
                "page_layer_ref": struct.unpack_from("<I", data, offset + 14)[0],
                "header_field_20": struct.unpack_from("<I", data, offset + 20)[0],
                "jsite_resource_id": struct.unpack_from("<I", data, offset + 162)[0],
                "placement_origin": [origin_x, origin_y],
                "placement_size": [width, height],
                "scale_raw": scale_raw,
                "placement_origin_repeat": origin_repeat,
                "placement_width_repeat": width_repeat,
                "placement_inverse_aspect_ratio": inverse_aspect_ratio,
                "affine_matrix": affine_matrix,
                "trailing_scale_repeat": trailing_scale_repeat,
                "placement_geometry_reconciled": (
                    abs(origin_x - float(origin_repeat[0])) < 1e-12
                    and abs(origin_y - float(origin_repeat[1])) < 1e-12
                    and abs(width - width_repeat) < 1e-12
                    and width != 0
                    and abs(height / width - inverse_aspect_ratio) < 1e-12
                    and affine_matrix == [1.0, 0.0, 0.0, 1.0]
                    and abs(scale_raw - trailing_scale_repeat) < 1e-12
                ),
            }
        )
        records[-1]["placement_bbox"] = [
            float(records[-1]["placement_origin"][0]),
            float(records[-1]["placement_origin"][1]),
            float(records[-1]["placement_origin"][0]) + float(records[-1]["placement_size"][0]),
            float(records[-1]["placement_origin"][1]) + float(records[-1]["placement_size"][1]),
        ]
    return records


def attach_3d_bitmap_scale_evidence(wrapper: dict[str, object], resource: dict[str, object]) -> None:
    """Reconcile a 0x003D physical scale with a self-identifying BMP DIB."""

    required = (
        "bmp_pixel_width",
        "bmp_pixel_height",
        "bmp_x_pixels_per_meter",
        "bmp_y_pixels_per_meter",
    )
    if not all(key in resource for key in required):
        return
    x_pixels_per_meter = float(resource["bmp_x_pixels_per_meter"])
    y_pixels_per_meter = float(resource["bmp_y_pixels_per_meter"])
    if x_pixels_per_meter <= 0 or y_pixels_per_meter <= 0:
        return
    native_width = float(resource["bmp_pixel_width"]) / x_pixels_per_meter
    native_height = float(resource["bmp_pixel_height"]) / y_pixels_per_meter
    scale = float(wrapper["scale_raw"])
    expected_size = [native_width * scale, native_height * scale]
    placement_size = [float(value) for value in wrapper["placement_size"]]
    wrapper["bmp_native_size_m"] = [native_width, native_height]
    wrapper["bmp_scale_expected_placement_size"] = expected_size
    wrapper["bmp_physical_scale_reconciled"] = all(
        # Sheet coordinates retain an exporter floating-point representation,
        # while DIB density is an integer pixels-per-metre field.
        abs(actual - expected) / max(abs(expected), 1e-15) < 1e-4
        for actual, expected in zip(placement_size, expected_size)
    )


def link_sheet_3d_resource_descriptors(
    wrappers: list[dict[str, object]], jsite_resources: list[dict[str, object]],
) -> None:
    """Attach bounded JSite resource descriptors to generic 0x003D wrappers."""

    by_id = {int(record["resource_id"]): record for record in jsite_resources}
    for wrapper in wrappers:
        resource = by_id.get(int(wrapper["jsite_resource_id"]))
        if resource is None:
            continue
        wrapper["jsite_resource_descriptor"] = {
            key: resource[key]
            for key in (
                "contents_kind",
                "bmp_pixel_width",
                "bmp_pixel_height",
                "bmp_bits_per_pixel",
                "bmp_x_pixels_per_meter",
                "bmp_y_pixels_per_meter",
                "jproperties_layout",
                "jproperties_property_code",
                "jproperties_utf16_value",
            )
            if key in resource
        }
        attach_3d_bitmap_scale_evidence(wrapper, resource)


def link_contentless_jsite_sheet_templates(
    wrappers: list[dict[str, object]],
    jsite_resources: list[dict[str, object]],
    streams: dict[str, bytes],
) -> None:
    """Resolve contentless OLES resources that explicitly name a Sheet stream.

    JSite559 has no bitmap ``CONTENTS`` payload. Its bounded OLES property is
    the decimal string ``221``, and a real ``Sheet221`` stream is present in
    the same document. This is a sheet-template route carried by a 0x3D page
    placement, distinct from an embedded bitmap placement.
    """

    by_id = {int(record["resource_id"]): record for record in jsite_resources}
    for wrapper in wrappers:
        resource = by_id.get(int(wrapper["jsite_resource_id"]))
        if resource is None or resource.get("contents_kind") != "no-embedded-contents":
            continue
        value = resource.get("jproperties_utf16_value")
        if not isinstance(value, str) or not value.isdecimal():
            continue
        sheet_stream = f"Sheet{value}"
        if sheet_stream not in streams:
            continue
        wrapper["contentless_jsite_template_sheet_stream"] = sheet_stream
        wrapper["contentless_jsite_template_reference_validated"] = True


def link_sheet221_bitmap_resource_descriptors(
    template_special_records: dict[str, list[dict[str, object]]],
    jsite_resources: list[dict[str, object]],
) -> None:
    """Attach self-identifying JSite payload metadata to 0x003D wrappers."""

    by_id = {int(record["resource_id"]): record for record in jsite_resources}
    for wrapper in template_special_records["bitmap_placement_wrappers_3d"]:
        resource_id = int(wrapper["jsite_resource_id"])
        resource = by_id.get(resource_id)
        if resource is None:
            continue
        wrapper["jsite_resource_descriptor"] = {
            key: resource[key]
            for key in (
                "contents_kind",
                "bmp_pixel_width",
                "bmp_pixel_height",
                "bmp_bits_per_pixel",
                "bmp_x_pixels_per_meter",
                "bmp_y_pixels_per_meter",
                "compobj_printable_strings",
            )
            if key in resource
        }
        attach_3d_bitmap_scale_evidence(wrapper, resource)


def parse_sheet221_template_text_records(data: bytes) -> dict[str, list[dict[str, object]]]:
    """Decode bounded Sheet221 text records without treating XML as visible text.

    The ordinary ``0x004d`` form stores visible UTF-16 text at ``+30`` and a
    four-double transform after it.  Revision fields retain the same record
    header and layer/style references, but insert a small opaque prefix before
    a UTF-16 XML binding expression.  The latter describes a value supplied by
    the Revision stream, so it must be reported as a binding rather than drawn
    as literal XML.
    """

    result: dict[str, list[dict[str, object]]] = {
        "visible_text_records": [],
        "revision_binding_records": [],
    }
    xml_start = b"<\x00?\x00x\x00m\x00l\x00"
    xml_end = b"<\x00/\x00b\x00o\x00d\x00y\x00>\x00"
    for offset in range(0, len(data) - 66, 2):
        if data[offset : offset + 2] != b"\x4d\x00":
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        end = offset + 6 + record_length
        if not 80 <= record_length <= 1000 or end > len(data):
            continue
        common: dict[str, object] = {
            "offset": offset,
            "record_length": record_length,
            "child_ref": struct.unpack_from("<I", data, offset + 6)[0],
            "sheet_ref": struct.unpack_from("<I", data, offset + 10)[0],
            "page_layer_ref": struct.unpack_from("<I", data, offset + 14)[0],
            "style_ref": struct.unpack_from("<I", data, offset + 20)[0],
        }
        character_count = struct.unpack_from("<H", data, offset + 28)[0]
        text_start = offset + 30
        text_end = text_start + 2 * character_count
        if record_length == 60 + 2 * character_count and text_end + 32 <= end:
            try:
                text = data[text_start:text_end].decode("utf-16le")
            except UnicodeDecodeError:
                text = ""
            x, y, direction_x, direction_y = struct.unpack_from("<4d", data, text_end)
            if (
                text
                and all(character.isprintable() for character in text)
                and 0 <= x <= 1
                and 0 <= y <= 1
                # Template note records use a text scale (for example 0.001),
                # whereas Revision bindings use a unit direction. Preserve
                # both without claiming their matrix semantics are identical.
                and 1e-6 <= (direction_x * direction_x + direction_y * direction_y) ** 0.5 <= 1.2
            ):
                result["visible_text_records"].append(
                    {
                        **common,
                        "text": text,
                        "character_count": character_count,
                        "x": x,
                        "y": y,
                        "direction": [direction_x, direction_y],
                    }
                )
            continue

        # A bounded UTF-16 XML expression is the Revision field-definition
        # subtype. Locate its own terminator before scanning only the record
        # tail for a normalized Shape2D transform.
        start = data.find(xml_start, text_start, end)
        if start == -1:
            continue
        finish = data.find(xml_end, start, end)
        if finish == -1:
            continue
        finish += len(xml_end)
        try:
            expression = data[start:finish].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        binding_attributes: dict[str, str] = {}
        try:
            binding_node = ET.fromstring(expression).find(".//intstgxml")
        except ET.ParseError:
            binding_node = None
        if binding_node is not None:
            binding_attributes = {
                key: value for key, value in binding_node.attrib.items()
                if key in {"stream", "select", "alt"}
            }
        transform: list[float] | None = None
        transform_offset: int | None = None
        for candidate in range(finish, end - 31, 4):
            x, y, direction_x, direction_y = struct.unpack_from("<4d", data, candidate)
            if (
                0 <= x <= 1
                and 0 <= y <= 1
                and 1e-6 <= (direction_x * direction_x + direction_y * direction_y) ** 0.5 <= 1.2
            ):
                transform = [x, y, direction_x, direction_y]
                transform_offset = candidate - offset
                break
        if transform is not None:
            prefix = data[text_start:start]
            result["revision_binding_records"].append(
                {
                    **common,
                    "binding_expression": expression,
                    # Preserve the unclassified gap instead of treating it as
                    # text. The XML attributes are the proven field contract.
                    "binding_xml_offset": start - offset,
                    "binding_xml_prefix_hex": prefix.hex(),
                    "binding_xml_prefix_byte_length": len(prefix),
                    "binding_xml_prefix_u16le": (
                        list(struct.unpack("<" + "H" * (len(prefix) // 2), prefix))
                        if len(prefix) % 2 == 0 else None
                    ),
                    "binding_stream": binding_attributes.get("stream"),
                    "binding_select": binding_attributes.get("select"),
                    "binding_alt": binding_attributes.get("alt"),
                    "transform_offset": transform_offset,
                    "x": transform[0],
                    "y": transform[1],
                    "direction": transform[2:],
                }
            )
    return result


def parse_stylecluster_font_records(data: bytes) -> dict[str, object]:
    """Decode bounded ``StyleCluster`` font/size records headed by ``0x002c``.

    The record payload length is exactly ``70 + 2 * font_name_char_count``.
    It provides the actual Shape2D text style reference at ``+20``, a
    page-normalized font-size ratio at ``+48``, and a UTF-16 font name after
    the character count at ``+74``. This is a direct style-definition record,
    unlike nearby human-readable library labels such as ``Arial:0.005::-1``.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x2c\x00"), data):
        offset = match.start()
        if offset + 76 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        name_char_count = struct.unpack_from("<H", data, offset + 74)[0]
        end = offset + 6 + record_length
        if (
            not 72 <= record_length <= 200
            or record_length != 70 + 2 * name_char_count
            or end > len(data)
        ):
            continue
        try:
            font_name = data[offset + 76 : offset + 76 + 2 * name_char_count].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        style_ref = struct.unpack_from("<I", data, offset + 20)[0]
        size_ratio = struct.unpack_from("<d", data, offset + 48)[0]
        if (
            not font_name
            or not all(31 < ord(character) < 127 for character in font_name)
            or not 0 < style_ref <= 0xFFFF
            or not math.isfinite(size_ratio)
            or not 0.0001 <= size_ratio <= 0.02
        ):
            continue
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "style_ref": style_ref,
                "font_size_ratio": size_ratio,
                "font_name": font_name,
            }
        )
    return {
        "layout": "validated-0x002c-font-style-record",
        "record_count": len(records),
        "font_family_counts": dict(Counter(str(record["font_name"]) for record in records)),
        "records": records,
    }


def parse_stylecluster_text_style_links(
    data: bytes, font_records: dict[str, object]
) -> dict[str, object]:
    """Resolve the observed ``0x002d`` text-style to font-style bridge.

    The fixed 90-byte record has the rendered text style id at ``+20`` and
    its referenced ``0x002c`` font-style id at ``+44``. It is the required
    intermediate link for physical Sheet ``0x004d`` text styles such as
    ``0x00F4 -> 0x00E3 -> Courier New / 0.005``.
    """

    fonts_by_style = {
        int(record["style_ref"]): record
        for record in font_records["records"]
    }
    records: list[dict[str, object]] = []
    signature = b"\x2d\x00\x5a\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 96 > len(data):
            continue
        style_ref = struct.unpack_from("<I", data, offset + 20)[0]
        font_style_ref = struct.unpack_from("<I", data, offset + 44)[0]
        if not 0 < style_ref <= 0xFFFF or not 0 < font_style_ref <= 0xFFFF:
            continue
        font = fonts_by_style.get(font_style_ref)
        record: dict[str, object] = {
            "offset": offset,
            "record_length": 90,
            "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
            "style_ref": style_ref,
            "font_style_ref": font_style_ref,
            "font_resolved": font is not None,
        }
        if font is not None:
            record["font_name"] = font["font_name"]
            record["font_size_ratio"] = font["font_size_ratio"]
        records.append(record)
    return {
        "layout": "validated-0x002d-text-style-to-font-style-link",
        "record_count": len(records),
        "font_resolved_record_count": sum(bool(record["font_resolved"]) for record in records),
        "records": records,
    }


def parse_stylecluster_2e_style_records(data: bytes) -> dict[str, object]:
    """Decode the bounded structural fields of ``StyleCluster 0x002E``.

    The record's category/flags/link slots are stable framing fields, but the
    business meaning of their numeric values has not yet been independently
    established.  The 58-byte form is the exception: its terminal u32 is a
    validated reference to a bounded ``0x002F`` dash-pattern style.
    """

    records: list[dict[str, int]] = []
    for match in re.finditer(re.escape(b"\x2e\x00"), data):
        offset = match.start()
        if offset + 26 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        if record_length not in {54, 58} or offset + 6 + record_length > len(data):
            continue
        tail_u32 = struct.unpack_from("<I", data, offset + 2 + record_length)[0]
        line_width_ratio = struct.unpack_from("<d", data, offset + 40)[0]
        if not math.isfinite(line_width_ratio) or not 0 <= line_width_ratio <= 0.1:
            continue
        record = {
            "offset": offset,
            "record_length": record_length,
            "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
            "category_raw": struct.unpack_from("<H", data, offset + 18)[0],
            "style_ref": struct.unpack_from("<I", data, offset + 20)[0],
            "flags_raw": struct.unpack_from("<H", data, offset + 32)[0],
            "auxiliary_u32_raw": struct.unpack_from("<I", data, offset + 34)[0],
            "line_width_ratio": line_width_ratio,
            "tail_u32_hex": f"0x{tail_u32:08X}",
        }
        if record_length == 54 and tail_u32 in {0x00000000, 0x00FFFFFF, 0x000000FF, 0x00FF0000}:
            record["rgb24_hex"] = f"#{tail_u32 & 0xFFFFFF:06X}"
        records.append(record)
    return {"layout": "bounded-0x002e-style-registration-record", "record_count": len(records), "records": records,
            "semantic_limit": "style_ref and line_width_ratio are direct Sheet line-style registry fields. category_raw, flags_raw, and auxiliary_u32_raw are structurally bounded but their business semantics are not yet named; this is not yet a general line-type, fill, or font decoder"}


def parse_stylecluster_12_font_resources(data: bytes) -> dict[str, object]:
    """Inventory bounded 0x0012 font-directory/resource records."""
    records = []
    for match in re.finditer(re.escape(b"\x12\x00"), data):
        offset = match.start(); length = struct.unpack_from("<I", data, offset + 2)[0] if offset + 6 <= len(data) else 0
        if length not in {224, 225, 512, 736, 737} or offset + 6 + length > len(data): continue
        names = [m.group().decode("utf-16le") for m in re.finditer(rb"(?:(?:[ -~]\x00){4,})", data[offset:offset + 6 + length])]
        records.append({"offset": offset, "record_length": length, "object_ref": struct.unpack_from("<I", data, offset + 6)[0], "font_name_fragments": names})
    return {"layout": "bounded-0x0012-font-directory-resource", "record_count": len(records), "records": records,
            "semantic_limit": "font directory/fallback resource only; not a direct Sheet style, coordinate, or geometry record"}


def parse_stylecluster_named_style_catalog_entries(data: bytes) -> dict[str, object]:
    """Decode bounded StyleCluster named line-style catalog entries.

    Each entry has a 30-byte header followed by exactly ``name_count`` UTF-16
    code units. The ``0x0043`` byte pair occurs as the letter ``C`` in the
    name, not as a binary record marker. Catalog-code semantics remain open,
    but the object reference can be cross-checked against bounded line styles.
    """

    records = []
    for name_offset in range(30, len(data)):
        offset = name_offset - 30
        if offset < 0:
            continue
        reserved, catalog_code, object_ref, field_1, field_2, field_3, field_4, name_count = struct.unpack_from(
            "<6IHI", data, offset
        )
        name_end = name_offset + 2 * name_count
        if (
            reserved != 0
            or field_1 != 0
            or field_2 != 0
            or not 1 <= field_3 <= 32
            or field_4 != 0
            or not 4 <= name_count <= 64
            or name_end > len(data)
        ):
            continue
        try:
            name = data[name_offset:name_end].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        if not name or not all(31 < ord(character) < 127 for character in name):
            continue
        records.append(
            {
                "offset": offset,
                "record_length": 30 + 2 * name_count,
                "catalog_code": catalog_code,
                "object_ref": object_ref,
                "catalog_type": field_3,
                "catalog_name": name,
            }
        )
    return {
        "layout": "bounded-stylecluster-named-line-style-catalog-entry",
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "the raw 0x0043 byte sequence is the letter C, not a record marker. Catalog-code semantics and the "
            "meaning of LW/C tokens and Dash payloads remain unknown; do not use a name alone as a Sheet width, RGB, geometry, or rendering rule"
        ),
    }


def parse_stylecluster_2f_dash_patterns(data: bytes) -> dict[str, object]:
    """Decode fixed 66-byte style-pattern records linked by ``Dash``.

    The two terminal doubles and their count are structurally stable. Their
    sign/scale-to-SVG interpretation is intentionally retained as raw ratios
    until a drawing rule is independently verified.
    """

    records: list[dict[str, int]] = []
    for match in re.finditer(re.escape(b"\x2f\x00"), data):
        offset = match.start()
        if offset + 72 > len(data) or struct.unpack_from("<I", data, offset + 2)[0] != 66:
            continue
        scale_ratio = struct.unpack_from("<d", data, offset + 40)[0]
        segment_1_ratio = struct.unpack_from("<d", data, offset + 56)[0]
        segment_2_ratio = struct.unpack_from("<d", data, offset + 64)[0]
        if not all(math.isfinite(value) and abs(value) <= 1 for value in (scale_ratio, segment_1_ratio, segment_2_ratio)):
            continue
        records.append({
            "offset": offset,
            "record_length": 66,
            "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
            "style_ref": struct.unpack_from("<I", data, offset + 20)[0],
            "scale_ratio": scale_ratio,
            "segment_count": struct.unpack_from("<H", data, offset + 54)[0],
            "segment_1_ratio": segment_1_ratio,
            "segment_2_ratio": segment_2_ratio,
        })
    return {
        "layout": "bounded-0x002f-dash-pattern-record",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "object/style linkage and raw ratio slots are direct; signs and scale are not yet a decoded SVG dash-array formula",
    }


def known_style_primitive_occurrences(
    streams: dict[str, bytes], target_style_refs: set[int]
) -> Counter[tuple[str, str, int]]:
    """Return direct style uses within independently decoded primitives."""

    primitive_families = {
        "18_32_line": parse_18_32_layer_bindings,
        "4d_text": parse_4d_text_layer_bindings,
        "59_circle": parse_59_2b_page_layer_bindings,
        "61_arc": parse_61_pipe_arc_records,
        "13_63_circle_companion": parse_13_63_circle_geometry,
    }
    occurrences: Counter[tuple[str, str, int]] = Counter()
    for sheet_name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", sheet_name):
            continue
        for family, parser in primitive_families.items():
            for primitive in parser(data):
                style_ref = int(primitive["style_ref"])
                if style_ref in target_style_refs:
                    occurrences[(sheet_name, family, style_ref)] += 1
    return occurrences


def summarize_stylecluster_2e_category_usage(
    streams: dict[str, bytes], line_style_records: list[dict[str, object]]
) -> dict[str, object]:
    """Report direct decoded primitive use by raw ``0x002E`` category.

    The category is deliberately retained as raw metadata. This summary can
    establish that a category is or is not used by the independently decoded
    physical-Sheet primitive families, but it cannot name the category as a
    fill, visibility state, cap, or business component type.
    """

    styles_by_category: dict[int, set[int]] = {}
    for record in line_style_records:
        styles_by_category.setdefault(int(record["category_raw"]), set()).add(
            int(record["style_ref"])
        )
    records: list[dict[str, object]] = []
    for category, style_refs in sorted(styles_by_category.items()):
        occurrences = known_style_primitive_occurrences(streams, style_refs)
        family_counts: Counter[str] = Counter()
        for (_, family, _), count in occurrences.items():
            family_counts[family] += count
        records.append(
            {
                "category_raw": category,
                "registered_style_ref_count": len(style_refs),
                "registered_style_refs": sorted(style_refs),
                "validated_primitive_use_count": sum(occurrences.values()),
                "validated_primitive_family_counts": dict(sorted(family_counts.items())),
            }
        )
    return {
        "layout": "0x002e-raw-category-to-validated-sheet-primitive-use",
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "a zero use is limited to currently decoded physical-Sheet primitive families; it does not prove "
            "the category is invisible, unused by every unparsed family, a fill, a visibility flag, or a component type"
        ),
    }


def summarize_stylecluster_local_resource_sheet_references(
    streams: dict[str, bytes],
    resource_families: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    """Check direct known Sheet reference slots for local StyleCluster resources.

    This deliberately tests only independently bounded Sheet fields. A zero
    result proves that a resource is not a direct instance in those fields; it
    does not prove that no future, unparsed instance mechanism exists.
    """

    sheet_reference_ids: set[int] = set()
    sheet_count = 0
    for sheet_name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", sheet_name) or len(data) <= 1024:
            continue
        sheet_count += 1
        for parser, fields in (
            (parse_18_32_layer_bindings, ("child_ref", "graphic_ref", "page_layer_ref", "style_ref")),
            (parse_4d_text_layer_bindings, ("child_ref", "secondary_ref", "page_layer_ref", "style_ref")),
            (parse_61_pipe_arc_records, ("primitive_ref", "graphic_ref", "page_layer_ref", "style_ref")),
            (parse_59_2b_page_layer_bindings, ("primitive_ref", "graphic_ref", "page_layer_ref", "style_ref")),
            (parse_13_63_circle_geometry, ("primitive_ref", "graphic_ref", "page_layer_ref", "style_ref")),
        ):
            for record in parser(data):
                sheet_reference_ids.update(int(record[field]) for field in fields)
        for composite in parse_7b_composite_headers(data):
            sheet_reference_ids.add(int(composite["composite_ref"]))
            sheet_reference_ids.update(int(ref) for ref in composite["child_refs"])

    records: list[dict[str, object]] = []
    for family, resources in sorted(resource_families.items()):
        resource_refs = {int(record["object_ref"]) for record in resources}
        direct_matches = sorted(resource_refs & sheet_reference_ids)
        records.append(
            {
                "family": family,
                "resource_object_count": len(resource_refs),
                "known_sheet_reference_match_count": len(direct_matches),
                "known_sheet_reference_matches": direct_matches,
            }
        )
    return {
        "layout": "cross-reference-validated-Sheet-fields-to-StyleCluster-local-object-refs",
        "physical_sheet_count": sheet_count,
        "known_sheet_reference_id_count": len(sheet_reference_ids),
        "records": records,
        "semantic_limit": (
            "zero matches means no direct instance occurs in the currently decoded Sheet fields; "
            "it does not rule out an unparsed instance mechanism"
        ),
    }


def summarize_known_dash_pattern_usage(
    streams: dict[str, bytes], line_style_records: list[dict[str, object]], dash_pattern_records: list[dict[str, object]]
) -> dict[str, object]:
    """Report uses of dash-linked styles in independently decoded primitives.

    This deliberately scans only primitive families whose style field is
    already validated: 0x0018/0x0032 lines, 0x0059 circles, 0x0061 arcs, and
    0x0013/0x0063 circle companions. An absent match is not proof that a
    pattern is unused by every unknown Shape2D record family.
    """

    line_styles_by_dash_ref: dict[int, list[int]] = {}
    for line_style in line_style_records:
        if int(line_style["record_length"]) != 58:
            continue
        dash_ref = int(str(line_style["tail_u32_hex"]), 16)
        line_styles_by_dash_ref.setdefault(dash_ref, []).append(int(line_style["style_ref"]))

    records: list[dict[str, object]] = []
    for dash_pattern in dash_pattern_records:
        dash_ref = int(dash_pattern["style_ref"])
        linked_line_styles = sorted(set(line_styles_by_dash_ref.get(dash_ref, [])))
        occurrences = known_style_primitive_occurrences(streams, set(linked_line_styles))
        records.append(
            {
                "dash_pattern_style_ref": dash_ref,
                "linked_line_style_refs": linked_line_styles,
                "validated_primitive_use_count": sum(occurrences.values()),
                "validated_primitive_uses": [
                    {
                        "sheet": sheet,
                        "primitive_family": family,
                        "line_style_ref": style_ref,
                        "count": count,
                    }
                    for (sheet, family, style_ref), count in sorted(occurrences.items())
                ],
            }
        )
    return {
        "layout": "dash-pattern-style-to-validated-sheet-primitive-use",
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "only independently decoded primitive families are covered; a zero count does not prove no use in "
            "unclassified Shape2D record families or establish an SVG dash-array formula"
        ),
    }


def summarize_known_fixed_style_usage(
    streams: dict[str, bytes], fixed_style_records: list[dict[str, object]]
) -> dict[str, object]:
    """Report direct primitive uses of the fixed 0x002A style directory."""

    fixed_refs = sorted({int(record["style_ref"]) for record in fixed_style_records})
    occurrences = known_style_primitive_occurrences(streams, set(fixed_refs))
    return {
        "layout": "fixed-style-to-validated-sheet-primitive-use",
        "fixed_style_refs": fixed_refs,
        "validated_primitive_use_count": sum(occurrences.values()),
        "validated_primitive_uses": [
            {
                "sheet": sheet,
                "primitive_family": family,
                "style_ref": style_ref,
                "count": count,
            }
            for (sheet, family, style_ref), count in sorted(occurrences.items())
        ],
        "semantic_limit": (
            "only independently decoded primitive families are covered; zero direct use does not prove the "
            "records have no default, container, or as-yet-unclassified use"
        ),
    }


def parse_stylecluster_2a_fixed_style_records(data: bytes) -> dict[str, object]:
    """Inventory the bounded 46-byte non-drawing StyleCluster style family."""

    records: list[dict[str, int]] = []
    signature = b"\x2a\x00\x2e\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 52 > len(data):
            continue
        records.append({
            "offset": offset,
            "record_length": 46,
            "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
            "style_ref": struct.unpack_from("<I", data, offset + 20)[0],
            "opaque_color_or_flags_hex": f"0x{struct.unpack_from('<I', data, offset + 34)[0]:08X}",
            "terminal_ratio": struct.unpack_from("<d", data, offset + 44)[0],
        })
    return {
        "layout": "bounded-0x002a-fixed-style-record",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "fixed style-directory record; opaque color/flags and terminal ratio are not a drawing or component rule",
    }


def parse_stylecluster_70_fixed_records(data: bytes) -> dict[str, object]:
    """Decode font-bearing local text-template resources headed by ``0x0070``.

    The payload is ``84 + 2 * font_name_char_count@86``. No text content is
    stored here, but its bounded UTF-16 font definition proves this family is
    a local text/template resource rather than a generic numeric constant.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x70\x00"), data):
        offset = match.start()
        if offset + 92 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        name_char_count = struct.unpack_from("<I", data, offset + 86)[0]
        end = offset + 6 + record_length
        if (
            not 1 <= name_char_count <= 64
            or record_length != 84 + 2 * name_char_count
            or end > len(data)
        ):
            continue
        try:
            font_name = data[offset + 90 : end].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        values = [struct.unpack_from("<d", data, offset + field_offset)[0] for field_offset in (18, 26, 34, 42, 74)]
        if (
            not all(math.isfinite(value) and abs(value) <= 2 for value in values)
            or not all(31 < ord(character) < 127 for character in font_name)
        ):
            continue
        records.append({
            "offset": offset,
            "record_length": record_length,
            "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
            "local_anchor_raw": values[:2],
            "scale_raw": values[2],
            "rotation_or_transform_raw": values[3],
            "font_size_ratio": values[4],
            "font_name": font_name,
        })
    return {
        "layout": "bounded-0x0070-font-bearing-local-text-template-resource",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "font definition is direct; anchor/scale/transform fields are local-template values and no text content or physical Sheet instance is proven",
    }


def parse_stylecluster_18_control_records(data: bytes) -> dict[str, object]:
    """Decode bounded local two-point line resources in StyleCluster.

    All 23 corpus records are children of 0x007C symbol groups. Their four
    doubles form two local points; several form closed rectangular outlines
    and others meet polygon endpoints, directly establishing line geometry.
    """

    records: list[dict[str, object]] = []
    signature = b"\x18\x00\x32\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 56 > len(data):
            continue
        values = [struct.unpack_from("<d", data, offset + field_offset)[0] for field_offset in (24, 32, 40, 48)]
        if not all(math.isfinite(value) and abs(value) <= 2 for value in values):
            continue
        records.append({
            "offset": offset,
            "record_length": 50,
            "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
            "start": values[:2],
            "end": values[2:],
            "control_values": values,
            "coordinate_space": "StyleCluster-local-template",
        })
    return {"layout": "fixed-0x0018-local-two-point-line-resource", "record_count": len(records), "records": records,
            "semantic_limit": "direct local line geometry only; no physical Sheet instance, line style, or component class has been proven"}


def parse_stylecluster_84_polygon_resources(data: bytes) -> dict[str, object]:
    """Decode bounded closed-polygon resource records.

    ``0x0084`` is a StyleCluster-local vector template, not a Sheet primitive.
    The verified 88/104-byte payload variants contain four/five point pairs
    beginning at ``+30``; the final point equals the first, which prevents
    UTF-16 text bytes from being accepted as paths. The points are normalized
    local coordinates and can only be drawn after a parent instance/transform
    is independently resolved.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x84\x00"), data):
        offset = match.start()
        if offset + 6 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        end = offset + 6 + record_length
        if record_length not in {88, 104} or end > len(data):
            continue
        points = [
            list(struct.unpack_from("<2d", data, point_offset))
            for point_offset in range(offset + 30, end, 16)
        ]
        if (
            len(points) not in {4, 5}
            or points[0] != points[-1]
            or not all(math.isfinite(value) and abs(value) <= 0.1 for point in points for value in point)
        ):
            continue
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "local_style_or_flags_raw": struct.unpack_from("<I", data, offset + 24)[0],
                "path_flags_hex": f"0x{struct.unpack_from('<H', data, offset + 28)[0]:04X}",
                "points": points,
                "closed": True,
            }
        )
    return {
        "layout": "fixed-0x0084-five-point-closed-polygon-resource",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "a local reusable polygon template only; its business symbol and any on-page placement remain unproven",
    }


def parse_stylecluster_7c_polygon_groups(data: bytes) -> dict[str, object]:
    """Decode bounded StyleCluster resource groups.

    The verified record size is ``16 + 4 * child_count``. Four two-member
    groups reference only polygons, while a seven-member group combines an
    ellipse, polygon paths, and other internal template resources.
    """

    records: list[dict[str, object]] = []
    for match in re.finditer(re.escape(b"\x7c\x00"), data):
        offset = match.start()
        if offset + 22 > len(data):
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        child_count = struct.unpack_from("<I", data, offset + 18)[0]
        end = offset + 6 + record_length
        if (
            not 2 <= child_count <= 32
            or record_length != 16 + 4 * child_count
            or end > len(data)
            or data[offset + 10 : offset + 18] != b"\x00" * 8
        ):
            continue
        child_refs = list(struct.unpack_from(f"<{child_count}I", data, offset + 22))
        if not all(0 < child_ref <= 0xFFFF for child_ref in child_refs):
            continue
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "child_count": child_count,
                "child_refs": child_refs,
            }
        )
    return {
        "layout": "bounded-0x007c-resource-member-group",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "a local resource composition only; no direct Sheet instance or component class has been proven",
    }


def parse_stylecluster_61_local_arc_resources(data: bytes) -> dict[str, object]:
    """Decode bounded local arc templates embedded in StyleCluster.

    This has the same five-double geometric shape as the independently
    validated Sheet arc family, but lives in the symbol library. Keep its
    coordinates local; never substitute them for Sheet coordinates.
    """

    records: list[dict[str, object]] = []
    signature = b"\x61\x00\x3b\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 65 > len(data) or data[offset + 10 : offset + 24] != b"\x00" * 14:
            continue
        center_x, center_y, radius, start_angle, end_angle = struct.unpack_from("<5d", data, offset + 24)
        if (
            not all(math.isfinite(value) and abs(value) <= 20 for value in (center_x, center_y, radius, start_angle, end_angle))
            or not 0 < radius <= 1
            or not -math.tau * 2 <= start_angle <= math.tau * 2
            or not -math.tau * 2 <= end_angle <= math.tau * 2
        ):
            continue
        records.append(
            {
                "offset": offset,
                "record_length": 59,
                "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "center": [center_x, center_y],
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
                "coordinate_space": "StyleCluster-local-template",
            }
        )
    return {
        "layout": "fixed-0x0061-local-arc-resource",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "local symbol-library arc geometry only; no direct Sheet placement or component class has been proven",
    }


def parse_stylecluster_59_local_ellipse_resources(data: bytes) -> dict[str, object]:
    """Decode the local 0x0059 circular template resource.

    Its tag/length match the physical Sheet circle family: center doubles at
    ``+24/+32``, radius at ``+40``, and a terminal flag at ``+48``.  The
    reference/layer/style fields are all zero here, which makes this a local
    symbol-library circle rather than a physical Sheet circle binding.
    """

    records: list[dict[str, object]] = []
    signature = b"\x59\x00\x2b\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 49 > len(data) or data[offset + 10 : offset + 32] != b"\x00" * 22:
            continue
        center_x, center_y, radius = struct.unpack_from("<3d", data, offset + 24)
        terminal_flag = data[offset + 48]
        if (
            not all(math.isfinite(value) and abs(value) <= 1 for value in (center_x, center_y, radius))
            or not 0 < radius <= 0.1
            or terminal_flag > 16
        ):
            continue
        records.append(
            {
                "offset": offset,
                "record_length": 43,
                "object_ref": struct.unpack_from("<I", data, offset + 6)[0],
                "center": [center_x, center_y],
                "radius": radius,
                "terminal_flag_raw": terminal_flag,
                "coordinate_space": "StyleCluster-local-template",
            }
        )
    return {
        "layout": "fixed-0x0059-local-circle-resource",
        "record_count": len(records),
        "records": records,
        "semantic_limit": "direct local circle geometry only; no physical Sheet instance or component class has been proven",
    }


def _stylecluster_name_backlinks(data: bytes, object_ref: int) -> list[str]:
    """Return bounded UTF-16 names immediately preceding an object reference.

    This is intentionally a backlink rather than a claim that the nearby
    bytes share the 0x001B record's layout. Shape2D stores these named catalog
    entries separately from their fixed resource objects.
    """

    reference = struct.pack("<I", object_ref)
    pattern = re.compile(rb"((?:(?:[ -~]\x00){4,}))\x00\x00.{6}" + re.escape(reference))
    names: set[str] = set()
    for match in pattern.finditer(data):
        try:
            name = match.group(1).decode("utf-16le")
        except UnicodeDecodeError:
            continue
        if name:
            names.add(name)
    return sorted(names)


def parse_stylecluster_1b_named_internal_style_records(data: bytes) -> dict[str, object]:
    """Inventory fixed Shape2D named internal-style resources.

    The record itself is exactly 202 payload bytes. Independent catalog
    backlinks label the common records ``Reference``, ``border`` and ``Office
    Automation``; rare records are explicitly named ``S3D_INTERNALSTYLE_*``.
    This establishes the family as style/template metadata, not Sheet geometry.
    Its category/subtype and scalar slots remain raw until their drawing
    semantics are separately demonstrated.
    """

    records: list[dict[str, object]] = []
    signature = b"\x1b\x00\xca\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        offset = match.start()
        if offset + 208 > len(data):
            continue
        object_ref = struct.unpack_from("<I", data, offset + 6)[0]
        category = struct.unpack_from("<I", data, offset + 26)[0]
        subtype = struct.unpack_from("<I", data, offset + 30)[0]
        scalar_slots = [struct.unpack_from("<d", data, offset + slot)[0] for slot in (96, 108, 116, 124, 132)]
        if (
            not 0 < object_ref <= 0xFFFF
            or not 1 <= category <= 3
            or not 1 <= subtype <= 2
            or not all(math.isfinite(value) and abs(value) <= 10 for value in scalar_slots)
        ):
            continue
        name_backlinks = _stylecluster_name_backlinks(data, object_ref)
        records.append(
            {
                "offset": offset,
                "record_length": 202,
                "object_ref": object_ref,
                "category_raw": category,
                "subtype_raw": subtype,
                "scalar_slots": scalar_slots,
                "name_backlinks": name_backlinks,
                "name_backlink_unambiguous": len(name_backlinks) == 1,
            }
        )
    return {
        "layout": "fixed-0x001b-named-internal-style-resource",
        "record_count": len(records),
        "named_record_count": sum(bool(record["name_backlinks"]) for record in records),
        "unambiguous_named_record_count": sum(bool(record["name_backlink_unambiguous"]) for record in records),
        "records": records,
        "semantic_limit": "name backlinks prove style/template catalog membership only; category, subtype, and scalar-slot drawing semantics are not yet decoded",
    }


def parse_stylecluster_zero_object_containers(data: bytes) -> dict[str, object]:
    """Inventory bounded tag-0 StyleCluster object-library containers.

    These records are a storage hierarchy around style objects, rather than
    visible geometry.  Their child payloads can themselves contain nested
    records, so this parser deliberately reports only the outer boundary and
    raw category rather than flattening overlapping bytes into false objects.
    """

    records: list[dict[str, object]] = []
    for offset in range(0, len(data) - 29, 2):
        if data[offset : offset + 2] != b"\x00\x00":
            continue
        record_length = struct.unpack_from("<I", data, offset + 2)[0]
        end = offset + 6 + record_length
        if (
            not 24 <= record_length <= 16384
            or end > len(data)
            or data[offset + 10 : offset + 18] != b"\x00" * 8
        ):
            continue
        object_ref = struct.unpack_from("<I", data, offset + 6)[0]
        category_raw = struct.unpack_from("<I", data, offset + 18)[0]
        if not 8192 <= object_ref <= 0xFFFF or not 0 <= category_raw <= 8:
            continue
        records.append(
            {
                "offset": offset,
                "record_length": record_length,
                "object_ref": object_ref,
                "category_raw": category_raw,
                "nested_payload_offset": offset + 30,
                "nested_payload_byte_length": max(0, end - (offset + 30)),
            }
        )
    for record in records:
        record_end = int(record["offset"]) + 6 + int(record["record_length"])
        parents = [
            candidate
            for candidate in records
            if int(candidate["offset"]) < int(record["offset"])
            and int(candidate["offset"]) + 6 + int(candidate["record_length"]) >= record_end
        ]
        parent = min(parents, key=lambda candidate: int(candidate["record_length"])) if parents else None
        record["parent_object_ref"] = int(parent["object_ref"]) if parent else None
    roots = [record for record in records if record["parent_object_ref"] is None]
    return {
        "layout": "bounded-tag-0-stylecluster-object-library-container",
        "record_count": len(records),
        "root_container_count": len(roots),
        "records": records,
        "semantic_limit": "storage/container hierarchy only; category and nested payload do not prove a visual primitive, component, or Sheet placement",
    }


def parse_jsites_list(data: bytes) -> dict[str, object]:
    """Decode the exact ``OLEM`` resource-id list without reading resources."""

    if len(data) < 8 or data[:4] != b"OLEM":
        raise ValueError("not an OLEM JSite list")
    count = struct.unpack_from("<I", data, 4)[0]
    if len(data) != 8 + 4 * count:
        raise ValueError("JSite count does not consume the source stream")
    return {
        "layout": "validated-OLEM-resource-id-list",
        "resource_ids": list(struct.unpack_from(f"<{count}I", data, 8)),
        "fully_consumed": True,
    }


def parse_appobject_dependency(data: bytes) -> dict[str, object]:
    """Extract the bounded UTF-16 provider path from the AppObject stream."""

    if len(data) < 24:
        raise ValueError("AppObject stream is too short")
    char_count = struct.unpack_from("<I", data, 20)[0]
    end = 24 + 2 * char_count
    if not 1 <= char_count <= 512 or end > len(data):
        raise ValueError("AppObject provider path is not bounded")
    try:
        provider_path = data[24:end].decode("utf-16le")
    except UnicodeDecodeError as error:
        raise ValueError("AppObject provider path is not UTF-16LE") from error
    # Shape2D's OLE stream can truncate the final UTF-16 terminator to one
    # zero padding byte. The declared UTF-16 payload itself is authoritative.
    if any(data[end:]):
        raise ValueError("AppObject provider path has nonzero trailing bytes")
    return {
        "layout": "validated-AppObject-provider-path",
        "class_id_hex": data[4:20].hex(),
        "provider_path": provider_path.rstrip("\x00"),
    }


def parse_docversion3_history(data: bytes) -> dict[str, object]:
    """Decode NUL-delimited Shape2D version-history quadruples."""

    parts = [part.decode("ascii") for part in data.split(b"\x00") if part]
    if not parts or len(parts) % 4:
        raise ValueError("DocVersion3 is not a complete version-history sequence")
    records = [
        {
            "module": parts[index],
            "version": parts[index + 1],
            "mode": parts[index + 2],
            "timestamp": parts[index + 3],
        }
        for index in range(0, len(parts), 4)
    ]
    return {
        "layout": "validated-NUL-delimited-version-history-quadruples",
        "record_count": len(records),
        "records": records,
    }


def classify_docversion2_profile(data: bytes) -> dict[str, object]:
    """Record the fixed legacy DocVersion2 profile without inventing fields."""

    return {
        "layout": "fixed-legacy-document-version-profile",
        "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "semantic_limit": "legacy version/compatibility metadata; internal integers are not validated records, references, or geometry",
    }


def parse_ole_summary_metadata(sha_path: Path, streams: dict[str, bytes]) -> dict[str, object]:
    """Read standard OLE summary properties as document provenance only."""

    required_streams = {"\x05SummaryInformation", "\x05DocumentSummaryInformation"}
    missing = sorted(required_streams.difference(streams))
    if missing:
        raise ValueError(f"missing OLE summary stream(s): {', '.join(missing)}")
    metadata = olefile.OleFileIO(str(sha_path)).get_metadata()

    def normalize(value: object) -> object:
        if isinstance(value, bytes):
            return value.decode("cp1252", errors="replace")
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    fields = (
        "title",
        "template",
        "author",
        "last_saved_by",
        "revision_number",
        "creating_application",
        "comments",
        "keywords",
        "category",
        "company",
        "codepage",
        "codepage_doc",
        "create_time",
        "last_saved_time",
    )
    return {
        "layout": "validated-standard-ole-summary-property-streams",
        "properties": {
            field: normalize(getattr(metadata, field)) for field in fields
        },
        "semantic_limit": "document/template provenance only; not a Shape2D Sheet, PSM hierarchy, UCI, component, font, coordinate, or rendering instruction",
    }


def parse_dynamic_attributes_metadata(data: bytes) -> dict[str, object]:
    """Decode the fixed non-geometry Dynamic Attributes registration header."""

    if len(data) != 28:
        raise ValueError("Dynamic Attributes Metadata has an unexpected length")
    signature, format_version, field_1, field_2, flags, tail_1, tail_2, tail_3 = struct.unpack("<IIHHIIII", data)
    return {
        "layout": "fixed-dynamic-attributes-registration-header",
        "signature_hex": f"0x{signature:08X}",
        "format_version": format_version,
        "opaque_header_u16": [field_1, field_2],
        "flags_hex": f"0x{flags:08X}",
        "zero_tail": [tail_1, tail_2, tail_3],
        "semantic_limit": "registration/version metadata only; not a dynamic property, UCI, Sheet primitive, or geometry record",
    }


def parse_tagged_text_storage_list(data: bytes) -> dict[str, object]:
    """Extract the registered TaggedTxt storage names from the fixed list stream."""

    strings = [
        match.group().decode("utf-16le")
        for match in re.finditer(rb"(?:(?:[\x20-\x7e]\x00){3,})", data)
    ]
    if len(data) != 70 or strings != ["TaggedTxtStorages", "TaggedTxtData"]:
        raise ValueError("JTaggedTxtStgList does not match the validated registration layout")
    return {
        "layout": "fixed-tagged-text-storage-registration",
        "storage_names": strings,
        "semantic_limit": "stream registration only; actual title/revision values remain in TaggedTxtData XML streams",
    }


def classify_empty_sheet_stub(data: bytes) -> dict[str, object]:
    """Recognize the invariant eight-byte non-drawing Sheet placeholder."""

    expected = struct.pack("<II", 0x6C90F544, 0)
    if data != expected:
        raise ValueError("Sheet stream does not match the validated empty stub")
    return {
        "layout": "fixed-empty-sheet-registration-stub",
        "byte_length": len(data),
        "signature_hex": "0x6C90F544",
        "reserved_u32": 0,
        "semantic_limit": "registered empty Sheet placeholder only; not a physical ISO page, split point, template drawing, primitive, or coordinate source",
    }


def jsite_resource_inventory(streams: dict[str, bytes], resource_ids: list[int]) -> list[dict[str, object]]:
    """Inventory JSite payloads, decoding only self-identifying BMP headers."""

    records: list[dict[str, object]] = []
    for resource_id in resource_ids:
        prefix = f"JSite{resource_id}/"
        names = sorted(name for name in streams if name.startswith(prefix))
        contents = streams.get(prefix + "CONTENTS")
        record: dict[str, object] = {
            "resource_id": resource_id,
            "stream_names": names,
            "has_contents": contents is not None,
        }
        compobj = streams.get(prefix + "\x01CompObj")
        if compobj is not None:
            record["compobj_printable_strings"] = [
                match.group().decode("ascii")
                for match in re.finditer(rb"[\x20-\x7e]{4,}", compobj)
            ]
        ole = streams.get(prefix + "\x01Ole")
        if ole is not None:
            record["ole_wrapper_length"] = len(ole)
            record["ole_wrapper_header_hex"] = ole[:8].hex()
        jproperties = streams.get(prefix + "JProperties")
        if jproperties is not None:
            record["jproperties_printable_strings"] = [
                match.group().decode("ascii")
                for match in re.finditer(rb"[\x20-\x7e]{3,}", jproperties)
            ]
            if (
                len(jproperties) >= 10
                and jproperties[:4] == b"OLES"
                and jproperties[4:6] == b"\x00\x00"
            ):
                property_code, utf16_code_unit_count = struct.unpack_from("<2H", jproperties, 6)
                expected_length = 10 + 2 * utf16_code_unit_count
                if expected_length == len(jproperties):
                    try:
                        raw_value = jproperties[10:expected_length].decode("utf-16le")
                    except UnicodeDecodeError:
                        raw_value = ""
                    if raw_value.endswith("\x00"):
                        record["jproperties_layout"] = "bounded-OLES-single-utf16-property"
                        record["jproperties_property_code"] = property_code
                        record["jproperties_utf16_value"] = raw_value.rstrip("\x00")
                        record["jproperties_utf16_code_unit_count"] = utf16_code_unit_count
        if contents is not None and len(contents) >= 54 and contents[:2] == b"BM":
            width, height = struct.unpack_from("<ii", contents, 18)
            bits_per_pixel = struct.unpack_from("<H", contents, 28)[0]
            x_pixels_per_meter, y_pixels_per_meter = struct.unpack_from("<ii", contents, 38)
            record["contents_kind"] = "BMP-DIB"
            record["bmp_pixel_width"] = width
            record["bmp_pixel_height"] = height
            record["bmp_bits_per_pixel"] = bits_per_pixel
            if x_pixels_per_meter > 0 and y_pixels_per_meter > 0:
                record["bmp_x_pixels_per_meter"] = x_pixels_per_meter
                record["bmp_y_pixels_per_meter"] = y_pixels_per_meter
        elif contents is None:
            record["contents_kind"] = "no-embedded-contents"
        else:
            record["contents_kind"] = "unrecognized-embedded-payload"
        records.append(record)
    return records


def parse_tagged_text_xml_streams(streams: dict[str, bytes]) -> dict[str, object]:
    """Inventory UTF-8 TaggedTxtData XML without treating values as geometry."""

    parsed_streams: list[dict[str, object]] = []
    for name, data in sorted(streams.items()):
        if not name.startswith("TaggedTxtData/"):
            continue
        try:
            root = ET.fromstring(data.decode("utf-8"))
        except (UnicodeDecodeError, ET.ParseError) as error:
            parsed_streams.append({"stream": name, "status": "unvalidated", "error": str(error)})
            continue
        leaves: list[dict[str, str]] = []

        def collect(element: ET.Element, prefix: str) -> None:
            path = f"{prefix}/{element.tag}" if prefix else element.tag
            children = list(element)
            if not children:
                leaves.append({"path": path, "value": element.text or ""})
                return
            for child in children:
                collect(child, path)

        collect(root, "")
        parsed_streams.append(
            {
                "stream": name,
                "status": "validated-utf8-xml",
                "source_class": (
                    "title-block-or-signature-candidate-requires-sheet-binding"
                    if name in {"TaggedTxtData/Revision", "TaggedTxtData/SignatureArea", "TaggedTxtData/TitleArea", "TaggedTxtData/TitleBlockInfo"}
                    else "metadata-only-unless-a-bounded-sheet-binding-is-proven"
                ),
                "root": root.tag,
                "leaf_field_count": len(leaves),
                "nonempty_fields": [leaf for leaf in leaves if leaf["value"]],
            }
        )
    return {"stream_count": len(parsed_streams), "streams": parsed_streams}


def template_revision_binding_field_names(data: bytes) -> list[str]:
    """Return Revision field names explicitly referenced by Sheet221 bindings."""

    fields: set[str] = set()
    for record in parse_sheet221_template_text_records(data)["revision_binding_records"]:
        match = re.search(
            r"/RevisionRecord\[[^]]+\]/([^\"<]+)",
            str(record["binding_expression"]),
        )
        if match is not None:
            fields.add(match.group(1))
    return sorted(fields)


def resolve_sheet221_revision_bindings(
    template_data: bytes, revision_xml_data: bytes | None,
) -> dict[str, object]:
    """Resolve proven Sheet221 Revision selectors against Revision XML.

    Only the two selector forms observed in the corpus are accepted:
    ``RevisionRecord[1+n]`` and ``RevisionRecord[last()-n]``.  This is a
    field-value join, not a source of Sheet geometry, font, or placement.
    """

    bindings = parse_sheet221_template_text_records(template_data)["revision_binding_records"]
    if revision_xml_data is None:
        return {
            "status": "unresolved-missing-TaggedTxtData-Revision",
            "binding_count": len(bindings),
            "records": [{**record, "resolution_status": "missing-revision-stream"} for record in bindings],
        }
    try:
        root = ET.fromstring(revision_xml_data.decode("utf-8"))
    except (UnicodeDecodeError, ET.ParseError) as error:
        return {
            "status": "unresolved-invalid-TaggedTxtData-Revision",
            "binding_count": len(bindings),
            "error": str(error),
            "records": [{**record, "resolution_status": "invalid-revision-stream"} for record in bindings],
        }
    if root.tag != "Revision":
        return {
            "status": "unresolved-unexpected-Revision-root",
            "binding_count": len(bindings),
            "root": root.tag,
            "records": [{**record, "resolution_status": "unexpected-revision-root"} for record in bindings],
        }
    rows = list(root.findall("RevisionRecord"))
    selector = re.compile(
        r"^/Revision/RevisionRecord\[(?:1\+(?P<from_first>\d+)|last\(\)-(?P<from_last>\d+))\]/(?P<field>[^/]+)$"
    )
    resolved: list[dict[str, object]] = []
    for record in bindings:
        select = record.get("binding_select")
        match = selector.fullmatch(str(select)) if select is not None else None
        result: dict[str, object] = {**record, "revision_record_count": len(rows)}
        if match is None:
            result["resolution_status"] = "unresolved-selector-syntax"
        else:
            row_index = (
                int(match.group("from_first"))
                if match.group("from_first") is not None
                else len(rows) - 1 - int(match.group("from_last"))
            )
            field = match.group("field")
            result["revision_row_index_zero_based"] = row_index
            result["revision_field"] = field
            if not 0 <= row_index < len(rows):
                if record.get("binding_alt") is not None:
                    result["resolution_status"] = "resolved-alt-row-out-of-range"
                    result["resolved_value"] = record["binding_alt"]
                else:
                    result["resolution_status"] = "unresolved-row-out-of-range"
            else:
                value = rows[row_index].findtext(field)
                if value is None:
                    if record.get("binding_alt") is not None:
                        result["resolution_status"] = "resolved-alt-field-missing"
                        result["resolved_value"] = record["binding_alt"]
                    else:
                        result["resolution_status"] = "unresolved-field-missing"
                else:
                    result["resolution_status"] = "resolved"
                    result["resolved_value"] = value
        resolved.append(result)
    return {
        "status": "validated-Revision-binding-resolution",
        "revision_record_count": len(rows),
        "binding_count": len(bindings),
        "resolved_binding_count": sum(
            str(record["resolution_status"]).startswith("resolved") for record in resolved
        ),
        "records": resolved,
    }


def parse_compact_iso_attribute_records(data: bytes) -> list[dict[str, int]]:
    """Parse the bounded compact ``_ISO`` dynamic-attribute subtype.

    The key immediately precedes ``0x0089``.  Its observed compact footer has
    a size of 30 or 36, an eight-byte zero field, a uint32 candidate graphic
    reference, and a ``0xFFFF`` terminator.  This is routing metadata, not a
    Sheet primitive or a visible text record.
    """

    records: list[dict[str, int]] = []
    for match in re.finditer(re.escape(b"_ISO\x00\x89\x00"), data):
        footer = match.start() + 5
        if footer + 24 > len(data):
            continue
        record_size, reference = struct.unpack_from("<II", data, footer + 2)
        if record_size not in {30, 36}:
            continue
        if data[footer + 10 : footer + 18] != b"\x00" * 8:
            continue
        if data[footer + 22 : footer + 24] != b"\xff\xff":
            continue
        records.append(
            {
                "offset": footer,
                "record_size": record_size,
                "reference": reference,
                "graphic_ref": struct.unpack_from("<I", data, footer + 18)[0],
            }
        )
    return records


def parse_dynamic_attribute_property_records(data: bytes) -> list[dict[str, object]]:
    """Parse bounded ``PipeLine Info`` dynamic-attribute records.

    A component record starts five bytes before the ASCII ``PipeLine Info``
    marker.  Its property list contains independent ``0x1080`` blocks whose
    uint16 size covers ``marker, size, flag, key NUL, value NUL``.  The record
    ends with ``0x0089``.  Many records have a size equal to the complete
    record length minus six bytes; other exports retain a different capacity
    value there.  This parser treats the independently bounded property blocks
    as the identity evidence and reports the footer-size agreement separately.
    """

    marker = b"PipeLine Info\x00"
    starts = [match.start() - 5 for match in re.finditer(re.escape(marker), data) if match.start() >= 5]
    records: list[dict[str, object]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(data)
        record = data[start:end]
        # A final PipeLine Info string can be followed by unrelated document
        # attributes rather than another component record.  It is not a safe
        # component boundary, so retain only locally bounded record variants.
        if len(record) < 45 or len(record) > 4096 or record[5 : 5 + len(marker)] != marker:
            continue
        footer_candidates = [
            match.start()
            for match in re.finditer(re.escape(b"\x89\x00"), record)
            if match.start() >= 19
            and match.start() + 10 <= len(record)
            and 16 <= struct.unpack_from("<I", record, match.start() + 2)[0] <= 0x10000
        ]
        if not footer_candidates:
            continue
        # A graphic reference can itself contain the byte pair 0x89,0x00.
        # The bounded footer has a plausible following uint32 length; the raw
        # final byte-pair occurrence does not necessarily have that property.
        footer = footer_candidates[-1]
        record_size, space_ref = struct.unpack_from("<II", record, footer + 2)
        if not 16 <= record_size <= 0x10000:
            continue
        properties: list[dict[str, object]] = []
        cursor = 19
        while cursor < footer:
            next_start = record.find(b"\x10\x80", cursor, footer)
            if next_start == -1:
                break
            if next_start + 7 > footer:
                break
            block_size = struct.unpack_from("<H", record, next_start + 2)[0]
            block_end = next_start + block_size
            if block_size < 7 or block_end > footer or record[next_start + 4] != 1:
                cursor = next_start + 2
                continue
            key_end = record.find(b"\x00", next_start + 5, block_end)
            if key_end == -1 or record[block_end - 1] != 0:
                cursor = next_start + 2
                continue
            try:
                key = record[next_start + 5 : key_end].decode("ascii")
                value = record[key_end + 1 : block_end - 1].decode("ascii")
            except UnicodeDecodeError:
                cursor = next_start + 2
                continue
            if not key or not all(31 < ord(character) < 127 for character in key):
                cursor = next_start + 2
                continue
            properties.append(
                {
                    "offset": start + next_start,
                    "block_size": block_size,
                    "key": key,
                    "value": value,
                }
            )
            cursor = block_end
        if not properties or len(record) < 8:
            continue
        uci_property = next(
            (property_record for property_record in properties if property_record["key"] == "Unique Component Identifier"),
            None,
        )
        element_tag_property = next(
            (property_record for property_record in properties if property_record["key"] == "Element Tag"),
            None,
        )
        uci_index: int | None = None
        if uci_property is not None and element_tag_property is not None:
            index_start = int(uci_property["offset"]) + int(uci_property["block_size"])
            index_end = int(element_tag_property["offset"])
            index_block = data[index_start:index_end]
            if (
                len(index_block) == 18
                and index_block[:4] == b"\x03\x00\x12\x00"
                and index_block[4:14] == b"UCI Index\x00"
            ):
                uci_index = struct.unpack_from("<I", index_block, 14)[0]
        property_values = {
            str(property_record["key"]): str(property_record["value"])
            for property_record in properties
        }
        has_component_uci = bool(property_values.get("Unique Component Identifier"))
        has_pipeline_reference = bool(property_values.get("PipeLine Reference"))
        has_fly_text = bool(property_values.get("Fly Text"))
        if has_component_uci and has_pipeline_reference and has_fly_text:
            record_kind = "component-instance"
        elif not has_component_uci and not has_pipeline_reference and not has_fly_text:
            record_kind = "empty-property-schema-stub"
        else:
            record_kind = "partial-property-record"
        records.append(
            {
                "offset": start,
                "record_length": len(record),
                "record_size": record_size,
                "footer_size_matches_record_length": record_size == len(record) - 6,
                "reference": space_ref,
                "graphic_ref": struct.unpack_from("<I", record, len(record) - 8)[0],
                "property_count": len(properties),
                "properties": properties,
                "uci_index": uci_index,
                "record_kind": record_kind,
                "has_component_uci": has_component_uci,
                "has_pipeline_reference": has_pipeline_reference,
                "has_fly_text": has_fly_text,
                "has_element_tag_value": bool(property_values.get("Element Tag")),
            }
        )
    return records


def parse_document_dynamic_settings(data: bytes) -> dict[str, object]:
    """Read the fixed document-level ``0x1080`` settings before component data."""

    settings: list[dict[str, object]] = []
    for key in ("Draw", "Schematic", "FileName", "MuSuStr"):
        marker = b"\x10\x80"
        start = 0
        while True:
            offset = data.find(marker, start)
            if offset < 0:
                break
            start = offset + 1
            if offset + 7 > len(data):
                continue
            block_size = struct.unpack_from("<H", data, offset + 2)[0]
            end = offset + block_size
            if not 10 <= block_size <= 2048 or end > len(data):
                continue
            payload = data[offset + 5 : end]
            try:
                raw_key, raw_value = payload.split(b"\0", 1)
                decoded_key = raw_key.decode("ascii")
                value = raw_value.split(b"\0", 1)[0].decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
            if decoded_key == key:
                settings.append({"offset": offset, "key": key, "value": value})
    return {
        "layout": "bounded-document-level-0x1080-settings",
        "settings": settings,
        "semantic_limit": "document/export configuration only; these blocks are not component properties or visible ISO text",
    }


def summarize_dynamic_property_sheet_text_matches(
    dynamic_property_records: list[dict[str, object]], physical_sheet_text_values: set[str]
) -> dict[str, dict[str, int]]:
    """Separate property-row and unique-value matches against rendered 4D text.

    A PipeLine Reference repeats across many component property rows, so a
    unique-value match alone cannot establish a one-component-to-one-label
    mapping.  Keep occurrence and distinct-value evidence separate.
    """

    result: dict[str, dict[str, int]] = {}
    for property_key in ("PipeLine Reference", "Fly Text", "Element Tag"):
        values = [
            str(property_record["value"])
            for record in dynamic_property_records
            for property_record in list(record["properties"])
            if property_record["key"] == property_key and str(property_record["value"])
        ]
        unique_values = set(values)
        matched_values = unique_values & physical_sheet_text_values
        result[property_key] = {
            "nonempty_occurrence_count": len(values),
            "nonempty_unique_value_count": len(unique_values),
            "exact_physical_sheet_4d_text_match_count": len(matched_values),
            "exact_physical_sheet_4d_text_match_occurrence_count": sum(
                value in physical_sheet_text_values for value in values
            ),
        }
    return result


def summarize_element_tag_unique_text_candidates(
    dynamic_property_records: list[dict[str, object]], physical_sheet_text_records: list[dict[str, object]]
) -> dict[str, object]:
    """Return only unambiguous Element Tag to visible-text candidate links.

    The link is based on a bounded property value and a bounded physical 4D
    text record in the same SHA. It is useful for locating a candidate label,
    but it does not prove that the text is owned by the component graphic.
    Multiple same-text records intentionally remain unresolved.
    """

    text_records_by_value: dict[str, list[dict[str, object]]] = {}
    for text_record in physical_sheet_text_records:
        text_records_by_value.setdefault(str(text_record["text"]), []).append(text_record)
    candidate_count_distribution: Counter[int] = Counter()
    unique_candidates: list[dict[str, object]] = []
    for property_record in dynamic_property_records:
        tag = next(
            (
                str(property_value["value"])
                for property_value in list(property_record["properties"])
                if property_value["key"] == "Element Tag" and str(property_value["value"])
            ),
            None,
        )
        if tag is None:
            continue
        candidates = text_records_by_value.get(tag, [])
        candidate_count_distribution[len(candidates)] += 1
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        unique_candidates.append(
            {
                "dynamic_graphic_ref": int(property_record["graphic_ref"]),
                "element_tag": tag,
                "sheet_stream": str(candidate["sheet_stream"]),
                "text_child_ref": int(candidate["child_ref"]),
                "text_secondary_ref": int(candidate["secondary_ref"]),
                "text_page_layer_ref": int(candidate["page_layer_ref"]),
                "text_style_ref": int(candidate["style_ref"]),
                "text_font_name": candidate.get("font_name"),
                "text_font_size_ratio": candidate.get("font_size_ratio"),
                "text_anchor": [float(candidate["x"]), float(candidate["y"])],
                "text_direction": list(candidate["direction"]),
            }
        )
    return {
        "layout": "bounded-element-tag-to-unique-physical-4d-text-candidate",
        "element_tag_property_record_count": sum(candidate_count_distribution.values()),
        "candidate_text_count_distribution": {
            str(value): count for value, count in sorted(candidate_count_distribution.items())
        },
        "unique_candidate_count": len(unique_candidates),
        "unique_candidates": unique_candidates,
        "semantic_limit": (
            "a unique same-SHA string candidate locates a visible label but does not establish component ownership, "
            "leader attachment, or a one-to-one UCI-to-text rule"
        ),
    }


def summarize_dynamic_graphic_sheet_primitive_bindings(
    streams: dict[str, bytes], dynamic_graphics_by_uci: dict[str, list[dict[str, object]]]
) -> dict[str, object]:
    """Resolve dynamic-UCI graphic refs to equal graphic refs in Sheet records.

    This is a direct numeric reference equality in one SHA, unlike a text
    string candidate. A single graphic can legitimately occur in several
    primitive families because a 0x7B composite groups the same line/arc/circle
    geometry; retain every family instead of selecting a component type.
    """

    sheet_families: dict[int, list[dict[str, object]]] = {}
    for sheet_name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", sheet_name) or len(data) <= 1024:
            continue
        for family, parser, reference_field in (
            ("18_32_line", parse_18_32_layer_bindings, "graphic_ref"),
            ("59_2b_circle", parse_59_2b_page_layer_bindings, "graphic_ref"),
            ("61_arc", parse_61_pipe_arc_records, "graphic_ref"),
            ("4d_text_secondary_ref", parse_4d_text_layer_bindings, "secondary_ref"),
        ):
            for record in parser(data):
                graphic_ref = int(record[reference_field])
                sheet_families.setdefault(graphic_ref, []).append(
                    {
                        "sheet_stream": sheet_name,
                        "family": family,
                    }
                )
        for record in parse_7b_composite_headers(data):
            sheet_families.setdefault(int(record["composite_ref"]), []).append(
                {"sheet_stream": sheet_name, "family": "7b_composite"}
            )

    records: list[dict[str, object]] = []
    direct_family_counts: Counter[str] = Counter()
    for uci, dynamic_records in dynamic_graphics_by_uci.items():
        for dynamic_record in dynamic_records:
            graphic_ref = int(dynamic_record["graphic_ref"])
            bindings = sheet_families.get(graphic_ref, [])
            for binding in bindings:
                direct_family_counts[str(binding["family"])] += 1
            records.append(
                {
                    "uci": str(uci),
                    "graphic_ref": graphic_ref,
                    "sheet_primitive_bindings": bindings,
                    "direct_sheet_binding_count": len(bindings),
                }
            )
    return {
        "layout": "dynamic-uci-graphic-ref-to-equal-sheet-graphic-ref",
        "dynamic_graphic_record_count": len(records),
        "direct_sheet_graphic_record_count": sum(
            bool(record["sheet_primitive_bindings"]) for record in records
        ),
        "unmatched_dynamic_graphic_record_count": sum(
            not bool(record["sheet_primitive_bindings"]) for record in records
        ),
        "direct_sheet_family_occurrence_counts": dict(sorted(direct_family_counts.items())),
        "records": records,
        "semantic_limit": (
            "an equal graphic ref proves UCI-to-Sheet graphic membership, not an exclusive component class; "
            "composite and primitive families can intentionally share the same graphic"
        ),
    }


def bounded_dynamic_graphics_by_uci(
    dynamic_property_records: list[dict[str, object]]
) -> dict[str, list[dict[str, int]]]:
    """Build UCI graphic records only from fully bounded PipeLine Info rows.

    The older next-marker scan can absorb unrelated trailing metadata after the
    final PipeLine Info record and emit ASCII ``SP1_`` as a false graphic ref.
    This helper requires the locally bounded property/0x0089 framing first.
    """

    result: dict[str, list[dict[str, int]]] = {}
    for record in dynamic_property_records:
        uci = next(
            (
                str(property_record["value"])
                for property_record in list(record["properties"])
                if property_record["key"] == "Unique Component Identifier"
                and re.fullmatch(
                    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}",
                    str(property_record["value"]),
                )
            ),
            None,
        )
        if uci is None:
            continue
        result.setdefault(uci, []).append(
            {
                "record_offset": int(record["offset"]),
                "space_ref": int(record["reference"]),
                "graphic_ref": int(record["graphic_ref"]),
            }
        )
    return result


def attach_4d_text_style_resources(
    text_records: list[dict[str, object]], text_style_links_by_ref: dict[int, dict[str, object]]
) -> None:
    """Attach directly resolved StyleCluster font data to bounded 4D text."""

    for text_record in text_records:
        style = text_style_links_by_ref.get(int(text_record["style_ref"]))
        if style is None:
            continue
        text_record["font_style_ref"] = int(style["font_style_ref"])
        text_record["font_name"] = str(style["font_name"])
        text_record["font_size_ratio"] = float(style["font_size_ratio"])
        if "explicit_font_style_ref" in text_record:
            text_record["explicit_font_style_matches_style_link"] = (
                int(text_record["explicit_font_style_ref"]) == int(style["font_style_ref"])
            )


def summarize_4d_text_psm_envelope_bindings(
    text_records: list[dict[str, object]], envelope_runs: dict[str, object]
) -> dict[str, object]:
    """Report the direct 0x004D-child to bounded PSM envelope relation.

    Physical Sheet text anchors are normalized against the Shape2D sheet unit
    (16800) on *both* axes.  This is deliberately distinct from the visible
    page height (11880): using the latter for ``y`` shifts labels vertically.
    The PSM envelope is the rendered glyph extent, while the StyleCluster
    ratio is only the requested text size, so callers must not substitute one
    for the other.
    """

    envelopes_by_ref: dict[int, list[dict[str, int]]] = {}
    for run in envelope_runs.get("runs", []):
        for envelope in run["records"]:
            envelopes_by_ref.setdefault(int(envelope["graphic_ref"]), []).append(envelope)

    multiplicity = Counter(
        len(envelopes_by_ref.get(int(record["child_ref"]), []))
        for record in text_records
    )
    glyph_boxes = [
        envelope
        for record in text_records
        for envelope in envelopes_by_ref.get(int(record["child_ref"]), [])
        if 1 <= int(envelope["right"]) - int(envelope["left"]) <= 8000
        and 1 <= int(envelope["top"]) - int(envelope["bottom"]) <= 1000
    ]
    extended_records = [
        record
        for record in text_records
        if str(record.get("layout")) == "extended-0x004d-text-with-eight-byte-prefix"
    ]
    return {
        "layout": "physical-4d-text-child-ref-to-run-bounded-psm-envelope",
        "text_record_count": len(text_records),
        "direct_child_ref_envelope_match_count": sum(
            occurrences for count, occurrences in multiplicity.items() if count > 0
        ),
        "single_envelope_match_count": multiplicity.get(1, 0),
        "candidate_count_distribution": {
            str(count): occurrences for count, occurrences in sorted(multiplicity.items())
        },
        "ordinary_glyph_envelope_count": len(glyph_boxes),
        "extended_layout_record_count": len(extended_records),
        "extended_prefix_constant_one_count": sum(
            int(record.get("text_prefix_constant_raw", -1)) == 1 for record in extended_records
        ),
        "extended_prefix_character_count_match_count": sum(
            bool(record.get("text_prefix_character_count_matches")) for record in extended_records
        ),
        "extended_explicit_font_style_link_match_count": sum(
            bool(record.get("explicit_font_style_matches_style_link")) for record in extended_records
        ),
        "normalized_anchor_page_unit": 16800,
        "normalized_anchor_contract": "page_x = 4D.x * 16800; page_y = 4D.y * 16800",
        "semantic_limit": (
            "the bounded PSM envelope is the rendered glyph extent; StyleCluster font_size_ratio remains "
            "a requested style metric and is not asserted to equal the final glyph-box height"
        ),
    }


def attribute_ref_0089_records(data: bytes) -> list[dict[str, object]]:
    """Return one bounded dynamic-attribute row per ``0x0089`` reference.

    Prefer the complete ``PipeLine Info`` framing, which preserves all four
    component properties.  Retain the older local scan as a fallback for
    records such as `_ISO` that are not part of that component family.
    """

    records: list[dict[str, object]] = []
    framed_records = parse_dynamic_attribute_property_records(data)
    framed_0089_offsets = {
        int(record["offset"]) + int(record["record_length"]) - 26
        for record in framed_records
    }
    for framed in framed_records:
        properties = list(framed["properties"])
        records.append(
            {
                "offset": int(framed["offset"]) + int(framed["record_length"]) - 26,
                "record_size": framed["record_size"],
                "reference": framed["reference"],
                "attribute_key": str(properties[-1]["key"]),
                "attribute_keys": [str(property_record["key"]) for property_record in properties],
                "properties": properties,
                "graphic_ref": framed["graphic_ref"],
                "framing": "validated-pipeline-info",
            }
        )
    for match in re.finditer(re.escape(b"\x89\x00"), data):
        offset = match.start()
        if offset in framed_0089_offsets:
            continue
        if offset + 10 > len(data):
            continue
        record_size, reference = struct.unpack_from("<II", data, offset + 2)
        if 16 <= record_size <= 0x10000:
            key: str | None = None
            # `_ISO` stores its key immediately before the field, without
            # the ordinary 0x1080 property-block prefix.
            if offset >= 5 and data[offset - 5 : offset] == b"_ISO\x00":
                key = "_ISO"
            else:
                starts = [
                    candidate
                    for candidate in range(max(0, offset - 512), offset)
                    if data.startswith(b"\x10\x80", candidate)
                ]
                if starts:
                    start = starts[-1]
                    key_end = data.find(b"\x00", start + 5, offset)
                    if key_end != -1:
                        try:
                            candidate_key = data[start + 5 : key_end].decode("ascii")
                        except UnicodeDecodeError:
                            candidate_key = ""
                        if candidate_key and all(31 < ord(character) < 127 for character in candidate_key):
                            key = candidate_key
            records.append(
                {
                    "offset": offset,
                    "record_size": record_size,
                    "reference": reference,
                    "attribute_key": key,
                    "attribute_keys": [key] if key is not None else [],
                    "properties": [],
                    "graphic_ref": None,
                    "framing": "local-0089-fallback",
                }
            )
    return records
def parse_psm_roots(data: bytes) -> dict[str, object]:
    """Parse the observed counted UTF-16 root-directory entries."""

    if len(data) < 8 or data[:5] != b"rootb":
        raise ValueError("not a rootb registry")
    # This byte is stable in the examined files but is not the number of
    # UTF-16 entries (it is 9 while five entries follow), so retain it as a
    # header value rather than assigning it a count semantic.
    header_count_byte = data[5]
    offset = 8
    entries: list[dict[str, object]] = []
    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError(f"truncated root name length at {offset}")
        char_count = struct.unpack_from("<I", data, offset)[0]
        text_end = offset + 4 + char_count * 2
        if char_count == 0 or text_end + 4 > len(data):
            raise ValueError(f"invalid root name at {offset}: chars={char_count}")
        name = data[offset + 4 : text_end].decode("utf-16le")
        root_ref = struct.unpack_from("<I", data, text_end)[0]
        entries.append({"name": name, "root_ref": root_ref})
        offset = text_end + 4
    return {
        "header_count_byte": header_count_byte,
        "entry_count": len(entries),
        "entries": entries,
        "fully_consumed": True,
    }


def parse_psm_cluster_table(data: bytes) -> dict[str, object]:
    """Parse the complete name directory stored in ``PSMclustertable``.

    Every observed entry starts with a UTF-16 byte length, followed by
    ``marker:uint8, directory_index:uint16, child_count:uint32`` and that
    many uint32 child directory indexes, then a NUL-terminated printable
    UTF-16LE stream name.
    """

    if len(data) < 12 or data[:4] != b"clst":
        raise ValueError("not a clst registry")
    declared_entry_count, header_u32 = struct.unpack_from("<2I", data, 4)
    offset = 12
    entries: list[dict[str, object]] = []
    for entry_index in range(declared_entry_count):
        if offset + 4 > len(data):
            raise ValueError(f"truncated cluster entry length at {offset}")
        name_bytes = struct.unpack_from("<I", data, offset)[0]
        if name_bytes < 4 or name_bytes % 2:
            raise ValueError(f"invalid cluster name byte length at {offset}: {name_bytes}")
        candidates: list[tuple[int, str, int]] = []
        # The hierarchy header is 7 bytes for Sheet entries and longer for
        # metadata entries with child indexes. Keep the bounded scan
        # conservative and require an exact NUL-terminated string boundary.
        for text_offset in range(offset + 4, min(offset + 36, len(data))):
            text_end = text_offset + name_bytes
            if text_end > len(data) or data[text_end - 2 : text_end] != b"\x00\x00":
                continue
            try:
                name = data[text_offset : text_end - 2].decode("utf-16le")
            except UnicodeDecodeError:
                continue
            if name and all(31 < ord(character) < 127 for character in name):
                candidates.append((text_offset, name, text_end))
        if len(candidates) != 1:
            raise ValueError(f"ambiguous cluster name boundary at {offset}: {len(candidates)} candidates")
        text_offset, name, text_end = candidates[0]
        opaque_header = data[offset + 4 : text_offset]
        if len(opaque_header) < 7:
            raise ValueError(f"truncated cluster directory header at {offset}")
        marker = opaque_header[0]
        directory_index = struct.unpack_from("<H", opaque_header, 1)[0]
        child_count = struct.unpack_from("<I", opaque_header, 3)[0]
        expected_header_length = 7 + child_count * 4
        if len(opaque_header) != expected_header_length:
            raise ValueError(
                f"cluster directory child list mismatch at {offset}: "
                f"header={len(opaque_header)}, children={child_count}"
            )
        child_indexes = list(struct.unpack_from(f"<{child_count}I", opaque_header, 7))
        entries.append(
            {
                "index": entry_index,
                "name": name,
                "name_bytes": name_bytes,
                "record_offset": offset,
                "marker": marker,
                "directory_index": directory_index,
                "child_count": child_count,
                "child_indexes": child_indexes,
            }
        )
        offset = text_end
    if offset != len(data):
        raise ValueError(f"cluster registry trailing bytes: {len(data) - offset}")
    if any(int(entry["marker"]) != 1 for entry in entries):
        raise ValueError("unexpected cluster directory marker")
    if any(int(entry["directory_index"]) != int(entry["index"]) for entry in entries):
        raise ValueError("cluster directory index does not match entry order")
    for entry in entries:
        entry["child_names"] = [
            entries[child_index]["name"] if child_index < len(entries) else None
            for child_index in entry["child_indexes"]
        ]
    return {
        "header_u32": header_u32,
        "declared_entry_count": declared_entry_count,
        "entry_count": len(entries),
        "entries": entries,
        "fully_consumed": True,
    }


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
                "parent_ref": repeated_count if node_type == 1 else None,
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


def parse_prefixed_tseg_nodes(data: bytes) -> dict[str, object]:
    """Parse a full 0x8000 node table preceded by a bounded uint16 index list.

    This is distinct from the compact-root form: all four header words are
    retained, word four is the exact prefix-list length, and word one is the
    exact count of following ordinary ``<4H> + <IH>`` nodes.
    """

    if len(data) < 20 or data[:4] != b"tseg":
        raise ValueError("not a sufficiently long prefixed tseg stream")
    declared_count, header_flag, header_value, prefix_count = struct.unpack_from("<4H", data, 4)
    # Physical ``0x0000`` map variants use the same bounded node payload but
    # have a longer local index prefix than the original ``0x8000`` examples.
    if declared_count == 0 or prefix_count == 0 or prefix_count > 4096:
        raise ValueError("not a prefixed full-node table")
    offset = 12 + prefix_count * 2
    if offset + 8 > len(data):
        raise ValueError("truncated prefixed tseg index list")
    prefix_values = list(struct.unpack_from(f"<{prefix_count}H", data, 12))
    nodes: list[dict[str, object]] = []
    zero_padding_runs: list[dict[str, int]] = []
    for _ in range(declared_count):
        if data[offset : offset + 4] == b"\x00" * 4:
            padding_start = offset
            while offset < len(data) and data[offset] == 0:
                offset += 1
            zero_padding_runs.append({"offset": padding_start, "length": offset - padding_start})
        if offset + 8 > len(data):
            raise ValueError("truncated prefixed tseg node header")
        node_id, node_type, child_count, repeated_count = struct.unpack_from("<4H", data, offset)
        end = offset + 8 + child_count * 6
        if child_count > 500 or end > len(data):
            raise ValueError(f"invalid prefixed tseg node at {offset}: child_count={child_count}")
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
                "parent_ref": repeated_count if node_type == 1 else None,
                "children": children,
            }
        )
        offset = end
    trailing_zero_padding_start = offset
    while offset < len(data) and data[offset] == 0:
        offset += 1
    if offset != len(data):
        raise ValueError("prefixed tseg node table does not consume stream")
    return {
        "header_u16": [declared_count, header_flag, header_value, prefix_count],
        "layout": "prefixed-index-node-table",
        "prefix_uint16_values": prefix_values,
        "zero_padding_runs": zero_padding_runs,
        "trailing_zero_padding_length": offset - trailing_zero_padding_start,
        "node_count": len(nodes),
        "nodes": nodes,
        "fully_consumed": True,
    }


def parse_counted_tail_root(data: bytes, offset: int) -> dict[str, object]:
    """Parse the self-sized type-3 footer after a counted node table."""

    if offset + 6 > len(data):
        raise ValueError("truncated counted-node tail")
    tail_type, tail_count, tail_repeat = struct.unpack_from("<3H", data, offset)
    if (
        tail_type == 3
        and tail_count == 0
        and tail_repeat == 1
        and len(data) == offset + 12
        and data[offset + 6 :] == b"\x00" * 6
    ):
        return {"kind": "empty-control-footer"}
    end = offset + 6 + tail_count * 6
    if tail_type != 3 or end != len(data):
        raise ValueError("unrecognized counted-node tail boundary")
    if tail_count in {1, 2} and tail_repeat == tail_count:
        children = [
            {"ref": ref, "relation": relation}
            for ref, relation in (
                struct.unpack_from("<IH", data, offset + 6 + index * 6)
                for index in range(tail_count)
            )
        ]
        # A two-child footer can combine a template route (182 -> shared
        # JSite 559) with the usual dynamic-property route (190). The tail
        # framing is still exact; retain relation codes without treating any
        # child as visible geometry.
        if all(int(child["relation"]) in {181, 182, 183, 184, 190, 201} for child in children):
            return {"kind": "tail-root", "children": children}
    raise ValueError("unrecognized counted-node tail")


def uses_tri16_relation_payload(node_type: int, node_id: int) -> bool:
    """Return whether a counted node stores children as ``<3H>`` relations.

    The layout is proven for type 1, the type-2/3 local relation tables, the
    two observed type-4 continuation records, and the high-numbered routing
    families. Other low types retain their six-byte payload as opaque until a
    repeatable structural rule is available.
    """

    return (
        node_type == 1
        or (node_id in {2, 3} and node_type >= 2)
        or node_type >= 170
    )


def parse_counted_node_payload(
    data: bytes, offset: int, child_count: int, node_type: int, node_id: int
) -> tuple[list[dict[str, int]], int]:
    """Separate bounded relation forms from opaque six-byte node payloads."""

    relations = {181, 182, 183, 184, 190, 201}
    if node_id == 3 and node_type == 0:
        entries = [struct.unpack_from("<3H", data, offset + index * 6) for index in range(child_count)]
        if all(reserved == 0 and relation == 0 for reserved, relation, _ in entries):
            # This is a bounded zero-relation anchor list. Its local IDs are
            # exposed separately and are not ordinary graph edges.
            return [], 0
    if uses_tri16_relation_payload(node_type, node_id):
        # Type-1 uses the same 3xuint16 child form as the mixed relation
        # sequence: reserved, relation, child-ref. Its header's fourth uint16
        # is the parent reference, not a repeated child count.
        entries = [struct.unpack_from("<3H", data, offset + index * 6) for index in range(child_count)]
        recognized = [
            {"ref": child_ref, "relation": relation}
            for reserved, relation, child_ref in entries
            if reserved == 0 and relation in relations
        ]
        return recognized, len(entries) - len(recognized)
    entries = [struct.unpack_from("<IH", data, offset + index * 6) for index in range(child_count)]
    recognized = [
        {"ref": ref, "relation": relation}
        for ref, relation in entries
        if relation in relations
    ]
    return recognized, len(entries) - len(recognized)


def parse_counted_zero_relation_list(
    data: bytes, offset: int, child_count: int, node_type: int, node_id: int, repeated_count: int
) -> list[int]:
    """Decode the counted-table form of a zero-relation anchor list."""

    if (node_id, node_type, repeated_count) != (3, 0, 0):
        return []
    entries = [struct.unpack_from("<3H", data, offset + index * 6) for index in range(child_count)]
    if not all(reserved == 0 and relation == 0 for reserved, relation, _ in entries):
        return []
    return [child_ref for _, _, child_ref in entries if child_ref]


def parse_counted_zero_relation_extension(
    data: bytes, offset: int, child_count: int, node_type: int, node_id: int
) -> list[int]:
    """Retain unbound local references carried by zero-relation extension rows."""

    if not uses_tri16_relation_payload(node_type, node_id):
        return []
    entries = [struct.unpack_from("<3H", data, offset + index * 6) for index in range(child_count)]
    return [child_ref for reserved, relation, child_ref in entries if reserved == 0 and relation == 0 and child_ref]


def classify_counted_zero_relation_extensions(
    nodes: list[dict[str, object]], named_object_name_by_ref: dict[int, str]
) -> dict[str, object]:
    """Summarize bounded zero-relation rows without promoting them to edges."""

    families: dict[str, dict[str, object]] = {}
    for node in nodes:
        refs = [int(ref) for ref in node.get("zero_relation_extension_refs", [])]
        if not refs:
            continue
        family = f"node_id={int(node['id'])},type={int(node['type'])}"
        summary = families.setdefault(
            family,
            {
                "node_count": 0,
                "nonzero_local_ref_count": 0,
                "zero_target_count": 0,
                "named_psm_object_ref_count": 0,
                "named_psm_object_names": Counter(),
                "unbound_local_refs": Counter(),
            },
        )
        summary["node_count"] = int(summary["node_count"]) + 1
        summary["nonzero_local_ref_count"] = int(summary["nonzero_local_ref_count"]) + len(refs)
        # In the currently proven triple-form families, every non-edge payload
        # is a zero-relation row. The remainder is therefore the explicit
        # zero-target companion count, not an unknown visible primitive.
        summary["zero_target_count"] = int(summary["zero_target_count"]) + int(
            node.get("opaque_payload_entry_count", 0)
        ) - len(refs)
        for ref in refs:
            object_name = named_object_name_by_ref.get(ref)
            if object_name is None:
                summary["unbound_local_refs"][ref] += 1
            else:
                summary["named_psm_object_ref_count"] = int(summary["named_psm_object_ref_count"]) + 1
                summary["named_psm_object_names"][object_name] += 1
    return {
        family: {
            **summary,
            "named_psm_object_names": dict(sorted(summary["named_psm_object_names"].items())),
            "unbound_local_refs": dict(sorted(summary["unbound_local_refs"].items())),
            "semantic_limit": (
                "zero-relation local references are bounded hierarchy/control inventory; "
                "a named PSM object match does not create a visible Sheet primitive"
            ),
        }
        for family, summary in sorted(families.items())
    }


def index_sheet221_template_primitives(streams: dict[str, bytes]) -> dict[int, list[dict[str, object]]]:
    """Index decoded Sheet221 template primitives by their local reference."""

    data = streams.get("Sheet221", b"")
    index: dict[int, list[dict[str, object]]] = {}
    for line in parse_18_32_layer_bindings(data):
        index.setdefault(int(line["child_ref"]), []).append(
            {
                "family": "18_32_template_line",
                "page_layer_ref": int(line["page_layer_ref"]),
                "style_ref": int(line["style_ref"]),
                "start": line["start"],
                "end": line["end"],
            }
        )
    special = parse_sheet221_template_special_records(data)
    for family, records in special.items():
        for record in records:
            entry: dict[str, object] = {
                "family": family,
                "page_layer_ref": int(record["page_layer_ref"]),
            }
            if "points" in record:
                entry["points"] = record["points"]
                entry["closed"] = bool(record["closed"])
            if "jsite_resource_id" in record:
                entry["jsite_resource_id"] = int(record["jsite_resource_id"])
            index.setdefault(int(record["primitive_ref"]), []).append(entry)
    return index


def classify_counted_zero_relation_anchor_lists(
    streams: dict[str, bytes], nodes: list[dict[str, object]], sheet_local_start_by_ref: dict[int, str]
) -> dict[str, object]:
    """Classify the explicit zero-relation anchor inventory by proven targets."""

    refs = Counter(
        int(ref)
        for node in nodes
        for ref in node.get("zero_relation_list_refs", [])
    )
    psmcluster0 = streams.get("PSMcluster0", b"")
    sheet221_template_primitives = index_sheet221_template_primitives(streams)
    records = []
    for ref, count in sorted(refs.items()):
        envelopes = psm_envelopes(psmcluster0, ref)
        template_primitives = sheet221_template_primitives.get(ref, [])
        records.append(
            {
                "local_ref": ref,
                "occurrence_count": count,
                "sheet_header_local_start": sheet_local_start_by_ref.get(ref),
                "psmcluster0_envelopes": envelopes,
                "sheet221_template_primitives": template_primitives,
                "classification": (
                    "sheet-content-root"
                    if ref in sheet_local_start_by_ref
                    else "sheet221-template-primitive"
                    if template_primitives
                    else "template-or-layout-envelope"
                    if envelopes
                    else "unbound-local-anchor"
                ),
            }
        )
    return {
        "nonzero_reference_count": sum(refs.values()),
        "distinct_reference_count": len(refs),
        "records": records,
        "semantic_limit": (
            "the list is a bounded local-anchor inventory; envelopes and Sheet roots classify storage targets only "
            "and do not make the references visible primitives"
        ),
    }


def classify_mixed_zero_relation_lists(
    streams: dict[str, bytes],
    zero_lists: list[list[int]],
    sheet_local_start_by_ref: dict[int, str],
) -> dict[str, object]:
    """Classify bounded zero-relation lists in the mixed ``0x0000`` layout.

    These lists are separate from ordinary relation containers.  They are
    retained as layout anchors only: a thin PSM envelope is a template-frame
    edge candidate, a page-sized envelope is a layout container candidate,
    and an exact Sheet root remains a storage/page anchor rather than a
    visible primitive.
    """

    psmcluster0 = streams.get("PSMcluster0", b"")
    records: list[dict[str, object]] = []
    for list_index, refs in enumerate(zero_lists):
        nonzero_refs = [int(ref) for ref in refs if int(ref) != 0]
        targets: list[dict[str, object]] = []
        for ref in nonzero_refs:
            envelopes = [
                envelope
                for envelope in psm_envelopes(psmcluster0, ref)
                if envelope[0] < envelope[2] and envelope[1] < envelope[3]
            ]
            thin_envelopes = [
                envelope
                for envelope in envelopes
                if envelope[2] - envelope[0] <= 1 or envelope[3] - envelope[1] <= 1
            ]
            if ref in sheet_local_start_by_ref:
                classification = "physical-sheet-root-anchor"
            elif thin_envelopes:
                classification = "template-frame-edge-anchor"
            elif any(envelope[0] == 0 and envelope[1] == 0 for envelope in envelopes):
                classification = "page-layout-container-anchor"
            else:
                classification = "unresolved-layout-anchor"
            targets.append(
                {
                    "local_ref": ref,
                    "sheet_stream": sheet_local_start_by_ref.get(ref),
                    "psmcluster0_envelopes": envelopes,
                    "classification": classification,
                }
            )
        records.append(
            {
                "list_index": list_index,
                "raw_ref_count": len(refs),
                "zero_ref_count": len(refs) - len(nonzero_refs),
                "targets": targets,
            }
        )
    return {
        "record_count": len(records),
        "records": records,
        "semantic_limit": (
            "these are PSM layout/template anchors only; none is a pipe component, Sheet primitive, "
            "or a request to render a new SVG path"
        ),
    }


def classify_tseg_relation_targets(
    streams: dict[str, bytes],
    nodes: list[dict[str, object]],
    named_object_name_by_ref: dict[int, str],
    sheet_local_start_by_ref: dict[int, str],
    dynamic_graphic_refs: set[int],
    dynamic_attribute_refs: set[int],
    jsite_resource_ids: set[int],
) -> dict[str, object]:
    """Classify fully bounded tseg routing edges by proven target namespace only."""

    categories: dict[str, Counter[str]] = {}
    named_targets: dict[str, Counter[str]] = {}
    parent_238_routes: Counter[str] = Counter()
    psmcluster0 = streams.get("PSMcluster0", b"")
    for node in nodes:
        parent_ref = node.get("parent_ref")
        for child in node.get("children", []):
            relation = str(int(child["relation"]))
            target = int(child["ref"])
            segment_base = target & 0xFFFFE000
            segment_name = f"PSMspacemap/0x{segment_base:08x}"
            counts = categories.setdefault(relation, Counter())
            counts["total"] += 1
            counts["sheet_header_local_start"] += target in sheet_local_start_by_ref
            counts["dynamic_graphic_ref"] += target in dynamic_graphic_refs
            counts["dynamic_attribute_ref_0089"] += target in dynamic_attribute_refs
            counts["named_psm_object"] += target in named_object_name_by_ref
            counts["jsite_resource"] += target in jsite_resource_ids
            counts["psmcluster0_envelope"] += bool(psm_envelopes(psmcluster0, target))
            counts["spacemap_segment_address"] += segment_name in streams
            if target in named_object_name_by_ref:
                named_targets.setdefault(relation, Counter())[named_object_name_by_ref[target]] += 1
            if parent_ref == 238:
                parent_238_routes[f"{relation}->0x{target:04X}"] += 1
    return {
        "relation_target_categories": {
            relation: dict(sorted(counts.items())) for relation, counts in sorted(categories.items())
        },
        "named_psm_object_targets_by_relation": {
            relation: dict(sorted(counts.items())) for relation, counts in sorted(named_targets.items())
        },
        "parent_238_route_counts": dict(sorted(parent_238_routes.items())),
        "spacemap_segment_address_target_counts_by_relation": {
            relation: dict(
                sorted(
                    Counter(
                        f"PSMspacemap/0x{int(child['ref']) & 0xFFFFE000:08x}"
                        for node in nodes
                        for child in node.get("children", [])
                        if str(int(child["relation"])) == relation
                        and f"PSMspacemap/0x{int(child['ref']) & 0xFFFFE000:08x}" in streams
                    ).items()
                )
            )
            for relation in sorted(categories)
        },
        "semantic_limit": (
            "relations classify hierarchy routing targets by namespace; a segment address is not a validated node id, "
            "Sheet primitive, component class, or rendering instruction"
        ),
    }


def summarize_mixed_relation_parent_target_namespaces(
    relation_sequence: dict[str, object],
    sheet_local_root_by_ref: dict[int, str],
    sheet_stream_by_id: dict[int, str],
    named_layer_name_by_ref: dict[int, str],
    page_default_object_refs: set[int],
    page_layer_container_refs: set[int],
    page_linked_control_refs: set[int],
    jsite_resource_ids: set[int],
    dynamic_attribute_refs: set[int],
    tseg_node_ids: set[int],
    sheet_graphic_refs: set[int],
    sheet_local_primitive_refs: set[int],
    spacemap_segment_bases: set[int],
    envelope_refs: set[int],
) -> dict[str, object]:
    """Classify the fully framed mixed-map edges by both endpoint namespaces.

    The relationship code alone is polymorphic in this export family.  This
    summary records only independently known endpoint namespaces so a caller
    can distinguish a physical-page root to named-layer membership from an
    unrelated container edge with the same relation code.
    """

    def namespace(ref: int, *, is_parent: bool) -> str:
        if ref in sheet_local_root_by_ref:
            return "physical-sheet-local-root"
        if ref in sheet_stream_by_id:
            return "physical-sheet-stream-id"
        if ref in named_layer_name_by_ref:
            return "named-layer-object"
        if ref in page_default_object_refs:
            return "page-default-object"
        if ref in page_layer_container_refs:
            return "page-layer-container-object"
        if ref in page_linked_control_refs:
            return "page-linked-control-object"
        if ref in jsite_resource_ids:
            return "jsite-resource"
        if not is_parent and ref in dynamic_attribute_refs:
            return "dynamic-attribute-ref-0089"
        if not is_parent and ref in tseg_node_ids:
            return "tseg-node-id"
        if not is_parent and ref in sheet_graphic_refs:
            return "decoded-sheet-graphic-ref"
        if not is_parent and ref in sheet_local_primitive_refs:
            return "decoded-sheet-local-primitive-ref"
        # Base zero is the ordinary mixed map itself; a low local reference
        # must not be promoted to a segment address merely because that map
        # exists. Only the explicit high-space selectors are proven routing
        # addresses in this export family.
        if not is_parent and ref >= 0x2000 and (ref & 0xFFFFE000) in spacemap_segment_bases:
            return "psm-spacemap-segment-address"
        if not is_parent and ref in envelope_refs:
            return "psmcluster0-envelope"
        return "unclassified-parent" if is_parent else "unclassified-target"

    patterns: Counter[tuple[int, str, str]] = Counter()
    unclassified_targets: Counter[tuple[int, int, int]] = Counter()
    for record in relation_sequence.get("records", []):
        if record.get("kind") != "relation-container":
            continue
        parent_namespace = namespace(int(record["parent_ref"]), is_parent=True)
        for child in record["children"]:
            relation = int(child["relation"])
            target = int(child["child_ref"])
            target_namespace = namespace(target, is_parent=False)
            patterns[(relation, parent_namespace, target_namespace)] += 1
            if target_namespace == "unclassified-target":
                unclassified_targets[(relation, int(record["parent_ref"]), target)] += 1
    return {
        "layout": "mixed-psm-relation-parent-and-target-namespace-summary",
        "edge_count": sum(patterns.values()),
        "patterns": [
            {
                "relation_code": relation,
                "parent_namespace": parent_namespace,
                "target_namespace": target_namespace,
                "edge_count": count,
            }
            for (relation, parent_namespace, target_namespace), count in sorted(patterns.items())
        ],
        "unclassified_target_samples": [
            {
                "relation_code": relation,
                "parent_ref": parent_ref,
                "target_ref": target_ref,
                "occurrence_count": count,
            }
            for (relation, parent_ref, target_ref), count in unclassified_targets.most_common(20)
        ],
        "semantic_limit": (
            "an endpoint namespace validates routing context only; it does not name the PSM parent, establish "
            "visibility, or classify a piping component"
        ),
    }


def classify_counted_relation_201_dynamic_graphic_bindings(
    streams: dict[str, bytes], nodes: list[dict[str, object]], dynamic_graphic_refs: set[int]
) -> dict[str, object]:
    """Validate counted-layout relation-201 routes to direct Sheet graphics.

    This applies only to the bounded standard/prefixed counted ``0x0000``
    forms.  A matching target is still a graphic-route relation, not a
    component type or a proof that every UCI has one visible primitive.
    """

    sheet_families: dict[int, set[str]] = {}
    for sheet_name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", sheet_name):
            continue
        for family, parser in (
            ("18_32_line", parse_18_32_layer_bindings),
            ("59_2b_circle", parse_59_2b_page_layer_bindings),
            ("61_arc", parse_61_pipe_arc_records),
            ("13_63_circle_companion", parse_13_63_circle_geometry),
        ):
            for record in parser(data):
                sheet_families.setdefault(int(record["graphic_ref"]), set()).add(family)
        for composite in parse_7b_composite_headers(data):
            sheet_families.setdefault(int(composite["composite_ref"]), set()).add("7b_composite")
        for text in parse_4d_text_layer_bindings(data):
            # Coordinate triplets (E/N/EL) can share this non-child reference;
            # counted-layout relation 201 routes to that shared text-group id.
            sheet_families.setdefault(int(text["secondary_ref"]), set()).add("4d_text_secondary_ref")

    targets = [
        int(child["ref"])
        for node in nodes
        for child in node.get("children", [])
        if int(child["relation"]) == 201 and int(child["ref"]) in dynamic_graphic_refs
    ]
    distinct_targets = sorted(set(targets))
    matched_targets = [target for target in distinct_targets if target in sheet_families]
    family_counts: Counter[str] = Counter(
        family
        for target in matched_targets
        for family in sheet_families[target]
    )
    return {
        "layout_scope": "standard-or-prefixed-counted-PSMspacemap-0x0000-only",
        "relation_201_dynamic_graphic_edge_count": len(targets),
        "distinct_dynamic_graphic_target_count": len(distinct_targets),
        "direct_sheet_graphic_match_count": len(matched_targets),
        "unmatched_dynamic_graphic_target_count": len(distinct_targets) - len(matched_targets),
        "direct_sheet_family_counts": dict(sorted(family_counts.items())),
        "semantic_limit": (
            "a match validates a dynamic-graphic-to-Sheet-graphic route only; it does not assign a component "
            "class, UCI identity, or one-to-one visible geometry"
        ),
    }


def summarize_prefixed_zero_relation_semantics(
    nodes: list[dict[str, object]], relation_evidence: dict[str, object]
) -> dict[str, object]:
    """Name only the relation routes proven for prefixed ``0x0000`` maps.

    Relation numbers are not global Shape2D opcodes.  These labels apply only
    when their target namespaces are observed in a fully consumed prefixed
    ``0x0000`` node table.
    """

    categories = relation_evidence.get("relation_target_categories", {})
    if not isinstance(categories, dict):
        return {}
    target_counts: dict[str, Counter[int]] = {}
    nodes_by_id = {int(node["id"]): node for node in nodes if "id" in node}
    for node in nodes:
        for child in node.get("children", []):
            relation = str(int(child["relation"]))
            target_counts.setdefault(relation, Counter())[int(child["ref"])] += 1
    result: dict[str, object] = {}
    for relation, raw_counts in categories.items():
        if not isinstance(raw_counts, dict):
            continue
        counts = {str(key): int(value) for key, value in raw_counts.items()}
        total = counts.get("total", 0)
        terminal_paths: Counter[tuple[int, ...]] = Counter()
        if relation == "184":
            for node in nodes:
                for child in node.get("children", []):
                    if str(int(child["relation"])) != relation:
                        continue
                    path = [int(node["id"]), int(child["ref"])]
                    seen = set(path)
                    while path[-1] in nodes_by_id:
                        next_refs = [
                            int(candidate["ref"])
                            for candidate in nodes_by_id[path[-1]].get("children", [])
                            if str(int(candidate["relation"])) == relation
                        ]
                        if len(next_refs) != 1 or next_refs[0] in seen:
                            break
                        path.append(next_refs[0])
                        seen.add(next_refs[0])
                    terminal_paths[tuple(path)] += 1
        terminal_count = sum(terminal_paths.values())
        terminal_refs: Counter[int] = Counter()
        for path, count in terminal_paths.items():
            terminal_refs[path[-1]] += count
        if relation == "190" and total and counts.get("dynamic_attribute_ref_0089") == total:
            meaning = "dynamic-attribute-route"
        elif relation == "181" and counts.get("sheet_header_local_start", 0):
            meaning = "physical-sheet-root-or-layout-anchor-route"
        elif relation == "182" and counts.get("jsite_resource", 0):
            meaning = "jsite-resource-placement-route"
        elif relation == "183" and counts.get("psmcluster0_envelope", 0):
            meaning = "template-frame-or-layout-anchor-route"
        elif relation == "184" and total and terminal_count == total and len(terminal_refs) == 1:
            meaning = "shared-internal-terminal-anchor-route"
        else:
            meaning = "unresolved-internal-routing"
        result[relation] = {
            "classification": meaning,
            "total": total,
            "direct_sheet_root_count": counts.get("sheet_header_local_start", 0),
            "direct_jsite_resource_count": counts.get("jsite_resource", 0),
            "direct_dynamic_attribute_count": counts.get("dynamic_attribute_ref_0089", 0),
            "psmcluster_envelope_count": counts.get("psmcluster0_envelope", 0),
            "top_target_refs": [
                {"ref": ref, "count": count}
                for ref, count in target_counts.get(str(relation), Counter()).most_common(8)
            ],
            "terminal_ref_counts": {str(ref): count for ref, count in sorted(terminal_refs.items())},
            "terminal_path_counts": [
                {"path": list(path), "count": count}
                for path, count in terminal_paths.most_common(12)
            ],
            "scope": "prefixed-PSMspacemap-0x0000-only",
        }
    return result


def classify_prefixed_zero_relation_201_geometry_companions(
    streams: dict[str, bytes], nodes: list[dict[str, object]]
) -> dict[str, object]:
    """Resolve bounded relation-201 targets to Sheet geometry companions.

    This is deliberately narrower than the separate 0x8000 UCI fallback:
    here relation 201 points at ``0x13`` companion primitive refs, which in
    turn validate against their owning visible line or ellipse.
    """

    range_by_ref: dict[int, list[dict[str, object]]] = {}
    circle_by_ref: dict[int, list[dict[str, object]]] = {}
    lines_by_graphic_layer: dict[tuple[int, int], list[dict[str, object]]] = {}
    lines_by_child_ref: dict[int, dict[str, object]] = {}
    ellipses_by_graphic_layer: dict[tuple[int, int], list[dict[str, object]]] = {}
    ellipses_by_primitive_ref: dict[int, dict[str, object]] = {}
    for name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", name):
            continue
        for record in parse_13_ac_layer_relations(data):
            range_by_ref.setdefault(int(record["primitive_ref"]), []).append(record)
        for record in parse_13_63_circle_geometry(data):
            circle_by_ref.setdefault(int(record["primitive_ref"]), []).append(record)
        for record in parse_18_32_layer_bindings(data):
            lines_by_graphic_layer.setdefault(
                (int(record["graphic_ref"]), int(record["page_layer_ref"])), []
            ).append(record)
            lines_by_child_ref[int(record["child_ref"])] = record
        for record in parse_59_2b_page_layer_bindings(data):
            ellipses_by_graphic_layer.setdefault(
                (int(record["graphic_ref"]), int(record["page_layer_ref"])), []
            ).append(record)
            ellipses_by_primitive_ref[int(record["primitive_ref"])] = record

    target_refs = [
        int(child["ref"])
        for node in nodes
        for child in node.get("children", [])
        if int(child["relation"]) == 201
    ]
    range_match_count = 0
    range_group_bbox_single_line_match_count = 0
    range_all_member_lines_validated_count = 0
    circle_match_count = 0
    circle_ellipse_validated_count = 0
    circle_ellipse_adjacent_primitive_count = 0
    for target in target_refs:
        for record in range_by_ref.get(target, []):
            range_match_count += 1
            left, bottom, right, top = [float(value) for value in record["bounding_box"]]
            for line in lines_by_graphic_layer.get(
                (int(record["graphic_ref"]), int(record["page_layer_ref"])), []
            ):
                x1, y1 = [float(value) for value in line["start"]]
                x2, y2 = [float(value) for value in line["end"]]
                if max(
                    abs(left - min(x1, x2)),
                    abs(bottom - min(y1, y2)),
                    abs(right - max(x1, x2)),
                    abs(top - max(y1, y2)),
                ) < 1e-9:
                    range_group_bbox_single_line_match_count += 1
                    break
            member_lines_valid = True
            for segment, child_ref in zip(record["segments"], record["member_child_refs"]):
                line = lines_by_child_ref.get(int(child_ref))
                if line is None:
                    member_lines_valid = False
                    break
                segment_points = (segment["start"], segment["end"])
                line_points = (line["start"], line["end"])
                if segment_points != line_points and segment_points != line_points[::-1]:
                    member_lines_valid = False
                    break
            if member_lines_valid:
                range_all_member_lines_validated_count += 1
        for record in circle_by_ref.get(target, []):
            circle_match_count += 1
            direct_ellipse = ellipses_by_primitive_ref.get(int(record["member_child_ref"]))
            candidate_ellipses = [direct_ellipse] if direct_ellipse is not None else []
            for ellipse in candidate_ellipses:
                if (
                    int(ellipse["graphic_ref"]) == int(record["graphic_ref"])
                    and int(ellipse["page_layer_ref"]) == int(record["page_layer_ref"])
                    and
                    math.hypot(
                        float(record["center"][0]) - float(ellipse["x"]),
                        float(record["center"][1]) - float(ellipse["y"]),
                    ) < 1e-9
                ):
                    circle_ellipse_validated_count += 1
                    if int(record["primitive_ref"]) == int(ellipse["primitive_ref"]) + 1:
                        circle_ellipse_adjacent_primitive_count += 1
                    break
    return {
        "relation_201_target_count": len(target_refs),
        "range_companion_match_count": range_match_count,
        "range_companion_group_bbox_single_line_match_count": range_group_bbox_single_line_match_count,
        "range_companion_all_member_lines_validated_count": range_all_member_lines_validated_count,
        "circle_companion_match_count": circle_match_count,
        "circle_companion_ellipse_validated_count": circle_ellipse_validated_count,
        "circle_companion_ellipse_adjacent_primitive_count": circle_ellipse_adjacent_primitive_count,
        "semantic_limit": (
            "these are geometry-companion routes only; they do not directly assign UCI or visible-object identity"
        ),
    }


def parse_standard_nodes_with_12_byte_tail(data: bytes) -> dict[str, object]:
    """Parse the observed ``0x0000`` standard-node variant with a 12-byte tail.

    This differs from the normal full-stream node table: the low uint16 at
    byte 4 declares the exact node count, followed by that many ordinary
    ``<4H> + <IH>`` nodes and one independently bounded 12-byte footer.
    Callers must prefer the complete mixed-relation parser when it also
    succeeds: overlapping byte framings occur in some exports.
    """

    if len(data) < 24 or data[:4] != b"tseg":
        raise ValueError("not a sufficiently long tseg stream")
    declared_count, header_flag = struct.unpack_from("<2H", data, 4)
    if declared_count == 0 or header_flag == 1:
        raise ValueError("not the standard-counted 12-byte-tail variant")
    offset = 12
    nodes: list[dict[str, object]] = []
    for _ in range(declared_count):
        if offset + 8 > len(data):
            raise ValueError("truncated counted node table")
        node_id, node_type, child_count, repeated_count = struct.unpack_from("<4H", data, offset)
        end = offset + 8 + child_count * 6
        if child_count > 500 or end > len(data):
            raise ValueError("invalid counted node")
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "child_count": child_count,
                "repeated_count": repeated_count,
                "parent_ref": repeated_count if uses_tri16_relation_payload(node_type, node_id) else None,
                "children": parse_counted_node_payload(data, offset + 8, child_count, node_type, node_id)[0],
                "opaque_payload_entry_count": parse_counted_node_payload(data, offset + 8, child_count, node_type, node_id)[1],
                "zero_relation_list_refs": parse_counted_zero_relation_list(data, offset + 8, child_count, node_type, node_id, repeated_count),
                "zero_relation_extension_refs": parse_counted_zero_relation_extension(data, offset + 8, child_count, node_type, node_id),
            }
        )
        offset = end
    tail_record = parse_counted_tail_root(data, offset)
    return {
        "layout": "standard-counted-nodes-with-12-byte-tail",
        "header_u16": [declared_count, header_flag],
        "node_count": len(nodes),
        "nodes": nodes,
        "tail_offset": offset,
        "tail": tail_record,
        "fully_consumed": True,
    }


def parse_prefixed_counted_nodes_with_12_byte_tail(data: bytes) -> dict[str, object]:
    """Parse a counted ``0x0000`` variant with a leading uint16 reference list.

    The fourth uint16 header field is the exact number of uint16 values between
    the header and the ordinary node table. It is a bounded namespace list;
    its entries have no decoded object meaning and are retained verbatim.
    """

    if len(data) < 24 or data[:4] != b"tseg":
        raise ValueError("not a sufficiently long tseg stream")
    declared_count, header_flag, header_value, prefix_count = struct.unpack_from("<4H", data, 4)
    if declared_count == 0 or header_flag != 10 or prefix_count == 0:
        raise ValueError("not the prefixed-counted 12-byte-tail variant")
    offset = 12 + prefix_count * 2
    if offset + 8 > len(data):
        raise ValueError("truncated prefixed uint16 list")
    prefix_values = list(struct.unpack_from(f"<{prefix_count}H", data, 12))
    nodes: list[dict[str, object]] = []
    for _ in range(declared_count):
        if offset + 8 > len(data):
            raise ValueError("truncated prefixed counted node table")
        node_id, node_type, child_count, repeated_count = struct.unpack_from("<4H", data, offset)
        end = offset + 8 + child_count * 6
        if child_count > 500 or end > len(data):
            raise ValueError("invalid prefixed counted node")
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "child_count": child_count,
                "repeated_count": repeated_count,
                "parent_ref": repeated_count if uses_tri16_relation_payload(node_type, node_id) else None,
                "children": parse_counted_node_payload(data, offset + 8, child_count, node_type, node_id)[0],
                "opaque_payload_entry_count": parse_counted_node_payload(data, offset + 8, child_count, node_type, node_id)[1],
                "zero_relation_list_refs": parse_counted_zero_relation_list(data, offset + 8, child_count, node_type, node_id, repeated_count),
                "zero_relation_extension_refs": parse_counted_zero_relation_extension(data, offset + 8, child_count, node_type, node_id),
            }
        )
        offset = end
    tail_record = parse_counted_tail_root(data, offset)
    return {
        "layout": "prefixed-counted-nodes-with-12-byte-tail",
        "header_u16": [declared_count, header_flag, header_value, prefix_count],
        "prefix_uint16_values": prefix_values,
        "node_count": len(nodes),
        "nodes": nodes,
        "tail_offset": offset,
        "tail": tail_record,
        "fully_consumed": True,
    }


def classify_zero_tail_child(
    streams: dict[str, bytes],
    child: dict[str, object],
    dynamic_attribute_refs: set[int],
) -> dict[str, object]:
    """Classify a structurally bounded ``0x0000`` tail child by namespace.

    A tail child is structurally real, but it is not necessarily a node id.
    Observed ``201`` values can be addresses inside a present 0x2000-sized
    PSM segment. Preserve this distinction so callers cannot turn a segment
    selector into geometry.
    """

    target = int(child["ref"])
    relation = int(child["relation"])
    map_base = target & 0xFFFFE000
    map_name = f"PSMspacemap/0x{map_base:08x}"
    evidence: dict[str, object] = {
        "target_ref": target,
        "target_ref_hex": f"0x{target:04X}",
        "relation": relation,
        "is_dynamic_attribute_ref_0089": target in dynamic_attribute_refs,
        "spacemap_segment": map_name if map_name in streams else None,
        "spacemap_segment_offset": target - map_base if map_name in streams else None,
    }
    if relation == 190 and target in dynamic_attribute_refs:
        evidence["classification"] = "dynamic-attribute-reference"
    elif relation == 201 and map_name in streams:
        evidence["classification"] = "spacemap-segment-address"
        evidence["semantic_limit"] = (
            "address/selector is proven only at the 0x2000 segment namespace; "
            "its object, page, layer, and primitive meaning is unresolved"
        )
    else:
        evidence["classification"] = "unresolved-reference"
    return evidence


def classify_counted_zero_tail_target(
    streams: dict[str, bytes],
    tail: dict[str, object],
    dynamic_attribute_refs: set[int],
) -> dict[str, object] | None:
    """Add namespace evidence to a validated counted-``0x0000`` tail."""

    if tail.get("kind") == "tail-root":
        children = tail.get("children", [])
        if isinstance(children, list) and len(children) == 1 and isinstance(children[0], dict):
            return classify_zero_tail_child(streams, children[0], dynamic_attribute_refs)
        return None
    if tail.get("kind") != "one-child-tail-root":
        return None
    child = tail.get("child")
    if not isinstance(child, dict):
        return None
    return classify_zero_tail_child(streams, child, dynamic_attribute_refs)


def decoded_sheet_reference_ids(streams: dict[str, bytes]) -> set[int]:
    """Collect proven reference fields from currently decoded Sheet families."""

    refs: set[int] = set()
    families = (
        parse_18_32_layer_bindings,
        parse_4d_text_layer_bindings,
        parse_13_ac_layer_relations,
        parse_59_2b_page_layer_bindings,
        parse_61_pipe_arc_records,
        parse_13_63_circle_geometry,
        parse_7b_composite_headers,
    )
    for name, data in streams.items():
        if not re.fullmatch(r"Sheet\d+", name):
            continue
        for parser in families:
            for record in parser(data):
                refs.update(
                    int(value)
                    for key, value in record.items()
                    if key.endswith("_ref") and isinstance(value, int)
                )
    return refs


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


def parse_zero_prefix_and_tail(data: bytes) -> dict[str, object]:
    """Recover only independently repeatable structures from spacemap ``0x0000``.

    The beginning is a short, zero-terminated sequence of type-2/type-3
    records using the proven ``<4H> + <IH>`` child layout.  The final 12 or 18
    bytes are a separate type-3 root block with one or two known-relation
    children.  The variable middle region has another layout (notably its
    type-1 records), so it is intentionally not walked or interpreted.
    """

    if len(data) < 30 or data[:4] != b"tseg":
        raise ValueError("not a sufficiently long tseg stream")
    declared_node_count, header_flag = struct.unpack_from("<2H", data, 4)
    offset = 12
    prefix_nodes: list[dict[str, object]] = []
    while True:
        if offset + 8 > len(data):
            raise ValueError("missing zero terminator after high-level prefix")
        node_id, node_type, child_count, repeated_count = struct.unpack_from("<4H", data, offset)
        if (node_id, node_type, child_count) == (0, 0, 0):
            prefix_end = offset + 8
            break
        end = offset + 8 + child_count * 6
        if node_type not in {2, 3} or child_count > 8 or end > len(data):
            raise ValueError(f"unexpected prefix record at {offset}: type={node_type}, children={child_count}")
        children = [
            {"ref": child_ref, "relation": relation}
            for child_ref, relation in (
                struct.unpack_from("<IH", data, offset + 8 + child_index * 6)
                for child_index in range(child_count)
            )
        ]
        if any(int(child["relation"]) not in {182, 183, 184, 190} for child in children):
            raise ValueError(f"unexpected prefix relation at {offset}")
        prefix_nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "child_count": child_count,
                "repeated_count": repeated_count,
                "children": children,
            }
        )
        offset = end

    root: dict[str, object] | None = None
    for candidate_offset in (len(data) - 18, len(data) - 12):
        if candidate_offset < prefix_end or candidate_offset + 6 > len(data):
            continue
        root_type, child_count, repeated_count = struct.unpack_from("<3H", data, candidate_offset)
        end = candidate_offset + 6 + child_count * 6
        if root_type != 3 or child_count not in {1, 2} or repeated_count != child_count or end != len(data):
            continue
        children = [
            {"ref": child_ref, "relation": relation}
            for child_ref, relation in (
                struct.unpack_from("<IH", data, candidate_offset + 6 + child_index * 6)
                for child_index in range(child_count)
            )
        ]
        if all(int(child["relation"]) in {190, 201} for child in children):
            root = {
                "offset": candidate_offset,
                "type": root_type,
                "child_count": child_count,
                "repeated_count": repeated_count,
                "children": children,
            }
            break
    if root is None:
        raise ValueError("no independently recognizable tail root block")
    return {
        "header_u16": [declared_node_count, header_flag],
        "prefix_nodes": prefix_nodes,
        "prefix_terminator_offset": prefix_end - 8,
        "unparsed_middle_bytes": int(root["offset"]) - prefix_end,
        "tail_root": root,
    }


def parse_tseg_u16_list(data: bytes) -> dict[str, object]:
    """Parse the exact-length uint16-list variant used by short spacemaps.

    The format has a ``tseg`` tag and four uint16 header fields.  Its final
    header field is a count of the remaining uint16 values.  Field semantics
    are intentionally left unnamed because no direct Sheet relation is proven.
    """

    if len(data) < 12 or data[:4] != b"tseg":
        raise ValueError("not a sufficiently long tseg list")
    header = list(struct.unpack_from("<4H", data, 4))
    value_count = header[3]
    expected_length = 12 + value_count * 2
    if expected_length != len(data):
        raise ValueError(f"u16 list length mismatch: expected={expected_length}, actual={len(data)}")
    values = list(struct.unpack_from(f"<{value_count}H", data, 12)) if value_count else []
    transitions = [right - left for left, right in zip(values, values[1:])]
    return {
        "header_u16": header,
        "value_count": value_count,
        "values": values,
        "value_min": min(values) if values else None,
        "value_max": max(values) if values else None,
        "unique_value_count": len(set(values)),
        "strictly_ascending": all(delta > 0 for delta in transitions),
        "descending_transition_count": sum(delta < 0 for delta in transitions),
        "nonunit_positive_transition_count": sum(delta > 1 for delta in transitions),
        "header_field_3_equals_max_plus_two": bool(values) and header[2] == max(values) + 2,
        "fully_consumed": True,
    }


def parse_psm_segment_table(data: bytes) -> dict[str, object]:
    """Parse the exact counted-byte framing of ``PSMsegmenttable``."""

    if len(data) < 8 or data[:4] != b"stab":
        raise ValueError("not a stab segment table")
    count = struct.unpack_from("<I", data, 4)[0]
    if len(data) != 8 + count:
        raise ValueError(f"segment payload length mismatch: count={count}, actual={len(data) - 8}")
    return {
        "payload_count": count,
        "payload_bytes": list(data[8:]),
        "fully_consumed": True,
    }


def parse_zero_type1_relation_edges(data: bytes, zero_map: dict[str, object]) -> list[dict[str, int]]:
    """Inventory type-1-like byte patterns in map ``0x0000``.

    The unresolved middle contains a stable record signature:
    ``<2, 1, 1, parent_ref, 0, relation_code, child_ref>``.  The parent
    fields can occur at unaligned positions inside the now fully bounded
    type-2/type-3 relation-container payloads. They are therefore diagnostics
    only, not independently framed records or hierarchy edges.
    """

    start = int(zero_map["prefix_terminator_offset"]) + 8
    tail_root = zero_map["tail_root"]
    end = int(tail_root["offset"])
    edges: list[dict[str, int]] = []
    for offset in range(start, end - 14 + 1, 2):
        node_id, node_type, child_count, parent_ref, reserved, relation, child_ref = struct.unpack_from(
            "<7H", data, offset
        )
        if (node_id, node_type, child_count, reserved) != (2, 1, 1, 0):
            continue
        if relation not in {182, 183, 184}:
            continue
        edges.append(
            {
                "offset": offset,
                "parent_ref": parent_ref,
                "relation": relation,
                "child_ref": child_ref,
            }
        )
    return edges


def parse_zero_initial_relation_run(data: bytes, zero_map: dict[str, object]) -> dict[str, object]:
    """Parse the contiguous type-2/type-3 relation run after the zero prefix.

    This is a different layout from ``parse_tseg_nodes``.  It is accepted only
    while each record's repeated count agrees with its child count and every
    child is a ``<0, known_relation, child_ref>`` triple.  The first special
    record is left unread so later variants cannot shift this proven prefix.
    """

    start = int(zero_map["prefix_terminator_offset"]) + 8
    end = int(zero_map["tail_root"]["offset"])
    offset = start
    records: list[dict[str, object]] = []
    while offset + 8 <= end:
        record_type, child_count, repeated_count, parent_ref = struct.unpack_from("<4H", data, offset)
        record_end = offset + 8 + child_count * 6
        if (
            record_type not in {2, 3}
            or child_count == 0
            or child_count != repeated_count
            or child_count > 1000
            or record_end > end
        ):
            break
        children = [struct.unpack_from("<3H", data, offset + 8 + index * 6) for index in range(child_count)]
        if not all(reserved == 0 and relation in {181, 182, 183, 184, 190, 201} for reserved, relation, _ in children):
            break
        records.append(
            {
                "offset": offset,
                "record_type": record_type,
                "parent_ref": parent_ref,
                "children": [
                    {"relation": relation, "child_ref": child_ref}
                    for _, relation, child_ref in children
                ],
            }
        )
        offset = record_end
    return {
        "start_offset": start,
        "end_offset": offset,
        "record_count": len(records),
        "edge_count": sum(len(record["children"]) for record in records),
        "record_type_counts": dict(Counter(int(record["record_type"]) for record in records)),
        "records": records,
        "stopped_before_tail": offset < end,
    }


def parse_zero_extended_relation_run(data: bytes, zero_map: dict[str, object]) -> dict[str, object]:
    """Extend the initial relation run through its proven zero-target variant.

    Nine regular samples insert one fixed 20-byte record between otherwise
    ordinary type-2/type-3 relation containers.  Its ``child_ref=0`` makes it
    incompatible with the normal guard, but its full bytes and the following
    record boundary repeat exactly.  Stop before the later zero-child control
    block rather than inventing a size for that still-unresolved layout.
    """

    start = int(zero_map["prefix_terminator_offset"]) + 8
    end = int(zero_map["tail_root"]["offset"])
    zero_target_variant = bytes.fromhex(
        "02 00 01 00 02 00 07 00 00 00 b7 00 00 00 00 00 00 00 a3 02"
    )
    offset = start
    records: list[dict[str, object]] = []
    special_offsets: list[int] = []
    while offset + 8 <= end:
        record_type, child_count, repeated_count, parent_ref = struct.unpack_from("<4H", data, offset)
        record_end = offset + 8 + child_count * 6
        if (
            record_type in {2, 3}
            and child_count > 0
            and child_count == repeated_count
            and child_count <= 1000
            and record_end <= end
        ):
            children = [struct.unpack_from("<3H", data, offset + 8 + index * 6) for index in range(child_count)]
            if all(reserved == 0 and relation in {181, 182, 183, 184, 190, 201} for reserved, relation, _ in children):
                records.append(
                    {
                        "offset": offset,
                        "record_type": record_type,
                        "parent_ref": parent_ref,
                        "children": [
                            {"relation": relation, "child_ref": child_ref}
                            for _, relation, child_ref in children
                        ],
                    }
                )
                offset = record_end
                continue
        if data[offset : offset + len(zero_target_variant)] == zero_target_variant:
            special_offsets.append(offset)
            offset += len(zero_target_variant)
            continue
        break
    return {
        "start_offset": start,
        "end_offset": offset,
        "record_count": len(records),
        "edge_count": sum(len(record["children"]) for record in records),
        "record_type_counts": dict(Counter(int(record["record_type"]) for record in records)),
        "zero_target_variant_offsets": special_offsets,
        "records": records,
        "stopped_before_tail": offset < end,
    }


def parse_zero_relation_sequence(data: bytes, zero_map: dict[str, object]) -> dict[str, object]:
    """Decode all currently proven middle records in regular ``0x0000`` maps.

    The sequence combines ordinary relation containers, one fixed zero-target
    extension, and type-3 zero-relation lists.  It is accepted only when the
    sequence reaches the independently parsed tail-root boundary exactly.
    This keeps the AMSS1 leading variant explicitly unresolved.
    """

    start = int(zero_map["prefix_terminator_offset"]) + 8
    end = int(zero_map["tail_root"]["offset"])
    zero_target_variant = bytes.fromhex(
        "02 00 01 00 02 00 07 00 00 00 b7 00 00 00 00 00 00 00 a3 02"
    )
    offset = start
    records: list[dict[str, object]] = []
    while offset < end:
        if data[offset : offset + len(zero_target_variant)] == zero_target_variant:
            records.append({"kind": "zero-target-variant", "offset": offset})
            offset += len(zero_target_variant)
            continue
        if offset + 8 > end:
            break
        record_type, child_count, repeated_count, parent_ref = struct.unpack_from("<4H", data, offset)
        entry_count = max(child_count, repeated_count)
        record_end = offset + 8 + entry_count * 6
        if record_end > end or entry_count == 0 or entry_count > 1000:
            break
        entries = [struct.unpack_from("<3H", data, offset + 8 + index * 6) for index in range(entry_count)]
        # This bounded extension has the same first zero-target child as the
        # original literal variant, but its second all-zero-relation entry
        # carries a file-local opaque id. Keep that id rather than requiring
        # the one byte sequence first seen in the regular exports.
        if (
            (record_type, child_count, repeated_count, parent_ref) == (2, 1, 2, 7)
            and len(entries) == 2
            and entries[0] == (0, 183, 0)
            and entries[1][0] == 0
            and entries[1][1] == 0
        ):
            records.append(
                {
                    "kind": "zero-target-variant",
                    "offset": offset,
                    "opaque_local_ref": entries[1][2],
                }
            )
            offset = record_end
            continue
        if (
            record_type in {2, 3}
            and child_count == repeated_count
            and child_count > 0
            and all(reserved == 0 and relation in {181, 182, 183, 184, 190, 201} for reserved, relation, _ in entries)
        ):
            records.append(
                {
                    "kind": "relation-container",
                    "offset": offset,
                    "record_type": record_type,
                    "parent_ref": parent_ref,
                    "children": [
                        {"relation": relation, "child_ref": child_ref}
                        for _, relation, child_ref in entries
                    ],
                }
            )
            offset = record_end
            continue
        if (
            record_type == 3
            and child_count == 0
            and repeated_count > 0
            and parent_ref == 0
            and all(reserved == 0 and relation == 0 for reserved, relation, _ in entries)
        ):
            records.append(
                {
                    "kind": "zero-relation-list",
                    "offset": offset,
                    "child_refs": [child_ref for _, _, child_ref in entries],
                }
            )
            offset = record_end
            continue
        break
    counts = Counter(str(record["kind"]) for record in records)
    relation_records = [record for record in records if record["kind"] == "relation-container"]
    return {
        "start_offset": start,
        "end_offset": offset,
        "fully_consumed_to_tail_root": offset == end,
        "record_counts": dict(counts),
        "relation_container_count": len(relation_records),
        "relation_edge_count": sum(len(record["children"]) for record in relation_records),
        "records": records,
    }


def parse_amss1_leading_variant(data: bytes, zero_map: dict[str, object]) -> dict[str, object] | None:
    """Decode the observed AMSS1 continuation of the high-level prefix.

    AMSS1 splits its type-7/type-8 prefix nodes after an earlier zero record.
    The continuation has four bytes of padding, two ordinary high-level node
    records, then a second zero terminator before the regular relation stream.
    """

    start = int(zero_map["prefix_terminator_offset"]) + 8
    if start + 46 > len(data) or data[start : start + 4] != b"\0\0\0\0":
        return None
    offset = start + 4
    nodes: list[dict[str, object]] = []
    for expected_id, expected_type, expected_count, expected_repeat in ((7, 2, 1, 1), (8, 2, 2, 3)):
        node_id, node_type, child_count, repeated_count = struct.unpack_from("<4H", data, offset)
        if (node_id, node_type, child_count, repeated_count) != (
            expected_id,
            expected_type,
            expected_count,
            expected_repeat,
        ):
            return None
        end = offset + 8 + child_count * 6
        children = [
            {"ref": ref, "relation": relation}
            for ref, relation in (
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
    if struct.unpack_from("<4H", data, offset) != (0, 0, 0, 9):
        return None
    return {
        "offset": start,
        "padding_bytes": 4,
        "nodes": nodes,
        "terminator_offset": offset,
        "regular_relation_start": offset + 8,
    }


def analyze(sha_path: Path) -> dict[str, object]:
    streams = read_sha_streams(sha_path)
    try:
        ole_summary_metadata = parse_ole_summary_metadata(sha_path, streams)
    except (OSError, ValueError, olefile.OleFileError) as error:
        ole_summary_metadata = {"status": "unvalidated", "error": str(error)}
    stylecluster_font_records = parse_stylecluster_font_records(
        streams.get("StyleCluster", b"")
    )
    stylecluster_text_style_links = parse_stylecluster_text_style_links(
        streams.get("StyleCluster", b""), stylecluster_font_records
    )
    stylecluster_2e_style_records = parse_stylecluster_2e_style_records(streams.get("StyleCluster", b""))
    stylecluster_12_font_resources = parse_stylecluster_12_font_resources(streams.get("StyleCluster", b""))
    stylecluster_named_style_catalog_entries = parse_stylecluster_named_style_catalog_entries(streams.get("StyleCluster", b""))
    stylecluster_2f_dash_patterns = parse_stylecluster_2f_dash_patterns(streams.get("StyleCluster", b""))
    stylecluster_2a_fixed_style_records = parse_stylecluster_2a_fixed_style_records(streams.get("StyleCluster", b""))
    stylecluster_70_fixed_records = parse_stylecluster_70_fixed_records(streams.get("StyleCluster", b""))
    stylecluster_18_control_records = parse_stylecluster_18_control_records(streams.get("StyleCluster", b""))
    stylecluster_84_polygon_resources = parse_stylecluster_84_polygon_resources(streams.get("StyleCluster", b""))
    stylecluster_7c_polygon_groups = parse_stylecluster_7c_polygon_groups(streams.get("StyleCluster", b""))
    stylecluster_61_local_arc_resources = parse_stylecluster_61_local_arc_resources(
        streams.get("StyleCluster", b"")
    )
    stylecluster_59_local_ellipse_resources = parse_stylecluster_59_local_ellipse_resources(
        streams.get("StyleCluster", b"")
    )
    stylecluster_1b_named_internal_style_records = parse_stylecluster_1b_named_internal_style_records(
        streams.get("StyleCluster", b"")
    )
    stylecluster_local_resource_sheet_references = summarize_stylecluster_local_resource_sheet_references(
        streams,
        {
            "local-polygon-0x0084": stylecluster_84_polygon_resources["records"],
            "local-arc-0x0061": stylecluster_61_local_arc_resources["records"],
            "local-circle-0x0059": stylecluster_59_local_ellipse_resources["records"],
            "local-line-0x0018": stylecluster_18_control_records["records"],
            "local-text-template-0x0070": stylecluster_70_fixed_records["records"],
            "local-composition-0x007c": stylecluster_7c_polygon_groups["records"],
        },
    )
    stylecluster_zero_object_containers = parse_stylecluster_zero_object_containers(
        streams.get("StyleCluster", b"")
    )
    line_styles_by_object_ref: dict[int, list[dict[str, int]]] = {}
    for line_style in stylecluster_2e_style_records["records"]:
        line_styles_by_object_ref.setdefault(int(line_style["object_ref"]), []).append(line_style)
    dash_patterns_by_object_ref = {int(record["object_ref"]): record for record in stylecluster_2f_dash_patterns["records"]}
    dash_patterns_by_style_ref = {int(record["style_ref"]): record for record in stylecluster_2f_dash_patterns["records"]}
    fixed_styles_by_object_ref = {int(record["object_ref"]): record for record in stylecluster_2a_fixed_style_records["records"]}
    polygons_by_object_ref = {
        int(record["object_ref"]): record for record in stylecluster_84_polygon_resources["records"]
    }
    arcs_by_object_ref = {
        int(record["object_ref"]): record for record in stylecluster_61_local_arc_resources["records"]
    }
    ellipses_by_object_ref = {
        int(record["object_ref"]): record for record in stylecluster_59_local_ellipse_resources["records"]
    }
    controls_18_by_object_ref = {
        int(record["object_ref"]): record for record in stylecluster_18_control_records["records"]
    }
    text_templates_70_by_object_ref = {
        int(record["object_ref"]): record for record in stylecluster_70_fixed_records["records"]
    }
    for line_style in stylecluster_2e_style_records["records"]:
        if int(line_style["record_length"]) != 58:
            continue
        pattern_style_ref = int(line_style["tail_u32_hex"], 16)
        if pattern_style_ref in dash_patterns_by_style_ref:
            line_style["dash_pattern_style_ref"] = pattern_style_ref
    stylecluster_2e_style_records["dash_pattern_linked_record_count"] = sum(
        "dash_pattern_style_ref" in line_style for line_style in stylecluster_2e_style_records["records"]
    )
    dash_pattern_usage = summarize_known_dash_pattern_usage(
        streams,
        stylecluster_2e_style_records["records"],
        stylecluster_2f_dash_patterns["records"],
    )
    stylecluster_2e_category_usage = summarize_stylecluster_2e_category_usage(
        streams, stylecluster_2e_style_records["records"]
    )
    fixed_style_usage = summarize_known_fixed_style_usage(
        streams,
        stylecluster_2a_fixed_style_records["records"],
    )
    for entry in stylecluster_named_style_catalog_entries["records"]:
        linked_styles = line_styles_by_object_ref.get(int(entry["object_ref"]), [])
        dash_pattern = dash_patterns_by_object_ref.get(int(entry["object_ref"]))
        fixed_style = fixed_styles_by_object_ref.get(int(entry["object_ref"]))
        entry["line_style_refs"] = sorted({int(style["style_ref"]) for style in linked_styles})
        entry["dash_pattern_style_ref"] = int(dash_pattern["style_ref"]) if dash_pattern else None
        entry["fixed_style_ref"] = int(fixed_style["style_ref"]) if fixed_style else None
    stylecluster_named_style_catalog_entries["line_style_linked_record_count"] = sum(bool(entry["line_style_refs"]) for entry in stylecluster_named_style_catalog_entries["records"])
    stylecluster_named_style_catalog_entries["dash_pattern_linked_record_count"] = sum(entry["dash_pattern_style_ref"] is not None for entry in stylecluster_named_style_catalog_entries["records"])
    stylecluster_named_style_catalog_entries["fixed_style_linked_record_count"] = sum(entry["fixed_style_ref"] is not None for entry in stylecluster_named_style_catalog_entries["records"])
    fixed_style_catalog_names_by_object_ref: dict[int, list[str]] = {}
    for entry in stylecluster_named_style_catalog_entries["records"]:
        if entry["fixed_style_ref"] is None:
            continue
        fixed_style_catalog_names_by_object_ref.setdefault(int(entry["object_ref"]), []).append(
            str(entry["catalog_name"])
        )
    for record in stylecluster_2a_fixed_style_records["records"]:
        record["catalog_names"] = sorted(
            set(fixed_style_catalog_names_by_object_ref.get(int(record["object_ref"]), []))
        )
    stylecluster_2a_fixed_style_records["catalog_named_record_count"] = sum(
        bool(record["catalog_names"])
        for record in stylecluster_2a_fixed_style_records["records"]
    )
    for group in stylecluster_7c_polygon_groups["records"]:
        group["polygon_child_refs"] = [
            child_ref for child_ref in group["child_refs"] if child_ref in polygons_by_object_ref
        ]
        group["arc_child_refs"] = [
            child_ref for child_ref in group["child_refs"] if child_ref in arcs_by_object_ref
        ]
        group["ellipse_like_child_refs"] = [
            child_ref for child_ref in group["child_refs"] if child_ref in ellipses_by_object_ref
        ]
        group["local_line_child_refs"] = [
            child_ref for child_ref in group["child_refs"] if child_ref in controls_18_by_object_ref
        ]
        group["text_template_70_child_refs"] = [
            child_ref for child_ref in group["child_refs"] if child_ref in text_templates_70_by_object_ref
        ]
        member_family_by_ref = {
            **{child_ref: "local-polygon-0x0084" for child_ref in group["polygon_child_refs"]},
            **{child_ref: "local-arc-0x0061" for child_ref in group["arc_child_refs"]},
            **{child_ref: "local-ellipse-like-0x0059" for child_ref in group["ellipse_like_child_refs"]},
            **{child_ref: "local-line-0x0018" for child_ref in group["local_line_child_refs"]},
            **{child_ref: "local-text-template-0x0070" for child_ref in group["text_template_70_child_refs"]},
        }
        group["member_families"] = [
            {"object_ref": child_ref, "family": member_family_by_ref.get(child_ref, "unclassified")}
            for child_ref in group["child_refs"]
        ]
        group["unclassified_child_refs"] = [
            child_ref for child_ref in group["child_refs"] if child_ref not in member_family_by_ref
        ]
        group["all_children_are_polygons"] = len(group["polygon_child_refs"]) == int(group["child_count"])
    stylecluster_7c_polygon_groups["all_polygon_member_group_count"] = sum(
        bool(group["all_children_are_polygons"])
        for group in stylecluster_7c_polygon_groups["records"]
    )
    stylecluster_7c_polygon_groups["unclassified_child_ref_count"] = sum(
        len(group["unclassified_child_refs"])
        for group in stylecluster_7c_polygon_groups["records"]
    )
    try:
        jsite_resource_list: dict[str, object] = parse_jsites_list(streams["JSitesList"])
    except (KeyError, ValueError) as error:
        jsite_resource_list = {"status": "unvalidated", "error": str(error)}
    jsite_resources = (
        jsite_resource_inventory(streams, list(jsite_resource_list["resource_ids"]))
        if "resource_ids" in jsite_resource_list
        else []
    )
    physical_sheet_3d_placements: list[dict[str, object]] = []
    for sheet_name, sheet_data in sorted(streams.items()):
        if not re.fullmatch(r"Sheet\d+", sheet_name) or sheet_name == "Sheet221" or len(sheet_data) <= 1024:
            continue
        wrappers = parse_sheet_3d_placement_wrappers(sheet_data)
        if not wrappers:
            continue
        link_sheet_3d_resource_descriptors(wrappers, jsite_resources)
        link_contentless_jsite_sheet_templates(wrappers, jsite_resources, streams)
        physical_sheet_3d_placements.append({"sheet": sheet_name, "wrappers": wrappers})
    physical_sheet_3d_placement_summary = {
        "sheet_with_wrapper_count": len(physical_sheet_3d_placements),
        "wrapper_count": sum(len(entry["wrappers"]) for entry in physical_sheet_3d_placements),
        "wrapper_count_by_jsite_resource": dict(
            sorted(
                Counter(
                    int(wrapper["jsite_resource_id"])
                    for entry in physical_sheet_3d_placements
                    for wrapper in entry["wrappers"]
                ).items()
            )
        ),
        "sheets": physical_sheet_3d_placements,
    }
    try:
        appobject_dependency: dict[str, object] = parse_appobject_dependency(streams["AppObject"])
    except (KeyError, ValueError) as error:
        appobject_dependency = {"status": "unvalidated", "error": str(error)}
    try:
        docversion3_history: dict[str, object] = parse_docversion3_history(streams["DocVersion3"])
    except (KeyError, ValueError) as error:
        docversion3_history = {"status": "unvalidated", "error": str(error)}
    docversion2_profile = classify_docversion2_profile(streams.get("DocVersion2", b""))
    try:
        dynamic_attributes_metadata = parse_dynamic_attributes_metadata(
            streams["Dynamic Attributes Metadata"]
        )
    except (KeyError, ValueError) as error:
        dynamic_attributes_metadata = {"status": "unvalidated", "error": str(error)}
    try:
        tagged_text_storage_list = parse_tagged_text_storage_list(streams["JTaggedTxtStgList"])
    except (KeyError, ValueError) as error:
        tagged_text_storage_list = {"status": "unvalidated", "error": str(error)}
    fixed_empty_sheet_stubs: dict[str, dict[str, object]] = {}
    for sheet_name in ("Sheet12", "Sheet39", "Sheet65", "Sheet91", "Sheet117"):
        if sheet_name not in streams:
            continue
        try:
            fixed_empty_sheet_stubs[sheet_name] = classify_empty_sheet_stub(
                streams[sheet_name]
            )
        except ValueError as error:
            fixed_empty_sheet_stubs[sheet_name] = {
                "status": "unvalidated",
                "error": str(error),
                "byte_length": len(streams[sheet_name]),
            }
    tagged_text_xml = parse_tagged_text_xml_streams(streams)
    revision_binding_fields = template_revision_binding_field_names(
        streams.get("Sheet221", b"")
    )
    revision_binding_resolution = resolve_sheet221_revision_bindings(
        streams.get("Sheet221", b""), streams.get("TaggedTxtData/Revision"),
    )
    sheet221_data = streams.get("Sheet221", b"")
    sheet221_template_profile = {
        "byte_length": len(sheet221_data),
        "sha256": hashlib.sha256(sheet221_data).hexdigest(),
        "semantic_limit": "shared-template version identifier only; rules must still validate their own bounded record layouts",
    }
    text_style_links_by_ref = {
        int(record["style_ref"]): record
        for record in stylecluster_text_style_links["records"]
        if bool(record["font_resolved"])
    }
    physical_sheet_text_records = [
        {**record, "sheet_stream": name}
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name) and len(data) > 1024
        for record in parse_4d_text_layer_bindings(data)
    ]
    attach_4d_text_style_resources(physical_sheet_text_records, text_style_links_by_ref)
    physical_sheet_composite_child_links = {
        name: summarize_7b_composite_child_graphic_links(data)
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name) and len(data) > 1024
    }
    physical_sheet_text_values = {str(record["text"]) for record in physical_sheet_text_records}
    try:
        named_layer_records_for_mapping = parse_psmcluster_named_records(streams["PSMcluster0"])["records"]
        page_default_records_for_mapping = parse_psmcluster_88_page_default_records(
            streams["PSMcluster0"]
        )["records"]
        page_layer_container_records_for_mapping = parse_psmcluster_42_page_layer_containers(
            streams["PSMcluster0"]
        )["records"]
        page_linked_control_records_for_mapping = parse_psmcluster_57_page_linked_control_records(
            streams["PSMcluster0"]
        )["records"]
        named_layer_name_by_ref = {
            int(record["object_ref"]): str(record["name"])
            for record in named_layer_records_for_mapping
        }
    except (KeyError, ValueError):
        named_layer_records_for_mapping = []
        page_default_records_for_mapping = []
        page_layer_container_records_for_mapping = []
        page_linked_control_records_for_mapping = []
        named_layer_name_by_ref = {}
    psmcluster_envelope_runs = parse_psmcluster_envelope_runs(
        streams.get("PSMcluster0", b"")
    )
    physical_sheet_4d_text_psm_envelope_bindings = summarize_4d_text_psm_envelope_bindings(
        physical_sheet_text_records, psmcluster_envelope_runs
    )
    psmcluster_envelope_tag_evidence = psm_envelope_tag_provenance(
        streams, psmcluster_envelope_runs
    )
    psmcluster_envelope_tag_layer_evidence = psm_envelope_tag_layer_provenance(
        streams, psmcluster_envelope_runs, named_layer_name_by_ref
    )
    space_streams = {name: data for name, data in streams.items() if name.startswith("PSMspacemap/")}
    dynamic_attribute_records = attribute_ref_0089_records(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    dynamic_property_records = parse_dynamic_attribute_property_records(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    document_dynamic_settings = parse_document_dynamic_settings(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    compact_iso_attribute_records = parse_compact_iso_attribute_records(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    compact_iso_attribute_refs = {int(record["reference"]) for record in compact_iso_attribute_records}
    dynamic_property_attribute_refs = {int(record["reference"]) for record in dynamic_property_records}
    compact_iso_psm_envelope_count = sum(
        bool(psm_envelopes(streams.get("PSMcluster0", b""), int(record["graphic_ref"])))
        for record in compact_iso_attribute_records
    )
    dynamic_attribute_refs = {int(record["reference"]) for record in dynamic_attribute_records}
    dynamic_attribute_details: dict[int, list[dict[str, object]]] = {}
    for record in dynamic_attribute_records:
        dynamic_attribute_details.setdefault(int(record["reference"]), []).append(
            {
                "attribute_key": record["attribute_key"],
                "attribute_keys": record["attribute_keys"],
                "record_size": record["record_size"],
                "framing": record["framing"],
            }
        )
    dynamic_attribute_key_counts = dict(
        Counter(str(record["attribute_key"]) for record in dynamic_attribute_records)
    )
    dynamic_property_key_counts = dict(
        Counter(
            str(property_record["key"])
            for record in dynamic_property_records
            for property_record in list(record["properties"])
        )
    )
    dynamic_property_nonempty_value_counts = dict(
        Counter(
            str(property_record["key"])
            for record in dynamic_property_records
            for property_record in list(record["properties"])
            if str(property_record["value"])
        )
    )
    dynamic_property_signatures = dict(
        Counter(
            " | ".join(str(property_record["key"]) for property_record in list(record["properties"]))
            for record in dynamic_property_records
        )
    )
    dynamic_property_sheet_text_matches = summarize_dynamic_property_sheet_text_matches(
        dynamic_property_records, physical_sheet_text_values
    )
    dynamic_element_tag_text_candidates = summarize_element_tag_unique_text_candidates(
        dynamic_property_records, physical_sheet_text_records
    )
    dynamic_graphics_by_uci = dynamic_graphics(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    bounded_dynamic_graphics = bounded_dynamic_graphics_by_uci(dynamic_property_records)
    dynamic_graphic_sheet_primitive_bindings = summarize_dynamic_graphic_sheet_primitive_bindings(
        streams, bounded_dynamic_graphics
    )
    dynamic_property_uci_rows: list[tuple[str, int, int | None]] = []
    for record in dynamic_property_records:
        uci_property = next(
            (
                property_record
                for property_record in list(record["properties"])
                if property_record["key"] == "Unique Component Identifier"
                and re.fullmatch(r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}", str(property_record["value"]))
            ),
            None,
        )
        if uci_property is not None:
            dynamic_property_uci_rows.append(
                (str(uci_property["value"]), int(record["graphic_ref"]), record["uci_index"])
            )
    dynamic_property_uci_unique = {uci for uci, _, _ in dynamic_property_uci_rows}
    dynamic_property_uci_graphic_matches = sum(
        graphic_ref in {int(dynamic_record["graphic_ref"]) for dynamic_record in dynamic_graphics_by_uci.get(uci, [])}
        for uci, graphic_ref, _ in dynamic_property_uci_rows
    )
    dynamic_property_uci_instances: dict[str, list[int | None]] = {}
    for uci, _, uci_index in dynamic_property_uci_rows:
        dynamic_property_uci_instances.setdefault(uci, []).append(uci_index)
    nonzero_uci_index_rows = sum(
        uci_index is not None and uci_index > 0
        for _, _, uci_index in dynamic_property_uci_rows
    )
    nonzero_uci_index_with_duplicate_uci = sum(
        uci_index is not None
        and uci_index > 0
        and len(dynamic_property_uci_instances[uci]) > 1
        for uci, _, uci_index in dynamic_property_uci_rows
    )
    dynamic_uci_by_graphic_ref = {
        int(record["graphic_ref"]): str(uci)
        for uci, records in dynamic_graphics_by_uci.items()
        for record in records
    }
    dynamic_refs = set(dynamic_uci_by_graphic_ref)
    sheet_local_starts = sorted(
        [
            (struct.unpack_from("<I", data, 14)[0], name)
            for name, data in streams.items()
            if re.fullmatch(r"Sheet\d+", name) and len(data) >= 18
        ]
    )

    def local_sheet_owner(ref: int) -> str | None:
        owner: str | None = None
        for start, name in sheet_local_starts:
            if start > ref:
                break
            owner = name
        return owner
    main_name = "PSMspacemap/0x00008000"
    main = space_streams.get(main_name)
    if main is None:
        # RHO1-style compact exports retain the root registry and short maps
        # but omit this larger hierarchy map entirely.
        main_result: dict[str, object] = {
            "layout": "absent-in-this-sha-variant",
            "semantic_status": "no 0x8000 hierarchy map is stored; other PSM streams remain analyzable",
        }
        relation_result: dict[str, int] = {}
    else:
        try:
            hierarchy = parse_tseg_nodes(main)
            relation_counts = Counter(
                child["relation"]
                for node in hierarchy["nodes"]
                for child in node["children"]
            )
            main_result = {"layout": "validated-full-node-table", "tseg": hierarchy}
            main_result["relation_target_namespace_evidence"] = classify_tseg_relation_targets(
                streams,
                hierarchy["nodes"],
                named_layer_name_by_ref,
                {start: sheet_name for start, sheet_name in sheet_local_starts},
                dynamic_refs,
                dynamic_attribute_refs,
                {int(resource_id) for resource_id in jsite_resource_list.get("resource_ids", [])},
            )
            relation_result = {str(key): value for key, value in sorted(relation_counts.items())}
        except ValueError as standard_error:
            try:
                hierarchy = parse_prefixed_tseg_nodes(main)
            except ValueError as prefixed_error:
                # Different Shape2D exports can use another compact layout
                # under the same stream name. Keep it explicitly
                # inventory-only rather than inventing node/child boundaries
                # from a parser that did not consume the source stream safely.
                main_result = {
                    "layout": "unvalidated-variant",
                    "parse_error": str(standard_error),
                    "prefixed_parse_error": str(prefixed_error),
                    "partial_summary": partial_tseg_summary(main),
                }
                relation_result = {}
            else:
                relation_counts = Counter(
                    child["relation"]
                    for node in hierarchy["nodes"]
                    for child in node["children"]
                )
                main_result = {"layout": "validated-prefixed-index-node-table", "tseg": hierarchy}
                main_result["relation_target_namespace_evidence"] = classify_tseg_relation_targets(
                    streams,
                    hierarchy["nodes"],
                    named_layer_name_by_ref,
                    {start: sheet_name for start, sheet_name in sheet_local_starts},
                    dynamic_refs,
                    dynamic_attribute_refs,
                    {int(resource_id) for resource_id in jsite_resource_list.get("resource_ids", [])},
                )
                relation_result = {str(key): value for key, value in sorted(relation_counts.items())}
    additional_validated: dict[str, object] = {}
    remaining_undecoded: dict[str, object] = {}
    for name, data in space_streams.items():
        if name == main_name:
            continue
        if name in {
            "PSMspacemap/0x00002000",
            "PSMspacemap/0x00004000",
            "PSMspacemap/0x00006000",
        }:
            # These headers can look like an empty ordinary node table. Their
            # declared uint16-list length is a stronger, stream-specific test.
            try:
                list_map = parse_tseg_u16_list(data)
            except ValueError as list_error:
                list_summary = partial_tseg_summary(data)
                list_summary["u16_list_parse_error"] = str(list_error)
                remaining_undecoded[name] = list_summary
            else:
                additional_validated[name] = {
                    "layout": "validated-exact-u16-list",
                    "header_u16": list_map["header_u16"],
                    "value_count": list_map["value_count"],
                    "values": list_map["values"],
                    "value_min": list_map["value_min"],
                    "value_max": list_map["value_max"],
                    "unique_value_count": list_map["unique_value_count"],
                    "strictly_ascending": list_map["strictly_ascending"],
                    "descending_transition_count": list_map["descending_transition_count"],
                    "nonunit_positive_transition_count": list_map["nonunit_positive_transition_count"],
                    "header_field_3_equals_max_plus_two": list_map["header_field_3_equals_max_plus_two"],
                    "numeric_interval_sheet_owners": dict(
                        Counter(
                            owner
                            for value in list_map["values"]
                            if (owner := local_sheet_owner(int(value))) is not None
                        )
                    ),
                    # The middle header values look like local-id bounds in
                    # some empty maps.  Record only their proven namespace
                    # and PSM-envelope evidence; their field semantics are
                    # deliberately not named as a range or object class.
                    "middle_header_numeric_interval_sheet_owners": {
                        "field_1": local_sheet_owner(int(list_map["header_u16"][1])),
                        "field_2": local_sheet_owner(int(list_map["header_u16"][2])),
                    },
                    "middle_header_psm_envelopes": {
                        "field_1": psm_envelopes(
                            streams.get("PSMcluster0", b""), int(list_map["header_u16"][1])
                        ),
                        "field_2": psm_envelopes(
                            streams.get("PSMcluster0", b""), int(list_map["header_u16"][2])
                        ),
                    },
                    "semantic_status": "field and value meanings unresolved; inventory only",
                }
                if name == "PSMspacemap/0x00002000":
                    sheet221 = streams.get("Sheet221", b"")
                    values = {int(value) for value in list_map["values"]}
                    additional_validated[name].update(
                        {
                            "sheet221_raw_u16_occurrence_count": sum(
                                struct.pack("<H", int(value)) in sheet221
                                for value in list_map["values"]
                            ),
                            "sheet221_raw_u32_occurrence_count": sum(
                                struct.pack("<I", int(value)) in sheet221
                                for value in list_map["values"]
                            ),
                            "dynamic_graphic_ref_match_count": len(values & dynamic_refs),
                            "dynamic_attribute_ref_0089_match_count": len(values & dynamic_attribute_refs),
                            "named_psm_object_match_count": len(values & set(named_layer_name_by_ref)),
                            "decoded_visible_sheet_reference_match_count": len(
                                values & decoded_sheet_reference_ids(streams)
                            ),
                            "psmcluster0_envelope_match_count": sum(
                                bool(psm_envelopes(streams.get("PSMcluster0", b""), value))
                                for value in values
                            ),
                            "semantic_status": (
                                "root-named Dynamic Attributes Set Table; values are a unique internal index sequence. "
                                "Numeric intersections with Sheet/PSM namespaces are inventory evidence only, not direct record links"
                            ),
                        }
                    )
                if name == "PSMspacemap/0x00006000":
                    additional_validated[name].update(
                        {
                            "dynamic_attribute_0089_record_count": len(dynamic_attribute_records),
                            "header_field_1_equals_dynamic_attribute_record_count": (
                                int(list_map["header_u16"][1]) == len(dynamic_attribute_records)
                            ),
                            "header_field_2_minus_field_1": (
                                int(list_map["header_u16"][2]) - int(list_map["header_u16"][1])
                            ),
                            "dynamic_attribute_0089_key_counts": dynamic_attribute_key_counts,
                            "compact_iso_dynamic_attributes": {
                                "record_count": len(compact_iso_attribute_records),
                                "record_size_counts": dict(
                                    Counter(
                                        int(record["record_size"])
                                        for record in compact_iso_attribute_records
                                    )
                                ),
                                "layout": (
                                    "_ISO NUL + 0x0089 + uint32 size/reference + eight zero bytes + "
                                    "uint32 candidate graphic_ref + 0xFFFF"
                                ),
                                "psmcluster0_envelope_match_count": compact_iso_psm_envelope_count,
                                "semantic_status": (
                                    "validated compact dynamic routing record; candidate graphic_ref has a PSM envelope "
                                    "when counted above, but no direct Sheet primitive relation is asserted"
                                ),
                            },
                            "dynamic_attribute_pipeline_info": {
                                "record_count": len(dynamic_property_records),
                                "framing": (
                                    "validated PipeLine Info record: bounded 0x1080 key/value blocks, "
                                    "then 0x0089 whose uint32 size equals record_length - 6"
                                ),
                                "property_key_counts": dynamic_property_key_counts,
                                "nonempty_value_counts": dynamic_property_nonempty_value_counts,
                                "property_signatures": dynamic_property_signatures,
                                "physical_sheet_text_value_matches": dynamic_property_sheet_text_matches,
                                "element_tag_unique_text_candidates": dynamic_element_tag_text_candidates,
                                "direct_sheet_graphic_bindings": dynamic_graphic_sheet_primitive_bindings,
                                "legacy_next_marker_graphic_record_count": sum(
                                    len(records) for records in dynamic_graphics_by_uci.values()
                                ),
                                "bounded_uci_graphic_record_count": sum(
                                    len(records) for records in bounded_dynamic_graphics.values()
                                ),
                                "uci_value_validation": {
                                    "property_record_count": len(dynamic_property_uci_rows),
                                    "property_unique_uci_count": len(dynamic_property_uci_unique),
                                    "dynamic_graphics_unique_uci_count": len(dynamic_graphics_by_uci),
                                    "unique_uci_match_count": len(
                                        dynamic_property_uci_unique & set(dynamic_graphics_by_uci)
                                    ),
                                    "graphic_ref_match_count": dynamic_property_uci_graphic_matches,
                                    "unmatched_property_uci_count": len(
                                        dynamic_property_uci_unique - set(dynamic_graphics_by_uci)
                                    ),
                                    "unmatched_dynamic_graphics_uci_count": len(
                                        set(dynamic_graphics_by_uci) - dynamic_property_uci_unique
                                    ),
                                    "uci_index_layout": "0x0003 + uint16 18 + ASCII UCI Index NUL + uint32",
                                    "uci_index_present_count": sum(
                                        uci_index is not None
                                        for _, _, uci_index in dynamic_property_uci_rows
                                    ),
                                    "uci_index_nonzero_count": nonzero_uci_index_rows,
                                    "uci_index_nonzero_with_duplicate_uci_count": (
                                        nonzero_uci_index_with_duplicate_uci
                                    ),
                                },
                                "records": dynamic_property_records,
                            },
                        }
                    )
            continue
        try:
            parsed = parse_tseg_nodes(data)
        except ValueError:
            if name == "PSMspacemap/0x00000000":
                try:
                    prefixed_zero = parse_prefixed_tseg_nodes(data)
                except ValueError:
                    prefixed_zero = None
                if prefixed_zero is not None:
                    remaining_undecoded[name] = {
                        "layout": "validated-prefixed-index-node-table",
                        "tseg": prefixed_zero,
                        "semantic_status": (
                            "complete prefixed node boundaries validated; local child-reference and relation semantics "
                            "remain internal routing only, not Sheet geometry"
                        ),
                    }
                    continue
                try:
                    zero_map = parse_zero_prefix_and_tail(data)
                except ValueError as zero_error:
                    try:
                        counted_tail = parse_standard_nodes_with_12_byte_tail(data)
                    except ValueError:
                        try:
                            prefixed_counted_tail = parse_prefixed_counted_nodes_with_12_byte_tail(data)
                        except ValueError:
                            zero_summary = partial_tseg_summary(data)
                            zero_summary["zero_layout_parse_error"] = str(zero_error)
                            remaining_undecoded[name] = zero_summary
                        else:
                            remaining_undecoded[name] = {
                                **prefixed_counted_tail,
                                "semantic_status": (
                                    "node, prefix-list, and tail boundaries validated; prefix-value, local child-reference, "
                                    "and relation semantics remain inventory only"
                                ),
                            }
                    else:
                        remaining_undecoded[name] = {
                            **counted_tail,
                            "semantic_status": (
                                "node and tail boundaries validated; local child-reference and relation semantics "
                                "remain inventory only"
                            ),
                        }
                else:
                    # Only the zero-terminated high-level prefix and the
                    # separate tail root are repeatable. The intervening
                    # type-1-dominated region is only partially decoded.
                    type1_edges = parse_zero_type1_relation_edges(data, zero_map)
                    initial_relation_run = parse_zero_initial_relation_run(data, zero_map)
                    extended_relation_run = parse_zero_extended_relation_run(data, zero_map)
                    relation_sequence = parse_zero_relation_sequence(data, zero_map)
                    leading_variant: dict[str, object] | None = None
                    if not bool(relation_sequence["fully_consumed_to_tail_root"]):
                        # Some exports split the type-7/type-8 high-level
                        # nodes after an early terminator. Identify the
                        # bounded continuation itself rather than tying it to
                        # AMSS1 or a particular prefix-node count.
                        decoded_leading_variant = parse_amss1_leading_variant(data, zero_map)
                        regular_start = (
                            int(decoded_leading_variant["regular_relation_start"])
                            if decoded_leading_variant is not None
                            else None
                        )
                        alternate_sequence = None
                        if regular_start is not None:
                            alternate_map = {
                                "prefix_terminator_offset": regular_start - 8,
                                "tail_root": zero_map["tail_root"],
                            }
                            alternate_sequence = parse_zero_relation_sequence(data, alternate_map)
                        if alternate_sequence is not None and bool(alternate_sequence["fully_consumed_to_tail_root"]):
                            leading_variant = {
                                "offset": int(zero_map["prefix_terminator_offset"]) + 8,
                                "length": regular_start - (int(zero_map["prefix_terminator_offset"]) + 8),
                                "status": "decoded-high-level-prefix-continuation",
                                "decoded": decoded_leading_variant,
                            }
                            relation_sequence = alternate_sequence
                    zero_lists = [
                        record["child_refs"]
                        for record in relation_sequence["records"]
                        if record["kind"] == "zero-relation-list"
                    ]
                    zero_list_nonzero_refs = [
                        int(child_ref)
                        for child_refs in zero_lists
                        for child_ref in child_refs
                        if int(child_ref) != 0
                    ]
                    zero_list_envelopes = {
                        f"0x{ref:04X}": psm_envelopes(streams.get("PSMcluster0", b""), ref)
                        for ref in zero_list_nonzero_refs
                    }
                    sheet_local_start_by_ref = {
                        struct.unpack_from("<I", sheet_data, 14)[0]: sheet_name
                        for sheet_name, sheet_data in streams.items()
                        if re.fullmatch(r"Sheet\d+", sheet_name) and len(sheet_data) >= 18
                    }
                    zero_list_sheet_roots = [
                        {
                            "ref": f"0x{ref:04X}",
                            "sheet_stream": sheet_local_start_by_ref[ref],
                        }
                        for ref in zero_list_nonzero_refs
                        if ref in sheet_local_start_by_ref
                    ]
                    mixed_zero_relation_list_evidence = classify_mixed_zero_relation_lists(
                        streams,
                        zero_lists,
                        sheet_local_start_by_ref,
                    )
                    dynamic_refs = {
                        int(record["graphic_ref"])
                        for records in bounded_dynamic_graphics.values()
                        for record in records
                    }
                    try:
                        node_ids_8000 = {
                            int(node["id"])
                            for node in parse_tseg_nodes(streams["PSMspacemap/0x00008000"])["nodes"]
                        }
                    except (KeyError, ValueError):
                        node_ids_8000 = set()
                    try:
                        node_ids_a000 = {
                            int(node["id"])
                            for node in parse_tseg_nodes(streams["PSMspacemap/0x0000a000"])["nodes"]
                        }
                    except (KeyError, ValueError):
                        node_ids_a000 = set()
                    all_tseg_node_ids = set(node_ids_8000) | set(node_ids_a000)
                    for stream_name, stream_data in streams.items():
                        if not stream_name.startswith("PSMspacemap/") or stream_name.endswith(("00008000", "0000a000")):
                            continue
                        try:
                            all_tseg_node_ids.update(
                                int(node["id"]) for node in parse_tseg_nodes(stream_data)["nodes"]
                            )
                        except ValueError:
                            continue
                    jsite_resource_ids = set(
                        int(resource_id)
                        for resource_id in jsite_resource_list.get("resource_ids", [])
                    )
                    sheet_stream_numeric_ids = {
                        int(sheet_name[5:])
                        for sheet_name in streams
                        if re.fullmatch(r"Sheet\d+", sheet_name)
                    }
                    relation_target_categories: dict[str, dict[str, int]] = {}
                    relation_spacemap_segment_targets: dict[str, Counter[str]] = {}
                    relation_181_shared_jsite_559_count = 0
                    relation_182_parent_238_to_sheet6_root_count = 0
                    relation_190_pipeline_to_compact_iso_count = 0
                    relation_183_targets_by_parent: dict[int, set[int]] = {}
                    relation_184_targets_by_parent: dict[int, Counter[str]] = {}
                    relation_dynamic_attribute_key_counts: dict[str, Counter[str]] = {}
                    relation_dynamic_uci_target_counts: Counter[str] = Counter()
                    for record in relation_sequence["records"]:
                        if record["kind"] != "relation-container":
                            continue
                        for child in record["children"]:
                            relation = str(child["relation"])
                            target = int(child["child_ref"])
                            segment_base = target & 0xFFFFE000
                            segment_name = f"PSMspacemap/0x{segment_base:08x}"
                            if relation == "181" and int(record["parent_ref"]) == 238 and target == 559:
                                relation_181_shared_jsite_559_count += 1
                            if (
                                relation == "182"
                                and int(record["parent_ref"]) == 238
                                and sheet_local_start_by_ref.get(target) == "Sheet6"
                            ):
                                relation_182_parent_238_to_sheet6_root_count += 1
                            if relation == "183":
                                relation_183_targets_by_parent.setdefault(int(record["parent_ref"]), set()).add(target)
                            if relation == "184":
                                if target in named_layer_name_by_ref:
                                    target_context = f"named-layer:{named_layer_name_by_ref[target]}"
                                elif target in sheet_local_start_by_ref:
                                    target_context = f"physical-sheet-root:{sheet_local_start_by_ref[target]}"
                                elif target in jsite_resource_ids:
                                    target_context = f"jsite:{target}"
                                elif segment_name in streams:
                                    target_context = f"spacemap-segment:{segment_name}"
                                else:
                                    target_context = f"unresolved:0x{target:04X}"
                                relation_184_targets_by_parent.setdefault(
                                    int(record["parent_ref"]), Counter()
                                )[target_context] += 1
                            if (
                                relation == "190"
                                and int(record["parent_ref"]) in dynamic_property_attribute_refs
                                and target in compact_iso_attribute_refs
                            ):
                                relation_190_pipeline_to_compact_iso_count += 1
                            categories = relation_target_categories.setdefault(
                                relation,
                                {
                                    "total": 0,
                                    "sheet_header_local_start": 0,
                                    "dynamic_graphic_ref": 0,
                                    "named_layer_object": 0,
                                    "psmcluster0_envelope": 0,
                                    "spacemap_8000_node": 0,
                                    "spacemap_a000_node": 0,
                                    "spacemap_segment_address": 0,
                                },
                            )
                            categories["total"] += 1
                            categories["sheet_header_local_start"] += target in sheet_local_start_by_ref
                            categories["dynamic_graphic_ref"] += target in dynamic_refs
                            categories["named_layer_object"] += target in named_layer_name_by_ref
                            categories["psmcluster0_envelope"] += bool(
                                psm_envelopes(streams.get("PSMcluster0", b""), target)
                            )
                            categories["spacemap_8000_node"] += target in node_ids_8000
                            categories["spacemap_a000_node"] += target in node_ids_a000
                            categories["spacemap_segment_address"] += segment_name in streams
                            if segment_name in streams:
                                relation_spacemap_segment_targets.setdefault(relation, Counter())[segment_name] += 1
                            if target in dynamic_attribute_details:
                                keys = relation_dynamic_attribute_key_counts.setdefault(
                                    relation, Counter()
                                )
                                for detail in dynamic_attribute_details[target]:
                                    key = str(detail["attribute_key"])
                                    keys[f"{key}|size={int(detail['record_size'])}"] += 1
                            if target in dynamic_uci_by_graphic_ref:
                                relation_dynamic_uci_target_counts[relation] += 1
                    mixed_relation_parent_target_namespace_summary = (
                        summarize_mixed_relation_parent_target_namespaces(
                            relation_sequence,
                            sheet_local_start_by_ref,
                            {
                                int(sheet_name[5:]): sheet_name
                                for sheet_name in streams
                                if re.fullmatch(r"Sheet\d+", sheet_name)
                            },
                            named_layer_name_by_ref,
                            {
                                int(record["object_ref"])
                                for record in page_default_records_for_mapping
                            },
                            {
                                int(record["object_ref"])
                                for record in page_layer_container_records_for_mapping
                            },
                            {
                                int(record["object_ref"])
                                for record in page_linked_control_records_for_mapping
                            },
                            jsite_resource_ids,
                            dynamic_attribute_refs,
                            all_tseg_node_ids,
                            {
                                int(record["graphic_ref"])
                                for stream_name, sheet_data in streams.items()
                                if re.fullmatch(r"Sheet\d+", stream_name)
                                for parser in (
                                    parse_18_32_layer_bindings,
                                    parse_59_2b_page_layer_bindings,
                                    parse_61_pipe_arc_records,
                                )
                                for record in parser(sheet_data)
                            }
                            | {
                                int(record["secondary_ref"])
                                for stream_name, sheet_data in streams.items()
                                if re.fullmatch(r"Sheet\d+", stream_name)
                                for record in parse_4d_text_layer_bindings(sheet_data)
                            }
                            | {
                                int(record["composite_ref"])
                                for stream_name, sheet_data in streams.items()
                                if re.fullmatch(r"Sheet\d+", stream_name)
                                for record in parse_7b_composite_headers(sheet_data)
                            },
                            {
                                int(record["child_ref"])
                                for stream_name, sheet_data in streams.items()
                                if re.fullmatch(r"Sheet\d+", stream_name)
                                for record in parse_18_32_layer_bindings(sheet_data)
                            }
                            | {
                                int(record["child_ref"])
                                for stream_name, sheet_data in streams.items()
                                if re.fullmatch(r"Sheet\d+", stream_name)
                                for record in parse_4d_text_layer_bindings(sheet_data)
                            }
                            | {
                                int(record["primitive_ref"])
                                for stream_name, sheet_data in streams.items()
                                if re.fullmatch(r"Sheet\d+", stream_name)
                                for parser in (parse_59_2b_page_layer_bindings, parse_61_pipe_arc_records)
                                for record in parser(sheet_data)
                            }
                            | {
                                int(child_ref)
                                for stream_name, sheet_data in streams.items()
                                if re.fullmatch(r"Sheet\d+", stream_name)
                                for record in parse_7b_composite_headers(sheet_data)
                                for child_ref in record["child_refs"]
                            }
                            | {
                                int(record["primitive_ref"])
                                for stream_name, sheet_data in streams.items()
                                if re.fullmatch(r"Sheet\d+", stream_name)
                                for record in parse_sheet_3d_placement_wrappers(sheet_data)
                            },
                            {
                                int(stream_name.rsplit("0x", 1)[1], 16)
                                for stream_name in streams
                                if stream_name.startswith("PSMspacemap/0x")
                            },
                            {
                                int(envelope["graphic_ref"])
                                for run in psmcluster_envelope_runs["runs"]
                                for envelope in run["records"]
                            },
                        )
                    )
                    prefix_child_refs = {
                        int(child["ref"])
                        for node in zero_map["prefix_nodes"]
                        for child in node["children"]
                    }
                    named_layer_groups = [
                        {int(named_record["object_ref"]) for named_record in named_layer_records_for_mapping[:175]}
                    ] + [
                        {
                            int(named_record["object_ref"])
                            for named_record in named_layer_records_for_mapping[175 + group_index * 92 : 175 + (group_index + 1) * 92]
                        }
                        for group_index in range(max(0, (len(named_layer_records_for_mapping) - 175) // 92))
                    ]
                    relation_183_group_overlap = {}
                    for parent_ref, targets in relation_183_targets_by_parent.items():
                        if not named_layer_groups:
                            continue
                        group_index, group = max(
                            enumerate(named_layer_groups), key=lambda item: len(targets & item[1])
                        )
                        relation_183_group_overlap[f"0x{parent_ref:04X}"] = {
                            "best_group": "base_175" if group_index == 0 else f"subsequent_{group_index}",
                            "target_count": len(targets),
                            "named_group_overlap": len(targets & group),
                            "target_refs_outside_best_group": len(targets - group),
                            "best_group_refs_not_targeted": len(group - targets),
                            "target_layer_names": sorted(
                                named_layer_name_by_ref[target]
                                for target in targets
                                if target in named_layer_name_by_ref
                            ),
                            "semantic_status": (
                                "bounded container-to-named-layer membership evidence; "
                                "the parent is a PSM hierarchy container, not a visible Sheet primitive or component"
                            ),
                        }
                    remaining_undecoded[name] = {
                        "layout": (
                            "structurally-complete-mixed-relation-sequence"
                            if bool(relation_sequence["fully_consumed_to_tail_root"])
                            else "partially-decoded-mixed-relation-sequence"
                        ),
                        "header_u16": zero_map["header_u16"],
                        "prefix_nodes": zero_map["prefix_nodes"],
                        "prefix_terminator_offset": zero_map["prefix_terminator_offset"],
                        "middle_bytes_between_prefix_and_tail": zero_map["unparsed_middle_bytes"],
                        "tail_root": zero_map["tail_root"],
                        "initial_relation_run": initial_relation_run,
                        "extended_relation_run": extended_relation_run,
                        "relation_sequence": relation_sequence,
                        "leading_variant": leading_variant,
                        "zero_relation_list_psm_envelopes": zero_list_envelopes,
                        "zero_relation_list_sheet_roots": zero_list_sheet_roots,
                        "zero_relation_list_layout_evidence": mixed_zero_relation_list_evidence,
                        "relation_target_categories": relation_target_categories,
                        "relation_spacemap_segment_target_counts": {
                            relation: dict(sorted(counts.items()))
                            for relation, counts in sorted(relation_spacemap_segment_targets.items())
                        },
                        "relation_dynamic_attribute_key_counts": {
                            relation: dict(sorted(counts.items()))
                            for relation, counts in sorted(
                                relation_dynamic_attribute_key_counts.items()
                            )
                        },
                        "relation_dynamic_uci_target_counts": dict(
                            sorted(relation_dynamic_uci_target_counts.items())
                        ),
                        "relation_181_parent_238_to_shared_jsite_559_count": (
                            relation_181_shared_jsite_559_count
                        ),
                        "relation_182_parent_238_to_sheet6_root_count": (
                            relation_182_parent_238_to_sheet6_root_count
                        ),
                        "relation_190_pipeline_attribute_to_compact_iso_attribute_count": (
                            relation_190_pipeline_to_compact_iso_count
                        ),
                        "relation_183_named_layer_group_overlap": relation_183_group_overlap,
                        "relation_184_parent_contexts": {
                            f"0x{parent_ref:04X}": dict(sorted(counts.items()))
                            for parent_ref, counts in sorted(
                                relation_184_targets_by_parent.items()
                            )
                        },
                        "relation_parent_target_namespace_summary": mixed_relation_parent_target_namespace_summary,
                        "type1_like_byte_pattern_diagnostic": {
                            "candidate_count": len(type1_edges),
                            "status": (
                                "sliding byte-pattern matches inside fully bounded type-2/type-3 payloads; "
                                "not records, edges, or namespace references"
                            ),
                        },
                        "semantic_status": (
                            "all observed record boundaries are decoded when the relation sequence reaches the "
                            "tail root; type-1-like patterns are unaligned byte diagnostics inside that sequence, "
                            "not independently framed hierarchy records. Relation-code and visible-object semantics "
                            "remain inventory only"
                        ),
                    }
            else:
                remaining_undecoded[name] = partial_tseg_summary(data)
        else:
            # A full source consumption is the structural proof.  Relation
            # semantics remain separate from record framing and are retained
            # as inventory data until linked to Sheet primitives.
            additional_validated[name] = {
                "layout": "validated-full-node-table",
                "node_count": parsed["node_count"],
                "node_type_counts": dict(Counter(int(node["type"]) for node in parsed["nodes"])),
                "relation_code_counts": dict(
                    Counter(
                        int(child["relation"])
                        for node in parsed["nodes"]
                        for child in node["children"]
                    )
                ),
                "relation_target_namespace_evidence": classify_tseg_relation_targets(
                    streams,
                    parsed["nodes"],
                    named_layer_name_by_ref,
                    {start: sheet_name for start, sheet_name in sheet_local_starts},
                    dynamic_refs,
                    dynamic_attribute_refs,
                    {int(resource_id) for resource_id in jsite_resource_list.get("resource_ids", [])},
                ),
            }

    # The counted 0x0000 variant is intentionally not fed through the normal
    # full-table parser above. Its 12-byte tail has a separate boundary and
    # receives namespace evidence only after the mixed-layout parser failed.
    counted_zero = remaining_undecoded.get("PSMspacemap/0x00000000")
    if (
        isinstance(counted_zero, dict)
        and counted_zero.get("layout") == "standard-counted-nodes-with-12-byte-tail"
    ):
        tail_evidence = classify_counted_zero_tail_target(
            streams,
            dict(counted_zero.get("tail", {})),
            dynamic_attribute_refs,
        )
        if tail_evidence is not None:
            counted_zero["tail_target_namespace_evidence"] = tail_evidence

    # The complete mixed layout has a differently framed tail root. Its
    # children receive the same namespace-only treatment; this is evidence
    # about reference addressing, not a request to render a primitive.
    mixed_zero = remaining_undecoded.get("PSMspacemap/0x00000000")
    if isinstance(mixed_zero, dict) and isinstance(mixed_zero.get("tail_root"), dict):
        tail_children = mixed_zero["tail_root"].get("children", [])
        if isinstance(tail_children, list):
            mixed_zero["tail_target_namespace_evidence"] = [
                classify_zero_tail_child(streams, child, dynamic_attribute_refs)
                for child in tail_children
                if isinstance(child, dict) and "ref" in child and "relation" in child
            ]
    prefixed_zero = remaining_undecoded.get("PSMspacemap/0x00000000")
    if (
        isinstance(prefixed_zero, dict)
        and prefixed_zero.get("layout") == "prefixed-counted-nodes-with-12-byte-tail"
    ):
        prefix_values = [int(value) for value in prefixed_zero.get("prefix_uint16_values", [])]
        local_limit = int(prefixed_zero["header_u16"][2])
        visible_refs = decoded_sheet_reference_ids(streams)
        prefixed_zero["prefix_namespace_evidence"] = {
            "unique_value_count": len(set(prefix_values)),
            "duplicate_value_count": len(prefix_values) - len(set(prefix_values)),
            "all_values_at_or_below_header_u16_2": all(value <= local_limit for value in prefix_values),
            "decoded_visible_sheet_reference_match_count": len(set(prefix_values) & visible_refs),
            "classification": "reserved-or-unbound-local-id-inventory",
            "semantic_limit": (
                "the table is structurally bounded and disjoint from currently decoded visible Sheet references; "
                "it is not proven to be an allocator free-list or a renderable object list"
            ),
        }
    validated_prefixed_zero = remaining_undecoded.get("PSMspacemap/0x00000000")
    if (
        isinstance(validated_prefixed_zero, dict)
        and validated_prefixed_zero.get("layout") == "validated-prefixed-index-node-table"
    ):
        prefixed_tseg = validated_prefixed_zero.get("tseg", {})
        if isinstance(prefixed_tseg, dict):
            nodes = list(prefixed_tseg.get("nodes", []))
            prefix_values = [int(value) for value in prefixed_tseg.get("prefix_uint16_values", [])]
            visible_refs = decoded_sheet_reference_ids(streams)
            validated_prefixed_zero["prefix_namespace_evidence"] = {
                "unique_value_count": len(set(prefix_values)),
                "duplicate_value_count": len(prefix_values) - len(set(prefix_values)),
                "decoded_visible_sheet_reference_match_count": len(set(prefix_values) & visible_refs),
                "classification": "local-id-routing-index",
                "semantic_limit": (
                    "the bounded prefix is an internal PSM local-id index; it is not a render order, "
                    "component list, or a list of Sheet primitives"
                ),
            }
            relation_evidence = classify_tseg_relation_targets(
                streams,
                nodes,
                named_layer_name_by_ref,
                {start: sheet_name for start, sheet_name in sheet_local_starts},
                dynamic_refs,
                dynamic_attribute_refs,
                {int(resource_id) for resource_id in jsite_resource_list.get("resource_ids", [])},
            )
            validated_prefixed_zero["relation_target_namespace_evidence"] = relation_evidence
            validated_prefixed_zero["relation_semantic_evidence"] = (
                summarize_prefixed_zero_relation_semantics(nodes, relation_evidence)
            )
            geometry_201_evidence = classify_prefixed_zero_relation_201_geometry_companions(streams, nodes)
            validated_prefixed_zero["relation_201_geometry_companion_evidence"] = geometry_201_evidence
            resolved_201_count = (
                int(geometry_201_evidence["range_companion_all_member_lines_validated_count"])
                + int(geometry_201_evidence["circle_companion_ellipse_validated_count"])
            )
            if resolved_201_count == int(geometry_201_evidence["relation_201_target_count"]):
                semantic_201 = validated_prefixed_zero["relation_semantic_evidence"].get("201")
                if isinstance(semantic_201, dict):
                    semantic_201["classification"] = "geometry-companion-route"
                    semantic_201["resolved_geometry_companion_count"] = resolved_201_count
    counted_or_prefixed_zero = remaining_undecoded.get("PSMspacemap/0x00000000")
    if (
        isinstance(counted_or_prefixed_zero, dict)
        and counted_or_prefixed_zero.get("layout")
        in {
            "standard-counted-nodes-with-12-byte-tail",
            "prefixed-counted-nodes-with-12-byte-tail",
        }
    ):
        counted_or_prefixed_zero["zero_relation_extension_target_evidence"] = (
            classify_counted_zero_relation_extensions(
                list(counted_or_prefixed_zero.get("nodes", [])), named_layer_name_by_ref
            )
        )
        counted_or_prefixed_zero["zero_relation_anchor_list_target_evidence"] = (
            classify_counted_zero_relation_anchor_lists(
                streams,
                list(counted_or_prefixed_zero.get("nodes", [])),
                {start: sheet_name for start, sheet_name in sheet_local_starts},
            )
        )
        counted_or_prefixed_zero["counted_relation_target_evidence"] = (
            classify_tseg_relation_targets(
                streams,
                list(counted_or_prefixed_zero.get("nodes", [])),
                named_layer_name_by_ref,
                {start: sheet_name for start, sheet_name in sheet_local_starts},
                dynamic_refs,
                dynamic_attribute_refs,
                {int(resource_id) for resource_id in jsite_resource_list.get("resource_ids", [])},
            )
        )
        counted_or_prefixed_zero["relation_201_dynamic_graphic_sheet_binding"] = (
            classify_counted_relation_201_dynamic_graphic_bindings(
                streams,
                list(counted_or_prefixed_zero.get("nodes", [])),
                dynamic_refs,
            )
        )
    psmcluster_top_vfset_records = parse_psmcluster_top_vfset_records(
        streams.get("PSMcluster0", b"")
    )
    root_registry = parse_psm_roots(streams["PSMroots"]) if "PSMroots" in streams else None
    if root_registry is not None:
        for entry in root_registry["entries"]:
            root_ref = int(entry["root_ref"])
            stream_name = f"PSMspacemap/0x{root_ref:08x}"
            entry["spacemap_stream"] = stream_name if stream_name in space_streams else None
            # A root reference may be a node id rather than a stream address.
            # Search only source-complete node tables and retain the literal
            # hit location; no match is deliberately left unresolved.
            node_locations: list[dict[str, object]] = []
            for candidate_name, candidate_data in space_streams.items():
                try:
                    candidate_nodes = parse_tseg_nodes(candidate_data)["nodes"]
                except ValueError:
                    continue
                for node in candidate_nodes:
                    if int(node["id"]) == root_ref:
                        node_locations.append(
                            {
                                "spacemap_stream": candidate_name,
                                "node_type": node["type"],
                                "child_count": node["child_count"],
                                "children": node["children"],
                            }
                        )
            entry["validated_node_locations"] = node_locations
            if str(entry["name"]) == "TopVFSet":
                entry["psmcluster_top_vfset_record_matches"] = [
                    record
                    for record in psmcluster_top_vfset_records["records"]
                    if int(record["object_ref"]) == root_ref
                ]

        dynamic_refs = {
            int(record["graphic_ref"])
            for records in bounded_dynamic_graphics.values()
            for record in records
        }
        validated_node_ids: set[int] = set()
        for candidate_data in space_streams.values():
            try:
                validated_node_ids.update(int(node["id"]) for node in parse_tseg_nodes(candidate_data)["nodes"])
            except ValueError:
                continue
        for entry in root_registry["entries"]:
            for location in entry["validated_node_locations"]:
                target_evidence: list[dict[str, object]] = []
                for child in location["children"]:
                    target = int(child["ref"])
                    map_base = target & 0xFFFFE000
                    map_name = f"PSMspacemap/0x{map_base:08x}"
                    map_offset = target - map_base
                    target_evidence.append(
                        {
                            "ref": target,
                            "relation": child["relation"],
                            "has_psmcluster0_envelope": bool(
                                psm_envelopes(streams.get("PSMcluster0", b""), target)
                            ),
                            "is_uci_dynamic_graphic_ref": target in dynamic_refs,
                            "is_dynamic_attribute_ref_0089": target in dynamic_attribute_refs,
                            "dynamic_attribute_ref_0089_details": dynamic_attribute_details.get(target, []),
                            "is_validated_spacemap_node_id": target in validated_node_ids,
                            # A small offset from an actual map base is a
                            # separate observed target form (e.g. 0xC001 for
                            # a 0xC000 map). Do not infer its selector meaning.
                            "near_spacemap_base": (
                                map_name if map_name in space_streams and map_offset <= 2 else None
                            ),
                            "near_spacemap_base_offset": map_offset if map_name in space_streams and map_offset <= 2 else None,
                        }
                    )
                location["child_target_evidence"] = target_evidence

    cluster_registry = (
        parse_psm_cluster_table(streams["PSMclustertable"])
        if "PSMclustertable" in streams
        else None
    )
    if cluster_registry is not None:
        for entry in cluster_registry["entries"]:
            entry["stream_exists"] = str(entry["name"]) in streams
    link_psm_root_directory_entries(root_registry, cluster_registry)

    segment_table = (
        parse_psm_segment_table(streams["PSMsegmenttable"])
        if "PSMsegmenttable" in streams
        else None
    )
    if segment_table is not None:
        segment_entries: list[dict[str, object]] = []
        for segment_index, payload_value in enumerate(segment_table["payload_bytes"]):
            map_name = f"PSMspacemap/0x{segment_index * 0x2000:08x}"
            stream_exists = map_name in space_streams
            segment_entries.append(
                {
                    "segment_index": segment_index,
                    "spacemap_stream": map_name if stream_exists else None,
                    "payload_value": payload_value,
                    "nonzero_payload_matches_stream_presence": bool(payload_value) == stream_exists,
                }
            )
        segment_table["spacemap_segment_entries"] = segment_entries
        segment_table["nonzero_payload_exactly_matches_stream_presence"] = all(
            bool(entry["nonzero_payload_matches_stream_presence"])
            for entry in segment_entries
        )
        segment_table["semantic_status"] = (
            "payload slots are a validated spacemap-presence/index table; nonzero byte subtypes remain unnamed"
        )

    psmcluster_named_records = (
        parse_psmcluster_named_records(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_42_page_layer_containers = (
        parse_psmcluster_42_page_layer_containers(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_88_page_default_records = (
        parse_psmcluster_88_page_default_records(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_88_default_parent_validation = (
        validate_psmcluster_88_default_parent_refs(
            psmcluster_88_page_default_records["records"],
            psmcluster_named_records["records"],
        )
        if psmcluster_88_page_default_records is not None
        and psmcluster_named_records is not None
        else None
    )
    psmcluster_88_page_container_validation = (
        validate_psmcluster_88_page_container_links(
            psmcluster_88_page_default_records["records"],
            psmcluster_named_records["records"],
            psmcluster_42_page_layer_containers["records"],
        )
        if psmcluster_88_page_default_records is not None
        and psmcluster_named_records is not None
        and psmcluster_42_page_layer_containers is not None
        else None
    )
    psmcluster_42_member_anchor_validation = (
        validate_psmcluster_42_member_anchor_refs(
            psmcluster_42_page_layer_containers["records"],
            psmcluster_named_records["records"],
        )
        if psmcluster_42_page_layer_containers is not None
        and psmcluster_named_records is not None
        else None
    )
    psmcluster_42_default_object_validation = (
        validate_psmcluster_42_default_object_refs(
            psmcluster_42_page_layer_containers["records"],
            psmcluster_88_page_default_records["records"],
        )
        if psmcluster_42_page_layer_containers is not None
        and psmcluster_88_page_default_records is not None
        else None
    )
    psmcluster_57_page_linked_control_records = (
        parse_psmcluster_57_page_linked_control_records(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_75_root_catalog = (
        parse_psmcluster_75_root_catalog(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_02_preference_index = (
        parse_psmcluster_02_preference_index(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_6c_default_style_bundles = (
        parse_psmcluster_6c_default_style_bundles(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_89_application_property_records = (
        parse_psmcluster_89_application_property_records(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_89_parent_object_validation = (
        validate_psmcluster_89_parent_object_links(
            streams, psmcluster_89_application_property_records["records"]
        )
        if psmcluster_89_application_property_records is not None
        else None
    )
    psmcluster_89_pasted_graphic_jsite_validation = (
        validate_psmcluster_89_pasted_graphic_jsite_links(
            streams, psmcluster_89_application_property_records["records"]
        )
        if psmcluster_89_application_property_records is not None
        else None
    )
    psmcluster_73_background_records = (
        parse_psmcluster_73_background_records(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_64_zero_control_slots = (
        parse_psmcluster_64_zero_control_slots(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_65_section_name_sites = (
        parse_psmcluster_65_section_name_sites(streams["PSMcluster0"])
        if "PSMcluster0" in streams
        else None
    )
    psmcluster_65_section_sheet_directory_validation = (
        validate_psmcluster_65_section_sheet_directory(
            psmcluster_65_section_name_sites["records"],
            psmcluster_42_page_layer_containers["records"],
        )
        if psmcluster_65_section_name_sites is not None
        and psmcluster_42_page_layer_containers is not None
        else None
    )
    psmcluster_57_layer_state_profile_validation = (
        validate_psmcluster_57_layer_state_profiles(
            psmcluster_named_records["records"],
            psmcluster_42_page_layer_containers["records"],
            psmcluster_57_page_linked_control_records["records"],
        )
        if (
            psmcluster_named_records is not None
            and psmcluster_42_page_layer_containers is not None
            and psmcluster_57_page_linked_control_records is not None
        )
        else None
    )
    if psmcluster_named_records is not None:
        dynamic_uci_by_graphic_ref = {
            int(record["graphic_ref"]): uci
            for uci, records in bounded_dynamic_graphics.items()
            for record in records
        }
        node_summaries_by_id: dict[int, list[dict[str, object]]] = {}
        for candidate_name, candidate_data in space_streams.items():
            try:
                candidate_nodes = parse_tseg_nodes(candidate_data)["nodes"]
            except ValueError:
                continue
            for node in candidate_nodes:
                node_summaries_by_id.setdefault(int(node["id"]), []).append(
                    {
                        "spacemap_stream": candidate_name,
                        "node_type": node["type"],
                        "child_count": node["child_count"],
                        "children": node["children"],
                    }
                )
        for record in psmcluster_named_records["records"]:
            record["validated_spacemap_node_locations"] = node_summaries_by_id.get(
                int(record["object_ref"]), []
            )
        relation_target_categories: Counter[str] = Counter()
        for record in psmcluster_named_records["records"]:
            for location in record["validated_spacemap_node_locations"]:
                for child in location["children"]:
                    target = int(child["ref"])
                    if target in dynamic_attribute_refs:
                        category = "dynamic_attribute_0089"
                    elif psm_envelopes(streams.get("PSMcluster0", b""), target):
                        category = "psmcluster0_envelope"
                    else:
                        category = "unresolved_target"
                    relation_target_categories[f"relation={child['relation']},{category}"] += 1
        psmcluster_named_records["validated_node_child_target_categories"] = dict(
            sorted(relation_target_categories.items())
        )
        subsequent_sheet_count: int | None = None
        if cluster_registry is not None:
            directory_names = [str(entry["name"]) for entry in cluster_registry["entries"]]
            if "Sheet221" in directory_names:
                sheet221_index = directory_names.index("Sheet221")
                subsequent_sheet_count = sum(
                    bool(re.fullmatch(r"Sheet\d+", name))
                    for name in directory_names[sheet221_index + 1 :]
                )
        psmcluster_named_records["subsequent_sheet_count_after_sheet221"] = subsequent_sheet_count
        psmcluster_named_records["observed_page_group_formula"] = {
            "fixed_base_records": 175,
            "records_per_subsequent_sheet": 92,
            "expected_record_count": (
                175 + 92 * subsequent_sheet_count if subsequent_sheet_count is not None else None
            ),
            "matches": (
                int(psmcluster_named_records["record_count"]) == 175 + 92 * subsequent_sheet_count
                if subsequent_sheet_count is not None
                else None
            ),
        }
        base_records = psmcluster_named_records["records"][:175]
        sheet6_data = streams.get("Sheet6")
        if sheet6_data is not None:
            base_layers_by_ref = {
                int(record["object_ref"]): record for record in base_records
            }
            base_families = (
                parse_18_32_layer_bindings(sheet6_data),
                parse_4d_text_layer_bindings(sheet6_data),
                parse_13_ac_layer_relations(sheet6_data),
                parse_59_2b_page_layer_bindings(sheet6_data),
                parse_61_pipe_arc_records(sheet6_data),
                parse_13_63_circle_geometry(sheet6_data),
            )
            base_member_counts = Counter(
                int(binding["page_layer_ref"])
                for bindings in base_families
                for binding in bindings
                if int(binding["page_layer_ref"]) in base_layers_by_ref
            )
            base_total_records = sum(len(bindings) for bindings in base_families)
            base_validation = [
                {
                    "name": str(record["name"]),
                    "object_ref": f"0x{int(record['object_ref']):04X}",
                    "declared_member_count_field_3": int(record["field_3"]),
                    "decoded_member_record_count": base_member_counts[int(record["object_ref"])],
                    "matches": int(record["field_3"])
                    == base_member_counts[int(record["object_ref"])],
                }
                for record in base_records
            ]
            template_member_counts: Counter[int] = Counter()
            template_data = streams.get("Sheet221", b"")
            template_special_records = parse_sheet221_template_special_records(template_data)
            link_sheet221_bitmap_resource_descriptors(template_special_records, jsite_resources)
            template_text_records = parse_sheet221_template_text_records(template_data)
            # These are the bounded template record families that retain the
            # same +14 base-layer reference. Long 0x4d variants are counted
            # here even when their text payload format is not yet rendered.
            for offset in range(max(0, len(template_data) - 17)):
                tag = struct.unpack_from("<H", template_data, offset)[0]
                record_length = struct.unpack_from("<I", template_data, offset + 2)[0]
                if (
                    tag not in {0x0018, 0x004D, 0x003D, 0x0084}
                    or not 16 <= record_length <= 2000
                    or offset + 6 + record_length > len(template_data)
                ):
                    continue
                layer_ref = struct.unpack_from("<I", template_data, offset + 14)[0]
                if layer_ref in base_layers_by_ref:
                    template_member_counts[layer_ref] += 1
            combined_base_validation = [
                {
                    "name": str(record["name"]),
                    "object_ref": f"0x{int(record['object_ref']):04X}",
                    "declared_member_count_field_3": int(record["field_3"]),
                    "sheet6_member_record_count": base_member_counts[int(record["object_ref"])],
                    "sheet221_template_record_count": template_member_counts[int(record["object_ref"])],
                    "combined_member_record_count": (
                        base_member_counts[int(record["object_ref"])]
                        + template_member_counts[int(record["object_ref"])]
                    ),
                    "matches": int(record["field_3"])
                    == (
                        base_member_counts[int(record["object_ref"])]
                        + template_member_counts[int(record["object_ref"])]
                    ),
                }
                for record in base_records
            ]
            psmcluster_named_records["sheet6_base_named_layer_group"] = {
                "record_count": len(base_records),
                "decoded_sheet_record_count": base_total_records,
                "decoded_sheet_record_refs_all_resolve_to_base_group": (
                    sum(base_member_counts.values()) == base_total_records
                ),
                "field_3_matching_record_count": sum(
                    bool(record["matches"]) for record in base_validation
                ),
                "field_3_nonmatching_base_records": [
                    record for record in base_validation if not bool(record["matches"])
                ],
                "records": base_validation,
            }
            psmcluster_named_records["shared_base_named_layer_group"] = {
                "sheet6_decoded_member_record_count": base_total_records,
                "sheet221_recognized_template_member_record_count": sum(
                    template_member_counts.values()
                ),
                "sheet221_recognized_tags": ["0x0018", "0x004D", "0x003D", "0x0084"],
                "sheet221_special_record_inventory": template_special_records,
                "sheet221_template_text_inventory": template_text_records,
                "field_3_matching_combined_record_count": sum(
                    bool(record["matches"]) for record in combined_base_validation
                ),
                "field_3_nonmatching_combined_base_records": [
                    record for record in combined_base_validation if not bool(record["matches"])
                ],
                "records": combined_base_validation,
            }
        if cluster_registry is not None and subsequent_sheet_count is not None:
            directory_names = [str(entry["name"]) for entry in cluster_registry["entries"]]
            sheet221_index = directory_names.index("Sheet221")
            physical_sheets = [
                name
                for name in directory_names[sheet221_index + 1 :]
                if re.fullmatch(r"Sheet\d+", name)
            ]
            records = psmcluster_named_records["records"]
            first_group_names = [
                str(record["name"])
                for record in records[175 : 175 + 92]
            ]
            page_groups: list[dict[str, object]] = []
            for page_index, sheet_name in enumerate(physical_sheets):
                group = records[175 + page_index * 92 : 175 + (page_index + 1) * 92]
                sheet_data = streams.get(sheet_name, b"")
                local_start = struct.unpack_from("<I", sheet_data, 14)[0] if len(sheet_data) >= 18 else None
                object_refs = [int(record["object_ref"]) for record in group]
                layer_names_by_ref = {
                    int(record["object_ref"]): str(record["name"])
                    for record in group
                }
                line_bindings = parse_18_32_layer_bindings(sheet_data)
                binding_layer_refs = [int(binding["page_layer_ref"]) for binding in line_bindings]
                unmatched_layer_refs = sorted(
                    set(binding_layer_refs).difference(layer_names_by_ref)
                )
                layer_line_counts = Counter(
                    layer_names_by_ref[layer_ref]
                    for layer_ref in binding_layer_refs
                    if layer_ref in layer_names_by_ref
                )
                text_bindings = parse_4d_text_layer_bindings(sheet_data)
                text_layer_refs = [int(binding["page_layer_ref"]) for binding in text_bindings]
                unmatched_text_layer_refs = sorted(
                    set(text_layer_refs).difference(layer_names_by_ref)
                )
                layer_text_counts = Counter(
                    layer_names_by_ref[layer_ref]
                    for layer_ref in text_layer_refs
                    if layer_ref in layer_names_by_ref
                )
                text_style_counts = Counter(
                    int(binding["style_ref"]) for binding in text_bindings
                )
                composite_headers = parse_7b_composite_headers(sheet_data)
                composite_refs = {
                    int(record["composite_ref"]) for record in composite_headers
                }
                sheet_numeric_id = int(sheet_name.removeprefix("Sheet"))
                text_secondary_ref_categories = Counter(
                    (
                        "current_sheet_ref"
                        if int(binding["secondary_ref"]) == sheet_numeric_id
                        else (
                            "composite_ref"
                            if int(binding["secondary_ref"]) in composite_refs
                            else "unresolved"
                        )
                    )
                    for binding in text_bindings
                )
                line_layer_by_graphic_ref = {
                    int(binding["graphic_ref"]): int(binding["page_layer_ref"])
                    for binding in line_bindings
                }
                relation_bindings = parse_13_ac_layer_relations(sheet_data)
                relation_reverse_alias_validation = validate_13_ac_reverse_line_aliases(sheet_data)
                relation_layer_refs = [int(binding["page_layer_ref"]) for binding in relation_bindings]
                relation_matches_line_layer = sum(
                    line_layer_by_graphic_ref.get(int(binding["graphic_ref"]))
                    == int(binding["page_layer_ref"])
                    for binding in relation_bindings
                )
                ellipse_bindings = parse_59_2b_page_layer_bindings(sheet_data)
                ellipse_layer_refs = [
                    int(binding["page_layer_ref"]) for binding in ellipse_bindings
                ]
                ellipse_layer_counts = Counter(
                    layer_names_by_ref[layer_ref]
                    for layer_ref in ellipse_layer_refs
                    if layer_ref in layer_names_by_ref
                )
                ellipse_direct_uci_counts = Counter(
                    layer_names_by_ref[int(binding["page_layer_ref"])]
                    for binding in ellipse_bindings
                    if int(binding["page_layer_ref"]) in layer_names_by_ref
                    and int(binding["graphic_ref"]) in dynamic_uci_by_graphic_ref
                )
                pipe_arc_bindings = parse_61_pipe_arc_records(sheet_data)
                pipe_arc_layer_refs = [
                    int(binding["page_layer_ref"]) for binding in pipe_arc_bindings
                ]
                pipe_arc_layer_counts = Counter(
                    layer_names_by_ref[layer_ref]
                    for layer_ref in pipe_arc_layer_refs
                    if layer_ref in layer_names_by_ref
                )
                pipe_arc_direct_uci_counts = Counter(
                    layer_names_by_ref[int(binding["page_layer_ref"])]
                    for binding in pipe_arc_bindings
                    if int(binding["page_layer_ref"]) in layer_names_by_ref
                    and int(binding["graphic_ref"]) in dynamic_uci_by_graphic_ref
                )
                circle_geometry_bindings = parse_13_63_circle_geometry(sheet_data)
                ellipse_by_graphic_ref = {
                    int(binding["graphic_ref"]): binding
                    for binding in ellipse_bindings
                }
                circle_geometry_pairs = [
                    binding
                    for binding in circle_geometry_bindings
                    if int(binding["graphic_ref"]) in ellipse_by_graphic_ref
                    and int(binding["page_layer_ref"])
                    == int(ellipse_by_graphic_ref[int(binding["graphic_ref"])]["page_layer_ref"])
                    and int(binding["primitive_ref"])
                    == int(ellipse_by_graphic_ref[int(binding["graphic_ref"])]["primitive_ref"]) + 1
                    and math.hypot(
                        float(binding["center"][0])
                        - float(ellipse_by_graphic_ref[int(binding["graphic_ref"])]["x"]),
                        float(binding["center"][1])
                        - float(ellipse_by_graphic_ref[int(binding["graphic_ref"])]["y"]),
                    )
                    < 1e-9
                ]
                layer_member_counts = Counter(
                    int(binding["page_layer_ref"])
                    for bindings in (
                        line_bindings,
                        text_bindings,
                        relation_bindings,
                        ellipse_bindings,
                        pipe_arc_bindings,
                        circle_geometry_bindings,
                    )
                    for binding in bindings
                    if int(binding["page_layer_ref"]) in layer_names_by_ref
                )
                layer_member_count_validation = [
                    {
                        "name": str(record["name"]),
                        "object_ref": f"0x{int(record['object_ref']):04X}",
                        "declared_member_count_field_3": int(record["field_3"]),
                        "decoded_member_record_count": layer_member_counts[int(record["object_ref"])],
                        "matches": int(record["field_3"])
                        == layer_member_counts[int(record["object_ref"])],
                    }
                    for record in group
                ]
                page_groups.append(
                    {
                        "page_index": page_index + 1,
                        "sheet_stream": sheet_name,
                        "sheet_local_start": local_start,
                        "record_count": len(group),
                        "object_ref_min": min(object_refs) if object_refs else None,
                        "object_ref_max": max(object_refs) if object_refs else None,
                        "object_ref_min_equals_sheet_local_start_minus_two": (
                            min(object_refs) == local_start - 2
                            if object_refs and local_start is not None
                            else None
                        ),
                        "name_sequence_matches_first_page_group": (
                            [str(record["name"]) for record in group] == first_group_names
                        ),
                        "18_32_page_layer_binding": {
                            "layout": "graphic_ref@10,page_layer_ref@14,style_ref@20",
                            "valid_record_count": len(line_bindings),
                            "renderable_line_record_count": sum(
                                not bool(binding["is_zero_length_point_record"])
                                for binding in line_bindings
                            ),
                            "zero_length_point_record_count": sum(
                                bool(binding["is_zero_length_point_record"])
                                for binding in line_bindings
                            ),
                            "page_layer_ref_match_count": len(line_bindings) - len(
                                [
                                    layer_ref
                                    for layer_ref in binding_layer_refs
                                    if layer_ref not in layer_names_by_ref
                                ]
                            ),
                            "unmatched_page_layer_refs": unmatched_layer_refs,
                            "matches_this_page_named_layer_group": not unmatched_layer_refs,
                            "line_count_by_named_layer": dict(sorted(layer_line_counts.items())),
                        },
                        "4d_text_page_layer_binding": {
                            "layout": "child_ref@6,secondary_ref@10,page_layer_ref@14,style_ref@20,utf16_text@30",
                            "valid_text_record_count": len(text_bindings),
                            "page_layer_ref_match_count": len(text_bindings) - len(
                                [
                                    layer_ref
                                    for layer_ref in text_layer_refs
                                    if layer_ref not in layer_names_by_ref
                                ]
                            ),
                            "unmatched_page_layer_refs": unmatched_text_layer_refs,
                            "matches_this_page_named_layer_group": not unmatched_text_layer_refs,
                            "text_count_by_named_layer": dict(sorted(layer_text_counts.items())),
                            "text_count_by_style_ref": {
                                f"0x{style_ref:04X}": count
                                for style_ref, count in sorted(text_style_counts.items())
                            },
                            "secondary_ref_categories": dict(
                                sorted(text_secondary_ref_categories.items())
                            ),
                        },
                        "7b_composite_header": {
                            "layout": "record_length@2,composite_ref@6,sheet_ref@10,child_count@22",
                            "valid_record_count": len(composite_headers),
                            "sheet_ref_match_count": sum(
                                int(record["sheet_ref"]) == sheet_numeric_id
                                for record in composite_headers
                            ),
                        },
                        "13_ac_page_layer_relation": {
                            "layout": "fixed-172-byte primitive_ref@6,graphic_ref@10,page_layer_ref@14,bounding_box_4d@35",
                            "valid_relation_count": len(relation_bindings),
                            "page_layer_ref_match_count": sum(
                                layer_ref in layer_names_by_ref
                                for layer_ref in relation_layer_refs
                            ),
                            "same_page_layer_as_18_32_graphic_count": relation_matches_line_layer,
                            "reverse_18_32_line_alias_validation": relation_reverse_alias_validation,
                            "unmatched_page_layer_refs": sorted(
                                set(relation_layer_refs).difference(layer_names_by_ref)
                            ),
                        },
                        "59_2b_page_layer_binding": {
                            "layout": "primitive_ref@6,graphic_ref@10,page_layer_ref@14,style_ref@20,center@24",
                            "valid_ellipse_like_record_count": len(ellipse_bindings),
                            "page_layer_ref_match_count": sum(
                                layer_ref in layer_names_by_ref
                                for layer_ref in ellipse_layer_refs
                            ),
                            "unmatched_page_layer_refs": sorted(
                                set(ellipse_layer_refs).difference(layer_names_by_ref)
                            ),
                            "record_count_by_named_layer": dict(
                                sorted(ellipse_layer_counts.items())
                            ),
                            "direct_dynamic_uci_match_count": sum(
                                int(binding["graphic_ref"]) in dynamic_uci_by_graphic_ref
                                for binding in ellipse_bindings
                            ),
                            "direct_dynamic_uci_match_count_by_named_layer": dict(
                                sorted(ellipse_direct_uci_counts.items())
                            ),
                        },
                        "61_pipe_arc_page_layer_binding": {
                            "layout": "primitive_ref@6,graphic_ref@10,page_layer_ref@14,style_ref@20,center_radius_angles@24",
                            "valid_arc_record_count": len(pipe_arc_bindings),
                            "page_layer_ref_match_count": sum(
                                layer_ref in layer_names_by_ref
                                for layer_ref in pipe_arc_layer_refs
                            ),
                            "unmatched_page_layer_refs": sorted(
                                set(pipe_arc_layer_refs).difference(layer_names_by_ref)
                            ),
                            "record_count_by_named_layer": dict(
                                sorted(pipe_arc_layer_counts.items())
                            ),
                            "direct_dynamic_uci_match_count_by_named_layer": dict(
                                sorted(pipe_arc_direct_uci_counts.items())
                            ),
                        },
                        "13_63_circle_geometry_binding": {
                            "layout": "primitive_ref@6,graphic_ref@10,page_layer_ref@14,style_ref@20,center_radius@35",
                            "valid_record_count": len(circle_geometry_bindings),
                            "paired_59_2b_ellipse_count": len(circle_geometry_pairs),
                            "paired_59_2b_ellipse_layer_counts": dict(
                                sorted(
                                    Counter(
                                        layer_names_by_ref[int(binding["page_layer_ref"])]
                                        for binding in circle_geometry_pairs
                                        if int(binding["page_layer_ref"]) in layer_names_by_ref
                                    ).items()
                                )
                            ),
                        },
                        "named_layer_member_count_validation": {
                            "field_3_semantic": "count of decoded Sheet records assigned to this page-layer object",
                            "layer_count": len(layer_member_count_validation),
                            "matching_layer_count": sum(
                                bool(record["matches"])
                                for record in layer_member_count_validation
                            ),
                            "layers": layer_member_count_validation,
                        },
                    }
                )
            psmcluster_named_records["physical_sheet_page_groups"] = page_groups

    return {
        "source_sha": str(sha_path),
        "validated": {
            "psmcluster_named_records": psmcluster_named_records,
            "psmcluster_88_page_default_records": psmcluster_88_page_default_records,
            "psmcluster_88_default_parent_validation": psmcluster_88_default_parent_validation,
            "psmcluster_88_page_container_validation": psmcluster_88_page_container_validation,
            "psmcluster_42_page_layer_containers": psmcluster_42_page_layer_containers,
            "psmcluster_42_default_object_validation": psmcluster_42_default_object_validation,
            "psmcluster_42_member_anchor_validation": psmcluster_42_member_anchor_validation,
            "psmcluster_57_page_linked_control_records": psmcluster_57_page_linked_control_records,
            "psmcluster_57_layer_state_profile_validation": psmcluster_57_layer_state_profile_validation,
            "psmcluster_75_root_catalog": psmcluster_75_root_catalog,
            "psmcluster_02_preference_index": psmcluster_02_preference_index,
            "psmcluster_6c_default_style_bundles": psmcluster_6c_default_style_bundles,
            "psmcluster_89_application_property_records": psmcluster_89_application_property_records,
            "psmcluster_89_parent_object_validation": psmcluster_89_parent_object_validation,
            "psmcluster_89_pasted_graphic_jsite_validation": psmcluster_89_pasted_graphic_jsite_validation,
            "psmcluster_73_background_records": psmcluster_73_background_records,
            "psmcluster_64_zero_control_slots": psmcluster_64_zero_control_slots,
            "psmcluster_65_section_name_sites": psmcluster_65_section_name_sites,
            "psmcluster_65_section_sheet_directory_validation": psmcluster_65_section_sheet_directory_validation,
            "psmcluster_top_vfset_records": psmcluster_top_vfset_records,
            "psmcluster_envelope_runs": psmcluster_envelope_runs,
            "psmcluster_envelope_tag_evidence": psmcluster_envelope_tag_evidence,
            "psmcluster_envelope_tag_layer_evidence": psmcluster_envelope_tag_layer_evidence,
            "cluster_registry": cluster_registry,
            "root_registry": root_registry,
            "spacemap": main_name,
            "main_spacemap": main_result,
            "relation_code_counts": relation_result,
            "segment_table": segment_table,
            "stylecluster_font_records": stylecluster_font_records,
            "stylecluster_text_style_links": stylecluster_text_style_links,
            "stylecluster_2e_style_records": stylecluster_2e_style_records,
            "stylecluster_2e_category_validated_primitive_usage": stylecluster_2e_category_usage,
            "stylecluster_12_font_resources": stylecluster_12_font_resources,
            "stylecluster_named_style_catalog_entries": stylecluster_named_style_catalog_entries,
            "stylecluster_2f_dash_patterns": stylecluster_2f_dash_patterns,
            "dash_pattern_validated_primitive_usage": dash_pattern_usage,
            "stylecluster_2a_fixed_style_records": stylecluster_2a_fixed_style_records,
            "fixed_style_validated_primitive_usage": fixed_style_usage,
            "stylecluster_70_fixed_records": stylecluster_70_fixed_records,
            "stylecluster_18_control_records": stylecluster_18_control_records,
            "stylecluster_84_polygon_resources": stylecluster_84_polygon_resources,
            "stylecluster_7c_polygon_groups": stylecluster_7c_polygon_groups,
            "stylecluster_61_local_arc_resources": stylecluster_61_local_arc_resources,
            "stylecluster_59_local_ellipse_resources": stylecluster_59_local_ellipse_resources,
            "stylecluster_1b_named_internal_style_records": stylecluster_1b_named_internal_style_records,
            "stylecluster_local_resource_sheet_references": stylecluster_local_resource_sheet_references,
            "stylecluster_zero_object_containers": stylecluster_zero_object_containers,
            "physical_sheet_4d_text_style_coverage": {
                "text_record_count": len(physical_sheet_text_records),
                "font_resolved_text_record_count": sum(
                    int(record["style_ref"]) in text_style_links_by_ref
                    for record in physical_sheet_text_records
                ),
                "unresolved_style_refs": sorted(
                    {
                        f"0x{int(record['style_ref']):04X}"
                        for record in physical_sheet_text_records
                        if int(record["style_ref"]) not in text_style_links_by_ref
                    }
                ),
            },
            "physical_sheet_4d_text_psm_envelope_bindings": physical_sheet_4d_text_psm_envelope_bindings,
            "physical_sheet_7b_composite_child_graphic_links": physical_sheet_composite_child_links,
            "jsite_resource_list": jsite_resource_list,
            "jsite_resources": jsite_resources,
            "physical_sheet_3d_placement_wrappers": physical_sheet_3d_placement_summary,
            "appobject_dependency": appobject_dependency,
            "docversion3_history": docversion3_history,
            "docversion2_profile": docversion2_profile,
            "ole_summary_metadata": ole_summary_metadata,
            "dynamic_attributes_metadata": dynamic_attributes_metadata,
            "tagged_text_storage_list": tagged_text_storage_list,
            "fixed_empty_sheet_stubs": fixed_empty_sheet_stubs,
            "tagged_text_xml": tagged_text_xml,
            "document_dynamic_settings": document_dynamic_settings,
            "sheet221_revision_binding_fields": revision_binding_fields,
            "sheet221_revision_binding_resolution": revision_binding_resolution,
            "sheet221_template_profile": sheet221_template_profile,
        },
        "not_yet_decoded": {
            name: summary
            for name, summary in remaining_undecoded.items()
        },
        "additional_validated_spacemaps": additional_validated,
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
