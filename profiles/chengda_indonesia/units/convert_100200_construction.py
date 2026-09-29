#!/usr/bin/env python3
"""Frozen 100200 entry point: contractor construction-weld comparison."""

from __future__ import annotations

import sys
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from convert_chengda_weld_sha_to_pdf import main


if __name__ == "__main__":
    if "--profile" in sys.argv[1:]:
        raise SystemExit("本入口的--profile已固化为100200")
    unit = DATA_DIR / "SHA文件转换" / "100200"
    defaults = [
        "--profile", "100200",
        "--sha-dir", str(unit / "SHA"),
        "--pcf-dir", str(unit / "PCF"),
        "--output-dir", str(unit / "100200_PDF-水印及编号标注"),
        "--audit-dir", str(unit / "水印及编号标注审计"),
        "--support-table", str(unit / "支架表.csv"),
        "--valve-table", str(unit / "阀门表.csv"),
        "--flange-table", str(unit / "法兰表.csv"),
        "--weld-table", str(unit / "焊口表-壁厚材质处理-带特性-汇总.xlsx"),
        "--weld-number-column", "施工焊口号",
    ]
    sys.argv[1:1] = defaults
    raise SystemExit(main())
