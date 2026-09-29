#!/usr/bin/env python3
"""000201 entry point: ISO review numbering, 0A/0B tables and signatures."""

from __future__ import annotations

import sys
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from convert_chengda_weld_sha_to_pdf import main


if __name__ == "__main__":
    unit = DATA_DIR / "SHA文件转换" / "000201"
    if "--profile" in sys.argv[1:] or "--weld-table-mode" in sys.argv[1:]:
        raise SystemExit("本入口已固化为000201 ISO对照及无坐标焊口人工补标规则")
    defaults = [
        "--profile", "100200",
        "--weld-table-mode", "iso-review",
        "--missing-position-is-ep3d-missing",
        "--sha-dir", str(unit / "000201 SHA"),
        "--pcf-dir", str(unit / "000201_0B"),
        "--output-dir", str(unit / "000201_PDF-水印及编号标注"),
        "--audit-dir", str(unit / "水印及编号标注审计"),
        "--signature-dir", str(unit / "签名资源"),
        "--support-table", str(unit / "支架表0A.csv"),
        "--support-table", str(unit / "支架表0B.csv"),
        "--valve-table", str(unit / "阀门表0A.csv"),
        "--valve-table", str(unit / "阀门表0B.csv"),
        "--flange-table", str(unit / "法兰表0A.csv"),
        "--flange-table", str(unit / "法兰表0B.csv"),
        "--weld-table", str(unit / "焊口表-000201_0A+0B_ISO对照结果.xlsx"),
    ]
    sys.argv[1:1] = defaults
    raise SystemExit(main())
