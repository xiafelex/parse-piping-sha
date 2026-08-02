#!/usr/bin/env python3
"""Promote a complete high-confidence CHAIN_100_V1 audit to stable P### IDs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("match_audit", type=Path)
    parser.add_argument("dxf_topology", type=Path)
    parser.add_argument("--line-key", required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.match_audit.read_text())
    topology = json.loads(args.dxf_topology.read_text())
    if audit.get("algorithm") != "CHAIN_100_V1" or audit.get("confidence") != "high":
        raise SystemExit("requires a high-confidence CHAIN_100_V1 audit")
    if len(audit.get("matches", [])) != audit.get("idf_100_count"):
        raise SystemExit("requires a complete IDF 100 mapping")
    topology_by_handles = {frozenset(pipe["handles"]): pipe for pipe in topology["pipes"]
                           if pipe["page"] == args.page}
    rows = []
    used = set()
    for match in audit["matches"]:
        candidates = [pipe for handles, pipe in topology_by_handles.items() if handles == frozenset(match["handles"])]
        if len(candidates) != 1:
            raise SystemExit(f"{match['idf_id']}: expected one exact source-handle pipe, found {len(candidates)}")
        pipe = candidates[0]
        if pipe["id"] in used:
            raise SystemExit(f"duplicate target pipe: {pipe['id']}")
        used.add(pipe["id"])
        rows.append({"idf_pipe": match["idf_id"], "dxf_pipe": pipe["id"], "confidence": "high",
                     "evidence": "complete_high_chain_100_v1_plus_exact_source_handle_set",
                     "source_handles": match["handles"], "chain_score": match["score"]})
    result = {"algorithm": "PROMOTE_CHAIN_100_AUDIT_V1", "line_key": args.line_key, "page": args.page,
              "policy": "only a complete high CHAIN_100_V1 result may be promoted; source handle set must join exactly once",
              "pipe_matches": rows,
              "summary": {"idf_100_count": audit["idf_100_count"], "mapped": len(rows),
                          "orientation_margin": audit["orientation_margin"]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"line_key": args.line_key, "page": args.page, "mapped": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
