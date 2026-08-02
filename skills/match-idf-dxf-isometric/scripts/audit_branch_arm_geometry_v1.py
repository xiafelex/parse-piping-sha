#!/usr/bin/env python3
"""Reject branch-arm direction matching when local IDF/DXF geometry disagrees."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def unit(vector):
    size = math.hypot(*vector)
    return (vector[0] / size, vector[1] / size) if size else None


def away(pipe, centre, point_a, point_b):
    endpoint = max((point_a, point_b), key=lambda point: math.dist(point, centre))
    return unit((endpoint[0] - centre[0], endpoint[1] - centre[1]))


def parallelness(left, right):
    return abs(left[0] * right[0] + left[1] * right[1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("frame_graph", type=Path)
    parser.add_argument("dxf_topology", type=Path)
    parser.add_argument("propagation", type=Path)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.frame_graph.read_text())
    topology = json.loads(args.dxf_topology.read_text())
    source = json.loads(args.propagation.read_text())
    idf_frames = {row["id"]: row for row in graph["idf"]["frames"]}
    dxf_frames = {row["id"]: row for row in graph["dxf"]["frames"] if row["page"] == args.page}
    idf_pipes = {row["id"]: row for row in graph["idf"]["pipe_geometry"]}
    dxf_pipes = {row["id"]: row for row in topology["pipes"] if row["page"] == args.page}
    frame_map = {row["idf_frame"]: row["dxf_frame"] for row in source.get("frame_matches", [])}
    pipe_map = {row["idf_pipe"]: row["dxf_pipe"] for row in source["pipe_matches"] if row.get("dxf_pipe")}
    audits = []
    for idf_id, dxf_id in frame_map.items():
        left, right = idf_frames.get(idf_id), dxf_frames.get(dxf_id)
        if not left or not right or left["kind"] != "junction_3" or right["kind"] not in {"branch", "tee"}:
            continue
        anchored = {pipe: pipe_map[pipe] for pipe in left["incident_pipes"]
                    if pipe in pipe_map and pipe_map[pipe] in right["incident_pipes"]}
        remaining_left = [pipe for pipe in left["incident_pipes"] if pipe not in anchored]
        remaining_right = [pipe for pipe in right["incident_pipes"] if pipe not in anchored.values()]
        if not (len(remaining_left) == len(remaining_right) == 2):
            continue
        idf_vectors = [away(idf_pipes[pipe], left["centre"], idf_pipes[pipe]["a2"], idf_pipes[pipe]["b2"])
                       for pipe in remaining_left]
        dxf_vectors = [away(dxf_pipes[pipe], right["centre"], *dxf_pipes[pipe]["endpoints"])
                       for pipe in remaining_right]
        if None in idf_vectors or None in dxf_vectors:
            continue
        idf_parallel = parallelness(*idf_vectors)
        dxf_parallel = parallelness(*dxf_vectors)
        mismatch = idf_parallel >= .98 and dxf_parallel < .90
        audits.append({"idf_frame": idf_id, "dxf_frame": dxf_id,
                       "anchored_arms": anchored, "unmatched_idf_arms": remaining_left,
                       "unmatched_dxf_arms": remaining_right,
                       "idf_abs_direction_cosine": round(idf_parallel, 5),
                       "dxf_abs_direction_cosine": round(dxf_parallel, 5),
                       "status": "branch_geometry_nonisomorphic_reject_direction" if mismatch else "geometry_not_rejected"})
    result = {"algorithm": "BRANCH_ARM_GEOMETRY_CONSISTENCY_V1",
              "policy": "only reject direction promotion; do not invent a branch-arm mapping",
              "audits": audits}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"audited": len(audits), "rejected": sum(a["status"].startswith("branch_geometry") for a in audits)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
