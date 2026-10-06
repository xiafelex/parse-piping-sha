"""100100: ISO-reviewed numbers in scope, original EP3D numbers outside scope.

Read both review-note columns. Future-revision cancellation notes do not
authorize removal: records without EP3D anchors stay in manual review.
"""
import sys
from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main

if __name__ == '__main__':
    unit = PROJECT_DIR / 'SHA文件转换' / '100100'
    if any(arg in sys.argv[1:] for arg in ('--profile', '--weld-table-mode', '--iso-review-remark-policy')):
        raise SystemExit('本入口固化100100 ISO审核及范围外EP3D编号规则')
    sys.argv[1:1] = [
        '--profile', '100200', '--weld-table-mode', 'iso-review',
        '--iso-review-remark-policy', 'default',
        '--sha-dir', str(unit / '100100 SHA'),
        '--pcf-dir', str(unit / '100100 0B'),
        '--output-dir', str(unit / '100100_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
        '--signature-dir', str(unit / '签名资源'),
        '--support-table', str(unit / '支架表.csv'),
        '--valve-table', str(unit / '阀门表.csv'),
        '--flange-table', str(unit / '法兰表.csv'),
        '--weld-table', str(unit / '焊口表-100100 0B_ISO对照结果-人工1001.xlsx'),
        '--spool-number-column', '国内预制管段号',
    ]
    raise SystemExit(main())
