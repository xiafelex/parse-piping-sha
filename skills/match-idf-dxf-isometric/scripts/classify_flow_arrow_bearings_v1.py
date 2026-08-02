#!/usr/bin/env python3
"""Express verified flow-wedge directions relative to a verified north vector.

Both inputs are source-vector audits.  The output is a geometric observation
only: it never assumes that IDF ``100 a -> b`` is a process-flow direction.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def norm(vector):
    length = math.hypot(vector[0], vector[1])
    if length == 0:
        raise ValueError("zero-length direction vector")
    return (vector[0] / length, vector[1] / length)


def clockwise_degrees(north, vector):
    # atan2(cross, dot) is positive counter-clockwise in DXF coordinates.
    cross = north[0] * vector[1] - north[1] * vector[0]
    dot = north[0] * vector[0] + north[1] * vector[1]
    return round((-math.degrees(math.atan2(cross, dot))) % 360.0, 3)


def octant(angle):
    labels = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return labels[int((angle + 22.5) // 45) % 8]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("flow_audit", type=Path)
    parser.add_argument("north_audit", type=Path)
    parser.add_argument("--allow-north-candidate", action="store_true",
                        help="emit review-only bearings from a visually inspected north candidate")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    flow = json.loads(args.flow_audit.read_text())
    north = json.loads(args.north_audit.read_text())
    result = {
        "algorithm": "FLOW_WEDGE_NORTH_BEARING_V1",
        "policy": "triangle minimum-angle tip gives flow direction; north is corroborating orientation only, never an IDF 100 direction assumption",
        "status": "north_reference_not_ready",
        "arrows": [],
    }
    north_ready = north.get("status") == "visually_confirmed"
    north_candidate = args.allow_north_candidate and north.get("status") == "candidate_requires_visual_confirmation"
    if (not north_ready and not north_candidate) or "vector_candidate" not in north:
        result["north_status"] = north.get("status")
    else:
        north_vector = norm(north["vector_candidate"])
        result.update({"status": "bearing_observations_ready" if north_ready else "bearing_observations_candidate_north",
                       "north_symbol_handle": north.get("north_symbol_handle"),
                       "north_unit_vector": [round(value, 6) for value in north_vector]})
        for arrow in flow["arrows"]:
            vector = norm(arrow["vector"])
            bearing = clockwise_degrees(north_vector, vector)
            result["arrows"].append({**arrow, "bearing_clockwise_from_north_deg": bearing,
                                     "compass_octant": octant(bearing)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"status": result["status"], "arrow_count": len(result["arrows"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
