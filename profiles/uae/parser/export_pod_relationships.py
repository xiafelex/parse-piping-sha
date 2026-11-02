"""Read-only UAE POD relationship audit; no inferred construction weld numbers.

Exports raw summary units and name evidence instead of silently normalizing them.
"""
from pathlib import Path
from collections import Counter, defaultdict
import csv
import json
import olefile
from pod_decode import decode

ROOT = Path(__file__).resolve().parents[4]
PROJECT = ROOT / '阿联酋项目'
OUT = PROJECT / 'POD关联深化核查'


def properties(c):
    return {p['name']: p for p in c['properties']}


def value(p, name):
    return p.get(name, {}).get('value')


def summary_properties(s):
    return {p['name']: p['default_or_value']['value'] for p in s['property_declarations']
            if p.get('default_or_value', {}).get('present')}


def write_csv(name, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / (name + '.csv')).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    OUT.mkdir(exist_ok=True)
    tables = defaultdict(list)
    files = sorted(p for d in PROJECT.glob('30201*') if d.is_dir() for p in d.rglob('*') if p.suffix.lower() == '.pod')
    for index, path in enumerate(files):
        base = path.name.removesuffix('-pod.pod')
        source = str(path.relative_to(ROOT))
        try:
            with olefile.OleFileIO(path) as ole:
                model = decode(ole.openstream('PODFile').read())
        except Exception as exc:
            tables['解析异常'].append({'文件': source, '错误': str(exc)})
            continue
        cm = {c['object_guid']: c for c in model['components']}
        materials = {s['header_strings'][0]['value']: s for s in model['materials'] if s['header_strings']}
        cuts = defaultdict(list)
        for c in model['components']:
            p = properties(c)
            seq = value(p, 'CUT-PIECE-SEQUENCE-NUMBER')
            if seq is not None:
                cuts[str(seq)].append(c)
        stats = Counter()
        summary_types = Counter()
        for s in model['summaries']:
            names = [x['value'] for x in s['header_strings']]
            kind = names[1] if len(names) > 1 else 'unknown'
            summary_types[kind] += 1
            props = summary_properties(s)
            if kind == 'Cut-Piece-Summary':
                candidates = cuts.get(str(props.get('SEQUENCE-NUMBER')), [])
                tables['下料段汇总'].append({'图号': base, '源文件': source, '偏移': s['offset'],
                    **props, '构件候选数_按序号': len(candidates),
                    '构件候选GUID': ';'.join(c['object_guid'] for c in candidates),
                    '构件CUT-PIECE-LENGTH原值': json.dumps([value(properties(c), 'CUT-PIECE-LENGTH') for c in candidates]),
                    '单位说明': 'LENGTH与构件CUT-PIECE-LENGTH保留不同原值，禁止直接等同'})
            elif kind == 'Spool-Summary':
                tables['预制段汇总'].append({'图号': base, '源文件': source, '偏移': s['offset'], **props,
                    '说明': '设计POD预制段；尚未证明等于施工图SP号；汇总数值保留原单位'})
        for c in model['components']:
            props = properties(c)
            material = materials.get(c['material_guid'])
            material_strings = [s['value'] for s in material['header_strings']] if material else []
            stats['material_resolved'] += bool(material)
            tables['构件材料及报告字段'].append({'图号': base, '源文件': source, '偏移': c['offset'],
                'GUID': c['object_guid'], 'UCI': c['uci'], '类型': c['type_name'],
                '材料GUID': c['material_guid'], '材料记录已关联': bool(material),
                '材料头字段_原始顺序': json.dumps(material_strings, ensure_ascii=False),
                '构件ITEM-DESCRIPTION': value(props, 'ITEM-DESCRIPTION'),
                '设计SPOOL-SEQUENCE-NUMBER': value(props, 'SPOOL-SEQUENCE-NUMBER'),
                'Spool字段名称依据': props.get('SPOOL-SEQUENCE-NUMBER', {}).get('name_evidence'),
                '下料序号': value(props, 'CUT-PIECE-SEQUENCE-NUMBER'),
                '下料长度原值': value(props, 'CUT-PIECE-LENGTH'),
                '报告片段31': value(props, 'COMPONENT-ATTRIBUTE31'),
                '报告片段32': value(props, 'COMPONENT-ATTRIBUTE32'),
                '报告片段33': value(props, 'COMPONENT-ATTRIBUTE33')})
            for p in c['ports']:
                peer = cm.get(p['peer_object_guid'])
                stats['ports'] += 1
                stats['nonempty_peers'] += bool(p['peer_object_guid'])
                stats['resolved_peers'] += bool(peer)
                reverse = [q for q in peer['ports'] if q['name'] == p['peer_port']] if peer else []
                reciprocal = any(q['peer_object_guid'] == c['object_guid'] and q['peer_port'] == p['name'] for q in reverse)
                stats['reciprocal_ports'] += reciprocal
                tables['端口连接'].append({'图号': base, '源文件': source, 'GUID': c['object_guid'],
                    'UCI': c['uci'], '端口': p['name'], '对端GUID': p['peer_object_guid'],
                    '对端口': p['peer_port'], '对端UCI': peer['uci'] if peer else '',
                    '对端类型': peer['type_name'] if peer else '', '双向连接验证': reciprocal,
                    '工程XYZ': json.dumps(p['xyz']), '图面XY原值': json.dumps(p['drawing_xy']),
                    '坡口代码': p['end_preparation']})
            if c['type_name'].endswith('-Weld') or 'WELD-TYPE' in props:
                sides = []
                for p in c['ports']:
                    peer = cm.get(p['peer_object_guid'])
                    sides.append({'port': p['name'], 'peer_port': p['peer_port'],
                        'peer_guid': p['peer_object_guid'], 'peer_uci': peer['uci'] if peer else None,
                        'peer_type': peer['type_name'] if peer else None})
                stats['welds'] += 1
                # Mitre welds carry an auxiliary Centre_1, not a third connection.
                connection_sides = [x for x in sides if not x['port'].startswith('Centre_')]
                stats['welds_both_sides'] += len(connection_sides) == 2 and all(x['peer_uci'] for x in connection_sides)
                tables['焊口两侧构件'].append({'图号': base, '源文件': source, '偏移': c['offset'],
                    'GUID': c['object_guid'], 'UCI': c['uci'], '设计类型': c['type_name'],
                    '设计焊接分类': value(props, 'WELD-ATTRIBUTE3'), '焊接形式': value(props, 'WELD-TYPE'),
                    'REPEAT-WELD-IDENTIFIER原值': value(props, 'REPEAT-WELD-IDENTIFIER'),
                    '设计Spool序号': value(props, 'SPOOL-SEQUENCE-NUMBER'),
                    '两侧连接': json.dumps(sides, ensure_ascii=False),
                    '施工焊口号': '', '说明': '原始编号值不自动等同施工编号'})
        tables['文件覆盖'].append({'图号': base, '源文件': source, '版本': model['version'],
            '构件数': len(cm), '材料记录数': len(materials), '解码错误数': len(model['decode_errors']),
            **dict(stats), '汇总类型计数': json.dumps(summary_types, ensure_ascii=False)})
        if (index + 1) % 20 == 0:
            print(f'processed {index + 1}/{len(files)}', flush=True)
    for name, rows in tables.items():
        write_csv(name, rows)
    result = {'input_pods': len(files), 'decoded_pods': len(tables['文件覆盖']),
        'errors': tables['解析异常'], 'table_rows': {k: len(v) for k, v in tables.items()},
        'totals': {key: sum(r.get(key, 0) for r in tables['文件覆盖']) for key in
            ('构件数', '解码错误数', 'material_resolved', 'ports', 'nonempty_peers', 'resolved_peers', 'reciprocal_ports', 'welds', 'welds_both_sides')}}
    (OUT / 'coverage.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in tables.items():
        if not rows:
            continue
        # Workbook focuses on five lines overlapping marked PDFs; CSVs cover all deliveries.
        rows = rows if name in ('文件覆盖', '解析异常') else [r for r in rows if r['图号'] in
            {'2-27-52-0605-CD-LAA', '2-27-52-1220-HCX-LAA', '2-27-52-1233-FL-LNA', '2-27-52-1296-HCX-LAA', '2-27-52-1296-HCX-LNA'}]
        if not rows:
            continue
        fields = list(dict.fromkeys(k for row in rows for k in row))
        ws = wb.create_sheet(name)
        ws.append(fields)
        for row in rows:
            ws.append([row.get(k) for k in fields])
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 24
    wb.save(OUT / '重点5套_加工及连接关联.xlsx')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
