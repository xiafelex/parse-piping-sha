"""Audit delivered UAE weld evidence; keep design and fabrication IDs separate.

Run from any directory. Outputs only to 阿联酋项目/焊口联动核查.
Geometric SHA matches are candidates, never an invented weld-number mapping.
"""
from pathlib import Path
import collections
import csv
import json
import math
import re
import olefile

from cross_format import read_pcf, line_key
from pod_decode import decode
from sha_native import inspect
from analyze_iso_split import read_sha_streams, forward_owned_dynamic_graphics, pages_for_graphic

BASE = re.compile(r'([24]-\d+-52-\d+-[A-Z0-9]+-[A-Z0-9]+)')
LABEL = re.compile(r'(?<![A-Za-z0-9-])(?:FW|S)-\d{2,}(?![A-Za-z0-9-])')
ROOT = Path(__file__).resolve().parents[4]
PROJECT = ROOT / '阿联酋项目'
OUT = PROJECT / '焊口联动核查'


def strings(data):
    return '\n'.join([m.group().decode('ascii') for m in re.finditer(rb'[ -~]{4,}', data)] +
                     [m.group().decode('utf-16le') for m in re.finditer(rb'(?:[ -~]\x00){4,}', data)])


def csv_write(name, rows):
    if not rows:
        return
    with (OUT / name).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    OUT.mkdir(exist_ok=True)
    marked = [p for d in ('2-27', '4-213') for p in (PROJECT / d).rglob('*.pdf')]
    native = [p for d in PROJECT.glob('30201*') if d.is_dir() for p in d.rglob('*') if p.is_file()]
    def group(paths):
        result = collections.defaultdict(list)
        for p in paths:
            m = BASE.match(p.name)
            if m:
                result[m[1]].append(p)
        return result
    mg, ng = group(marked), group(native)
    summary, welds, scans = [], [], []
    for base in sorted(mg.keys() & ng.keys()):
        files = ng[base]
        pcf = next((p for p in files if p.suffix.lower() == '.pcf'), None)
        pod = next((p for p in files if p.suffix.lower() == '.pod'), None)
        sha = next((p for p in files if p.name == base + '.sha'), None)
        if not (pcf and pod and sha):
            continue
        with olefile.OleFileIO(pod) as ole:
            model = decode(ole.openstream('PODFile').read())
        sha_model = inspect(sha)
        streams = read_sha_streams(sha)
        bindings = forward_owned_dynamic_graphics(streams.get('Unclustered Dynamic Attributes', b''))
        sheet_streams = {k: v for k, v in streams.items() if re.fullmatch(r'Sheet\d+', k) and k != 'Sheet39817'}
        by_uci = collections.defaultdict(list)
        for c in model['components']:
            by_uci[c['uci']].append(c)
        groups = {g['offset']: g for g in model['graphics']}
        sha_lines = collections.defaultdict(list)
        for sheet, info in sha_model['sheets'].items():
            if sheet == 'Sheet39817':
                continue
            for line in info['lines']:
                sha_lines[line_key([v * 1e6 for v in line['xy_metres']])].append((sheet, line['offset']))
        blocks = [b for b in read_pcf(pcf) if b['type'] == 'WELD']
        start = len(welds)
        for b in blocks:
            a = b['attributes']
            candidates = by_uci[a.get('UCI')]
            ports = [p for c in candidates for p in c['ports']]
            points = sorted({tuple(p['drawing_xy']) for p in ports})
            matching = []
            for c in candidates:
                for off in c['drawing_group_offsets']:
                    for primitive in groups[off]['primitives']:
                        if primitive['kind'] == 'line':
                            matching.extend(sha_lines.get(line_key(primitive['xy']), []))
            sheets = sorted({s for s, _ in matching})
            refs = bindings.get(a.get('UCI'), [])
            ref_sheets = sorted({s for ref in refs for s in pages_for_graphic(ref['graphic_ref'], sheet_streams)})
            distance = max((min(math.dist(p['xyz'], q['xyz']) for q in b['points']) for p in ports), default=None)
            welds.append({'图号': base, 'PCF构件ID_非焊口号': a.get('COMPONENT-IDENTIFIER'),
                          'UCI': a.get('UCI'), '设计焊接分类': a.get('WELD-ATTRIBUTE3'),
                          '设计施工责任': a.get('WELD-ATTRIBUTE4'), '口径': a.get('WELD-ATTRIBUTE2'),
                          'PCF坐标mm': json.dumps(b['points'][0]['xyz']),
                          'POD同UCI对象数': len(candidates), '最大端口坐标偏差mm': distance,
                          'POD图面坐标_整数单位': json.dumps(points),
                          'SHA候选Sheet_按所属线段几何': ';'.join(sheets),
                          'SHA匹配线段数': len(set(matching)),
                          'SHA含相同UCI': a.get('UCI') in sha_model['uci_strings'],
                          'SHA动态graphic引用': ';'.join(str(ref['graphic_ref']) for ref in refs),
                          'SHA引用候选Sheet_需图元核对': ';'.join(ref_sheets),
                          '落图焊口号': '', '落图SF': '',
                          '关联状态': '设计焊口已关联POD；施工编号待图面定位核对',
                          'PCF文件': str(pcf.relative_to(ROOT)), 'POD文件': str(pod.relative_to(ROOT)),
                          'SHA文件': str(sha.relative_to(ROOT))})
        rows = welds[start:]
        summary.append({'图号': base, '落图PDF数': len(mg[base]), 'PCF焊口数': len(blocks),
                        'Shop数': sum(b['attributes'].get('WELD-ATTRIBUTE3') == 'Shop weld' for b in blocks),
                        'Field数': sum('Field' in b['attributes'].get('WELD-ATTRIBUTE3', '') for b in blocks),
                        'UCI关联POD数': sum(bool(r['POD同UCI对象数']) for r in rows),
                        'SHA所属线段找到候选数': sum(bool(r['SHA候选Sheet_按所属线段几何']) for r in rows),
                        'SHA同UCI数': sum(r['SHA含相同UCI'] for r in rows),
                        '配套扩展名': ','.join(sorted({p.suffix.lower() for p in files})),
                        '落图PDF': '\n'.join(str(p.relative_to(ROOT)) for p in mg[base])})
        for p in files:
            ext = p.suffix.lower()
            if ext == '.dwg':
                scans.append({'文件': str(p.relative_to(ROOT)), '扫描方式': '未解码DWG，不推断编号缺失', '标签候选': ''})
                continue
            if ext not in ('.pcf', '.txt', '.pod', '.sha'):
                continue
            text = '\n'.join(strings(b) for b in read_sha_streams(p).values()) if ext in ('.sha', '.pod') else strings(p.read_bytes())
            scans.append({'文件': str(p.relative_to(ROOT)), '扫描方式': 'ASCII/UTF16字符串；候选需排除材料牌号',
                          '标签候选': ';'.join(sorted(set(LABEL.findall(text))))})
    # Wider index/reference evidence does not imply same drawing geometry.
    refs = []
    import fitz
    import openpyxl
    for p in native:
        ext = p.suffix.lower()
        sections = []
        if ext == '.pdf':
            with fitz.open(p) as doc:
                sections = [(f'page {i+1}', page.get_text()) for i, page in enumerate(doc)]
        elif ext == '.csv':
            sections = [('csv', p.read_text(encoding='utf-8-sig', errors='replace'))]
        elif ext == '.xlsm':
            with_workbook = openpyxl.load_workbook(p, read_only=True, data_only=True)
            for s in with_workbook:
                sections.append((s.title, '\n'.join(' '.join(str(v) for v in row if v is not None) for row in s.iter_rows(values_only=True))))
            with_workbook.close()
        for section, content in sections:
            for base in sorted(set(BASE.findall(content)) & mg.keys()):
                pos = content.find(base)
                refs.append({'图号': base, '文件': str(p.relative_to(ROOT)), '位置': section,
                             '上下文': content[max(0, pos-70):pos+len(base)+90],
                             '证据范围': '文本提及/索引；不是完整配套或同图面确认'})
    csv_write('重叠管线汇总.csv', summary)
    csv_write('设计焊口关联明细.csv', welds)
    csv_write('配套焊口编号字符串核查.csv', scans)
    csv_write('索引及PDF文本提及.csv', refs)
    result = {'marked_count': len(marked), 'native_count': len(native), 'summary': summary,
              'welds': welds, 'string_scans': scans, 'reference_mentions': refs,
              'limitations': ['DWG未语义解码', '落图PDF未做全量OCR，施工编号未强行赋值',
                              '多Sheet重合线段只给候选，不凭二维坐标推断唯一页',
                              '字符串扫描不能证明未知二进制字段不存在编号']}
    (OUT / '焊口关联核查.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for title, rows in [('重叠管线', summary), ('设计焊口关联', welds), ('字符串核查', scans), ('索引及接续引用', refs)]:
        ws = workbook.create_sheet(title)
        if not rows:
            continue
        ws.append(list(rows[0]))
        for row in rows:
            ws.append(list(row.values()))
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = min(65, max(18, len(str(column[0].value)) * 2))
    workbook.save(OUT / '焊口联动核查.xlsx')
    print(json.dumps({'marked': len(marked), 'native': len(native), 'summary': summary,
                      'welds': len(welds), 'reference_mentions': len(refs)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
