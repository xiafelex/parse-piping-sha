#!/usr/bin/env python3
"""Match Excel weld coordinates to PCF geometry and write complete weld data.

The source PCF directory is never modified.  The complete directory is copied
to a fixed sibling output directory.  Missing endpoint welds are created as
WELD blocks; an interior straight-pipe weld rebuilds the topology as
PIPE+WELD+PIPE.  For each unique match, the script writes the weld number to
REPEAT-WELD-IDENTIFIER and maps the Excel S/F weld-point type to ISOGEN SKEY,
CATEGORY and project responsibility attributes.  Ambiguous or unresolved
records are left unchanged and listed in an audit workbook.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from bluestar_pcf_weld_augmentation import (
    AugmentDecision,
    augment_pcf_tree,
    validate_augmented_tree,
)


SCRIPT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_EXCEL = SCRIPT_DIR / "蓝星项目" / "焊口表-蓝星项目-增加字母管段号.xlsx"
DEFAULT_INPUT = SCRIPT_DIR / "蓝星项目" / "PCF" / "最终可导入完整版本_第2版"
DEFAULT_OUTPUT = SCRIPT_DIR / "蓝星项目" / "PCF" / "最终可导入完整版本_第2版_焊口号及类型反写"
DEFAULT_AUDIT_NAME = "焊口号及类型反写审计.xlsx"

NUMBER_RE = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
PIPELINE_RE = re.compile(
    rb"^PIPELINE-REFERENCE[ \t]+([^\r\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
ENDPOINT_RE = re.compile(
    rb"^[ \t]+END-POINT[ \t]+(" + NUMBER_RE + rb")[ \t]+("
    + NUMBER_RE
    + rb")[ \t]+("
    + NUMBER_RE
    + rb")",
    re.IGNORECASE,
)
ATTRIBUTE_RE = re.compile(
    rb"^[ \t]+([A-Z][A-Z0-9-]*)[ \t]*(.*?)[ \t]*(?:\r?\n)?$",
    re.IGNORECASE,
)
REPEAT_LINE_RE = re.compile(
    rb"^([ \t]+REPEAT-WELD-IDENTIFIER)([ \t]+)([^ \t\r\n]*)([ \t]*)(\r\n|\n|\r)?$",
    re.IGNORECASE,
)
SKEY_LINE_RE = re.compile(
    rb"^([ \t]+SKEY)([ \t]+)([^ \t\r\n]*)([ \t]*)(\r\n|\n|\r)?$",
    re.IGNORECASE,
)
CATEGORY_LINE_RE = re.compile(
    rb"^([ \t]+CATEGORY)([ \t]+)([^ \t\r\n]*)([ \t]*)(\r\n|\n|\r)?$",
    re.IGNORECASE,
)
WELD_ATTRIBUTE3_LINE_RE = re.compile(
    rb"^([ \t]+WELD-ATTRIBUTE3)([ \t]+)(.*?)([ \t]*)(\r\n|\n|\r)?$",
    re.IGNORECASE,
)
WELD_ATTRIBUTE4_LINE_RE = re.compile(
    rb"^([ \t]+WELD-ATTRIBUTE4)([ \t]+)(.*?)([ \t]*)(\r\n|\n|\r)?$",
    re.IGNORECASE,
)


@dataclass
class ExcelWeld:
    row_number: int
    unit: str
    pipeline: str
    weld_number: str
    segment_number: str
    weld_point_type: str
    coordinate_text: str
    coordinate: tuple[Decimal, Decimal, Decimal] | None
    parse_error: str = ""


@dataclass
class PcfWeld:
    source_path: Path
    relative_path: Path
    unit: str
    pipeline: str
    block_start_line: int
    repeat_line_index: int
    repeat_line_number: int
    repeat_value_bytes: bytes
    repeat_value_text: str
    skey_line_index: int
    skey_line_number: int
    skey_value_bytes: bytes
    endpoints: tuple[tuple[Decimal, Decimal, Decimal], ...]
    skey: str
    category_line_index: int | None
    category_line_number: int | None
    category_value_bytes: bytes
    category: str
    attribute3_line_index: int
    attribute3_line_number: int
    attribute3_value_bytes: bytes
    attribute3: str
    attribute4_line_index: int
    attribute4_line_number: int
    attribute4_value_bytes: bytes
    attribute4: str
    component_identifier: str
    uci: str

    @property
    def key(self) -> tuple[str, int]:
        return (self.relative_path.as_posix(), self.block_start_line)


@dataclass
class Candidate:
    weld: PcfWeld
    distance: Decimal
    endpoint_index: int


@dataclass
class WeldWritePlan:
    weld: PcfWeld
    new_number: str
    new_skey: str
    new_category: str
    new_attribute3: str
    new_attribute4: str


@dataclass
class AuditResult:
    excel: ExcelWeld
    status: str = ""
    reason: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    selected: Candidate | None = None
    written_number: str = ""
    target_skey: str = ""
    target_category: str = ""
    type_action: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按单元号、管线号和焊点坐标，将Excel焊点编号写入PCF副本。"
    )
    parser.add_argument("--excel", type=Path, default=DEFAULT_EXCEL, help="焊口表Excel")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT, help="正式PCF目录")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="新建的PCF副本目录")
    parser.add_argument(
        "--tolerance-mm",
        type=Decimal,
        default=Decimal("0.03"),
        help="每个坐标轴允许的最大差值，默认0.03 mm",
    )
    parser.add_argument(
        "--overwrite-existing-number",
        action="store_true",
        help="允许覆盖PCF中非0且与Excel不同的已有焊口号；默认拒绝覆盖",
    )
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help="允许原子替换已存在的自定义输出目录；固定默认输出目录自动替换",
    )
    return parser.parse_args()


def cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_coordinate(value: object) -> tuple[tuple[Decimal, Decimal, Decimal] | None, str]:
    text = cell_text(value)
    if not text:
        return None, "焊点位置为空"
    pieces = [piece.strip() for piece in text.split(",")]
    if len(pieces) != 3:
        return None, f"焊点位置不是三个逗号分隔的数值：{text}"
    try:
        xyz = tuple(Decimal(piece) for piece in pieces)
    except InvalidOperation:
        return None, f"焊点位置包含非数值内容：{text}"
    return xyz, ""  # type: ignore[return-value]


def read_excel_welds(path: Path) -> list[ExcelWeld]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    worksheet = workbook.active
    rows = worksheet.iter_rows(values_only=True)
    try:
        first_row = next(rows)
    except StopIteration as exc:
        raise ValueError("Excel没有任何内容") from exc

    headers = {
        cell_text(value): index
        for index, value in enumerate(first_row)
        if cell_text(value)
    }
    required = ["管线号", "焊点编号", "管段编号", "焊点类型", "焊点位置", "单元号"]
    missing = [name for name in required if name not in headers]
    if missing:
        raise ValueError(f"Excel缺少必要列：{', '.join(missing)}")

    welds: list[ExcelWeld] = []
    for row_number, values in enumerate(rows, start=2):
        pipeline = cell_text(values[headers["管线号"]])
        weld_number = cell_text(values[headers["焊点编号"]])
        coordinate_value = values[headers["焊点位置"]]
        coordinate_text = cell_text(coordinate_value)
        if not pipeline and not weld_number and not coordinate_text:
            continue
        coordinate, error = parse_coordinate(coordinate_value)
        if not weld_number:
            error = error or "焊点编号为空"
        elif any(ord(char) > 127 for char in weld_number):
            error = error or f"焊点编号不是ASCII字符：{weld_number}"
        elif any(char.isspace() for char in weld_number):
            error = error or f"焊点编号包含空格：{weld_number}"
        weld_point_type = cell_text(values[headers["焊点类型"]]).upper()
        if weld_point_type not in {"S", "F"}:
            error = error or f"焊点类型必须为S或F：{weld_point_type or '空'}"
        welds.append(
            ExcelWeld(
                row_number=row_number,
                unit=cell_text(values[headers["单元号"]]),
                pipeline=pipeline,
                weld_number=weld_number,
                segment_number=cell_text(values[headers["管段编号"]]),
                weld_point_type=weld_point_type,
                coordinate_text=coordinate_text,
                coordinate=coordinate,
                parse_error=error,
            )
        )
    return welds


def strip_eol(line: bytes) -> bytes:
    return line.rstrip(b"\r\n")


def display_bytes(value: bytes) -> str:
    if not value:
        return ""
    for encoding in ("ascii", "utf-8", "gb18030"):
        try:
            return value.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    return value.hex()


def strict_encoding(data: bytes) -> str:
    if all(byte < 128 for byte in data):
        data.decode("ascii", errors="strict")
        return "ascii"
    if data.startswith(b"\xef\xbb\xbf"):
        data.decode("utf-8-sig", errors="strict")
        return "utf-8-sig"
    try:
        data.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        data.decode("gb18030", errors="strict")
        return "gb18030"


def non_ascii_signature(data: bytes) -> bytes:
    return bytes(byte for byte in data if byte >= 0x80)


def parse_decimal_bytes(value: bytes) -> Decimal:
    return Decimal(value.decode("ascii", errors="strict"))


def parse_pcf_file(source_path: Path, input_root: Path) -> tuple[list[PcfWeld], str]:
    raw = source_path.read_bytes()
    encoding = strict_encoding(raw)
    pipeline_match = PIPELINE_RE.search(raw)
    if not pipeline_match:
        raise ValueError(f"PCF缺少PIPELINE-REFERENCE：{source_path}")
    pipeline = pipeline_match.group(1).strip().decode("ascii", errors="strict")
    relative_path = source_path.relative_to(input_root)
    unit = relative_path.parts[0] if len(relative_path.parts) > 1 else ""
    lines = raw.splitlines(keepends=True)
    welds: list[PcfWeld] = []

    index = 0
    while index < len(lines):
        body = strip_eol(lines[index])
        if body and body[:1] not in (b" ", b"\t") and body.strip().upper() == b"WELD":
            block_start = index
            attributes: dict[bytes, list[tuple[bytes, int]]] = defaultdict(list)
            endpoints: list[tuple[Decimal, Decimal, Decimal]] = []
            repeat_lines: list[tuple[int, re.Match[bytes]]] = []
            skey_lines: list[tuple[int, re.Match[bytes]]] = []
            category_lines: list[tuple[int, re.Match[bytes]]] = []
            index += 1
            while index < len(lines):
                child = lines[index]
                child_body = strip_eol(child)
                if child_body and child_body[:1] not in (b" ", b"\t"):
                    break
                endpoint_match = ENDPOINT_RE.match(child_body)
                if endpoint_match:
                    endpoints.append(tuple(parse_decimal_bytes(item) for item in endpoint_match.groups()))
                repeat_match = REPEAT_LINE_RE.match(child)
                if repeat_match:
                    repeat_lines.append((index, repeat_match))
                skey_match = SKEY_LINE_RE.match(child)
                if skey_match:
                    skey_lines.append((index, skey_match))
                category_match = CATEGORY_LINE_RE.match(child)
                if category_match:
                    category_lines.append((index, category_match))
                attribute_match = ATTRIBUTE_RE.match(child)
                if attribute_match:
                    attributes[attribute_match.group(1).upper()].append(
                        (attribute_match.group(2).strip(), index)
                    )
                index += 1

            if len(endpoints) != 2:
                raise ValueError(
                    f"WELD端点数量不是2：{relative_path}，块起始行{block_start + 1}"
                )
            if len(repeat_lines) != 1:
                raise ValueError(
                    f"REPEAT-WELD-IDENTIFIER数量不是1：{relative_path}，块起始行{block_start + 1}"
                )
            if len(skey_lines) != 1:
                raise ValueError(
                    f"SKEY数量不是1：{relative_path}，块起始行{block_start + 1}"
                )
            if len(category_lines) > 1:
                raise ValueError(
                    f"CATEGORY数量大于1：{relative_path}，块起始行{block_start + 1}"
                )
            attribute3_entries = attributes.get(b"WELD-ATTRIBUTE3", [])
            attribute4_entries = attributes.get(b"WELD-ATTRIBUTE4", [])
            if len(attribute3_entries) != 1 or len(attribute4_entries) != 1:
                raise ValueError(
                    f"WELD-ATTRIBUTE3/4数量不是1：{relative_path}，"
                    f"块起始行{block_start + 1}"
                )
            repeat_index, repeat_match = repeat_lines[0]
            skey_index, skey_match = skey_lines[0]
            category_index = category_lines[0][0] if category_lines else None
            category_match = category_lines[0][1] if category_lines else None
            attribute3_value, attribute3_index = attribute3_entries[0]
            attribute4_value, attribute4_index = attribute4_entries[0]

            def attribute_text(name: bytes) -> str:
                entries = attributes.get(name, [])
                return display_bytes(entries[0][0]) if entries else ""

            welds.append(
                PcfWeld(
                    source_path=source_path,
                    relative_path=relative_path,
                    unit=unit,
                    pipeline=pipeline,
                    block_start_line=block_start + 1,
                    repeat_line_index=repeat_index,
                    repeat_line_number=repeat_index + 1,
                    repeat_value_bytes=repeat_match.group(3),
                    repeat_value_text=display_bytes(repeat_match.group(3)),
                    skey_line_index=skey_index,
                    skey_line_number=skey_index + 1,
                    skey_value_bytes=skey_match.group(3),
                    endpoints=tuple(endpoints),
                    skey=attribute_text(b"SKEY"),
                    category_line_index=category_index,
                    category_line_number=category_index + 1 if category_index is not None else None,
                    category_value_bytes=category_match.group(3) if category_match else b"",
                    category=attribute_text(b"CATEGORY"),
                    attribute3_line_index=attribute3_index,
                    attribute3_line_number=attribute3_index + 1,
                    attribute3_value_bytes=attribute3_value,
                    attribute3=display_bytes(attribute3_value),
                    attribute4_line_index=attribute4_index,
                    attribute4_line_number=attribute4_index + 1,
                    attribute4_value_bytes=attribute4_value,
                    attribute4=display_bytes(attribute4_value),
                    component_identifier=attribute_text(b"COMPONENT-IDENTIFIER"),
                    uci=attribute_text(b"UCI"),
                )
            )
        else:
            index += 1
    return welds, encoding


def pcf_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pcf"
    )


def coordinate_distance(
    excel_coordinate: tuple[Decimal, Decimal, Decimal],
    endpoints: Iterable[tuple[Decimal, Decimal, Decimal]],
) -> tuple[Decimal, int]:
    distances = [
        max(abs(excel_coordinate[axis] - endpoint[axis]) for axis in range(3))
        for endpoint in endpoints
    ]
    minimum = min(distances)
    return minimum, distances.index(minimum)


def match_records(
    excel_welds: list[ExcelWeld],
    pcf_welds: list[PcfWeld],
    tolerance: Decimal,
    overwrite_existing: bool,
) -> tuple[list[AuditResult], dict[Path, dict[int, WeldWritePlan]], set[tuple[str, int]]]:
    results = [AuditResult(excel=record) for record in excel_welds]
    pcf_by_pipeline: dict[tuple[str, str], list[PcfWeld]] = defaultdict(list)
    for weld in pcf_welds:
        pcf_by_pipeline[(weld.unit, weld.pipeline)].append(weld)

    duplicate_coordinate_rows: set[int] = set()
    duplicate_number_rows: set[int] = set()
    coordinate_groups: dict[
        tuple[str, str, tuple[Decimal, Decimal, Decimal]], list[int]
    ] = defaultdict(list)
    number_groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(excel_welds):
        if record.coordinate is not None:
            coordinate_groups[(record.unit, record.pipeline, record.coordinate)].append(index)
        if record.weld_number:
            number_groups[(record.unit, record.pipeline, record.weld_number)].append(index)
    for indexes in coordinate_groups.values():
        if len(indexes) > 1:
            duplicate_coordinate_rows.update(indexes)
    for indexes in number_groups.values():
        if len(indexes) > 1:
            duplicate_number_rows.update(indexes)

    for index, result in enumerate(results):
        record = result.excel
        if record.parse_error:
            result.status = "Excel数据无效"
            result.reason = record.parse_error
            continue
        if index in duplicate_coordinate_rows:
            result.status = "Excel记录冲突"
            result.reason = "同一单元、管线和坐标在Excel中出现多次"
            continue
        if index in duplicate_number_rows:
            result.status = "Excel记录冲突"
            result.reason = "同一单元和管线内焊点编号重复"
            continue

        pipeline_welds = pcf_by_pipeline.get((record.unit, record.pipeline), [])
        if not pipeline_welds:
            result.status = "管线未找到"
            result.reason = "正式PCF中没有相同单元号和PIPELINE-REFERENCE"
            continue
        assert record.coordinate is not None
        for weld in pipeline_welds:
            distance, endpoint_index = coordinate_distance(record.coordinate, weld.endpoints)
            if distance <= tolerance:
                result.candidates.append(
                    Candidate(weld=weld, distance=distance, endpoint_index=endpoint_index)
                )
        result.candidates.sort(
            key=lambda item: (
                item.distance,
                item.weld.relative_path.as_posix(),
                item.weld.block_start_line,
            )
        )
        if not result.candidates:
            result.status = "坐标未匹配"
            result.reason = f"该管线内没有每轴差值不超过{tolerance} mm的WELD"
        elif len(result.candidates) > 1:
            result.status = "PCF候选不唯一"
            result.reason = f"坐标容差内存在{len(result.candidates)}个WELD块"
        else:
            result.selected = result.candidates[0]

    target_rows: dict[tuple[str, int], list[AuditResult]] = defaultdict(list)
    for result in results:
        if result.selected is not None:
            target_rows[result.selected.weld.key].append(result)
    for target_results in target_rows.values():
        if len(target_results) > 1:
            rows = ", ".join(str(item.excel.row_number) for item in target_results)
            for result in target_results:
                result.status = "Excel记录冲突"
                result.reason = f"多条Excel记录匹配同一个PCF焊缝：行{rows}"
                result.selected = None

    modifications: dict[Path, dict[int, WeldWritePlan]] = defaultdict(dict)
    matched_pcf_keys: set[tuple[str, int]] = set()
    for result in results:
        if result.selected is None:
            continue
        weld = result.selected.weld
        matched_pcf_keys.add(weld.key)
        old_value = weld.repeat_value_text.strip()
        new_value = result.excel.weld_number
        if old_value == new_value:
            result.status = "编号已一致"
            result.reason = "PCF已有焊口号与Excel一致，无需修改"
            result.written_number = new_value
        elif old_value not in ("", "0", "焊点未编号") and not overwrite_existing:
            result.status = "现有编号冲突"
            result.reason = f"PCF已有非占位编号{old_value}，默认不覆盖"
        else:
            result.status = "已写入"
            result.reason = "管线号和坐标唯一匹配"
            result.written_number = new_value
        if result.status in {"已写入", "编号已一致"}:
            new_skey, new_category = isogen_type_values(
                result.excel.weld_point_type, weld.skey
            )
            new_attribute3, new_attribute4 = isogen_responsibility_values(
                result.excel.weld_point_type
            )
            result.target_skey = new_skey
            result.target_category = new_category
            actions = []
            if weld.skey.upper() != new_skey:
                actions.append(f"SKEY {weld.skey or '空'}→{new_skey}")
            if weld.category.upper() != new_category:
                actions.append(
                    f"CATEGORY {weld.category or '缺失'}→{new_category}"
                )
            if weld.attribute3 != new_attribute3:
                actions.append(
                    f"WELD-ATTRIBUTE3 {weld.attribute3 or '空'}→{new_attribute3}"
                )
            if weld.attribute4 != new_attribute4:
                actions.append(
                    f"WELD-ATTRIBUTE4 {weld.attribute4 or '空'}→{new_attribute4}"
                )
            result.type_action = "；".join(actions) if actions else "类型字段已一致"
            file_changes = modifications[weld.relative_path]
            if weld.block_start_line in file_changes:
                raise ValueError(
                    f"内部错误：同一WELD被计划修改两次：{weld.relative_path}:{weld.block_start_line}"
                )
            file_changes[weld.block_start_line] = WeldWritePlan(
                weld=weld,
                new_number=new_value,
                new_skey=new_skey,
                new_category=new_category,
                new_attribute3=new_attribute3,
                new_attribute4=new_attribute4,
            )
    return results, modifications, matched_pcf_keys


def isogen_type_values(weld_point_type: str, original_skey: str) -> tuple[str, str]:
    """Map project S/F values to ISOGEN SKEY and CATEGORY values.

    Mitre-weld semantics are preserved: WM is the fabrication mitre symbol and
    WMS is the erection mitre symbol.  The Blue Star source contains only the
    ordinary WW/WS and mitre WM/WMS families.
    """
    normalized_type = weld_point_type.strip().upper()
    normalized_skey = original_skey.strip().upper()
    is_mitre = normalized_skey.startswith("WM")
    if normalized_type == "S":
        return ("WM" if is_mitre else "WW", "FABRICATION")
    if normalized_type == "F":
        return ("WMS" if is_mitre else "WS", "ERECTION")
    raise ValueError(f"不支持的焊点类型：{weld_point_type}")


def isogen_responsibility_values(weld_point_type: str) -> tuple[str, str]:
    normalized_type = weld_point_type.strip().upper()
    if normalized_type == "S":
        return "Shop weld", "By Pipe Fabricator"
    if normalized_type == "F":
        return "Field weld at job site, including offshore", "By Pipe Erector"
    raise ValueError(f"不支持的焊点类型：{weld_point_type}")


def replace_repeat_value(line: bytes, new_value: str) -> bytes:
    match = REPEAT_LINE_RE.match(line)
    if not match:
        raise ValueError("目标行不再是REPEAT-WELD-IDENTIFIER")
    encoded = new_value.encode("ascii", errors="strict")
    return match.group(1) + match.group(2) + encoded + match.group(4) + (match.group(5) or b"")


def replace_keyword_value(
    line: bytes, pattern: re.Pattern[bytes], new_value: str, field_name: str
) -> bytes:
    match = pattern.match(line)
    if not match:
        raise ValueError(f"目标行不再是{field_name}")
    encoded = new_value.encode("ascii", errors="strict")
    return match.group(1) + match.group(2) + encoded + match.group(4) + (match.group(5) or b"")


def inserted_category_line(skey_line: bytes, category: str) -> bytes:
    match = SKEY_LINE_RE.match(skey_line)
    if not match:
        raise ValueError("无法从SKEY行确定CATEGORY缩进格式")
    indent = match.group(1)[: -len(b"SKEY")]
    separator = match.group(2)
    eol = match.group(5) or b"\n"
    return indent + b"CATEGORY" + separator + category.encode("ascii") + eol


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_modified_pcfs(
    input_root: Path,
    temp_output: Path,
    modifications: dict[Path, dict[int, WeldWritePlan]],
    encodings: dict[Path, str],
) -> dict[str, int]:
    changed_files = 0
    changed_number_lines = 0
    changed_skey_lines = 0
    replaced_category_lines = 0
    inserted_category_lines = 0
    changed_attribute3_lines = 0
    changed_attribute4_lines = 0
    for relative_path, file_changes in modifications.items():
        source_path = input_root / relative_path
        output_path = temp_output / relative_path
        source_raw = source_path.read_bytes()
        source_hash = sha256_bytes(source_raw)
        source_non_ascii = non_ascii_signature(source_raw)
        original_lines = source_raw.splitlines(keepends=True)
        replacements: dict[int, bytes] = {}
        insertions: dict[int, list[bytes]] = defaultdict(list)
        file_changed = False
        for plan in sorted(file_changes.values(), key=lambda item: item.weld.block_start_line):
            weld = plan.weld
            for line_index in (
                weld.repeat_line_index,
                weld.skey_line_index,
                weld.attribute3_line_index,
                weld.attribute4_line_index,
            ):
                if line_index >= len(original_lines):
                    raise ValueError(f"修改行超出文件范围：{relative_path}:{line_index + 1}")
            repeat_match = REPEAT_LINE_RE.match(original_lines[weld.repeat_line_index])
            skey_match = SKEY_LINE_RE.match(original_lines[weld.skey_line_index])
            if not repeat_match or repeat_match.group(3) != weld.repeat_value_bytes:
                raise ValueError(
                    f"PCF焊口号与解析计划不一致：{relative_path}:{weld.repeat_line_number}"
                )
            if not skey_match or skey_match.group(3) != weld.skey_value_bytes:
                raise ValueError(
                    f"PCF SKEY与解析计划不一致：{relative_path}:{weld.skey_line_number}"
                )
            attribute3_match = WELD_ATTRIBUTE3_LINE_RE.match(
                original_lines[weld.attribute3_line_index]
            )
            attribute4_match = WELD_ATTRIBUTE4_LINE_RE.match(
                original_lines[weld.attribute4_line_index]
            )
            if (
                not attribute3_match
                or attribute3_match.group(3).strip() != weld.attribute3_value_bytes
            ):
                raise ValueError(
                    f"PCF WELD-ATTRIBUTE3与解析计划不一致："
                    f"{relative_path}:{weld.attribute3_line_number}"
                )
            if (
                not attribute4_match
                or attribute4_match.group(3).strip() != weld.attribute4_value_bytes
            ):
                raise ValueError(
                    f"PCF WELD-ATTRIBUTE4与解析计划不一致："
                    f"{relative_path}:{weld.attribute4_line_number}"
                )

            new_repeat = replace_repeat_value(
                original_lines[weld.repeat_line_index], plan.new_number
            )
            if new_repeat != original_lines[weld.repeat_line_index]:
                replacements[weld.repeat_line_index] = new_repeat
                changed_number_lines += 1
                file_changed = True

            new_skey_line = replace_keyword_value(
                original_lines[weld.skey_line_index], SKEY_LINE_RE, plan.new_skey, "SKEY"
            )
            if new_skey_line != original_lines[weld.skey_line_index]:
                replacements[weld.skey_line_index] = new_skey_line
                changed_skey_lines += 1
                file_changed = True

            if weld.category_line_index is None:
                insertions[weld.skey_line_index].append(
                    inserted_category_line(original_lines[weld.skey_line_index], plan.new_category)
                )
                inserted_category_lines += 1
                file_changed = True
            else:
                if weld.category_line_index >= len(original_lines):
                    raise ValueError(
                        f"CATEGORY行超出文件范围：{relative_path}:{weld.category_line_number}"
                    )
                category_match = CATEGORY_LINE_RE.match(
                    original_lines[weld.category_line_index]
                )
                if not category_match or category_match.group(3) != weld.category_value_bytes:
                    raise ValueError(
                        f"PCF CATEGORY与解析计划不一致：{relative_path}:{weld.category_line_number}"
                    )
                new_category_line = replace_keyword_value(
                    original_lines[weld.category_line_index],
                    CATEGORY_LINE_RE,
                    plan.new_category,
                    "CATEGORY",
                )
                if new_category_line != original_lines[weld.category_line_index]:
                    replacements[weld.category_line_index] = new_category_line
                    replaced_category_lines += 1
                    file_changed = True

            new_attribute3_line = replace_keyword_value(
                original_lines[weld.attribute3_line_index],
                WELD_ATTRIBUTE3_LINE_RE,
                plan.new_attribute3,
                "WELD-ATTRIBUTE3",
            )
            if new_attribute3_line != original_lines[weld.attribute3_line_index]:
                replacements[weld.attribute3_line_index] = new_attribute3_line
                changed_attribute3_lines += 1
                file_changed = True

            new_attribute4_line = replace_keyword_value(
                original_lines[weld.attribute4_line_index],
                WELD_ATTRIBUTE4_LINE_RE,
                plan.new_attribute4,
                "WELD-ATTRIBUTE4",
            )
            if new_attribute4_line != original_lines[weld.attribute4_line_index]:
                replacements[weld.attribute4_line_index] = new_attribute4_line
                changed_attribute4_lines += 1
                file_changed = True

        output_lines: list[bytes] = []
        for line_index, original_line in enumerate(original_lines):
            output_lines.append(replacements.get(line_index, original_line))
            output_lines.extend(insertions.get(line_index, []))
        output_raw = b"".join(output_lines)
        if source_path.read_bytes() != source_raw or sha256_bytes(source_raw) != source_hash:
            raise ValueError(f"源PCF在处理期间发生变化：{relative_path}")
        output_raw.decode(encodings[relative_path], errors="strict")
        if non_ascii_signature(output_raw) != source_non_ascii:
            raise ValueError(f"非ASCII字节发生变化，已拒绝输出：{relative_path}")

        output_path.write_bytes(output_raw)
        if file_changed:
            changed_files += 1
    return {
        "changed_files": changed_files,
        "changed_number_lines": changed_number_lines,
        "changed_skey_lines": changed_skey_lines,
        "replaced_category_lines": replaced_category_lines,
        "inserted_category_lines": inserted_category_lines,
        "changed_attribute3_lines": changed_attribute3_lines,
        "changed_attribute4_lines": changed_attribute4_lines,
    }


def endpoint_text(endpoint: tuple[Decimal, Decimal, Decimal]) -> str:
    return ",".join(format(value, "f") for value in endpoint)


def candidate_text(candidates: list[Candidate]) -> str:
    return "; ".join(
        f"{item.weld.relative_path.as_posix()}:{item.weld.block_start_line}"
        f"@{endpoint_text(item.weld.endpoints[item.endpoint_index])}"
        f"(差{format(item.distance, 'f')})"
        for item in candidates
    )


def style_worksheet(worksheet, widths: dict[int, float] | None = None) -> None:
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
        cell.fill = fill
    if widths:
        for index, width in widths.items():
            worksheet.column_dimensions[get_column_letter(index)].width = width


def write_audit(
    path: Path,
    excel_path: Path,
    input_root: Path,
    output_root: Path,
    tolerance: Decimal,
    results: list[AuditResult],
    all_pcf_welds: list[PcfWeld],
    matched_pcf_keys: set[tuple[str, int]],
    write_stats: dict[str, int],
    augmentation_decisions: list[AugmentDecision],
    augmentation_stats: dict[str, int],
) -> dict[str, object]:
    status_counts = Counter(result.status for result in results)
    summary: dict[str, object] = {
        "Excel数据行": len(results),
        "PCF文件": len(pcf_files(input_root)),
        "含WELD块的PCF文件": len({weld.relative_path for weld in all_pcf_welds}),
        "PCF焊缝块": len(all_pcf_welds),
        "已写入": status_counts["已写入"],
        "编号已一致": status_counts["编号已一致"],
        "未处理Excel行": len(results) - status_counts["已写入"] - status_counts["编号已一致"],
        "修改PCF文件": write_stats["changed_files"],
        "修改焊口号字段": write_stats["changed_number_lines"],
        "修改SKEY字段": write_stats["changed_skey_lines"],
        "替换CATEGORY字段": write_stats["replaced_category_lines"],
        "新增CATEGORY字段": write_stats["inserted_category_lines"],
        "修改WELD-ATTRIBUTE3字段": write_stats["changed_attribute3_lines"],
        "修改WELD-ATTRIBUTE4字段": write_stats["changed_attribute4_lines"],
        "补建焊缝PCF文件": augmentation_stats["augmented_files"],
        "新增组件端点WELD": augmentation_stats["created_endpoint_welds"],
        "拆分PIPE新增WELD": augmentation_stats["created_pipe_split_welds"],
        "补建焊缝未解决": augmentation_stats["augmentation_unresolved"],
        "PCF未匹配焊缝": len(all_pcf_welds) - len(matched_pcf_keys),
    }

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "汇总"
    summary_sheet.append(["项目", "值"])
    summary_sheet.append(["Excel", str(excel_path)])
    summary_sheet.append(["正式PCF目录", str(input_root)])
    summary_sheet.append(["输出副本目录", str(output_root)])
    summary_sheet.append(["坐标容差(mm，每轴)", format(tolerance, "f")])
    for name, value in summary.items():
        summary_sheet.append([name, value])
    summary_sheet.append([])
    summary_sheet.append(["Excel处理状态", "数量"])
    for status, count in sorted(status_counts.items()):
        summary_sheet.append([status, count])
    summary_sheet.column_dimensions["A"].width = 28
    summary_sheet.column_dimensions["B"].width = 90
    summary_sheet["A1"].font = Font(bold=True)
    summary_sheet["B1"].font = Font(bold=True)

    detail_sheet = workbook.create_sheet("Excel匹配明细")
    detail_headers = [
        "Excel行",
        "单元号",
        "管线号",
        "焊点编号",
        "管段编号",
        "焊点类型",
        "Excel焊点位置",
        "状态",
        "原因",
        "PCF相对路径",
        "WELD块起始行",
        "REPEAT字段行",
        "PCF匹配端点",
        "最大坐标差(mm)",
        "PCF原焊口号",
        "写入焊口号",
        "PCF原SKEY",
        "目标SKEY",
        "PCF原CATEGORY",
        "目标CATEGORY",
        "类型字段处理",
        "候选数",
        "候选详情",
    ]
    detail_sheet.append(detail_headers)
    for result in results:
        selected = result.selected
        weld = selected.weld if selected else None
        detail_sheet.append(
            [
                result.excel.row_number,
                result.excel.unit,
                result.excel.pipeline,
                result.excel.weld_number,
                result.excel.segment_number,
                result.excel.weld_point_type,
                result.excel.coordinate_text,
                result.status,
                result.reason,
                weld.relative_path.as_posix() if weld else "",
                weld.block_start_line if weld else "",
                weld.repeat_line_number if weld else "",
                endpoint_text(weld.endpoints[selected.endpoint_index]) if selected and weld else "",
                format(selected.distance, "f") if selected else "",
                weld.repeat_value_text if weld else "",
                result.written_number,
                weld.skey if weld else "",
                result.target_skey,
                weld.category if weld else "",
                result.target_category,
                result.type_action,
                len(result.candidates),
                candidate_text(result.candidates),
            ]
        )
    style_worksheet(
        detail_sheet,
        {
            1: 10,
            2: 10,
            3: 34,
            4: 12,
            7: 34,
            8: 18,
            9: 45,
            10: 55,
            13: 34,
            20: 18,
            21: 28,
            23: 90,
        },
    )

    unmatched_sheet = workbook.create_sheet("PCF未匹配焊缝")
    unmatched_sheet.append(
        [
            "单元号",
            "管线号",
            "PCF相对路径",
            "WELD块起始行",
            "REPEAT字段行",
            "端点1",
            "端点2",
            "SKEY",
            "CATEGORY",
            "组件编号",
            "UCI",
            "原焊口号",
        ]
    )
    for weld in all_pcf_welds:
        if weld.key in matched_pcf_keys:
            continue
        unmatched_sheet.append(
            [
                weld.unit,
                weld.pipeline,
                weld.relative_path.as_posix(),
                weld.block_start_line,
                weld.repeat_line_number,
                endpoint_text(weld.endpoints[0]),
                endpoint_text(weld.endpoints[1]),
                weld.skey,
                weld.category,
                weld.component_identifier,
                weld.uci,
                weld.repeat_value_text,
            ]
        )
    style_worksheet(
        unmatched_sheet,
        {1: 10, 2: 34, 3: 55, 6: 34, 7: 34, 11: 42, 12: 14},
    )

    augmentation_sheet = workbook.create_sheet("PCF补建焊缝")
    augmentation_sheet.append(
        [
            "Excel行",
            "单元号",
            "管线号",
            "焊点编号",
            "焊点类型",
            "请求坐标",
            "处理",
            "原因",
            "PCF相对路径",
            "所属组件类型",
            "所属组件编号",
            "所属组件UCI",
            "实际插入坐标",
            "管径",
            "原组件比例",
            "新增WELD UCI",
        ]
    )
    for decision in augmentation_decisions:
        augmentation_sheet.append(
            [
                decision.excel_row,
                decision.unit,
                decision.pipeline,
                decision.weld_number,
                decision.weld_type,
                endpoint_text(decision.requested_coordinate),
                decision.action,
                decision.reason,
                decision.relative_path,
                decision.owner_kind,
                decision.owner_component_identifier,
                decision.owner_uci,
                endpoint_text(decision.inserted_coordinate)
                if decision.inserted_coordinate
                else "",
                format(decision.inserted_bore, "f")
                if decision.inserted_bore is not None
                else "",
                format(decision.source_fraction, "f")
                if decision.source_fraction is not None
                else "",
                decision.new_weld_uci,
            ]
        )
    style_worksheet(
        augmentation_sheet,
        {1: 10, 2: 10, 3: 34, 6: 34, 8: 48, 9: 55, 12: 44, 13: 34, 16: 44},
    )
    workbook.save(path)
    return summary


def validate_copy(
    input_root: Path,
    output_root: Path,
    modifications: dict[Path, dict[int, WeldWritePlan]],
) -> None:
    input_pcfs = pcf_files(input_root)
    output_pcfs = pcf_files(output_root)
    input_rel = {path.relative_to(input_root) for path in input_pcfs}
    output_rel = {path.relative_to(output_root) for path in output_pcfs}
    if input_rel != output_rel:
        raise ValueError("输入和输出PCF文件清单不一致")

    for relative_path in input_rel:
        source_raw = (input_root / relative_path).read_bytes()
        output_raw = (output_root / relative_path).read_bytes()
        if relative_path not in modifications and source_raw != output_raw:
            raise ValueError(f"无需修改的PCF不再字节一致：{relative_path}")

        if relative_path in modifications:
            source_welds, _ = parse_pcf_file(input_root / relative_path, input_root)
            output_welds, _ = parse_pcf_file(output_root / relative_path, output_root)
            if len(source_welds) != len(output_welds):
                raise ValueError(f"修改后WELD数量发生变化：{relative_path}")
            plans = modifications[relative_path]
            for source_weld, output_weld in zip(source_welds, output_welds):
                if source_weld.endpoints != output_weld.endpoints:
                    raise ValueError(f"修改后焊点坐标发生变化：{relative_path}")
                if source_weld.component_identifier != output_weld.component_identifier:
                    raise ValueError(f"修改后组件编号发生变化：{relative_path}")
                plan = plans.get(source_weld.block_start_line)
                if plan is None:
                    if (
                        source_weld.repeat_value_text != output_weld.repeat_value_text
                        or source_weld.skey != output_weld.skey
                        or source_weld.category != output_weld.category
                        or source_weld.attribute3 != output_weld.attribute3
                        or source_weld.attribute4 != output_weld.attribute4
                    ):
                        raise ValueError(f"未计划焊缝字段发生变化：{relative_path}")
                    continue
                if output_weld.repeat_value_text != plan.new_number:
                    raise ValueError(f"焊口号写入验证失败：{relative_path}")
                if output_weld.skey.upper() != plan.new_skey:
                    raise ValueError(f"SKEY写入验证失败：{relative_path}")
                if output_weld.category.upper() != plan.new_category:
                    raise ValueError(f"CATEGORY写入验证失败：{relative_path}")
                if output_weld.attribute3 != plan.new_attribute3:
                    raise ValueError(f"WELD-ATTRIBUTE3写入验证失败：{relative_path}")
                if output_weld.attribute4 != plan.new_attribute4:
                    raise ValueError(f"WELD-ATTRIBUTE4写入验证失败：{relative_path}")

    for source_path in input_root.rglob("*"):
        if not source_path.is_file() or source_path.suffix.lower() == ".pcf":
            continue
        relative_path = source_path.relative_to(input_root)
        output_path = output_root / relative_path
        if not output_path.is_file() or source_path.read_bytes() != output_path.read_bytes():
            raise ValueError(f"非PCF文件未被原样复制：{relative_path}")


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def publish_output_directory(
    prepared: Path,
    output_root: Path,
    allow_replace: bool,
) -> None:
    if not output_root.exists():
        prepared.rename(output_root)
        return
    if not allow_replace:
        raise FileExistsError(f"输出目录已存在，为避免覆盖已停止：{output_root}")
    if output_root.parent.resolve() != prepared.parent.resolve():
        raise ValueError("正式输出与临时输出不在同一父目录")
    backup = output_root.parent / f".{output_root.name}.backup-{uuid.uuid4().hex[:8]}"
    if backup.exists():
        raise FileExistsError(f"输出备份目录意外存在：{backup}")
    output_root.rename(backup)
    try:
        prepared.rename(output_root)
    except Exception:
        backup.rename(output_root)
        raise
    shutil.rmtree(backup)


def main() -> int:
    args = parse_args()
    excel_path = args.excel.resolve()
    input_root = args.input_dir.resolve()
    output_root = args.output_dir.resolve()
    tolerance: Decimal = args.tolerance_mm

    if tolerance < 0:
        raise ValueError("坐标容差不能为负数")
    if not excel_path.is_file():
        raise FileNotFoundError(f"Excel不存在：{excel_path}")
    if not input_root.is_dir():
        raise FileNotFoundError(f"正式PCF目录不存在：{input_root}")
    if output_root == input_root or is_relative_to(output_root, input_root):
        raise ValueError("输出目录不能等于或位于正式PCF目录内部")
    allow_replace = args.overwrite_output or output_root == DEFAULT_OUTPUT.resolve()
    if output_root.exists() and not allow_replace:
        raise FileExistsError(f"输出目录已存在，为避免覆盖已停止：{output_root}")

    excel_welds = read_excel_welds(excel_path)
    source_pcfs = pcf_files(input_root)
    if not source_pcfs:
        raise ValueError(f"正式PCF目录中没有PCF文件：{input_root}")

    temp_augmented = output_root.parent / (
        f".{output_root.name}.augment-{uuid.uuid4().hex[:8]}"
    )
    temp_output = output_root.parent / f".{output_root.name}.tmp-{uuid.uuid4().hex[:8]}"
    for temporary in (temp_augmented, temp_output):
        if temporary.exists():
            raise FileExistsError(f"临时目录意外存在：{temporary}")

    summary: dict[str, object]
    try:
        shutil.copytree(input_root, temp_augmented)
        augmentation_decisions, augmentation_stats = augment_pcf_tree(
            excel_welds,
            input_root,
            temp_augmented,
            tolerance,
        )
        validate_augmented_tree(
            input_root,
            temp_augmented,
            augmentation_decisions,
        )

        all_pcf_welds: list[PcfWeld] = []
        encodings: dict[Path, str] = {}
        for augmented_path in pcf_files(temp_augmented):
            welds, encoding = parse_pcf_file(augmented_path, temp_augmented)
            relative_path = augmented_path.relative_to(temp_augmented)
            all_pcf_welds.extend(welds)
            encodings[relative_path] = encoding

        results, modifications, matched_pcf_keys = match_records(
            excel_welds,
            all_pcf_welds,
            tolerance,
            args.overwrite_existing_number,
        )

        shutil.copytree(temp_augmented, temp_output)
        write_stats = write_modified_pcfs(
            temp_augmented, temp_output, modifications, encodings
        )
        audit_path = temp_output / DEFAULT_AUDIT_NAME
        summary = write_audit(
            audit_path,
            excel_path,
            input_root,
            output_root,
            tolerance,
            results,
            all_pcf_welds,
            matched_pcf_keys,
            write_stats,
            augmentation_decisions,
            augmentation_stats,
        )
        validate_copy(temp_augmented, temp_output, modifications)
        publish_output_directory(temp_output, output_root, allow_replace)
    except Exception:
        for temporary in (temp_output, temp_augmented):
            if (
                temporary.exists()
                and temporary.parent.resolve() == output_root.parent.resolve()
                and temporary.name.startswith(f".{output_root.name}.")
            ):
                shutil.rmtree(temporary)
        raise
    finally:
        if (
            temp_augmented.exists()
            and temp_augmented.parent.resolve() == output_root.parent.resolve()
            and temp_augmented.name.startswith(f".{output_root.name}.augment-")
        ):
            shutil.rmtree(temp_augmented)

    report = {
        **summary,
        "输出目录": str(output_root),
        "审计文件": str(output_root / DEFAULT_AUDIT_NAME),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)
