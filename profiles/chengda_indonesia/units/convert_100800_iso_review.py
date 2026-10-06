"""100800 ISO-reviewed numbers, EP3D fallback outside review scope."""
import sys
from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main

if __name__ == '__main__':
    unit = PROJECT_DIR / 'SHA文件转换' / '100800'
    if any(arg in sys.argv[1:] for arg in ('--profile', '--weld-table-mode', '--iso-review-remark-policy')):
        raise SystemExit('本入口固化100800 ISO审核及范围外EP3D编号规则')
    sys.argv[1:1] = [
        '--profile', '100200', '--weld-table-mode', 'iso-review',
        '--iso-review-remark-policy', 'default',
        '--sha-dir', str(unit / '100800 SHA'),
        '--pcf-dir', str(unit / '100800 0B'),
        '--output-dir', str(unit / '100800_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
        '--signature-dir', str(unit / '签名资源'),
        '--support-table', str(unit / '支架表.csv'),
        '--valve-table', str(unit / '阀门表.csv'),
        '--flange-table', str(unit / '法兰表.csv'),
        '--weld-table', str(unit / '焊口表-100800 0B_ISO对照结果.xlsx'),
        '--spool-number-column', '国内预制管段号',
    ]
    raise SystemExit(main())
