"""Rebuild success metrics from native/SVG page audits without rendering PDFs."""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiles/chengda_indonesia/units'))
import _bootstrap
from merge_repair_audit import repaired_row, read_issues_without_jobs, read_missing_pcf_ids
from convert_chengda_weld_sha_to_pdf import write_manual_review_log, write_unmatched_number_xlsx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    audit, output = args.audit_dir.resolve(), args.output_dir.resolve()
    summary_path = audit / 'PDF标注汇总.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    extra = read_issues_without_jobs(audit, summary['rows'])
    rows = [repaired_row(audit / 'pages' / f'{row["sheet_id"]}_{row["page"]}', output,
                         str(row['sheet_id']), int(row['page']))
            if row['status'] == 'success' else row for row in summary['rows']]
    backup = audit / ('汇总重建前备份_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    backup.mkdir()
    for name in ('PDF标注汇总.json', '未标注编号人工补标清单.xlsx', 'SHA转换人工审核日志.txt'):
        p = audit / name
        if p.is_file():
            shutil.copy2(p, backup / name)
    summary['rows'] = rows
    for key in ('callout_count', 'management_callout_count', 'spool_callout_count',
                'numbered_weld_count', 'unmapped_numbered_count', 'ambiguous_count'):
        summary[key] = sum(int(row.get(key, 0)) for row in rows)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    write_manual_review_log(audit, rows)
    write_unmatched_number_xlsx(audit, rows, read_missing_pcf_ids(audit), extra_issues=extra)
    print(json.dumps({k: v for k, v in summary.items() if k != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
