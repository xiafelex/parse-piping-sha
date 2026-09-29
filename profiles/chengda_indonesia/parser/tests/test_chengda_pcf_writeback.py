from __future__ import annotations

import sys
import hashlib
import subprocess
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR

from openpyxl import Workbook


PROJECT_DIR = DATA_DIR
PROGRAM_DIR = CODE_PROGRAM_DIR
if str(PROGRAM_DIR) not in sys.path:
    sys.path.insert(0, str(PROGRAM_DIR))


def write_weld_book(path: Path, number: str, weld_type: str, coordinate: str) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["单页图号", "焊点编号", "焊点类型", "焊点位置", "材料唯一码1", "材料唯一码2"])
    worksheet.append(["LINE-01", number, weld_type, coordinate, "{PIPE-A}", "{FLANGE-A}"])
    workbook.save(path)
    return path


def existing_weld_pcf() -> str:
    return """PIPELINE-REFERENCE   LINE-01
PIPE
    COMPONENT-IDENTIFIER    1
    END-POINT    0.000    0.000    0.000    4
    END-POINT    100.000    0.000    0.000    4
    SKEY    PIPE
    UCI    {PIPE-A}
    MATERIAL-IDENTIFIER 1
WELD
    COMPONENT-IDENTIFIER    2
    MASTER-COMPONENT-IDENTIFIER    1
    END-POINT    100.000    0.000    0.000    4
    END-POINT    101.000    0.000    0.000    4
    SKEY    WS
    CATEGORY    ERECTION
    REPEAT-WELD-IDENTIFIER    0
    WELD-ATTRIBUTE3    Field weld at job site, including offshore
    WELD-ATTRIBUTE4    By Pipe Erector
    UCI    {WELD-A}
FLANGE
    COMPONENT-IDENTIFIER    3
    END-POINT    101.000    0.000    0.000    4
    END-POINT    110.000    0.000    0.000    4
    SKEY    FLSO
    UCI    {FLANGE-A}
    MATERIAL-IDENTIFIER 2
MATERIALS
MATERIAL-IDENTIFIER 1
    DESCRIPTION PIPE
MATERIAL-IDENTIFIER 2
    DESCRIPTION FLANGE
"""


def pipe_only_pcf(with_flange: bool) -> str:
    flange = """
FLANGE
    COMPONENT-IDENTIFIER    2
    END-POINT    100.000    0.000    0.000    4
    END-POINT    110.000    0.000    0.000    4
    SKEY    FLSO
    UCI    {FLANGE-A}
    MATERIAL-IDENTIFIER 2
""" if with_flange else ""
    return f"""PIPELINE-REFERENCE   LINE-01
PIPE
    COMPONENT-IDENTIFIER    1
    END-POINT    0.000    0.000    0.000    4
    END-POINT    100.000    0.000    0.000    4
    SKEY    PIPE
    UCI    {{PIPE-A}}
    MATERIAL-IDENTIFIER 1
    CUT-PIECE-LENGTH    100
{flange}MATERIALS
MATERIAL-IDENTIFIER 1
    DESCRIPTION PIPE
MATERIAL-IDENTIFIER 2
    DESCRIPTION FLANGE
"""


def test_updates_existing_weld_with_original_identifier_and_type(tmp_path: Path) -> None:
    from write_chengda_welds_to_pcf import writeback_tree

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    source = input_dir / "LINE-01.pcf"
    source.write_text(existing_weld_pcf(), encoding="ascii")
    before = source.read_bytes()
    excel = write_weld_book(tmp_path / "welds.xlsx", "FS1", "S", "100,0,0")

    summary = writeback_tree(excel, input_dir, tmp_path / "output", tmp_path / "audit")

    text = (tmp_path / "output" / "LINE-01.pcf").read_text(encoding="ascii")
    assert "REPEAT-WELD-IDENTIFIER    FS1" in text
    assert "SKEY    WW" in text
    assert "CATEGORY    FABRICATION" in text
    assert "WELD-ATTRIBUTE3    Shop weld" in text
    assert "WELD-ATTRIBUTE4    By Pipe Fabricator" in text
    assert summary.written == 1
    assert summary.unmatched == 0
    assert source.read_bytes() == before


def test_creates_missing_endpoint_weld(tmp_path: Path) -> None:
    from write_chengda_welds_to_pcf import writeback_tree

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "LINE-01.pcf").write_text(pipe_only_pcf(with_flange=True), encoding="ascii")
    excel = write_weld_book(tmp_path / "welds.xlsx", "F2", "F", "100,0,0")

    summary = writeback_tree(excel, input_dir, tmp_path / "output", tmp_path / "audit")

    text = (tmp_path / "output" / "LINE-01.pcf").read_text(encoding="ascii")
    assert summary.created_endpoint_welds == 1
    assert summary.written == 1
    assert "REPEAT-WELD-IDENTIFIER    F2" in text
    assert "WELD-ATTRIBUTE91    COMPONENT-ENDPOINT" in text


def test_splits_pipe_at_interior_weld_and_preserves_span(tmp_path: Path) -> None:
    from write_chengda_welds_to_pcf import writeback_tree

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "LINE-01.pcf").write_text(pipe_only_pcf(with_flange=False), encoding="ascii")
    excel = write_weld_book(tmp_path / "welds.xlsx", "T66", "F", "50,0,0")

    summary = writeback_tree(excel, input_dir, tmp_path / "output", tmp_path / "audit")

    text = (tmp_path / "output" / "LINE-01.pcf").read_text(encoding="ascii")
    assert summary.split_pipe_welds == 1
    assert summary.written == 1
    assert text.splitlines().count("PIPE") == 2
    assert "END-POINT    50.000" in text
    assert "REPEAT-WELD-IDENTIFIER    T66" in text
    assert "WELD-ATTRIBUTE91    PIPE-INTERPOLATION" in text


def test_repeated_writeback_is_deterministic_and_writes_both_audits(tmp_path: Path) -> None:
    from write_chengda_welds_to_pcf import writeback_tree

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    source = input_dir / "LINE-01.pcf"
    source.write_text(pipe_only_pcf(with_flange=False), encoding="ascii")
    excel = write_weld_book(tmp_path / "welds.xlsx", "T66", "F", "50,0,0")
    output = tmp_path / "output"
    audit = tmp_path / "audit"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    writeback_tree(excel, input_dir, output, audit)
    first_hash = hashlib.sha256((output / "LINE-01.pcf").read_bytes()).hexdigest()
    writeback_tree(excel, input_dir, output, audit)

    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256((output / "LINE-01.pcf").read_bytes()).hexdigest() == first_hash
    assert (audit / "PCF反写明细.json").is_file()
    assert (audit / "PCF反写明细.xlsx").is_file()


def test_cli_runs_writeback_and_reports_json_summary(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "LINE-01.pcf").write_text(existing_weld_pcf(), encoding="ascii")
    excel = write_weld_book(tmp_path / "welds.xlsx", "F2", "F", "100,0,0")
    output = tmp_path / "output"
    audit = tmp_path / "audit"

    completed = subprocess.run(
        [
            sys.executable,
            str(PROGRAM_DIR / "write_chengda_welds_to_pcf.py"),
            "--excel", str(excel),
            "--input-dir", str(input_dir),
            "--output-dir", str(output),
            "--audit-dir", str(audit),
            "--number-column", "焊点编号",
            "--overwrite-output",
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"written": 1' in completed.stdout
    assert (output / "LINE-01.pcf").is_file()
