from decimal import Decimal
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from chengda_construction_welds import (
    ConstructionWeldInput,
    apply_construction_welds,
    construction_records_for_sheet,
    group_construction_by_sheet,
    read_construction_weld_inputs,
)
from openpyxl import Workbook


def _record(number: str, row: int) -> ConstructionWeldInput:
    return ConstructionWeldInput(
        excel_row=row,
        sheet_id="10AB02113-01",
        weld_number=number,
        weld_type="S",
        coordinate=(Decimal("1"), Decimal("2"), Decimal("3")),
    )


def test_abbreviated_sheet_id_and_duplicate_root_is_not_double_labelled() -> None:
    records = [_record("S3", 10), _record("S4", 11)]
    grouped = group_construction_by_sheet(records)
    assert construction_records_for_sheet(
        grouped, "10-42-0200-26-10AB02113-01"
    ) == records

    payload = {
        "welds": [
            {
                "page": 1,
                "graphic_ref": 99,
                "uci": "{UCI}",
                "pcf_endpoint": [1.0, 2.0, 3.0, 4.0],
                "sha_x": 100.0,
                "sha_y": 200.0,
                "mapping_method": "native",
                "confidence_status": "mapped",
            }
        ]
    }
    output, unresolved = apply_construction_welds(payload, records)
    numbered = [row for row in output["welds"] if row.get("weld_number")]
    assert [row["weld_number"] for row in numbered] == ["S3"]
    assert numbered[0]["uci"] == "{UCI}"
    assert unresolved[0]["weld_number"] == "S4"
    assert output["summary"]["callout_target_count"] == 1


def test_unique_submillimetre_coordinate_offset_is_mapped() -> None:
    payload = {
        "welds": [
            {
                "page": 1,
                "graphic_ref": 99,
                "uci": "{UNIQUE}",
                "pcf_endpoint": [1.9, 2.0, 3.0, 4.0],
                "mapping_method": "pcf-topology-pipe-end",
                "confidence_status": "mapped",
            }
        ]
    }

    output, unresolved = apply_construction_welds(payload, [_record("S3", 10)])

    assert unresolved == []
    numbered = [row for row in output["welds"] if row.get("weld_number")]
    assert numbered[0]["uci"] == "{UNIQUE}"
    assert numbered[0]["mapping_method"].endswith(
        "construction-coordinate-unique-offset<=1mm"
    )


def test_coordinate_offset_with_multiple_targets_stays_unresolved() -> None:
    payload = {
        "welds": [
            {
                "page": 1,
                "graphic_ref": ref,
                "uci": uci,
                "pcf_endpoint": [x, 2.0, 3.0, 4.0],
                "confidence_status": "mapped",
            }
            for ref, uci, x in ((98, "{A}", 1.8), (99, "{B}", 1.9))
        ]
    }

    output, unresolved = apply_construction_welds(payload, [_record("S3", 10)])

    assert not [row for row in output["welds"] if row.get("weld_number")]
    assert unresolved[0]["reason"] == "PCF焊口块中没有同坐标焊点"


def test_sheet_prefers_construction_numbers_and_falls_back_to_original(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["单页图号", "焊点编号", "施工焊口号", "焊点类型", "焊点位置"])
    sheet.append(["A-01", "F1", "S1", "S", "1,2,3"])
    sheet.append(["A-01", "F2", "", "F", "4,5,6"])
    sheet.append(["A-01", "", "S2", "S", "7,8,9"])
    sheet.append(["B-01", "F3", "", "F", "10,11,12"])
    path = tmp_path / "welds.xlsx"
    workbook.save(path)

    records, skipped = read_construction_weld_inputs(path)
    assert [(row.sheet_id, row.weld_number, row.number_source) for row in records] == [
        ("A-01", "S1", "施工焊口号"),
        ("B-01", "F3", "焊点编号"),
    ]
    assert [(row.weld_number, row.reason) for row in skipped] == [
        ("S2", "焊点编号为空，按规则忽略")
    ]
