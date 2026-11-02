from openpyxl import Workbook
from chengda_construction_welds import read_iso_review_weld_inputs


def test_201600_0b_scope_and_review_findings(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['单页图号', '焊点编号', '焊点类型', '焊点位置',
                  'ISO图号', 'ISO焊口号', '对应状态', '核对备注'])
    sheet.append(['A', 'F1', 'F', '1,2,3', '', '', '仅EP3D侧', 'ISO无图'])
    sheet.append(['A', 'FS2', 'S', '2,3,4', 'A', 'F1A1', '仅EP3D侧', 'ISO未标注'])
    sheet.append(['A', 'F3', 'F', '3,4,5', 'A', 'F2A1', '已配对',
                  'EP图存在，ISO与EP图不一致，无此焊点'])
    sheet.append(['A', 'F4', 'F', '4,5,6', 'A', 'F118A1', '已配对', '标号重复，两个118'])
    for i, note in enumerate(['ISO侧存在、EP3D未标注', 'ISO侧存在、EP3D无此点',
                              'ISO侧存在、EP3D无图', 'ISO侧存在，EP图无此支管'], 5):
        sheet.append(['A', f'F{i}A1', 'F', '', 'A', f'F{i}', '仅ISO侧', note])
    sheet.append(['A', '', 'F', '', 'A', 'F9', '仅ISO侧', '识别错误,需删除'])
    sheet.append(['A', 'F10A1', 'F', '', 'A', 'FS152', '仅ISO侧', 'ISO侧标注错误，需删除'])
    path = tmp_path / '201600-0b.xlsx'
    book.save(path)
    book.close()
    records, skipped = read_iso_review_weld_inputs(path, remark_policy='201600')
    assert [(r.weld_number, r.number_source) for r in records] == [
        ('F1', '焊点编号'), ('F1A1', 'ISO焊口号'), ('F2A1', 'ISO焊口号'), ('F118A1', 'ISO焊口号')]
    assert {r.weld_number for r in skipped if r.manual_review_required} == {'F5', 'F6', 'F7', 'F8'}
    assert {r.weld_number for r in skipped if not r.manual_review_required} == {'F9', 'FS152'}
    assert all('审核备注' in r.reason for r in skipped)
