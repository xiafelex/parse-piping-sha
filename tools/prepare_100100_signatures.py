"""Extract WZQ from the supplied A3 reference and reuse verified LY/MXY assets."""
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
    unit = base / '100100'
    reference = next(unit.glob('CAA-CE-1-ED-10-PIP-ISO-E-0100001*.pdf'))
    out = unit / '签名资源'
    audit = unit / '样本水印及编号标注审计'
    out.mkdir(exist_ok=True)
    audit.mkdir(exist_ok=True)
    streams = read_sha_streams(unit / '100100 SHA/10-42-0100-26-10BP01002A-01.sha')
    inherited = next((v for data in streams.values() if (v := declared_sheet_viewbox(data)) is not None), None)
    view = sheet_viewbox(sheet_streams(streams, {1})[0].data, inherited)
    with fitz.open(reference) as doc:
        page = doc[10]
        candidates = [(im[0], rect) for im in page.get_images() for rect in page.get_image_rects(im[0])
                      if 350 < rect.x0 < 355 and 680 < rect.y0 < 687]
        if len(candidates) != 1:
            raise RuntimeError(f'Expected unique WZQ signature: {candidates}')
        xref, rect = candidates[0]
        page_width, page_height = page.rect.width, page.rect.height
    with fitz.open(reference) as doc:
        source = Image.open(io.BytesIO(doc.extract_image(xref)['image'])).convert('RGB')
        source.save(audit / 'WZQ原始图像.png')
        image = signature_rgba(source, 2, upscale=12)
        image.save(out / 'WZQ.png')
    profile = {'source_pdf_path': str(reference), 'source_page': 11, 'sha_viewbox_scaled': list(view), 'people': {
        'WZQ': {'file': 'WZQ.png', 'field': 'RevisedBy', 'flip_180': True,
                'width': rect.width * image.width / (source.width * 12) * view[2] / page_width * .90,
                'height': rect.height * image.height / (source.height * 12) * view[3] / page_height * .90,
                'source_xref': xref, 'source_pixels': list(source.size)},
    }}
    for person, source_unit, field in [('LY', '100400', 'CheckedBy'), ('MXY', '101604', 'ApprovedBy')]:
        source_dir = base / source_unit / '签名资源'
        settings = json.loads((source_dir / 'profile.json').read_text(encoding='utf-8'))['people'][person]
        shutil.copy2(source_dir / settings['file'], out / f'{person}.png')
        profile['people'][person] = {k: settings[k] for k in ['width', 'height', 'flip_180']}
        profile['people'][person].update(file=f'{person}.png', field=field, asset_source=f'{source_unit}/签名资源/{settings["file"]}')
    (out / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    preview = fitz.open()
    page = preview.new_page(width=600, height=300)
    for i, person in enumerate(profile['people']):
        page.insert_text((10, 30+i*90), person)
        page.insert_image(fitz.Rect(160, 10+i*90, 500, 85+i*90), filename=str(out/f'{person}.png'), rotate=180)
    page.get_pixmap().save(audit/'签名检查.png')
    print(out)


if __name__ == '__main__':
    main()
