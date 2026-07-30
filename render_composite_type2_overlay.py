#!/usr/bin/env python3
"""Render SHA-only diagnostic bounds for composite child ``type=2``.

This is explicitly an investigation overlay.  It does not claim that a type-2
bounding range is a visible rectangle, line, glyph, or component symbol.  The
base ISO and every overlay position come from the same SHA file.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from analyze_composite_primitives import parse_composites
from analyze_iso_split import read_sha_streams
from sha_to_svg_prototype import PAGE_HEIGHT, render


def overlay_svg(sheet: bytes) -> str:
    """Create bounded type-2 diagnostic rectangles in Shape2D page space."""

    elements = [
        '<g id="sha-composite-type2-diagnostic" fill="none" stroke="#d35400" stroke-width="8">',
        '<desc>SHA-only diagnostic: each rectangle is a raw type-2 composite child range. '
        'It is not a decoded visible primitive.</desc>',
    ]
    count = 0
    for parent in parse_composites(sheet):
        for child in parent["children"]:
            if child["type"] != 2:
                continue
            left, bottom, right, top = (child[key] / 2 for key in ("left", "bottom", "right", "top"))
            if not (left < right and bottom < top):
                continue
            count += 1
            elements.append(
                f'<rect x="{left:.3f}" y="{PAGE_HEIGHT - top:.3f}" '
                f'width="{right - left:.3f}" height="{top - bottom:.3f}" '
                f'data-parent="0x{int(parent["parent_ref"]):08X}" '
                f'data-child="0x{int(child["ref"]):08X}" data-primitive-type="2"/>'
            )
    elements.extend(
        [
            '<g transform="translate(260 230)">',
            '<rect x="-45" y="-120" width="3920" height="190" fill="white" fill-opacity="0.9" stroke="#34495e"/>',
            '<text x="0" y="-25" fill="#17202a" stroke="none" font-family="monospace" font-size="76">'
            'Type-2 raw bounds: diagnostic only, not decoded geometry</text>',
            f'<text x="0" y="92" fill="#17202a" stroke="none" font-family="monospace" font-size="60">'
            f'{count} SHA composite-child ranges from this physical Sheet</text>',
            '</g>',
            '</g>',
        ]
    )
    return "\n".join(elements)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path)
    parser.add_argument("--sheet", required=True, help="Physical Sheet stream, for example Sheet32912.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--png", action="store_true")
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
    if "</svg>" not in base_svg:
        raise ValueError("SHA renderer did not produce a complete SVG")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        base_svg.rsplit("</svg>", 1)[0] + overlay_svg(streams[args.sheet]) + "\n</svg>\n",
        encoding="utf-8",
    )
    if args.png:
        node = shutil.which("node")
        if node is None:
            print("PNG skipped: Node.js was not found.")
            return
        script = r'''
const { chromium } = require("playwright");
const path = require("path");
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 2600, height: 1900 }, deviceScaleFactor: 1 });
  await page.goto("file://" + path.resolve(process.argv[1]));
  await page.locator("svg").evaluate((element) => {
    element.setAttribute("width", "2600");
    element.setAttribute("height", "1835");
    element.style.display = "block";
  });
  await page.locator("svg").screenshot({ path: process.argv[2] });
  await browser.close();
})();
'''
        try:
            subprocess.run([node, "-e", script, str(args.output), str(args.output) + ".png"], check=True)
        except subprocess.CalledProcessError:
            print("PNG skipped: Playwright is unavailable; the diagnostic SVG was still written.")
    print(f"Type-2 diagnostic SVG: {args.output}")


if __name__ == "__main__":
    main()
