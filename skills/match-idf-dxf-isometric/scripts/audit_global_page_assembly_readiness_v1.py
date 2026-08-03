#!/usr/bin/env python3
"""Audit whether a multi-page DXF line has enough *independent* structure for assembly.

This does not connect pages and does not use paper coordinates as geometry.  It
reports the size of the finite port search space and the page-level semantic
anchors available to score it against the complete IDF graph.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def page_signature(graph, page, frame_rows=()):
    pipes = [p for p in graph["pipes"] if p["page"] == page]
    comps = [c for c in graph.get("components", []) if c["page"] == page]
    page_pipe_ids = {p["id"] for p in pipes}
    endpoints = [
        n for n in graph.get("endpoint_nodes", [])
        if n["id"].rsplit(":E", 1)[0] in page_pipe_ids
    ]
    roles = Counter(
        "+".join(k for k, v in n.get("role", {}).items() if v) or "plain"
        for n in endpoints
    )
    kinds = Counter(p["kind"] for p in pipes)
    component_kinds = Counter(c["kind"] for c in comps)
    frame_kinds = Counter(row["kind"] for row in frame_rows if row.get("page") == page)
    return {
        "page": page,
        "source": next((p["source"] for p in pipes), None),
        "pipe_count": len(pipes),
        "component_count": len(comps),
        "pipe_kinds": dict(sorted(kinds.items())),
        "component_kinds": dict(sorted(component_kinds.items())),
        "endpoint_roles": dict(sorted(roles.items())),
        # Empty ends are the broad geometric search space, not a continuation claim.
        "raw_port_count": sum(1 for n in endpoints if n.get("role", {}).get("empty")),
        "all_vector_endpoint_count": len(endpoints),
        "distinct_component_classes": len(component_kinds),
        "structural_frames": dict(sorted(frame_kinds.items())),
        "structural_signature": "|".join(
            f"{kind}:{count}" for kind, count in sorted(frame_kinds.items())
        ) or "none",
    }


def classify(pages, continuation_rows):
    graphical = [p for p in pages if p["pipe_count"]]
    if len(graphical) < 2:
        return "not_multipage_graphical"
    raw_ports = sum(p["raw_port_count"] for p in graphical)
    # Every undirected binary port pairing is a candidate before IDF scoring.
    pair_space = raw_ports * (raw_ports - 1) // 2
    anchors = sum(p["distinct_component_classes"] for p in graphical)
    frame_signatures = [p["structural_signature"] for p in graphical]
    unique_frame_pages = len(set(frame_signatures)) == len(frame_signatures)
    nearby = [r for r in continuation_rows if r["distance"] <= 35]
    if len(graphical) == 2 and len(nearby) >= 2 and raw_ports <= 8:
        return "assembly_candidate_small_search_space"
    if unique_frame_pages and anchors >= len(graphical) * 3 and raw_ports <= 24:
        return "assembly_candidate_requires_global_scoring"
    return "insufficient_independent_page_anchors"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("graphs_dir", type=Path)
    ap.add_argument("port_dir", type=Path)
    ap.add_argument("--frames-dir", type=Path,
                    help="component-frame-graphs directory; lets readiness use semantic structure, not raw component count")
    ap.add_argument("--global-cover-dir", type=Path,
                    help="existing IDF↔DXF component-frame cover results; records whether whole-line page ranges are unique")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    lines = []
    for path in sorted(args.graphs_dir.glob("*.json")):
        graph = json.loads(path.read_text())
        summary = graph.get("page_summary", [])
        if len(summary) < 2:
            continue
        port_path = args.port_dir / path.name
        continuation = json.loads(port_path.read_text()).get("candidates", []) if port_path.exists() else []
        frame_rows = []
        if args.frames_dir:
            frame_path = args.frames_dir / path.name
            if frame_path.exists():
                frame_rows = json.loads(frame_path.read_text()).get("dxf", {}).get("frames", [])
        pages = [page_signature(graph, s["page"], frame_rows) for s in summary]
        status = classify(pages, continuation)
        graphical = [p for p in pages if p["pipe_count"]]
        lines.append({
            "line_key": path.stem,
            "status": status,
            "graphical_page_count": len(graphical),
            "zero_graphical_pages": [p["page"] for p in pages if not p["pipe_count"]],
            "raw_port_pair_search_space": sum(p["raw_port_count"] for p in graphical) * (sum(p["raw_port_count"] for p in graphical)-1)//2,
            "nearby_continuation_review_candidates": [
                {"page": r["page"], "endpoint": r["candidate_endpoint"], "distance": r["distance"]}
                for r in continuation if r["distance"] <= 35
            ],
            "all_graphical_page_structural_signatures_unique": len({p["structural_signature"] for p in graphical}) == len(graphical),
            "pages": pages,
            "why_not_yet_unique": (
                "Source-vector endpoints are available, but component/weld continuity has not yet been promoted to a page-level graph. "
                "Therefore this is a finite candidate set, not a proved assembly."
            ),
        })
        if args.global_cover_dir:
            cover_path = args.global_cover_dir / path.name
            if cover_path.exists():
                cover = json.loads(cover_path.read_text())
                lines[-1]["idf_global_cover"] = {
                    "status": cover.get("status"),
                    "best_page_ranges": cover.get("best", {}).get("page_ranges", []),
                    "meaning": (
                        "whole-line page range is structurally unique"
                        if cover.get("status") == "topology_global_unique_exact_cover_candidate"
                        else "page-range relation remains partial or tied; do not emit individual I→P matches"
                    ),
                }
    out = {
        "algorithm": "GLOBAL_PAGE_ASSEMBLY_READINESS_V1",
        "policy": "north-normalised geometry + IDF whole-graph scoring is required before confirming a join; CONT labels are review evidence only",
        "lines": lines,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    for row in lines:
        print(f"{row['line_key']:12} {row['status']:48} pages={row['graphical_page_count']} pairs={row['raw_port_pair_search_space']}")


if __name__ == "__main__":
    main()
