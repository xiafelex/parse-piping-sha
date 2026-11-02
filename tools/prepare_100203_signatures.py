"""Extract WK/LHJ from the unit reference and reuse verified YHL/CP/LKF."""
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


def main():
    base = PROJECT_DIR / 'SHA文件转换'
    unit = base / '100203'
    reference = next(unit.glob('CAA-CE-1-ED-10-PIP-ISO-E-02030001*.pdf'))
    out = unit / '签名资源'
    audit = unit / '样本水印及编号标注审计'
    out.mkdir(exist_ok=True)
    audit.mkdir(exist_ok=True)
    streams = read_sha_streams(sorted((unit / '100203 SHA').glob('*.sha'))[0])
    inherited = next((v for data in streams.values()
                      if (v := declared_sheet_viewbox(data)) is not None), None)
    view = sheet_viewbox(sheet_streams(streams, {1})[0].data, inherited)
    selected = None
    with fitz.open(reference) as document:
        for number, page in enumerate(document, 1):
            if page.rect.width <= page.rect.height:
                continue
            names = {v.strip() for v in page.get_text().splitlines()}
            if not {'WK/YHL', 'LHJ'} <= names:
                continue
            w, h = page.rect.width, page.rect.height
            images = [(im[0], rect) for im in page.get_images()
                      for rect in page.get_image_rects(im[0])
                      if .80 <= rect.y0 / h <= .83 and rect.height / h < .04]
            drafted = sorted([(xref, rect) for xref, rect in images
                              if .28 <= rect.x0 / w <= .325], key=lambda item: item[1].x0)
            checked = [(xref, rect) for xref, rect in images if .35 <= rect.x0 / w <= .385]
            if len(drafted) == 2 and len(checked) == 1:
                selected = (number, w, h, {'WK': drafted[0], 'LHJ': checked[0]})
                break
    if selected is None:
        raise RuntimeError('No unambiguous WK/YHL and LHJ signature objects in reference')
    number, page_width, page_height, candidates = selected
    profile = {'signature_scale': 1.0, 'multi_signature_gap': 12.0,
               'source_pdf_path': str(reference), 'source_page': number,
               'sha_viewbox_scaled': list(view), 'people': {}}
    for name, (xref, rect) in candidates.items():
        with fitz.open(reference) as document:
            source = Image.open(io.BytesIO(document.extract_image(xref)['image'])).convert('RGB')
            source.save(audit / f'{name}原始图像.png')
            image = signature_rgba(source, 2, upscale=12)
        if image.getchannel('A').getbbox() is None:
            raise RuntimeError(f'Empty extracted signature: {name}')
        image.save(out / f'{name}.png')
        profile['people'][name] = {
            'file': f'{name}.png', 'field': 'RevisedBy' if name == 'WK' else 'CheckedBy',
            'flip_180': True,
            'width': rect.width * image.width / (source.width * 12) * view[2] / page_width * .90,
            'height': rect.height * image.height / (source.height * 12) * view[3] / page_height * .90,
            'source_xref': xref, 'source_pixels': list(source.size),
        }
        print(f'{name}: reference page {number}, xref {xref}, source {source.size}')
    for name, source_unit, field in [('YHL', '100400', 'RevisedBy'),
                                     ('CP', '100800', 'CheckedBy'),
                                     ('LKF', '100400', 'ApprovedBy')]:
        source_dir = base / source_unit / '签名资源'
        settings = json.loads((source_dir / 'profile.json').read_text(encoding='utf-8'))['people'][name]
        shutil.copy2(source_dir / settings['file'], out / f'{name}.png')
        profile['people'][name] = {key: settings[key] for key in ('width', 'height', 'flip_180')}
        profile['people'][name].update(file=f'{name}.png', field=field,
                                      asset_source=f'{source_unit}/签名资源/{settings["file"]}')
    (out / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with fitz.open() as document:
        page = document.new_page(width=600, height=250)
        for i, name in enumerate(('WK', 'LHJ')):
            page.insert_text((15, 25 + i * 120), name)
            page.insert_image(fitz.Rect(160, 10 + i * 120, 500, 115 + i * 120),
                              filename=str(out / f'{name}.png'), rotate=180)
        page.get_pixmap().save(audit / '新设计签名检查.png')


if __name__ == '__main__':
    main()
