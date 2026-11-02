"""Inspect bounded PipeLine Info text fields without guessing binary properties."""
import re
import json
from collections import Counter
from compare_three_format_fields import PROJECT, OUT, pcf_read, save_csv
from analyze_iso_split import read_sha_streams


def main():
    OUT.mkdir(exist_ok=True)
    rows = []
    for pod in sorted(p for d in PROJECT.glob('30201*') for p in d.rglob('*-pod.pod')):
        base = pod.name.removesuffix('-pod.pod')
        pcf, sha = pod.with_name(base+'-pcf.pcf'), pod.with_name(base+'.sha')
        if not pcf.exists() or not sha.exists():
            continue
        blocks, materials, _ = pcf_read(pcf)
        by_uci = {}
        for b in blocks:
            for uci in b['attributes'].get('UCI', []):
                by_uci.setdefault(uci, []).append(b)
        data = read_sha_streams(sha).get('Unclustered Dynamic Attributes', b'')
        starts = [m.start()-5 for m in re.finditer(b'PipeLine Info\x00', data)]
        for start, end in zip(starts, starts[1:]+[len(data)]):
            rec = data[start:end]
            ucis = re.findall(rb'\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}', rec)
            if len(ucis) != 1:
                continue
            uci = ucis[0].decode('ascii')
            fly = ''
            left, right = rec.find(b'Fly Text\x00'), rec.find(b'Unique Component Identifier\x00')
            if 0 <= left < right:
                candidates = re.findall(rb'[ -~]{2,}', rec[left+len(b'Fly Text\x00'):right])
                if len(candidates) == 1:
                    fly = candidates[0].decode('ascii')
            source = by_uci.get(uci, [])
            codes = sorted({materials[mid]['ITEM-CODE'] for b in source
                for mid in b['attributes'].get('MATERIAL-IDENTIFIER', [])
                if mid in materials and 'ITEM-CODE' in materials[mid]})
            keys = sorted({s for b in source for s in b['attributes'].get('SKEY', [])})
            rows.append({'图号': base, 'UCI': uci, 'SHA动态记录偏移': start,
                'FlyText_边界内ASCII候选': fly, 'PCF同UCI构件数': len(source),
                'PCF材料代码': ';'.join(codes), 'PCF_SKEY': ';'.join(keys),
                'FlyText包含PCF材料代码': bool(codes) and any(code in fly for code in codes),
                '说明': '同UCI动态记录的显示文字；不等于完整工程属性表'})
    save_csv('SHA同UCI显示属性', rows)
    stats = {'records': len(rows), 'with_pcf_uci': sum(bool(r['PCF同UCI构件数']) for r in rows),
        'fly_text': sum(bool(r['FlyText_边界内ASCII候选']) for r in rows),
        'has_pcf_material_code': sum(bool(r['PCF材料代码']) for r in rows),
        'contains_pcf_material_code': sum(r['FlyText包含PCF材料代码'] for r in rows)}
    (OUT/'sha_component_summary.json').write_text(json.dumps(stats, indent=2), encoding='utf-8')
    print(stats)


if __name__ == '__main__':
    main()
