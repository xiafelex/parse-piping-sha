#!/usr/bin/env python3
"""Audit IDF 149 FLOW marker distribution against DXF arrow-pipe page counts."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def marked_pipes(idf_path: Path):
    serial = 0
    pending_flow = False
    marked = []
    for line in idf_path.read_text(errors="replace").splitlines():
        fields = line.split()
        if not fields or not re.fullmatch(r"-?\d+", fields[0]):
            continue
        code = int(fields[0])
        if code == 149 and "FLOW" in line:
            pending_flow = True
        elif code == 100:
            serial += 1
            if pending_flow:
                marked.append(serial)
            pending_flow = False
    return marked


def signature(page_ranges):
    return tuple((row["page"], tuple(row["idf_range"])) for row in page_ranges)


def evaluate(page_ranges, marked, arrows):
    rows = []
    total = 0
    for row in page_ranges:
        lo, hi = (int(value[1:]) for value in row["idf_range"])
        idf_count = sum(lo <= pipe <= hi for pipe in marked)
        dxf_count = arrows.get(row["page"], 0)
        total += abs(idf_count - dxf_count)
        rows.append({"page": row["page"], "idf_range": row["idf_range"], "idf_flow_149_count": idf_count,
                     "dxf_arrow_pipe_count": dxf_count, "absolute_difference": abs(idf_count - dxf_count)})
    return total, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("idf", type=Path)
    parser.add_argument("dxf_topology", type=Path)
    parser.add_argument("global_cover", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    marked = marked_pipes(args.idf)
    topology = json.loads(args.dxf_topology.read_text())
    arrows = {}
    for pipe in topology["pipes"]:
        if pipe["kind"] == "arrow_pipe":
            arrows[pipe["page"]] = arrows.get(pipe["page"], 0) + 1
    cover = json.loads(args.global_cover.read_text())
    raw_candidates = [cover["best"]] + cover.get("alternatives", [])
    candidates = []
    seen = set()
    for candidate in raw_candidates:
        # A flow count cannot rescue a range cover that already duplicates or
        # omits an IDF 100.  Such rows are diagnostics, not legal candidates.
        if candidate.get("missing_indices") or candidate.get("duplicate_indices"):
            continue
        key = signature(candidate["page_ranges"])
        if key in seen:
            continue
        seen.add(key)
        score, rows = evaluate(candidate["page_ranges"], marked, arrows)
        candidates.append({"flow_count_difference": score, "page_ranges": rows})
    candidates.sort(key=lambda row: row["flow_count_difference"])
    unique = len(candidates) == 1 or candidates[0]["flow_count_difference"] < candidates[1]["flow_count_difference"]
    result = {"algorithm": "IDF149_FLOW_MARKER_PAGE_RANGE_AUDIT_V1",
              "policy": "149 FLOW is a page-range corroboration only; marker counts never create an I-to-P match",
              "idf_marked_pipes": [f"I{pipe:03d}" for pipe in marked], "dxf_arrow_pipe_count_by_page": arrows,
              "candidates": candidates,
              "status": "unique_flow_marker_range_candidate" if unique else "flow_marker_non_discriminating"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"status": result["status"], "candidate_count": len(candidates),
                      "best_difference": candidates[0]["flow_count_difference"] if candidates else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
