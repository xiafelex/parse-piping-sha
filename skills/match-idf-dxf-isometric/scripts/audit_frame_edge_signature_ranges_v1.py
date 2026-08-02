#!/usr/bin/env python3
"""Audit page-range candidates using landmark-to-landmark pipe edge types.

This is deliberately a range audit, not an I-to-P matcher.  It supplements
frame counts with the multiset of directly connected landmark kinds.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def number(value): return int(value[1:])


def kind(frame, side):
    if side == "idf":
        if frame["kind"] == "junction_3": return "junction"
        if frame["kind"] == "turn_2": return "elbow"
        if frame["kind"] == "inline_2" and frame.get("bore_change"): return "reducer"
    else:
        if frame["kind"] in {"branch", "tee"} and frame["degree"] == 3: return "junction"
        if frame["kind"] == "elbow" and frame["degree"] == 2: return "elbow"
        if frame["kind"] == "reducer" and frame["degree"] == 2: return "reducer"
    return None


def signature(frames, incidence, side, allowed_pipes=None, page=None):
    by_id = {row["id"]: row for row in frames}
    result = collections.Counter()
    for row in incidence:
        if allowed_pipes is not None and row["pipe"] not in allowed_pipes:
            continue
        if page is not None and row.get("page") != page:
            continue
        ends = sorted(kind(by_id[item], side) for item in row["frames"] if item in by_id and kind(by_id[item], side))
        if len(ends) == 2:
            result["--".join(ends)] += 1
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("frame_graph", type=Path)
    parser.add_argument("global_cover", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    graph, cover = json.loads(args.frame_graph.read_text()), json.loads(args.global_cover.read_text())
    all_candidates = [cover["best"]] + cover.get("alternatives", [])
    rows = []
    for candidate_index, candidate in enumerate(all_candidates):
        if candidate.get("missing_indices") or candidate.get("duplicate_indices"):
            continue
        pages, total = [], 0
        for row in candidate["page_ranges"]:
            start, end = map(number, row["idf_range"])
            wanted = {f"I{value:03d}" for value in range(start, end + 1)}
            left = signature(graph["idf"]["frames"], graph["idf"]["pipe_frame_incidence"], "idf", wanted)
            right = signature(graph["dxf"]["frames"], graph["dxf"]["pipe_frame_incidence"], "dxf", page=row["page"])
            distance = sum(abs(left[key] - right[key]) for key in set(left) | set(right))
            total -= distance
            pages.append({"page": row["page"], "idf_range": row["idf_range"],
                          "idf_edge_signature": dict(left), "dxf_edge_signature": dict(right), "distance": distance})
        rows.append({"cover_candidate_index": candidate_index, "edge_signature_score": total, "pages": pages})
    rows.sort(key=lambda row: -row["edge_signature_score"])
    result = {"algorithm": "FRAME_EDGE_SIGNATURE_RANGE_AUDIT_V1",
              "policy": "landmark edge signatures only rank existing legal page ranges; no IDF-to-DXF pipe assignment",
              "candidates": rows,
              "status": "unique_edge_signature_range_candidate" if len(rows) > 1 and rows[0]["edge_signature_score"] > rows[1]["edge_signature_score"] else "edge_signature_non_discriminating"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"status": result["status"], "best": rows[0]["edge_signature_score"] if rows else None}, ensure_ascii=False))


if __name__ == "__main__": main()
