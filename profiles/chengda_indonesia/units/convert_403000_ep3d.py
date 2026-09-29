#!/usr/bin/env python3
"""Frozen 403000 entry point: label every weld by its EP3D weld-point id."""

from __future__ import annotations

import sys
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from convert_chengda_weld_sha_to_pdf import main


if __name__ == "__main__":
    unit = DATA_DIR / "SHA文件转换" / "403000"
    if "--profile" in sys.argv[1:] or "--weld-table-mode" in sys.argv[1:]:
        raise SystemExit("本入口已固化为403000纯EP3D焊点编号规则")
    if "--weld-number-column" in sys.argv[1:]:
        raise SystemExit("403000焊口编号列已固化为“焊点编号”")
    defaults = [
        "--profile", "100200",
        "--weld-table-mode", "construction",
        "--weld-number-column", "焊点编号",
        "--sha-dir", str(unit / "403000 SHA"),
        "--pcf-dir", str(unit / "403000 0A"),
        "--output-dir", str(unit / "403000_PDF-水印及编号标注"),
        "--audit-dir", str(unit / "水印及编号标注审计"),
        "--support-table", str(unit / "支架表.csv"),
        "--valve-table", str(unit / "阀门表.csv"),
        "--flange-table", str(unit / "法兰表.csv"),
        "--weld-table", str(
            unit / "焊口表-403000 0A-壁厚材质处理-带特性.xlsx"
        ),
    ]
    sys.argv[1:1] = defaults
    raise SystemExit(main())
