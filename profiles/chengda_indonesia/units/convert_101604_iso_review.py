#!/usr/bin/env python3
"""101604: ISO-reviewed weld numbers, unit signatures and management callouts."""

from __future__ import annotations

import sys

from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main


if __name__ == "__main__":
    unit = PROJECT_DIR / "SHA文件转换" / "101604"
    if "--profile" in sys.argv[1:] or "--weld-table-mode" in sys.argv[1:]:
        raise SystemExit("本入口已固化为101604 ISO审核对照规则")
    defaults = [
        "--profile", "100200",
        "--weld-table-mode", "iso-review",
        "--sha-dir", str(unit / "101604 SHA"),
        "--pcf-dir", str(unit / "101604 0B"),
        "--output-dir", str(unit / "101604_PDF-水印及编号标注"),
        "--audit-dir", str(unit / "水印及编号标注审计"),
        "--signature-dir", str(unit / "签名资源"),
        "--support-table", str(unit / "支架表.csv"),
        "--valve-table", str(unit / "阀门表.csv"),
        "--flange-table", str(unit / "法兰表.csv"),
        "--weld-table", str(
            unit / "焊口表-101604 0B_ISO对照结果-人工0930.xlsx"
        ),
    ]
    sys.argv[1:1] = defaults
    raise SystemExit(main())
