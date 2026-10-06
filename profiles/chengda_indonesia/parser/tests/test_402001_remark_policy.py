from openpyxl import Workbook
from chengda_construction_welds import read_iso_review_weld_inputs


def test_402001_remark_priority(tmp_path):
    path = tmp_path / 'review.xlsx'
    book = Workbook()
    sheet = book.active
    sheet.append(['单页图号', '焊点编号', '焊点类型', '焊点位置',
                  'ISO图号', 'ISO焊口号', '对应状态', '核对备注'])
    notes = ['EP图焊点缺失', 'EP与ISO图不同，无法核对，需升版,',
             'EP与ISO图不同，需升版', 'EP与ISO图不同，需升版,EP焊点缺失',
             'EP图与ISO图均无焊点，但ISO标注错误，需删除',
             'EP与ISO图不同，需升版,ISO焊点缺失', 'ISO图焊点缺失',
             'ISO图漏标', '', 'ISO图比EP图多一个直管，EP无此焊点',
             '与ISO图F1为同一点，ISO侧重复标注']
    for i, note in enumerate(notes, 1):
        sheet.append(['LINE', f'F{i}', 'F', '1,2,3', 'LINE', f'F{i}', '已配对', note])
    book.save(path)
    records, skipped = read_iso_review_weld_inputs(path, remark_policy='402001')
    assert [r.weld_number for r in records] == ['F6', 'F7', 'F8', 'F9']
    assert {s.weld_number for s in skipped if s.manual_review_required} == {'F1', 'F2', 'F3', 'F4', 'F10'}
    assert {s.weld_number for s in skipped if not s.manual_review_required} == {'F5', 'F11'}


def test_default_policy_still_accepts_revision_only_note(tmp_path):
    path = tmp_path / 'review.xlsx'
    book = Workbook()
    book.active.append(['单页图号', '焊点编号', '焊点类型', '焊点位置',
                        'ISO图号', 'ISO焊口号', '对应状态', '核对备注'])
    book.active.append(['LINE', 'F1', 'F', '1,2,3', 'LINE', 'F1', '已配对', 'EP与ISO图不同，需升版'])
    book.save(path)
    records, skipped = read_iso_review_weld_inputs(path)
    assert len(records) == 1 and not skipped
