#!/usr/bin/env python3
"""Extract direction from source-verified DXF flow-arrow wedges.

The first three vertices are the wedge outline in the source order used by the
DXF semantic-recognition skill.  Do not inspect all hatch vertices: their tiny
interior angles are not arrow tips.
"""
import argparse
import json
import math
import re
from pathlib import Path
import ezdxf
def angle(a, b, c):
    u = (a[0] - b[0], a[1] - b[1])
    v = (c[0] - b[0], c[1] - b[1])
    return math.degrees(math.acos(max(-1, min(1, (u[0] * v[0] + u[1] * v[1]) / math.hypot(*u) / math.hypot(*v)))))


def points(entity):
    if entity.dxftype() == "POLYLINE":
        return [(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in entity.vertices]
    if entity.dxftype() == "LWPOLYLINE":
        return [(vertex[0], vertex[1]) for vertex in entity.get_points("xy")]
    return []


def arrow_handle(title):
    fields = [field.strip() for field in title.split("|")]
    if len(fields) < 3 or not re.fullmatch(r"[0-9A-Fa-f]+", fields[1]):
        raise ValueError(f"cannot read verified arrow handle from semantic title: {title!r}")
    return fields[1]


def join_topology(arrows, topology_path):
    """Attach stable P### IDs and determine their source-vector orientation."""
    if topology_path is None:
        return
    topology = json.loads(topology_path.read_text())
    by_handles = {frozenset(pipe["handles"]): pipe for pipe in topology["pipes"]}
    for arrow in arrows:
        pipe = by_handles.get(frozenset(arrow["pipe_handles"]))
        if pipe is None:
            arrow["topology_join"] = "unresolved"
            continue
        first, last = pipe["endpoints"]
        tangent = (last[0] - first[0], last[1] - first[1])
        dot = arrow["vector"][0] * tangent[0] + arrow["vector"][1] * tangent[1]
        arrow.update({
            "topology_pipe": pipe["id"],
            "page": pipe["page"],
            "pipe_endpoints": pipe["endpoints"],
            "flow_endpoint_order": [first, last] if dot > 0 else [last, first],
            "flow_alignment_dot": round(dot, 6),
            "topology_join": "exact_source_handle_set",
        })
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dxf", type=Path)
    parser.add_argument("semantic", type=Path)
    parser.add_argument("--dxf-topology", type=Path, help="global DXF pipe topology for stable P### IDs")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    drawing = ezdxf.readfile(args.dxf)
    semantic = json.loads(args.semantic.read_text())
    arrows = []
    for row in semantic["pipes"]:
        if row["kind"] != "arrow_pipe":
            continue
        handle = arrow_handle(row["title"])
        entity = drawing.entitydb.get(handle)
        outline = points(entity) if entity else []
        if len(outline) < 3:
            raise ValueError(f"{handle}: expected a source wedge POLYLINE/LWPOLYLINE with three outline vertices")
        triangle = outline[:3]
        tip_index = min(range(3), key=lambda index: angle(triangle[(index - 1) % 3], triangle[index], triangle[(index + 1) % 3]))
        tip = triangle[tip_index]
        tail = ((triangle[(tip_index - 1) % 3][0] + triangle[(tip_index + 1) % 3][0]) / 2,
                (triangle[(tip_index - 1) % 3][1] + triangle[(tip_index + 1) % 3][1]) / 2)
        arrows.append({"arrow_handle": handle, "pipe_handles": row["handles"], "tip": tip, "tail": tail,
                       "vector": [tip[0] - tail[0], tip[1] - tail[1]],
                       "tip_angle_deg": round(angle(triangle[(tip_index - 1) % 3], tip, triangle[(tip_index + 1) % 3]), 3)})
    join_topology(arrows, args.dxf_topology)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"algorithm": "FLOW_WEDGE_TIP_V1", "arrows": arrows}, indent=2))
    print(json.dumps(arrows))
if __name__=='__main__':main()
