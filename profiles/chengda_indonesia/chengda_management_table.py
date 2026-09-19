#!/usr/bin/env python3
"""Read Chengda support, valve and flange management-number tables."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManagementInput:
    kind: str
    sheet_id: str
    page: int
    number: str
    ucis: tuple[str, ...]
    coordinate: tuple[float, float, float] | None = None
    source_rows: tuple[int, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.number}"


def _read_csv(path: Path) -> list[tuple[int, dict[str, str]]]:
    raw = Path(path).read_bytes()
    text = None
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"无法识别CSV编码：{path}")
    return [
        (index, {str(key).strip(): str(value or "").strip() for key, value in row.items()})
        for index, row in enumerate(csv.DictReader(text.splitlines()), start=2)
    ]


def _coordinate(value: str, path: Path, row_number: int) -> tuple[float, float, float]:
    parts = [part.strip() for part in re.split(r"[,，]", value) if part.strip()]
    if len(parts) != 3:
        raise ValueError(f"{path.name}第{row_number}行支架坐标不是三个数值：{value!r}")
    try:
        return tuple(float(part) for part in parts)  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"{path.name}第{row_number}行支架坐标无效：{value!r}") from exc


def _ucis(value: str) -> tuple[str, ...]:
    result: list[str] = []
    for part in re.split(r"[;；]", value):
        normalized = part.strip().upper()
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def read_management_inputs(
    support_table: Path | None = None,
    valve_table: Path | None = None,
    flange_table: Path | None = None,
    project_profile: str = "000200fw",
) -> list[ManagementInput]:
    records: list[ManagementInput] = []
    if support_table is not None:
        path = Path(support_table)
        grouped: dict[
            tuple[str, int, str, tuple[float, float, float]],
            dict[str, object],
        ] = {}
        for row_number, row in _read_csv(path):
            sheet = row.get("管线号", "")
            number = row.get("支架编号", "")
            page = int(row.get("页码", "1") or 1)
            coordinate = _coordinate(row.get("支架坐标", ""), path, row_number)
            if not sheet or not number:
                raise ValueError(f"{path.name}第{row_number}行缺少管线号或支架编号")
            key = (sheet, page, number, coordinate)
            group = grouped.setdefault(key, {"ucis": [], "rows": []})
            for uci in _ucis(row.get("UCI", "")):
                if uci not in group["ucis"]:
                    group["ucis"].append(uci)
            group["rows"].append(row_number)
        for (sheet, page, number, coordinate), group in grouped.items():
            records.append(
                ManagementInput(
                    "support", sheet, page, number,
                    tuple(group["ucis"]), coordinate, tuple(group["rows"]),
                )
            )

    seen_component_rows: set[tuple[object, ...]] = set()
    for path_value, kind, number_column in (
        (valve_table, "valve", "阀门号"),
        (flange_table, "flange", "法兰编号"),
    ):
        if path_value is None:
            continue
        path = Path(path_value)
        for row_number, row in _read_csv(path):
            sheet = row.get("管线号", "")
            number = row.get(number_column, "")
            page = int(row.get("页码", "1") or 1)
            ucis = _ucis(row.get("UCI", ""))
            if not sheet or not number or not ucis:
                raise ValueError(f"{path.name}第{row_number}行缺少管线号、编号或UCI")
            if kind == "valve" and len(ucis) != 1:
                raise ValueError(f"{path.name}第{row_number}行阀门UCI必须唯一")
            if kind == "flange" and len(ucis) > 2:
                raise ValueError(f"{path.name}第{row_number}行法兰UCI最多允许两个")
            identity = (kind, sheet.casefold(), page, number, ucis)
            if project_profile == "100200" and identity in seen_component_rows:
                continue
            seen_component_rows.add(identity)
            records.append(
                ManagementInput(kind, sheet, page, number, ucis, None, (row_number,))
            )

    return sorted(records, key=lambda row: (row.sheet_id.casefold(), row.page, row.kind, row.number))


def group_management_by_sheet(
    records: list[ManagementInput],
) -> dict[tuple[str, int], list[ManagementInput]]:
    grouped: dict[tuple[str, int], list[ManagementInput]] = {}
    for record in records:
        grouped.setdefault((record.sheet_id.casefold(), record.page), []).append(record)
    return grouped
