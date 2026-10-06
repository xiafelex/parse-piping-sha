"""Extract verified signature-only crops from the 301300 reference, including annotations."""
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
import sha_to_svg_prototype as api
from inject_sha_weld_callouts import sheet_streams


def main():
    reference = Path(r'D:\研发工作\Cursor\PDF轴测图图元研究\PDF-ISO\301300-WS\301300-水处理焊口图新版.pdf')
    unit = PROJECT_DIR / 'SHA文件转换' / '301300'
    output = unit / '签名资源'
    output.mkdir(exist_ok=True)
    streams = api.read_sha_streams(unit / '301300 SHA' / '30-80-1300-26-30AB13001A-01.sha')
    inherited = next((v for data in streams.values() if (v := api.declared_sheet_viewbox(data)) is not None), None)
    view = api.sheet_viewbox(sheet_streams(streams, {1})[0].data, inherited)
    profile = {'source_pdf_path': str(reference), 'source_page': 33, 'sha_viewbox_scaled': list(view), 'people': {}}
    with fitz.open(reference) as document:
        page = document[32]
        for person, field, left, right in [('LCG', 'RevisedBy', 480, 560), ('XJ', 'CheckedBy', 580, 635), ('CJ', 'ApprovedBy', 680, 730)]:
            rect = fitz.Rect(left, 953, right, 966)
            pix = page.get_pixmap(matrix=fitz.Matrix(12, 12), clip=rect, alpha=False)
            source = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
            image = signature_rgba(source, 2, upscale=1)
            image.save(output / f'{person}.png')
            profile['people'][person] = {
                'file': f'{person}.png', 'field': field, 'flip_180': True,
                'width': image.width / 12 * view[2] / page.rect.width * .90,
                'height': image.height / 12 * view[3] / page.rect.height * .90,
                'source_crop': list(rect),
            }
    (output / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
