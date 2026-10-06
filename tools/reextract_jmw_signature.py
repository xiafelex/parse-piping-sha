"""Re-extract JMW's source image, retaining handwriting across table rules.

The source-specific bottom crop excludes the printed personnel code, not ink.
Only the requested signature resource and its person-level metadata are updated.
"""
import argparse
import io
import json
from pathlib import Path

import fitz
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference_pdf', type=Path)
    parser.add_argument('signature_dir', type=Path)
    args = parser.parse_args()
    with fitz.open(args.reference_pdf) as doc:
        source = Image.open(io.BytesIO(doc.extract_image(191)['image'])).convert('L')
    if source.size != (288, 151):
        raise ValueError('Unexpected JMW reference image; recheck xref and crop')
    grey = source.crop((0, 0, 288, 140))
    bands = []
    for y in range(grey.height):
        if sum(grey.getpixel((x, y)) < 180 for x in range(grey.width)) >= .8 * grey.width:
            if not bands or y > bands[-1][-1] + 1:
                bands.append([y])
            else:
                bands[-1].append(y)
    original = grey.copy()
    for band in bands:
        lo, hi = max(0, band[0] - 1), min(grey.height - 1, band[-1] + 1)
        for x in range(grey.width):
            # Preserve strokes crossing the boundary, without preserving the rule.
            ink = max(original.getpixel((x, lo - 1)), original.getpixel((x, hi + 1))) if lo > 0 and hi + 1 < grey.height else 255
            for y in range(lo, hi + 1):
                grey.putpixel((x, y), ink)
        if lo > 0 and hi + 1 < grey.height:
            def runs(y):
                result = []
                for x in range(grey.width):
                    if original.getpixel((x, y)) < 180:
                        if not result or x > result[-1][-1] + 1:
                            result.append([x])
                        else:
                            result[-1].append(x)
                return [(r[0], r[-1]) for r in result]
            below = runs(hi + 1)
            draw = ImageDraw.Draw(grey)
            for left, right in runs(lo - 1):
                if not below:
                    continue
                a, b = min(below, key=lambda r: abs(sum(r) - left - right))
                if abs(a + b - left - right) / 2 <= (hi - lo + 2) * 2:
                    # Interpolate only the narrow obscured boundary between
                    # observed pen strokes; don't erase diagonal crossings.
                    draw.polygon([(left, lo-1), (right, lo-1), (b, hi+1), (a, hi+1)], fill=0)
    grey = grey.transpose(Image.Transpose.ROTATE_180)
    alpha = grey.point(lambda v: max(0, min(255, round((245 - v) * 255 / 165))))
    bounds = alpha.getbbox()
    alpha = alpha.crop(bounds)
    native_size = alpha.size
    alpha = alpha.resize((alpha.width * 12, alpha.height * 12), Image.Resampling.LANCZOS)
    output = Image.new('RGBA', alpha.size, (23, 32, 42, 0))
    output.putalpha(alpha)
    output.save(args.signature_dir / 'JMW.png')
    profile_path = args.signature_dir / 'profile.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    person = profile['people']['JMW']
    person.update(width=person['slot_width'] * native_size[0] / 288 * .9,
                  height=person['slot_height'] * native_size[1] / 151 * .9,
                  source_pdf_path=str(args.reference_pdf), source_page=15,
                  extraction='preserve-crossing-strokes; source crop 0,0,288,140',
                  source_ink_bbox=list(bounds))
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('JMW native ink size:', native_size, 'display:', person['width'], person['height'])


if __name__ == '__main__':
    main()
