#!/usr/bin/env python3
"""Read-only study of unique PCF FPFL paths in current SHA geometry."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from openpyxl import load_workbook

ROOT = DATA_DIR
PARSER = CODE_PARSER_DIR
sys.path[:0] = [str(ROOT), str(PARSER)]

from chengda_management_callouts import (  # noqa: E402
    _bbox_center,
    _bbox_gap,
    _pcf_fpfl_records_by_uci,
    _pcf_component_records_by_uci,
    forward_owned_uci_regions,
)
from chengda_management_table import read_management_inputs  # noqa: E402
from convert_chengda_weld_sha_to_pdf import discover_weld_jobs  # noqa: E402

UNIT = ROOT / "SHA文件转换" / "201600"


def ordered_path(adjacency: dict[object, set[object]], nodes: set[object]) -> list[object]:
    ends = [node for node in nodes if len(adjacency.get(node, set()) & nodes) == 1]
    if len(nodes) == 1:
        return list(nodes)
    if len(ends) != 2 or any(len(adjacency.get(node, set()) & nodes) > 2 for node in nodes):
        return []
    result = [ends[0]]
    while len(result) < len(nodes):
        candidates = (adjacency[result[-1]] & nodes) - set(result)
        if len(candidates) != 1:
            return []
        result.append(next(iter(candidates)))
    return result


def components(adjacency: dict[object, set[object]]) -> list[set[object]]:
    remaining = set(adjacency)
    result = []
    while remaining:
        pending = [next(iter(remaining))]
        group = set()
        while pending:
            node = pending.pop()
            if node in group:
                continue
            group.add(node)
            pending.extend(adjacency.get(node, set()) - group)
        remaining -= group
        result.append(group)
    return result


def main() -> int:
    workbook = load_workbook(
        UNIT / "201600_法兰标注规则预检.xlsx", read_only=True, data_only=True
    )
    remaining_sheet = workbook["仍需分析"]
    remaining_headers = list(next(remaining_sheet.iter_rows(values_only=True)))
    still_unresolved = {
        (str(row["单页图号"]).casefold(), str(row["编号"]))
        for values in remaining_sheet.iter_rows(min_row=2, values_only=True)
        for row in [dict(zip(remaining_headers, values))]
    }
    workbook.close()
    records = read_management_inputs(
        flange_table=UNIT / "法兰表.csv", project_profile="100200"
    )
    by_sheet: dict[str, list[object]] = {}
    for record in records:
        by_sheet.setdefault(record.sheet_id.casefold(), []).append(record)
    jobs = discover_weld_jobs(
        UNIT / "201600 SHA", UNIT / "201600 0A PCF", missing_pcf_sheet_ids=[]
    )
    outcomes = []
    for job in jobs:
        page_records = by_sheet.get(job.sheet_id.casefold(), [])
        fpfl = _pcf_fpfl_records_by_uci(job.pcf_path)
        components_by_uci = _pcf_component_records_by_uci(job.pcf_path)
        edge_records = [
            record for record in page_records
            if len(record.ucis) == 2 and all(uci in fpfl for uci in record.ucis)
        ]
        pcf_adj: dict[str, set[str]] = {}
        for record in edge_records:
            first, second = record.ucis
            pcf_adj.setdefault(first, set()).add(second)
            pcf_adj.setdefault(second, set()).add(first)
        if not pcf_adj:
            continue

        regions = forward_owned_uci_regions(job.sha_path, job.sheet_stream)
        all_region_by_ref = {
            int(region["graphic_ref"]): region for region in regions
        }
        all_uci_ref = {
            str(region["uci"]).upper(): int(region["graphic_ref"])
            for region in regions
        }
        region_by_ref = {
            int(region["graphic_ref"]): region
            for region in regions
            if str(region.get("uci", "")).startswith("{0001388C-")
        }
        sha_adj: dict[int, set[int]] = {ref: set() for ref in region_by_ref}
        items = list(region_by_ref.items())
        for index, (left_ref, left) in enumerate(items):
            for right_ref, right in items[index + 1:]:
                if _bbox_gap(tuple(left["bbox"]), tuple(right["bbox"])) <= 1.0:
                    sha_adj[left_ref].add(right_ref)
                    sha_adj[right_ref].add(left_ref)
        uci_ref = {
            str(region["uci"]).upper(): ref
            for ref, region in region_by_ref.items()
        }

        for pcf_nodes in components(pcf_adj):
            pcf_order = ordered_path(pcf_adj, pcf_nodes)
            if not pcf_order:
                continue
            landmarks = {
                index: uci_ref[uci]
                for index, uci in enumerate(pcf_order)
                if uci in uci_ref
            }
            solutions: list[list[int]] = []
            for sha_nodes in components(sha_adj):
                sha_order = ordered_path(sha_adj, sha_nodes)
                if not sha_order or len(sha_order) < len(pcf_order):
                    continue
                for candidate_order in (sha_order, list(reversed(sha_order))):
                    for offset in range(len(candidate_order) - len(pcf_order) + 1):
                        candidate = candidate_order[offset:offset + len(pcf_order)]
                        if landmarks and all(candidate[index] == ref for index, ref in landmarks.items()):
                            solutions.append(candidate)
            unique = {tuple(solution) for solution in solutions}
            terminal_seed_solutions: set[tuple[int, ...]] = set()
            if not landmarks:
                for pcf_endpoint_index in (0, len(pcf_order) - 1):
                    endpoint_uci = pcf_order[pcf_endpoint_index]
                    endpoint_points = components_by_uci.get(endpoint_uci, ("", []))[1]
                    external_visible = {
                        uci
                        for uci, (_kind, endpoints) in components_by_uci.items()
                        if uci not in pcf_nodes
                        and uci in all_uci_ref
                        and any(
                            __import__('math').dist(first, second) <= 5.0
                            for first in endpoint_points for second in endpoints
                        )
                    }
                    for seed_uci in external_visible:
                        seed_ref = all_uci_ref[seed_uci]
                        seed_box = tuple(all_region_by_ref[seed_ref]["bbox"])
                        touching = [
                            ref for ref, region in region_by_ref.items()
                            if ref != seed_ref
                            and _bbox_gap(seed_box, tuple(region["bbox"])) <= 1.0
                        ]
                        for first_ref in touching:
                            for sha_nodes in components(sha_adj):
                                if first_ref not in sha_nodes:
                                    continue
                                sha_order = ordered_path(sha_adj, sha_nodes)
                                if len(sha_order) != len(pcf_order):
                                    continue
                                if sha_order[0] == first_ref:
                                    terminal_seed_solutions.add(tuple(sha_order))
                                if sha_order[-1] == first_ref:
                                    terminal_seed_solutions.add(tuple(reversed(sha_order)))
            edge_numbers = [
                record.number for record in edge_records
                if record.ucis[0] in pcf_nodes and record.ucis[1] in pcf_nodes
            ]
            outcomes.append({
                "sheet": job.sheet_id,
                "pcf_nodes": len(pcf_order),
                "landmarks": len(landmarks),
                "solutions": len(unique),
                "terminal_seed_solutions": len(terminal_seed_solutions),
                "edge_count": len(edge_numbers),
                "numbers": edge_numbers,
                "unresolved_numbers": [
                    number for number in edge_numbers
                    if (job.sheet_id.casefold(), number) in still_unresolved
                ],
            })

    print("components", len(outcomes))
    print("status", Counter(
        "unique" if row["solutions"] == 1 else "none" if row["solutions"] == 0 else "ambiguous"
        for row in outcomes
    ))
    print("recoverable_edges", sum(row["edge_count"] for row in outcomes if row["solutions"] == 1))
    print("recoverable_unresolved", sum(len(row["unresolved_numbers"]) for row in outcomes if row["solutions"] == 1))
    print("terminal_seed_recoverable_unresolved", sum(
        len(row["unresolved_numbers"])
        for row in outcomes
        if row["solutions"] == 0 and row["terminal_seed_solutions"] == 1
    ))
    for row in outcomes:
        if row["solutions"] == 1 and row["unresolved_numbers"]:
            print("UNIQUE", row)
        if row["solutions"] == 0 and row["terminal_seed_solutions"] == 1 and row["unresolved_numbers"]:
            print("TERMINAL", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
