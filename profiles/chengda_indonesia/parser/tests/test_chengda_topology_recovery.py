from decimal import Decimal
from pathlib import Path
import struct
import sys
from types import SimpleNamespace


PROFILE_DIR = Path(__file__).resolve().parents[2]
PARSER_DIR = PROFILE_DIR / "parser"
for directory in (PROFILE_DIR / "workflow", PARSER_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from chengda_construction_welds import ConstructionWeldInput
import chengda_weld_mapping as weld_mapping
from chengda_management_callouts import resolve_management_targets
from chengda_management_table import ManagementInput


def mock_visible_page(monkeypatch):
    """These topology-only fixtures have no OLE graphics; supply their viewport."""
    monkeypatch.setattr(weld_mapping, "read_sha_streams", lambda _: {})
    monkeypatch.setattr(weld_mapping, "sheet_streams", lambda *_: [SimpleNamespace(page=1, data=b"")])
    monkeypatch.setattr(weld_mapping, "sheet_viewbox", lambda *_: (0, 0, 16800, 12000))
    monkeypatch.setattr(weld_mapping, "infer_layout_region", lambda *_: SimpleNamespace(drawing=(0, 0, 16800, 12000)))


def test_first_dynamic_uci_uses_serialized_prefix_graphic_reference(
    monkeypatch,
) -> None:
    payload = bytearray(128)
    record_start = 20
    marker_start = record_start + 5
    marker = b"PipeLine Info\x00"
    payload[marker_start : marker_start + len(marker)] = marker
    uci = b"{00013885-0000-0000-902C-77807C675204}"
    uci_start = marker_start + len(marker) + 4
    payload[uci_start : uci_start + len(uci)] = uci
    struct.pack_into("<I", payload, record_start - 20, 0x11223344)
    struct.pack_into("<I", payload, record_start - 8, 0x00001778)
    monkeypatch.setattr(
        weld_mapping,
        "forward_owned_dynamic_graphics",
        lambda _payload: {},
    )

    decoded = weld_mapping._dynamic_graphics_including_first_record(
        bytes(payload)
    )

    assert decoded[uci.decode()][0]["space_ref"] == 0x11223344
    assert decoded[uci.decode()][0]["graphic_ref"] == 0x00001778


def test_double_line_interface_edges_collapse_to_cross_section_centre() -> None:
    best = [
        (0.0, (5607.822, 3070.026), (5607.822, 3070.026)),
        (0.0, (5633.022, 3054.906), (5633.022, 3054.906)),
    ]

    assert weld_mapping._unique_interface_point(best) == (5620.4, 3062.5)


def test_separate_interface_candidates_are_not_collapsed() -> None:
    best = [
        (0.0, (100.0, 100.0), (100.0, 100.0)),
        (0.0, (200.0, 200.0), (200.0, 200.0)),
    ]

    assert weld_mapping._unique_interface_point(best) is None


def test_connection_reference_adds_only_declared_neighbour_ports(
    tmp_path: Path,
) -> None:
    current = tmp_path / "BRANCH-01.pcf"
    current.write_text(
        """PIPELINE-REFERENCE BRANCH
PIPE-FIXED
    COMPONENT-IDENTIFIER 1
    END-POINT 0 0 100 1.5
    END-POINT 0 0 0 1.5
    UCI {NIPPLE}
END-CONNECTION-PIPELINE
    CO-ORDS 10 0 110
    CONNECTION-REFERENCE UNIT$HEADER-01
""",
        encoding="utf-8",
    )
    neighbour = tmp_path / "HEADER-01.pcf"
    neighbour.write_text(
        """PIPELINE-REFERENCE HEADER
TEE
    COMPONENT-IDENTIFIER 2
    END-POINT -10 0 110 2
    END-POINT 10 0 110 2
    CENTRE-POINT 0 0 110
    BRANCH1-POINT 0 0 100 1.5
    UCI {TEE}
""",
        encoding="utf-8",
    )
    unrelated = tmp_path / "UNRELATED.pcf"
    unrelated.write_text(
        """PIPELINE-REFERENCE OTHER
FLANGE
    COMPONENT-IDENTIFIER 3
    END-POINT 0 0 100 1.5
    END-POINT 0 0 90 1.5
    UCI {UNRELATED}
""",
        encoding="utf-8",
    )
    source_lines, _ = weld_mapping.parse_weld_blocks(current)

    topology_lines = weld_mapping._connection_topology_source_lines(
        current, source_lines
    )
    members = weld_mapping._component_ports_near_coordinate(
        weld_mapping._component_ports_at_coordinates(topology_lines),
        (0, 0, 100),
        tolerance=0.1,
    )

    assert {(kind, uci) for kind, uci, _, _ in members} == {
        ("PIPE-FIXED", "{NIPPLE}"),
        ("TEE", "{TEE}"),
    }


def _construction_record(
    number: str,
    row: int,
    coordinate: tuple[str, str, str],
    material_a: str,
    material_b: str,
) -> ConstructionWeldInput:
    return ConstructionWeldInput(
        excel_row=row,
        sheet_id="10-42-0200-26-10BP02000-07",
        weld_number=number,
        weld_type="F",
        coordinate=tuple(Decimal(value) for value in coordinate),
        original_weld_number=number,
        material_uci_a=material_a,
        material_uci_b=material_b,
        number_source="焊点编号",
    )


def test_pipe_internal_split_ignores_explicit_weld_records_when_orienting_run(
    tmp_path: Path, monkeypatch
) -> None:
    mock_visible_page(monkeypatch)
    pcf = tmp_path / "split.pcf"
    pcf.write_text(
        """PIPELINE-REFERENCE TEST
FLANGE
    COMPONENT-IDENTIFIER 1
    END-POINT 0 0 0 18
    END-POINT -10 0 0 18
    UCI {LEFT-FLANGE}
WELD
    COMPONENT-IDENTIFIER 2
    END-POINT 0 0 0 18
    UCI {LEFT-WELD}
PIPE
    COMPONENT-IDENTIFIER 3
    END-POINT 0 0 0 18
    END-POINT 7220 0 0 18
    UCI {PIPE-RUN}
WELD
    COMPONENT-IDENTIFIER 4
    END-POINT 7220 0 0 18
    UCI {RIGHT-WELD}
FLANGE
    COMPONENT-IDENTIFIER 5
    END-POINT 7220 0 0 18
    END-POINT 7230 0 0 18
    UCI {RIGHT-FLANGE}
""",
        encoding="utf-8",
    )
    sha = tmp_path / "split.sha"
    sha.write_bytes(b"fixture")
    record = _construction_record(
        "F26", 10, ("6000", "0", "0"), "PIPE-A", "PIPE-B"
    )
    captured = []

    monkeypatch.setattr(
        weld_mapping, "sha_implicit_pipe_end_weld_points", lambda *args, **kwargs: {}
    )

    def virtual_points(_sha, welds, _known, overrides):
        captured.extend(welds)
        bracket = overrides[welds[0].uci]["endpoint_bracket"]
        fraction = welds[0].owner_fraction
        start, end = bracket["start"], bracket["end"]
        return {
            weld.uci: {
                "page": 1,
                "graphic_ref": 123,
                "sha_x": start[0] + fraction * (end[0] - start[0]),
                "sha_y": start[1] + fraction * (end[1] - start[1]),
            }
            for weld in welds
        }

    monkeypatch.setattr(weld_mapping, "sha_virtual_weld_points", virtual_points)

    payload = {
        "welds": [
            {
                "uci": "{LEFT-WELD}",
                "pcf_endpoint": [0.0, 0.0, 0.0, 18.0],
                "page": 1,
                "graphic_ref": 100,
                "sha_x": 100.0,
                "sha_y": 200.0,
            },
            {
                "uci": "{RIGHT-WELD}",
                "pcf_endpoint": [7220.0, 0.0, 0.0, 18.0],
                "page": 1,
                "graphic_ref": 101,
                "sha_x": 300.0,
                "sha_y": 400.0,
            },
        ]
    }
    output = weld_mapping.add_construction_virtual_targets(payload, [record], pcf, sha)

    assert len(captured) == 1
    assert captured[0].owner_uci == "{PIPE-RUN}"
    assert captured[0].neighbor_uci_a == "{LEFT-FLANGE}"
    assert captured[0].neighbor_uci_b == "{RIGHT-FLANGE}"
    assert captured[0].owner_fraction == 6000 / 7220
    recovered = next(row for row in output["welds"] if row.get("construction_virtual_target"))
    assert recovered["sha_x"] == 100.0 + (6000 / 7220) * 200.0
    assert recovered["sha_y"] == 200.0 + (6000 / 7220) * 200.0
    assert output["construction_virtual_target_summary"]["collinear_pipe_run_count"] == 1


def test_olet_main_and_branch_interfaces_become_two_distinct_virtual_welds(
    tmp_path: Path, monkeypatch
) -> None:
    mock_visible_page(monkeypatch)
    pcf = tmp_path / "olet.pcf"
    pcf.write_text(
        """PIPELINE-REFERENCE TEST
PIPE
    COMPONENT-IDENTIFIER 1
    END-POINT 88000 42130 4525 18
    END-POINT 89100 42130 4525 18
    UCI {MAIN-PIPE}
OLET
    COMPONENT-IDENTIFIER 2
    CENTRE-POINT 89100.003 42130 4525 18
    BRANCH1-POINT 89100.003 42130 4287.45 1
    UCI {OLET}
PIPE
    COMPONENT-IDENTIFIER 3
    END-POINT 89100.003 42130 4287.45 1
    END-POINT 89100.003 42130 4120.48 1
    UCI {BRANCH-PIPE}
""",
        encoding="utf-8",
    )
    sha = tmp_path / "olet.sha"
    sha.write_bytes(b"fixture")
    records = [
        _construction_record(
            "F40", 20, ("89100", "42130", "4525"), "MAIN", "SADDLE"
        ),
        _construction_record(
            "F41", 21, ("89100", "42130", "4287.45"), "SADDLE", "BRANCH"
        ),
    ]
    captured = []

    monkeypatch.setattr(
        weld_mapping, "sha_implicit_pipe_end_weld_points", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(weld_mapping, "sha_virtual_weld_points", lambda *args: {})

    def interface_points(_sha, _lines, welds):
        captured.extend(welds)
        return {
            weld.uci: {
                "page": 1,
                "graphic_ref": 200 + index,
                "sha_x": 1000.0 + index * 100,
                "sha_y": 500.0 + index * 100,
                "point_evidence": "pcf-topology-two-sided-component-junction",
            }
            for index, weld in enumerate(welds)
        }

    monkeypatch.setattr(
        weld_mapping, "_sha_two_sided_component_junctions", interface_points
    )

    output = weld_mapping.add_construction_virtual_targets(
        {"welds": []}, records, pcf, sha
    )

    assert [weld.repeat_weld_identifier for weld in captured] == ["F40", "F41"]
    assert [tuple(weld.endpoint[:3]) for weld in captured] == [
        (89100.0, 42130.0, 4525.0),
        (89100.0, 42130.0, 4287.45),
    ]
    accepted = [row for row in output["welds"] if row["construction_virtual_target"]]
    assert len(accepted) == 2
    assert {row["construction_virtual_rule"] for row in accepted} == {
        "construction-table-component-interface"
    }
    assert len({(row["sha_x"], row["sha_y"]) for row in accepted}) == 2


def test_support_at_unique_elbow_centre_uses_component_uci_region(tmp_path: Path) -> None:
    pcf = tmp_path / "support-at-elbow.pcf"
    pcf.write_text(
        """PIPELINE-REFERENCE TEST
ELBOW
    COMPONENT-IDENTIFIER 9
    END-POINT 12900 43450 867 6
    END-POINT 12813 43450 780 6
    CENTRE-POINT 12900 43450 780
    UCI {ELBOW-UCI}
SUPPORT
    CO-ORDS 12900 43450 780 6
    UCI {SUPPORT-A}
SUPPORT
    CO-ORDS 12900 43450 780 6
    UCI {SUPPORT-B}
""",
        encoding="utf-8",
    )
    record = ManagementInput(
        kind="support",
        number="SP3",
        sheet_id="TEST",
        page=1,
        ucis=("{SUPPORT-A}", "{SUPPORT-B}"),
        coordinate=(12900.0, 43450.0, 780.0),
        source_rows=(77, 78),
    )
    trace = {
        "uci_regions": [
            {
                "uci": "{ELBOW-UCI}",
                "graphic_ref": 32834,
                "bbox": [4963.0, 4340.0, 5155.0, 4660.0],
            }
        ],
        "segments": [],
    }

    mapped, unresolved = resolve_management_targets(
        [record], trace, pcf_path=pcf, project_profile="100200"
    )

    assert unresolved == []
    assert len(mapped) == 1
    assert mapped[0].mapping_method == "support-coordinate-component-centre-no-sha-uci"
    assert mapped[0].anchor == (5059.0, 4500.0)
    assert mapped[0].graphic_refs == (32834,)


def test_support_without_sha_uci_uses_pcf_pipe_coordinate_projection(
    tmp_path: Path,
) -> None:
    pcf = tmp_path / "support-on-pipe.pcf"
    pcf.write_text(
        """PIPELINE-REFERENCE TEST
PIPE
    COMPONENT-IDENTIFIER 1
    END-POINT 0 0 0 6
    END-POINT 10 0 0 6
    UCI {PIPE-UCI}
SUPPORT
    CO-ORDS 5 0 0 6
    UCI {SUPPORT-UCI}
""",
        encoding="utf-8",
    )
    record = ManagementInput(
        kind="support",
        number="SP1",
        sheet_id="TEST",
        page=1,
        ucis=("{SUPPORT-UCI}",),
        coordinate=(5.0, 0.0, 0.0),
        source_rows=(10,),
    )
    trace = {
        "uci_regions": [],
        "segments": [
            {
                "uci_candidates": ["{PIPE-UCI}"],
                "mapping_basis": "direct_object_ref",
                "sheet_points_normalized": [[1.0, 2.0], [3.0, 4.0]],
            }
        ],
    }

    mapped, unresolved = resolve_management_targets(
        [record], trace, pcf_path=pcf, project_profile="100200"
    )

    assert unresolved == []
    assert len(mapped) == 1
    assert mapped[0].mapping_method == "support-coordinate-pipe-projection-no-sha-uci"
    assert mapped[0].anchor == (33600.0, 50400.0)
    assert mapped[0].graphic_refs == ()
