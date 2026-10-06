"""Rerun an ISO-review unit's missing-PCF list and merge successful page audits."""
import argparse
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiles/chengda_indonesia/units'))
from _bootstrap import PROJECT_DIR
from merge_repair_audit import read_missing_pcf_ids
from convert_chengda_weld_sha_to_pdf import discover_weld_jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    if not args.unit.isdigit():
        parser.error('unit must be numeric')
    unit = PROJECT_DIR / 'SHA文件转换' / args.unit
    audit = unit / '水印及编号标注审计'
    ids = read_missing_pcf_ids(audit)
    if not ids:
        raise SystemExit('No missing-PCF entries to repair')
    missing = []
    jobs = discover_weld_jobs(unit / f'{args.unit} SHA', unit / f'{args.unit}-WS PCF', set(ids), missing)
    if missing or not jobs:
        raise RuntimeError(f'Preflight unresolved: {missing}')
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    repair = unit / f'文件名匹配修复审计_{stamp}'
    backup = repair / '正式清单备份'
    backup.mkdir(parents=True)
    for source in audit.iterdir():
        if source.is_file():
            shutil.copy2(source, backup / source.name)
    output = unit / f'{args.unit}_PDF-水印及编号标注'
    print(f'Repair {len(ids)} SHA / {len(jobs)} pages; audit={repair}', flush=True)
    result = subprocess.run([
        sys.executable, '-u', str(ROOT / f'profiles/chengda_indonesia/units/convert_{args.unit}_iso_review.py'),
        '--include', ','.join(ids), '--workers', str(args.workers), '--force',
        '--output-dir', str(output), '--audit-dir', str(repair),
    ], cwd=ROOT)
    if not (repair / 'PDF标注汇总.json').is_file():
        raise RuntimeError(f'Conversion terminated without summary: {result.returncode}')
    merged = subprocess.run([
        sys.executable, str(ROOT / 'profiles/chengda_indonesia/workflow/merge_repair_audit.py'),
        '--main-audit', str(audit), '--repair-audit', str(repair), '--output-dir', str(output),
        '--replace-all-repair-pages',
    ], cwd=ROOT)
    return result.returncode or merged.returncode


if __name__ == '__main__':
    raise SystemExit(main())
