"""Compare physical Sheet6 matching with template-owned geometry matching."""
from pathlib import Path
import argparse
import json
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'profiles' / 'chengda_indonesia' / 'parser'))
from chengda_paths import PROJECT_DIR as PROJECT, PROGRAM_DIR, SHARED_DIR
sys.path[:0] = [str(PROGRAM_DIR), str(SHARED_DIR)]
UNIT = PROJECT / 'SHA文件转换' / '400101'
WORK = UNIT / '管子分段焊口适配验证'
import convert_chengda_weld_sha_to_pdf as converter
import chengda_weld_mapping as mapping
import number_pcf_welds as numbering
import audit_pcf_sha_weld_topology as topology
import inject_sha_weld_callouts as injection
import sha_to_svg_prototype as renderer
from chengda_template_owned_page import is_template_owned_page
from chengda_construction_welds import read_iso_review_weld_inputs


def probe_sheet_streams(streams, wanted):
    targets = injection.sheet_streams(streams, wanted)
    name, data = renderer.discover_template_stream(streams)
    return [injection.SheetTarget(t.stream_name, t.page, data)
            if is_template_owned_page(streams, t.stream_name, name) else t
            for t in targets]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe-adapter', action='store_true')
    parser.add_argument('--include', default='40-42-0101-26-40EC40001-25')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()
    if args.probe_adapter:
        mapping.sheet_streams = numbering.sheet_streams = topology.sheet_streams = probe_sheet_streams
    records, skipped = read_iso_review_weld_inputs(next(UNIT.glob('焊口*.xlsx')))
    sheet_ids = [p.stem for p in sorted((UNIT/'400101 SHA').glob('*.sha'))] if args.all else args.include.split(',')
    comparison = []
    for sheet_id in sheet_ids:
        job = converter.WeldPageJob(sheet_id, UNIT/'400101 SHA'/f'{sheet_id}.sha',
                                    UNIT/'400101 0A'/f'{sheet_id}.pcf', 1, 'Sheet6')
        suffix = 'adapter' if args.probe_adapter else 'current'
        payload = converter.prepare_weld_payload(job, WORK/f'{sheet_id}-{suffix}.json',
                     [r for r in records if r.sheet_id == sheet_id], '100200',
                     [r for r in skipped if r.sheet_id == sheet_id and r.manual_review_required])
        (WORK/f'{sheet_id}-{suffix}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        if not args.quiet:
            print(sheet_id, suffix, json.dumps(payload['summary'],ensure_ascii=False))
            print('matched', [(r.get('weld_number'),r.get('mapping_method'),r.get('sha_x'),r.get('sha_y'))
                          for r in payload['welds'] if r.get('weld_number') and r.get('graphic_ref') is not None])
            print('unresolved',payload.get('construction_unresolved'))
        previous_path = UNIT/'水印及编号标注审计'/'pages'/f'{sheet_id}_1'/'weld-map.json'
        previous = json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else {}
        def matched_numbers(p):
            return {str(r['weld_number']) for r in p.get('welds', [])
                    if r.get('weld_number') and r.get('graphic_ref') is not None}
        before, after = matched_numbers(previous), matched_numbers(payload)
        unresolved = sorted({str(r['weld_number']) for r in payload.get('construction_unresolved', [])})
        comparison.append(dict(sheet_id=sheet_id, before_unmatched=previous.get('summary', {}).get('unmapped_numbered_count', 0),
            after_unmatched=payload['summary']['unmapped_numbered_count'], recovered=sorted(after-before),
            lost=sorted(before-after), unresolved=unresolved))
    if args.all:
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = '焊口匹配预检查'
        sheet.append(['单页图号','原未匹配数量','现未匹配数量','本次新增匹配','原已匹配现丢失','剩余未匹配'])
        for r in comparison:
            sheet.append([r['sheet_id'],r['before_unmatched'],r['after_unmatched'],
                          ', '.join(r['recovered']),', '.join(r['lost']),', '.join(r['unresolved'])])
        workbook.save(WORK/'全单元焊口匹配预检查.xlsx')
        (WORK/'匹配对比.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(page_count=len(comparison), before_unmatched=sum(r['before_unmatched'] for r in comparison),
              after_unmatched=sum(r['after_unmatched'] for r in comparison), recovered=sum(len(r['recovered']) for r in comparison),
              lost=sum(len(r['lost']) for r in comparison)),ensure_ascii=False))


if __name__ == '__main__':
    main()
