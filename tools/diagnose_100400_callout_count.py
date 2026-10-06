"""Reproduce one page's binary callout QA in an isolated diagnostics folder."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiles/chengda_indonesia/workflow'))
import convert_chengda_weld_sha_to_pdf as api


def main():
    unit = api.PROJECT_DIR / 'SHA文件转换/100400'
    name = '10-42-0400-26-10CHWR04002-01'
    work = unit / '问题定位' / name
    work.mkdir(parents=True, exist_ok=True)
    job = api.discover_weld_jobs(unit/'100400 SHA', unit/'100400 0B', {name.casefold()})[0]
    records, _ = api.read_iso_review_weld_inputs(next(unit.glob('焊口表*.xlsx')))
    records = api.construction_records_for_sheet(api.group_construction_by_sheet(records), name)
    mapping = work/'weld-map.json'
    payload = api.prepare_weld_payload(job, mapping, records, '100200', None)
    expected = [r for r in payload['welds'] if r.get('weld_number') and r.get('graphic_ref') is not None and r.get('page') == job.page]
    print('EXPECTED', [(r['weld_number'], r['graphic_ref'], r.get('sha_x'), r.get('sha_y')) for r in expected], flush=True)
    annotated = work/'annotated.sha'
    api.run_weld_injection_with_safe_retry([
        sys.executable, str(api.PARSER_DIR/'inject_sha_weld_callouts.py'), str(job.sha_path),
        '--output', str(annotated), '--pages', str(job.page), '--weld-map', str(mapping),
        '--distance-threshold', '80.0', '--diamond-radius', '95',
    ])
    api.run_checked([sys.executable, str(api.PARSER_DIR/'verify_sha_weld_callouts.py'),
                     str(job.sha_path), str(annotated), str(mapping), '--output', str(work/'verification.json')], api.PARSER_DIR)
    qa = json.loads((work/'verification.json').read_text(encoding='utf-8'))
    print(json.dumps(qa, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
