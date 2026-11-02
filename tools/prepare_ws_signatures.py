"""Extract WS stamp signatures and FCN images; normalize FCN scale using LL."""
import io
import json
import shutil
import sys
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiles/chengda_indonesia/parser'))
from chengda_paths import PROJECT_DIR
from extract_reference_signatures import signature_rgba
from sha_to_svg_prototype import read_sha_streams, declared_sheet_viewbox, sheet_viewbox
from inject_sha_weld_callouts import sheet_streams

FIELDS = ('RevisedBy', 'CheckedBy', 'ApprovedBy')


def view_for(unit):
    streams = read_sha_streams(sorted((unit / 'SHA').rglob('*.sha'))[0])
    inherited = next((v for data in streams.values() if (v := declared_sheet_viewbox(data)) is not None), None)
    return sheet_viewbox(sheet_streams(streams, {1})[0].data, inherited)


def write_profile(out, profile):
    (out / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    base = PROJECT_DIR / 'SHA文件转换'
    references = PROJECT_DIR / 'PCF处理/施工单位焊口图图纸（二阶段）261022/空白FIN图'
    unit = base / '200300-WS'
    out = unit / '签名资源'
    out.mkdir(exist_ok=True)
    reference = next(references.glob('CAA-CE-1-ED-20-PIP-ISO-E-03901*.pdf'))
    view = view_for(unit)
    profile = {'signature_scale': 1.0, 'source_pdf_path': str(reference), 'source_page': 6,
               'sha_viewbox_scaled': list(view), 'people': {}}
    with fitz.open(reference) as document:
        page = document[5]
        stamps = sorted([a for a in page.annots() if a.type[1] == 'Stamp'
                         and 470 < a.rect.x0 < 750 and 950 < a.rect.y0 < 1000], key=lambda a: a.rect.x0)
        if len(stamps) != 3:
            raise RuntimeError(f'Expected three personnel stamps, found {len(stamps)}')
        for person, field, annotation in zip(('TLZ', 'LL', 'ZH'), FIELDS, stamps):
            pix = annotation.get_pixmap(matrix=fitz.Matrix(16, 16), colorspace=fitz.csRGB, alpha=False)
            # Stamp pixmaps can retain an alpha channel even with alpha=False.
            # Decode the PNG instead of assuming a three-byte sample stride.
            rgba = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGBA')
            source = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba).convert('RGB')
            image = signature_rgba(source, 0, upscale=1)
            image.save(out / f'{person}.png')
            profile['people'][person] = {
                'file': f'{person}.png', 'field': field, 'flip_180': False,
                'width': annotation.rect.width * image.width / source.width * view[2] / page.rect.width * .90,
                'height': annotation.rect.height * image.height / source.height * view[3] / page.rect.height * .90,
                'source_annotation': annotation.xref,
            }
    write_profile(out, profile)
    second = base / '200600-WS/签名资源'
    second.mkdir(exist_ok=True)
    if view_for(base / '200600-WS')[2:] != view[2:]:
        raise RuntimeError('WS unit viewbox differs; signature scaling needs review')
    for person in ('TLZ', 'LL', 'ZH'):
        shutil.copy2(out / f'{person}.png', second / f'{person}.png')
    write_profile(second, profile)

    target = base / '404049-WS/签名资源'
    target.mkdir(exist_ok=True)
    fcn = next(references.glob('CAA-CE-1-ED-40-PIP-FCN-E-0002*.pdf'))
    extracted = {}
    with fitz.open(fcn) as document:
        page = document[8]
        for person, limits in {'DZX': (2080, 2120), 'LL': (2150, 2190), 'ZXE': (2220, 2270)}.items():
            candidates = [(im[0], r) for im in page.get_images() for r in page.get_image_rects(im[0])
                          if limits[0] <= r.x0 <= limits[1] and 1245 <= r.y0 <= 1270]
            if len(candidates) != 1:
                raise RuntimeError(f'Ambiguous FCN signature: {person}: {candidates}')
            xref, rect = candidates[0]
            source = Image.open(io.BytesIO(document.extract_image(xref)['image'])).convert('RGB')
            image = signature_rgba(source, 2, upscale=12)
            extracted[person] = (image, rect.width * image.width / (source.width * 12),
                                 rect.height * image.height / (source.height * 12), xref)
    ratio = profile['people']['LL']['height'] / extracted['LL'][2]
    result = {'signature_scale': 1.0, 'source_pdf_path': str(fcn), 'source_page': 9,
              'sha_viewbox_scaled': list(view_for(base / '404049-WS')),
              'scale_reference': 'Reuse LL ink height to normalize the FCN drawing viewport', 'people': {}}
    for person, field in zip(('DZX', 'LL', 'ZXE'), FIELDS):
        if person == 'LL':
            shutil.copy2(out / 'LL.png', target / 'LL.png')
            result['people'][person] = dict(profile['people']['LL'], asset_source='200300-WS/签名资源/LL.png')
        else:
            image, width, height, xref = extracted[person]
            image.save(target / f'{person}.png')
            result['people'][person] = {'file': f'{person}.png', 'field': field, 'flip_180': True,
                                        'width': width * ratio, 'height': height * ratio, 'source_xref': xref}
    write_profile(target, result)
    with fitz.open() as document:
        page = document.new_page(width=650, height=500)
        for index, (person, path, settings) in enumerate([
            *[(p, out / f'{p}.png', profile['people'][p]) for p in ('TLZ', 'LL', 'ZH')],
            *[(p, target / f'{p}.png', result['people'][p]) for p in ('DZX', 'ZXE')],
        ]):
            page.insert_text((15, 30 + index * 95), person)
            page.insert_image(fitz.Rect(180, 10 + index * 95, 500, 90 + index * 95),
                              filename=str(path), rotate=180 if settings['flip_180'] else 0)
        page.get_pixmap().save(base / '200300-WS/签名检查/新签名资源检查.png')
    print('Prepared TLZ/LL/ZH for 200300-WS and 200600-WS; DZX/LL/ZXE for 404049-WS')


if __name__ == '__main__':
    main()
