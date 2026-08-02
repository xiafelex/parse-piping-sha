#!/usr/bin/env python3
"""Validate whether IDF 149 FLOW + following 100 agrees with DXF wedge flow.

This is intentionally a regression/audit tool.  It evaluates only already
matched I-to-P pairs and therefore cannot bootstrap a new correspondence.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def number(value):
    return int(value[1:])


def project(point, origin):
    x, y, z = (point[index] - origin[index] for index in range(3))
    return ((x - y) * .5, (x + y) * .288675 - z * .57735)


def transform(vector, name):
    x, y = vector
    return {"identity": (x, y), "flip_x": (-x, y), "flip_y": (x, -y), "flip_xy": (-x, -y),
            "swap": (y, x), "swap_flip_x": (-y, x), "swap_flip_y": (y, -x),
            "swap_flip_xy": (-y, -x)}[name]


def cosine(left, right):
    denominator = math.hypot(*left) * math.hypot(*right)
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else None


def read_idf(path):
    result, pending = [], None
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if not fields:
            continue
        try:
            code = int(fields[0])
        except ValueError:
            continue
        if code == 149 and "FLOW" in line.upper() and len(fields) >= 4:
            pending = tuple(map(float, fields[1:4]))
        elif code == 100 and len(fields) >= 8:
            result.append({"id": f"I{len(result) + 1:03d}", "a": tuple(map(float, fields[1:4])),
                           "b": tuple(map(float, fields[4:7])), "flow_marker": pending})
            pending = None
    origin = tuple(min(min(row["a"][axis], row["b"][axis]) for row in result) for axis in range(3))
    return {row["id"]: row for row in result}, origin


def pipe_id_by_handles(topology):
    return {frozenset(row["handles"]): row["id"] for row in topology["pipes"]}


def matching_rows(data):
    return data.get("pipe_matches", data.get("matches", []))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("idf", type=Path)
    parser.add_argument("flow_audit", type=Path)
    parser.add_argument("dxf_topology", type=Path)
    parser.add_argument("verified_matches", type=Path)
    parser.add_argument("--axis-transform", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    idf, origin = read_idf(args.idf)
    arrows = json.loads(args.flow_audit.read_text())["arrows"]
    topology = json.loads(args.dxf_topology.read_text())
    matches = matching_rows(json.loads(args.verified_matches.read_text()))
    handle_lookup = pipe_id_by_handles(topology)
    by_dxf = {row.get("dxf_pipe"): row.get("idf_pipe") for row in matches if row.get("dxf_pipe") and row.get("idf_pipe")}
    observations = []
    for arrow in arrows:
        pipe = arrow.get("topology_pipe") or handle_lookup.get(frozenset(arrow["pipe_handles"]))
        identifier = by_dxf.get(pipe)
        record = idf.get(identifier)
        if record is None or record["flow_marker"] is None:
            continue
        a2, b2 = project(record["a"], origin), project(record["b"], origin)
        vector = transform((b2[0] - a2[0], b2[1] - a2[1]), args.axis_transform)
        score = cosine(vector, arrow["vector"])
        if score is None:
            continue
        observations.append({"idf_pipe": identifier, "dxf_pipe": pipe, "arrow_handle": arrow["arrow_handle"],
                             "idf149_point": record["flow_marker"], "direction_cosine": round(score, 6)})
    aligned = sum(row["direction_cosine"] >= .9 for row in observations)
    result = {"algorithm": "IDF149_TO_DXF_FLOW_DIRECTION_AUDIT_V1",
              "policy": "only evaluates independently verified I-to-P pairs; 149 attaches to the following 100 and is the only case where 100 a-to-b may be tested as flow",
              "axis_transform": args.axis_transform, "observations": observations,
              "aligned_count": aligned, "non_aligned_count": len(observations) - aligned,
              "status": "flow_direction_regression_confirmed" if len(observations) >= 3 and aligned == len(observations) else
                        "mixed_direction_observations" if observations and aligned and aligned < len(observations) else
                        "insufficient_direction_observations"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"status": result["status"], "observations": len(observations)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
