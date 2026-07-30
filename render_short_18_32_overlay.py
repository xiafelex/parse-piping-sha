#!/usr/bin/env python3
"""Overlay raw short ``18/32`` line primitives on a SHA-only ISO render.

The default renderer excludes lines shorter than four page units to avoid
binary false positives.  This diagnostic keeps only structurally valid raw
18/32 records below that threshold, so their contribution can be reviewed
without changing the production renderer.
"""

from __future__ import annotations

import argparse
import math
import re
import struct
from pathlib import Path

from analyze_iso_split import read_sha_streams
from sha_to_svg_prototype import PAGE_HEIGHT, SHEET_UNIT, render


def short_segments(data: bytes) -> list[tuple[int, int, float, float, float, float]]:
    """Return bounded, normalized raw 18/32 segments shorter than four units."""

    result: list[tuple[int, int, float, float, float, float]] = []
    signature = b"\x18\x00\x32\x00\x00\x00"
    for match in re.finditer(re.escape(signature), data):
        start = match.start()
        if start + 56 > len(data):
            continue
        child_ref = struct.unpack_from("<I", data, start + 6)[0]
        object_ref = struct.unpack_from("<I", data, start + 10)[0]
        x1, y1, x2, y2 = struct.unpack_from("<4d", data, start + 24)
        if not all(-0.03 <= value <= 1.05 for value in (x1, y1, x2, y2)):
            continue
        length = math.hypot(x2 - x1, y2 - y1) * SHEET_UNIT
        if not 0 < length < 4:
            continue
        result.append((child_ref, object_ref, x1, y1, x2, y2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    streams = read_sha_streams(args.sha)
    if args.sheet not in streams:
        raise ValueError(f"{args.sheet} is absent from {args.sha.name}")
    base_path = args.output.with_suffix(".base.svg")
    render(
        args.sha,
        base_path,
        wanted_page=1,
        debug_boxes=False,
        manifest_path=None,
        component_layer=None,
        sheet_stream=args.sheet,
    )
    base_svg = base_path.read_text(encoding="utf-8")
    base_path.unlink()
    segments = short_segments(streams[args.sheet])
    layer = [
        '<g id="sha-short-18-32-diagnostic" fill="none" stroke="#c0392b" stroke-width="10" stroke-linecap="round">',
        '<desc>SHA-only diagnostic: structurally valid raw 18/32 segments shorter than four page units. '
        'Not enabled in the production renderer.</desc>',
    ]
    for child_ref, object_ref, x1, y1, x2, y2 in segments:
        layer.append(
            f'<line x1="{x1 * SHEET_UNIT:.3f}" y1="{PAGE_HEIGHT - y1 * SHEET_UNIT:.3f}" '
            f'x2="{x2 * SHEET_UNIT:.3f}" y2="{PAGE_HEIGHT - y2 * SHEET_UNIT:.3f}" '
            f'data-child="0x{child_ref:08X}" data-object="0x{object_ref:08X}"/>'
        )
    layer.extend(
        [
            '<g transform="translate(260 230)">',
            '<rect x="-45" y="-120" width="4420" height="190" fill="white" fill-opacity="0.9" stroke="#34495e"/>',
            '<text x="0" y="-25" fill="#17202a" stroke="none" font-family="monospace" font-size="76">'
            'Raw short 18/32 lines: diagnostic only</text>',
            f'<text x="0" y="92" fill="#17202a" stroke="none" font-family="monospace" font-size="60">'
            f'{len(segments)} source records below the production four-unit filter</text>',
            '</g>',
            '</g>',
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(base_svg.rsplit("</svg>", 1)[0] + "\n".join(layer) + "\n</svg>\n", encoding="utf-8")
    print(f"Short 18/32 diagnostic SVG: {args.output}")
    print(f"Short source segments: {len(segments)}")


if __name__ == "__main__":
    main()
