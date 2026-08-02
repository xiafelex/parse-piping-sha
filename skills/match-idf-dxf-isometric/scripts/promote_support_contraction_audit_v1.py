#!/usr/bin/env python3
"""Promote complete high support-contraction groups to stable DXF P### lists."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("support_audit", type=Path)
    parser.add_argument("dxf_topology", type=Path)
    parser.add_argument("--line-key", required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.support_audit.read_text())
    topology = json.loads(args.dxf_topology.read_text())
    if audit.get("algorithm") != "SUPPORT_CONTRACTION_CHAIN_V1" or audit.get("confidence") != "high" or not audit.get("eligible"):
        raise SystemExit("requires an eligible high-confidence SUPPORT_CONTRACTION_CHAIN_V1 audit")
    if len(audit.get("matches", [])) != audit.get("idf_100_count"):
        raise SystemExit("requires a complete IDF 100 group mapping")
    pipes = [pipe for pipe in topology["pipes"] if pipe["page"] == args.page]
    by_handle = {}
    for pipe in pipes:
        for handle in pipe["handles"]:
            if handle in by_handle:
                raise SystemExit(f"source handle appears in two P### pipes: {handle}")
            by_handle[handle] = pipe["id"]
    used = set()
    rows = []
    for match in audit["matches"]:
        targets = []
        for handle in match["handles"]:
            pipe_id = by_handle.get(handle)
            if pipe_id is None:
                raise SystemExit(f"{match['idf_id']}: source handle not found in page topology: {handle}")
            if pipe_id not in targets:
                targets.append(pipe_id)
        overlap = used.intersection(targets)
        if overlap:
            raise SystemExit(f"DXF P### reused by two IDF groups: {sorted(overlap)}")
        used.update(targets)
        rows.append({"idf_pipe": match["idf_id"], "dxf_pipes": targets, "confidence": "high",
                     "evidence": "complete_high_support_contraction_chain_v1_plus_exact_source_handle_set",
                     "support_group": match["dxf_group"], "source_handles": match["handles"],
                     "contraction_members": match["members"], "chain_score": match["score"]})
    result = {"algorithm": "PROMOTE_SUPPORT_CONTRACTION_AUDIT_V1", "line_key": args.line_key, "page": args.page,
              "policy": "a group preserves individual DXF P### segments and verified support cuts; it is not a geometric merge",
              "pipe_matches": rows,
              "summary": {"idf_100_count": audit["idf_100_count"], "mapped": len(rows),
                          "dxf_fragment_count": audit["dxf_fragment_count"], "orientation_margin": audit["orientation_margin"]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"line_key": args.line_key, "page": args.page, "mapped": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
