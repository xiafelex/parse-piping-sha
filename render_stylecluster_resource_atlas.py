#!/usr/bin/env python3
"""Render a SHA-only atlas of decoded StyleCluster local resource groups.

This is a diagnostic view of local template geometry, not an ISO page renderer.
Only records with proven local geometry are drawn.  Unknown ellipse formulas
and text content are marked, not invented.
"""

from __future__ import annotations

import argparse
import html
import math
from pathlib import Path

from analyze_iso_split import read_sha_streams
from analyze_psm_hierarchy import (
    parse_stylecluster_18_control_records,
    parse_stylecluster_59_local_ellipse_resources,
    parse_stylecluster_61_local_arc_resources,
    parse_stylecluster_70_fixed_records,
    parse_stylecluster_7c_polygon_groups,
    parse_stylecluster_84_polygon_resources,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    data = read_sha_streams(args.sha)["StyleCluster"]
    groups = parse_stylecluster_7c_polygon_groups(data)["records"]
    polygons = {record["object_ref"]: record for record in parse_stylecluster_84_polygon_resources(data)["records"]}
    arcs = {record["object_ref"]: record for record in parse_stylecluster_61_local_arc_resources(data)["records"]}
    lines = {record["object_ref"]: record for record in parse_stylecluster_18_control_records(data)["records"]}
    ellipses = {record["object_ref"]: record for record in parse_stylecluster_59_local_ellipse_resources(data)["records"]}
    texts = {record["object_ref"]: record for record in parse_stylecluster_70_fixed_records(data)["records"]}

    cells: list[str] = []
    for index, group in enumerate(groups):
        members = group["child_refs"]
        points = [point for ref in members for point in polygons.get(ref, {}).get("points", [])]
        for ref in members:
            if line := lines.get(ref):
                points.extend((line["start"], line["end"]))
            if arc := arcs.get(ref):
                points.append(arc["center"])
            if ellipse := ellipses.get(ref):
                points.extend((
                    [ellipse["center"][0] - ellipse["radius"], ellipse["center"][1] - ellipse["radius"]],
                    [ellipse["center"][0] + ellipse["radius"], ellipse["center"][1] + ellipse["radius"]],
                ))
        if not points:
            points = [[0.0, 0.0]]
        min_x, max_x = min(point[0] for point in points), max(point[0] for point in points)
        min_y, max_y = min(point[1] for point in points), max(point[1] for point in points)
        extent = max(max_x - min_x, max_y - min_y, 0.002)
        scale = 126 / extent
        ox, oy = 30 + (index % 3) * 250, 45 + (index // 3) * 205

        def xy(point: list[float]) -> tuple[float, float]:
            return ox + (point[0] - min_x) * scale, oy + (max_y - point[1]) * scale

        parts = [f'<g transform="translate(0 0)"><rect x="{ox - 18}" y="{oy - 27}" width="205" height="174" fill="#fffdf7" stroke="#8b7d6b"/>']
        parts.append(f'<text x="{ox - 10}" y="{oy - 9}" font-family="monospace" font-size="10">group {group["object_ref"]}</text>')
        for ref in members:
            if polygon := polygons.get(ref):
                path = " ".join(f"{'M' if point_index == 0 else 'L'} {xy(point)[0]:.2f} {xy(point)[1]:.2f}" for point_index, point in enumerate(polygon["points"]))
                parts.append(f'<path d="{path}" fill="#d9e9f4" stroke="#1f5c82" stroke-width="1.3"/>')
            elif line := lines.get(ref):
                x1, y1 = xy(line["start"]); x2, y2 = xy(line["end"])
                parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="#bd4b35" stroke-width="1.3"/>')
            elif arc := arcs.get(ref):
                center_x, center_y = xy(arc["center"]); radius = arc["radius"] * scale
                start = arc["start_angle"]; end = arc["end_angle"]
                sx, sy = center_x + radius * math.cos(start), center_y - radius * math.sin(start)
                ex, ey = center_x + radius * math.cos(end), center_y - radius * math.sin(end)
                large = int(abs(end - start) > math.pi)
                parts.append(f'<path d="M {sx:.2f} {sy:.2f} A {radius:.2f} {radius:.2f} 0 {large} 0 {ex:.2f} {ey:.2f}" fill="none" stroke="#28784c" stroke-width="1.3"/>')
            elif ellipse := ellipses.get(ref):
                center_x, center_y = xy(ellipse["center"])
                radius = ellipse["radius"] * scale
                parts.append(f'<circle cx="{center_x:.2f}" cy="{center_y:.2f}" r="{radius:.2f}" fill="none" stroke="#7c3f99" stroke-width="1.3"/>')
            elif text := texts.get(ref):
                x, y = xy(text["local_anchor_raw"])
                parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.5" fill="#555"/><text x="{x + 4:.2f}" y="{y - 3:.2f}" font-family="Arial" font-size="8">{html.escape(text["font_name"])} anchor</text>')
        parts.append('</g>')
        cells.extend(parts)

    height = 45 + 205 * math.ceil(len(groups) / 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join([
            f'<svg xmlns="http://www.w3.org/2000/svg" width="780" height="{height}" viewBox="0 0 780 {height}">',
            '<rect width="100%" height="100%" fill="#f5f0e7"/>',
            '<text x="20" y="23" font-family="monospace" font-size="13">SHA-only StyleCluster local resource atlas (not physical ISO placement)</text>',
            *cells,
            '</svg>',
        ]),
        encoding="utf-8",
    )
    print(f"StyleCluster resource atlas: {args.output}")


if __name__ == "__main__":
    main()
