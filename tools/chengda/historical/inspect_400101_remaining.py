from pathlib import Path
import sys, json
import fitz

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'profiles' / 'chengda_indonesia' / 'parser'))
from chengda_paths import PROJECT_DIR
import sha_to_svg_prototype as api

UNIT = PROJECT_DIR / 'SHA文件转换' / '400101'
OUT = UNIT / '首条样本修复'
reference = next(UNIT.glob('CAA*.pdf'))
result = OUT / 'PDF' / '40-42-0101-26-40EC40001-25_1.pdf'
for label, path, index in [('reference', reference, 8), ('result', result, 0)]:
    with fitz.open(path) as doc:
        page = doc[index]
        print(label, page.rect)
        for trace in page.get_texttrace():
            text = ''.join(chr(c[0]) for c in trace['chars'])
            if ('40EC40001' in text or '0101001' in text or
                'Isometric drawing' in text or 'Contractor' in text or
                (text == '0' and 200 < trace['bbox'][1] < 900)):
                print(text, 'font', trace['font'], 'size', trace['size'],
                      'bbox', trace['bbox'], 'origins', [c[2] for c in trace['chars'][:3]])
        page.get_pixmap(matrix=fitz.Matrix(2,2), clip=fitz.Rect(1060,1055,1640,1180)).save(OUT / f'{label}-numbers.png')
        page.get_pixmap(matrix=fitz.Matrix(3,3), clip=fitz.Rect(435,435,485,480)).save(OUT / f'{label}-triangle.png')

streams = api.read_sha_streams(UNIT / '400101 SHA' / '40-42-0101-26-40EC40001-25.sha')
for name in ['Sheet6','Sheet221']:
    data = streams[name]
    for r in api.text_records(data):
        if '40EC40001' in r['text'] or r['text'] == '0':
            print(name, r, 'bbox', api.psm_bbox(streams['PSMcluster0'], r['graphic_ref']))
    for r in api.template_bound_text(data, streams, streams['PSMcluster0']):
        if '40EC40001' in r['text']:
            print('bound', name, r)
    for r in api.template_text_records(data, streams['PSMcluster0'], streams['StyleCluster']):
        if '40EC40001' in r['text'] or r['text'] == '0':
            print('template', name, r)
