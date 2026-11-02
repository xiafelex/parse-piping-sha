"""Reuse accepted complete signature assets for the original-number FW units."""
from pathlib import Path
import json
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_ws_iso_review_signatures import reused, view_for
from _bootstrap import PROJECT_DIR


def main():
    for name, selection in {
        '200500FW': [('201600', 'BMZ', 'RevisedBy'), ('200702', 'ZXY', 'CheckedBy'),
                     ('201600', 'CP', 'ApprovedBy')],
        '201600FW': [('201600', 'BMZ', 'RevisedBy'), ('201600', 'CP', 'CheckedBy'),
                     ('201600', 'LKF', 'ApprovedBy')],
    }.items():
        unit = PROJECT_DIR / 'SHA文件转换' / name
        folder = unit / '签名资源'
        if (folder / 'profile.json').exists():
            raise FileExistsError(f'Existing signature profile must be reviewed: {folder}')
        folder.mkdir(exist_ok=True)
        profile = {'signature_scale': 1.0, 'sha_viewbox_scaled': list(view_for(unit)),
                   'people': {person: reused(source, person, field, folder)
                              for source, person, field in selection}}
        if name == '200500FW':
            # The proven unit prefers the upright user-cleaned asset over
            # its older automatically extracted (inverted) complete image.
            manual = PROJECT_DIR / 'SHA文件转换/200702/签名资源/ZXY-manual.png'
            shutil.copy2(manual, folder / manual.name)
            profile['people']['ZXY'].update(file=manual.name, flip_180=False,
                                             asset_source=str(manual))
        (folder / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(name, 'reused', ', '.join(profile['people']))


if __name__ == '__main__':
    main()
