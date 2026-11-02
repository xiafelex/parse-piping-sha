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
import re
import shutil
import subprocess
import sys
from pathlib import Path

from analyze_iso_split import read_sha_streams
from analyze_sha_pages import logical_page, text_objects


ROOT = Path(__file__).resolve().parent


def discovered_pages(sha_path: Path) -> list[int]:
    streams = read_sha_streams(sha_path)
    pages = {
        int(page_info[0])
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name)
        and len(data) > 1024
        and (page_info := logical_page(text_objects(data)))
    }
    return sorted(pages)


def available_pages(sha_path: Path) -> list[int]:
    """Return logical ISO pages for the local workspace compatibility API.

    Keep this public name for ``app_server.py`` and any downstream scripts that
    predate the clearer ``discovered_pages`` name.
    """
    return discovered_pages(sha_path)


def discovered_sheets(sha_path: Path) -> list[str]:
    streams = read_sha_streams(sha_path)
    return sorted(
        name
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name) and len(data) > 1024 and name != "Sheet221"
    )


def render_one_page(sha_path: Path, page_number: int, out_dir: Path, component_layer: Path | None, png: bool) -> None:
    stem = sha_path.stem
    svg = out_dir / f"{stem}-page-{page_number}-sha.svg"
    manifest = out_dir / f"{stem}-page-{page_number}-sha.trace.json"
    command = [
        sys.executable,
        str(ROOT / "sha_to_svg_prototype.py"),
        str(sha_path.resolve()),
        "--page",
        str(page_number),
        "--output",
        str(svg),
        "--manifest",
        str(manifest),
    ]
    if component_layer is not None:
        command.extend(["--component-layer", str(component_layer.resolve())])
    subprocess.run(command, check=True)

    if png:
        node = shutil.which("node")
        if node is None:
            print(f"PNG skipped for page {page_number}: Node.js was not found.")
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
                print(f"PNG skipped for page {page_number}: install Playwright with `npm install playwright` and retry --png.")

    print(f"SVG: {svg}")
    print(f"Trace manifest: {manifest}")


def render_one_sheet(sha_path: Path, sheet_name: str, out_dir: Path, component_layer: Path | None, png: bool) -> None:
    stem = sha_path.stem
    svg = out_dir / f"{stem}-{sheet_name}-sha.svg"
    manifest = out_dir / f"{stem}-{sheet_name}-sha.trace.json"
    command = [
        sys.executable, str(ROOT / "sha_to_svg_prototype.py"), str(sha_path.resolve()),
        "--page", "1", "--sheet-stream", sheet_name, "--output", str(svg), "--manifest", str(manifest),
    ]
    if component_layer is not None:
        command.extend(["--component-layer", str(component_layer.resolve())])
    subprocess.run(command, check=True)
    if png:
        node = shutil.which("node")
        if node is not None:
            script = r'''const { chromium } = require("playwright"); const path = require("path"); (async () => { const b = await chromium.launch({headless:true}); const p = await b.newPage({viewport:{width:2600,height:1900},deviceScaleFactor:1}); await p.goto("file://"+path.resolve(process.argv[1])); await p.locator("svg").evaluate(e=>{e.setAttribute("width","2600");e.setAttribute("height","1835");e.style.display="block"}); await p.locator("svg").screenshot({path:process.argv[2]}); await b.close(); })();'''
            subprocess.run([node, "-e", script, str(svg), str(svg) + ".png"], check=True, cwd=ROOT)
    print(f"SVG: {svg}")
    print(f"Trace manifest: {manifest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path, help="Source Shape2D/PDMS SHA file.")
    parser.add_argument("--page", type=int, default=1, help="Logical ISO page number (default: 1).")
    parser.add_argument("--all-pages", action="store_true", help="Render every populated logical ISO page in the SHA.")
    parser.add_argument("--all-sheets", action="store_true", help="Render every populated physical SHA Sheet stream, including duplicate logical page numbers.")
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

    output_dir = args.out_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.all_sheets:
        sheets = discovered_sheets(args.sha)
        if not sheets:
            parser.error(f"No populated physical Sheet streams were found in {args.sha}")
        for sheet_name in sheets:
            render_one_sheet(args.sha, sheet_name, output_dir, args.component_layer, args.png)
    elif args.all_pages:
        pages = discovered_pages(args.sha)
        if not pages:
            parser.error(f"No populated logical ISO pages were found in {args.sha}")
        for page_number in pages:
            render_one_page(args.sha, page_number, output_dir, args.component_layer, args.png)
    else:
        render_one_page(args.sha, args.page, output_dir, args.component_layer, args.png)


if __name__ == "__main__":
    main()
