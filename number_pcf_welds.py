#!/usr/bin/env python3
"""Assign stable weld numbers in a PCF and map them to SHA weld dots by UCI.

The numbering source of truth becomes the PCF copy.  SHA is used only to
determine which weld appears on which ISO page and where its 2D point lies on
the plotted drawing.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from analyze_iso_split import dynamic_graphics, read_sha_streams
from inject_sha_weld_callouts import point_segment_distance, sheet_streams
from sha_to_svg_prototype import (
    composite_segments,
    ellipse_anchors,
    line_segments,
    psm_bbox,
    template_line_segments,
)


TOP_LEVEL = re.compile(r"^[A-Z][A-Z-]+$")
POINT_RE = re.compile(r"(END-POINT|BRANCH1-POINT)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)")


@dataclass
class WeldBlock:
    start: int
    end: int
    lines: list[str]
    uci: str
    component_identifier: str
    master_component_identifier: str
    skey: str
    endpoint: tuple[float, float, float, float] | None
    description: str
    repeat_weld_identifier: str


def parse_weld_blocks(pcf_path: Path) -> tuple[list[str], list[WeldBlock]]:
    lines = pcf_path.read_text(errors="replace").splitlines()
    starts = [
        (index, line)
        for index, line in enumerate(lines)
        if TOP_LEVEL.fullmatch(line) and line != "MATERIALS"
    ]

    welds: list[WeldBlock] = []
    for index, (start, kind) in enumerate(starts):
        if kind != "WELD":
            continue
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        block_lines = lines[start:end]
        uci = ""
        component_identifier = ""
        master_component_identifier = ""
        skey = ""
        endpoint = None
        description = ""
        repeat_weld_identifier = ""
        for raw in block_lines:
            stripped = raw.strip()
            if stripped.startswith("UCI "):
                uci = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("COMPONENT-IDENTIFIER "):
                component_identifier = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("MASTER-COMPONENT-IDENTIFIER "):
                master_component_identifier = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("SKEY "):
                skey = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("WELD-ATTRIBUTE8 "):
                description = stripped.split(maxsplit=1)[1]
            elif stripped.startswith("REPEAT-WELD-IDENTIFIER "):
                repeat_weld_identifier = stripped.split(maxsplit=1)[1]
            else:
                match = POINT_RE.fullmatch(stripped)
                if match and match.group(1) == "END-POINT" and endpoint is None:
                    endpoint = tuple(float(match.group(i)) for i in range(2, 6))
        if not uci:
            continue
        welds.append(
            WeldBlock(
                start=start,
                end=end,
                lines=block_lines,
                uci=uci,
                component_identifier=component_identifier,
                master_component_identifier=master_component_identifier,
                skey=skey,
                endpoint=endpoint,
                description=description,
                repeat_weld_identifier=repeat_weld_identifier,
            )
        )
    return lines, welds


def sha_weld_points(sha_path: Path, distance_threshold: float) -> dict[str, dict[str, object]]:
    streams = read_sha_streams(sha_path)
    sheets = sheet_streams(streams, None)
    dynamic = dynamic_graphics(streams.get("Unclustered Dynamic Attributes", b""))
    psm = streams.get("PSMcluster0", b"")

    graphic_to_uci = {
        int(record["graphic_ref"]): uci
        for uci, records in dynamic.items()
        for record in records
    }
    graphic_regions = []
    for graphic_ref, uci in graphic_to_uci.items():
        bbox = psm_bbox(psm, graphic_ref)
        if bbox is None:
            continue
        left, bottom, right, top = bbox
        if (right - left) > 45 or (top - bottom) > 45:
            continue
        graphic_regions.append((graphic_ref, uci, (left + right) / 2, (bottom + top) / 2))

    by_uci: dict[str, dict[str, object]] = {}
    for sheet in sheets:
        sheet_name = sheet.stream_name
        data = sheet.data
        page_number = sheet.page
        segments = line_segments(data) + template_line_segments(data) + composite_segments(data)
        vector_refs = {ref for *_, ref, child_ref in segments} | {child_ref for *_, ref, child_ref in segments}
        # The ellipse primitive carries the true page-space centre for the
        # micro weld dot; PSM's tiny envelope can be in a displaced layout
        # coordinate system.
        anchors = ellipse_anchors(data)
        for graphic_ref, uci, center_x, center_y in graphic_regions:
            if graphic_ref in vector_refs:
                continue
            center_x, center_y = anchors.get(graphic_ref, (center_x, center_y))
            best = min(
                point_segment_distance(center_x, center_y, x1 * 16800, y1 * 16800, x2 * 16800, y2 * 16800)
                for x1, y1, x2, y2, _, _ in segments
            )
            if best > distance_threshold:
                continue
            candidate = {
                "uci": uci,
                "graphic_ref": graphic_ref,
                "page": page_number,
                "sheet_stream": sheet_name,
                "sha_x": round(center_x, 3),
                "sha_y": round(center_y, 3),
                "distance_to_geometry": round(best, 3),
            }
            current = by_uci.get(uci)
            if current is None or (
                page_number,
                center_y,
                center_x,
            ) < (
                int(current["page"]),
                float(current["sha_y"]),
                float(current["sha_x"]),
            ):
                by_uci[uci] = candidate
    return by_uci


def number_welds(welds: list[WeldBlock], sha_points: dict[str, dict[str, object]], prefix: str, use_repeat_id: bool) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for weld in welds:
        sha = sha_points.get(weld.uci)
        endpoint = weld.endpoint or (math.inf, math.inf, math.inf, math.inf)
        rows.append(
            {
                "uci": weld.uci,
                "component_identifier": weld.component_identifier,
                "master_component_identifier": weld.master_component_identifier,
                "skey": weld.skey,
                "description": weld.description,
                "pcf_endpoint": list(endpoint),
                "page": sha["page"] if sha else 999999,
                # Retain the exact SHA spatial graphic selected for this PCF
                # weld.  UCI alone can legitimately recur on another ISO page.
                "graphic_ref": sha["graphic_ref"] if sha else None,
                "sha_x": sha["sha_x"] if sha else math.inf,
                "sha_y": sha["sha_y"] if sha else math.inf,
                "sheet_stream": sha["sheet_stream"] if sha else "",
                "distance_to_geometry": sha["distance_to_geometry"] if sha else None,
                "repeat_weld_identifier": weld.repeat_weld_identifier,
            }
        )

    rows.sort(
        key=lambda item: (
            int(item["page"]),
            float(item["sha_y"]) if math.isfinite(float(item["sha_y"])) else math.inf,
            float(item["sha_x"]) if math.isfinite(float(item["sha_x"])) else math.inf,
            float(item["pcf_endpoint"][2]) if item["pcf_endpoint"] else math.inf,
            float(item["pcf_endpoint"][0]) if item["pcf_endpoint"] else math.inf,
            float(item["pcf_endpoint"][1]) if item["pcf_endpoint"] else math.inf,
            item["uci"],
        )
    )
    for index, row in enumerate(rows, start=1):
        repeat_id = str(row["repeat_weld_identifier"])
        row["weld_number"] = f"{prefix}{repeat_id}" if use_repeat_id and repeat_id and repeat_id != "0" else f"{prefix}{index:03d}"
    return rows


def write_numbered_pcf(source_lines: list[str], welds: list[WeldBlock], numbered: list[dict[str, object]], output_path: Path) -> None:
    weld_number_by_uci = {str(row["uci"]): str(row["weld_number"]) for row in numbered}
    result = list(source_lines)
    for weld in welds:
        weld_number = weld_number_by_uci[weld.uci]
        for index in range(weld.start, weld.end):
            if result[index].strip().startswith("WELD-REMARK-NUMBER"):
                result[index] = f"    WELD-REMARK-NUMBER    {weld_number}"
                break
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(result) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcf", type=Path, help="Source PCF file.")
    parser.add_argument("sha", type=Path, help="Matching SHA file from the same ISO.")
    parser.add_argument("--output-pcf", type=Path, required=True, help="Numbered PCF copy to write.")
    parser.add_argument("--output-map", type=Path, required=True, help="JSON weld map to write.")
    parser.add_argument("--prefix", default="S", help="Weld number prefix. Default: S")
    parser.add_argument("--use-repeat-id", action="store_true", help="Use existing REPEAT-WELD-IDENTIFIER values instead of renumbering.")
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=80.0,
        help="Maximum SHA point-to-geometry distance used when deciding whether a weld dot is visible on a page.",
    )
    args = parser.parse_args()

    source_lines, welds = parse_weld_blocks(args.pcf)
    sha_points = sha_weld_points(args.sha, args.distance_threshold)
    numbered = number_welds(welds, sha_points, args.prefix, args.use_repeat_id)
    write_numbered_pcf(source_lines, welds, numbered, args.output_pcf)

    payload = {
        "pcf": str(args.pcf),
        "sha": str(args.sha),
        "prefix": args.prefix,
        "weld_count": len(numbered),
        "welds": numbered,
    }
    args.output_map.parent.mkdir(parents=True, exist_ok=True)
    args.output_map.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")

    mapped = sum(1 for row in numbered if row["page"] != 999999)
    print(f"Welds numbered: {len(numbered)}")
    print(f"Welds mapped to SHA page points: {mapped}")
    print(f"PCF copy: {args.output_pcf}")
    print(f"Weld map: {args.output_map}")


if __name__ == "__main__":
    main()
