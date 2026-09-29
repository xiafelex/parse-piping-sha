#!/usr/bin/env python3
"""Create missing Blue Star PCF WELD blocks from Excel weld coordinates.

This module is intentionally byte-oriented.  It only rewrites ASCII PCF
keywords and appends ASCII component blocks; all original non-ASCII bytes,
encoding, BOM and line endings are preserved and verified by the caller.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable


NUMBER = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
PIPELINE_RE = re.compile(
    rb"^PIPELINE-REFERENCE[ \t]+([^\r\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
POINT_RE = re.compile(
    rb"^([ \t]+)(END-POINT)[ \t]+("
    + NUMBER
    + rb")[ \t]+("
    + NUMBER
    + rb")[ \t]+("
    + NUMBER
    + rb")[ \t]+("
    + NUMBER
    + rb")([^\r\n]*)(\r\n|\n|\r)?$",
    re.IGNORECASE,
)
ATTRIBUTE_RE = re.compile(
    rb"^([ \t]+)([A-Z][A-Z0-9-]*)([ \t]*)(.*?)([ \t]*)(\r\n|\n|\r)?$",
    re.IGNORECASE,
)


@dataclass
class PointLine:
    line_index: int
    xyz: tuple[Decimal, Decimal, Decimal]
    bore: Decimal
    suffix: bytes


@dataclass
class Block:
    index: int
    start: int
    end: int
    kind: str
    lines: list[bytes]
    attributes: dict[str, list[tuple[bytes, int]]] = field(default_factory=dict)
    endpoints: list[PointLine] = field(default_factory=list)

    def value(self, key: str) -> str:
        rows = self.attributes.get(key.upper(), [])
        return rows[0][0].decode("ascii", errors="strict").strip() if rows else ""

    @property
    def component_identifier(self) -> str:
        return self.value("COMPONENT-IDENTIFIER")

    @property
    def master_component_identifier(self) -> str:
        return self.value("MASTER-COMPONENT-IDENTIFIER")

    @property
    def uci(self) -> str:
        return self.value("UCI") or self.value("UNIQUE-COMPONENT-IDENTIFIER")


@dataclass
class Document:
    path: Path
    relative_path: Path
    unit: str
    pipeline: str
    raw: bytes
    lines: list[bytes]
    blocks: list[Block]
    eol: bytes


@dataclass
class AugmentDecision:
    excel_row: int
    unit: str
    pipeline: str
    weld_number: str
    weld_type: str
    requested_coordinate: tuple[Decimal, Decimal, Decimal]
    action: str
    reason: str
    relative_path: str = ""
    owner_kind: str = ""
    owner_component_identifier: str = ""
    owner_uci: str = ""
    inserted_coordinate: tuple[Decimal, Decimal, Decimal] | None = None
    inserted_bore: Decimal | None = None
    source_fraction: Decimal | None = None
    new_weld_uci: str = ""


@dataclass
class RequestPlacement:
    record: object
    decision: AugmentDecision
    owner: Block
    coordinate: tuple[Decimal, Decimal, Decimal]
    bore: Decimal
    mode: str
    fraction: Decimal
    neighbor_uci_a: str
    neighbor_uci_b: str


@dataclass
class SplitPlan:
    owner: Block
    requests: list[RequestPlacement]


def decimal_distance_max(
    left: tuple[Decimal, Decimal, Decimal],
    right: tuple[Decimal, Decimal, Decimal],
) -> Decimal:
    return max(abs(left[index] - right[index]) for index in range(3))


def point_segment_projection(
    point: tuple[Decimal, Decimal, Decimal],
    start: tuple[Decimal, Decimal, Decimal],
    finish: tuple[Decimal, Decimal, Decimal],
) -> tuple[Decimal, tuple[Decimal, Decimal, Decimal]]:
    vector = tuple(finish[index] - start[index] for index in range(3))
    offset = tuple(point[index] - start[index] for index in range(3))
    denominator = sum(value * value for value in vector)
    if denominator == 0:
        return Decimal("-1"), start
    fraction = sum(offset[index] * vector[index] for index in range(3)) / denominator
    projected = tuple(
        start[index] + fraction * vector[index] for index in range(3)
    )
    return fraction, projected  # type: ignore[return-value]


def euclidean(
    start: tuple[Decimal, Decimal, Decimal],
    finish: tuple[Decimal, Decimal, Decimal],
) -> Decimal:
    return Decimal(
        str(
            math.sqrt(
                sum(float(finish[index] - start[index]) ** 2 for index in range(3))
            )
        )
    )


def parse_document(path: Path, root: Path) -> Document:
    raw = path.read_bytes()
    pipeline_match = PIPELINE_RE.search(raw)
    if not pipeline_match:
        raise ValueError(f"PCF缺少PIPELINE-REFERENCE：{path}")
    pipeline = pipeline_match.group(1).strip().decode("ascii", errors="strict")
    lines = raw.splitlines(keepends=True)
    eol = next(
        (
            suffix
            for line in lines
            for suffix in (b"\r\n", b"\n", b"\r")
            if line.endswith(suffix)
        ),
        b"\n",
    )
    starts = [
        index
        for index, line in enumerate(lines)
        if line.rstrip(b"\r\n")
        and line.rstrip(b"\r\n")[:1] not in (b" ", b"\t")
    ]
    blocks: list[Block] = []
    for block_index, start in enumerate(starts):
        end = starts[block_index + 1] if block_index + 1 < len(starts) else len(lines)
        kind = lines[start].strip().decode("ascii", errors="strict").upper()
        block = Block(
            index=block_index,
            start=start,
            end=end,
            kind=kind,
            lines=lines[start:end],
        )
        for line_index in range(start + 1, end):
            line = lines[line_index]
            point_match = POINT_RE.match(line)
            if point_match:
                block.endpoints.append(
                    PointLine(
                        line_index=line_index,
                        xyz=tuple(
                            Decimal(point_match.group(group).decode("ascii"))
                            for group in (3, 4, 5)
                        ),
                        bore=Decimal(point_match.group(6).decode("ascii")),
                        suffix=point_match.group(7),
                    )
                )
            attribute_match = ATTRIBUTE_RE.match(line)
            if attribute_match:
                key = attribute_match.group(2).decode("ascii").upper()
                block.attributes.setdefault(key, []).append(
                    (attribute_match.group(4).strip(), line_index)
                )
        blocks.append(block)
    relative = path.relative_to(root)
    return Document(
        path=path,
        relative_path=relative,
        unit=relative.parts[0] if len(relative.parts) > 1 else "",
        pipeline=pipeline,
        raw=raw,
        lines=lines,
        blocks=blocks,
        eol=eol,
    )


def component_id(block: Block) -> int:
    value = block.component_identifier
    return int(value) if value.isdigit() else 0


def endpoint_neighbors(
    document: Document,
    coordinate: tuple[Decimal, Decimal, Decimal],
    tolerance: Decimal,
    excluded: Block | None = None,
) -> list[Block]:
    result: list[Block] = []
    for block in document.blocks:
        if block is excluded or not block.uci:
            continue
        if any(decimal_distance_max(coordinate, point.xyz) <= tolerance for point in block.endpoints):
            result.append(block)
    return result


def preferred_neighbor_uci(blocks: Iterable[Block]) -> str:
    ranked = sorted(
        blocks,
        key=lambda block: (
            0 if block.kind == "WELD" else 1,
            0 if block.kind in {"PIPE", "PIPE-FIXED"} else 1,
            block.start,
        ),
    )
    return ranked[0].uci if ranked else ""


def owner_rank(block: Block) -> tuple[int, int]:
    priorities = {
        "PIPE": 0,
        "PIPE-FIXED": 1,
        "ELBOW": 2,
        "BEND": 3,
        "TEE": 4,
        "REDUCER-CONCENTRIC": 5,
        "REDUCER-ECCENTRIC": 6,
        "COUPLING": 7,
        "UNION": 8,
        "FLANGE": 9,
    }
    return priorities.get(block.kind, 20), block.start


def existing_weld_at(
    document: Document,
    coordinate: tuple[Decimal, Decimal, Decimal],
    tolerance: Decimal,
) -> bool:
    return any(
        block.kind == "WELD"
        and any(decimal_distance_max(coordinate, point.xyz) <= tolerance for point in block.endpoints)
        for block in document.blocks
    )


def locate_request(
    document: Document,
    record: object,
    tolerance: Decimal,
) -> RequestPlacement | None:
    coordinate = getattr(record, "coordinate")
    assert coordinate is not None
    endpoint_candidates: list[tuple[Block, PointLine]] = []
    for block in document.blocks:
        if (
            block.kind == "WELD"
            or not block.component_identifier
            or not block.uci
        ):
            continue
        for endpoint in block.endpoints:
            if endpoint.bore > 0 and decimal_distance_max(coordinate, endpoint.xyz) <= tolerance:
                endpoint_candidates.append((block, endpoint))
    if endpoint_candidates:
        bores = {endpoint.bore for _, endpoint in endpoint_candidates}
        if len(bores) != 1:
            return None
        owner, endpoint = min(endpoint_candidates, key=lambda item: owner_rank(item[0]))
        if len(owner.endpoints) >= 2:
            distances = [
                decimal_distance_max(endpoint.xyz, candidate.xyz)
                for candidate in owner.endpoints[:2]
            ]
            endpoint_index = distances.index(min(distances))
            fraction = Decimal(endpoint_index)
            start_xyz = owner.endpoints[0].xyz
            finish_xyz = owner.endpoints[1].xyz
        else:
            fraction = Decimal("0")
            start_xyz = finish_xyz = endpoint.xyz
        neighbor_a = preferred_neighbor_uci(
            endpoint_neighbors(document, start_xyz, tolerance, owner)
        )
        neighbor_b = preferred_neighbor_uci(
            endpoint_neighbors(document, finish_xyz, tolerance, owner)
        )
        decision = AugmentDecision(
            excel_row=getattr(record, "row_number"),
            unit=getattr(record, "unit"),
            pipeline=getattr(record, "pipeline"),
            weld_number=getattr(record, "weld_number"),
            weld_type=getattr(record, "weld_point_type"),
            requested_coordinate=coordinate,
            action="新增端点WELD",
            reason="Excel坐标与现有组件端点一致，但PCF缺少显式WELD块",
            relative_path=document.relative_path.as_posix(),
            owner_kind=owner.kind,
            owner_component_identifier=owner.component_identifier,
            owner_uci=owner.uci,
            inserted_coordinate=endpoint.xyz,
            inserted_bore=endpoint.bore,
            source_fraction=fraction,
        )
        return RequestPlacement(
            record=record,
            decision=decision,
            owner=owner,
            coordinate=endpoint.xyz,
            bore=endpoint.bore,
            mode="COMPONENT-ENDPOINT",
            fraction=fraction,
            neighbor_uci_a=neighbor_a,
            neighbor_uci_b=neighbor_b,
        )

    interior: list[
        tuple[Decimal, Block, Decimal, tuple[Decimal, Decimal, Decimal]]
    ] = []
    for block in document.blocks:
        if (
            block.kind not in {"PIPE", "PIPE-FIXED"}
            or len(block.endpoints) != 2
            or not block.component_identifier
            or not block.uci
        ):
            continue
        first, second = block.endpoints
        if first.bore <= 0 or second.bore <= 0 or first.bore != second.bore:
            continue
        fraction, projected = point_segment_projection(
            coordinate, first.xyz, second.xyz
        )
        if not Decimal("0") < fraction < Decimal("1"):
            continue
        distance = euclidean(coordinate, projected)
        if distance <= tolerance:
            interior.append((distance, block, fraction, projected))
    if not interior:
        return None
    interior.sort(key=lambda item: (item[0], item[1].start))
    if len(interior) > 1 and interior[0][0] == interior[1][0]:
        return None
    _, owner, fraction, projected = interior[0]
    first, second = owner.endpoints
    neighbor_a = preferred_neighbor_uci(
        endpoint_neighbors(document, first.xyz, tolerance, owner)
    )
    neighbor_b = preferred_neighbor_uci(
        endpoint_neighbors(document, second.xyz, tolerance, owner)
    )
    decision = AugmentDecision(
        excel_row=getattr(record, "row_number"),
        unit=getattr(record, "unit"),
        pipeline=getattr(record, "pipeline"),
        weld_number=getattr(record, "weld_number"),
        weld_type=getattr(record, "weld_point_type"),
        requested_coordinate=coordinate,
        action="拆分PIPE并新增WELD",
        reason="Excel坐标位于现有直管内部，按PIPE+WELD+PIPE重建拓扑",
        relative_path=document.relative_path.as_posix(),
        owner_kind=owner.kind,
        owner_component_identifier=owner.component_identifier,
        owner_uci=owner.uci,
        inserted_coordinate=projected,
        inserted_bore=first.bore,
        source_fraction=fraction,
    )
    return RequestPlacement(
        record=record,
        decision=decision,
        owner=owner,
        coordinate=projected,
        bore=first.bore,
        mode="PIPE-INTERPOLATION",
        fraction=fraction,
        neighbor_uci_a=neighbor_a,
        neighbor_uci_b=neighbor_b,
    )


def stable_weld_uci(document: Document, request: RequestPlacement) -> str:
    coordinate = ",".join(format(value, "f") for value in request.coordinate)
    seed = (
        f"bluestar-pcf-weld|{document.unit}|{document.pipeline}|"
        f"{coordinate}|{getattr(request.record, 'weld_number')}"
    )
    return "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed)).upper() + "}"


def replace_attribute(
    line: bytes,
    key: str,
    value: str,
) -> bytes:
    match = ATTRIBUTE_RE.match(line)
    if not match or match.group(2).decode("ascii").upper() != key.upper():
        raise ValueError(f"目标行不是{key}")
    return (
        match.group(1)
        + match.group(2)
        + (match.group(3) or b"    ")
        + value.encode("ascii")
        + match.group(5)
        + (match.group(6) or b"")
    )


def decimal_places(value: bytes) -> int:
    text = value.decode("ascii").strip()
    return len(text.rsplit(".", 1)[1]) if "." in text else 0


def format_like(value: Decimal, original: bytes) -> str:
    places = decimal_places(original)
    return f"{value:.{places}f}" if places else str(int(value.to_integral_value()))


def point_line(
    coordinate: tuple[Decimal, Decimal, Decimal],
    bore: Decimal,
    eol: bytes,
    suffix: bytes = b"",
) -> bytes:
    xyz = "    ".join(f"{value:.3f}" for value in coordinate)
    bore_text = format(bore, "f")
    return (
        b"    END-POINT    "
        + xyz.encode("ascii")
        + b"     "
        + bore_text.encode("ascii")
        + suffix.rstrip(b"\r\n")
        + eol
    )


def clone_pipe_segment(
    document: Document,
    block: Block,
    component_identifier: int,
    start: tuple[Decimal, Decimal, Decimal],
    finish: tuple[Decimal, Decimal, Decimal],
    start_suffix: bytes,
    finish_suffix: bytes,
    bore: Decimal,
    fraction_of_length: Decimal,
) -> list[bytes]:
    lines = list(block.lines)
    endpoint_seen = 0
    for local_index, line in enumerate(lines):
        attribute = ATTRIBUTE_RE.match(line)
        if not attribute:
            continue
        key = attribute.group(2).decode("ascii").upper()
        raw_value = attribute.group(4).strip()
        if key == "COMPONENT-IDENTIFIER":
            lines[local_index] = replace_attribute(
                line, key, str(component_identifier)
            )
        elif key == "END-POINT":
            coordinate = start if endpoint_seen == 0 else finish
            suffix = start_suffix if endpoint_seen == 0 else finish_suffix
            lines[local_index] = point_line(
                coordinate, bore, document.eol, suffix
            )
            endpoint_seen += 1
        elif key == "WEIGHT" and raw_value:
            original = Decimal(raw_value.decode("ascii"))
            lines[local_index] = replace_attribute(
                line, key, format_like(original * fraction_of_length, raw_value)
            )
        elif key == "CUT-PIECE-LENGTH" and raw_value:
            length = euclidean(start, finish)
            lines[local_index] = replace_attribute(
                line, key, format_like(length, raw_value)
            )
        elif key == "COMPONENT-ATTRIBUTE41" and raw_value:
            length = euclidean(start, finish)
            lines[local_index] = replace_attribute(
                line, key, format_like(length, raw_value)
            )
    if endpoint_seen != 2:
        raise ValueError(
            f"{document.relative_path}:{block.start + 1} PIPE端点数量不是2"
        )
    return lines


def component_attribute(block: Block, number: int) -> str:
    return block.value(f"COMPONENT-ATTRIBUTE{number}")


def nominal_inches(bore: Decimal) -> str:
    known = {
        Decimal("15"): "0.5",
        Decimal("20"): "0.75",
        Decimal("25"): "1",
        Decimal("32"): "1.25",
        Decimal("40"): "1.5",
        Decimal("50"): "2",
        Decimal("65"): "2.5",
        Decimal("80"): "3",
        Decimal("100"): "4",
        Decimal("125"): "5",
        Decimal("150"): "6",
        Decimal("200"): "8",
        Decimal("250"): "10",
        Decimal("300"): "12",
        Decimal("350"): "14",
        Decimal("400"): "16",
        Decimal("450"): "18",
        Decimal("500"): "20",
        Decimal("600"): "24",
    }
    return known.get(bore, format(bore / Decimal("25"), "f").rstrip("0").rstrip("."))


def weld_block_lines(
    document: Document,
    request: RequestPlacement,
    component_identifier: int,
    master_component_identifier: int,
) -> list[bytes]:
    record = request.record
    weld_type = getattr(record, "weld_point_type").upper()
    if weld_type == "S":
        skey = "WW"
        category = "FABRICATION"
        description = "Shop weld"
        responsibility = "By Pipe Fabricator"
    elif weld_type == "F":
        skey = "WS"
        category = "ERECTION"
        description = "Field weld at job site, including offshore"
        responsibility = "By Pipe Erector"
    else:
        raise ValueError(f"不支持的焊点类型：{weld_type}")
    owner = request.owner
    owner_uci = owner.uci
    weld_uci = stable_weld_uci(document, request)
    request.decision.new_weld_uci = weld_uci
    pipeline_parts = document.pipeline.split("-")
    attr11 = component_attribute(owner, 2) or pipeline_parts[0]
    attr13 = component_attribute(owner, 6) or "-".join(pipeline_parts[1:3])
    attr27 = component_attribute(owner, 27) or document.unit
    attr14 = component_attribute(owner, 8) or pipeline_parts[-1]
    piping_spec = owner.value("PIPING-SPEC")
    size = nominal_inches(request.bore)
    coord = request.coordinate
    eol = document.eol
    lines = [
        b"WELD" + eol,
        f"    COMPONENT-IDENTIFIER    {component_identifier}".encode("ascii") + eol,
        f"    MASTER-COMPONENT-IDENTIFIER    {master_component_identifier}".encode("ascii") + eol,
        point_line(coord, request.bore, eol),
        point_line(coord, request.bore, eol),
        f"    SKEY    {skey}".encode("ascii") + eol,
        f"    CATEGORY    {category}".encode("ascii") + eol,
        f"    REPEAT-WELD-IDENTIFIER    {getattr(record, 'weld_number')}".encode("ascii") + eol,
        f"    UCI    {weld_uci}".encode("ascii") + eol,
        b"    WELD-REMARK-NUMBER" + eol,
        f"    WELD-ATTRIBUTE1    {document.pipeline}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE2    {format(request.bore, 'f')}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE3    {description}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE4    {responsibility}".encode("ascii") + eol,
        b"    WELD-ATTRIBUTE10    WELD" + eol,
        f"    WELD-ATTRIBUTE11    {attr11}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE13    {attr13}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE27    {attr27}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE14    {attr14}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE15    {piping_spec}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE6    {size}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE5    {size}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE7    {size}".encode("ascii") + eol,
        b"    WELD-ATTRIBUTE90    PCF-AUGMENTED" + eol,
        f"    WELD-ATTRIBUTE91    {request.mode}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE92    {owner_uci}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE93    {request.fraction:.12f}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE94    {request.neighbor_uci_a}".encode("ascii") + eol,
        f"    WELD-ATTRIBUTE95    {request.neighbor_uci_b}".encode("ascii") + eol,
    ]
    if owner.value("STATUS"):
        lines.append(f"    STATUS    {owner.value('STATUS')}".encode("ascii") + eol)
    if owner.value("MATERIAL-LIST").upper() == "EXCLUDE":
        lines.append(b"    MATERIAL-LIST    EXCLUDE" + eol)
    return lines


def block_point(block: Block) -> tuple[Decimal, Decimal, Decimal] | None:
    return block.endpoints[0].xyz if block.endpoints else None


def segment_index_for_coordinate(
    boundaries: list[tuple[Decimal, Decimal, Decimal]],
    coordinate: tuple[Decimal, Decimal, Decimal],
) -> int:
    distances = [
        min(
            decimal_distance_max(coordinate, boundaries[index]),
            decimal_distance_max(coordinate, boundaries[index + 1]),
        )
        for index in range(len(boundaries) - 1)
    ]
    return distances.index(min(distances))


def rebuild_document(
    document: Document,
    placements: list[RequestPlacement],
) -> bytes:
    split_by_block: dict[int, list[RequestPlacement]] = defaultdict(list)
    endpoint_by_block: dict[int, list[RequestPlacement]] = defaultdict(list)
    for placement in placements:
        if placement.mode == "PIPE-INTERPOLATION":
            split_by_block[placement.owner.index].append(placement)
        else:
            endpoint_by_block[placement.owner.index].append(placement)

    used_ids = [
        component_id(block)
        for block in document.blocks
        if component_id(block) > 0
    ]
    next_id = max(used_ids, default=0) + 1

    split_rendered: dict[int, list[bytes]] = {}
    split_segment_ids: dict[int, list[int]] = {}
    split_boundaries: dict[
        int, list[tuple[Decimal, Decimal, Decimal]]
    ] = {}
    child_master_replacements: dict[int, int] = {}

    for block_index, requests in split_by_block.items():
        owner = document.blocks[block_index]
        requests.sort(key=lambda item: item.fraction)
        if len(owner.endpoints) != 2:
            raise ValueError("拆分PIPE端点数量不是2")
        first, second = owner.endpoints
        boundaries = [first.xyz] + [item.coordinate for item in requests] + [second.xyz]
        split_boundaries[block_index] = boundaries
        original_id = component_id(owner)

        child_segments: list[int] = []
        for child in document.blocks:
            if child.master_component_identifier != str(original_id):
                continue
            coordinate = block_point(child)
            if coordinate is not None:
                child_segments.append(segment_index_for_coordinate(boundaries, coordinate))
        keep_index = Counter(child_segments).most_common(1)[0][0] if child_segments else 0
        segment_ids: list[int] = []
        for index in range(len(boundaries) - 1):
            if index == keep_index:
                segment_ids.append(original_id)
            else:
                segment_ids.append(next_id)
                next_id += 1
        split_segment_ids[block_index] = segment_ids

        original_length = euclidean(first.xyz, second.xyz)
        rendered: list[bytes] = []
        endpoint_requests = endpoint_by_block.pop(block_index, [])
        at_a = [
            item
            for item in endpoint_requests
            if decimal_distance_max(item.coordinate, first.xyz)
            <= decimal_distance_max(item.coordinate, second.xyz)
        ]
        at_b = [item for item in endpoint_requests if item not in at_a]
        for segment_index, segment_id in enumerate(segment_ids):
            start = boundaries[segment_index]
            finish = boundaries[segment_index + 1]
            start_suffix = first.suffix if segment_index == 0 else b""
            finish_suffix = (
                second.suffix if segment_index == len(segment_ids) - 1 else b""
            )
            segment_length = euclidean(start, finish)
            rendered.extend(
                clone_pipe_segment(
                    document,
                    owner,
                    segment_id,
                    start,
                    finish,
                    start_suffix,
                    finish_suffix,
                    first.bore,
                    segment_length / original_length,
                )
            )
            if segment_index == 0:
                for request in at_a:
                    rendered.extend(
                        weld_block_lines(document, request, next_id, segment_id)
                    )
                    next_id += 1
            if segment_index < len(requests):
                request = requests[segment_index]
                rendered.extend(
                    weld_block_lines(document, request, next_id, segment_id)
                )
                next_id += 1
            if segment_index == len(segment_ids) - 1:
                for request in at_b:
                    rendered.extend(
                        weld_block_lines(document, request, next_id, segment_id)
                    )
                    next_id += 1
        split_rendered[block_index] = rendered

        for child in document.blocks:
            if child.master_component_identifier != str(original_id):
                continue
            coordinate = block_point(child)
            if coordinate is None:
                continue
            target_segment = segment_index_for_coordinate(boundaries, coordinate)
            target_id = segment_ids[target_segment]
            if target_id != original_id:
                child_master_replacements[child.index] = target_id

    insert_after: dict[int, list[bytes]] = defaultdict(list)
    for block_index, requests in endpoint_by_block.items():
        owner = document.blocks[block_index]
        master_id = component_id(owner)
        for request in sorted(
            requests, key=lambda item: getattr(item.record, "row_number")
        ):
            insert_after[block_index].extend(
                weld_block_lines(document, request, next_id, master_id)
            )
            next_id += 1

    output: list[bytes] = []
    for block in document.blocks:
        if block.index in split_rendered:
            output.extend(split_rendered[block.index])
            continue
        lines = list(block.lines)
        replacement_master = child_master_replacements.get(block.index)
        if replacement_master is not None:
            for local_index, line in enumerate(lines):
                match = ATTRIBUTE_RE.match(line)
                if (
                    match
                    and match.group(2).decode("ascii").upper()
                    == "MASTER-COMPONENT-IDENTIFIER"
                ):
                    lines[local_index] = replace_attribute(
                        line,
                        "MASTER-COMPONENT-IDENTIFIER",
                        str(replacement_master),
                    )
                    break
        output.extend(lines)
        output.extend(insert_after.get(block.index, []))
    return b"".join(output)


def augment_pcf_tree(
    records: list[object],
    input_root: Path,
    copied_root: Path,
    tolerance: Decimal,
) -> tuple[list[AugmentDecision], dict[str, int]]:
    documents: list[Document] = []
    for path in sorted(input_root.rglob("*.pcf")):
        documents.append(parse_document(path, input_root))
    by_pipeline: dict[tuple[str, str], Document] = {}
    for document in documents:
        key = (document.unit, document.pipeline)
        if key in by_pipeline:
            raise ValueError(f"同一单元和管线对应多个PCF：{key}")
        by_pipeline[key] = document

    decisions: list[AugmentDecision] = []
    placements_by_path: dict[Path, list[RequestPlacement]] = defaultdict(list)
    for record in records:
        coordinate = getattr(record, "coordinate", None)
        if getattr(record, "parse_error", "") or coordinate is None:
            continue
        document = by_pipeline.get((getattr(record, "unit"), getattr(record, "pipeline")))
        if document is None:
            continue
        if existing_weld_at(document, coordinate, tolerance):
            continue
        placement = locate_request(document, record, tolerance)
        if placement is None:
            decisions.append(
                AugmentDecision(
                    excel_row=getattr(record, "row_number"),
                    unit=getattr(record, "unit"),
                    pipeline=getattr(record, "pipeline"),
                    weld_number=getattr(record, "weld_number"),
                    weld_type=getattr(record, "weld_point_type"),
                    requested_coordinate=coordinate,
                    action="未补建",
                    reason="坐标未能唯一定位到组件端点或直管内部",
                    relative_path=document.relative_path.as_posix(),
                )
            )
            continue
        decisions.append(placement.decision)
        placements_by_path[document.relative_path].append(placement)

    changed_files = 0
    for relative_path, placements in placements_by_path.items():
        document = next(
            item for item in documents if item.relative_path == relative_path
        )
        output_raw = rebuild_document(document, placements)
        if bytes(byte for byte in output_raw if byte >= 0x80) != bytes(
            byte for byte in document.raw if byte >= 0x80
        ):
            raise ValueError(f"补建焊缝后非ASCII字节发生变化：{relative_path}")
        output_path = copied_root / relative_path
        output_path.write_bytes(output_raw)
        changed_files += 1

    stats = Counter(decision.action for decision in decisions)
    return decisions, {
        "augmented_files": changed_files,
        "created_endpoint_welds": stats["新增端点WELD"],
        "created_pipe_split_welds": stats["拆分PIPE并新增WELD"],
        "augmentation_unresolved": stats["未补建"],
    }


def validate_augmented_tree(
    input_root: Path,
    copied_root: Path,
    decisions: list[AugmentDecision],
) -> None:
    input_paths = {
        path.relative_to(input_root): path for path in input_root.rglob("*.pcf")
    }
    output_paths = {
        path.relative_to(copied_root): path for path in copied_root.rglob("*.pcf")
    }
    if input_paths.keys() != output_paths.keys():
        raise ValueError("补建焊缝前后PCF文件清单不一致")
    expected_by_path = Counter(
        Path(decision.relative_path)
        for decision in decisions
        if decision.action in {"新增端点WELD", "拆分PIPE并新增WELD"}
    )
    expected_uci = {
        decision.new_weld_uci: decision
        for decision in decisions
        if decision.new_weld_uci
    }
    observed_uci: set[str] = set()
    for relative_path, source_path in input_paths.items():
        output_path = output_paths[relative_path]
        source_raw = source_path.read_bytes()
        output_raw = output_path.read_bytes()
        if bytes(byte for byte in source_raw if byte >= 0x80) != bytes(
            byte for byte in output_raw if byte >= 0x80
        ):
            raise ValueError(f"补建焊缝后非ASCII字节发生变化：{relative_path}")
        expected = expected_by_path.get(relative_path, 0)
        if expected == 0:
            if source_raw != output_raw:
                raise ValueError(f"无需补建焊缝的PCF不再字节一致：{relative_path}")
            continue
        source = parse_document(source_path, input_root)
        output = parse_document(output_path, copied_root)
        source_welds = sum(block.kind == "WELD" for block in source.blocks)
        output_welds = sum(block.kind == "WELD" for block in output.blocks)
        if output_welds - source_welds != expected:
            raise ValueError(
                f"新增WELD数量不正确：{relative_path}，"
                f"应新增{expected}，实际新增{output_welds - source_welds}"
            )
        identifiers = [
            block.component_identifier
            for block in output.blocks
            if block.component_identifier
        ]
        duplicate_ids = [
            value for value, count in Counter(identifiers).items() if count > 1
        ]
        if duplicate_ids:
            raise ValueError(f"组件编号重复：{relative_path} -> {duplicate_ids}")
        identifier_set = set(identifiers)
        missing_masters = [
            (block.start + 1, block.master_component_identifier)
            for block in output.blocks
            if block.master_component_identifier
            and block.master_component_identifier not in identifier_set
        ]
        if missing_masters:
            raise ValueError(
                f"MASTER-COMPONENT-IDENTIFIER无父组件："
                f"{relative_path} -> {missing_masters[:5]}"
            )
        for block in output.blocks:
            if block.kind in {"PIPE", "PIPE-FIXED"} and len(block.endpoints) == 2:
                if euclidean(block.endpoints[0].xyz, block.endpoints[1].xyz) <= 0:
                    raise ValueError(
                        f"拆分后出现零长度PIPE：{relative_path}:{block.start + 1}"
                    )
            if block.value("WELD-ATTRIBUTE90") != "PCF-AUGMENTED":
                continue
            if block.uci not in expected_uci:
                raise ValueError(
                    f"发现计划外PCF-AUGMENTED焊缝："
                    f"{relative_path}:{block.start + 1}:{block.uci}"
                )
            if len(block.endpoints) != 2:
                raise ValueError(
                    f"新增WELD端点数量不是2：{relative_path}:{block.start + 1}"
                )
            if (
                block.endpoints[0].xyz != block.endpoints[1].xyz
                or block.endpoints[0].bore <= 0
                or block.endpoints[0].bore != block.endpoints[1].bore
            ):
                raise ValueError(
                    f"新增WELD端点/管径无效：{relative_path}:{block.start + 1}"
                )
            if block.value("SKEY") not in {"WW", "WS"} or block.value(
                "CATEGORY"
            ) not in {"FABRICATION", "ERECTION"}:
                raise ValueError(
                    f"新增WELD类型字段无效：{relative_path}:{block.start + 1}"
                )
            observed_uci.add(block.uci)
    if observed_uci != set(expected_uci):
        missing = sorted(expected_uci.keys() - observed_uci)
        raise ValueError(f"新增WELD验证缺失：{missing[:5]}")
