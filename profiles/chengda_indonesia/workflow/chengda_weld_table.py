#!/usr/bin/env python3
"""Read Chengda weld-table rows without changing their weld identifiers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from openpyxl import load_workbook


@dataclass(frozen=True)
class WeldInput:
    excel_row: int
    sheet_id: str
    weld_number: str
    weld_type: str
    coordinate: tuple[Decimal, Decimal, Decimal]
    material_uci_a: str = ""
    material_uci_b: str = ""
    component_coordinate_a: str = ""
    component_coordinate_b: str = ""


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


def parse_coordinate(value: object, excel_row: int) -> tuple[Decimal, Decimal, Decimal]:
    text = cell_text(value)
    pieces = [piece.strip() for piece in text.split(",")]
    if len(pieces) != 3:
        raise ValueError(f"Excel第{excel_row}行焊点位置不是三个逗号分隔的数值：{text}")
    try:
        coordinate = tuple(Decimal(piece) for piece in pieces)
    except InvalidOperation as exc:
        raise ValueError(f"Excel第{excel_row}行焊点位置包含非数值内容：{text}") from exc
    return coordinate  # type: ignore[return-value]


def reject_indistinguishable_duplicates(records: list[WeldInput]) -> None:
    grouped: dict[tuple[object, ...], list[WeldInput]] = defaultdict(list)
    for record in records:
        key = (
            record.sheet_id.casefold(),
            record.coordinate,
            record.material_uci_a.casefold(),
            record.material_uci_b.casefold(),
        )
        grouped[key].append(record)
    conflicts = [
        rows
        for rows in grouped.values()
        if len(rows) > 1 and len({row.weld_number for row in rows}) > 1
    ]
    if conflicts:
        detail = "; ".join(
            f"{rows[0].sheet_id} 坐标{','.join(str(v) for v in rows[0].coordinate)} "
            f"行{','.join(str(row.excel_row) for row in rows)}"
            for rows in conflicts
        )
        raise ValueError(f"存在无法区分的重复焊口：{detail}")


def read_weld_inputs(path: Path, number_column: str = "焊点编号") -> list[WeldInput]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        try:
            raw_headers = next(rows)
        except StopIteration as exc:
            raise ValueError(f"焊口表为空：{path}") from exc
        headers = [cell_text(value) for value in raw_headers]
        required = ["单页图号", number_column, "焊点类型", "焊点位置"]
        missing = [name for name in required if name not in headers]
        if missing:
            raise ValueError(f"焊口表缺少必需列：{', '.join(missing)}")
        index = {name: headers.index(name) for name in headers if name}

        def optional(row: tuple[object, ...], name: str) -> str:
            column = index.get(name)
            return cell_text(row[column]) if column is not None and column < len(row) else ""

        records: list[WeldInput] = []
        for excel_row, row in enumerate(rows, start=2):
            if not any(cell_text(value) for value in row):
                continue
            sheet_id = optional(row, "单页图号")
            weld_number = optional(row, number_column)
            weld_type = optional(row, "焊点类型").upper()
            if not sheet_id:
                raise ValueError(f"Excel第{excel_row}行单页图号为空")
            if not weld_number:
                raise ValueError(f"Excel第{excel_row}行{number_column}为空")
            if weld_type not in {"S", "F"}:
                raise ValueError(f"Excel第{excel_row}行焊点类型不是S或F：{weld_type}")
            records.append(
                WeldInput(
                    excel_row=excel_row,
                    sheet_id=sheet_id,
                    weld_number=weld_number,
                    weld_type=weld_type,
                    coordinate=parse_coordinate(optional(row, "焊点位置"), excel_row),
                    material_uci_a=optional(row, "材料唯一码1"),
                    material_uci_b=optional(row, "材料唯一码2"),
                    component_coordinate_a=optional(row, "元件1坐标"),
                    component_coordinate_b=optional(row, "元件2坐标"),
                )
            )
    finally:
        workbook.close()
    reject_indistinguishable_duplicates(records)
    return records


def group_by_sheet(records: list[WeldInput]) -> dict[str, list[WeldInput]]:
    grouped: dict[str, list[WeldInput]] = defaultdict(list)
    for record in records:
        grouped[record.sheet_id].append(record)
    return dict(grouped)
