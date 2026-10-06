"""Render an unmodified reference-PDF crop for manual signature extraction."""
import argparse
from pathlib import Path

import fitz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--page', type=int, required=True, help='One-based page')
    parser.add_argument('--rect', type=float, nargs=4, required=True)
    parser.add_argument('--scale', type=float, default=12)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(args.pdf) as document:
        page = document[args.page - 1]
        clip = fitz.Rect(args.rect)
        if clip.is_empty or not page.rect.contains(clip):
            raise ValueError('Crop must be entirely inside the page')
        pix = page.get_pixmap(matrix=fitz.Matrix(args.scale, args.scale),
                              clip=clip, alpha=False)
        pix.save(args.output)
        print(f'{args.output}: {pix.width} x {pix.height}; page {args.page}; {clip}')


if __name__ == '__main__':
    main()
