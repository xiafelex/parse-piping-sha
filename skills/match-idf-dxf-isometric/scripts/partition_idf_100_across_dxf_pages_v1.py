#!/usr/bin/env python3
"""Assign conservative IDF-100 page ranges before individual DXF matching.

This is intentionally a *global* pre-match.  It derives page order only from
CONT. ON/FROM relations, retains zero-geometry continuation sheets as context,
and emits page-range candidates.  It never invents an I###→handle mapping.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def page_order(graph):
    physical = {entry["page"] for entry in graph["page_summary"] if entry["pipe_count"]}
    directed = defaultdict(set)
    for edge in graph.get("continuation_edges", []):
        if edge.get("mode") != "on":
            continue
        source = next(port["page"] for port in graph["continuation_ports"] if port["id"] == edge["from"])
        target = edge["to_page"]
        if source in physical and target in physical:
            directed[source].add(target)
    if not physical:
        return [], directed, "no_physical_dxf_page"
    incoming = defaultdict(int)
    for source, targets in directed.items():
        for target in targets:
            incoming[target] += 1
    roots = sorted(page for page in physical if not incoming[page])
    if len(roots) != 1:
        return [], directed, "not_single_global_page_path"
    order = []
    current = roots[0]
    seen = set()
    while current not in seen:
        seen.add(current); order.append(current)
        targets = sorted(directed.get(current, set()))
        if not targets:
            break
        if len(targets) != 1:
            return [], directed, "not_single_global_page_path"
        current = targets[0]
    if set(order) != physical:
        return [], directed, "not_single_global_page_path"
    return order, directed, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("idf_topology", type=Path)
    parser.add_argument("global_dxf_graph", type=Path)
    parser.add_argument("--branch-outlet-candidates", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    idf = json.loads(args.idf_topology.read_text())
    graph = json.loads(args.global_dxf_graph.read_text())
    order, directed, reason = page_order(graph)
    pages = {entry["page"]: entry for entry in graph["page_summary"]}
    ranges = []
    cursor = 1
    for page in order:
        count = pages[page]["pipe_count"]
        ranges.append({"page": page, "source": pages[page]["source"], "pipe_count": count,
                       "idf_range": [f"I{cursor:03d}", f"I{cursor + count - 1:03d}"]})
        cursor += count
    count_match = cursor - 1 == idf["idf_100_count"]
    anchors = []
    if args.branch_outlet_candidates and args.branch_outlet_candidates.exists():
        candidate = json.loads(args.branch_outlet_candidates.read_text())
        for match in candidate.get("matches", []):
            handles = match.get("dxf_handles", [])
            pipe = next((item for item in graph["pipes"] if set(handles) <= set(item["handles"])), None)
            anchors.append({"idf_id": match.get("idf_id", match.get("idf")), "dxf_handles": handles,
                            "candidate_page": pipe["page"] if pipe else None,
                            "candidate_pipe_id": pipe["id"] if pipe else None,
                            "confidence": match.get("confidence"),
                            "evidence": "prior direct branch-body contact; not upgraded by page partition"})
    records = []
    for pipe in idf["pipes"]:
        candidate = next((item for item in ranges if item["idf_range"][0] <= pipe["id"] <= item["idf_range"][1]), None)
        anchor = next((item for item in anchors if item["idf_id"] == pipe["id"]), None)
        records.append({"idf_id": pipe["id"], "source_line": pipe["line"],
                        "candidate_page": candidate["page"] if candidate else None,
                        "page_range_status": "candidate" if candidate and count_match and not reason else "unresolved",
                        "dxf_handle": anchor["dxf_handles"] if anchor else None,
                        "individual_match_status": "medium_anchor_candidate" if anchor else "unresolved",
                        "evidence": (["continuation-derived global page order", "per-page typed-pipe count"] +
                                     ([anchor["evidence"]] if anchor else []))})
    result = {
        "algorithm": "GLOBAL_PAGE_PARTITION_100_V1",
        "idf": idf["idf"], "line_key": graph["line_key"],
        "eligible": not reason and count_match,
        "reason": reason if reason else (None if count_match else "idf_dxf_global_pipe_count_mismatch"),
        "policy": "page range is not an individual I###→DXF-handle match",
        "physical_page_order": order,
        "continuation_directed_edges": {str(key): sorted(value) for key, value in directed.items()},
        "zero_geometry_continuation_pages": [entry for entry in graph["page_summary"] if not entry["pipe_count"]],
        "page_ranges": ranges,
        "idf_100_count": idf["idf_100_count"],
        "dxf_global_typed_pipe_count": sum(entry["pipe_count"] for entry in graph["page_summary"]),
        "branch_anchor_candidates": anchors,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"eligible": result["eligible"], "physical_page_order": order,
                      "idf_100_count": result["idf_100_count"], "dxf_pipe_count": result["dxf_global_typed_pipe_count"],
                      "anchor_candidate_count": len(anchors), "reason": result["reason"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
