"""100600: use reviewed ISO numbers, honoring deletion and unresolved notes."""
import sys
from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main

if __name__ == '__main__':
    unit = PROJECT_DIR / 'SHA文件转换' / '100600'
    if any(arg.split('=', 1)[0] in ('--profile', '--weld-table-mode', '--iso-review-remark-policy') for arg in sys.argv[1:]):
        raise SystemExit('本入口固化100600 ISO审核及核对备注规则')
    sys.argv[1:1] = [
        '--profile', '100200', '--weld-table-mode', 'iso-review',
        '--iso-review-remark-policy', '100600',
        '--sha-dir', str(unit / '100600 SHA'),
        '--pcf-dir', str(unit / '100600_0A'),
        '--output-dir', str(unit / '100600_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
        '--signature-dir', str(unit / '签名资源'),
        '--support-table', str(unit / '支架表.csv'),
        '--valve-table', str(unit / '阀门表.csv'),
        '--flange-table', str(unit / '法兰表.csv'),
        '--weld-table', str(unit / '焊口表-100600_0A_ISO对照结果-人工1010.xlsx'),
        '--spool-number-column', '国内预制管段号',
    ]
    raise SystemExit(main())
