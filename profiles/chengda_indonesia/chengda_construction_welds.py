#!/usr/bin/env python3
"""Apply Chengda construction weld numbers to PCF/SHA topology mappings."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook


@dataclass(frozen=True)
class ConstructionWeldInput:
    excel_row: int
    sheet_id: str
    weld_number: str
    weld_type: str
    coordinate: tuple[Decimal, Decimal, Decimal]
    original_weld_number: str = ""
    material_uci_a: str = ""
    material_uci_b: str = ""
    number_source: str = "施工焊口号"


@dataclass(frozen=True)
class SkippedConstructionWeld:
    excel_row: int
    sheet_id: str
    weld_number: str
    reason: str


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _coordinate(value: object, excel_row: int) -> tuple[Decimal, Decimal, Decimal]:
    raw = _text(value).replace("，", ",")
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Excel第{excel_row}行焊点位置不是三个逗号分隔的数值：{raw}")
    try:
        return tuple(Decimal(part) for part in parts)  # type: ignore[return-value]
    except InvalidOperation as exc:
        raise ValueError(f"Excel第{excel_row}行焊点位置包含非数值内容：{raw}") from exc


def read_construction_weld_inputs(
    path: Path,
    number_column: str = "施工焊口号",
) -> tuple[list[ConstructionWeldInput], list[SkippedConstructionWeld]]:
    """Select one numbering system for every sheet.

    Rows without an original weld number are ignored first.  Of the remaining
    rows, a sheet containing any construction number uses construction numbers
    exclusively; otherwise the whole sheet falls back to original weld numbers.
    """

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        try:
            headers = [_text(value) for value in next(rows)]
        except StopIteration as exc:
            raise ValueError(f"焊口表为空：{path}") from exc
        required = ("单页图号", "焊点编号", number_column, "焊点类型", "焊点位置")
        missing = [name for name in required if name not in headers]
        if missing:
            raise ValueError(f"焊口表缺少必需列：{', '.join(missing)}")
        index = {name: headers.index(name) for name in headers if name}

        def value(row: tuple[object, ...], name: str) -> str:
            column = index.get(name)
            return _text(row[column]) if column is not None and column < len(row) else ""

        candidate_rows: list[dict[str, object]] = []
        skipped: list[SkippedConstructionWeld] = []
        for excel_row, row in enumerate(rows, start=2):
            sheet_id = value(row, "单页图号")
            original_number = value(row, "焊点编号")
            construction_number = value(row, number_column)
            if not original_number:
                if construction_number:
                    skipped.append(
                        SkippedConstructionWeld(
                            excel_row,
                            sheet_id,
                            construction_number,
                            "焊点编号为空，按规则忽略",
                        )
                    )
                continue
            weld_type = value(row, "焊点类型").upper()
            position = value(row, "焊点位置")
            if not sheet_id:
                raise ValueError(f"Excel第{excel_row}行焊点编号不为空但单页图号为空")
            if not position:
                raise ValueError(f"Excel第{excel_row}行焊点编号不为空但焊点位置为空")
            if weld_type not in {"S", "F"}:
                raise ValueError(f"Excel第{excel_row}行焊点类型不是S或F：{weld_type}")
            candidate_rows.append(
                {
                    "excel_row": excel_row,
                    "sheet_id": sheet_id,
                    "original_number": original_number,
                    "construction_number": construction_number,
                    "weld_type": weld_type,
                    "coordinate": _coordinate(position, excel_row),
                    "material_uci_a": value(row, "材料唯一码1"),
                    "material_uci_b": value(row, "材料唯一码2"),
                }
            )
    finally:
        workbook.close()

    construction_sheets = {
        str(row["sheet_id"]).casefold()
        for row in candidate_rows
        if row["construction_number"]
    }
    records: list[ConstructionWeldInput] = []
    for row in candidate_rows:
        uses_construction = str(row["sheet_id"]).casefold() in construction_sheets
        if uses_construction and not row["construction_number"]:
            continue
        selected_number = (
            str(row["construction_number"])
            if uses_construction
            else str(row["original_number"])
        )
        records.append(
            ConstructionWeldInput(
                excel_row=int(row["excel_row"]),
                sheet_id=str(row["sheet_id"]),
                weld_number=selected_number,
                weld_type=str(row["weld_type"]),
                coordinate=row["coordinate"],  # type: ignore[arg-type]
                original_weld_number=str(row["original_number"]),
                material_uci_a=str(row["material_uci_a"]),
                material_uci_b=str(row["material_uci_b"]),
                number_source=(number_column if uses_construction else "焊点编号"),
            )
        )
    return records, skipped


def group_construction_by_sheet(
    records: Iterable[ConstructionWeldInput],
) -> dict[str, list[ConstructionWeldInput]]:
    grouped: dict[str, list[ConstructionWeldInput]] = defaultdict(list)
    for record in records:
        grouped[record.sheet_id.casefold()].append(record)
    return dict(grouped)


def construction_records_for_sheet(
    grouped: dict[str, list[ConstructionWeldInput]], sheet_id: str
) -> list[ConstructionWeldInput]:
    """Accept either the full ISO number or the table's abbreviated suffix."""

    wanted = sheet_id.casefold()
    if wanted in grouped:
        return grouped[wanted]
    matches = [rows for key, rows in grouped.items() if wanted.endswith("-" + key)]
    if len(matches) > 1:
        raise ValueError(f"{sheet_id}匹配到多个焊口表单页图号")
    return matches[0] if matches else []


def apply_construction_welds(
    payload: dict[str, object],
    records: list[ConstructionWeldInput],
    tolerance: float = 0.10,
    coordinate_offset_tolerance: float = 1.0,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Clone mapped PCF/SHA rows and replace their number with construction IDs."""

    base_rows = list(payload.get("welds", []))
    numbered_rows: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    used_targets: set[tuple[object, object, object]] = set()
    for record in records:
        candidates: list[tuple[float, dict[str, object]]] = []
        expected = tuple(float(value) for value in record.coordinate)
        for raw in base_rows:
            row = dict(raw)
            endpoint = row.get("pcf_endpoint")
            if not isinstance(endpoint, list) or len(endpoint) < 3:
                continue
            try:
                actual = tuple(float(endpoint[index]) for index in range(3))
            except (TypeError, ValueError):
                continue
            deltas = tuple(abs(actual[index] - expected[index]) for index in range(3))
            if max(deltas) <= tolerance:
                candidates.append((sum(delta * delta for delta in deltas), row))
        mapped = [item for item in candidates if item[1].get("graphic_ref") is not None]
        pool = mapped or candidates
        mapping_suffix = "construction-coordinate"
        if not pool:
            # EP3D exports occasionally differ from PCF coordinates by a
            # sub-millimetre rounding/precision offset.  Accept that only
            # when exactly one PCF WELD lies inside the wider envelope; never
            # pick the merely-nearest item from multiple engineering targets.
            offset_candidates: list[tuple[float, dict[str, object]]] = []
            for raw in base_rows:
                row = dict(raw)
                endpoint = row.get("pcf_endpoint")
                if not isinstance(endpoint, list) or len(endpoint) < 3:
                    continue
                try:
                    actual = tuple(float(endpoint[index]) for index in range(3))
                except (TypeError, ValueError):
                    continue
                deltas = tuple(abs(actual[index] - expected[index]) for index in range(3))
                if max(deltas) <= coordinate_offset_tolerance:
                    offset_candidates.append(
                        (sum(delta * delta for delta in deltas), row)
                    )
            unique_targets = {
                (row.get("uci"), tuple(row.get("pcf_endpoint", [])[:3]))
                for _, row in offset_candidates
            }
            if len(unique_targets) == 1:
                pool = offset_candidates
                mapping_suffix = "construction-coordinate-unique-offset<=1mm"
        if not pool:
            unresolved.append(
                {
                    "excel_row": record.excel_row,
                    "sheet_id": record.sheet_id,
                    "weld_number": record.weld_number,
                    "coordinate": [str(value) for value in record.coordinate],
                    "reason": "PCF焊口块中没有同坐标焊点",
                }
            )
            continue
        _, selected = min(pool, key=lambda item: item[0])
        target_key = (selected.get("page"), selected.get("graphic_ref"), selected.get("uci"))
        if target_key in used_targets:
            unresolved.append(
                {
                    "excel_row": record.excel_row,
                    "sheet_id": record.sheet_id,
                    "weld_number": record.weld_number,
                    "coordinate": [str(value) for value in record.coordinate],
                    "reason": "同一SHA焊点已分配其他焊口号，按唯一性规则忽略",
                }
            )
            continue
        used_targets.add(target_key)
        selected.update(
            {
                "weld_number": record.weld_number,
                "weld_type": record.weld_type,
                "construction_excel_row": record.excel_row,
                "construction_sheet_id": record.sheet_id,
                "original_weld_number": record.original_weld_number,
                "number_source": record.number_source,
                "mapping_method": f'{selected.get("mapping_method", "")};{mapping_suffix}'.strip(";"),
                "confidence_status": "mapped" if selected.get("graphic_ref") is not None else "unmapped",
            }
        )
        numbered_rows.append(selected)

    callouts = [row for row in numbered_rows if row.get("confidence_status") == "mapped"]
    unmapped_count = len(numbered_rows) - len(callouts) + len(unresolved)
    output = dict(payload)
    output["number_source"] = "Chengda weld table 单页优先施工焊口号，否则焊点编号"
    # Retain the unnumbered topology rows for valve/flange/support recovery.
    # The weld injector filters on weld_number, so these do not create labels.
    output["welds"] = base_rows + numbered_rows
    output["construction_unresolved"] = unresolved
    output["summary"] = {
        "numbered_weld_count": len(records),
        "callout_target_count": len(callouts),
        "unmapped_numbered_count": unmapped_count,
        "ambiguous_count": 0,
    }
    return output, unresolved
