from openpyxl import Workbook
from chengda_construction_welds import read_iso_review_weld_inputs


def test_100600_review_filtering_and_blank_tail(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['单页图号', '焊点编号', '焊点类型', '焊点位置',
                  'ISO图号', 'ISO焊口号', '对应状态', '核对备注'])
    notes = ['', 'EP与ISO图不同，需升版，无法核对',
             'EP与ISO图不同，需升版，ISO图焊点缺失',
             'EP图焊点缺失', '识别错误，需删除']
    for i, note in enumerate(notes, 1):
        sheet.append(['LINE', f'F{i}', 'F', '1,2,3', 'LINE', f'F{i}A1', '已配对', note])
    sheet.append(['LINE', 'F6', 'F', '4,5,6', 'LINE', 'F6A1', '不在对照范围', ''])
    sheet.append([None] * 8)
    path = tmp_path / 'review.xlsx'
    book.save(path)
    book.close()
    records, skipped = read_iso_review_weld_inputs(path, remark_policy='100600')
    assert [r.weld_number for r in records] == ['F1A1', 'F3A1', 'F6']
    assert {r.weld_number for r in skipped if r.manual_review_required} == {'F2A1', 'F4A1'}
    assert {r.weld_number for r in skipped if not r.manual_review_required} == {'F5A1'}
    assert all(r.sheet_id for r in skipped)
