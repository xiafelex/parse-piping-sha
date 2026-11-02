"""Extract HBY from the issued reference; retain verified PJ and CP assets."""
import io
import json
import sys
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiles/chengda_indonesia/parser'))
from chengda_paths import PROJECT_DIR
from extract_reference_signatures import signature_rgba
from extract_unit_signatures import select_page
from sha_to_svg_prototype import read_sha_streams, declared_sheet_viewbox, sheet_viewbox
from inject_sha_weld_callouts import sheet_streams


def main():
    unit = PROJECT_DIR / 'SHA文件转换' / '100600'
    reference = next(unit.glob('CAA-CE-1-ED-10-PIP-ISO-E-060001*.pdf'))
    out = unit / '签名资源'
    audit = unit / '样本水印及编号标注审计'
    audit.mkdir(exist_ok=True)
    profile_path = out / 'profile.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    for person in ('PJ', 'CP'):
        if not (out / profile['people'][person]['file']).is_file():
            raise RuntimeError(f'Missing verified signature asset: {person}')
    sha = sorted((unit / '100600 SHA').glob('*.sha'))[0]
    streams = read_sha_streams(sha)
    inherited = next((v for data in streams.values()
                      if (v := declared_sheet_viewbox(data)) is not None), None)
    view = sheet_viewbox(sheet_streams(streams, {1})[0].data, inherited)
    with fitz.open(reference) as document:
        candidate = max(select_page(document, None)['RevisedBy'], key=lambda item: item.score)
        page = document[candidate.page_number - 1]
        width, height = page.rect.width, page.rect.height
    with fitz.open(reference) as document:
        source = Image.open(io.BytesIO(document.extract_image(candidate.xref)['image'])).convert('RGB')
        source.save(audit / 'HBY原始图像.png')
        image = signature_rgba(source, 2, upscale=12)
    if image.getchannel('A').getbbox() is None:
        raise RuntimeError('Extracted HBY signature is empty')
    image.save(out / 'HBY.png')
    profile['people']['HBY'] = {
        'file': 'HBY.png', 'field': 'RevisedBy', 'flip_180': True,
        'width': candidate.rect.width * image.width / (source.width * 12) * view[2] / width * .90,
        'height': candidate.rect.height * image.height / (source.height * 12) * view[3] / height * .90,
        'source_xref': candidate.xref, 'source_pixels': list(source.size),
    }
    profile['source_pdf_path'] = str(reference)
    profile['source_page'] = candidate.page_number
    profile['sha_viewbox_scaled'] = list(view)
    profile.pop('pending_signature_people', None)
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with fitz.open() as preview:
        page = preview.new_page(width=600, height=130)
        page.insert_text((15, 25), 'HBY')
        page.insert_image(fitz.Rect(150, 10, 500, 115), filename=str(out / 'HBY.png'), rotate=180)
        page.get_pixmap().save(audit / 'HBY签名检查.png')
    print(f'HBY: reference page {candidate.page_number}, xref {candidate.xref}, source {source.size}')


if __name__ == '__main__':
    main()
