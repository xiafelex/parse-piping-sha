#!/usr/bin/env python3
"""Prepare a manually cleaned, upright signature for direct SVG embedding."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def prepare(source: Path, target: Path, padding: int = 12) -> None:
    image = Image.open(source).convert("RGBA")
    grey = image.convert("L")
    # Remove only the near-white paper background. This does not perform line
    # detection or morphology, so manually restored handwriting is untouched.
    alpha = grey.point(lambda value: max(0, min(255, round((248 - value) * 255 / 180))))
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError(f"签名图像没有可见笔画：{source}")
    left, top, right, bottom = bbox
    crop = (
        max(0, left - padding),
        max(0, top - padding),
        min(image.width, right + padding),
        min(image.height, bottom + padding),
    )
    rgba = Image.new("RGBA", image.size, (23, 32, 42, 0))
    rgba.putalpha(alpha)
    target.parent.mkdir(parents=True, exist_ok=True)
    rgba.crop(crop).save(target, optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    prepare(args.source, args.target)
    print(args.target.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
