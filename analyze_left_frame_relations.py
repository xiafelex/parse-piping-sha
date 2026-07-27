#!/usr/bin/env python3
"""Audit SHA-only relationships between left ISO note text and closed frames.

The paired PDF is deliberately not an input.  This utility is evidence
collection for a renderer rule: a direct Sheet text anchor inside a direct
closed rectangle can disagree with the PSM glyph envelope when Shape2D stores
the envelope in a local layout space.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from analyze_iso_split import read_sha_streams
from sha_to_svg_prototype import (
    PAGE_HEIGHT,
    SHEET_UNIT,
    composite_segments,
    line_segments,
    psm_bbox,
    rectangles_by_parent_ref,
    sheet_rectangles,
    template_line_segments,
    text_records,
)


def contains(frame: tuple[int, int, int, int], x: float, y: float, margin: float = 0) -> bool:
    left, bottom, right, top = frame
    return left - margin <= x <= right + margin and bottom - margin <= y <= top + margin


def overlap_area(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> int:
    left = max(first[0], second[0])
    bottom = max(first[1], second[1])
    right = min(first[2], second[2])
    top = min(first[3], second[3])
    return max(0, right - left) * max(0, top - bottom)


def nearest_frame(
    frames: list[tuple[int, int, int, int]], x: float, y: float
) -> tuple[int, int, int, int] | None:
    if not frames:
        return None
    return min(
        frames,
        key=lambda frame: abs((frame[0] + frame[2]) / 2 - x)
        + abs((frame[1] + frame[3]) / 2 - y),
    )


def classify(
    frame: tuple[int, int, int, int] | None,
    bbox: tuple[int, int, int, int] | None,
) -> str:
    if frame is None:
        return "no-direct-frame-at-anchor"
    if bbox is None:
        return "direct-frame-no-psm-glyph-box"
    if overlap_area(frame, bbox):
        return "anchor-frame-and-psm-overlap"
    return "anchor-frame-psm-disjoint"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/left_text_ten_sample/frame_relation_audit.json"),
    )
    args = parser.parse_args()

    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    prefixes = tuple(value.upper() for value in selection["targets"])
    records: list[dict[str, object]] = []

    for drawing in selection["selected_drawings"]:
        streams = read_sha_streams(Path(drawing["sha"]))
        psm = streams.get("PSMcluster0", b"")
        for page in drawing["pages_with_targets"]:
            sheet_name = page["sheet_stream"]
            sheet = streams[sheet_name]
            source_segments = {
                "ordinary": line_segments(sheet),
                "alternate": template_line_segments(sheet),
                "composite": composite_segments(sheet),
            }
            segments = [segment for values in source_segments.values() for segment in values]
            frames = sheet_rectangles(segments)
            frame_sources: dict[tuple[int, int, int, int], list[str]] = {}
            for source, values in source_segments.items():
                for parent_ref, frame in rectangles_by_parent_ref(values).items():
                    frame_sources.setdefault(frame, []).append(f"{source}:0x{parent_ref:04X}")
            for text in text_records(sheet):
                value = str(text["text"]).strip()
                if not value or not any(value.upper().startswith(prefix) for prefix in prefixes):
                    continue
                anchor_x = float(text["x"]) * SHEET_UNIT
                anchor_y = float(text["y"]) * SHEET_UNIT
                bbox = psm_bbox(psm, int(text["graphic_ref"]))
                anchor_frames = [frame for frame in frames if contains(frame, anchor_x, anchor_y, margin=12)]
                psm_frames = [frame for frame in frames if bbox and overlap_area(frame, bbox)]
                frame = nearest_frame(anchor_frames, anchor_x, anchor_y)
                state = classify(frame, bbox)
                record: dict[str, object] = {
                    "drawing": drawing["drawing"],
                    "sheet_stream": sheet_name,
                    "text": value,
                    "graphic_ref": f"0x{int(text['graphic_ref']):08X}",
                    "style_ref": f"0x{int(text['style_ref']):08X}",
                    "anchor_page_units": [round(anchor_x, 1), round(anchor_y, 1)],
                    "psm_bbox_page_units": list(bbox) if bbox else None,
                    "direct_frame_page_units": list(frame) if frame else None,
                    "direct_frame_sources": frame_sources.get(frame, []) if frame else [],
                    "psm_overlapping_frames_page_units": [list(candidate) for candidate in psm_frames],
                    "psm_overlapping_frame_sources": [frame_sources.get(candidate, []) for candidate in psm_frames],
                    "relation": state,
                }
                if frame and bbox:
                    record["psm_overlap_ratio_of_frame"] = round(
                        overlap_area(frame, bbox) / ((frame[2] - frame[0]) * (frame[3] - frame[1])), 4
                    )
                    record["psm_offset_from_anchor"] = [
                        round(bbox[0] - anchor_x, 1),
                        round(bbox[1] - anchor_y, 1),
                    ]
                records.append(record)

    counts = Counter(str(record["relation"]) for record in records)
    result = {
        "scope": "SHA-only note/frame relationship audit; PDF is not read.",
        "drawings": len(selection["selected_drawings"]),
        "prefixes": prefixes,
        "record_count": len(records),
        "relation_counts": dict(sorted(counts.items())),
        "records": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("drawings", "record_count", "relation_counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
