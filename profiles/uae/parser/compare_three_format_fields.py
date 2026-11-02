"""Evidence-based PCF/POD field differences and SHA text availability.

SHA text occurrence is never treated as a same-component property binding.
"""
from pathlib import Path
from collections import defaultdict, Counter
from decimal import Decimal, InvalidOperation
import csv
import json
import re
import olefile
from pod_decode import decode
from analyze_iso_split import read_sha_streams
from sha_graphics import native_texts

ROOT = Path(__file__).resolve().parents[4]
PROJECT = ROOT / '阿联酋项目'
OUT = PROJECT / 'PCF_SHA_POD字段差异核查'


def pcf_read(path):
    blocks, materials, current = [], {}, None
    units = {}
    in_materials = False
    raw = path.read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    for number, line in enumerate(text.splitlines(), 1):
        if line == 'MATERIALS':
            in_materials = True
            current = None
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        key, val = parts[0], parts[1] if len(parts) > 1 else ''
        if key.startswith('UNITS-'):
            units[key] = val
        if in_materials:
            if key == 'MATERIAL-IDENTIFIER':
                current = materials.setdefault(val, {})
            elif current is not None:
                current[key] = val
        elif not line[0].isspace() and (len(parts) == 1 or key == 'PIPELINE-REFERENCE'):
            current = {'type': key, 'line': number, 'attributes': defaultdict(list)}
            blocks.append(current)
        elif current is not None:
            current['attributes'][key].append(val)
    return blocks, materials, units


def norm(v):
    return ' '.join(str(v).split())


def equal(a, b):
    if norm(a) == norm(b):
        return True
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return False


def save_csv(name, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / (name + '.csv')).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    OUT.mkdir(exist_ok=True)
    rows, coverage, titles, missing, reverse = [], [], [], [], []
    for pod in sorted(p for d in PROJECT.glob('30201*') for p in d.rglob('*-pod.pod')):
        base = pod.name.removesuffix('-pod.pod')
        pcf, sha = pod.with_name(base+'-pcf.pcf'), pod.with_name(base+'.sha')
        if not pcf.exists() or not sha.exists():
            missing.append({'POD': str(pod.relative_to(ROOT)), 'PCF存在': pcf.exists(), 'SHA存在': sha.exists()})
            continue
        with olefile.OleFileIO(pod) as ole:
            model = decode(ole.openstream('PODFile').read())
        blocks, materials, units = pcf_read(pcf)
        pipeline = next((b['attributes'] for b in blocks if b['type'] == 'PIPELINE-REFERENCE'), {})
        by_uci = defaultdict(list)
        for block in blocks:
            for uci in block['attributes'].get('UCI', []):
                by_uci[uci].append(block)
        streams = read_sha_streams(sha)
        texts = [(sheet, t) for sheet, data in streams.items() if re.fullmatch(r'Sheet\d+', sheet) for t in native_texts(data, None)]
        exact_texts = defaultdict(list)
        for sheet, t in texts:
            exact_texts[norm(t['text'])].append(f"{sheet}@{t['offset']}")
            if re.search(r'CUT[ -]*(?:LENGTH|PIECE)|END\s*PREP|BEVEL|SPOOL\s*(?:NO|NUMBER)', t['text'], re.I):
                titles.append({'图号': base, 'SHA': str(sha.relative_to(ROOT)), 'Sheet': sheet,
                    '偏移': t['offset'], '文本': t['text'], 'X': t['x'], 'Y': t['y']})
        raw = '\n'.join(m.group().decode('ascii') for b in streams.values() for m in re.finditer(rb'[ -~]{4,}', b))
        raw += '\n' + '\n'.join(m.group().decode('utf-16le') for b in streams.values() for m in re.finditer(rb'(?:[ -~]\x00){4,}', b))
        cut_markers = sorted(set(re.findall(r'CUT[ -]*(?:LENGTH|PIECE)|BEVEL|SPOOL\s*(?:NO|NUMBER)', raw, re.I)))
        counts = Counter()
        pod_ucis = {c['uci'] for c in model['components']}
        for c in model['components']:
            candidates = by_uci[c['uci']]
            podnames = {p['name'] for p in c['properties']}
            for prop in c['properties']:
                name, val = prop['name'], prop['value']
                direct = [(b['line'], v) for b in candidates for v in b['attributes'].get(name, [])]
                via = []
                if name == 'ITEM-DESCRIPTION':
                    via = [(b['line'], materials[mid]['DESCRIPTION']) for b in candidates
                        for mid in b['attributes'].get('MATERIAL-IDENTIFIER', [])
                        if mid in materials and 'DESCRIPTION' in materials[mid]]
                if name == 'CATEGORY':
                    via = [(b['line'], label) for b in candidates for key, label in
                        [('FABRICATION-ITEM', 'FABRICATION'), ('ERECTION-ITEM', 'ERECTION')]
                        if key in b['attributes']]
                if not direct and name in {'PIPING-SPEC','INSULATION-SPEC','TRACING-SPEC','PAINTING-SPEC','MISC-SPEC3'}:
                    via = [('pipeline', v) for v in pipeline.get(name, [])]
                values = direct or via
                if not candidates:
                    status = '无同UCI构件'
                elif not prop['present'] or val is None:
                    status = 'POD空值_不算增量'
                elif name.startswith('PROPERTY_') or 'candidate:' in prop['name_evidence']:
                    status = 'POD字段名待确认'
                elif values:
                    status = ('同值' if direct else 'PCF间接同值') if any(equal(val, v) for _, v in values) else '有字段但值不同'
                    if status == '有字段但值不同' and name == 'BOLT-DIA' and units.get('UNITS-BOLT-DIA') == 'INCH':
                        if any(abs(float(v)*25.4-float(val)) < 1e-7 for _,v in values):
                            status = '单位换算同值_INCH转mm'
                    if status == '有字段但值不同' and name == 'BOLT-LENGTH' and units.get('UNITS-BOLT-LENGTH') == 'MM':
                        if any(abs(float(v)-float(val)) <= .500001 for _,v in values):
                            status = '长度取整差异_不超过0.5mm'
                else:
                    status = 'PCF同构件无对应字段_待判定增量'
                counts[status] += 1
                sha_hits = exact_texts.get(norm(val), []) if val is not None and norm(val) else []
                rows.append({'图号': base, 'POD': str(pod.relative_to(ROOT)), 'UCI': c['uci'],
                    'POD对象GUID': c['object_guid'], '类型': c['type_name'], '字段': name,
                    '字段名依据': prop['name_evidence'], 'POD偏移': prop['offset'],
                    'POD值': val, 'PCF同UCI候选数': len(candidates),
                    'PCF候选值及行号': json.dumps(values, ensure_ascii=False), 'PCF比较': status,
                    'SHA整条文本等值候选': ';'.join(sha_hits[:8]),
                    'SHA结论': '仅文本等值_未绑定同一构件' if sha_hits else '当前文本解析未见等值_不证明二进制无该字段'})
            for name in sorted({k for b in candidates for k in b['attributes']} - podnames):
                reverse.append({'图号': base, 'UCI': c['uci'], 'POD对象GUID': c['object_guid'],
                    'PCF字段': name, '值': json.dumps([v for b in candidates for v in b['attributes'].get(name, [])], ensure_ascii=False),
                    '说明': 'POD构件属性列表未见同名；可能在端口/头部/材料记录，不等于POD缺失'})
        coverage.append({'图号': base, 'POD': str(pod.relative_to(ROOT)), '构件数': len(model['components']),
            'PCF_UCI数': len(by_uci), 'POD_UCI数': len(pod_ucis),
            'PCF未在POD中出现UCI数': len(set(by_uci)-pod_ucis),
            'SHA文本对象数': len(texts), 'SHA切管表关键词候选': ';'.join(cut_markers),
            'PCF块类型': json.dumps(Counter(b['type'] for b in blocks)), **dict(counts)})
        if len(coverage) % 20 == 0:
            print(f'compared {len(coverage)} triples', flush=True)
    aggregation = Counter((r['字段'], r['PCF比较']) for r in rows)
    summary = [{'字段': f, 'PCF比较': s, '属性记录数': n} for (f,s),n in sorted(aggregation.items())]
    for name, data in [('逐构件逐字段', rows), ('字段统计', summary), ('配套覆盖', coverage),
                       ('SHA切管表文本候选', titles), ('PCF反向字段检查', reverse), ('缺少配套', missing)]:
        save_csv(name, data)
    result = {'triples': len(coverage), 'missing': missing, 'property_records': len(rows),
        'status_counts': dict(Counter(r['PCF比较'] for r in rows)),
        'sha_cut_marker_files': sum(bool(r['SHA切管表关键词候选']) for r in coverage),
        'sha_cut_text_candidates': len(titles),
        'note': 'SHA文本等值不当作同构件字段证据；缺少文本不当作不存在。计数为文件内属性记录，非去重物理对象。'}
    (OUT/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    selected = {'CUT-PIECE-LENGTH','CUT-PIECE-SEQUENCE-NUMBER','SPOOL-SEQUENCE-NUMBER','ITEM-DESCRIPTION',
                'COMPONENT-ATTRIBUTE31','COMPONENT-ATTRIBUTE32','COMPONENT-ATTRIBUTE33','WELD-TYPE','REPEAT-WELD-IDENTIFIER'}
    for name, data in [('字段统计', summary), ('配套覆盖', coverage), ('SHA切管表文本候选', titles),
        ('0605关键字段实例', [r for r in rows if r['图号']=='2-27-52-0605-CD-LAA' and r['字段'] in selected]),
        ('值不同实例', [r for r in rows if r['PCF比较']=='有字段但值不同'][:3000])]:
        if not data:
            continue
        keys=list(dict.fromkeys(k for r in data for k in r))
        ws=wb.create_sheet(name);ws.append(keys)
        for r in data:ws.append([r.get(k) for k in keys])
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=26
    wb.save(OUT/'三文件差异摘要.xlsx')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
