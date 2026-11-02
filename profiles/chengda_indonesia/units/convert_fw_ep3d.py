"""FW units with original EP3D weld IDs and centrally stored PCFs."""
import argparse
import sys

from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main


UNITS = {
    '200500FW': ('200500FW 0A', '焊口表-200500FW-壁厚材质处理-带特性.xlsx'),
    '201600FW': ('201600FW 0', '焊口表-201600FW 0-壁厚材质处理-带特性.xlsx'),
}


def conversion_args(name):
    unit = PROJECT_DIR / 'SHA文件转换' / name
    pcf_folder, weld_table = UNITS[name]
    return [
        '--profile', '100200', '--weld-table-mode', 'construction',
        '--weld-number-column', '焊点编号',
        '--sha-dir', str(unit / 'SHA'),
        '--pcf-dir', str(PROJECT_DIR / 'PCF文件夹' / pcf_folder),
        '--weld-table', str(unit / weld_table),
        '--support-table', str(unit / '支架表.csv'),
        '--valve-table', str(unit / '阀门表.csv'),
        '--flange-table', str(unit / '法兰表.csv'),
        '--signature-dir', str(unit / '签名资源'),
        '--output-dir', str(unit / f'{name}_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
    ]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--unit', choices=UNITS, required=True)
    selected, remaining = parser.parse_known_args()
    forbidden = {'--profile', '--weld-table-mode', '--weld-number-column', '--spool-number-column'}
    if any(arg.split('=', 1)[0] in forbidden for arg in remaining):
        raise SystemExit('FW入口固定使用EP3D焊点编号，不标管段号')
    sys.argv[1:] = [*conversion_args(selected.unit), *remaining]
    raise SystemExit(main())
