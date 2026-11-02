from openpyxl import Workbook
from chengda_construction_welds import read_iso_review_weld_inputs


def test_ep3d_alias_and_both_review_columns(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['单页图号', 'EP3D', '焊点类型', '焊点位置',
                  'ISO图号', 'ISO焊口号', '对应状态', '核对备注', '核对备注2'])
    sheet.append(['A', 'F1', 'F', '1,2,3', 'A', 'S1', '已配对', '', 'MC图纸缺少焊口'])
    sheet.append(['A', 'F2', 'F', '2,3,4', 'A', 'F2A1', '已配对', '', 'JT图纸缺少焊点'])
    sheet.append(['A', 'F3A1', 'F', '', 'A', 'F3', '仅ISO侧',
                  'ISO侧存在、EP3D焊口表无对应行，已自动加行', 'EP图缺少焊点'])
    sheet.append(['A', 'F4A1', 'F', '', 'A', 'F4', '仅ISO侧',
                  'ISO侧存在、EP3D焊口表无对应行，已自动加行',
                  'JT图纸与EP图纸不一致，EP图纸缺少管件，需要增加焊口'])
    sheet.append(['A', 'F5', 'F', '4,5,6', 'A', 'F5A1', '已配对', '', 'JT和EP图纸不一致'])
    sheet.append(['A', 'F6', 'F', '5,6,7', 'A', '', '仅EP3D侧', '', 'JT和EP图纸不一致'])
    sheet.append(['A', 'FS8', 'S', '6,7,8', 'A', 'F6A2', '仅EP3D侧', '', 'MC标号重复'])
    sheet.append(['A', 'F9', 'F', '7,8,9', 'A', '', '不在对照范围', '', ''])
    path = tmp_path / 'review.xlsx'
    book.save(path)
    book.close()
    records, skipped = read_iso_review_weld_inputs(path, remark_policy='100203')
    assert [r.weld_number for r in records] == ['S1', 'F2A1', 'F5A1', 'F6A2', 'F9']
    assert records[-2].original_weld_number == 'FS8'
    assert {r.weld_number for r in skipped} == {'F3', 'F4', 'F6'}
    assert all(r.manual_review_required for r in skipped)
    assert '审核备注：EP图缺少焊点' in skipped[0].reason
    assert 'EP图纸缺少管件' in skipped[1].reason


def test_canonical_original_number_wins_over_alias(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['单页图号', '焊点编号', 'EP3D', '焊点类型', '焊点位置',
                  'ISO图号', 'ISO焊口号', '对应状态', '核对备注2'])
    sheet.append(['A', 'F1', 'F99', 'F', '1,2,3', 'A', 'F2', '已配对', 'EP图缺少焊点'])
    sheet.append(['B', 'F3', 'F98', 'F', '4,5,6', 'B', 'F4', '已配对', ''])
    path = tmp_path / 'review.xlsx'
    book.save(path)
    book.close()
    records, skipped = read_iso_review_weld_inputs(path)
    assert len(records) == 1 and records[0].original_weld_number == 'F3'
    assert len(skipped) == 1 and skipped[0].manual_review_required
