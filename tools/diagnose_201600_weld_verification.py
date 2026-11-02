"""Persist the two failed 201600 weld-injection verification cases."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiles/chengda_indonesia/units'))
import _bootstrap
from convert_chengda_weld_sha_to_pdf import (
    PARSER_DIR, discover_weld_jobs, prepare_weld_payload,
    run_weld_injection_with_safe_retry, run_checked,
)
from chengda_construction_welds import read_iso_review_weld_inputs, group_construction_by_sheet, construction_records_for_sheet


def main():
    unit = _bootstrap.PROJECT_DIR / 'SHA文件转换/201600'
    records, _ = read_iso_review_weld_inputs(next(unit.glob('*.xlsx')), remark_policy='201600')
    grouped = group_construction_by_sheet(records)
    names = {'20-42-1600-26-SLL-9023-16', '20-42-1600-26-SLL-9023-26'}
    for job in discover_weld_jobs(unit / '201600 SHA', unit / '201600_0B', names):
        out = unit / '验证问题诊断' / job.sheet_id
        out.mkdir(parents=True, exist_ok=True)
        weld_map = out / 'weld-map.json'
        prepare_weld_payload(job, weld_map, construction_records_for_sheet(grouped, job.sheet_id), '100200', [])
        annotated = out / 'annotated.sha'
        mode = run_weld_injection_with_safe_retry([
            sys.executable, str(PARSER_DIR / 'inject_sha_weld_callouts.py'), str(job.sha_path),
            '--output', str(annotated), '--pages', str(job.page), '--weld-map', str(weld_map),
            '--distance-threshold', '80.0', '--diamond-radius', '95',
        ])
        verification = out / 'verification.json'
        run_checked([sys.executable, str(PARSER_DIR / 'verify_sha_weld_callouts.py'),
                     str(job.sha_path), str(annotated), str(weld_map), '--output', str(verification)], PARSER_DIR)
        qa = json.loads(verification.read_text(encoding='utf-8'))
        print(job.sheet_id, mode, qa['typed_marker_structurally_verified'], qa['callout_count'], flush=True)
        for item in qa['callouts']:
            failed = {k: v for k, v in item.items() if v is False}
            if failed:
                print(item['label'], failed, json.dumps(item, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
