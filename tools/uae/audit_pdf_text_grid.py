"""Measure character-origin quantization without changing rendered drawings."""
import argparse
import json
import math
from pathlib import Path
import statistics
import fitz


def inspect(path):
    advances=[]
    with fitz.open(path) as document:
        producer=document.metadata.get('producer','')
        creator=document.metadata.get('creator','')
        fonts=sorted({(font[1],font[2],font[3]) for page in document for font in page.get_fonts()})
        for page in document:
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines',[]):
                    dx,dy=line['dir']
                    for span in line['spans']:
                        chars=span['chars']
                        for a,b in zip(chars,chars[1:]):
                            x,y=a['origin'];u,v=b['origin']
                            advance=((u-x)*dx+(v-y)*dy)*25.4/72
                            if math.isfinite(advance) and .05<advance<20:advances.append(advance)
    grids={}
    for dpi in (96,300,600,1200):
        residuals=[abs(v*dpi/25.4-round(v*dpi/25.4)) for v in advances]
        grids[str(dpi)]={'median_fractional_pixel_residual':statistics.median(residuals) if residuals else None,
                         'fraction_within_0_01_pixel':sum(v<.01 for v in residuals)/len(residuals) if residuals else None}
    return {'path':str(path),'producer':producer,'creator':creator,'fonts':fonts,
            'character_advances':len(advances),'grid_candidates_dpi':grids}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--reference-dir',required=True,type=Path)
    cli.add_argument('--output-dir',required=True,type=Path)
    cli.add_argument('--report',required=True,type=Path)
    args=cli.parse_args()
    rows=[]
    for output in sorted(args.output_dir.glob('*_SHA.pdf')):
        reference=args.reference_dir/(output.stem.removesuffix('_SHA')+'_0.pdf')
        rows.append({'reference':inspect(reference),'output':inspect(output)})
    if not rows:cli.error('No paired drawings found')
    report={'drawings':rows,'limitations':[
        'Grid agreement does not identify the original rendering software or prove a unique DPI.',
        'A finer integer-multiple grid can fit the same positions.',
        'Character positions alone do not determine font outlines, point size or native SHA height semantics.',
        'Evidence only; no per-character metrics are copied into production rendering.']}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in rows:
        print(Path(row['output']['path']).name,
              'reference 600dpi agreement',row['reference']['grid_candidates_dpi']['600']['fraction_within_0_01_pixel'],
              'output',row['output']['grid_candidates_dpi']['600']['fraction_within_0_01_pixel'])


if __name__=='__main__':main()
