from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR


PARSER_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = DATA_DIR
PROGRAM_DIR = CODE_PROGRAM_DIR
WORKSPACE_DIR = SHARED_DIR
for candidate in (PARSER_DIR, PROGRAM_DIR, PROJECT_DIR, WORKSPACE_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


def test_natural_weld_key_accepts_alphanumeric_labels() -> None:
    from chengda_weld_mapping import natural_weld_key

    labels = ["T66", "F10", "FS1", "F2"]
    assert sorted(labels, key=natural_weld_key) == ["F2", "F10", "FS1", "T66"]


def test_wr3279_reducer_welds_use_distinct_sha_roots(tmp_path: Path) -> None:
    """S2 belongs to the reducer's opposite face and must not share F3's root."""

    from chengda_construction_welds import apply_construction_welds, read_iso_review_weld_inputs
    from chengda_weld_mapping import build_weld_map

    unit = PROJECT_DIR / "SHA文件转换" / "200301"
    sheet_id = "20-42-0301-26-WR-3279-01"
    output = tmp_path / "weld-map.json"
    build_weld_map(
        unit / "200301 0A PCF" / f"{sheet_id}.pcf",
        unit / "200301 SHA" / f"{sheet_id}.sha",
        output,
        include_unnumbered_topology=True,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    records, _ = read_iso_review_weld_inputs(
        unit / "200301焊口表-壁厚材质处理-带特性_ISO对照结果.xlsx"
    )
    selected = [row for row in records if row.sheet_id == sheet_id]
    payload, unresolved = apply_construction_welds(payload, selected)
    by_number = {
        row["weld_number"]: row
        for row in payload["welds"]
        if row.get("weld_number") in {"F1", "S2", "F3"}
    }

    assert unresolved == []
    roots = {
        number: (round(float(row["sha_x"]), 1), round(float(row["sha_y"]), 1))
        for number, row in by_number.items()
    }
    assert len(set(roots.values())) == 3
    assert roots["S2"] == (5633.0, 3141.6)
    assert roots["F3"] == (1349.0, 5614.6)


def test_long_pipe_distribution_accepts_consecutive_alphanumeric_labels() -> None:
    from verify_sha_weld_callouts import distribution_number_sequence_is_valid

    assert distribution_number_sequence_is_valid(["F10", "F11", "F12"])
    assert distribution_number_sequence_is_valid(["FS7", "FS8"])
    assert not distribution_number_sequence_is_valid(["F10", "F12"])
    assert not distribution_number_sequence_is_valid(["F10", "FS11"])


def test_build_weld_map_uses_repeat_identifier_from_real_chengda_sha(tmp_path: Path) -> None:
    from chengda_weld_mapping import build_weld_map

    source_pcf = PROJECT_DIR / "SHA文件转换" / "000200FW 0-PCF" / "00-42-0200-26-10FW41001-01.pcf"
    source_sha = PROJECT_DIR / "SHA文件转换" / "000200FW" / "00-42-0200-26-10FW41001-01.sha"
    modified_pcf = tmp_path / source_pcf.name
    raw = source_pcf.read_bytes()
    modified_pcf.write_bytes(raw.replace(b"REPEAT-WELD-IDENTIFIER    0", b"REPEAT-WELD-IDENTIFIER    F6", 1))
    output = tmp_path / "weld-map.json"

    summary = build_weld_map(modified_pcf, source_sha, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    numbered = [row for row in payload["welds"] if row.get("weld_number") == "F6"]
    assert summary.callout_target_count == 1
    assert len(numbered) == 1
    assert numbered[0]["page"] == 1
    assert numbered[0]["graphic_ref"] is not None
    assert numbered[0]["weld_type"] == "F"


def test_default_distance_threshold_keeps_unique_native_weld_anchor(tmp_path: Path) -> None:
    from chengda_weld_mapping import build_weld_map

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-10FW41006-01"
    output = tmp_path / "weld-map.json"

    summary = build_weld_map(
        base / "000200FW 0-PCF-焊口反写测试" / f"{sheet_id}.pcf",
        base / "000200FW" / f"{sheet_id}.sha",
        output,
    )

    assert summary.unmapped_numbered_count == 0


def test_native_weld_without_sha_uci_uses_master_pipe_topology(tmp_path: Path) -> None:
    from chengda_weld_mapping import build_weld_map

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-10FW41007-01"
    output = tmp_path / "weld-map.json"

    summary = build_weld_map(
        base / "000200FW 0-PCF-焊口反写测试" / f"{sheet_id}.pcf",
        base / "000200FW" / f"{sheet_id}.sha",
        output,
        80.0,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    row = next(row for row in payload["welds"] if row["weld_number"] == "F1")

    assert summary.unmapped_numbered_count == 0
    assert row["point_evidence"] == "pcf-topology-pipe-end"


def test_threaded_augmented_weld_uses_shared_component_junction(tmp_path: Path) -> None:
    from chengda_weld_mapping import build_weld_map

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-10IA41102-06"
    output = tmp_path / "weld-map.json"

    summary = build_weld_map(
        base / "000200FW 0-PCF-焊口反写测试" / f"{sheet_id}.pcf",
        base / "000200FW" / f"{sheet_id}.sha",
        output,
        80.0,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    row = next(row for row in payload["welds"] if row["weld_number"] == "T59")

    assert summary.unmapped_numbered_count == 0
    assert row["point_evidence"] == "pcf-topology-component-junction"
    assert row["sha_x"] == 3613.68
    assert row["sha_y"] == 2163.84


def test_threaded_pipe_end_welds_use_the_full_owned_pipe_axis(tmp_path: Path) -> None:
    """T17/T18 must land on opposite ends of the 100 mm branch pipe."""

    from chengda_weld_mapping import build_weld_map

    base = PROJECT_DIR / "SHA文件转换"
    sheet_id = "00-42-0200-26-10FW41001-02"
    output = tmp_path / "weld-map.json"

    summary = build_weld_map(
        base / "000200FW 0-PCF-焊口反写测试" / f"{sheet_id}.pcf",
        base / "000200FW" / f"{sheet_id}.sha",
        output,
        80.0,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    threaded = {
        row["weld_number"]: (row["sha_x"], row["sha_y"])
        for row in payload["welds"]
        if row["weld_number"] in {"T17", "T18"}
    }

    assert summary.unmapped_numbered_count == 0
    assert threaded == {
        "T17": (2200.8, 3074.4),
        "T18": (2200.8, 4030.32),
    }


def test_three_collinear_equal_interface_points_use_the_center() -> None:
    from chengda_weld_mapping import _unique_interface_point

    candidates = [
        (0.0, (2451.12, 6111.84), (2451.12, 6111.84)),
        (0.0, (2451.12, 6142.08), (2451.12, 6142.08)),
        (0.0, (2451.12, 6172.32), (2451.12, 6172.32)),
    ]

    assert _unique_interface_point(candidates) == (2451.1, 6142.1)


def test_000206_long_pipe_split_welds_use_the_long_diagonal_run(
    tmp_path: Path,
) -> None:
    """Virtual split welds must not collapse onto the short elbow-side axis."""

    from chengda_construction_welds import (
        apply_construction_welds,
        construction_records_for_sheet,
        group_construction_by_sheet,
        read_iso_review_weld_inputs,
    )
    from chengda_weld_mapping import (
        add_construction_virtual_targets,
        build_weld_map,
    )

    unit = PROJECT_DIR / "SHA文件转换" / "000206"
    sheet_id = "00-42-0206-26-00CNS02100-02"
    output = tmp_path / "weld-map.json"
    build_weld_map(
        unit / "000206_0B" / f"{sheet_id}.pcf",
        unit / "000206 SHA" / f"{sheet_id}.sha",
        output,
        include_unnumbered_topology=True,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    records, _ = read_iso_review_weld_inputs(
        unit / "000206焊口表-ISO对照结果.xlsx"
    )
    selected = construction_records_for_sheet(
        group_construction_by_sheet(records), sheet_id
    )
    payload = add_construction_virtual_targets(
        payload,
        selected,
        unit / "000206_0B" / f"{sheet_id}.pcf",
        unit / "000206 SHA" / f"{sheet_id}.sha",
    )
    payload, unresolved = apply_construction_welds(payload, selected)
    by_number = {
        str(row.get("weld_number")): row
        for row in payload["welds"]
        if row.get("weld_number") in {"F17", "FS16", "F19", "F20", "F21", "F22"}
    }

    assert not [
        row for row in unresolved
        if row.get("weld_number") in {"F19", "F20", "F21", "F22"}
    ]
    split_x = [float(by_number[number]["sha_x"]) for number in ("F19", "F20", "F21", "F22")]
    assert max(split_x) - min(split_x) > 1500.0
    for explicit, virtual in (("F17", "F19"), ("FS16", "F21")):
        assert math.dist(
            (float(by_number[explicit]["sha_x"]), float(by_number[explicit]["sha_y"])),
            (float(by_number[virtual]["sha_x"]), float(by_number[virtual]["sha_y"])),
        ) > 100.0


def test_000204_long_pipe_chain_uses_local_support_brackets(tmp_path: Path) -> None:
    """Nine splits without terminal weld anchors need local support evidence."""

    from chengda_construction_welds import (
        apply_construction_welds,
        construction_records_for_sheet,
        group_construction_by_sheet,
        read_iso_review_weld_inputs,
    )
    from chengda_weld_mapping import add_construction_virtual_targets, build_weld_map

    unit = PROJECT_DIR / "SHA文件转换" / "000204"
    sheet_id = "00-42-0204-26-00NGF00004-02"
    pcf = unit / "000204_0A" / f"{sheet_id}.pcf"
    sha = unit / "000204 SHA" / f"{sheet_id}.sha"
    output = tmp_path / "weld-map.json"
    build_weld_map(pcf, sha, output, include_unnumbered_topology=True)
    payload = json.loads(output.read_text(encoding="utf-8"))
    records, _ = read_iso_review_weld_inputs(
        unit / "焊口表-000204_0+0A_ISO对照结果-人工审核0929.xlsx"
    )
    selected = construction_records_for_sheet(group_construction_by_sheet(records), sheet_id)
    payload = add_construction_virtual_targets(payload, selected, pcf, sha)
    payload, unresolved = apply_construction_welds(payload, selected)
    wanted = {f"F{number}" for number in range(13, 22)}
    rows = [row for row in payload["welds"] if row.get("weld_number") in wanted]

    assert {row["weld_number"] for row in rows} == wanted
    assert not [row for row in unresolved if row.get("weld_number") in wanted]
    assert all(row.get("manual_review_required") for row in rows)
    assert all(
        row.get("construction_virtual_rule")
        == "construction-table-material-chain-support-bracketed-pipe"
        for row in rows
    )
    assert len({tuple(row["support_bracket_ucis"]) for row in rows}) >= 5
    assert all(1400.0 < float(row["sha_x"]) < 4900.0 for row in rows)
    f13 = next(row for row in rows if row["weld_number"] == "F13")
    # The dimension string is a parallel line about 200 page units away;
    # the leader must root on the UCI-owned black pipe stroke instead.
    assert 4800.0 < float(f13["sha_y"]) < 4950.0


def test_000206_material_chain_ratio_uses_complete_collinear_sha_run(
    tmp_path: Path,
) -> None:
    """F23/F24A1 must span the full 24.119 m run, not its first short stroke."""

    import pytest

    from chengda_construction_welds import (
        apply_construction_welds,
        construction_records_for_sheet,
        group_construction_by_sheet,
        read_iso_review_weld_inputs,
    )
    from chengda_weld_mapping import (
        add_construction_virtual_targets,
        build_weld_map,
    )

    unit = PROJECT_DIR / "SHA文件转换" / "000206"
    sheet_id = "00-42-0206-26-30LS00025-03"
    pcf = unit / "000206_0B" / f"{sheet_id}.pcf"
    sha = unit / "000206 SHA" / f"{sheet_id}.sha"
    output = tmp_path / "weld-map.json"
    build_weld_map(pcf, sha, output, include_unnumbered_topology=True)
    payload = json.loads(output.read_text(encoding="utf-8"))
    records, _ = read_iso_review_weld_inputs(
        unit / "000206焊口表-ISO对照结果.xlsx"
    )
    selected = construction_records_for_sheet(
        group_construction_by_sheet(records), sheet_id
    )
    payload = add_construction_virtual_targets(payload, selected, pcf, sha)
    payload, unresolved = apply_construction_welds(payload, selected)
    by_number = {
        str(row.get("weld_number")): row
        for row in payload["welds"]
        if row.get("weld_number") in {"F23", "F24A1"}
    }

    assert not [
        row for row in unresolved
        if row.get("weld_number") in {"F23", "F24A1"}
    ]
    assert math.dist(*by_number["F23"]["pipe_axis"]) > 5000.0
    assert (by_number["F23"]["sha_x"], by_number["F23"]["sha_y"]) == pytest.approx(
        (3341.52, 4173.12), abs=1.0
    )
    assert (by_number["F24A1"]["sha_x"], by_number["F24A1"]["sha_y"]) == pytest.approx(
        (1086.8, 2873.4), abs=3.0
    )


def test_000206_single_remaining_terminal_pipe_splits_are_recovered(
    tmp_path: Path,
) -> None:
    """A verified material chain may end in one last unresolved pipe split."""

    from chengda_construction_welds import (
        apply_construction_welds,
        construction_records_for_sheet,
        group_construction_by_sheet,
        read_iso_review_weld_inputs,
    )
    from chengda_weld_mapping import add_construction_virtual_targets, build_weld_map

    unit = PROJECT_DIR / "SHA文件转换" / "000206"
    records, _ = read_iso_review_weld_inputs(
        unit / "000206焊口表-ISO对照结果.xlsx"
    )
    grouped = group_construction_by_sheet(records)
    expected = {
        "00-42-0206-26-00HY00001-11": (
            "F84", (2147.3, 3557.9), None, None, None, None
        ),
        "00-42-0206-26-00NA00007-07": (
            "F38", (967.0, 2848.0), "F39", (3448.25, 4277.841),
            (1207.92, 2987.04), None
        ),
        "00-42-0206-26-00PW00004-04": (
            "F45", (1723.6, 3549.6), "F43", (2525.933, 4012.252),
            None, None
        ),
        "00-42-0206-26-00SWW02102-05": (
            "F72", (1000.4, 3088.1), "F71", (1649.928, 3413.856),
            (1201.2, 3198.72), (1750.451, 3512.578)
        ),
    }

    for sheet_id, (
        number,
        old_root,
        peer_number,
        old_peer_root,
        wrong_snap_root,
        wrong_peer_snap_root,
    ) in expected.items():
        pcf = unit / "000206_0B" / f"{sheet_id}.pcf"
        sha = unit / "000206 SHA" / f"{sheet_id}.sha"
        output = tmp_path / f"{sheet_id}.json"
        build_weld_map(pcf, sha, output, include_unnumbered_topology=True)
        payload = json.loads(output.read_text(encoding="utf-8"))
        selected = construction_records_for_sheet(grouped, sheet_id)
        payload = add_construction_virtual_targets(payload, selected, pcf, sha)
        payload, unresolved = apply_construction_welds(payload, selected)
        rows = [
            row
            for row in payload["welds"]
            if str(row.get("weld_number", "")) == number
            and row.get("confidence_status") == "mapped"
            and row.get("graphic_ref") is not None
        ]

        assert not [row for row in unresolved if row.get("weld_number") == number]
        assert len(rows) == 1
        row = rows[0]
        assert row["point_evidence"] == (
            "construction-table-material-chain-terminal-continuation"
        )
        assert row["manual_review_required"] is True
        root = (float(row["sha_x"]), float(row["sha_y"]))
        axis = [tuple(float(value) for value in point) for point in row["pipe_axis"]]
        terminal_gap = min(math.dist(root, endpoint) for endpoint in axis)
        if peer_number is None:
            assert math.dist(root, old_root) < 15.0
        else:
            # The short terminal piece must remain visually distinguishable
            # from the conventional weld at the pipe end.
            assert terminal_gap >= 175.0
            assert math.dist(root, old_root) >= 80.0
            if wrong_snap_root is not None:
                assert math.dist(root, wrong_snap_root) >= 30.0
            peer = next(
                item
                for item in payload["welds"]
                if str(item.get("weld_number", "")) == peer_number
                and item.get("confidence_status") == "mapped"
            )
            assert peer["drawing_distribution_mode"] == (
                "TERMINAL-CHAIN-JOINT-DISTRIBUTION"
            )
            assert math.dist(
                (float(peer["sha_x"]), float(peer["sha_y"])), old_peer_root
            ) >= 20.0
            if wrong_peer_snap_root is not None:
                assert math.dist(
                    (float(peer["sha_x"]), float(peer["sha_y"])),
                    wrong_peer_snap_root,
                ) >= 20.0


def test_100200_remaining_pipe_splits_and_threaded_elbows_are_recovered(
    tmp_path: Path,
) -> None:
    from chengda_construction_welds import (
        apply_construction_welds,
        construction_records_for_sheet,
        group_construction_by_sheet,
        read_construction_weld_inputs,
    )
    from chengda_weld_mapping import add_construction_virtual_targets, build_weld_map

    unit = PROJECT_DIR / "SHA文件转换" / "100200"
    records, _ = read_construction_weld_inputs(
        unit / "焊口表-壁厚材质处理-带特性-汇总.xlsx"
    )
    grouped = group_construction_by_sheet(records)
    expected = {
        "10-42-0200-26-10BR02103-06": {"F20", "F21"},
        "10-42-0200-26-10DW02110-03": {"F19"},
        "10-42-0200-26-10PW02009-02": {"T12"},
        "10-42-0200-26-10PW02010-02": {"T10"},
        "10-42-0200-26-10PW02011-02": {"T10"},
    }

    for sheet_id, expected_numbers in expected.items():
        map_path = tmp_path / f"{sheet_id}.json"
        pcf_path = unit / "PCF" / f"{sheet_id}.pcf"
        sha_path = unit / "SHA" / f"{sheet_id}.sha"
        build_weld_map(
            pcf_path,
            sha_path,
            map_path,
            include_unnumbered_topology=True,
        )
        payload = json.loads(map_path.read_text(encoding="utf-8"))
        sheet_records = construction_records_for_sheet(grouped, sheet_id)
        payload = add_construction_virtual_targets(
            payload, sheet_records, pcf_path, sha_path
        )
        payload, _ = apply_construction_welds(payload, sheet_records)
        mapped = {
            str(row.get("weld_number", ""))
            for row in payload["welds"]
            if row.get("confidence_status") == "mapped"
            and row.get("graphic_ref") is not None
        }
        assert expected_numbers <= mapped


def test_201600_material_chain_splits_accept_one_physical_endpoint_root(
    tmp_path: Path,
) -> None:
    """Several PCF UCIs may describe one plotted root without making it ambiguous."""

    from chengda_construction_welds import (
        apply_construction_welds,
        construction_records_for_sheet,
        group_construction_by_sheet,
        read_iso_review_weld_inputs,
    )
    from chengda_weld_mapping import add_construction_virtual_targets, build_weld_map

    unit = PROJECT_DIR / "SHA文件转换" / "201600"
    records, _ = read_iso_review_weld_inputs(
        unit / "焊口表-201600_ISO对照结果_焊口号数字排序-合并.xlsx"
    )
    grouped = group_construction_by_sheet(records)
    for sheet_id, expected in {
        "20-42-1600-26-EC-3077-02": {"F12", "F13"},
        "20-42-1600-26-ARH-1167-02": {"F17"},
    }.items():
        pcf_path = unit / "201600 0A PCF" / f"{sheet_id}.pcf"
        sha_path = unit / "201600 SHA" / f"{sheet_id}.sha"
        output = tmp_path / f"{sheet_id}.json"
        build_weld_map(
            pcf_path,
            sha_path,
            output,
            include_unnumbered_topology=True,
        )
        payload = json.loads(output.read_text(encoding="utf-8"))
        sheet_records = construction_records_for_sheet(grouped, sheet_id)
        payload = add_construction_virtual_targets(
            payload, sheet_records, pcf_path, sha_path
        )
        payload, _ = apply_construction_welds(payload, sheet_records)
        mapped = {
            str(row.get("weld_number"))
            for row in payload["welds"]
            if row.get("confidence_status") == "mapped"
            and row.get("graphic_ref") is not None
        }
        assert expected <= mapped


def _mapped_200701_welds(tmp_path: Path, sheet_id: str) -> dict[str, dict[str, object]]:
    """Build the real 200701 topology map used by the delivery pipeline."""

    from chengda_construction_welds import (
        apply_construction_welds,
        construction_records_for_sheet,
        group_construction_by_sheet,
        read_iso_review_weld_inputs,
    )
    from chengda_weld_mapping import add_construction_virtual_targets, build_weld_map

    unit = PROJECT_DIR / "SHA文件转换" / "200701"
    records, _ = read_iso_review_weld_inputs(
        unit
        / "焊口表-200701 0-壁厚材质处理-带特性_ISO对照结果_焊口号数字排序-人工核对0921.xlsx"
    )
    sheet_records = construction_records_for_sheet(
        group_construction_by_sheet(records), sheet_id
    )
    pcf_path = unit / "200701 0 PCF" / f"{sheet_id}.pcf"
    sha_path = unit / "200701 SHA" / f"{sheet_id}.sha"
    output = tmp_path / f"{sheet_id}.json"
    build_weld_map(
        pcf_path,
        sha_path,
        output,
        include_unnumbered_topology=True,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload = add_construction_virtual_targets(
        payload, sheet_records, pcf_path, sha_path
    )
    payload, _ = apply_construction_welds(payload, sheet_records)
    return {
        str(row.get("weld_number")): row
        for row in payload["welds"]
        if row.get("weld_number")
        and row.get("graphic_ref") is not None
        and row.get("confidence_status") == "mapped"
    }


def test_200701_submillimetre_threaded_interfaces_are_uniquely_recovered(
    tmp_path: Path,
) -> None:
    """Rounded table coordinates must still distinguish the two coupling faces."""

    mapped = _mapped_200701_welds(
        tmp_path, "20-42-0701-26-WR-7010A-01"
    )

    assert {"T2", "T3"} <= set(mapped)
    roots = {
        number: (
            int(mapped[number]["page"]),
            round(float(mapped[number]["sha_x"]), 2),
            round(float(mapped[number]["sha_y"]), 2),
        )
        for number in ("T2", "T3")
    }
    assert roots["T2"] != roots["T3"]


def test_200701_multiple_splits_on_one_long_pipe_keep_distinct_ordered_roots(
    tmp_path: Path,
) -> None:
    """Four 12 m construction splits must not snap onto one short SHA stroke."""

    mapped = _mapped_200701_welds(
        tmp_path, "20-42-0701-26-EC-6095-01"
    )

    numbers = ("F15", "F16", "F17", "F18")
    assert set(numbers) <= set(mapped)
    roots = [
        (
            round(float(mapped[number]["sha_x"]), 2),
            round(float(mapped[number]["sha_y"]), 2),
        )
        for number in numbers
    ]
    assert len(set(roots)) == len(numbers)
    assert all(
        mapped[number].get("point_evidence") == "pcf-pipe-interpolation"
        for number in numbers
    )


def test_200701_bracket_snap_does_not_steal_an_existing_tee_root(
    tmp_path: Path,
) -> None:
    """The PAD/SW pair shares one root, but the next pipe weld stays distinct."""

    mapped = _mapped_200701_welds(
        tmp_path, "20-42-0701-26-DES-7922-03"
    )

    assert {"F23", "F32A1", "F32", "F33"} <= set(mapped)
    f23_root = (
        round(float(mapped["F23"]["sha_x"]), 2),
        round(float(mapped["F23"]["sha_y"]), 2),
    )
    f33_root = (
        round(float(mapped["F33"]["sha_x"]), 2),
        round(float(mapped["F33"]["sha_y"]), 2),
    )
    f32_root = (
        round(float(mapped["F32"]["sha_x"]), 2),
        round(float(mapped["F32"]["sha_y"]), 2),
    )
    f32a1_root = (
        round(float(mapped["F32A1"]["sha_x"]), 2),
        round(float(mapped["F32A1"]["sha_y"]), 2),
    )
    assert f32_root == f32a1_root
    assert f32_root != f23_root
    assert f23_root != f33_root
