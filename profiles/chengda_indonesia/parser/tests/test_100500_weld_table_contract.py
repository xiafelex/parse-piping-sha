from openpyxl import Workbook
from chengda_construction_welds import read_iso_review_weld_inputs


def test_100500_scope_and_review_notes(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['单页图号', '焊点编号', '焊点类型', '焊点位置',
                  'ISO图号', 'ISO焊口号', '对应状态', '核对备注'])
    sheet.append(['A', 'F1', 'F', '1,2,3', 'A', 'F9', '不在对照范围', ''])
    sheet.append(['B', 'F2', 'F', '4,5,6', 'B', 'F10', '已配对', ''])
    sheet.append(['C', 'F3', 'F', '7,8,9', 'C', 'F11A1', '仅EP3D侧', 'ISO图焊点缺失'])
    sheet.append(['D', 'F4', 'F', '10,11,12', 'D', 'F20', '已配对', 'EP图焊点缺失'])
    path = tmp_path / '100500.xlsx'
    book.save(path)
    book.close()
    records, skipped = read_iso_review_weld_inputs(path)
    assert [(r.sheet_id, r.weld_number) for r in records] == [('A', 'F1'), ('B', 'F10'), ('C', 'F11A1')]
    assert len(skipped) == 1
    assert skipped[0].weld_number == 'F20'
    assert skipped[0].manual_review_required
    assert 'EP图焊点缺失' in skipped[0].reason
