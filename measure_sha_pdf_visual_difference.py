#!/usr/bin/env python3
"""Rank SHA/PDF visual QA pairs by coarse image-structure difference.

This is an acceptance-ranking aid only. It reads already-created PNG images,
does not inspect PDF source data, and never modifies SVG or SHA output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def ink_grid(path: Path, columns: int = 48, rows: int = 34) -> tuple[np.ndarray, float]:
    with Image.open(path) as source:
        image = source.convert("L").resize((columns, rows), Image.Resampling.LANCZOS)
    pixels = np.asarray(image, dtype=np.float32)
    # Dark ink has positive weight while an empty white page remains zero.
    ink = np.clip((245.0 - pixels) / 245.0, 0.0, 1.0)
    return ink, float(ink.mean())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quality_summary", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    summary = json.loads(args.quality_summary.read_text(encoding="utf-8"))
    pages: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for page in summary["pages_ranked_for_visual_qa"]:
        sha_png = Path(page["sha_svg"]).with_suffix(".png")
        pdf_png = Path(str(page.get("pdf_png") or ""))
        if not sha_png.is_file() or not pdf_png.is_file():
            skipped.append({"drawing": page["drawing"], "sheet_stream": page["sheet_stream"], "reason": "missing PNG"})
            continue
        sha_grid, sha_ink = ink_grid(sha_png)
        pdf_grid, pdf_ink = ink_grid(pdf_png)
        structure_difference = float(np.abs(sha_grid - pdf_grid).mean())
        ink_ratio = sha_ink / pdf_ink if pdf_ink else None
        pages.append({
            "drawing": page["drawing"],
            "sheet_stream": page["sheet_stream"],
            "qa_page_index": page["qa_page_index"],
            "existing_flags": page["flags"],
            "coarse_structure_difference": structure_difference,
            "sha_ink_density": sha_ink,
            "pdf_ink_density": pdf_ink,
            "sha_to_pdf_ink_ratio": ink_ratio,
        })

    pages.sort(key=lambda page: -float(page["coarse_structure_difference"]))
    report = {
        "scope": "Coarse PNG acceptance ranking only; not a geometry/text source for the renderer.",
        "pages_measured": len(pages),
        "pages_skipped": skipped,
        "pages_ranked": pages,
    }
    output = args.output or args.quality_summary.with_name("visual_difference_ranking.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {output}")
    for page in pages[:20]:
        print(
            f"{page['coarse_structure_difference']:.5f} "
            f"ink={page['sha_to_pdf_ink_ratio']:.3f} "
            f"{page['drawing']} {page['sheet_stream']}"
        )


if __name__ == "__main__":
    main()
