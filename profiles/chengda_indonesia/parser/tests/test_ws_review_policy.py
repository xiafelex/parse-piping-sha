from openpyxl import Workbook
from chengda_construction_welds import read_iso_review_weld_inputs


def test_ws_uses_only_iso_column_and_reports_unreviewed_rows(tmp_path):
    w = Workbook()
    s = w.active
    s.append(['单页图号', '焊点编号', '焊点类型', '焊点位置', 'ISO图号',
              'ISO焊口号', '对应状态', '核对备注', '施工焊口号人工核对'])
    s.append(['EP-01', 'F1', 'F', '1,2,3', 'ISO-01', 'F9', '已配对', '', 'F99'])
    s.append(['EP-01', 'F2', 'F', '2,3,4', '', '', '暂不对照', '', ''])
    s.append(['EP-01', 'F3', 'F', '3,4,5', 'ISO-01', 'F8', '不在对照范围', '', ''])
    s.append(['EP-01', 'F4', 'F', '', 'ISO-01', 'F7', '仅ISO侧', 'EP图焊点缺失', ''])
    p = tmp_path / 'review.xlsx'
    w.save(p)
    w.close()
    records, skipped = read_iso_review_weld_inputs(p, remark_policy='ws-iso-review')
    assert [(r.sheet_id, r.weld_number) for r in records] == [('ISO-01', 'F9'), ('ISO-01', 'F8')]
    assert [(r.weld_number, r.manual_review_required) for r in skipped] == [('F2', True), ('F7', True)]
    assert 'ISO焊口号为空' in skipped[0].reason
    assert records[0].number_source == 'ISO焊口号'


def test_302100_fallback_only_for_confirmed_no_reference_rows(tmp_path):
    w = Workbook()
    s = w.active
    s.append(['单页图号', '焊点编号', '焊点类型', '焊点位置', 'ISO图号',
              'ISO焊口号', '对应状态', '核对备注'])
    s.append(['EP-01', 'F1', 'F', '1,2,3', '', '', '暂不对照', ''])
    s.append(['EP-01', 'F2', 'F', '2,3,4', '', '', '仅EP3D侧', ''])
    s.append(['EP-01', 'F3', 'F', '3,4,5', 'ISO-01', 'F8', '暂不对照', ''])
    s.append(['EP-01', 'F4', 'F', '', '', '', '暂不对照', ''])
    s.append(['EP-01', 'F5', 'F', '4,5,6', '', '', '暂不对照', 'EP图焊点缺失'])
    p = tmp_path / 'review.xlsx'
    w.save(p)
    w.close()
    records, skipped = read_iso_review_weld_inputs(p, remark_policy='ws-iso-review-302100')
    assert [(r.sheet_id, r.weld_number, r.number_source) for r in records] == [
        ('EP-01', 'F1', '焊点编号'), ('ISO-01', 'F8', 'ISO焊口号'),
    ]
    assert [r.weld_number for r in skipped] == ['F2', 'F4', 'F5']
    assert all(r.manual_review_required for r in skipped)
    regular, _ = read_iso_review_weld_inputs(p, remark_policy='ws-iso-review')
    assert [r.weld_number for r in regular] == ['F8']
