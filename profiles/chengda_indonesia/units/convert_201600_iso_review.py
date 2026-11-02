#!/usr/bin/env python3
"""201600 0B PCF, reviewed weld table and management tables with existing SHA."""

from __future__ import annotations

import sys
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from convert_chengda_weld_sha_to_pdf import main


if __name__ == "__main__":
    unit = DATA_DIR / "SHA文件转换" / "201600"
    if any(arg.split('=', 1)[0] in ('--profile', '--weld-table-mode', '--iso-review-remark-policy') for arg in sys.argv[1:]):
        raise SystemExit("本入口已固化为201600 0B ISO审核对照规则")
    defaults = [
        "--profile", "100200",
        "--weld-table-mode", "iso-review",
        "--iso-review-remark-policy", "201600",
        "--sha-dir", str(unit / "201600 SHA"),
        "--pcf-dir", str(unit / "201600_0B"),
        "--output-dir", str(unit / "201600_PDF-水印及编号标注"),
        "--audit-dir", str(unit / "水印及编号标注审计"),
        "--signature-dir", str(unit / "签名资源"),
        "--support-table", str(unit / "支架表.csv"),
        "--valve-table", str(unit / "阀门表.csv"),
        "--flange-table", str(unit / "法兰表.csv"),
        "--weld-table", str(
            unit / "焊口表-201600_0B_ISO对照结果-(周子豪)人工1012.xlsx"
        ),
    ]
    sys.argv[1:1] = defaults
    raise SystemExit(main())
