#!/usr/bin/env python3
"""Render a corpus of SHA ISO sheets and prepare PDF-only visual QA evidence.

Every reconstructed SVG is generated exclusively from its paired SHA file.
PDF files are rasterized only after rendering so reviewers can compare the
result visually.  This tool never reads PDF text, vectors, or coordinates and
never feeds PDF-derived values into the SHA renderer.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from analyze_iso_split import read_sha_streams
from analyze_sha_pages import logical_page, text_objects
from sha_to_svg_prototype import render


ROOT = Path(__file__).resolve().parent
SHEET_RE = re.compile(r"Sheet(\d+)$")


def pdf_page_count(path: Path) -> int:
    """Return the PDF page count without extracting any page content."""

    result = subprocess.run(
        ["pdfinfo", str(path)], check=True, text=True, capture_output=True
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.partition(":")[2].strip())
    raise ValueError(f"pdfinfo did not report a page count for {path}")


def page_sheets(sha_path: Path) -> tuple[list[dict[str, Any]], str]:
    """Return physical ISO sheets in the best SHA-derived display order.

    The title block normally supplies a unique logical page number.  Some
    legacy files repeat or omit it, so those pages retain a physical-Sheet
    fallback order and are explicitly labelled as uncertain rather than being
    silently paired to a PDF page.
    """

    streams = read_sha_streams(sha_path)
    sheets: list[dict[str, Any]] = []
    for name, data in streams.items():
        match = SHEET_RE.fullmatch(name)
        if not match or name == "Sheet221" or len(data) <= 1024:
            continue
        logical = logical_page(text_objects(data))
        sheets.append(
            {
                "sheet_stream": name,
                "sheet_id": int(match.group(1)),
                "logical_page": logical[0] if logical else None,
                "logical_total": logical[1] if logical else None,
            }
        )

    logical_pages = [sheet["logical_page"] for sheet in sheets]
    if (
        sheets
        and all(page is not None for page in logical_pages)
        and len(set(logical_pages)) == len(sheets)
        and sorted(logical_pages) == list(range(1, len(sheets) + 1))
    ):
        sheets.sort(key=lambda sheet: int(sheet["logical_page"]))
        return sheets, "unique SHA title-block logical page numbers"

    # Sheet6 is the observed base page stream.  Remaining IDs are not page
    # numbers, but numeric ordering is deterministic for visual QA.
    sheets.sort(key=lambda sheet: (sheet["sheet_stream"] != "Sheet6", int(sheet["sheet_id"])))
    return sheets, "physical Sheet fallback; PDF page pairing requires visual confirmation"


def matching_pdf(sha_path: Path) -> Path | None:
    """Find only a same-stem sibling PDF, regardless of filename case."""

    expected = f"{sha_path.stem}.pdf".lower()
    for candidate in sha_path.parent.iterdir():
        if candidate.is_file() and candidate.name.lower() == expected:
            return candidate
    return None


def render_pdf_page(pdf_path: Path, page: int, output: Path, width: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "pdftoppm",
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-png",
            "-scale-to-x",
            str(width),
            "-scale-to-y",
            "-1",
            str(pdf_path),
            str(output.with_suffix("")),
        ],
        check=True,
    )


def render_sha_sheet(sha_path: Path, sheet: dict[str, Any], output: Path) -> Path:
    svg_path = output / "sha-svg" / f"{sheet['sheet_stream']}.svg"
    trace_path = output / "trace" / f"{sheet['sheet_stream']}.json"
    render(
        sha_path,
        svg_path,
        int(sheet["logical_page"] or 1),
        False,
        trace_path,
        None,
        str(sheet["sheet_stream"]),
    )
    return svg_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folders", nargs="+", type=Path, help="Folders containing sibling SHA and PDF files.")
    parser.add_argument("--out-dir", type=Path, default=Path("output/corpus_audit"))
    parser.add_argument("--limit", type=int, help="Render at most this many SHA files, for a smoke test.")
    parser.add_argument("--pdf-width", type=int, default=1200, help="PDF QA thumbnail width in pixels.")
    parser.add_argument("--skip-pdf-raster", action="store_true", help="Render SHA only; do not create PDF QA PNGs.")
    parser.add_argument(
        "--reuse-sha-render",
        action="store_true",
        help="Reuse existing SHA SVG and trace files while rebuilding page mapping/PDF QA artifacts.",
    )
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    sha_paths: list[Path] = []
    for folder in args.folders:
        resolved = folder.resolve()
        if not resolved.is_dir():
            parser.error(f"Folder not found: {resolved}")
        sha_paths.extend(sorted(resolved.glob("*.sha")))
    if args.limit is not None:
        sha_paths = sha_paths[: args.limit]

    for index, sha_path in enumerate(sha_paths, start=1):
        pdf_path = matching_pdf(sha_path)
        drawing_dir = out_dir / sha_path.stem
        try:
            sheets, ordering = page_sheets(sha_path)
            if not sheets:
                raise ValueError("no populated physical Sheet streams")
            pdf_pages = pdf_page_count(pdf_path) if pdf_path else None
            page_records: list[dict[str, Any]] = []
            for page_index, sheet in enumerate(sheets, start=1):
                svg_path = drawing_dir / "sha-svg" / f"{sheet['sheet_stream']}.svg"
                trace_path = drawing_dir / "trace" / f"{sheet['sheet_stream']}.json"
                if not (args.reuse_sha_render and svg_path.is_file() and trace_path.is_file()):
                    svg_path = render_sha_sheet(sha_path, sheet, drawing_dir)
                entry = {
                    **sheet,
                    "qa_page_index": page_index,
                    "sha_svg": str(svg_path),
                    "trace": str(trace_path),
                    "pdf_page": page_index if pdf_pages and page_index <= pdf_pages else None,
                    "pdf_png": None,
                }
                if pdf_path and page_index <= pdf_pages:
                    png_path = drawing_dir / "pdf-png" / f"page-{page_index}.png"
                    if not args.skip_pdf_raster:
                        render_pdf_page(pdf_path, page_index, png_path, args.pdf_width)
                    if png_path.is_file():
                        entry["pdf_png"] = str(png_path)
                page_records.append(entry)
            records.append(
                {
                    "drawing": sha_path.stem,
                    "sha": str(sha_path),
                    "pdf": str(pdf_path) if pdf_path else None,
                    "physical_sheet_count": len(sheets),
                    "pdf_page_count": pdf_pages,
                    "page_order_basis": ordering,
                    "page_count_matches_pdf": pdf_pages == len(sheets) if pdf_pages is not None else None,
                    "pages": page_records,
                }
            )
            print(f"[{index}/{len(sha_paths)}] {sha_path.name}: {len(sheets)} SHA sheets")
        except Exception as exc:  # Keep the corpus run auditable after one bad file.
            failures.append({"sha": str(sha_path), "error": str(exc)})
            print(f"[{index}/{len(sha_paths)}] ERROR {sha_path.name}: {exc}", file=sys.stderr)

    manifest = {
        "scope": "SHA-only reconstruction; PDF rasterization is visual QA only",
        "renderer": str(ROOT / "sha_to_svg_prototype.py"),
        "drawings": records,
        "failures": failures,
        "summary": {
            "sha_files_requested": len(sha_paths),
            "drawings_rendered": len(records),
            "physical_sheets_rendered": sum(item["physical_sheet_count"] for item in records),
            "pdf_page_count_matches": sum(item["page_count_matches_pdf"] is True for item in records),
            "pdf_page_count_mismatches": sum(item["page_count_matches_pdf"] is False for item in records),
        },
    }
    (out_dir / "audit_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Manifest: {out_dir / 'audit_manifest.json'}")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
