from __future__ import annotations

import sys
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR


PROJECT_DIR = DATA_DIR
PROGRAM_DIR = CODE_PROGRAM_DIR
if str(PROGRAM_DIR) not in sys.path:
    sys.path.insert(0, str(PROGRAM_DIR))
PARSER_DIR = CODE_PARSER_DIR
if str(PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(PARSER_DIR))


def test_discovers_all_matching_chengda_sha_and_pcf_jobs() -> None:
    from convert_chengda_weld_sha_to_pdf import discover_weld_jobs

    base = PROJECT_DIR / "SHA文件转换"
    jobs = discover_weld_jobs(base / "000200FW", base / "000200FW 0-PCF")

    assert len(jobs) == 130
    assert len({job.sheet_id for job in jobs}) == 130
    assert all(job.page == 1 for job in jobs)
    assert all(job.sha_path.stem == job.pcf_path.stem == job.sheet_id for job in jobs)


def test_converts_one_real_page_without_modifying_source_sha(tmp_path: Path) -> None:
    from convert_chengda_sha_to_pdf import validate_pdf
    from convert_chengda_weld_sha_to_pdf import WeldPageJob, convert_weld_job

    base = PROJECT_DIR / "SHA文件转换"
    source_sha = base / "000200FW" / "00-42-0200-26-10FW41001-01.sha"
    source_pcf = base / "000200FW 0-PCF" / "00-42-0200-26-10FW41001-01.pcf"
    test_pcf = tmp_path / source_pcf.name
    test_pcf.write_bytes(
        source_pcf.read_bytes().replace(
            b"REPEAT-WELD-IDENTIFIER    0",
            b"REPEAT-WELD-IDENTIFIER    F6",
            1,
        )
    )
    before = hashlib.sha256(source_sha.read_bytes()).hexdigest()
    job = WeldPageJob(source_sha.stem, source_sha, test_pcf, 1, "Sheet6")

    result = convert_weld_job(job, tmp_path / "pdf", tmp_path / "audit", force=True)

    assert hashlib.sha256(source_sha.read_bytes()).hexdigest() == before
    assert result.callout_count == 1
    assert validate_pdf(result.pdf_path)
    verification = json.loads(result.verification_path.read_text(encoding="utf-8"))
    assert verification["callout_count"] == 1
    assert verification["typed_marker_structurally_verified"] == 1


def test_cli_plan_only_reports_all_130_jobs() -> None:
    base = PROJECT_DIR / "SHA文件转换"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROGRAM_DIR / "convert_chengda_weld_sha_to_pdf.py"),
            "--sha-dir", str(base / "000200FW"),
            "--pcf-dir", str(base / "000200FW 0-PCF"),
            "--plan-only",
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"job_count": 130' in completed.stdout


def test_missing_pcf_sha_can_be_collected_for_audit(tmp_path: Path) -> None:
    from convert_chengda_weld_sha_to_pdf import discover_weld_jobs

    sha_dir = tmp_path / "sha"
    pcf_dir = tmp_path / "pcf"
    sha_dir.mkdir()
    pcf_dir.mkdir()
    (sha_dir / "LINE-WITHOUT-PCF.sha").write_bytes(b"not parsed without a PCF")
    missing: list[str] = []

    jobs = discover_weld_jobs(sha_dir, pcf_dir, missing_pcf_sheet_ids=missing)

    assert jobs == []
    assert missing == ["LINE-WITHOUT-PCF"]


def test_unmatched_workbook_lists_sha_without_pcf(tmp_path: Path) -> None:
    from openpyxl import load_workbook
    from convert_chengda_weld_sha_to_pdf import write_unmatched_number_xlsx

    output = write_unmatched_number_xlsx(
        tmp_path,
        [],
        ["LINE-B", "LINE-A", "LINE-A"],
    )
    workbook = load_workbook(output, read_only=True, data_only=True)
    sheet = workbook["SHA缺少PCF"]

    assert list(sheet.values) == [
        ("单页图号", "处理状态", "说明"),
        ("LINE-A", "未转换", "存在SHA，但未找到同名单页PCF；未执行编号标注转换"),
        ("LINE-B", "未转换", "存在SHA，但未找到同名单页PCF；未执行编号标注转换"),
    ]


def test_unmatched_workbook_lists_reviewable_support_inferences(tmp_path: Path) -> None:
    from openpyxl import load_workbook
    from convert_chengda_weld_sha_to_pdf import write_unmatched_number_xlsx

    page_dir = tmp_path / "pages" / "LINE-A_1"
    page_dir.mkdir(parents=True)
    (page_dir / "weld-map.json").write_text(
        json.dumps({"welds": []}, ensure_ascii=False), encoding="utf-8"
    )
    (page_dir / "management-callouts.json").write_text(
        json.dumps(
            {
                "callouts": [
                    {
                        "kind": "support",
                        "number": "SP1",
                        "mapping_method": "support-partial-uci-cluster",
                        "ucis": ["{A}", "{B}"],
                    },
                    {
                        "kind": "support",
                        "number": "SP2",
                        "mapping_method": "support-coordinate-unique-weld-root",
                        "ucis": ["{C}"],
                    },
                ],
                "unresolved": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = write_unmatched_number_xlsx(
        tmp_path,
        [{"sheet_id": "LINE-A", "page": 1, "status": "success"}],
    )
    workbook = load_workbook(output, read_only=True, data_only=True)
    rows = list(workbook["自动匹配需审核"].iter_rows(min_row=2, values_only=True))

    assert [(row[3], row[4]) for row in rows] == [
        ("SP1", "support-partial-uci-cluster"),
        ("SP2", "support-coordinate-unique-weld-root"),
    ]


def test_unmatched_workbook_lists_best_effort_pages_for_manual_layout_review(
    tmp_path: Path,
) -> None:
    from openpyxl import load_workbook
    from convert_chengda_weld_sha_to_pdf import write_unmatched_number_xlsx

    page_dir = tmp_path / "pages" / "LINE-A_1"
    page_dir.mkdir(parents=True)
    (page_dir / "weld-map.json").write_text(
        json.dumps({"welds": []}, ensure_ascii=False), encoding="utf-8"
    )
    (page_dir / "management-callouts.json").write_text(
        json.dumps({"callouts": [], "unresolved": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (page_dir / "verification.json").write_text(
        json.dumps(
            {
                "layout_mode": "best-effort",
                "callout_count": 35,
                "leader_source_crossing_count": 12,
                "leader_leader_crossing_callout_count": 0,
                "callout_conflict_count": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = write_unmatched_number_xlsx(
        tmp_path,
        [{"sheet_id": "LINE-A", "page": 1, "status": "success"}],
    )
    workbook = load_workbook(output, read_only=True, data_only=True)
    rows = list(workbook["宽松布局需审核"].iter_rows(min_row=2, values_only=True))

    assert rows == [
        (
            "LINE-A",
            1,
            "best-effort",
            35,
            12,
            0,
            0,
            "严格及紧凑布局均无解，需人工检查是否手工落图",
        )
    ]


def test_writes_concise_manual_review_log_for_unmatched_and_unique_remaining(
    tmp_path: Path,
) -> None:
    from convert_chengda_weld_sha_to_pdf import write_manual_review_log

    audit_dir = tmp_path / "audit"
    page_a = audit_dir / "pages" / "LINE-A_1"
    page_b = audit_dir / "pages" / "LINE-B_1"
    page_a.mkdir(parents=True)
    page_b.mkdir(parents=True)
    (page_a / "weld-map.json").write_text(
        json.dumps(
            {
                "welds": [
                    {
                        "weld_number": "",
                        "confidence_status": "unmapped",
                        "mapping_method": "unmapped",
                    },
                    {
                        "weld_number": "FS12",
                        "confidence_status": "unmapped",
                        "mapping_method": "unmapped",
                    },
                    {
                        "weld_number": "F13",
                        "confidence_status": "mapped",
                        "mapping_method": "sheet-ellipse-anchor-forward-owned",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (page_a / "management-callouts.json").write_text(
        json.dumps(
            {
                "callouts": [
                    {
                        "kind": "flange",
                        "number": "FL2",
                        "mapping_method": "unique-pcf-flange-interface-weld",
                    }
                ],
                "unresolved": [
                    {"kind": "support", "number": "SP9", "reason": "SHA中无唯一目标"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (page_b / "weld-map.json").write_text(
        json.dumps({"welds": []}, ensure_ascii=False), encoding="utf-8"
    )
    (page_b / "management-callouts.json").write_text(
        json.dumps({"callouts": [], "unresolved": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    rows = [
        {"sheet_id": "LINE-A", "page": 1, "status": "success"},
        {"sheet_id": "LINE-B", "page": 1, "status": "success"},
    ]

    log_path = write_manual_review_log(audit_dir, rows)
    content = log_path.read_text(encoding="utf-8")

    assert log_path.name == "SHA转换人工审核日志.txt"
    assert "转换页数：2；成功：2；失败：0" in content
    assert "LINE-A：焊口 FS12；支架 SP9" in content
    assert "LINE-A：法兰 FL2" in content
    assert "LINE-B" not in content
    assert "编号缺失" not in content
    assert "graphic_ref" not in content
    assert "mapping_method" not in content


def test_injected_weld_label_is_not_reclassified_as_instrument_tag(monkeypatch) -> None:
    """A callout placed over an existing bubble must remain a weld label."""

    import sha_to_svg_prototype as parser

    records = [
        {
            "graphic_ref": 0x00F01008,
            "text": "FS19",
            "x": 0.2,
            "y": 0.2,
            "format_ref": 0,
        },
        {
            "graphic_ref": 1234,
            "text": "10PG",
            "x": 0.2,
            "y": 0.2,
            "format_ref": 0,
        },
    ]
    circle = {
        "child_ref": 99,
        "center_x": 0.2 * parser.SHEET_UNIT,
        "center_y": 0.2 * parser.SHEET_UNIT,
        "radius": 126.0,
    }
    monkeypatch.setattr(parser, "text_records", lambda _data: records)

    tags = parser.instrument_tag_records(b"", [circle], b"")

    assert [(row["graphic_ref"], row["text"]) for row in tags] == [(1234, "10PG")]


def test_rendered_weld_callouts_are_large_bold_red_circles(tmp_path: Path) -> None:
    """Catch regressions to black labels, F diamonds, or undersized text."""

    from chengda_weld_mapping import build_weld_map

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-10FW41001-01"
    source_sha = base / "000200FW" / f"{sheet_id}.sha"
    source_pcf = base / "000200FW 0-PCF-焊口反写测试" / f"{sheet_id}.pcf"
    weld_map = tmp_path / "weld-map.json"
    annotated_sha = tmp_path / "annotated.sha"
    svg = tmp_path / "rendered.svg"

    build_weld_map(source_pcf, source_sha, weld_map)
    injected = subprocess.run(
        [
            sys.executable,
            str(CODE_PARSER_DIR / "inject_sha_weld_callouts.py"),
            str(source_sha),
            "--output", str(annotated_sha),
            "--pages", "1",
            "--weld-map", str(weld_map),
            "--distance-threshold", "80.0",
            "--diamond-radius", "95",
        ],
        capture_output=True,
        text=True,
    )
    assert injected.returncode == 0, injected.stderr
    rendered = subprocess.run(
        [
            sys.executable,
            str(CODE_PARSER_DIR / "sha_to_svg_prototype.py"),
            str(annotated_sha),
            "--page", "1",
            "--sheet-stream", "Sheet6",
            "--output", str(svg),
        ],
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr

    root = ET.parse(svg).getroot()
    labels = [
        node for node in root.iter("{http://www.w3.org/2000/svg}text")
        if node.attrib.get("data-mapping") == "sha-injected-weld-marker-center"
    ]
    leaders = [
        node for node in root.iter("{http://www.w3.org/2000/svg}line")
        if node.attrib.get("data-layer") == "sha-injected-weld-leader"
    ]
    leader_halos = [
        node for node in root.iter("{http://www.w3.org/2000/svg}line")
        if node.attrib.get("data-layer") == "sha-injected-weld-leader-halo"
    ]
    marker_edges = [
        node for node in root.iter("{http://www.w3.org/2000/svg}line")
        if node.attrib.get("data-layer") == "sha-injected-weld-marker"
    ]
    marker_circles = [
        node for node in root.iter("{http://www.w3.org/2000/svg}circle")
        if node.attrib.get("data-layer") == "sha-injected-weld-marker"
    ]
    inferred_frames = [
        node for node in root.iter("{http://www.w3.org/2000/svg}rect")
        if node.attrib.get("data-layer") == "inferred-marker-frame"
    ]

    assert labels
    assert len(leaders) == len(labels)
    assert len(leader_halos) == len(labels)
    assert all(node.attrib.get("stroke") == "#ffffff" for node in leader_halos)
    assert all(float(node.attrib["stroke-width"]) > 10.0 for node in leader_halos)
    assert not marker_edges
    assert len(marker_circles) == len(labels)
    assert all(node.attrib.get("fill") == "#ff0000" for node in labels)
    assert all(node.attrib.get("font-weight") == "700" for node in labels)
    assert all("SimHei" in node.attrib.get("font-family", "") for node in labels)
    assert all(float(node.attrib["font-size"]) == 88.0 for node in labels)
    assert all("textLength" not in node.attrib for node in labels)
    assert all(node.attrib.get("stroke") == "#ff0000" for node in leaders + marker_circles)
    assert all(float(node.attrib["r"]) == 120.0 for node in marker_circles)
    for label in labels:
        center_x = float(label.attrib["x"])
        center_y = float(label.attrib["y"])
        assert not any(
            float(frame.attrib["x"]) <= center_x <= float(frame.attrib["x"]) + float(frame.attrib["width"])
            and float(frame.attrib["y"]) <= center_y <= float(frame.attrib["y"]) + float(frame.attrib["height"])
            for frame in inferred_frames
        )


def test_known_callouts_prefer_blank_lanes_without_crossing_source_lines(tmp_path: Path) -> None:
    """Catch short-leader layouts that hide crossings inside the root exemption."""

    from analyze_iso_split import read_sha_streams
    from convert_chengda_weld_sha_to_pdf import WeldPageJob, convert_weld_job
    from inject_sha_weld_callouts import (
        leader_clear_of_source,
        leader_source_crossing_counts,
        sheet_streams,
        source_obstacle_boxes,
    )
    from sha_to_svg_prototype import (
        line_segments,
        template_line_segments,
    )

    base = PROJECT_DIR / "SHA文件转换"
    cases = {
        "00-42-0200-26-40IA40502-01": {"F4", "FS9", "F19"},
        "00-42-0200-26-10FW41001-01": {"FS5"},
    }
    for sheet_id, labels in cases.items():
        source_sha = base / "000200FW" / f"{sheet_id}.sha"
        source_pcf = base / "000200FW 0-PCF-焊口反写测试" / f"{sheet_id}.pcf"
        result = convert_weld_job(
            WeldPageJob(sheet_id, source_sha, source_pcf, 1, "Sheet6"),
            tmp_path / "pdf",
            tmp_path / "audit",
            force=True,
        )
        qa = json.loads(result.verification_path.read_text(encoding="utf-8"))
        streams = read_sha_streams(source_sha)
        target = sheet_streams(streams, {1})[0]
        psm = streams.get("PSMcluster0", b"")
        template = streams.get("Sheet221", b"")
        segments = (
            line_segments(target.data)
            + template_line_segments(target.data)
            + template_line_segments(template)
        )
        boxes = source_obstacle_boxes(target.data, psm, gap=5.0)
        boxes.extend(source_obstacle_boxes(template, psm, gap=5.0))
        selected = [row for row in qa["callouts"] if row["label"] in labels]

        assert {row["label"] for row in selected} == labels
        if sheet_id == "00-42-0200-26-40IA40502-01":
            reviewed = {
                row["label"]: row
                for row in qa["callouts"]
                if row["label"] in {"FS3", "FS7", "FS9", "FS14", "FS16"}
            }
            assert set(reviewed) == {"FS3", "FS7", "FS9", "FS14", "FS16"}
            offsets = {
                label: (
                    row["marker_center"][0] - row["leader_start"][0],
                    row["marker_center"][1] - row["leader_start"][1],
                )
                for label, row in reviewed.items()
            }
            assert offsets["FS3"][0] < 0
            for label, horizontal, vertical in (
                ("FS7", 1, 1),
                ("FS9", -1, 1),
                ("FS14", -1, 1),
                ("FS16", 1, -1),
            ):
                dx, dy = offsets[label]
                directed_x = dx * horizontal
                directed_y = dy * vertical
                assert directed_x > 0 and directed_y > 0
                assert min(directed_x, directed_y) >= 0.25 * max(directed_x, directed_y)
        clear_after_local_root = {
            row["label"]: leader_clear_of_source(
                tuple(row["leader_start"] + row["leader_end"]),
                segments,
                boxes,
                # Ignore only the compact fitting/weld symbol at the root;
                # pipe, dimension and annotation crossings beyond it remain.
                root_clearance=150.0,
            )
            for row in selected
        }
        assert all(
            clear
            for label, clear in clear_after_local_root.items()
            if label != "FS9"
        ), clear_after_local_root
        crossings = {
            row["label"]: leader_source_crossing_counts(
                tuple(row["leader_start"] + row["leader_end"]),
                segments,
                boxes,
                root_clearance=20.0,
            )
            for row in selected
        }
        # FS9 is enclosed by several local fitting/dimension strokes.  Those
        # root-local intersections receive a narrow white drafting halo, but
        # no reviewed callout may cut through source text or continue across
        # source geometry outside the compact root zone checked above.
        if "FS9" in crossings:
            assert crossings["FS9"][0] <= 1, crossings
        assert all(text_count == 0 for _, text_count in crossings.values()), crossings
