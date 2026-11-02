"""Second-stage WS units: EP3D weld-point IDs, management labels, no spools."""
import argparse
import sys
from pathlib import Path
from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main

UNITS = ('200300-WS', '200600-WS', '404049-WS', '204100-WS',
         '100201-WS', '100401-WS', '100800-WS', '104100-WS')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--unit', choices=UNITS, required=True)
    selected, remaining = parser.parse_known_args()
    forbidden = {'--profile', '--weld-table-mode', '--weld-number-column', '--spool-number-column'}
    if any(arg.split('=', 1)[0] in forbidden for arg in remaining):
        raise SystemExit('WS入口固定按焊点编号标注，且不标注管段号')
    name = selected.unit
    unit = PROJECT_DIR / 'SHA文件转换' / name
    tables = (
        unit if name in {'204100-WS', '104100-WS'}
        else Path('D:/EP3D_Spool30/Project/成达印尼项目260723/tmp') / f'{name}-261022出图'
    )
    # Reviewer-confirmed EP3D split drawing. This is an input alias, not a
    # general rule that drops suffixes from other drawing numbers.
    weld_alias_args = (
        ['--weld-sheet-alias', '10-80-0200-26-10FW00201-01B=10-80-0200-26-10FW00201-01']
        if name == '100201-WS' else []
    )
    sys.argv[1:] = [
        '--profile', '100200', '--weld-table-mode', 'construction',
        '--weld-number-column', '焊点编号',
        '--sha-dir', str(unit / 'SHA'),
        '--pcf-dir', str(PROJECT_DIR / 'PCF文件夹' / name),
        '--weld-table', str(tables / f'焊口表-{name}-壁厚材质处理-带特性.xlsx'),
        '--support-table', str(tables / '支架表.csv'),
        '--valve-table', str(tables / '阀门表.csv'),
        '--flange-table', str(tables / '法兰表.csv'),
        '--signature-dir', str(unit / '签名资源'),
        '--output-dir', str(unit / f'{name}_PDF-水印及编号标注'),
        '--audit-dir', str(unit / '水印及编号标注审计'),
        *weld_alias_args,
        *remaining,
    ]
    raise SystemExit(main())
