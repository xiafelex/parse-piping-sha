from __future__ import annotations

import sys
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR

from openpyxl import Workbook


PROJECT_DIR = DATA_DIR
PROGRAM_DIR = CODE_PROGRAM_DIR
if str(PROGRAM_DIR) not in sys.path:
    sys.path.insert(0, str(PROGRAM_DIR))


def test_spool_reader_emits_one_label_per_sheet_for_cross_page_spool(tmp_path: Path) -> None:
    from chengda_spool_table import group_spools_by_sheet, read_spool_inputs

    path = tmp_path / "welds.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["单页图号", "ISO图号", "焊点类型", "国内预制管段号"])
    sheet.append(["LINE-01-01", "LINE-01-01", "F", "LINE-01-SP01"])
    sheet.append(["LINE-01-02", "LINE-01-02", "S", "LINE-01-SP01"])
    sheet.append(["LINE-01-02", "LINE-01-02", "S", "LINE-01-SP01"])
    sheet.append(["LINE-01-01", "LINE-01-01", "F", "LINE-01-SP02"])
    workbook.save(path)
    workbook.close()

    records = read_spool_inputs(path, "国内预制管段号")
    assert len(records) == 3
    sp01 = [record for record in records if record.number == "LINE-01-SP01"]
    assert [(record.sheet_id, record.source_rows) for record in sp01] == [
        ("LINE-01-01", (2,)),
        ("LINE-01-02", (3, 4)),
    ]
    assert all(record.all_source_rows == (2, 3, 4) for record in sp01)
    grouped = group_spools_by_sheet(records)
    assert [row.number for row in grouped[("line-01-02", 1)]] == ["LINE-01-SP01"]
    assert [row.number for row in grouped[("line-01-01", 1)]] == [
        "LINE-01-SP01",
        "LINE-01-SP02",
    ]


def test_spool_reader_accepts_manual_checked_type_and_number_columns(tmp_path: Path) -> None:
    from chengda_spool_table import read_spool_inputs

    path = tmp_path / "manual-checked.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "单页图号",
            "ISO图号",
            "焊点类型-人工核对",
            "国内预制管段号-人工核对",
        ]
    )
    sheet.append(["LINE-01", "LINE-01", "F", "SP01"])
    sheet.append(["LINE-01", "LINE-01", "FS", "SP01"])
    workbook.save(path)
    workbook.close()

    records = read_spool_inputs(path, "国内预制管段号-人工核对")

    assert len(records) == 1
    assert records[0].source_rows == (3, 2)
