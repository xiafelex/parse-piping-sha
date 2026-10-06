"""301300 ISO review numbering and latest shared Chengda conversion rules."""
import sys
from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main

if __name__ == '__main__':
    unit = PROJECT_DIR / 'SHA文件转换' / '301300'
    if any(arg in sys.argv[1:] for arg in ('--profile', '--weld-table-mode', '--iso-review-remark-policy')):
        raise SystemExit('本入口固化301300 ISO审核编号规则')
    sys.argv[1:1] = [
        '--profile', '100200', '--weld-table-mode', 'iso-review',
        '--iso-review-remark-policy', 'default',
        '--sha-dir', str(unit / '301300 SHA'),
        '--pcf-dir', str(unit / '301300-WS PCF'),
        '--output-dir', str(unit / '301300_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
        '--signature-dir', str(unit / '签名资源'),
        '--support-table', str(unit / '支架表.csv'),
        '--valve-table', str(unit / '阀门表.csv'),
        '--flange-table', str(unit / '法兰表.csv'),
        '--weld-table', str(unit / '焊口表-301300施工单位_JT综合ISO对照结果-人工0930.xlsx'),
        '--spool-number-column', '国内预制管段号',
    ]
    raise SystemExit(main())
