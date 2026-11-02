from openpyxl import load_workbook
from convert_chengda_weld_sha_to_pdf import write_unmatched_number_xlsx
from merge_repair_audit import read_issues_without_jobs, repaired_row
import json


def test_no_sha_target_is_written_without_page_worker(tmp_path):
    issue = {'sheet_id': 'ISO-01', 'page': 1, 'kind': '焊口', 'number': 'F180',
             'reason': '审核目标图号无对应SHA文件，需人工核对图号及补标',
             'coordinate': '1, 2, 3', 'source_row': 5723}
    path = write_unmatched_number_xlsx(tmp_path, [], extra_issues=[issue, issue])
    book = load_workbook(path, read_only=True, data_only=True)
    rows = list(book['未标注编号'].values)
    book.close()
    assert len(rows) == 2
    assert rows[1] == ('ISO-01', 1, '焊口', 'F180', issue['reason'], '1, 2, 3', 5723)
    assert read_issues_without_jobs(tmp_path, [{'sheet_id': 'ISO-02', 'page': 1}]) == [issue]
    assert read_issues_without_jobs(tmp_path, [{'sheet_id': 'ISO-01', 'page': 1}]) == []


def test_svg_direct_weld_counts_survive_audit_reconstruction(tmp_path):
    page = tmp_path / 'page'
    page.mkdir()
    for filename, value in {
        'weld-map.json': {'summary': {'numbered_weld_count': 12}},
        'verification.json': {'callout_count': 0, 'unified_layout_counts': {'weld': 12}},
        'management-callouts.json': {'callouts': [{'kind': 'flange'}]},
    }.items():
        (page / filename).write_text(json.dumps(value), encoding='utf-8')
    (tmp_path / 'ISO-01_1.pdf').write_bytes(b'fixture')
    row = repaired_row(page, tmp_path, 'ISO-01', 1)
    assert row['callout_count'] == 12
    assert row['management_callout_count'] == 1
