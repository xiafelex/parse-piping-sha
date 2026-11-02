"""Compare stroked straight segments, excluding fills, curves and images."""
import argparse
import json
import math
from pathlib import Path
import fitz


def segments(path):
    result=[]
    with fitz.open(path) as document:
        for page in document:
            for drawing in page.get_drawings():
                if drawing['width'] is None:continue
                for item in drawing['items']:
                    pairs=[]
                    if item[0]=='l':pairs=[(item[1],item[2])]
                    elif item[0]=='re':
                        r=item[1];pairs=[(r.tl,r.tr),(r.tr,r.br),(r.br,r.bl),(r.bl,r.tl)]
                    elif item[0]=='qu':
                        q=item[1];pairs=[(q.ul,q.ur),(q.ur,q.lr),(q.lr,q.ll),(q.ll,q.ul)]
                    for start,end in pairs:
                        start=start*page.rotation_matrix;end=end*page.rotation_matrix
                        result.append({'xy_mm':[v*25.4/72 for v in (*start,*end)],
                                       'width_mm':drawing['width']*25.4/72,'page':page.number})
    return result


def distance(a,b):
    a,b=a['xy_mm'],b['xy_mm']
    return min(max(math.dist(a[:2],b[:2]),math.dist(a[2:],b[2:])),
               max(math.dist(a[:2],b[2:]),math.dist(a[2:],b[:2])))


def compare(reference,output):
    original=segments(reference);rendered=segments(output)
    unmatched=[];width_differences=[];matched=0
    for segment in original:
        if math.dist(segment['xy_mm'][:2],segment['xy_mm'][2:])<3:continue
        candidates=[v for v in rendered if v['page']==segment['page']]
        nearest=min(candidates,key=lambda v:distance(segment,v)) if candidates else None
        error=distance(segment,nearest) if nearest else None
        if error is None or error>.25:
            unmatched.append({'reference':segment,'nearest_endpoint_error_mm':error})
        else:
            matched+=1
            if abs(segment['width_mm']-nearest['width_mm'])>.15:
                width_differences.append({'reference':segment,'output':nearest})
    return {'reference':str(reference),'output':str(output),'matched_segments':matched,
            'unmatched_segments':unmatched,'width_differences':width_differences,
            'scope':'Reference stroked straight edges >=3mm; endpoint tolerance 0.25mm, width difference threshold 0.15mm. Not one-to-one; split edges and stroke-to-fill conversions may appear unmatched. Curves, images, text and filled shapes excluded.'}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--reference-dir',required=True,type=Path)
    cli.add_argument('--output-dir',required=True,type=Path)
    cli.add_argument('--report',required=True,type=Path)
    args=cli.parse_args()
    rows=[compare(args.reference_dir/(p.stem.removesuffix('_SHA')+'_0.pdf'),p)
          for p in sorted(args.output_dir.glob('*_SHA.pdf'))]
    if not rows:cli.error('No output PDFs found')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in rows:
        print(Path(row['output']).name,'matched',row['matched_segments'],
              'unmatched',len(row['unmatched_segments']),'width differences',len(row['width_differences']))


if __name__=='__main__':main()
