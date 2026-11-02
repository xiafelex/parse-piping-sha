"""304100-WS: reviewed ISO weld numbers, management labels, no spool labels."""
import sys

from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main


if __name__ == '__main__':
    unit = PROJECT_DIR / 'SHA文件转换' / '304100-WS'
    fixed_options = ('--profile', '--weld-table-mode', '--iso-review-remark-policy',
                     '--weld-number-column', '--spool-number-column')
    if any(arg.split('=', 1)[0] in fixed_options for arg in sys.argv[1:]):
        raise SystemExit('本入口固化304100-WS审核编号、管理编号及无管段标注规则')
    sys.argv[1:1] = [
        '--profile', '100200', '--weld-table-mode', 'iso-review',
        '--iso-review-remark-policy', '304100-WS',
        '--sha-dir', str(unit / 'SHA'),
        '--pcf-dir', str(PROJECT_DIR / 'PCF文件夹' / '304100-WS'),
        '--output-dir', str(unit / '304100-WS_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
        '--signature-dir', str(unit / '签名资源'),
        '--support-table', str(unit / '支架表.csv'),
        '--valve-table', str(unit / '阀门表.csv'),
        '--flange-table', str(unit / '法兰表.csv'),
        '--weld-table', str(unit / '焊口表-304100_ISO对照结果.xlsx'),
    ]
    raise SystemExit(main())
