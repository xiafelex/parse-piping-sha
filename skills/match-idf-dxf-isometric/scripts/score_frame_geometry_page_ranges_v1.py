#!/usr/bin/env python3
"""Score page-range candidates by junction-to-elbow relative directions."""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path


def number(identifier):
    return int(identifier[1:])


def transform(vector, name):
    x, y = vector
    return {"identity": (x, y), "flip_x": (-x, y), "flip_y": (x, -y), "flip_xy": (-x, -y),
            "swap": (y, x), "swap_flip_x": (-y, x), "swap_flip_y": (y, -x),
            "swap_flip_xy": (-y, -x)}[name]


def unit(vector):
    length = math.hypot(*vector)
    return (vector[0] / length, vector[1] / length) if length else None


def idf_frames(frames, start, end):
    selected = []
    for frame in frames:
        ids = [number(pipe) for pipe in frame["incident_pipes"]]
        if ids and min(ids) >= start and max(ids) <= end and frame.get("centre") is not None:
            selected.append(frame)
    return selected


def dxf_frames(frames, page):
    return [frame for frame in frames if frame["page"] == page and frame.get("centre") is not None]


def score_one(idf, dxf, axis):
    ij = [frame for frame in idf if frame["kind"] == "junction_3"]
    dj = [frame for frame in dxf if frame["kind"] in {"branch", "tee"} and frame["degree"] == 3]
    ie = [frame for frame in idf if frame["kind"] == "turn_2"]
    de = [frame for frame in dxf if frame["kind"] == "elbow" and frame["degree"] == 2]
    if not (len(ij) == len(dj) == 1 and len(ie) == len(de) and len(ie) >= 2):
        return None
    iv = [unit((frame["centre"][0] - ij[0]["centre"][0], frame["centre"][1] - ij[0]["centre"][1])) for frame in ie]
    dv = [unit((frame["centre"][0] - dj[0]["centre"][0], frame["centre"][1] - dj[0]["centre"][1])) for frame in de]
    if any(vector is None for vector in iv + dv):
        return None
    alternatives = []
    for order in itertools.permutations(dv):
        cosines = [sum(left * right for left, right in zip(transform(vector, axis), target))
                   for vector, target in zip(iv, order)]
        alternatives.append((sum(cosines) / len(cosines), min(cosines), cosines))
    alternatives.sort(reverse=True)
    return {"mean_cosine": round(alternatives[0][0], 5), "min_cosine": round(alternatives[0][1], 5),
            "assignment_margin": round(alternatives[0][0] - alternatives[1][0], 5) if len(alternatives) > 1 else None,
            "idf_junction": ij[0]["id"], "dxf_junction": dj[0]["id"],
            "idf_elbows": [frame["id"] for frame in ie], "dxf_elbows": [frame["id"] for frame in de]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("frame_graph", type=Path, help="must retain IDF frame centres")
    parser.add_argument("global_cover", type=Path)
    parser.add_argument("--axis-transform", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.frame_graph.read_text())
    cover = json.loads(args.global_cover.read_text())
    if args.axis_transform not in {"identity", "flip_x", "flip_y", "flip_xy", "swap", "swap_flip_x", "swap_flip_y", "swap_flip_xy"}:
        raise SystemExit("unsupported D4 axis transform")
    all_candidates = [cover["best"]] + cover.get("alternatives", [])
    dxf_pipe_counts = {}
    for row in graph["dxf"]["pipe_frame_incidence"]:
        dxf_pipe_counts[row["page"]] = dxf_pipe_counts.get(row["page"], 0) + 1
    rows = []
    for index, candidate in enumerate(all_candidates):
        if candidate.get("missing_indices") or candidate.get("duplicate_indices"):
            continue
        page_rows = []
        values = []
        pipe_count_deviation = 0
        for page_range in candidate["page_ranges"]:
            start, end = (number(value) for value in page_range["idf_range"])
            detail = score_one(idf_frames(graph["idf"]["frames"], start, end),
                               dxf_frames(graph["dxf"]["frames"], page_range["page"]), args.axis_transform)
            page_rows.append({"page": page_range["page"], "idf_range": page_range["idf_range"], "geometry": detail})
            pipe_count_deviation += abs((end - start + 1) - dxf_pipe_counts.get(page_range["page"], 0))
            if detail is not None:
                values.append(detail["mean_cosine"])
        rows.append({"cover_candidate_index": index, "geometry_mean": round(sum(values) / len(values), 5) if values else None,
                     "geometry_evidence_pages": len(values), "pipe_count_deviation": pipe_count_deviation, "pages": page_rows})
    ranked = sorted((row for row in rows if row["geometry_mean"] is not None),
                    key=lambda row: (-row["geometry_mean"], row["pipe_count_deviation"]))
    geometry_separated = len(ranked) >= 2 and ranked[0]["geometry_mean"] - ranked[1]["geometry_mean"] >= .10
    count_breaks_geometry_tie = (len(ranked) >= 2 and abs(ranked[0]["geometry_mean"] - ranked[1]["geometry_mean"]) < .00001
                                 and ranked[0]["pipe_count_deviation"] < ranked[1]["pipe_count_deviation"])
    result = {"algorithm": "JUNCTION_ELBOW_RELATIVE_GEOMETRY_V1",
              "policy": "requires independently calibrated axis; scores page range only, never individual I-to-P pairs",
              "axis_transform": args.axis_transform, "candidates": rows,
              "status": "unique_geometry_range_cover_candidate" if geometry_separated else
                        "unique_geometry_plus_pipe_count_range_cover_candidate" if count_breaks_geometry_tie else
                        "geometry_non_discriminating"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"status": result["status"], "ranked_count": len(ranked),
                      "best": ranked[0]["geometry_mean"] if ranked else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
