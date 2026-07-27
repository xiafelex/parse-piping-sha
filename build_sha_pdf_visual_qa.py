#!/usr/bin/env python3
"""Create labelled SHA-versus-PDF visual QA boards without changing SVG data.

The left side is a PNG rasterization of a SHA-only SVG. The right side is the
matching PDF raster produced by ``audit_sha_pdf_corpus.py``. The PDF is never
parsed or used to generate/reposition any SHA geometry.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", size)
    except OSError:
        return ImageFont.load_default()


def page_image(path: Path, width: int, height: int) -> Image.Image:
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
        panel = Image.new("RGB", (width, height), "white")
        panel.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
        return panel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quality_summary", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("output/visual_qa"))
    parser.add_argument("--top", type=int, default=80, help="Maximum ranked pages to board.")
    parser.add_argument("--per-board", type=int, default=4)
    parser.add_argument(
        "--visual-ranking",
        type=Path,
        help="Optional visual_difference_ranking.json to choose board order while retaining SHA trace flags.",
    )
    args = parser.parse_args()

    report = json.loads(args.quality_summary.read_text(encoding="utf-8"))
    pages = [page for page in report["pages_ranked_for_visual_qa"] if page.get("pdf_png")]
    if args.visual_ranking:
        visual = json.loads(args.visual_ranking.read_text(encoding="utf-8"))
        by_key = {(page["drawing"], page["sheet_stream"]): page for page in pages}
        ordered: list[dict[str, object]] = []
        for item in visual["pages_ranked"]:
            page = by_key.get((item["drawing"], item["sheet_stream"]))
            if page is not None:
                ordered.append(page)
        pages = ordered
    pages = pages[: args.top]
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    panel_width, panel_height, caption = 900, 635, 52
    board_width = panel_width * 2
    generated = 0

    for start in range(0, len(pages), args.per_board):
        group = pages[start : start + args.per_board]
        board = Image.new("RGB", (board_width, (panel_height + caption) * len(group)), "#e7e7e7")
        draw = ImageDraw.Draw(board)
        for row, page in enumerate(group):
            sha_png = Path(page["sha_svg"]).with_suffix(".png")
            pdf_png = Path(str(page["pdf_png"]))
            if not sha_png.is_file() or not pdf_png.is_file():
                continue
            y = row * (panel_height + caption)
            board.paste(page_image(sha_png, panel_width, panel_height), (0, y))
            board.paste(page_image(pdf_png, panel_width, panel_height), (panel_width, y))
            label = (
                f"{page['drawing']} | {page['sheet_stream']} | QA page {page['qa_page_index']} | "
                f"flags: {', '.join(page['flags']) or 'none'}"
            )
            draw.rectangle((0, y + panel_height, board_width, y + panel_height + caption), fill="white")
            draw.text((10, y + panel_height + 14), label, fill="black", font=font(20))
            draw.text((10, y + 10), "SHA-only render", fill="#c00000", font=font(22))
            draw.text((panel_width + 10, y + 10), "PDF visual reference", fill="#0050a4", font=font(22))
        output = out_dir / f"qa-board-{generated + 1:03d}.png"
        board.save(output, optimize=True)
        generated += 1

    index = {
        "scope": "Left: SHA-only reconstruction. Right: PDF visual reference only.",
        "pages_boarded": len(pages),
        "boards": generated,
        "ranking_source": str(args.quality_summary),
    }
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(index, ensure_ascii=False))


if __name__ == "__main__":
    main()
