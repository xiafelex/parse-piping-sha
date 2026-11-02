"""404043: reviewed ISO IDs, except records explicitly requiring EP3D IDs."""
import sys
from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main

if __name__ == '__main__':
    unit = PROJECT_DIR / 'SHA文件转换' / '404043'
    if any(arg.split('=', 1)[0] in ('--profile', '--weld-table-mode', '--iso-review-remark-policy') for arg in sys.argv[1:]):
        raise SystemExit('本入口固化404043 ISO审核及以EP为准的编号规则')
    sys.argv[1:1] = [
        '--profile', '100200', '--weld-table-mode', 'iso-review',
        '--iso-review-remark-policy', '404043',
        '--sha-dir', str(unit / '404043 SHA'),
        '--pcf-dir', str(unit / '404043 0A'),
        '--output-dir', str(unit / '404043_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
        '--signature-dir', str(unit / '签名资源'),
        '--support-table', str(unit / '支架表.csv'),
        '--valve-table', str(unit / '阀门表.csv'),
        '--flange-table', str(unit / '法兰表.csv'),
        '--weld-table', str(unit / '焊口表-404043 0A_ISO对照结果-人工1014.xlsx'),
        '--spool-number-column', '国内预制管段号',
    ]
    raise SystemExit(main())
