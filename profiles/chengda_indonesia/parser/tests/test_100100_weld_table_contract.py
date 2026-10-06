from openpyxl import Workbook
from chengda_construction_welds import read_iso_review_weld_inputs


def test_mixed_scope_cross_sheet_and_future_cancellation(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['单页图号', '焊点编号', '焊点类型', '焊点位置',
                  'ISO图号', 'ISO焊口号', '对应状态', '核对备注', '核对备注', '描述1'])
    sheet.append(['A-01', 'F1', 'F', '1,2,3', 'A-02', 'S9', '不在对照范围', '', '没有ISO图纸', 'PIPE'])
    sheet.append(['B-01', 'F2', 'F', '4,5,6', 'B-03', 'F8', '已配对', '', '1', 'PIPE'])
    sheet.append(['C-01', 'F3', 'F', '7,8,9', 'C-01', 'F10A1', '仅EP3D侧', '', 'ISO图缺少焊点,升版需增口', 'PIPE'])
    sheet.append(['D-01', 'F4A1', 'F', '', 'D-01', 'S4', '仅ISO侧', '', '金童焊口升版后需取消', ''])
    sheet.append(['E-01', 'F5', 'F', '10,11,12', 'E-01', 'S5', '已配对', '', '', 'STUB END SP'])
    path = tmp_path / '100100-review.xlsx'
    workbook.save(path)
    workbook.close()
    records, skipped = read_iso_review_weld_inputs(path)
    assert [(r.sheet_id, r.weld_number) for r in records] == [('A-01', 'F1'), ('B-03', 'F8'), ('C-01', 'F10A1')]
    assert records[1].source_sheet_id == 'B-01'
    assert [(r.weld_number, r.manual_review_required) for r in skipped] == [('S4', True), ('S5', False)]
