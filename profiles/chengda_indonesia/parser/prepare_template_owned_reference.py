"""Extract plot font outlines and signatures from the supplied reference PDF."""
from pathlib import Path
from chengda_paths import ASSET_DIR, PROJECT_DIR
import io
import json
import statistics
import fitz
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable


def main():
    unit = PROJECT_DIR / 'SHA文件转换' / '400101'
    reference = next(unit.glob('CAA-CE-1-ED-40-PIP-ISO-E-0101001*.pdf'))
    font_dir = ASSET_DIR / 'template_owned_fonts'
    font_dir.mkdir(parents=True, exist_ok=True)
    with fitz.open(reference) as document:
        page = document[8]
        traces = page.get_texttrace()
        fonts = page.get_fonts(full=True)
        roles = {'plot': 'ERECTION MATERIALS', 'feature': '65'}
        profile = {'source_pdf': reference.name, 'source_page': 9, 'fonts': {}, 'plot_height_to_em': 1.4}
        for role, sample in roles.items():
            trace = next(t for t in traces if ''.join(chr(c[0]) for c in t['chars']).strip() == sample)
            name = trace['font']
            xref = next(f[0] for f in fonts if f[3].split('+')[-1] == name)
            extracted = document.extract_font(xref)
            font = TTFont(io.BytesIO(extracted[3]))
            order = font.getGlyphOrder()
            cmap = {}
            for t in traces:
                if t['font'] == name:
                    for code, gid, *_ in t['chars']:
                        if code >= 32 and 0 <= gid < len(order):
                            cmap[code] = order[gid]
            table = CmapSubtable.newSubtable(4)
            table.platformID, table.platEncID, table.language = 3, 1, 0
            table.cmap = cmap
            font['cmap'].tables = [table]
            output = font_dir / f'{role}.ttf'
            font.save(output)
            profile['fonts'][role] = dict(file=output.name, source_font=name, xref=xref,
                                           unicode_count=len(cmap),
                                           ascent=font['hhea'].ascent/font['head'].unitsPerEm,
                                           descent=font['hhea'].descent/font['head'].unitsPerEm)
            if role == 'feature':
                # STANDARD-Z is plotted with tighter run spacing than the
                # embedded TTF advances. Recover that font-level tracking
                # from horizontal runs, rather than fit individual strings.
                deltas = [
                    (following[2][0]-current[2][0])/t['size']
                    - font['hmtx'][cmap[current[0]]][0]/font['head'].unitsPerEm
                    for t in traces if t['font'] == name and t['dir'] == (1, 0)
                    for current, following in zip(t['chars'], t['chars'][1:])
                    if current[0] in cmap
                ]
                profile['fonts'][role]['letter_spacing_em'] = statistics.median(deltas)
        # Source glyph envelope for ordinary plot text is about 1.13 em;
        # derive the feature equivalent from the actual SHA/PSM and source font.
        import sha_to_svg_prototype as api
        sha = unit / '400101 SHA' / '40-42-0101-26-40EC40001-25.sha'
        streams = api.read_sha_streams(sha)
        r = next(r for r in api.text_records(streams['Sheet221']) if r['text'] == '65')
        box = api.psm_bbox(streams['PSMcluster0'], r['graphic_ref'])
        trace = next(t for t in traces if ''.join(chr(c[0]) for c in t['chars']).strip() == '65')
        inherited = next((v for b in streams.values() if (v := api.declared_sheet_viewbox(b)) is not None), None)
        view = api.sheet_viewbox(streams['Sheet6'], inherited)
        em = trace['size'] * view[2] / page.rect.width
        profile['feature_bbox_to_em'] = (box[3]-box[1]) / em
        (font_dir/'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
        signature_profile = unit / '签名资源' / 'profile.json'
        if signature_profile.exists():
            sig = json.loads(signature_profile.read_text(encoding='utf-8'))
            if not sig.get('sha_viewbox_scaled'):
                for person in sig['people'].values():
                    for key in ('width', 'slot_width'):
                        person[key] *= view[2] / 16840.0
                    for key in ('height', 'slot_height'):
                        person[key] *= view[3] / 11910.0
                sig['sha_viewbox_scaled'] = list(view)
                signature_profile.write_text(json.dumps(sig, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(profile, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
