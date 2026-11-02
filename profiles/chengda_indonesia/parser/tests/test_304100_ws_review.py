from openpyxl import Workbook

from chengda_construction_welds import read_iso_review_weld_inputs


def test_review_numbers_delete_rows_and_iso_only_missing_rows(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['单页图号', '焊点编号', '焊点类型', '焊点位置',
                  'ISO图号', 'ISO焊口号', '对应状态', '核对备注'])
    sheet.append(['LINE-01', 'T12', 'F', '1,2,3',
                  'LINE-01', 'F9', '已配对', ''])
    sheet.append(['LINE-01', '', 'F', '',
                  'LINE-01', 'F9', '仅ISO侧', '识别错误，需删除'])
    sheet.append(['LINE-01', 'F1A1', 'F', '',
                  'LINE-01', 'T2', '仅ISO侧', 'EP图焊点缺失，已添加'])
    sheet.append(['', '', 'F', '', 'LINE-01', 'T3', '仅ISO侧',
                  'ISO侧存在、EP3D焊口表无对应行，已自动加行'])
    path = tmp_path / 'welds.xlsx'
    workbook.save(path)
    workbook.close()

    records, skipped = read_iso_review_weld_inputs(path, remark_policy='304100-WS')
    assert [(r.original_weld_number, r.weld_number) for r in records] == [('T12', 'F9')]
    assert [(r.weld_number, r.manual_review_required) for r in skipped] == [
        ('F9', False), ('T2', True), ('T3', True),
    ]
