from decimal import Decimal

import pytest

from chengda_construction_welds import (
    ConstructionWeldInput, SkippedConstructionWeld,
    construction_records_for_sheet, group_construction_by_sheet,
    remap_weld_sheet_aliases,
)


def test_explicit_alias_merges_welds_and_retains_source():
    records = [ConstructionWeldInput(i, sheet, number, 'F', (Decimal(1),)*3)
               for i, sheet, number in [(2, 'LINE-01', 'F1'),
                                        (3, 'LINE-01B', 'F2'),
                                        (4, 'LINE-01C', 'F3')]]
    mapped = remap_weld_sheet_aliases(records, ['line-01b=LINE-01'])
    merged = construction_records_for_sheet(group_construction_by_sheet(mapped), 'LINE-01')
    assert [r.weld_number for r in merged] == ['F1', 'F2']
    assert mapped[1].source_sheet_id == 'LINE-01B'
    assert mapped[1].excel_row == 3
    assert mapped[2].sheet_id == 'LINE-01C'
    assert records[1].sheet_id == 'LINE-01B'
    skipped = remap_weld_sheet_aliases(
        [SkippedConstructionWeld(8, 'LINE-01B', 'F8', '需人工补标', True)],
        ['LINE-01B=LINE-01'],
    )
    assert skipped[0].sheet_id == 'LINE-01'
    assert skipped[0].manual_review_required


@pytest.mark.parametrize('aliases', [
    ['LINE-01B'], ['=LINE-01'], ['LINE-01B='],
    ['B=A', 'B=C'], ['C=B', 'B=A'], ['B=A', 'A=B'],
])
def test_ambiguous_aliases_are_rejected(aliases):
    with pytest.raises(ValueError):
        remap_weld_sheet_aliases([], aliases)
