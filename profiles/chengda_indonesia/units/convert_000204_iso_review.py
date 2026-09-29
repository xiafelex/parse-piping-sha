#!/usr/bin/env python3
"""Frozen 000204 entry point: human-reviewed ISO numbering plus 0/0A assets."""

from __future__ import annotations

import sys
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from convert_chengda_weld_sha_to_pdf import main


if __name__ == "__main__":
    unit = DATA_DIR / "SHA文件转换" / "000204"
    if "--profile" in sys.argv[1:] or "--weld-table-mode" in sys.argv[1:]:
        raise SystemExit("本入口已固化为000204人工审核ISO焊口规则")
    defaults = [
        "--profile", "100200",
        "--weld-table-mode", "iso-review",
        "--sha-dir", str(unit / "000204 SHA"),
        "--pcf-dir", str(unit / "000204_0A"),
        "--output-dir", str(unit / "000204_PDF-水印及编号标注"),
        "--audit-dir", str(unit / "水印及编号标注审计"),
        "--signature-dir", str(unit / "签名资源"),
        "--support-table", str(unit / "支架表0.csv"),
        "--support-table", str(unit / "支架表0A.csv"),
        "--valve-table", str(unit / "阀门表0.csv"),
        "--valve-table", str(unit / "阀门表0A.csv"),
        "--flange-table", str(unit / "法兰表0.csv"),
        "--flange-table", str(unit / "法兰表0A.csv"),
        "--weld-table", str(
            unit / "焊口表-000204_0+0A_ISO对照结果-人工审核0929.xlsx"
        ),
    ]
    sys.argv[1:1] = defaults
    raise SystemExit(main())
