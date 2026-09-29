#!/usr/bin/env python3
"""Frozen 000202 entry point: checked construction welds plus 0A/0B assets."""

from __future__ import annotations

import sys
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from convert_chengda_weld_sha_to_pdf import main


if __name__ == "__main__":
    unit = DATA_DIR / "SHA文件转换" / "000202"
    if "--profile" in sys.argv[1:] or "--weld-table-mode" in sys.argv[1:]:
        raise SystemExit("本入口已固化为000202人工核对施工焊口规则")
    defaults = [
        "--profile", "100200",
        "--weld-table-mode", "iso-review",
        "--sha-dir", str(unit / "000202 SHA"),
        "--pcf-dir", str(unit / "000202_0B"),
        "--output-dir", str(unit / "000202_PDF-水印及编号标注"),
        "--audit-dir", str(unit / "水印及编号标注审计"),
        "--support-table", str(unit / "支架表0A.csv"),
        "--support-table", str(unit / "支架表0B.csv"),
        "--valve-table", str(unit / "阀门表0A.csv"),
        "--valve-table", str(unit / "阀门表0B.csv"),
        "--flange-table", str(unit / "法兰表0A.csv"),
        "--flange-table", str(unit / "法兰表0B.csv"),
        "--weld-table", str(
            unit / "000202焊口表-ISO对照结果0920-人工核对0925.xlsx"
        ),
    ]
    sys.argv[1:1] = defaults
    raise SystemExit(main())
