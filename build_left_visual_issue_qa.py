#!/usr/bin/env python3
"""Locate left-side visual QA differences without feeding PDF data to rendering.

This is an inspection aid only. It ranks raster differences between an already
rendered SHA-only SVG PNG and its paired PDF PNG, then emits side-by-side
crops for a human to identify a discrepancy. It never writes SVG/SHA data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", size)
    except OSError:
        return ImageFont.load_default()


def crop_panel(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    crop = image.crop(box).convert("RGB")
    panel = Image.new("RGB", (420, 280), "white")
    scale = min(panel.width / crop.width, panel.height / crop.height)
    resized = crop.resize(
        (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
        Image.Resampling.LANCZOS,
    )
    panel.paste(resized, ((panel.width - resized.width) // 2, (panel.height - resized.height) // 2))
    return panel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_manifest", type=Path)
    parser.add_argument("selection", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("output/left_text_ten_sample/visual_issue_qa"))
    parser.add_argument("--per-drawing", type=int, default=2)
    parser.add_argument(
        "--sha-png-root",
        type=Path,
        help="Optional SHA-only PNG root arranged as <drawing>/<sheet>.png.",
    )
    args = parser.parse_args()

    corpus = json.loads(args.corpus_manifest.read_text(encoding="utf-8"))
    selected = {item["drawing"] for item in json.loads(args.selection.read_text(encoding="utf-8"))["selected_drawings"]}
    pages = [
        (drawing["drawing"], page)
        for drawing in corpus["drawings"]
        if drawing["drawing"] in selected
        for page in drawing["pages"]
    ]
    candidates = []
    for drawing, page in pages:
        sha_path = (
            args.sha_png_root / drawing / f"{page['sheet_stream']}.png"
            if args.sha_png_root
            else Path(page["sha_svg"]).with_suffix(".png")
        )
        pdf_path = Path(page["pdf_png"])
        if not sha_path.is_file() or not pdf_path.is_file():
            continue
        with Image.open(sha_path) as source:
            sha = source.convert("L")
        with Image.open(pdf_path) as source:
            pdf = source.convert("L").resize(sha.size, Image.Resampling.LANCZOS)
        width = round(sha.width * 0.55)
        # Ignore the outer border and use a coarse grid so font antialiasing
        # does not turn every glyph edge into a separate visual issue.
        tile = max(80, round(min(sha.width, sha.height) / 10))
        delta = np.abs(np.asarray(sha, dtype=np.int16) - np.asarray(pdf, dtype=np.int16))
        for top in range(tile, sha.height - tile, tile):
            for left in range(tile, width - tile, tile):
                score = float(delta[top : top + tile, left : left + tile].mean())
                if score >= 8.0:
                    candidates.append((score, drawing, page, (left, top, left + tile, top + tile)))

    chosen = []
    by_drawing: dict[str, int] = {}
    # Scores can tie across uniform page areas.  Sort only on stable scalar
    # fields so Python never falls through to comparing the drawing/page dicts.
    for candidate in sorted(
        candidates,
        key=lambda item: (-item[0], item[1], str(item[2]["sheet_stream"]), item[3]),
    ):
        _, drawing, _, box = candidate
        if by_drawing.get(drawing, 0) >= args.per_drawing:
            continue
        # Suppress heavily overlapping tiles from the same drawing.
        if any(
            old_drawing == drawing
            and max(box[0], old_box[0]) < min(box[2], old_box[2])
            and max(box[1], old_box[1]) < min(box[3], old_box[3])
            for _, old_drawing, _, old_box in chosen
        ):
            continue
        chosen.append(candidate)
        by_drawing[drawing] = by_drawing.get(drawing, 0) + 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    font = load_font(15)
    boards = 0
    for start in range(0, len(chosen), 4):
        batch = chosen[start : start + 4]
        board = Image.new("RGB", (840, len(batch) * 330), "#e8e8e8")
        draw = ImageDraw.Draw(board)
        for row, (score, drawing, page, box) in enumerate(batch):
            y = row * 330
            sha_path = (
                args.sha_png_root / drawing / f"{page['sheet_stream']}.png"
                if args.sha_png_root
                else Path(page["sha_svg"]).with_suffix(".png")
            )
            with Image.open(sha_path) as source:
                sha = source.convert("RGB")
            with Image.open(Path(page["pdf_png"])) as source:
                pdf = source.convert("RGB").resize(sha.size, Image.Resampling.LANCZOS)
            board.paste(crop_panel(sha, box), (0, y))
            board.paste(crop_panel(pdf, box), (420, y))
            draw.rectangle((0, y + 280, 840, y + 330), fill="white")
            draw.text((8, y + 8), "SHA-only render", fill="#b00020", font=font)
            draw.text((428, y + 8), "PDF visual reference", fill="#0050a4", font=font)
            draw.text(
                (8, y + 287),
                f"score={score:.1f} | {drawing} | {page['sheet_stream']} | raster QA only",
                fill="black",
                font=font,
            )
        boards += 1
        board.save(args.out_dir / f"left-issue-{boards:03d}.png", optimize=True)
    report = {
        "scope": "Left-side raster QA only; PDF has no renderer input.",
        "drawings": len(selected),
        "candidate_tiles": len(candidates),
        "chosen_tiles": len(chosen),
        "boards": boards,
    }
    (args.out_dir / "index.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
