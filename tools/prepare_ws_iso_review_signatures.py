"""Prepare reused/annotation-stamp signatures for the four reviewed WS units."""
from pathlib import Path
import io
import json
import shutil
import sys

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiles/chengda_indonesia/units'))
import _bootstrap
from convert_ws_iso_review import unit_paths
from extract_reference_signatures import signature_rgba
from sha_to_svg_prototype import read_sha_streams, declared_sheet_viewbox, sheet_viewbox
from inject_sha_weld_callouts import sheet_streams

FIELDS = ('RevisedBy', 'CheckedBy', 'ApprovedBy')


def view_for(unit):
    streams = read_sha_streams(sorted((unit / 'SHA').rglob('*.sha'))[0])
    shared = next((v for d in streams.values() if (v := declared_sheet_viewbox(d)) is not None), None)
    return sheet_viewbox(sheet_streams(streams, {1})[0].data, shared)


def reused(source_unit, person, field, output):
    folder = _bootstrap.PROJECT_DIR / 'SHA文件转换' / source_unit / '签名资源'
    profile = json.loads((folder / 'profile.json').read_text(encoding='utf-8'))
    item = dict(profile['people'][person])
    source = folder / item['file']
    shutil.copy2(source, output / source.name)
    for key in ('width', 'height', 'layout_width'):
        if key in item:
            item[key] *= float(profile.get('signature_scale', 1.0))
    item.update(field=field, asset_source=str(source))
    return item


def stamp(reference, page_number, person, field, slot, output, view):
    with fitz.open(reference) as doc:
        page = doc[page_number - 1]
        candidates = sorted([
            a for a in page.annots() or [] if a.type[1] == 'Stamp'
            and 470 < a.rect.x0 < 750 and 940 < a.rect.y0 < 1010
        ], key=lambda a: a.rect.x0)
        if len(candidates) != 3:
            raise ValueError(f'{reference.name} page {page_number}: expected 3 signature stamps')
        annotation = candidates[slot]
        pix = annotation.get_pixmap(matrix=fitz.Matrix(16, 16), colorspace=fitz.csRGB, alpha=False)
        # Annotation pixmaps can remain RGBA despite alpha=False.
        rgba = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGBA')
        source = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba).convert('RGB')
        image = signature_rgba(source, 0, upscale=1)
        if image.getchannel('A').getbbox() is None:
            raise ValueError(f'Empty signature: {person}')
        filename = f'{person}.png'
        image.save(output / filename)
        return dict(file=filename, field=field, flip_180=False,
                    width=annotation.rect.width * image.width / source.width * view[2] / page.rect.width * .90,
                    height=annotation.rect.height * image.height / source.height * view[3] / page.rect.height * .90,
                    source_pdf_path=str(reference), source_page=page_number,
                    source_annotation=annotation.xref)


def prepare(name):
    unit, _, reference = unit_paths(name)
    output = unit / '签名资源'
    if (output / 'profile.json').exists():
        raise FileExistsError(f'Existing profile must be reviewed before replacing: {output}')
    output.mkdir(exist_ok=True)
    view = view_for(unit)
    profile = dict(signature_scale=1.0, source_pdf_path=str(reference),
                   sha_viewbox_scaled=list(view), people={})
    people = profile['people']
    if name == '404100-WS-FW':
        for person, field in zip(('DZX', 'LL', 'ZXE'), FIELDS):
            people[person] = reused('404049-WS', person, field, output)
    elif name in ('302100-WS', '302200-WS'):
        people['LCG'] = reused('301300', 'LCG', FIELDS[0], output)
        people['CJ'] = reused('301300', 'CJ', FIELDS[1], output)
        if name == '302100-WS':
            people['ZQ'] = stamp(reference, 7, 'ZQ', FIELDS[2], 2, output, view)
        else:
            people['DZX'] = reused('404049-WS', 'DZX', FIELDS[0], output)
            people['YKX'] = stamp(reference, 8, 'YKX', FIELDS[2], 2, output, view)
    elif name == '300500-WS':
        for person, field in zip(('DZX', 'LL'), FIELDS):
            people[person] = reused('404049-WS', person, field, output)
        # The first issued isometric in the FCN attachment is page 38.
        # MB is an embedded image, not an annotation stamp.
        with fitz.open(reference) as doc:
            page = doc[37]
            candidates = [(im[0], rect) for im in page.get_images()
                          for rect in page.get_image_rects(im[0])
                          if 680 < rect.x0 < 740 and 940 < rect.y0 < 980]
            if len(candidates) != 1:
                raise ValueError(f'MB signature image ambiguous: {candidates}')
            xref, rect = candidates[0]
            source = Image.open(io.BytesIO(doc.extract_image(xref)['image'])).convert('RGB')
            image = signature_rgba(source, 2, upscale=12)
            if image.getchannel('A').getbbox() is None:
                raise ValueError('MB signature image is empty')
            image.save(output / 'MB.png')
            people['MB'] = dict(
                file='MB.png', field=FIELDS[2], flip_180=True,
                width=rect.width * image.width / (source.width * 12) * view[2] / page.rect.width * .90,
                height=rect.height * image.height / (source.height * 12) * view[3] / page.rect.height * .90,
                source_pdf_path=str(reference), source_page=38, source_xref=xref,
            )
    else:
        for slot, (person, field) in enumerate(zip(('Zhang Shijin', 'Ye Zhiyu', 'Cui Jing'), FIELDS)):
            people[person] = stamp(reference, 9, person, field, slot, output, view)
    (output / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(name, 'prepared signatures:', ', '.join(people))


if __name__ == '__main__':
    for unit_name in sys.argv[1:] or ('404100-WS-FW', '302100-WS', '302200-WS', '000800-WS'):
        prepare(unit_name)
