"""Refresh DZX or ZXE from clear FCN page-18 stamps."""
import argparse
import io
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiles/chengda_indonesia/parser'))
from chengda_paths import PROJECT_DIR
from extract_reference_signatures import signature_rgba


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--person', choices=('DZX', 'ZXE'), default='ZXE')
    person = parser.parse_args().person
    unit = PROJECT_DIR / 'SHA文件转换/404049-WS'
    assets = unit / '签名资源'
    profile_path = assets / 'profile.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    backup = assets / (person + '更新前备份_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    backup.mkdir()
    for name in ('profile.json', profile['people'][person]['file']):
        shutil.copy2(assets / name, backup / name)
    reference = Path(profile['source_pdf_path'])
    with fitz.open(reference) as document:
        page = document[17]
        min_x, max_x = (490, 540) if person == 'DZX' else (680, 735)
        stamps = [a for a in page.annots() if a.type[1] == 'Stamp'
                  and min_x <= a.rect.x0 <= max_x and 945 <= a.rect.y0 <= 970]
        if len(stamps) != 1:
            raise RuntimeError(f'Expected unique {person} stamp on page 18, found {len(stamps)}')
        stamp = stamps[0]
        pix = stamp.get_pixmap(matrix=fitz.Matrix(16, 16), colorspace=fitz.csRGB, alpha=False)
        rgba = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGBA')
        source = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba).convert('RGB')
        source.save(unit / f'签名检查/{person}第18页图章原图.png')
        image = signature_rgba(source, 0, upscale=1)
        annotation_xref = stamp.xref
    if image.getchannel('A').getbbox() is None:
        raise RuntimeError(f'Extracted {person} stamp has no ink')
    target = assets / f'{person}-page18.png'
    image.save(target)
    previous = profile['people'][person]
    previous_height = previous['height']
    if person == 'ZXE':
        profile['signature_scale'] = 1.15
    profile['people'][person] = {
        'file': target.name, 'field': previous['field'], 'flip_180': False,
        'width': previous['width'] if person == 'DZX' else previous_height * image.width / image.height,
        'height': previous_height,
        'source_pdf_path': str(reference), 'source_page': 18,
        'source_annotation': annotation_xref,
        'sizing_reference': ('Retain existing width, height and unit scale' if person == 'DZX'
                             else 'Retain original ink height and use the new signature aspect ratio; unit scale 1.15'),
    }
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with fitz.open() as document:
        page = document.new_page(width=650, height=320)
        for i, name in enumerate(('DZX', 'LL', 'ZXE')):
            settings = profile['people'][name]
            page.insert_text((15, 30 + i * 100), name)
            page.insert_image(fitz.Rect(160, 10 + i * 100, 500, 95 + i * 100),
                              filename=str(assets / settings['file']), rotate=180 if settings['flip_180'] else 0)
        page.get_pixmap().save(unit / f'签名检查/{person}更新后签名检查.png')
    print(f'Updated {person} from page 18 annotation {annotation_xref}; scale={profile["signature_scale"]}; source={source.size}; ink={image.size}')


if __name__ == '__main__':
    main()
