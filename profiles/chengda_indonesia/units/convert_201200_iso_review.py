#!/usr/bin/env python3
"""Frozen 201200 entry point: ISO-review numbering and unit resources."""

from __future__ import annotations

import sys
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from convert_chengda_weld_sha_to_pdf import main


if __name__ == "__main__":
    unit = DATA_DIR / "SHA文件转换" / "201200"
    if "--profile" in sys.argv[1:] or "--weld-table-mode" in sys.argv[1:]:
        raise SystemExit("本入口已固化为201200 ISO审核对照规则")
    defaults = [
        "--profile", "100200",
        "--weld-table-mode", "iso-review",
        "--sha-dir", str(unit / "201200 SHA"),
        "--pcf-dir", str(unit / "201200 0 PCF"),
        "--output-dir", str(unit / "201200_PDF-水印及编号标注"),
        "--audit-dir", str(unit / "水印及编号标注审计"),
        "--signature-dir", str(unit / "签名资源"),
        "--support-table", str(unit / "支架表.csv"),
        "--valve-table", str(unit / "阀门表.csv"),
        "--flange-table", str(unit / "法兰表.csv"),
        "--weld-table", str(
            unit
            / "201200焊口表-壁厚材质处理-带特性_ISO对照结果_焊口号数字排序.xlsx"
        ),
        "--spool-number-column", "国内预制管段号",
    ]
    sys.argv[1:1] = defaults
    raise SystemExit(main())
