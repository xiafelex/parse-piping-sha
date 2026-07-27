#!/usr/bin/env python3
"""Audit paired PCF types against SHA-only rendered-geometry coverage."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from analyze_iso_split import dynamic_graphics, pcf_components, read_sha_streams
from sha_to_svg_prototype import (
    SHEET_UNIT,
    composite_arcs,
    composite_segments,
    intersects_bbox,
    line_segments,
    page_uci_regions,
    psm_bbox,
    psm_ellipses,
    template_line_segments,
    visible_connection_points,
)


def has_geometry(
    sheet: bytes,
    psm: bytes,
    bbox: tuple[int, int, int, int],
    template_segments: list[tuple[float, float, float, float, int, int]],
    connection_refs: set[int],
    graphic_ref: int,
) -> bool:
    if graphic_ref in connection_refs:
        return True
    left, bottom, right, top = bbox
    padded = (left - 80, bottom - 80, right + 80, top + 80)
    segments = line_segments(sheet) + template_segments + composite_segments(sheet)
    if any(
        intersects_bbox(x1 * SHEET_UNIT, y1 * SHEET_UNIT, x2 * SHEET_UNIT, y2 * SHEET_UNIT, padded)
        for x1, y1, x2, y2, _, _ in segments
    ):
        return True
    if any(not (r < padded[0] or l > padded[2] or t < padded[1] or b > padded[3])
           for _, (l, b, r, t) in psm_ellipses(sheet, psm)):
        return True
    return any(not (r < padded[0] or l > padded[2] or t < padded[1] or b > padded[3])
               for l, b, r, t in composite_arcs(sheet).values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("--out", type=Path, default=Path("output/left_text_ten_sample/component_coverage.json"))
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    uncovered: list[dict[str, object]] = []
    total = 0
    for drawing in selection["selected_drawings"]:
        _, components = pcf_components(Path(drawing["pcf"]))
        pcf_kind = {str(component["uci"]): str(component["kind"]) for component in components}
        streams = read_sha_streams(Path(drawing["sha"]))
        psm = streams.get("PSMcluster0", b"")
        dynamic = dynamic_graphics(streams.get("Unclustered Dynamic Attributes", b""))
        template_segments = template_line_segments(streams.get("Sheet221", b""))
        for page in drawing["pages_with_targets"]:
            sheet = streams[page["sheet_stream"]]
            refs, regions = page_uci_regions(sheet, psm, dynamic)
            connection_refs = {
                int(region["graphic_ref"])
                for region in visible_connection_points(sheet, psm, regions, template_segments)
            }
            for ref, ucis in refs.items():
                bbox = psm_bbox(psm, ref)
                if bbox is None:
                    continue
                for uci in sorted(set(ucis)):
                    total += 1
                    if has_geometry(sheet, psm, bbox, template_segments, connection_refs, ref):
                        continue
                    uncovered.append({
                        "drawing": drawing["drawing"],
                        "sheet_stream": page["sheet_stream"],
                        "uci": uci,
                        "pcf_kind": pcf_kind.get(uci, "SHA-only"),
                        "graphic_ref": f"0x{ref:08X}",
                        "psm_bbox_page_units": list(bbox),
                    })
    report = {
        "scope": "PCF supplies component kind only; geometry coverage is calculated from SHA streams.",
        "checked_uci_instances": total,
        "uncovered_count": len(uncovered),
        "uncovered_by_pcf_kind": dict(sorted(Counter(row["pcf_kind"] for row in uncovered).items())),
        "uncovered": uncovered,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("checked_uci_instances", "uncovered_count", "uncovered_by_pcf_kind")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
