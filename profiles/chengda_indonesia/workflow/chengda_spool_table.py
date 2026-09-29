#!/usr/bin/env python3
"""Read and deduplicate domestic prefabricated-spool labels from a weld table."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from openpyxl import load_workbook


@dataclass(frozen=True)
class SpoolInput:
    number: str
    sheet_id: str
    page: int
    source_rows: tuple[int, ...]
    all_source_rows: tuple[int, ...]

    @property
    def key(self) -> str:
        return f"spool:{self.number}"


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _pipeline_key(sheet_id: str) -> str:
    return re.sub(r"-\d{2}$", "", sheet_id.strip(), flags=re.IGNORECASE).casefold()


def _normalized_weld_type(value: object) -> str:
    weld_type = _text(value).upper()
    return "S" if weld_type == "FS" else weld_type


def read_spool_inputs(path: Path, number_column: str) -> list[SpoolInput]:
    """Return one target for each sheet/spool-number pair.

    A spool can be referenced by several weld rows and can continue across
    different ISO sheets. Each involved sheet receives exactly one callout;
    every candidate row on that sheet remains available to the geometry
    resolver so one bad weld mapping does not suppress an otherwise
    identifiable spool.
    """

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        try:
            headers = [_text(value) for value in next(rows)]
        except StopIteration as exc:
            raise ValueError(f"焊口表为空：{path}") from exc
        weld_type_column = next(
            (name for name in ("焊点类型", "焊点类型-人工核对") if name in headers),
            "焊点类型",
        )
        required = ("单页图号", "ISO图号", weld_type_column, number_column)
        missing = [name for name in required if name not in headers]
        if missing:
            raise ValueError(f"管段号表缺少必需列：{', '.join(missing)}")
        index = {name: headers.index(name) for name in headers if name}

        def value(row: tuple[object, ...], name: str) -> str:
            column = index[name]
            return _text(row[column]) if column < len(row) else ""

        grouped: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
        for excel_row, row in enumerate(rows, start=2):
            number = value(row, number_column)
            if not number:
                continue
            sheet_id = value(row, "ISO图号") or value(row, "单页图号")
            if not sheet_id:
                raise ValueError(f"Excel第{excel_row}行管段号{number}缺少ISO图号和单页图号")
            grouped.setdefault((_pipeline_key(sheet_id), number), []).append(
                (excel_row, sheet_id, _normalized_weld_type(value(row, weld_type_column)))
            )
    finally:
        workbook.close()

    result: list[SpoolInput] = []
    for (_pipeline, number), candidates in grouped.items():
        by_sheet: dict[str, list[tuple[int, str]]] = {}
        for excel_row, sheet_id, weld_type in candidates:
            by_sheet.setdefault(sheet_id, []).append((excel_row, weld_type))
        all_source_rows = tuple(sorted(row[0] for row in candidates))
        for selected_sheet, selected_rows in by_sheet.items():
            ordered_rows = tuple(
                excel_row
                for excel_row, _ in sorted(
                    selected_rows,
                    key=lambda item: (item[1] != "S", item[0]),
                )
            )
            result.append(
                SpoolInput(
                    number=number,
                    sheet_id=selected_sheet,
                    page=1,
                    source_rows=ordered_rows,
                    all_source_rows=all_source_rows,
                )
            )
    return sorted(result, key=lambda row: (row.sheet_id.casefold(), row.number.casefold()))


def group_spools_by_sheet(
    records: list[SpoolInput],
) -> dict[tuple[str, int], list[SpoolInput]]:
    from chengda_sheet_ids import sheet_id_key
    grouped: dict[tuple[str, int], list[SpoolInput]] = {}
    for record in records:
        grouped.setdefault((sheet_id_key(record.sheet_id), record.page), []).append(record)
    return grouped
