#!/usr/bin/env python3
"""Build PCF-topology weld lanes for SHA callout placement.

PIPE blocks are the straight-run source of truth; ELBOW blocks split runs.
For each mapped weld, this script assigns the nearest PCF PIPE segment and an
alternating side by its pipe-component order.  The result is a traceable plan,
not a visual/PDF-derived layout.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


# A PIPE can contain top-level-looking auxiliary records such as FLOW-ARROW
# and SUPPORT.  Only actual physical components terminate a component block.
COMPONENT_KINDS = {
    "PIPE", "ELBOW", "WELD", "TEE", "FLANGE", "REDUCER", "VALVE",
    "CAP", "OLET", "BEND", "INSTRUMENT", "FILTER", "TRAP",
}
POINT_RE = re.compile(r"(?:END-POINT|BRANCH1-POINT)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)")


@dataclass
class PipeSegment:
    component_id: int
    file_order: int
    start: tuple[float, float, float]
    end: tuple[float, float, float]


def distance_to_segment(point: tuple[float, float, float], segment: PipeSegment) -> float:
    ax, ay, az = segment.start
    bx, by, bz = segment.end
    px, py, pz = point
    dx, dy, dz = bx - ax, by - ay, bz - az
    length_sq = dx * dx + dy * dy + dz * dz
    if length_sq == 0:
        return math.dist(point, segment.start)
    factor = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy + (pz - az) * dz) / length_sq))
    return math.dist(point, (ax + factor * dx, ay + factor * dy, az + factor * dz))


def parse_pipe_segments(path: Path) -> list[PipeSegment]:
    lines = path.read_text(errors="replace").splitlines()
    starts = [
        (index, line)
        for index, line in enumerate(lines)
        if line in COMPONENT_KINDS
    ]
    pipes: list[PipeSegment] = []
    for position, (start, kind) in enumerate(starts):
        if kind != "PIPE":
            continue
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        component_id = None
        points: list[tuple[float, float, float]] = []
        for raw in lines[start:end]:
            text = raw.strip()
            if text.startswith("COMPONENT-IDENTIFIER"):
                component_id = int(text.split()[-1])
            # PCF END-POINT records normally append bore and sometimes an end
            # type after XYZ, so deliberately match just the coordinate prefix.
            match = POINT_RE.match(text)
            if match:
                points.append(tuple(float(match.group(index)) for index in range(1, 4)))
        if component_id is not None and len(points) >= 2 and points[0] != points[1]:
            pipes.append(PipeSegment(component_id, start, points[0], points[1]))
    # Component identifiers are not a traversal sequence.  PCF records are
    # emitted in path order for this ISO, so retain their source-file order.
    return sorted(pipes, key=lambda item: item.file_order)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcf", type=Path)
    parser.add_argument("weld_map", type=Path)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-distance", type=float, default=2.0)
    args = parser.parse_args()

    pipes = parse_pipe_segments(args.pcf)
    pipe_rank = {pipe.component_id: index for index, pipe in enumerate(pipes)}
    welds = json.loads(args.weld_map.read_text())["welds"]
    plan: list[dict[str, object]] = []
    for weld in welds:
        if int(weld.get("page", 999999)) != args.page:
            continue
        point = tuple(float(value) for value in weld["pcf_endpoint"][:3])
        pipe = min(pipes, key=lambda item: distance_to_segment(point, item))
        distance = distance_to_segment(point, pipe)
        rank = pipe_rank[pipe.component_id]
        plan.append(
            {
                "weld_number": weld["weld_number"],
                "uci": weld["uci"],
                "graphic_ref": weld["graphic_ref"],
                "page": args.page,
                "pipe_component_id": pipe.component_id,
                "pipe_rank": rank,
                "side": "left" if rank % 2 == 0 else "right",
                "pcf_distance_to_pipe": round(distance, 6),
                "assignment": "on_pipe" if distance <= args.max_distance else "nearest_pipe_inherited",
                "pipe_start": list(pipe.start),
                "pipe_end": list(pipe.end),
            }
        )
    payload = {
        "pcf": str(args.pcf),
        "page": args.page,
        "rule": "PCF PIPE straight runs retain PCF source order; side flips at each successive straight run. Connection-point welds inherit their nearest run.",
        "pipe_segment_count": len(pipes),
        "planned_weld_count": len(plan),
        "welds": plan,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"PCF pipe segments: {len(pipes)}")
    print(f"Page {args.page} planned welds: {len(plan)}")
    for row in plan:
        print(f"{row['weld_number']} -> PIPE {row['pipe_component_id']} ({row['side']})")


if __name__ == "__main__":
    main()
