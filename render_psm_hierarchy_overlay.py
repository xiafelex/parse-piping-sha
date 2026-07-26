#!/usr/bin/env python3
"""Render validated PSM hierarchy-node envelope candidates over a SHA-only ISO.

This is a diagnostic renderer, not a semantic decoder.  It overlays node ids
from the fully consumed ``PSMspacemap/0x00008000`` tseg table where the same id
also has a plausible ``PSMcluster0`` envelope.  The underlying ISO SVG and all
overlay coordinates are generated from the supplied SHA; PDF is never read.
"""

from __future__ import annotations

import argparse
import html
import shutil
import subprocess
from pathlib import Path

from analyze_iso_split import read_sha_streams
from analyze_psm_hierarchy import parse_psm_bbox_record_runs, parse_tseg_nodes
from sha_to_svg_prototype import PAGE_HEIGHT, PAGE_WIDTH, render


COLORS = {2: "#d35400", 3: "#007f8b"}


def parse_types(value: str) -> set[int]:
    """Parse a comma-separated list while making the diagnostic scope explicit."""

    try:
        result = {int(item.strip(), 0) for item in value.split(",") if item.strip()}
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--types must be comma-separated integers, for example 2,3") from exc
    if not result:
        raise argparse.ArgumentTypeError("--types cannot be empty")
    return result


def candidate_nodes(sha_path: Path, types: set[int], min_node_id: int) -> list[dict[str, object]]:
    """Return visually plausible node/envelope matches without assigning semantics."""

    streams = read_sha_streams(sha_path)
    hierarchy = parse_tseg_nodes(streams["PSMspacemap/0x00008000"])
    record_index = parse_psm_bbox_record_runs(streams.get("PSMcluster0", b""))
    envelopes = {
        int(record["graphic_ref"]): tuple(int(value) for value in record["bbox"])
        for record in record_index["records"]
    }
    candidates: list[dict[str, object]] = []
    for node in hierarchy["nodes"]:
        node_id = int(node["id"])
        node_type = int(node["type"])
        if node_id < min_node_id or node_type not in types:
            continue
        bbox = envelopes.get(node_id)
        if bbox is None:
            continue
        left, bottom, right, top = bbox
        if not (0 <= left < right <= PAGE_WIDTH and 0 <= bottom < top <= PAGE_HEIGHT):
            continue
        candidates.append({"id": node_id, "type": node_type, "bbox": bbox})
    return candidates


def overlay_svg(candidates: list[dict[str, object]], types: set[int], min_node_id: int) -> str:
    """Create an SVG group in the same page coordinate system as the base SVG."""

    labels: list[str] = [
        '<g id="sha-psm-hierarchy-candidates" fill="none" stroke-linejoin="round">',
        '<desc>Candidate PSMcluster0 envelopes linked by identical numeric ids to validated '
        'PSMspacemap/0x00008000 nodes. These are not semantic primitive classifications.</desc>',
    ]
    for candidate in candidates:
        node_id = int(candidate["id"])
        node_type = int(candidate["type"])
        left, bottom, right, top = candidate["bbox"]
        width, height = right - left, top - bottom
        color = COLORS.get(node_type, "#7f8c8d")
        labels.append(
            f'<rect x="{left}" y="{PAGE_HEIGHT - top}" width="{width}" height="{height}" '
            f'stroke="{color}" stroke-width="12" fill="{color}" fill-opacity="0.06" '
            f'data-psm-node="0x{node_id:04X}" data-psm-type="{node_type}" '
            'data-mapping="candidate-id-envelope-match"/>'
        )
        # Avoid unreadable label collisions on extremely small glyph envelopes.
        # Thin envelopes are often leaders, strokes, or individual glyph runs.
        # Their node label would conceal the very source geometry under review.
        if width >= 100 and height >= 100:
            label_size = max(42, min(96, max(width, height) * 0.22))
            labels.append(
                f'<text x="{left + 14}" y="{PAGE_HEIGHT - top + label_size}" fill="{color}" '
                f'stroke="white" stroke-width="9" paint-order="stroke" '
                f'font-family="monospace" font-size="{label_size:.1f}" '
                f'data-psm-node-label="0x{node_id:04X}">0x{node_id:04X} / T{node_type}</text>'
            )
    type_summary = ", ".join(f"T{node_type}={COLORS.get(node_type, '#7f8c8d')}" for node_type in sorted(types))
    labels.extend(
        [
            '<g id="sha-psm-hierarchy-legend" transform="translate(260 225)">',
            '<rect x="-42" y="-105" width="4080" height="195" fill="white" fill-opacity="0.88" stroke="#34495e" stroke-width="8"/>',
            '<text x="0" y="-20" fill="#17202a" font-family="monospace" font-size="76">'
            'PSM candidate envelopes: validated tseg node id = PSMcluster0 id</text>',
            f'<text x="0" y="95" fill="#17202a" font-family="monospace" font-size="60">'
            f'types: {html.escape(type_summary)}; node id >= 0x{min_node_id:04X}; semantic role unresolved</text>',
            '</g>',
            '</g>',
        ]
    )
    return "\n".join(labels)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--types", type=parse_types, default={2}, help="PSM node types to overlay (default: 2)")
    parser.add_argument("--min-node-id", type=lambda value: int(value, 0), default=0x500)
    parser.add_argument("--png", action="store_true", help="Also create a scaled PNG preview when Node Playwright is available.")
    args = parser.parse_args()

    candidates = candidate_nodes(args.sha, args.types, args.min_node_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    base_path = args.output.with_suffix(".base.svg")
    render(args.sha, base_path, args.page, False, None, None)
    base_svg = base_path.read_text(encoding="utf-8")
    base_path.unlink()
    if "</svg>" not in base_svg:
        raise ValueError("SHA renderer did not produce a complete SVG")
    args.output.write_text(
        base_svg.rsplit("</svg>", 1)[0] + overlay_svg(candidates, args.types, args.min_node_id) + "\n</svg>\n",
        encoding="utf-8",
    )
    if args.png:
        node = shutil.which("node")
        if node is None:
            print("PNG skipped: Node.js was not found.")
        else:
            # Render the SVG element itself at a reviewable page size. Visiting
            # the SVG document directly screenshots only its top-left corner.
            script = r'''
const { chromium } = require("playwright");
const path = require("path");
(async () => {
  const svg = process.argv[1];
  const png = process.argv[2];
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 2600, height: 1900 }, deviceScaleFactor: 1 });
  await page.goto("file://" + path.resolve(svg));
  await page.locator("svg").evaluate((element) => {
    element.setAttribute("width", "2600");
    element.setAttribute("height", "1835");
    element.style.display = "block";
  });
  await page.locator("svg").screenshot({ path: png });
  await browser.close();
})();
'''
            try:
                subprocess.run([node, "-e", script, str(args.output), str(args.output) + ".png"], check=True)
            except subprocess.CalledProcessError:
                print("PNG skipped: install Playwright with `npm install playwright` and retry --png.")
    print(f"PSM diagnostic SVG: {args.output}")
    print(f"Candidate envelopes: {len(candidates)} (types {sorted(args.types)}, node id >= 0x{args.min_node_id:04X})")


if __name__ == "__main__":
    main()
