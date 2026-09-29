from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR

import pytest
from openpyxl import Workbook


PROJECT_DIR = DATA_DIR
PROGRAM_DIR = CODE_PROGRAM_DIR
if str(PROGRAM_DIR) not in sys.path:
    sys.path.insert(0, str(PROGRAM_DIR))


def save_workbook(path: Path, rows: list[list[object]]) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    return path


def test_reads_original_alphanumeric_weld_identifiers(tmp_path: Path) -> None:
    from chengda_weld_table import group_by_sheet, read_weld_inputs

    path = save_workbook(
        tmp_path / "welds.xlsx",
        [
            ["单页图号", "焊点编号", "焊点类型", "焊点位置", "材料唯一码1", "材料唯一码2"],
            ["00-42-0200-26-10FW41001-01", "F2", "F", "9207000,7519500,512.70", "{A}", "{B}"],
            ["00-42-0200-26-10FW41001-01", "FS1", "S", "9207000,7520000,512.70", "{B}", "{C}"],
        ],
    )

    records = read_weld_inputs(path)

    assert [record.weld_number for record in records] == ["F2", "FS1"]
    assert records[0].coordinate == (
        Decimal("9207000"),
        Decimal("7519500"),
        Decimal("512.70"),
    )
    assert records[0].material_uci_a == "{A}"
    assert set(group_by_sheet(records)) == {"00-42-0200-26-10FW41001-01"}


def test_rejects_indistinguishable_duplicate_rows(tmp_path: Path) -> None:
    from chengda_weld_table import read_weld_inputs

    path = save_workbook(
        tmp_path / "duplicates.xlsx",
        [
            ["单页图号", "焊点编号", "焊点类型", "焊点位置", "材料唯一码1", "材料唯一码2"],
            ["LINE-01", "F2", "F", "1,2,3", "{A}", "{B}"],
            ["LINE-01", "FS1", "S", "1,2,3", "{A}", "{B}"],
        ],
    )

    with pytest.raises(ValueError, match="无法区分的重复焊口"):
        read_weld_inputs(path)


def test_reports_excel_row_for_invalid_coordinate(tmp_path: Path) -> None:
    from chengda_weld_table import read_weld_inputs

    path = save_workbook(
        tmp_path / "invalid.xlsx",
        [
            ["单页图号", "焊点编号", "焊点类型", "焊点位置"],
            ["LINE-01", "T66", "F", "1,not-a-number,3"],
        ],
    )

    with pytest.raises(ValueError, match="Excel第2行.*焊点位置"):
        read_weld_inputs(path)
