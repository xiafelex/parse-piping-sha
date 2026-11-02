from openpyxl import Workbook
from chengda_construction_welds import read_iso_review_weld_inputs


def test_404043_explicit_ep_numbering_and_missing_side(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['单页图号', '焊点编号', '焊点类型', '焊点位置',
                  'ISO图号', 'ISO焊口号', '对应状态', '核对备注'])
    notes = ['ISO图未标注焊点，以EP为准', '图纸升版，以EP为准',
             '没有ISO图，以EP焊口为准', 'ISO图未标注焊点',
             'ISO图缺少焊点，EP图和ISO图不一致，需要升图',
             'EP图与ISO图不同，导致EP图焊点缺失', 'EP图缺少焊点']
    for i, note in enumerate(notes, 1):
        sheet.append(['EP-A', f'F{i}', 'F', '1,2,3' if i <= 5 else '',
                      'ISO-B' if i != 3 else '', f'F{i}A1', '已配对', note])
    path = tmp_path / 'review.xlsx'
    book.save(path)
    book.close()
    records, skipped = read_iso_review_weld_inputs(path, remark_policy='404043')
    assert [(r.sheet_id, r.weld_number) for r in records] == [
        ('EP-A', 'F1'), ('EP-A', 'F2'), ('EP-A', 'F3'), ('ISO-B', 'F4A1'), ('ISO-B', 'F5A1')]
    assert [r.number_source for r in records[:3]] == ['焊点编号'] * 3
    assert {r.weld_number for r in skipped} == {'F6A1', 'F7A1'}
    assert all(r.manual_review_required and '审核备注' in r.reason for r in skipped)
