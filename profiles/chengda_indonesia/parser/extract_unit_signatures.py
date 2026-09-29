#!/usr/bin/env python3
"""Extract the three revision signatures from a unit reference PDF."""

from __future__ import annotations

import argparse
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import fitz
from PIL import Image, ImageFilter

from extract_reference_signatures import signature_rgba


SHEET_WIDTH = 16840.0
SHEET_HEIGHT = 11910.0
# Cropped transparent PNGs have visibly stronger edges than the JPEG objects
# in the issued PDF. A uniform 90% optical scale preserves the source slot
# positions while matching the apparent handwriting size.
SIGNATURE_DISPLAY_SCALE = 0.90
FIELDS = ("RevisedBy", "CheckedBy", "ApprovedBy")
X_RANGES = {
    "RevisedBy": (470.0, 570.0),
    "CheckedBy": (570.0, 655.0),
    "ApprovedBy": (655.0, 750.0),
}


def crossing_signature_rgba(source: Image.Image, upscale: int = 12) -> Image.Image:
    """Remove full-width rules without cropping the pen strokes across them.

    The optional mode is for references with handwriting on a table boundary.
    Pixels dark on both sides of a rule are retained as crossing strokes.
    """
    grey = source.convert("L")
    rows = [y for y in range(grey.height)
            if sum(grey.getpixel((x, y)) < 180 for x in range(grey.width))
            >= grey.width * 0.80]
    bands: list[list[int]] = []
    for y in rows:
        if not bands or y > bands[-1][-1] + 1:
            bands.append([y])
        else:
            bands[-1].append(y)
    original = grey.copy()
    for band in bands:
        lo, hi = max(0, band[0] - 1), min(grey.height - 1, band[-1] + 1)
        for x in range(grey.width):
            ink = (max(original.getpixel((x, lo - 1)),
                       original.getpixel((x, hi + 1)))
                   if lo > 0 and hi + 1 < grey.height else 255)
            for y in range(lo, hi + 1):
                grey.putpixel((x, y), ink)
    if len(bands) >= 2:
        # Below the second boundary is the printed personnel identifier,
        # not handwriting. Retain a small stroke continuation at the rule.
        grey = grey.crop((0, 0, grey.width, min(grey.height, bands[-1][-1] + 5)))
    grey = grey.transpose(Image.Transpose.ROTATE_180)
    alpha = grey.point(lambda v: max(0, min(255, round((245 - v) * 255 / 165))))
    bounds = alpha.getbbox()
    if bounds is None:
        raise RuntimeError("Empty signature after rule removal")
    alpha = alpha.crop(bounds)
    alpha = alpha.resize((alpha.width * upscale, alpha.height * upscale),
                         Image.Resampling.LANCZOS).filter(
                             ImageFilter.UnsharpMask(radius=1.2, percent=110, threshold=2))
    output = Image.new("RGBA", alpha.size, (23, 32, 42, 0))
    output.putalpha(alpha)
    return output


@dataclass(frozen=True)
class Candidate:
    page_number: int
    field: str
    xref: int
    rect: fitz.Rect
    source_width: int
    source_height: int
    extension: str

    @property
    def score(self) -> int:
        return self.source_width * self.source_height


def person_key(value: str) -> str:
    return re.split(r"[\\/]", value.strip())[-1].strip()


def page_candidates(document: fitz.Document, page_number: int) -> dict[str, list[Candidate]]:
    page = document[page_number - 1]
    if page.rect.width <= page.rect.height:
        return {}
    found: dict[str, list[Candidate]] = {}
    for image in page.get_images(full=True):
        xref = int(image[0])
        for rect in page.get_image_rects(xref):
            if not (930.0 <= rect.y0 <= 1015.0 and 8.0 <= rect.height <= 30.0):
                continue
            field = next(
                (
                    name
                    for name, (left, right) in X_RANGES.items()
                    if left <= rect.x0 and rect.x1 <= right
                ),
                None,
            )
            if field is None:
                continue
            candidate = Candidate(
                page_number=page_number,
                field=field,
                xref=xref,
                rect=rect,
                source_width=int(image[2]),
                source_height=int(image[3]),
                extension=str(image[8]),
            )
            peers = found.setdefault(field, [])
            duplicate = next(
                (
                    item
                    for item in peers
                    if abs(item.rect.x0 - candidate.rect.x0) < 0.5
                    and abs(item.rect.x1 - candidate.rect.x1) < 0.5
                    and abs(item.rect.y0 - candidate.rect.y0) < 0.5
                    and abs(item.rect.y1 - candidate.rect.y1) < 0.5
                ),
                None,
            )
            if duplicate is None:
                peers.append(candidate)
            elif candidate.score > duplicate.score:
                peers[peers.index(duplicate)] = candidate
    for peers in found.values():
        peers.sort(key=lambda item: (item.rect.x0, item.rect.y0, -item.score))
    return found


def select_page(
    document: fitz.Document,
    requested_page: int | None,
) -> dict[str, list[Candidate]]:
    if requested_page is not None:
        candidates = page_candidates(document, requested_page)
        if set(candidates) != set(FIELDS):
            raise RuntimeError(
                f"page {requested_page}: expected {FIELDS}, found {tuple(candidates)}"
            )
        return candidates
    for page_number in range(1, len(document) + 1):
        candidates = page_candidates(document, page_number)
        if set(candidates) == set(FIELDS):
            # Reference sets normally share one signature triplet across all
            # drawing pages. Returning the first complete landscape title
            # block also avoids repeatedly decoding thousands of reused image
            # xrefs in very large combined PDFs.
            return candidates
    raise RuntimeError("No landscape page contains all three title-block signatures")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_pdf", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--people", nargs=3, metavar=("REVISED", "CHECKED", "APPROVED"), required=True)
    parser.add_argument("--page", type=int)
    parser.add_argument("--sha-reference", type=Path,
                        help="Use the actual SHA viewbox for image dimensions")
    parser.add_argument("--preserve-crossing-strokes", action="store_true",
                        help="Keep handwriting crossing embedded table rules")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(args.reference_pdf)
    try:
        selected = select_page(document, args.page)
        first_candidate = next(iter(selected.values()))[0]
        page = document[first_candidate.page_number - 1]
        scale_x = SHEET_WIDTH / page.rect.width
        scale_y = SHEET_HEIGHT / page.rect.height
        profile: dict[str, object] = {
            "source_pdf": args.reference_pdf.name,
            "source_page": first_candidate.page_number,
            "people": {},
        }
        for field, raw_person in zip(FIELDS, args.people):
            keys = [person_key(item) for item in re.split(r"[\\/]", raw_person) if item.strip()]
            field_candidates = selected[field]
            if len(keys) == 1:
                candidates = [max(field_candidates, key=lambda item: item.score)]
            else:
                if len(field_candidates) < len(keys):
                    raise RuntimeError(
                        f"{field} {raw_person}: expected {len(keys)} signature images, "
                        f"found {len(field_candidates)}"
                    )
                candidates = field_candidates[: len(keys)]
            for key, candidate in zip(keys, candidates):
            # Reopen for each xref.  Large merged PDFs can reuse image names
            # across pages, and PyMuPDF's page-image traversal cache may
            # otherwise return the last decoded peer instead of this xref.
                with fitz.open(args.reference_pdf) as source_document:
                    payload = source_document.extract_image(candidate.xref)
            # Fully detach the image from the transient BytesIO before the
            # cleanup pipeline.  Some FlateDecode PNG objects otherwise stay
            # lazy and can yield an empty alpha mask during optimized save.
                with Image.open(io.BytesIO(payload["image"])) as opened:
                    source = opened.convert("RGB").copy()
                upscale = 12
                output = (crossing_signature_rgba(source, upscale=upscale)
                          if args.preserve_crossing_strokes
                          else signature_rgba(source, 2, upscale=upscale))
                if output.getchannel("A").getbbox() is None:
                    raise RuntimeError(
                        f"{field} {key}: extracted signature is empty "
                        f"(page={candidate.page_number}, xref={candidate.xref})"
                    )
                target = args.output_dir / f"{key}.png"
                output.save(target, optimize=True)
                width_ratio = output.width / (source.width * upscale)
                height_ratio = output.height / (source.height * upscale)
                profile["people"][key] = {
                    "file": target.name,
                    "width": round(
                        candidate.rect.width * scale_x * width_ratio * SIGNATURE_DISPLAY_SCALE,
                        3,
                    ),
                    "height": round(
                        candidate.rect.height * scale_y * height_ratio * SIGNATURE_DISPLAY_SCALE,
                        3,
                    ),
                    "slot_width": round(candidate.rect.width * scale_x, 3),
                    "slot_height": round(candidate.rect.height * scale_y, 3),
                    "flip_180": True,
                    "field": field,
                    "source_xref": candidate.xref,
                    "source_pixels": [candidate.source_width, candidate.source_height],
                }
                print(
                    f"{field}={key} -> {target.name}; page={candidate.page_number}, "
                    f"xref={candidate.xref}, source={source.size}, "
                    f"display=({candidate.rect.width:.2f}, {candidate.rect.height:.2f})"
                )
        if args.sha_reference is not None:
            import sha_to_svg_prototype as api
            streams = api.read_sha_streams(args.sha_reference)
            inherited = next((view for data in streams.values()
                              if (view := api.declared_sheet_viewbox(data)) is not None), None)
            from inject_sha_weld_callouts import sheet_streams
            target = sheet_streams(streams, {1})[0]
            view = api.sheet_viewbox(target.data, inherited)
            for person in profile["people"].values():
                for key in ("width", "slot_width"):
                    person[key] *= view[2] / SHEET_WIDTH
                for key in ("height", "slot_height"):
                    person[key] *= view[3] / SHEET_HEIGHT
            profile["sha_viewbox_scaled"] = list(view)
        profile["source_pdf_path"] = str(args.reference_pdf.resolve())
        (args.output_dir / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        document.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
