#!/usr/bin/env python3
"""Create visual QA boards for the bounded SHA-only left-text trial.

PDF PNGs are paired only as visual references.  This script never provides
coordinates, text, or geometry to the renderer.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

from build_left_text_offset_qa import crop_for_text, load_font


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_manifest", type=Path)
    parser.add_argument("trial_manifest", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("output/left_text_ten_sample/qa"))
    parser.add_argument(
        "--png-root",
        type=Path,
        help="Optional raster root mirroring the trial SVG tree.",
    )
    args = parser.parse_args()

    corpus = json.loads(args.corpus_manifest.read_text(encoding="utf-8"))
    pdf_by_page = {
        (drawing["drawing"], page["sheet_stream"]): page["pdf_png"]
        for drawing in corpus["drawings"]
        for page in drawing["pages"]
    }
    trial = json.loads(args.trial_manifest.read_text(encoding="utf-8"))
    selections = []
    for page in trial["pages"]:
        trace = json.loads(Path(page["trace"]).read_text(encoding="utf-8"))
        changed = [record for record in trace["text"] if record["position_mapping"] == "sha-left-free-anchor-plus-psm-size"]
        if not changed:
            continue
        # One representative per target prefix per drawing keeps the review
        # small while preserving INSUL/CLASS/TRACE coverage.
        by_prefix = defaultdict(list)
        for record in changed:
            prefix = next(prefix for prefix in trial["prefixes"] if record["text"].upper().startswith(prefix))
            by_prefix[prefix].append(record)
        for prefix, records in by_prefix.items():
            selections.append((page, trace, prefix, sorted(records, key=lambda item: (-len(item["text"]), item["text"]))[0]))

    selected_by_key = {}
    for page, trace, prefix, record in selections:
        key = (page["drawing"], prefix)
        old = selected_by_key.get(key)
        if old is None or page["changed_text_count"] > old[0]["changed_text_count"]:
            selected_by_key[key] = (page, trace, prefix, record)
    rows = [selected_by_key[key] for key in sorted(selected_by_key)]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    font = load_font(16)
    boards = 0
    for start in range(0, len(rows), 4):
        batch = rows[start : start + 4]
        board = Image.new("RGB", (840, len(batch) * 330), "#e8e8e8")
        draw = ImageDraw.Draw(board)
        for row, (page, trace, prefix, record) in enumerate(batch):
            y = row * 330
            svg_path = Path(page["svg"])
            if args.png_root:
                trial_root = args.trial_manifest.parent.resolve()
                sha_png = args.png_root.resolve() / svg_path.relative_to(trial_root).with_suffix(".png")
            else:
                sha_png = svg_path.with_suffix(".png")
            pdf_png = Path(pdf_by_page[(page["drawing"], page["sheet_stream"])])
            board.paste(crop_for_text(sha_png, trace, record, 700, 480), (0, y))
            board.paste(crop_for_text(pdf_png, trace, record, 700, 480), (420, y))
            draw.rectangle((0, y + 280, 840, y + 330), fill="white")
            draw.text((8, y + 8), "SHA-only trial render", fill="#b00020", font=font)
            draw.text((428, y + 8), "PDF visual reference", fill="#0050a4", font=font)
            draw.text(
                (8, y + 287),
                f"{prefix} | {record['text']} | {page['drawing']} {page['sheet_stream']}",
                fill="black",
                font=font,
            )
        boards += 1
        board.save(args.out_dir / f"trial-text-{boards:03d}.png", optimize=True)
    print(json.dumps({"representatives": len(rows), "boards": boards}, ensure_ascii=False))


if __name__ == "__main__":
    main()
