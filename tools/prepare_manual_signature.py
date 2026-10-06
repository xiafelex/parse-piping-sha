"""Prepare a user-cleaned signature without reconstructing handwriting."""
import argparse
from pathlib import Path
from PIL import Image, ImageChops


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--crop', type=int, nargs=4, required=True)
    args = parser.parse_args()
    source = Image.open(args.source).convert('RGBA').crop(tuple(args.crop))
    # White-background removal only: no rule erasure or stroke interpolation.
    alpha = source.convert('L').point(lambda v: 0 if v >= 245 else 255-v)
    alpha = ImageChops.multiply(alpha, source.getchannel('A'))
    bounds = alpha.getbbox()
    if bounds is None:
        raise ValueError('No handwriting found')
    output = Image.new('RGBA', source.size, (0, 0, 0, 0))
    output.putalpha(alpha)
    output = output.crop(bounds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.save(args.output)
    print(f'{args.output}: {output.size}, ink bounds within crop={bounds}')


if __name__ == '__main__':
    main()
