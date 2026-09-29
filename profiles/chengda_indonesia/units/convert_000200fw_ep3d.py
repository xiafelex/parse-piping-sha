#!/usr/bin/env python3
"""Frozen 000200FW entry point: pure EP3D/PCF weld-number comparison."""

from __future__ import annotations

import sys
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from convert_chengda_weld_sha_to_pdf import main


if __name__ == "__main__":
    if "--profile" in sys.argv[1:]:
        raise SystemExit("本入口的--profile已固化为000200fw")
    root = DATA_DIR / "SHA文件转换"
    defaults = [
        "--profile", "000200fw",
        "--sha-dir", str(root / "000200FW-SHA"),
        "--pcf-dir", str(root / "000200FW 0-PCF-焊口反写测试"),
        "--output-dir", str(root / "000200FW_PDF-水印及编号标注"),
        "--audit-dir", str(root / "水印及编号标注审计"),
        "--support-table", str(root / "000200FW" / "支架表.csv"),
        "--valve-table", str(root / "000200FW" / "阀门表.csv"),
        "--flange-table", str(root / "000200FW" / "法兰表.csv"),
    ]
    sys.argv[1:1] = defaults
    raise SystemExit(main())
