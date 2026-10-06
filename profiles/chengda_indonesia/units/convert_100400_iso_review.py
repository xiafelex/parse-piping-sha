"""100400: ISO review numbering with shared default remark policy."""
import sys
from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main

if __name__ == '__main__':
    unit = PROJECT_DIR / 'SHA文件转换' / '100400'
    if any(arg in sys.argv[1:] for arg in ('--profile', '--weld-table-mode', '--iso-review-remark-policy')):
        raise SystemExit('本入口固化100400 ISO编号及默认备注规则')
    sys.argv[1:1] = [
        '--profile', '100200', '--weld-table-mode', 'iso-review',
        '--iso-review-remark-policy', 'default',
        '--sha-dir', str(unit / '100400 SHA'),
        '--pcf-dir', str(unit / '100400 0B'),
        '--output-dir', str(unit / '100400_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
        '--signature-dir', str(unit / '签名资源'),
        '--support-table', str(unit / '支架表.csv'),
        '--valve-table', str(unit / '阀门表.csv'),
        '--flange-table', str(unit / '法兰表.csv'),
        '--weld-table', str(unit / '焊口表-100400 0B-壁厚材质处理-带特性_ISO对照--人工1005xlsx.xlsx'),
        '--spool-number-column', '国内预制管段号',
    ]
    raise SystemExit(main())
