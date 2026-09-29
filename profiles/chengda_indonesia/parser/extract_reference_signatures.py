#!/usr/bin/env python3
"""Rebuild Chengda signature assets from the issued reference PDF.

The reference PDF stores PJ and ML as very small JPEG image objects crossed
by the revision-table rules.  This script extracts the original objects,
removes those two rules, converts the paper background to alpha, and performs
one high-quality enlargement before SVG/PDF embedding.  Keeping this process
reproducible avoids repeatedly screen-cropping already rasterised signatures.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from chengda_paths import ASSET_DIR, PROJECT_DIR

import fitz
from PIL import Image, ImageFilter


REFERENCE_PROFILES = {
    "000200": {
    # person: (PDF key, 1-based page, embedded pixel size, quarter-turns CCW)
    # Page 1 contains a substantially clearer PJ object than the 21x23 copy
    # reused on ISO pages.  Its asset is stored 180 degrees from final plot
    # orientation because the SVG renderer applies the common project flip.
    "XZS": ("primary", 1, (339, 166), 2),
    "PJ": ("primary", 1, (47, 41), 2),
    # The 000203 reference page 85 contains the same ML signature as a
    # 61x41 object, instead of the unreadable 21x31 ISO-page copy in 000200.
    "ML": ("ml", 85, (61, 41), 2),
    },
    "100200": {
        # Reference page 66 is 10-42-0200-26-10AB02113-01.  These are the
        # original embedded objects, not crops from a rendered screenshot.
        "ZBT": ("primary", 66, (210, 83), 2),
        "LHJ": ("primary", 66, (97, 57), 2),
        "LKF": ("primary", 66, (156, 52), 2),
    },
}
INK_RGB = (23, 32, 42)


def locate_xref(document: fitz.Document, page_index: int, size: tuple[int, int]) -> int:
    page = document[page_index]
    matches: list[int] = []
    for image in page.get_images(full=True):
        xref = int(image[0])
        payload = document.extract_image(xref)
        if (int(payload["width"]), int(payload["height"])) == size:
            matches.append(xref)
    if len(matches) != 1:
        raise RuntimeError(f"page {page_index + 1}: expected one {size} image, got {matches}")
    return matches[0]


def remove_embedded_table_rules(image: Image.Image, quarter_turns: int) -> Image.Image:
    grey = image.convert("L")
    for _ in range(quarter_turns % 4):
        grey = grey.transpose(Image.Transpose.ROTATE_90)
    rule_rows = []
    for y in range(grey.height):
        # JPEG table rules may be light grey at one edge; 245 reliably finds
        # those full-width rows while the handwriting never spans 80% width.
        dark = sum(grey.getpixel((x, y)) < 245 for x in range(grey.width))
        if dark >= grey.width * 0.80:
            rule_rows.append(y)
    rule_bands: list[tuple[int, int]] = []
    for row in rule_rows:
        if not rule_bands or row > rule_bands[-1][1] + 1:
            rule_bands.append((row, row))
        else:
            rule_bands[-1] = (rule_bands[-1][0], row)
    # A single thick JPEG rule spans several adjacent pixel rows.  It is not
    # a pair of table boundaries and must never be used as a crop interval.
    if len(rule_bands) >= 2:
        lower = rule_bands[0][1]
        upper = rule_bands[-1][0]
        if upper - lower >= 4:
            grey = grey.crop((0, lower + 1, grey.width, upper))
    return grey


def signature_rgba(
    image: Image.Image,
    quarter_turns: int,
    upscale: int = 12,
    remove_vertical_rules: bool = True,
) -> Image.Image:
    grey = remove_embedded_table_rules(image, quarter_turns)
    # Values above 245 are JPEG/paper noise and become transparent. Values at
    # 80 and below are full ink; intermediate antialiasing is retained.
    alpha = grey.point(
        lambda value: max(0, min(255, round((245 - value) * 255 / 165)))
    )
    # Some embedded signature objects include the revision-table rules in the
    # bitmap itself. Remove only rows/columns spanning almost the whole image,
    # then trim the transparent margin. Handwriting cannot satisfy these
    # full-span thresholds, so this is independent of the person's signature.
    rule_rows = [
        y
        for y in range(alpha.height)
        if sum(alpha.getpixel((x, y)) > 8 for x in range(alpha.width))
        >= alpha.width * 0.78
    ]
    rule_columns = (
        [
            x
            for x in range(alpha.width)
            if sum(alpha.getpixel((x, y)) > 8 for y in range(alpha.height))
            >= alpha.height * 0.96
        ]
        if remove_vertical_rules
        else []
    )
    pixels = alpha.load()
    for y in rule_rows:
        for clear_y in range(max(0, y - 3), min(alpha.height, y + 4)):
            for x in range(alpha.width):
                pixels[x, clear_y] = 0
    for x in rule_columns:
        for clear_x in range(max(0, x - 3), min(alpha.width, x + 4)):
            for y in range(alpha.height):
                pixels[clear_x, y] = 0
    ink_bbox = alpha.getbbox()
    if ink_bbox is not None:
        left, top, right, bottom = ink_bbox
        alpha = alpha.crop(
            (
                max(0, left - 1),
                max(0, top - 1),
                min(alpha.width, right + 1),
                min(alpha.height, bottom + 1),
            )
        )
    alpha = alpha.resize(
        (alpha.width * upscale, alpha.height * upscale),
        Image.Resampling.LANCZOS,
    ).filter(ImageFilter.UnsharpMask(radius=1.2, percent=110, threshold=2))
    rgba = Image.new("RGBA", alpha.size, (*INK_RGB, 0))
    rgba.putalpha(alpha)
    return rgba


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_pdf", type=Path)
    parser.add_argument("--profile", choices=tuple(REFERENCE_PROFILES), default="000200")
    parser.add_argument(
        "--ml-reference-pdf",
        type=Path,
        required=False,
        help="Reference PDF whose page 85 contains the clearer ML object",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ASSET_DIR / "signatures",
    )
    parser.add_argument(
        "--people",
        nargs="+",
        default=None,
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sources = REFERENCE_PROFILES[args.profile]
    people = tuple(args.people) if args.people else tuple(sources)
    unknown = [person for person in people if person not in sources]
    if unknown:
        parser.error(f"--people不属于{args.profile}配置：{', '.join(unknown)}")
    if any(sources[person][0] == "ml" for person in people) and args.ml_reference_pdf is None:
        parser.error("当前人员需要--ml-reference-pdf")
    documents = {"primary": fitz.open(args.reference_pdf)}
    if args.ml_reference_pdf is not None:
        documents["ml"] = fitz.open(args.ml_reference_pdf)
    try:
        for person in people:
            document_key, page_number, size, quarter_turns = sources[person]
            document = documents[document_key]
            page_index = page_number - 1
            if not 0 <= page_index < len(document):
                raise ValueError(f"page {page_number} is outside 1..{len(document)}")
            xref = locate_xref(document, page_index, size)
            payload = document.extract_image(xref)
            source = Image.open(io.BytesIO(payload["image"]))
            # ZBT contains genuine near-full-height pen strokes. Only its
            # horizontal table rules are safe to remove automatically.
            output = signature_rgba(
                source,
                quarter_turns,
                remove_vertical_rules=(person != "ZBT"),
            )
            target = args.output_dir / f"{person}.png"
            output.save(target, optimize=True)
            print(
                f"{person}: pdf={document_key}, page={page_number}, xref={xref}, "
                f"source={source.size}, output={output.size}, {target}"
            )
    finally:
        for document in documents.values():
            document.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
