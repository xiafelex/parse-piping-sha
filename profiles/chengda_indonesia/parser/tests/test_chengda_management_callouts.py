from __future__ import annotations

import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR

import pytest


PROJECT_DIR = DATA_DIR
PROGRAM_DIR = CODE_PROGRAM_DIR
PARSER_DIR = CODE_PARSER_DIR
for candidate in (PROGRAM_DIR, PARSER_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.mark.parametrize(
    "suffix,number,expected",
    [
        ("40DSA30001-06", "FL4", (1476.72, 5311.74)),
        ("40DSA30001-06", "FL5", (1055.88, 5555.34)),
        ("40HA30001-05", "FL1", (1236.48, 5468.4)),
        ("40HA30001-05", "SP1", (1468.103, 5336.902)),
        ("40DSA30001-01", "FL10", (1291.92, 2848.86)),
        ("40HA30001-01", "FL1", (1584.24, 3169.74)),
        ("40DSA30001-03", "FL1", (1945.44, 5977.44)),
        ("40HA30002-03", "FL6", (5118.96, 3729.18)),
    ],
)
def test_403000_roots_use_physical_faces_and_visible_support(suffix, number, expected):
    """Check physical drawing roots, not merely successful number matching."""
    import json
    from chengda_management_callouts import forward_owned_uci_regions, resolve_management_targets
    from chengda_management_table import read_management_inputs

    unit = PROJECT_DIR / "SHA文件转换" / "403000"
    sheet = "40-42-3000-26-" + suffix
    trace_path = unit / "水印及编号标注审计" / "pages" / (sheet + "_1") / "trace.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace["legacy_uci_regions"] = trace["uci_regions"]
    trace["uci_regions"] = forward_owned_uci_regions(unit / "403000 SHA" / (sheet + ".sha"), "Sheet6")
    records = [row for row in read_management_inputs(
        flange_table=unit / "法兰表.csv", support_table=unit / "支架表.csv",
        project_profile="100200",
    ) if row.sheet_id == sheet]
    mapped, _ = resolve_management_targets(
        records, trace, pcf_path=unit / "403000 0A" / (sheet + ".pcf"),
        project_profile="100200",
    )
    target = next(row for row in mapped if row.number == number)
    assert target.anchor == pytest.approx(expected, abs=0.01)


def test_support_rows_form_one_callout_with_multiple_member_ucis(tmp_path: Path) -> None:
    """Losing support assembly grouping would print SP1 three times."""

    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [
            {"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "1,2,3", "UCI": "{A}"},
            {"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "1,2,3", "UCI": "{B}"},
            {"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "1,2,3", "UCI": "{C}"},
        ],
    )

    records = read_management_inputs(support_table=support)

    assert len(records) == 1
    assert records[0].kind == "support"
    assert records[0].number == "SP1"
    assert records[0].ucis == ("{A}", "{B}", "{C}")
    assert records[0].coordinate == (1.0, 2.0, 3.0)


def test_same_support_uci_on_two_pipelines_stays_distinct_by_coordinate(tmp_path: Path) -> None:
    """A repeated support UCI must not collapse two physical placements."""

    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [
            {"管线号": "LINE-A", "页码": "1", "支架编号": "SP1", "支架坐标": "1,2,3", "UCI": "{DUP}"},
            {"管线号": "LINE-B", "页码": "1", "支架编号": "SP1", "支架坐标": "4,5,6", "UCI": "{DUP}"},
        ],
    )

    records = read_management_inputs(support_table=support)

    assert [(row.sheet_id, row.coordinate) for row in records] == [
        ("LINE-A", (1.0, 2.0, 3.0)),
        ("LINE-B", (4.0, 5.0, 6.0)),
    ]


def test_management_inputs_combine_multiple_revision_tables(tmp_path: Path) -> None:
    """0A/0B tables must form one complete set without creating duplicate tags."""

    from chengda_management_table import read_management_inputs

    fields = ["管线号", "页码", "阀门号", "UCI"]
    table_0a = write_csv(
        tmp_path / "valve-0A.csv",
        fields,
        [{"管线号": "LINE-A", "页码": "1", "阀门号": "V1", "UCI": "{A}"}],
    )
    table_0b = write_csv(
        tmp_path / "valve-0B.csv",
        fields,
        [
            {"管线号": "LINE-B", "页码": "1", "阀门号": "V2", "UCI": "{B}"},
            {"管线号": "LINE-A", "页码": "1", "阀门号": "V1", "UCI": "{A}"},
        ],
    )

    records = read_management_inputs(
        valve_table=[table_0a, table_0b],
        project_profile="100200",
    )

    assert [(row.sheet_id, row.number, row.ucis) for row in records] == [
        ("LINE-A", "V1", ("{A}",)),
        ("LINE-B", "V2", ("{B}",)),
    ]


def test_flange_semicolon_uci_pair_resolves_to_interface(tmp_path: Path) -> None:
    """Treating a two-UCI flange row as one literal UCI loses the gasket anchor."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    flange = write_csv(
        tmp_path / "flange.csv",
        ["管线号", "页码", "法兰编号", "UCI"],
        [{"管线号": "LINE-01", "页码": "1", "法兰编号": "FL9", "UCI": "{LEFT};{RIGHT}"}],
    )
    record = read_management_inputs(flange_table=flange)[0]
    trace = {
        "logical_page": 1,
        "uci_regions": [
            {"uci": "{LEFT}", "graphic_ref": 10, "bbox": [100.0, 200.0, 140.0, 240.0]},
            {"uci": "{RIGHT}", "graphic_ref": 20, "bbox": [160.0, 210.0, 200.0, 250.0]},
        ],
        "segments": [],
    }

    mapped, unresolved = resolve_management_targets([record], trace)

    assert unresolved == []
    assert len(mapped) == 1
    assert mapped[0].anchor == (150.0, 225.0)
    assert mapped[0].mapping_method == "paired-uci-interface"


def test_hidden_paired_flange_ucis_use_their_unique_pcf_interface_root(
    tmp_path: Path,
) -> None:
    """The two table UCIs may name adjoining pipe/fitting blocks, not FLANGE blocks."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    flange = write_csv(
        tmp_path / "flange.csv",
        ["管线号", "页码", "法兰编号", "UCI"],
        [{"管线号": "LINE-01", "页码": "1", "法兰编号": "FL1", "UCI": "{PIPE};{ELBOW}"}],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "PIPE-FIXED\n"
        "    END-POINT 0 0 0 4\n"
        "    END-POINT 10 0 0 4\n"
        "    UCI {PIPE}\n"
        "ELBOW\n"
        "    END-POINT 10 0 0 4\n"
        "    END-POINT 10 10 0 4\n"
        "    UCI {ELBOW}\n",
        encoding="utf-8",
    )
    weld_map = {
        "welds": [{
            "page": 1,
            "graphic_ref": 77,
            "sha_x": 700.0,
            "sha_y": 800.0,
            "pcf_endpoint": [10.0, 0.0, 0.0, 4.0],
        }]
    }
    record = read_management_inputs(flange_table=flange)[0]

    mapped, unresolved = resolve_management_targets(
        [record],
        {"logical_page": 1, "uci_regions": [], "segments": []},
        pcf_path=pcf,
        weld_map=weld_map,
        project_profile="100200",
    )

    assert unresolved == []
    assert mapped[0].anchor == (700.0, 800.0)
    assert mapped[0].mapping_method == "pcf-paired-component-interface-weld"


def test_hidden_flange_on_opposite_port_of_visible_fitting_uses_mapped_peer(
    tmp_path: Path,
) -> None:
    """A material-excluded pipe can be recovered from the other port of a fitting."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    flange = write_csv(
        tmp_path / "flange.csv",
        ["管线号", "页码", "法兰编号", "UCI"],
        [
            {"管线号": "LINE-01", "页码": "1", "法兰编号": "FL1", "UCI": "{HIDDEN};{ELBOW}"},
            {"管线号": "LINE-01", "页码": "1", "法兰编号": "FL2", "UCI": "{VISIBLE};{ELBOW}"},
        ],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "ELBOW\n"
        "    END-POINT 0 0 0 4 FL\n"
        "    END-POINT 0 10 0 4 FL\n"
        "    UCI {ELBOW}\n"
        "PIPE-FIXED\n"
        "    END-POINT -10 0 0 4\n"
        "    END-POINT -3 0 0 4\n"
        "    UCI {VISIBLE}\n"
        "PIPE-FIXED\n"
        "    END-POINT 0 13 0 4\n"
        "    END-POINT 0 30 0 4\n"
        "    UCI {HIDDEN}\n",
        encoding="utf-8",
    )
    trace = {
        "logical_page": 1,
        "uci_regions": [
            {"uci": "{ELBOW}", "graphic_ref": 20, "bbox": [100.0, 100.0, 200.0, 200.0]},
            {"uci": "{VISIBLE}", "graphic_ref": 30, "bbox": [180.0, 180.0, 260.0, 260.0]},
        ],
        # The legacy same-record association is not component identity, but it
        # retains the otherwise unowned dashed neighbour geometry.
        "legacy_uci_regions": [
            {"uci": "{ELBOW}", "graphic_ref": 40, "bbox": [40.0, 140.0, 110.0, 210.0]},
        ],
        "segments": [],
    }
    records = read_management_inputs(flange_table=flange)

    mapped, unresolved = resolve_management_targets(
        records,
        trace,
        pcf_path=pcf,
        project_profile="100200",
    )

    assert unresolved == []
    by_number = {target.number: target for target in mapped}
    assert by_number["FL2"].mapping_method == "paired-uci-interface"
    assert by_number["FL1"].anchor == (112.5, 162.5)
    assert by_number["FL1"].mapping_method == "pcf-fitting-opposite-port-legacy-neighbour"
    assert by_number["FL1"].confidence == "review"


def test_opposite_port_recovery_does_not_apply_to_literal_flange(
    tmp_path: Path,
) -> None:
    """The fitting rule must not infer an ordinary FLANGE's hidden neighbour."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    flange = write_csv(
        tmp_path / "flange.csv",
        ["管线号", "页码", "法兰编号", "UCI"],
        [
            {"管线号": "LINE-01", "页码": "1", "法兰编号": "FL1", "UCI": "{HIDDEN};{FLANGE}"},
            {"管线号": "LINE-01", "页码": "1", "法兰编号": "FL2", "UCI": "{VISIBLE};{FLANGE}"},
        ],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "FLANGE\n"
        "    END-POINT 0 0 0 4\n"
        "    END-POINT 0 10 0 4\n"
        "    UCI {FLANGE}\n"
        "PIPE-FIXED\n"
        "    END-POINT -3 0 0 4\n"
        "    END-POINT -10 0 0 4\n"
        "    UCI {VISIBLE}\n"
        "PIPE-FIXED\n"
        "    END-POINT 0 13 0 4\n"
        "    END-POINT 0 30 0 4\n"
        "    UCI {HIDDEN}\n",
        encoding="utf-8",
    )
    trace = {
        "logical_page": 1,
        "uci_regions": [
            {"uci": "{FLANGE}", "graphic_ref": 20, "bbox": [100.0, 100.0, 200.0, 200.0]},
            {"uci": "{VISIBLE}", "graphic_ref": 30, "bbox": [180.0, 180.0, 260.0, 260.0]},
        ],
        "legacy_uci_regions": [
            {"uci": "{FLANGE}", "graphic_ref": 40, "bbox": [40.0, 140.0, 110.0, 210.0]},
        ],
        "segments": [],
    }

    mapped, unresolved = resolve_management_targets(
        read_management_inputs(flange_table=flange),
        trace,
        pcf_path=pcf,
        project_profile="100200",
    )

    assert [target.number for target in mapped] == ["FL2"]
    assert [row["number"] for row in unresolved] == ["FL1"]


def test_fpfl_chain_reconciles_reissued_sha_ucis_by_unique_order(
    tmp_path: Path,
) -> None:
    """A straight FPFL chain may keep its geometry after all internal UCIs change."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    flange = write_csv(
        tmp_path / "flange.csv",
        ["管线号", "页码", "法兰编号", "UCI"],
        [
            {"管线号": "LINE-01", "页码": "1", "法兰编号": "FL2", "UCI": "{A};{B}"},
            {"管线号": "LINE-01", "页码": "1", "法兰编号": "FL1", "UCI": "{B};{C}"},
        ],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "PIPE-FIXED\n"
        "    END-POINT 0 0 0 4\n"
        "    END-POINT 10 0 0 4\n"
        "    SKEY FPFL\n"
        "    UCI {A}\n"
        "    CUT-PIECE-LENGTH 10\n"
        "PIPE-FIXED\n"
        "    END-POINT 13 0 0 4\n"
        "    END-POINT 23 0 0 4\n"
        "    SKEY FPFL\n"
        "    UCI {B}\n"
        "    CUT-PIECE-LENGTH 10\n"
        "PIPE-FIXED\n"
        "    END-POINT 26 0 0 4\n"
        "    END-POINT 36 0 0 4\n"
        "    SKEY FPFL\n"
        "    UCI {C}\n"
        "    CUT-PIECE-LENGTH 10\n",
        encoding="utf-8",
    )
    trace = {
        "logical_page": 1,
        "uci_regions": [
            {"uci": "{A}", "graphic_ref": 10, "bbox": [100.0, 100.0, 200.0, 150.0]},
            {"uci": "{SHA-B}", "graphic_ref": 20, "bbox": [190.0, 100.0, 290.0, 150.0]},
            {"uci": "{SHA-C}", "graphic_ref": 30, "bbox": [280.0, 100.0, 380.0, 150.0]},
            # A following pipe is connected geometrically but lies outside
            # the three-node PCF management chain.
            {"uci": "{SHA-D}", "graphic_ref": 40, "bbox": [370.0, 100.0, 470.0, 150.0]},
        ],
        "segments": [],
    }

    mapped, unresolved = resolve_management_targets(
        read_management_inputs(flange_table=flange),
        trace,
        pcf_path=pcf,
        project_profile="100200",
    )

    assert unresolved == []
    by_number = {target.number: target for target in mapped}
    assert by_number["FL2"].anchor == (195.0, 125.0)
    assert by_number["FL1"].anchor == (285.0, 125.0)
    assert {
        by_number["FL1"].mapping_method,
        by_number["FL2"].mapping_method,
    } == {"pcf-sha-fpfl-chain-order-reconciliation"}
    assert by_number["FL1"].confidence == "review"


def test_repeated_multiport_fitting_uci_uses_bolt_signature_per_port(
    tmp_path: Path,
) -> None:
    """Three labels sharing one TEFL UCI must root at its three flange faces."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    flange = write_csv(
        tmp_path / "flange.csv",
        ["管线号", "页码", "法兰编号", "UCI", "螺栓规格"],
        [
            {"管线号": "LINE-01", "页码": "1", "法兰编号": "FL14", "UCI": "{TEE}", "螺栓规格": "5/8X150"},
            {"管线号": "LINE-01", "页码": "1", "法兰编号": "FL15", "UCI": "{TEE}", "螺栓规格": ""},
            {"管线号": "LINE-01", "页码": "1", "法兰编号": "FL17", "UCI": "{TEE}", "螺栓规格": "1/2X70"},
        ],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "TEE\n"
        "    END-POINT 0 0 0 4 FL\n"
        "    END-POINT 0 100 0 4 FL\n"
        "    BRANCH1-POINT 100 50 0 1 FL\n"
        "    SKEY TEFL\n"
        "    UCI {TEE}\n"
        "GASKET\n    END-POINT -3 0 0 4\n    END-POINT 0 0 0 4\n    UCI {GA}\n"
        "GASKET\n    END-POINT 0 100 0 4\n    END-POINT 0 103 0 4\n    UCI {GB}\n"
        "GASKET\n    END-POINT 100 50 0 1\n    END-POINT 103 50 0 1\n    UCI {GC}\n"
        "VALVE\n    END-POINT -30 0 0 4\n    END-POINT -3 0 0 4\n    UCI {A}\n"
        "VALVE\n    END-POINT 0 103 0 4\n    END-POINT 0 130 0 4\n    UCI {B}\n"
        "VALVE\n    END-POINT 103 50 0 1\n    END-POINT 130 50 0 1\n    UCI {C}\n"
        "BOLT\n    CO-ORDS -3 0 0\n    BOLT-LENGTH 150\n    UCI {BA}\n"
        "BOLT\n    CO-ORDS 103 50 0\n    BOLT-LENGTH 70\n    UCI {BC}\n",
        encoding="utf-8",
    )
    trace = {
        "logical_page": 1,
        "uci_regions": [
            {"uci": "{TEE}", "graphic_ref": 10, "bbox": [100.0, 100.0, 200.0, 200.0]},
            {"uci": "{A}", "graphic_ref": 20, "bbox": [40.0, 140.0, 110.0, 210.0]},
            {"uci": "{B}", "graphic_ref": 30, "bbox": [140.0, 190.0, 210.0, 260.0]},
            {"uci": "{C}", "graphic_ref": 40, "bbox": [190.0, 140.0, 260.0, 210.0]},
        ],
        "segments": [
            {"graphic_ref_low16": "0x000A", "mapping_basis": "direct_object_ref", "sheet_points_normalized": [[0.10, 0.10], [0.10, 0.11]]},
            {"graphic_ref_low16": "0x000A", "mapping_basis": "direct_object_ref", "sheet_points_normalized": [[0.20, 0.10], [0.20, 0.11]]},
            {"graphic_ref_low16": "0x000A", "mapping_basis": "direct_object_ref", "sheet_points_normalized": [[0.30, 0.10], [0.30, 0.11]]},
            {"graphic_ref_low16": "0x0014", "mapping_basis": "direct_object_ref", "sheet_points_normalized": [[0.099, 0.10], [0.099, 0.11]]},
            {"graphic_ref_low16": "0x001E", "mapping_basis": "direct_object_ref", "sheet_points_normalized": [[0.201, 0.10], [0.201, 0.11]]},
            {"graphic_ref_low16": "0x0028", "mapping_basis": "direct_object_ref", "sheet_points_normalized": [[0.301, 0.10], [0.301, 0.11]]},
        ],
    }

    mapped, unresolved = resolve_management_targets(
        read_management_inputs(flange_table=flange, project_profile="100200"),
        trace,
        pcf_path=pcf,
        project_profile="100200",
    )

    assert unresolved == []
    by_number = {target.number: target for target in mapped}
    assert by_number["FL14"].anchor == (1671.6, 1764.0)
    assert by_number["FL15"].anchor == (3368.4, 1764.0)
    assert by_number["FL17"].anchor == (5048.4, 1764.0)
    assert {
        by_number[number].mapping_method for number in ("FL14", "FL15", "FL17")
    } == {"pcf-multiport-fitting-bolt-interface"}


@pytest.mark.parametrize(
    ("sheet_id", "expected_numbers"),
    [
        ("20-42-1600-26-HCL-8890-01", {"FL12", "FL13"}),
        ("20-42-1600-26-HCL-8890-03", {"FL21", "FL22", "FL23", "FL24"}),
        (
            "20-42-1600-26-HCL-8890-04",
            {"FL6", "FL7", "FL8", "FL9", "FL12", "FL13", "FL14", "FL15", "FL19", "FL20"},
        ),
    ],
)
def test_two_port_flanged_elbows_use_their_actual_sha_faces(
    sheet_id: str,
    expected_numbers: set[str],
) -> None:
    """ELFL rows must root at both elbow faces, never at the elbow envelope centre."""

    import json

    from chengda_management_callouts import (
        forward_owned_uci_regions,
        resolve_management_targets,
    )
    from chengda_management_table import read_management_inputs

    unit = PROJECT_DIR / "SHA文件转换" / "201600"
    sha = unit / "201600 SHA" / f"{sheet_id}.sha"
    pcf = unit / "201600 0A PCF" / f"{sheet_id}.pcf"
    page_dir = unit / "水印及编号标注审计" / "pages" / f"{sheet_id}_1"
    trace = json.loads((page_dir / "trace.json").read_text(encoding="utf-8"))
    trace["legacy_uci_regions"] = list(trace.get("uci_regions", []))
    trace["uci_regions"] = forward_owned_uci_regions(sha, "Sheet6")
    weld_map = json.loads((page_dir / "weld-map.json").read_text(encoding="utf-8"))
    records = [
        row for row in read_management_inputs(
            flange_table=unit / "法兰表.csv", project_profile="100200"
        )
        if row.sheet_id == sheet_id and row.page == 1
    ]

    mapped, _unresolved = resolve_management_targets(
        records,
        trace,
        pcf_path=pcf,
        weld_map=weld_map,
        project_profile="100200",
    )
    targets = {row.number: row for row in mapped if row.number in expected_numbers}

    assert set(targets) == expected_numbers
    assert {
        row.mapping_method for row in targets.values()
    } == {"pcf-multiport-fitting-bolt-interface"}
    assert len({tuple(round(value, 2) for value in row.anchor) for row in targets.values()}) == len(targets)


@pytest.mark.parametrize(
    ("sheet_id", "number", "expected_anchor"),
    [
        ("20-42-1600-26-HCL-8890-02", "FL18", (1374.24, 5276.88)),
        ("20-42-1600-26-MUA-8812-05", "FL4", (3341.8, 4307.24)),
        ("20-42-1600-26-MUA-7708-04", "FL15", (3220.56, 4121.04)),
        ("20-42-1600-26-MUA-7714-07", "FL15", (4202.52, 4672.92)),
    ],
)
def test_hidden_flange_neighbour_uses_visible_components_opposite_port(
    sheet_id: str,
    number: str,
    expected_anchor: tuple[float, float],
) -> None:
    """A hidden two-port neighbour is rooted at the visible component's free face."""

    import json

    from chengda_management_callouts import (
        forward_owned_uci_regions,
        resolve_management_targets,
    )
    from chengda_management_table import read_management_inputs

    unit = PROJECT_DIR / "SHA文件转换" / "201600"
    sha = unit / "201600 SHA" / f"{sheet_id}.sha"
    pcf = unit / "201600 0A PCF" / f"{sheet_id}.pcf"
    page_dir = unit / "水印及编号标注审计" / "pages" / f"{sheet_id}_1"
    trace = json.loads((page_dir / "trace.json").read_text(encoding="utf-8"))
    trace["legacy_uci_regions"] = list(trace.get("uci_regions", []))
    trace["uci_regions"] = forward_owned_uci_regions(sha, "Sheet6")
    weld_map = json.loads((page_dir / "weld-map.json").read_text(encoding="utf-8"))
    records = [
        row for row in read_management_inputs(
            flange_table=unit / "法兰表.csv", project_profile="100200"
        )
        if row.sheet_id == sheet_id and row.page == 1
    ]

    mapped, unresolved = resolve_management_targets(
        records,
        trace,
        pcf_path=pcf,
        weld_map=weld_map,
        project_profile="100200",
    )

    target = next(row for row in mapped if row.kind == "flange" and row.number == number)
    assert target.mapping_method == "pcf-visible-component-opposite-port"
    assert target.confidence == "review"
    assert target.anchor == pytest.approx(expected_anchor, abs=0.05)
    assert not any(row["kind"] == "flange" and row["number"] == number for row in unresolved)


def test_fully_regenerated_fpfl_chain_uses_unique_visible_terminal_seed() -> None:
    """A hidden FPFL chain is recoverable only from one exact terminal fitting path."""

    import json

    from chengda_management_callouts import (
        forward_owned_uci_regions,
        resolve_management_targets,
    )
    from chengda_management_table import read_management_inputs

    unit = PROJECT_DIR / "SHA文件转换" / "201600"
    sheet_id = "20-42-1600-26-WVH-8564-01"
    sha = unit / "201600 SHA" / f"{sheet_id}.sha"
    pcf = unit / "201600 0A PCF" / f"{sheet_id}.pcf"
    page_dir = unit / "水印及编号标注审计" / "pages" / f"{sheet_id}_1"
    trace = json.loads((page_dir / "trace.json").read_text(encoding="utf-8"))
    trace["legacy_uci_regions"] = list(trace.get("uci_regions", []))
    trace["uci_regions"] = forward_owned_uci_regions(sha, "Sheet6")
    weld_map = json.loads((page_dir / "weld-map.json").read_text(encoding="utf-8"))
    records = [
        row for row in read_management_inputs(
            flange_table=unit / "法兰表.csv", project_profile="100200"
        )
        if row.sheet_id == sheet_id and row.page == 1
    ]

    mapped, unresolved = resolve_management_targets(
        records,
        trace,
        pcf_path=pcf,
        weld_map=weld_map,
        project_profile="100200",
    )

    recovered = {
        row.number: row
        for row in mapped
        if row.kind == "flange"
        and row.mapping_method == "pcf-sha-fpfl-terminal-seed-reconciliation"
    }
    assert set(recovered) == {f"FL{index}" for index in range(1, 9)}
    assert all(row.confidence == "review" for row in recovered.values())
    assert len({tuple(round(value, 2) for value in row.anchor) for row in recovered.values()}) == 8
    assert not any(
        row["kind"] == "flange" and row["number"] in recovered
        for row in unresolved
    )


@pytest.mark.parametrize(
    ("sheet_id", "expected_numbers"),
    [
        (
            "20-42-1600-26-HCL-8890-02",
            {f"FL{index}" for index in range(1, 18)},
        ),
        (
            "20-42-1600-26-MUA-7714-07",
            {f"FL{index}" for index in range(1, 15)},
        ),
    ],
)
def test_partial_regenerated_fpfl_chain_uses_unique_anchored_subpath(
    sheet_id: str,
    expected_numbers: set[str],
) -> None:
    """A regenerated PIPE-FIXED chain can occupy a unique anchored subpath."""

    import json

    from chengda_management_callouts import (
        forward_owned_uci_regions,
        resolve_management_targets,
    )
    from chengda_management_table import read_management_inputs

    unit = PROJECT_DIR / "SHA文件转换" / "201600"
    sha = unit / "201600 SHA" / f"{sheet_id}.sha"
    pcf = unit / "201600 0A PCF" / f"{sheet_id}.pcf"
    page_dir = unit / "水印及编号标注审计" / "pages" / f"{sheet_id}_1"
    trace = json.loads((page_dir / "trace.json").read_text(encoding="utf-8"))
    trace["legacy_uci_regions"] = list(trace.get("uci_regions", []))
    trace["uci_regions"] = forward_owned_uci_regions(sha, "Sheet6")
    weld_map = json.loads((page_dir / "weld-map.json").read_text(encoding="utf-8"))
    records = [
        row for row in read_management_inputs(
            flange_table=unit / "法兰表.csv", project_profile="100200"
        )
        if row.sheet_id == sheet_id and row.page == 1
    ]

    mapped, unresolved = resolve_management_targets(
        records,
        trace,
        pcf_path=pcf,
        weld_map=weld_map,
        project_profile="100200",
    )

    recovered = {
        row.number: row
        for row in mapped
        if row.kind == "flange"
        and row.mapping_method == "pcf-sha-fpfl-anchored-subpath-reconciliation"
    }
    assert set(recovered) == expected_numbers
    assert all(row.confidence == "review" for row in recovered.values())
    assert len({tuple(round(value, 2) for value in row.anchor) for row in recovered.values()}) == len(recovered)
    assert not any(
        row["kind"] == "flange" and row["number"] in recovered
        for row in unresolved
    )


def test_hidden_single_flange_uses_visible_valves_proven_opposite_port() -> None:
    """A hidden literal flange can root at a uniquely oriented valve port."""

    import json

    from chengda_management_callouts import (
        forward_owned_uci_regions,
        resolve_management_targets,
    )
    from chengda_management_table import read_management_inputs

    unit = PROJECT_DIR / "SHA文件转换" / "201600"
    sheet_id = "20-42-1600-26-NG-0025-03"
    sha = unit / "201600 SHA" / f"{sheet_id}.sha"
    pcf = unit / "201600 0A PCF" / f"{sheet_id}.pcf"
    page_dir = unit / "水印及编号标注审计" / "pages" / f"{sheet_id}_1"
    trace = json.loads((page_dir / "trace.json").read_text(encoding="utf-8"))
    trace["legacy_uci_regions"] = list(trace.get("uci_regions", []))
    trace["uci_regions"] = forward_owned_uci_regions(sha, "Sheet6")
    weld_map = json.loads((page_dir / "weld-map.json").read_text(encoding="utf-8"))
    records = [
        row for row in read_management_inputs(
            flange_table=unit / "法兰表.csv", project_profile="100200"
        )
        if row.sheet_id == sheet_id and row.page == 1
    ]

    mapped, unresolved = resolve_management_targets(
        records, trace, pcf_path=pcf, weld_map=weld_map, project_profile="100200"
    )

    target = next(row for row in mapped if row.kind == "flange" and row.number == "FL3")
    assert target.mapping_method == "pcf-hidden-component-visible-neighbour-opposite-port"
    assert target.confidence == "review"
    assert target.anchor == pytest.approx((3402.0, 5638.08), abs=0.05)
    assert not any(row["kind"] == "flange" and row["number"] == "FL3" for row in unresolved)


def test_repeated_support_uci_uses_coordinate_projection_to_pick_instance(tmp_path: Path) -> None:
    """A duplicated UCI must be resolved by the support's 3D position on its pipe."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [{"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "25,0,0", "UCI": "{SUP}"}],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "PIPE\n"
        "    END-POINT 0 0 0 4\n"
        "    END-POINT 100 0 0 4\n"
        "    UCI {PIPE}\n"
        "SUPPORT\n"
        "    CO-ORDS 25 0 0 4\n"
        "    UCI {SUP}\n",
        encoding="utf-8",
    )
    trace = {
        "logical_page": 1,
        "uci_regions": [
            {"uci": "{SUP}", "graphic_ref": 10, "bbox": [390.0, 1660.0, 430.0, 1700.0]},
            {"uci": "{SUP}", "graphic_ref": 20, "bbox": [1240.0, 1660.0, 1280.0, 1700.0]},
        ],
        "segments": [{
            "sheet_points_normalized": [[0.0, 0.1], [0.1, 0.1]],
            "uci_candidates": ["{PIPE}"],
            "mapping_basis": "direct_object_ref",
        }],
    }
    record = read_management_inputs(support_table=support)[0]

    mapped, unresolved = resolve_management_targets([record], trace, pcf_path=pcf)

    assert unresolved == []
    assert mapped[0].anchor == (420.0, 1680.0)
    assert mapped[0].graphic_refs == (10,)
    assert mapped[0].mapping_method == "support-coordinate-pipe-projection"


def test_support_missing_from_sha_uses_coordinate_projection(tmp_path: Path) -> None:
    """A support whose own UCI is absent remains locatable through its pipe coordinate."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [{"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "25,0,0", "UCI": "{MISSING}"}],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "PIPE\n"
        "    END-POINT 0 0 0 4\n"
        "    END-POINT 100 0 0 4\n"
        "    UCI {PIPE}\n",
        encoding="utf-8",
    )
    trace = {
        "logical_page": 1,
        "uci_regions": [],
        "segments": [{
            "sheet_points_normalized": [[0.0, 0.1], [0.1, 0.1]],
            "uci_candidates": ["{PIPE}"],
            "mapping_basis": "direct_object_ref",
        }],
    }
    record = read_management_inputs(support_table=support)[0]

    mapped, unresolved = resolve_management_targets([record], trace, pcf_path=pcf)

    assert unresolved == []
    assert mapped[0].anchor == (420.0, 1680.0)
    assert mapped[0].graphic_refs == ()
    assert mapped[0].mapping_method == "support-coordinate-pipe-projection-no-sha-uci"


def test_support_missing_from_sha_projects_along_pipe_fixed(tmp_path: Path) -> None:
    """EP3D commonly stores the support-bearing run as PIPE-FIXED."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [{"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "25,0,0", "UCI": "{MISSING}"}],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "PIPE-FIXED\n"
        "    END-POINT 0 0 0 4\n"
        "    END-POINT 100 0 0 4\n"
        "    UCI {PIPE-FIXED-UCI}\n",
        encoding="utf-8",
    )
    trace = {
        "logical_page": 1,
        "uci_regions": [],
        "segments": [{
            "sheet_points_normalized": [[0.0, 0.1], [0.1, 0.1]],
            "uci_candidates": ["{PIPE-FIXED-UCI}"],
            "mapping_basis": "direct_object_ref",
        }],
    }
    record = read_management_inputs(support_table=support)[0]

    mapped, unresolved = resolve_management_targets([record], trace, pcf_path=pcf)

    assert unresolved == []
    assert mapped[0].anchor == (420.0, 1680.0)
    assert mapped[0].mapping_method == "support-coordinate-pipe-projection-no-sha-uci"


def test_multi_uci_support_with_missing_members_uses_coordinate_projection(tmp_path: Path) -> None:
    """A partially or wholly missing support assembly must not use flange-pair logic."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [
            {"管线号": "LINE-01", "页码": "1", "支架编号": "SP2", "支架坐标": "60,0,0", "UCI": "{A}"},
            {"管线号": "LINE-01", "页码": "1", "支架编号": "SP2", "支架坐标": "60,0,0", "UCI": "{B}"},
        ],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "PIPE\n"
        "    END-POINT 0 0 0 4\n"
        "    END-POINT 100 0 0 4\n"
        "    UCI {PIPE}\n",
        encoding="utf-8",
    )
    trace = {
        "logical_page": 1,
        "uci_regions": [{"uci": "{A}", "graphic_ref": 9, "bbox": [900.0, 1600.0, 940.0, 1640.0]}],
        "segments": [{
            "sheet_points_normalized": [[0.0, 0.1], [0.1, 0.1]],
            "uci_candidates": ["{PIPE}"],
            "mapping_basis": "direct_object_ref",
        }],
    }
    record = read_management_inputs(support_table=support)[0]

    mapped, unresolved = resolve_management_targets([record], trace, pcf_path=pcf)

    assert unresolved == []
    assert mapped[0].anchor == (1008.0, 1680.0)
    assert mapped[0].mapping_method == "support-coordinate-pipe-projection-partial-sha-uci"


def test_support_at_two_port_component_midpoint_uses_component_geometry(
    tmp_path: Path,
) -> None:
    """A support at a flange centre need not have an explicit CENTRE-POINT."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [{"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "5,0,0", "UCI": "{SUP}"}],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text(
        "PIPELINE-REFERENCE LINE-01\n"
        "FLANGE\n"
        "    END-POINT 0 0 0 4\n"
        "    END-POINT 10 0 0 4\n"
        "    UCI {FLANGE}\n",
        encoding="utf-8",
    )
    trace = {
        "logical_page": 1,
        "uci_regions": [
            {"uci": "{FLANGE}", "graphic_ref": 11, "bbox": [100.0, 200.0, 180.0, 260.0]}
        ],
        "segments": [],
    }
    record = read_management_inputs(support_table=support)[0]

    mapped, unresolved = resolve_management_targets([record], trace, pcf_path=pcf)

    assert unresolved == []
    assert mapped[0].anchor == (140.0, 230.0)
    assert mapped[0].mapping_method == "support-coordinate-component-centre-no-sha-uci"


def test_partial_support_uci_cluster_is_reviewable_when_coordinate_geometry_is_absent(
    tmp_path: Path,
) -> None:
    """One exact visible member is safer than dropping the whole support assembly."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [
            {"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "5,5,5", "UCI": "{VISIBLE}"},
            {"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "5,5,5", "UCI": "{MISSING}"},
        ],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text("PIPELINE-REFERENCE LINE-01\n", encoding="utf-8")
    trace = {
        "logical_page": 1,
        "uci_regions": [
            {"uci": "{VISIBLE}", "graphic_ref": 12, "bbox": [300.0, 400.0, 340.0, 440.0]}
        ],
        "segments": [],
    }
    record = read_management_inputs(support_table=support)[0]

    mapped, unresolved = resolve_management_targets([record], trace, pcf_path=pcf)

    assert unresolved == []
    assert mapped[0].anchor == (320.0, 420.0)
    assert mapped[0].mapping_method == "support-partial-uci-cluster"
    assert mapped[0].confidence == "review"


def test_partial_support_uci_with_two_distant_instances_stays_unresolved(
    tmp_path: Path,
) -> None:
    """A repeated visible UCI is not enough to choose one physical support."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [
            {"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "5,5,5", "UCI": "{VISIBLE}"},
            {"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "5,5,5", "UCI": "{MISSING}"},
        ],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text("PIPELINE-REFERENCE LINE-01\n", encoding="utf-8")
    trace = {
        "logical_page": 1,
        "uci_regions": [
            {"uci": "{VISIBLE}", "graphic_ref": 12, "bbox": [300.0, 400.0, 340.0, 440.0]},
            {"uci": "{VISIBLE}", "graphic_ref": 13, "bbox": [1300.0, 1400.0, 1340.0, 1440.0]},
        ],
        "segments": [],
    }
    record = read_management_inputs(support_table=support)[0]

    mapped, unresolved = resolve_management_targets([record], trace, pcf_path=pcf)

    assert mapped == []
    assert len(unresolved) == 1
    assert unresolved[0]["reason"] == "support UCI unavailable and coordinate projection failed"


def test_support_coordinate_can_use_one_unique_mapped_weld_root(tmp_path: Path) -> None:
    """An interface support remains locatable when its own SHA UCI is omitted."""

    from chengda_management_callouts import resolve_management_targets
    from chengda_management_table import read_management_inputs

    support = write_csv(
        tmp_path / "support.csv",
        ["管线号", "页码", "支架编号", "支架坐标", "UCI"],
        [{"管线号": "LINE-01", "页码": "1", "支架编号": "SP1", "支架坐标": "5,5,5", "UCI": "{MISSING}"}],
    )
    pcf = tmp_path / "LINE-01.pcf"
    pcf.write_text("PIPELINE-REFERENCE LINE-01\n", encoding="utf-8")
    trace = {"logical_page": 1, "uci_regions": [], "segments": []}
    weld_map = {
        "welds": [
            {
                "page": 1,
                "graphic_ref": 99,
                "sha_x": 700.0,
                "sha_y": 800.0,
                "pcf_endpoint": [5.002, 5.0, 5.0, 4.0],
            }
        ]
    }
    record = read_management_inputs(support_table=support)[0]

    mapped, unresolved = resolve_management_targets(
        [record], trace, pcf_path=pcf, weld_map=weld_map, project_profile="100200"
    )

    assert unresolved == []
    assert mapped[0].anchor == (700.0, 800.0)
    assert mapped[0].mapping_method == "support-coordinate-unique-weld-root"
    assert mapped[0].confidence == "review"


def test_svg_management_callout_is_tight_blue_rectangle(tmp_path: Path) -> None:
    """Regression guard against circular, red, or fixed-width management tags."""

    from chengda_management_callouts import ManagementTarget, overlay_management_callouts

    svg = tmp_path / "page.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000"></svg>',
        encoding="utf-8",
    )
    target = ManagementTarget(
        kind="valve",
        number="V123",
        sheet_id="LINE-01",
        page=1,
        ucis=("{V}",),
        anchor=(100.0, 100.0),
        mapping_method="direct-uci-region",
        confidence="high",
        graphic_refs=(7,),
    )

    overlay_management_callouts(
        svg,
        [target],
        placements={"valve:V123": (300.0, 300.0)},
    )

    root = ET.parse(svg).getroot()
    nodes = {node.attrib.get("data-layer"): node for node in root.iter() if node.attrib.get("data-layer")}
    box = nodes["sha-management-marker"]
    text = nodes["sha-management-label"]
    leader = nodes["sha-management-leader"]
    assert box.tag.rsplit("}", 1)[-1] == "rect"
    assert box.attrib["stroke"] == text.attrib["fill"] == leader.attrib["stroke"] == "#0066ff"
    assert float(box.attrib["width"]) > float(box.attrib["height"])
    assert float(box.attrib["width"]) < 400.0
    assert box.attrib["fill"] == "none"
    assert "SimHei" in text.attrib["font-family"]
    assert float(text.attrib["font-size"]) == 88.0
    assert "V123" == "".join(text.itertext())


def test_svg_spool_callout_is_red_double_rectangle(tmp_path: Path) -> None:
    """Spool tags use their own red, double-line rectangular convention."""

    from chengda_management_callouts import ManagementTarget, overlay_management_callouts

    svg = tmp_path / "page.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2000 1000"></svg>',
        encoding="utf-8",
    )
    target = ManagementTarget(
        kind="spool",
        number="200301-WT-3603-SP05",
        sheet_id="LINE-01",
        page=1,
        ucis=(),
        anchor=(100.0, 100.0),
        mapping_method="shop-weld-adjacent-component",
        confidence="high",
        graphic_refs=(),
    )

    overlay_management_callouts(
        svg,
        [target],
        placements={target.key: (700.0, 300.0)},
    )

    root = ET.parse(svg).getroot()
    by_layer: dict[str, list[ET.Element]] = {}
    for node in root.iter():
        layer = node.attrib.get("data-layer")
        if layer:
            by_layer.setdefault(layer, []).append(node)
    outer = by_layer["sha-management-marker"][0]
    inner = by_layer["sha-spool-marker-inner"][0]
    text = by_layer["sha-management-label"][0]
    leader = by_layer["sha-management-leader"][0]
    assert outer.tag.rsplit("}", 1)[-1] == inner.tag.rsplit("}", 1)[-1] == "rect"
    assert outer.attrib["stroke"] == inner.attrib["stroke"] == "#ff0000"
    assert outer.attrib["fill"] == inner.attrib["fill"] == "none"
    assert text.attrib["fill"] == leader.attrib["stroke"] == "#ff0000"
    assert float(inner.attrib["width"]) < float(outer.attrib["width"])
    assert float(inner.attrib["height"]) < float(outer.attrib["height"])
    assert float(text.attrib["font-size"]) == 64.0
    assert target.number == "".join(text.itertext())


def test_spool_box_uses_character_widths_instead_of_fixed_length_multiplier() -> None:
    """Digit/hyphen-heavy spool ids should not inherit an over-wide fixed box."""

    from chengda_management_callouts import label_box

    number = "200301-WT-3603-SP05"
    width, height = label_box(number, "spool")
    old_fixed_width = 64.0 * 0.68 * len(number) + 120.0 + 28.0

    assert width < old_fixed_width * 0.90
    assert width > 64.0 * 0.45 * len(number)
    assert height > 64.0


def test_rectangular_candidate_uses_real_width_and_height() -> None:
    """Global solver collision boxes must match the emitted rectangular frame."""

    from inject_sha_weld_callouts import LayoutRegion, PointTarget, candidate_options

    point = PointTarget(graphic_ref=1, uci="spool:1", center_x=1000, center_y=1000, page=1)
    options = candidate_options(
        point=point,
        source_segments=[],
        source_boxes=[],
        leader_boxes=[],
        region=LayoutRegion((0, 0, 5000, 5000), (0, 0, 5000, 5000), "test"),
        marker_radius=1.0,
        marker_width=800.0,
        marker_height=120.0,
        gap=40.0,
        normal=(1.0, 0.0),
        marker_shape="rectangle",
        max_candidates=8,
    )

    assert options
    left, bottom, right, top = options[0].marker_box
    assert right - left == 880.0
    assert top - bottom == 200.0


def test_preferred_length_band_is_used_before_short_or_long_fallbacks() -> None:
    """A feasible normal leader length wins; shortest is not the objective."""

    from inject_sha_weld_callouts import (
        CalloutPlacement,
        PlacementCandidate,
        PointTarget,
        solve_page_layout_in_preferred_bands,
    )

    point = PointTarget(1, "weld:1", 0.0, 0.0, 1)

    def option(distance: float) -> PlacementCandidate:
        return PlacementCandidate(
            placement=CalloutPlacement(
                center_x=distance,
                center_y=0.0,
                leader_x=distance - 100.0,
                leader_y=0.0,
                number="F1",
                score=distance,
                marker_radius=100.0,
            ),
            marker_box=(distance - 100.0, -100.0, distance + 100.0, 100.0),
        )

    audit: dict[str, object] = {}
    placements = solve_page_layout_in_preferred_bands(
        [point],
        [[option(400.0), option(800.0), option(2400.0)]],
        [(650.0, 1050.0)],
        layout_audit=audit,
    )

    assert placements[0].center_x == 800.0
    assert audit["leader_length_stage"] == "preferred"


def test_spool_anchor_prefers_pcf_adjacent_uci_geometry_over_nearby_annotation_line() -> None:
    """HTV SP02 must root on its flange, not the closer unowned drawing leader."""

    import json
    import pytest

    from chengda_management_callouts import resolve_spool_targets
    from chengda_spool_table import read_spool_inputs

    unit = PROJECT_DIR / "SHA文件转换" / "200301"
    sheet_id = "20-42-0301-26-HTV-3123-03"
    records = [
        row
        for row in read_spool_inputs(
            unit / "200301焊口表-壁厚材质处理-带特性_ISO对照结果.xlsx",
            "国内预制管段号",
        )
        if row.sheet_id == sheet_id and row.number == "200301-HTV-3123-SP02"
    ]
    weld_map = json.loads(
        (
            unit
            / "水印及编号标注审计"
            / "pages"
            / f"{sheet_id}_1"
            / "weld-map.json"
        ).read_text(encoding="utf-8")
    )

    targets, unresolved = resolve_spool_targets(
        records,
        weld_map,
        unit / "200301 SHA" / f"{sheet_id}.sha",
        "Sheet6",
        1,
        pcf_path=unit / "PCF-合并输入" / f"{sheet_id}.pcf",
    )

    assert unresolved == []
    assert len(targets) == 1
    assert targets[0].mapping_method == "mapped-shop-weld-pcf-adjacent-component-line"
    assert targets[0].anchor == pytest.approx((1093.5088248199738, 4653.530288310118))


def test_first_dynamic_flange_uses_stream_prefix_reference(tmp_path: Path) -> None:
    """FL2's first-record UCI must resolve directly instead of by remainder."""

    import json

    from chengda_management_callouts import (
        forward_owned_uci_regions,
        resolve_management_targets,
    )
    from chengda_management_table import read_management_inputs
    from chengda_weld_mapping import build_weld_map

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-10FW41001-02"
    sha = base / "000200FW" / f"{sheet_id}.sha"
    pcf = base / "000200FW 0-PCF-焊口反写测试" / f"{sheet_id}.pcf"
    weld_map_path = tmp_path / "weld-map.json"
    build_weld_map(pcf, sha, weld_map_path)
    weld_map = json.loads(weld_map_path.read_text(encoding="utf-8"))
    records = [
        row
        for row in read_management_inputs(
            base / "支架表.csv", base / "阀门表.csv", base / "法兰表.csv"
        )
        if row.sheet_id == sheet_id and row.page == 1
    ]
    trace = {
        "uci_regions": forward_owned_uci_regions(sha, "Sheet6"),
        "segments": [],
    }

    targets, unresolved = resolve_management_targets(
        records, trace, pcf_path=pcf, weld_map=weld_map
    )
    by_key = {target.key: target for target in targets}

    assert unresolved == []
    assert by_key["flange:FL2"].anchor == (2200.48828125, 4024.91455078125)
    assert by_key["flange:FL2"].mapping_method == "direct-uci-region"


def test_first_dynamic_uci_uses_stream_prefix_reference_for_valve_v1() -> None:
    """The first UCI owns the reference immediately before its first marker."""

    from chengda_management_callouts import (
        forward_owned_uci_regions,
        resolve_management_targets,
    )
    from chengda_management_table import read_management_inputs

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-30IA05101-02"
    sha = base / "000200FW" / f"{sheet_id}.sha"
    records = [
        row
        for row in read_management_inputs(valve_table=base / "阀门表.csv")
        if row.sheet_id == sheet_id and row.page == 1
    ]
    regions = forward_owned_uci_regions(sha, "Sheet6")
    v1_uci = "{00013885-0000-0000-B01B-623BCD685004}"
    v1_regions = [row for row in regions if row["uci"] == v1_uci]

    assert len(v1_regions) == 1
    assert v1_regions[0]["graphic_ref"] == 0x1655
    assert v1_regions[0]["bbox"] == [
        3792.919921875,
        3873.4130859375,
        3914.4287109375,
        4005.17578125,
    ]

    targets, unresolved = resolve_management_targets(records, {"uci_regions": regions})
    by_key = {target.key: target for target in targets}
    assert unresolved == []
    assert by_key["valve:V1"].mapping_method == "direct-uci-region"
    assert by_key["valve:V1"].anchor == (3853.67431640625, 3939.29443359375)


def test_real_page_management_tags_use_existing_non_crossing_layout() -> None:
    """The converter must plan every mapped tag instead of using fixed offsets."""

    import json

    from chengda_management_callouts import plan_management_layout, resolve_management_targets
    from chengda_management_table import read_management_inputs

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-10FW41001-01"
    records = [
        row
        for row in read_management_inputs(
            base / "支架表.csv", base / "阀门表.csv", base / "法兰表.csv"
        )
        if row.sheet_id == sheet_id and row.page == 1
    ]
    trace = json.loads(
        (base / "焊口标注样例审计" / "pages" / f"{sheet_id}_1" / "trace.json").read_text(
            encoding="utf-8"
        )
    )
    targets, unresolved = resolve_management_targets(
        records,
        trace,
        pcf_path=base / "000200FW 0-PCF-焊口反写测试" / f"{sheet_id}.pcf",
    )

    placements = plan_management_layout(
        base / "000200FW" / f"{sheet_id}.sha",
        page=1,
        sheet_stream="Sheet6",
        targets=targets,
    )

    assert unresolved == []
    assert set(placements) == {target.key for target in targets}
    assert len(set((round(x), round(y)) for x, y in placements.values())) == len(targets)


def test_000206_weld_management_and_spool_tags_share_one_layout_graph() -> None:
    """跨类型标注必须和焊口一起参与同一个页面级冲突求解。"""

    import json

    from chengda_management_callouts import (
        ManagementTarget,
        _rectangle_edge,
        label_box,
        plan_unified_page_layout,
    )
    from inject_sha_weld_callouts import boxes_overlap, segments_intersect

    unit = PROJECT_DIR / "SHA文件转换" / "000206"
    sheet_id = "00-42-0206-26-00CNS02100-01"
    page_dir = unit / "水印及编号标注审计" / "pages" / f"{sheet_id}_1"
    weld_payload = json.loads((page_dir / "weld-map.json").read_text(encoding="utf-8"))
    weld_rows = [
        row
        for row in weld_payload["welds"]
        if row.get("weld_number")
        and int(row.get("page", 999999)) == 1
        and row.get("graphic_ref") is not None
    ]
    management_payload = json.loads(
        (page_dir / "management-callouts.json").read_text(encoding="utf-8")
    )
    targets = [
        ManagementTarget(
            kind=str(row["kind"]),
            number=str(row["number"]),
            sheet_id=str(row["sheet_id"]),
            page=int(row["page"]),
            ucis=tuple(row["ucis"]),
            anchor=tuple(row["anchor"]),
            mapping_method=str(row["mapping_method"]),
            confidence=str(row["confidence"]),
            graphic_refs=tuple(row["graphic_refs"]),
        )
        for row in management_payload["callouts"]
    ]
    audit: dict[str, object] = {}
    weld_placements, management_placements = plan_unified_page_layout(
        unit / "000206 SHA" / f"{sheet_id}.sha",
        page=1,
        sheet_stream="Sheet6",
        weld_rows=weld_rows,
        management_targets=targets,
        layout_audit=audit,
    )

    callouts = []
    for row, placement in zip(weld_rows, weld_placements):
        radius = float(placement.marker_radius)
        callouts.append(
            (
                (
                    placement.center_x - radius,
                    placement.center_y - radius,
                    placement.center_x + radius,
                    placement.center_y + radius,
                ),
                (
                    float(row["sha_x"]),
                    float(row["sha_y"]),
                    placement.leader_x,
                    placement.leader_y,
                ),
            )
        )
    for target in targets:
        center = management_placements[target.key]
        width, height = label_box(target.number, target.kind)
        edge = _rectangle_edge(target.anchor, center, width, height)
        callouts.append(
            (
                (
                    center[0] - width / 2,
                    center[1] - height / 2,
                    center[0] + width / 2,
                    center[1] + height / 2,
                ),
                (*target.anchor, *edge),
            )
        )

    assert audit["layout_scope"] == "weld-management-spool-joint"
    assert audit["layout_mode"] in {"strict", "compact"}
    for first_index, (first_box, first_leader) in enumerate(callouts):
        for second_box, second_leader in callouts[first_index + 1 :]:
            assert not boxes_overlap(first_box, second_box)
            assert not segments_intersect(first_leader, second_leader)


def test_real_page_components_use_forward_owned_sha_graphics() -> None:
    """Legacy same-record UCI ownership shifts every management root to its neighbour."""

    import json

    from chengda_management_callouts import (
        forward_owned_uci_regions,
        resolve_management_targets,
    )
    from chengda_management_table import read_management_inputs

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-10FW41001-01"
    sha = base / "000200FW" / f"{sheet_id}.sha"
    records = [
        row
        for row in read_management_inputs(
            base / "支架表.csv", base / "阀门表.csv", base / "法兰表.csv"
        )
        if row.sheet_id == sheet_id and row.page == 1
    ]
    trace = json.loads(
        (base / "焊口标注样例审计" / "pages" / f"{sheet_id}_1" / "trace.json").read_text(
            encoding="utf-8"
        )
    )
    trace["uci_regions"] = forward_owned_uci_regions(sha, "Sheet6")

    targets, unresolved = resolve_management_targets(
        records,
        trace,
        pcf_path=base / "000200FW 0-PCF-焊口反写测试" / f"{sheet_id}.pcf",
    )
    anchors = {target.key: target.anchor for target in targets}

    assert unresolved == []
    assert anchors["flange:FL1"] == (5549.15771484375, 1952.08740234375)
    assert anchors["flange:FL2"] == (2890.0634765625, 4622.9736328125)
    assert anchors["flange:FL3"] == (2666.5283203125, 4750.89111328125)
    assert anchors["flange:FL4"] == (1047.18017578125, 5682.97119140625)
    assert anchors["flange:FL5"] == (1870.3125, 5899.84130859375)
    assert anchors["valve:V1"] == (2779.06494140625, 4687.82958984375)
    assert anchors["valve:V2"] == (1851.85546875, 6005.712890625)
    assert anchors["support:SP1"] == (4225.634765625, 3855.21240234375)


def test_converter_emits_management_pdf_and_audit(tmp_path: Path) -> None:
    """Dropping the management overlay from the PDF pipeline must fail."""

    import json

    from chengda_management_table import read_management_inputs
    from convert_chengda_weld_sha_to_pdf import WeldPageJob, convert_weld_job

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-10FW41001-01"
    records = [
        row
        for row in read_management_inputs(
            base / "支架表.csv", base / "阀门表.csv", base / "法兰表.csv"
        )
        if row.sheet_id == sheet_id and row.page == 1
    ]
    job = WeldPageJob(
        sheet_id,
        base / "000200FW" / f"{sheet_id}.sha",
        base / "000200FW 0-PCF" / f"{sheet_id}.pcf",
        1,
        "Sheet6",
    )

    result = convert_weld_job(
        job,
        tmp_path / "pdf",
        tmp_path / "audit",
        force=True,
        management_records=records,
    )

    audit = json.loads(result.management_audit_path.read_text(encoding="utf-8"))
    assert result.management_callout_count == 8
    assert audit["mapped_count"] == 8
    assert audit["unresolved_count"] == 0
    assert result.pdf_path.read_bytes().startswith(b"%PDF-")
