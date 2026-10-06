#!/usr/bin/env python3
"""402001 ISO-reviewed numbering, shared renderer and unit signatures."""
import sys
from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main

if __name__ == "__main__":
    unit = PROJECT_DIR / "SHA文件转换" / "402001"
    if "--profile" in sys.argv[1:] or "--weld-table-mode" in sys.argv[1:]:
        raise SystemExit("本入口已固化为402001 ISO审核对照规则")
    sys.argv[1:1] = [
        "--profile", "100200", "--weld-table-mode", "iso-review",
        "--iso-review-remark-policy", "402001",
        "--sha-dir", str(unit / "402001 SHA"),
        "--pcf-dir", str(unit / "402001 0A"),
        "--output-dir", str(unit / "402001_PDF-水印及编号标注"),
        "--audit-dir", str(unit / "水印及编号标注审计"),
        "--signature-dir", str(unit / "签名资源"),
        "--support-table", str(unit / "支架表.csv"),
        "--valve-table", str(unit / "阀门表.csv"),
        "--flange-table", str(unit / "法兰表.csv"),
        "--weld-table", str(unit / "焊口表-402001 0A-壁厚材质处理-带特性_ISO对照结果-单侧自动配对-合并.xlsx"),
        "--spool-number-column", "国内预制管段号",
    ]
    raise SystemExit(main())
