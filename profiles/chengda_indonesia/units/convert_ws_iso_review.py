"""Reviewed WS units: ISO weld IDs, management labels, watermarks, no spools."""
import argparse
from pathlib import Path
import sys

from _bootstrap import PROJECT_DIR
from convert_chengda_weld_sha_to_pdf import main


UNITS = ('404100-WS-FW', '302100-WS', '302200-WS', '000800-WS', '300500-WS')
REFERENCE_PATTERNS = {
    '404100-WS-FW': '*40-PIP-FCN-E-0001*.pdf',
    '302100-WS': '*30-PIP-ISO-E-8001~8009*.pdf',
    '302200-WS': '*30-PIP-ISO-E-1101~1159*.pdf',
    '000800-WS': '*00-PIP-ISO-E-7001~7067*.pdf',
    '300500-WS': '*30-PIP-FCN-E-0003*.pdf',
}


def unit_paths(name):
    unit = PROJECT_DIR / 'SHA文件转换' / name
    local_pcf = [p for p in (unit / 'PCF', unit) if p.is_dir() and any(p.glob('*.pcf'))]
    pcf = local_pcf[0] if local_pcf else PROJECT_DIR / 'PCF文件夹' / name
    local_pdf = sorted(unit.glob('*.pdf'))
    candidates = local_pdf or sorted((PROJECT_DIR / 'PCF处理' /
        '施工单位焊口图图纸（二阶段）261022' / '空白FIN图').glob(REFERENCE_PATTERNS[name]))
    if len(candidates) != 1:
        raise ValueError(f'{name}参考PDF需唯一确认：{candidates}')
    return unit, pcf, candidates[0]


def conversion_args(name):
    unit, pcf, _reference = unit_paths(name)
    policy = 'ws-iso-review-302100' if name == '302100-WS' else 'ws-iso-review'
    weld_name = (f'焊口表-{name}_ISO对照结果-DD-改.xlsx' if name == '300500-WS'
                 else f'焊口表-{name}_ISO对照结果.xlsx')
    return [
        '--profile', '100200', '--weld-table-mode', 'iso-review',
        '--iso-review-remark-policy', policy,
        '--sha-dir', str(unit / 'SHA'), '--pcf-dir', str(pcf),
        '--weld-table', str(unit / weld_name),
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
    forbidden = {'--profile', '--weld-table-mode', '--weld-number-column',
                 '--iso-review-remark-policy', '--spool-number-column'}
    if any(arg.split('=', 1)[0] in forbidden for arg in remaining):
        raise SystemExit('本入口固化各单元审核编号及无对照图回退规则，不标管段号')
    sys.argv[1:] = [*conversion_args(selected.unit), *remaining]
    raise SystemExit(main())
