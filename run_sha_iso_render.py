#!/usr/bin/env python3
"""Run the SHA-only ISO renderer and write handoff-friendly artifacts.

Required sibling files:
  - sha_to_svg_prototype.py
  - analyze_iso_split.py
  - analyze_sha_pages.py

The optional component layer must itself be a documented SHA-derived SVG from
the same drawing. PDF files are deliberately not accepted by this runner.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path, help="Source Shape2D/PDMS SHA file.")
    parser.add_argument("--page", type=int, default=1, help="Logical ISO page number (default: 1).")
    parser.add_argument("--out-dir", type=Path, default=Path("output/sha_svg"), help="Artifact directory.")
    parser.add_argument(
        "--component-layer",
        type=Path,
        help="Optional SHA-derived UCI component SVG from the same SHA only.",
    )
    parser.add_argument("--png", action="store_true", help="Also render a PNG when Node Playwright is available.")
    args = parser.parse_args()

    if not args.sha.is_file():
        parser.error(f"SHA file not found: {args.sha}")
    if args.component_layer is not None and not args.component_layer.is_file():
        parser.error(f"Component layer not found: {args.component_layer}")

    stem = args.sha.stem
    output_dir = args.out_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    svg = output_dir / f"{stem}-page-{args.page}-sha.svg"
    manifest = output_dir / f"{stem}-page-{args.page}-sha.trace.json"
    command = [
        sys.executable,
        str(ROOT / "sha_to_svg_prototype.py"),
        str(args.sha.resolve()),
        "--page",
        str(args.page),
        "--output",
        str(svg),
        "--manifest",
        str(manifest),
    ]
    if args.component_layer is not None:
        command.extend(["--component-layer", str(args.component_layer.resolve())])
    subprocess.run(command, check=True)

    if args.png:
        node = shutil.which("node")
        if node is None:
            print("PNG skipped: Node.js was not found. SVG and trace JSON were created.")
        else:
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
                subprocess.run([node, "-e", script, str(svg), str(svg) + ".png"], check=True, cwd=ROOT)
            except subprocess.CalledProcessError:
                print("PNG skipped: install Playwright with `npm install playwright` and retry --png.")

    print(f"SVG: {svg}")
    print(f"Trace manifest: {manifest}")


if __name__ == "__main__":
    main()
