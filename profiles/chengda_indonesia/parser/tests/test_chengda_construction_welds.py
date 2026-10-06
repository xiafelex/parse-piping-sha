from decimal import Decimal
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR
import sys

PROJECT_DIR = DATA_DIR
PROGRAM_DIR = CODE_PROGRAM_DIR
if str(PROGRAM_DIR) not in sys.path:
    sys.path.insert(0, str(PROGRAM_DIR))

from chengda_construction_welds import (
    ConstructionWeldInput,
    apply_construction_welds,
    construction_records_for_sheet,
    group_construction_by_sheet,
    read_construction_weld_inputs,
    read_iso_review_weld_inputs,
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


def test_pad_and_topologically_connected_weld_can_share_sha_root() -> None:
    records = [
        ConstructionWeldInput(
            excel_row=10, sheet_id="LINE-01", weld_number="F32", weld_type="F",
            coordinate=(Decimal("100"), Decimal("200"), Decimal("300")),
            joining_type="pad", material_uci_a="PIPE-A", material_uci_b="PAD-A",
        ),
        ConstructionWeldInput(
            excel_row=11, sheet_id="LINE-01", weld_number="F33", weld_type="F",
            coordinate=(Decimal("100"), Decimal("200"), Decimal("380")),
            joining_type="sw", material_uci_a="PAD-A", material_uci_b="PIPE-B",
        ),
    ]
    payload = {"welds": [
        {
            "page": 1, "graphic_ref": 99, "uci": "{PAD-ROOT-A}",
            "pcf_endpoint": [100.0, 200.0, 300.0, 4.0],
            "sha_x": 100.0, "sha_y": 200.0,
        },
        {
            "page": 1, "graphic_ref": 100, "uci": "{PAD-ROOT-B}",
            "pcf_endpoint": [100.0, 200.0, 380.0, 4.0],
            "sha_x": 100.0, "sha_y": 200.0,
        },
    ]}
    output, unresolved = apply_construction_welds(payload, records)
    assert not unresolved
    assert [row["weld_number"] for row in output["welds"] if row.get("weld_number")] == [
        "F32", "F33"
    ]

    unrelated = [
        records[0],
        ConstructionWeldInput(
            **{**records[1].__dict__, "material_uci_a": "OTHER-PAD"}
        ),
    ]
    _, unresolved = apply_construction_welds(payload, unrelated)
    assert [row["weld_number"] for row in unresolved] == ["F33"]


def test_coincident_olets_can_share_root_but_distant_olets_cannot() -> None:
    payload = {"welds": [
        {
            "page": 1, "graphic_ref": 99, "uci": "{OLET-ROOT-A}",
            "pcf_endpoint": [100.0, 200.0, 300.0, 4.0],
            "sha_x": 100.0, "sha_y": 200.0,
        },
        {
            "page": 1, "graphic_ref": 100, "uci": "{OLET-ROOT-B}",
            "pcf_endpoint": [100.5, 200.0, 300.0, 4.0],
            "sha_x": 100.0, "sha_y": 200.0,
        },
        {
            "page": 1, "graphic_ref": 101, "uci": "{OLET-ROOT-C}",
            "pcf_endpoint": [100.0, 200.0, 310.0, 4.0],
            "sha_x": 100.0, "sha_y": 200.0,
        },
    ]}
    first = ConstructionWeldInput(
        excel_row=10, sheet_id="LINE-01", weld_number="F1", weld_type="F",
        coordinate=(Decimal("100"), Decimal("200"), Decimal("300")),
        joining_type="olet",
    )
    second = ConstructionWeldInput(
        excel_row=11, sheet_id="LINE-01", weld_number="F2", weld_type="F",
        coordinate=(Decimal("100.5"), Decimal("200"), Decimal("300")),
        joining_type="OLET",
    )
    output, unresolved = apply_construction_welds(payload, [first, second])
    assert not unresolved
    assert output["summary"]["callout_target_count"] == 2
    far = ConstructionWeldInput(
        **{**second.__dict__, "coordinate": (
            Decimal("100"), Decimal("200"), Decimal("310")
        )}
    )
    _, unresolved = apply_construction_welds(payload, [first, far])
    assert [row["weld_number"] for row in unresolved] == ["F2"]


def test_duplicate_iso_number_prefers_matching_original_number() -> None:
    records = [
        ConstructionWeldInput(
            excel_row=20,
            sheet_id="LINE-01",
            weld_number="F27",
            weld_type="F",
            coordinate=(Decimal("4"), Decimal("5"), Decimal("6")),
            original_weld_number="F31",
        ),
        ConstructionWeldInput(
            excel_row=10,
            sheet_id="LINE-01",
            weld_number="F27",
            weld_type="F",
            coordinate=(Decimal("1"), Decimal("2"), Decimal("3")),
            original_weld_number="F27",
        ),
    ]

    payload = {
        "welds": [
            {
                "page": 1,
                "graphic_ref": ref,
                "uci": uci,
                "pcf_endpoint": point,
                "sha_x": x,
                "sha_y": 100.0,
                "mapping_method": "native",
                "confidence_status": "mapped",
            }
            for ref, uci, point, x in (
                (1, "{EXACT}", [1.0, 2.0, 3.0, 4.0], 100.0),
                (2, "{DUPLICATE}", [4.0, 5.0, 6.0, 4.0], 200.0),
            )
        ]
    }

    output, unresolved = apply_construction_welds(payload, records)

    numbered = [row for row in output["welds"] if row.get("weld_number")]
    assert [(row["weld_number"], row["uci"]) for row in numbered] == [
        ("F27", "{EXACT}")
    ]
    assert unresolved == [
        {
            "excel_row": 20,
            "sheet_id": "LINE-01",
            "weld_number": "F27",
            "coordinate": ["4", "5", "6"],
            "reason": "同页ISO焊口号重复，已保留与EP3D焊点编号一致的记录，需人工核对",
        }
    ]
    assert output["summary"]["numbered_weld_count"] == 2
    assert output["summary"]["callout_target_count"] == 1
    assert output["summary"]["unmapped_numbered_count"] == 1


def test_iso_review_duplicate_note_columns_and_301200_discard(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([
        "管线号", "焊点编号", "焊点类型", "焊点位置",
        "ISO图号", "ISO焊口号", "对应状态", "核对备注", "核对备注",
    ])
    for number, note in [
        ("F1", "EP图缺少焊点"),
        ("F2", "ISO图缺少焊点"),
        ("F3", "EP图缺少焊点,ISO需变更删除，可忽略"),
        ("F4", "EP图缺少焊点，长直管增口"),
    ]:
        sheet.append(["A-01", number, "F", "100,200,300",
                      "A-01", number, "已配对", "", note])
    path = tmp_path / "duplicate-review-notes.xlsx"
    workbook.save(path)
    workbook.close()
    records, skipped = read_iso_review_weld_inputs(path, remark_policy="301200")
    assert [record.weld_number for record in records] == ["F2"]
    assert {item.weld_number: item.manual_review_required for item in skipped} == {
        "F1": True, "F3": False, "F4": True,
    }
    assert all("核对备注" in item.reason or "审核备注" in item.reason for item in skipped)


def test_iso_review_omitted_ep_threaded_valve_note(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["单页图号", "焊点编号", "焊点类型", "焊点位置",
                  "ISO图号", "ISO焊口号", "对应状态", "核对备注"])
    sheet.append(["A-01", "T1", "F", "100,200,300", "A-01", "T8",
                  "已配对", "EP3D的螺纹阀门遗漏焊口"])
    sheet.append(["A-01", "T2", "F", "400,500,600", "A-01", "T9",
                  "已配对", "ISO遗漏焊口"])
    path = tmp_path / "omitted-threaded-weld.xlsx"
    workbook.save(path)
    workbook.close()
    records, skipped = read_iso_review_weld_inputs(path)
    assert [r.weld_number for r in records] == ["T9"]
    assert len(skipped) == 1 and skipped[0].manual_review_required
    assert "EP3D的螺纹阀门遗漏焊口" in skipped[0].reason


def test_iso_review_ep_missing_notes_require_manual_placement(tmp_path: Path) -> None:
    """EP侧缺失焊点不得吸附邻点；其他已补ISO号的记录仍应正常落图。"""

    workbook = Workbook()
    sheet = workbook.active
    sheet.append([
        "单页图号", "焊点编号", "焊点类型", "焊点位置",
        "ISO图号", "ISO焊口号", "对应状态", "核对备注",
    ])
    sheet.append([
        "A-01", "F3A1", "F", "100,200,300",
        "A-01", "FS76", "已配对", "EP图与ISO图不同，EP缺少焊点",
    ])
    sheet.append([
        "A-02", "F6A1", "F", "400,500,600",
        "A-02", "FS46", "已配对", "EP图焊点漏标",
    ])
    sheet.append([
        "A-04", "F9", "F", "1000,1100,1200",
        "A-04", "F109", "已配对", "EP图焊点缺失",
    ])
    sheet.append([
        "A-03", "F8", "F", "700,800,900",
        "A-03", "F108", "ISO缺失漏标", "ISO侧漏标，已人工补号",
    ])
    path = tmp_path / "iso-review-ep-missing.xlsx"
    workbook.save(path)
    workbook.close()

    records, skipped = read_iso_review_weld_inputs(path)

    assert [record.weld_number for record in records] == ["F108"]
    assert [item.weld_number for item in skipped] == ["FS76", "FS46", "F109"]
    assert all(item.manual_review_required for item in skipped)
    assert all("EP3D缺少焊点" in item.reason for item in skipped)


def test_iso_review_ep_missing_note_precedes_blank_position_and_preserves_review_reason(
    tmp_path: Path,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([
        "单页图号", "焊点编号", "焊点类型", "焊点位置",
        "ISO图号", "ISO焊口号", "对应状态", "审核备注", "核对备注",
    ])
    sheet.append([
        "A-01", "F3A1", "F", None,
        "A-01", "F1", "已配对", "ISO侧存在、EP图未标注", "完成核对",
    ])
    sheet.append([
        "A-02", "F5", "F", "100,200,300",
        "A-02", "F2", "已配对", "EP3D焊点漏标", None,
    ])
    sheet.append([
        "A-03", "F7", "F", None,
        "A-03", "F3", "已配对", None, "完成核对",
    ])
    sheet.append([
        "A-04", "F8", "F", "400,500,600",
        "A-04", "F4", "已配对", "ISO侧漏标，已人工补号", None,
    ])
    path = tmp_path / "review-notes.xlsx"
    workbook.save(path)
    workbook.close()

    records, skipped = read_iso_review_weld_inputs(
        path, missing_position_is_ep3d_missing=True
    )
    assert [record.weld_number for record in records] == ["F4"]
    reasons = {item.weld_number: item.reason for item in skipped}
    assert "ISO侧存在、EP图未标注" in reasons["F1"]
    assert "焊点位置为空" not in reasons["F1"]
    assert "EP3D焊点漏标" in reasons["F2"]
    assert reasons["F3"] == "焊点位置为空，视为EP3D缺失焊口，需人工补标"


def test_000201_all_blank_positions_require_manual_placement() -> None:
    book = (
        PROJECT_DIR / "SHA文件转换" / "000201"
        / "焊口表-000201_0A+0B_ISO对照结果.xlsx"
    )
    records, skipped = read_iso_review_weld_inputs(
        book, missing_position_is_ep3d_missing=True
    )
    missing = [
        row for row in skipped
        if row.reason == "焊点位置为空，视为EP3D缺失焊口，需人工补标"
    ]
    assert len(missing) == 37
    assert all(row.manual_review_required for row in missing)
    assert not ({row.excel_row for row in missing} & {row.excel_row for row in records})


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


def test_iso_review_mode_uses_mixed_numbering_and_cross_sheet_target(
    tmp_path: Path,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "单页图号", "焊点编号", "焊点类型", "焊点位置",
            "ISO图号", "ISO焊口号", "对应状态",
            "焊接材料1", "焊接材料2", "数量1", "数量2",
            "单位1", "单位2", "材料唯一码1", "材料唯一码2",
        ]
    )
    sheet.append(["A-01", "F1", "F", "1,2,3", "", "", "不在对照范围"])
    sheet.append(["A-02", "F2", "S", "4,5,6", "A-02", "S20", "已配对"])
    sheet.append(["A-03", "F3", "F", "7,8,9", "A-01", "F18", "已配对"])
    sheet.append(["A-02", "", "F", "", "A-02", "F21", "仅ISO侧"])
    sheet.append(["A-02", "F4", "F", "10,11,12", "A-02", "", "仅EP3D侧"])
    path = tmp_path / "iso-review.xlsx"
    workbook.save(path)

    records, skipped = read_iso_review_weld_inputs(path)

    assert [
        (row.sheet_id, row.weld_number, row.original_weld_number, row.number_source)
        for row in records
    ] == [
        ("A-01", "F1", "F1", "焊点编号"),
        ("A-02", "S20", "F2", "ISO焊口号"),
        ("A-01", "F18", "F3", "ISO焊口号"),
    ]
    assert records[2].source_sheet_id == "A-03"
    assert [(row.weld_number, row.manual_review_required) for row in skipped] == [
        ("F21", True),
        ("F4", False),
    ]


def test_iso_review_mode_accepts_manual_checked_weld_type_column(tmp_path: Path) -> None:
    """Later units rename the checked type column and retain FS for shop welds."""

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "单页图号", "焊点编号", "焊点类型-人工核对", "焊点位置",
            "ISO图号", "ISO焊口号", "对应状态",
        ]
    )
    sheet.append(["A-01", "FS2", "FS", "1,2,3", "", "", "不在对照范围"])
    sheet.append(["A-01", "F3", "S", "4,5,6", "A-01", "S8", "已配对"])
    path = tmp_path / "manual-checked.xlsx"
    workbook.save(path)
    workbook.close()

    records, skipped = read_iso_review_weld_inputs(path)

    assert skipped == []
    assert [(row.weld_number, row.weld_type) for row in records] == [
        ("FS2", "S"),
        ("S8", "S"),
    ]


def test_iso_review_mode_routes_jintong_long_pipe_added_welds_to_manual_review(
    tmp_path: Path,
) -> None:
    """金童长直管增口是ISO单侧焊口，即使带坐标也不得自动落图。"""

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "单页图号", "焊点编号", "焊点类型", "焊点位置",
            "施工焊口号人工核对", "ISO图号", "ISO焊口号", "对应状态",
            "人工核对备注",
        ]
    )
    sheet.append(
        [
            "A-01", "FS7-1", "S", "1,2,3",
            "F10", "A-01", "S7A1", "已配对", "金童长直管增口",
        ]
    )
    sheet.append(
        [
            "B-01", "F9-1", "F", "4,5,6",
            "F14", "", "", "", "金童长直管增口",
        ]
    )
    path = tmp_path / "jintong-added-welds.xlsx"
    workbook.save(path)
    workbook.close()

    records, skipped = read_iso_review_weld_inputs(path)

    assert records == []
    assert [
        (
            row.sheet_id,
            row.weld_number,
            row.reason,
            row.manual_review_required,
        )
        for row in skipped
    ] == [
        (
            "A-01",
            "F10",
            "金童长直管增口，属于施工ISO单侧焊口，需人工补标",
            True,
        ),
        (
            "B-01",
            "F14",
            "金童长直管增口，属于施工ISO单侧焊口，需人工补标",
            True,
        ),
    ]


def test_iso_review_mode_prefers_manually_checked_construction_number(
    tmp_path: Path,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "单页图号", "焊点编号", "焊点类型", "焊点位置",
            "施工焊口号人工核对", "ISO图号", "ISO焊口号", "对应状态",
        ]
    )
    sheet.append(
        [
            "A-01", "F10", "F", "1,2,3",
            "F7", "A-01", "F10", "已配对",
        ]
    )
    path = tmp_path / "manually-checked-number.xlsx"
    workbook.save(path)
    workbook.close()

    records, skipped = read_iso_review_weld_inputs(path)

    assert skipped == []
    assert [(row.weld_number, row.number_source) for row in records] == [
        ("F7", "施工焊口号人工核对")
    ]


def test_iso_review_mode_accepts_line_number_as_sheet_and_checked_type(
    tmp_path: Path,
) -> None:
    """后续单元可用管线号代替单页图号，并优先采用人工核对编号和类型。"""

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "管线号", "焊点编号", "焊点类型-人工核对", "焊点位置",
            "施工焊口号人工核对", "ISO图号", "ISO焊口号", "对应状态",
        ]
    )
    sheet.append(
        [
            "00-42-0202-26-00BR00001-01", "FS2", "F", "1,2,3",
            "F9", "00-42-0202-26-00BR00001-01", "F8", "已配对",
        ]
    )
    path = tmp_path / "checked-columns.xlsx"
    workbook.save(path)
    workbook.close()

    records, skipped = read_iso_review_weld_inputs(path)

    assert skipped == []
    assert [
        (row.sheet_id, row.weld_number, row.weld_type, row.number_source)
        for row in records
    ] == [
        (
            "00-42-0202-26-00BR00001-01",
            "F9",
            "F",
            "施工焊口号人工核对",
        )
    ]


def test_iso_review_mode_routes_contractor_long_pipe_added_welds_to_manual_review(
    tmp_path: Path,
) -> None:
    """不同审核人员写作“施工单位长直管增口”时仍执行同一排除规则。"""

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "管线号", "焊点编号", "焊点类型-人工核对", "焊点位置",
            "施工焊口号人工核对", "ISO图号", "ISO焊口号", "对应状态",
            "人工核对备注",
        ]
    )
    sheet.append(
        [
            "A-01", "F3-1", "F", "1,2,3",
            "F4", "A-01", "F3", "仅ISO侧", "施工单位长直管增口",
        ]
    )
    path = tmp_path / "contractor-added-weld.xlsx"
    workbook.save(path)
    workbook.close()

    records, skipped = read_iso_review_weld_inputs(path)

    assert records == []
    assert [
        (row.sheet_id, row.weld_number, row.reason, row.manual_review_required)
        for row in skipped
    ] == [
        (
            "A-01",
            "F4",
            "施工单位长直管增口，属于施工ISO单侧焊口，需人工补标",
            True,
        )
    ]
