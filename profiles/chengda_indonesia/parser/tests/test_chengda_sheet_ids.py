from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR
import sys
from decimal import Decimal

PROJECT_DIR = DATA_DIR
sys.path.insert(0, str(CODE_PROGRAM_DIR))
from chengda_sheet_ids import sheet_id_key
from chengda_construction_welds import (
    ConstructionWeldInput, construction_records_for_sheet, group_construction_by_sheet,
)


def test_separator_filename_encoding_matches_table_without_conflating_suffixes():
    assert sheet_id_key('LINE/P-01') == sheet_id_key('line^2FP-01')
    assert sheet_id_key('LINE/P-01') == sheet_id_key('LINE^2fP-01')
    assert sheet_id_key('LINE^2FPG-01') != sheet_id_key('LINE/P-01')
    assert sheet_id_key('LINE^P-01') != sheet_id_key('LINE/P-01')


def test_encoded_sheet_selects_weld_records_for_annotation_and_audit():
    row = ConstructionWeldInput(2, 'LINE/P-01', 'F1', 'F', (Decimal(1),)*3)
    grouped = group_construction_by_sheet([row])
    assert construction_records_for_sheet(grouped, 'LINE^2FP-01') == [row]
    assert construction_records_for_sheet(grouped, 'LINE^2FPG-01') == []
