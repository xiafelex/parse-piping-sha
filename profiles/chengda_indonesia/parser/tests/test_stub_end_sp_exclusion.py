from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR
import sys

from openpyxl import Workbook

PROJECT_DIR = DATA_DIR
sys.path.insert(0, str(CODE_PROGRAM_DIR))

from chengda_construction_welds import (
    read_construction_weld_inputs,
    read_iso_review_weld_inputs,
)
from chengda_management_table import read_management_inputs


def test_flange_table_force_excludes_stub_end_sp(tmp_path: Path) -> None:
    table = tmp_path / "flange.csv"
    table.write_text(
        "管线号,页码,法兰编号,UCI,材料描述\n"
        "A-01,1,FL1,{UCI-1},STUB END SP; PE\n"
        "A-01,1,FL2,{UCI-2},WELD NECK FLANGE\n",
        encoding="utf-8-sig",
    )

    records = read_management_inputs(flange_table=table)

    assert [(row.number, row.ucis) for row in records] == [("FL2", ("{UCI-2}",))]


def test_construction_table_force_excludes_stub_end_sp(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([
        "单页图号", "焊点编号", "施工焊口号", "焊点类型", "焊点位置",
        "描述1", "描述2",
    ])
    sheet.append(["A-01", "F1", "F101", "F", "1,2,3", "PIPE", "stub end sp; PE"])
    sheet.append(["A-01", "F2", "F102", "F", "4,5,6", "PIPE", "FLANGE"])
    path = tmp_path / "construction.xlsx"
    workbook.save(path)
    workbook.close()

    records, skipped = read_construction_weld_inputs(path)

    assert [row.weld_number for row in records] == ["F102"]
    assert [(row.weld_number, row.manual_review_required) for row in skipped] == [("F101", False)]
    assert "强制排除" in skipped[0].reason


def test_iso_review_force_excludes_stub_end_before_manual_review(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([
        "单页图号", "焊点编号", "焊点类型", "焊点位置", "描述1", "描述2",
        "ISO图号", "ISO焊口号", "对应状态", "核对备注",
    ])
    sheet.append([
        "A-01", "F1", "F", "1,2,3", "STUB END SP", "PIPE",
        "A-01", "F9", "已配对", "EP图焊点缺失",
    ])
    sheet.append([
        "A-01", "F2", "F", "4,5,6", "PIPE", "FLANGE",
        "A-01", "F10", "已配对", "",
    ])
    path = tmp_path / "iso-review.xlsx"
    workbook.save(path)
    workbook.close()

    records, skipped = read_iso_review_weld_inputs(path)

    assert [row.weld_number for row in records] == ["F10"]
    assert [(row.weld_number, row.manual_review_required) for row in skipped] == [("F9", False)]
    assert "STUB END SP" in skipped[0].reason
