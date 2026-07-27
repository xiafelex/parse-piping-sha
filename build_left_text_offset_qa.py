#!/usr/bin/env python3
"""Build zoomed SHA/PDF QA boards for left-side ISO annotation placement.

SVG text placement always comes from SHA trace data. PDF PNGs are cropped only
as visual references, never parsed and never fed back into the renderer.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


PAGE_HEIGHT = 11880


def category(text: str) -> str:
    if re.fullmatch(r"(?:\d+(?:\.\d+)?|\d+X\d+NPD|\d+NPD)", text):
        return "尺寸/管径"
    if re.fullmatch(r"(?:[FGBST]\d+(?:-[A-Z0-9]+)?)(?:\s+[FGBST]\d+(?:-[A-Z0-9]+)?)*|T\d+-\d+", text):
        return "方框构件码"
    if re.fullmatch(r"(?:E|N|EL)\s*[+\-]?\d+(?:\.\d+)?", text):
        return "坐标"
    if text.startswith(("SEE ISO", "CONN TO", "CONT TO", "NOT FOUND")):
        return "跨页/连接"
    if "DIMENSION" in text.upper() or "SLOPE" in text.upper():
        return "尺寸说明"
    if text.startswith(("STEM", "ORIENT", "FLOW", "GRID")):
        return "方向/工艺"
    return "其他注释"


def load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", size)
    except OSError:
        return ImageFont.load_default()


def crop_for_text(
    image_path: Path, trace: dict[str, Any], text: dict[str, Any], half_width: int = 1200, half_height: int = 800
) -> Image.Image:
    with Image.open(image_path) as source:
        source = source.convert("RGB")
        view_x, view_y, view_width, view_height = trace["coordinate_system"]["visible_sheet_viewbox"]
        left, bottom, right, top = text["psm_bbox_page_units"]
        anchor_x, anchor_y = text["sheet_anchor_normalized"]
        centre_x = (left + right + anchor_x * 16800) / 3
        centre_y = (bottom + top + anchor_y * 16800) / 3
        # Convert Shape2D lower-left paper coordinates into the SVG/PNG's
        # top-left page coordinate system.
        pixel_x = (centre_x - view_x) / view_width * source.width
        pixel_y = ((PAGE_HEIGHT - centre_y) - view_y) / view_height * source.height
        pixel_half_width = half_width / view_width * source.width
        pixel_half_height = half_height / view_height * source.height
        crop = source.crop((
            max(0, round(pixel_x - pixel_half_width)),
            max(0, round(pixel_y - pixel_half_height)),
            min(source.width, round(pixel_x + pixel_half_width)),
            min(source.height, round(pixel_y + pixel_half_height)),
        ))
        panel = Image.new("RGB", (420, 280), "white")
        scale = min(panel.width / crop.width, panel.height / crop.height)
        scaled = crop.resize(
            (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
            Image.Resampling.LANCZOS,
        )
        panel.paste(scaled, ((panel.width - scaled.width) // 2, (panel.height - scaled.height) // 2))
        return panel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("output/left_text_qa"))
    parser.add_argument("--per-path", type=int, default=4, help="Samples per mapping/category combination.")
    parser.add_argument("--half-width", type=int, default=1200, help="Half crop width in SHA page units.")
    parser.add_argument("--half-height", type=int, default=800, help="Half crop height in SHA page units.")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    groups: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for drawing in manifest["drawings"]:
        for page in drawing["pages"]:
            if not page.get("pdf_png"):
                continue
            trace = json.loads(Path(page["trace"]).read_text(encoding="utf-8"))
            view_x, _, view_width, _ = trace["coordinate_system"]["visible_sheet_viewbox"]
            cutoff = view_x + view_width * 0.55
            for text in trace["text"]:
                if text["sheet_anchor_normalized"][0] * 16800 >= cutoff:
                    continue
                groups[(text["position_mapping"], category(text["text"]))].append((drawing, page, {"trace": trace, "text": text}))

    selections: list[tuple[str, str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for (mapping, kind), rows in sorted(groups.items()):
        # One representative per style avoids filling the board with identical
        # occurrences of a single SHA text family.
        seen_styles: set[str] = set()
        for drawing, page, payload in rows:
            style = payload["text"]["style_ref"]
            if style in seen_styles:
                continue
            seen_styles.add(style)
            selections.append((mapping, kind, drawing, page, payload))
            if len(seen_styles) >= args.per_path:
                break

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    font = load_font(16)
    boards = 0
    rows_per_board = 4
    for offset in range(0, len(selections), rows_per_board):
        selected = selections[offset : offset + rows_per_board]
        board = Image.new("RGB", (840, len(selected) * 330), "#e8e8e8")
        draw = ImageDraw.Draw(board)
        for row, (mapping, kind, drawing, page, payload) in enumerate(selected):
            y = row * 330
            trace, text = payload["trace"], payload["text"]
            sha_png = Path(page["sha_svg"]).with_suffix(".png")
            pdf_png = Path(page["pdf_png"])
            board.paste(crop_for_text(sha_png, trace, text, args.half_width, args.half_height), (0, y))
            board.paste(crop_for_text(pdf_png, trace, text, args.half_width, args.half_height), (420, y))
            draw.rectangle((0, y + 280, 840, y + 330), fill="white")
            label = f"{mapping} | {kind} | {text['text']} | {drawing['drawing']} {page['sheet_stream']}"
            draw.text((8, y + 287), label, fill="black", font=font)
            draw.text((8, y + 8), "SHA-only render", fill="#b00020", font=font)
            draw.text((428, y + 8), "PDF visual reference", fill="#0050a4", font=font)
        boards += 1
        board.save(out_dir / f"left-text-{boards:03d}.png", optimize=True)

    index = {
        "scope": "Left ISO text only; PDF is visual QA only.",
        "mapping_category_groups": len(groups),
        "samples": len(selections),
        "boards": boards,
    }
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(index, ensure_ascii=False))


if __name__ == "__main__":
    main()
