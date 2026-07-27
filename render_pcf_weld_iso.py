#!/usr/bin/env python3
"""Render a PCF-only isometric PNG with PCF weld identifiers.

The renderer intentionally uses only PCF geometry.  A non-empty
REPEAT-WELD-IDENTIFIER is displayed as W<id>; WELD-REMARK-NUMBER takes
precedence when its line contains a value.
"""

from __future__ import annotations

import argparse
import html
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


KINDS = {
    "PIPE", "ELBOW", "WELD", "TEE", "TEE-STUB", "FLANGE", "FLANGE-BLIND",
    "REDUCER", "VALVE", "CAP", "OLET", "BEND", "FILTER", "TRAP",
}
POINT = re.compile(r"(?:END-POINT|BRANCH1-POINT)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)")


@dataclass
class Component:
    kind: str
    points: list[tuple[float, float, float]]
    weld_id: str | None


def parse_pcf(path: Path) -> tuple[str, list[Component]]:
    lines = path.read_text(errors="replace").splitlines()
    starts = [(index, line) for index, line in enumerate(lines) if line in KINDS]
    line_name = path.stem.replace("-pcf", "")
    for raw in lines:
        if raw.startswith("PIPELINE-REFERENCE"):
            line_name = raw.split()[-1]
            break
    components: list[Component] = []
    for pos, (start, kind) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        block = [line.strip() for line in lines[start:end]]
        points = [tuple(float(match.group(i)) for i in range(1, 4)) for line in block if (match := POINT.match(line))]
        weld_id = None
        if kind == "WELD":
            for line in block:
                if line.startswith("WELD-REMARK-NUMBER") and len(line.split(maxsplit=1)) == 2:
                    weld_id = line.split(maxsplit=1)[1]
                if line.startswith("REPEAT-WELD-IDENTIFIER") and line.split()[-1] != "0" and weld_id is None:
                    weld_id = line.split()[-1]
        components.append(Component(kind, points, weld_id))
    return line_name, components


def iso(point: tuple[float, float, float]) -> tuple[float, float]:
    x, y, z = point
    return ((x - y) * 0.8660254, z - (x + y) * 0.5)


def render(path: Path, output: Path, width: int = 1800, height: int = 1200) -> None:
    line_name, components = parse_pcf(path)
    raw_points = [iso(point) for component in components for point in component.points]
    if not raw_points:
        raise ValueError(f"No PCF coordinates found in {path}")
    min_x, max_x = min(x for x, _ in raw_points), max(x for x, _ in raw_points)
    min_y, max_y = min(y for _, y in raw_points), max(y for _, y in raw_points)
    # Reserve a title band plus room for callouts above/below the geometry.
    scale = min((width - 260) / max(1.0, max_x - min_x), (height - 360) / max(1.0, max_y - min_y))

    def screen(point: tuple[float, float]) -> tuple[float, float]:
        return (130 + (point[0] - min_x) * scale, 240 + (max_y - point[1]) * scale)

    geometry: list[str] = []
    welds: list[tuple[float, float, str]] = []
    for component in components:
        points = [screen(iso(point)) for point in component.points]
        if component.kind == "WELD" and points:
            if component.weld_id:
                welds.append((*points[0], f"W{component.weld_id}"))
            geometry.append(f'<circle cx="{points[0][0]:.1f}" cy="{points[0][1]:.1f}" r="4" fill="#111"/>')
            continue
        if len(points) >= 2:
            style = "stroke:#111;stroke-width:4;fill:none" if component.kind == "PIPE" else "stroke:#43515c;stroke-width:3;fill:none"
            geometry.append('<path d="M ' + ' L '.join(f'{x:.1f} {y:.1f}' for x, y in points[:2]) + f'" style="{style}"/>')
            if component.kind in {"FLANGE", "FLANGE-BLIND"}:
                x, y = points[0]
                geometry.append(f'<rect x="{x-7:.1f}" y="{y-7:.1f}" width="14" height="14" fill="white" stroke="#111" stroke-width="2"/>')

    callouts: list[str] = []
    placed: list[tuple[float, float]] = []
    for index, (x, y, label) in enumerate(welds):
        angle = -math.pi / 2 if index % 2 == 0 else math.pi / 2
        radius = 70.0
        for _ in range(12):
            cx, cy = x + math.cos(angle) * radius, y + math.sin(angle) * radius
            if all(math.hypot(cx - ox, cy - oy) > 65 for ox, oy in placed):
                break
            radius += 42
        placed.append((cx, cy))
        edge_x = cx - math.cos(angle) * 20
        edge_y = cy - math.sin(angle) * 20
        callouts.append(f'<path d="M {x:.1f} {y:.1f} L {edge_x:.1f} {edge_y:.1f}" stroke="#38434d" stroke-width="1.5"/>')
        callouts.append(f'<path d="M {cx:.1f} {cy-20:.1f} L {cx+20:.1f} {cy:.1f} L {cx:.1f} {cy+20:.1f} L {cx-20:.1f} {cy:.1f} Z" fill="white" stroke="#38434d" stroke-width="1.5"/>')
        callouts.append(f'<text x="{cx:.1f}" y="{cy+4:.1f}" text-anchor="middle" font-family="monospace" font-size="12">{html.escape(label)}</text>')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="white"/><rect x="25" y="25" width="{width-50}" height="{height-50}" fill="none" stroke="#111"/>
<text x="55" y="70" font-family="monospace" font-size="24" font-weight="bold">PCF ISOMETRIC: {html.escape(line_name)}</text>
<text x="55" y="96" font-family="monospace" font-size="14">PCF-only reconstruction | weld labels from REPEAT-WELD-IDENTIFIER</text>
{''.join(geometry)}{''.join(callouts)}</svg>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    svg_path = output.with_suffix(".svg")
    svg_path.write_text(svg, encoding="utf-8")
    # sips renders SVG transparency incorrectly on this macOS build; Quick
    # Look produces a faithful white-background PNG from the same vector SVG.
    subprocess.run(["qlmanage", "-t", "-s", str(width), "-o", str(output.parent), str(svg_path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    quicklook_output = output.parent / f"{svg_path.name}.png"
    quicklook_output.replace(output)
    print(f"{path.name}: {len(welds)} numbered welds -> {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcf", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render(args.pcf, args.output)


if __name__ == "__main__":
    main()
