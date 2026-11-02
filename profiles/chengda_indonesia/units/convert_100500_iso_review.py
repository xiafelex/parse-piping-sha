"""100500: ISO-reviewed numbers in scope, original EP3D numbers outside scope."""
import sys
from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main

if __name__ == '__main__':
    unit = PROJECT_DIR / 'SHA文件转换' / '100500'
    if any(arg.split('=', 1)[0] in ('--profile', '--weld-table-mode', '--iso-review-remark-policy') for arg in sys.argv[1:]):
        raise SystemExit('本入口固化100500 ISO审核及范围外EP3D编号规则')
    sys.argv[1:1] = [
        '--profile', '100200', '--weld-table-mode', 'iso-review',
        '--iso-review-remark-policy', 'default',
        '--sha-dir', str(unit / '100500 SHA'),
        '--pcf-dir', str(unit / '100500 0B'),
        '--output-dir', str(unit / '100500_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
        '--signature-dir', str(unit / '签名资源'),
        '--support-table', str(unit / '支架表.csv'),
        '--valve-table', str(unit / '阀门表.csv'),
        '--flange-table', str(unit / '法兰表.csv'),
        '--weld-table', str(unit / '焊口表-100500 0B-壁厚材质处理-带特性_ISO对照结果-人工1008.xlsx'),
        '--spool-number-column', '国内预制管段号',
    ]
    raise SystemExit(main())
