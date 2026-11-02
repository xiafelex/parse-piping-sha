"""Compare exact PDF text spans and repeated occurrences; not full fidelity."""
import argparse
import json
import math
from pathlib import Path
import statistics
import fitz


def spans(path):
    result={}
    with fitz.open(path) as document:
        for page in document:
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    for span in line['spans']:
                        text=span['text'].strip()
                        if not text:continue
                        xy=fitz.Point(span['origin'])*page.rotation_matrix
                        quad=fitz.recover_quad(line['dir'],span)
                        result.setdefault(text,[]).append({'x_mm':xy.x*25.4/72,'y_mm':xy.y*25.4/72,
                            'width_mm':abs(quad.ur-quad.ul)*25.4/72,
                            'font_size_mm':span['size']*25.4/72,'font':span['font'],'page':page.number})
    return result


def compare(reference,output):
    a,b=spans(reference),spans(output)
    matched=[]
    for text in sorted(a.keys()&b.keys()):
        if len(a[text])!=1 or len(b[text])!=1:continue
        old,new=a[text][0],b[text][0]
        if old['page']!=new['page']:continue
        matched.append({'text':text,'reference':old,'output':new,
                        'delta':{key:new[key]-old[key] for key in ('x_mm','y_mm','font_size_mm','width_mm')}})
    occurrence_pairs=[];missing=[];extra=[];count_differences=[]
    for text in sorted(a.keys()|b.keys()):
        old=a.get(text,[]);new=b.get(text,[])
        if len(old)!=len(new):count_differences.append({'text':text,'reference_count':len(old),'output_count':len(new)})
        # Greedy nearest matching is explicit and bounded; it is not a claim
        # that all duplicate labels have a unique semantic association.
        candidates=sorted((math.hypot(u['x_mm']-v['x_mm'],u['y_mm']-v['y_mm']),i,j)
                          for i,u in enumerate(old) for j,v in enumerate(new) if u['page']==v['page'])
        used_old=set();used_new=set()
        for distance,i,j in candidates:
            if distance>5:break
            if i in used_old or j in used_new:continue
            used_old.add(i);used_new.add(j)
            occurrence_pairs.append({'text':text,'reference':old[i],'output':new[j],'position_distance_mm':distance})
        missing.extend({'text':text,**v} for i,v in enumerate(old) if i not in used_old)
        extra.extend({'text':text,**v} for j,v in enumerate(new) if j not in used_new)
    return {'reference':str(reference),'output':str(output),'unique_exact_matches':len(matched),
            'median_absolute_delta':{key:statistics.median(abs(v['delta'][key]) for v in matched) if matched else None
                for key in ('x_mm','y_mm','font_size_mm','width_mm')},
            'reference_texts_without_exact_span_match':sorted(a.keys()-b.keys()),
            'output_texts_without_exact_span_match':sorted(b.keys()-a.keys()),'matched':matched,
            'occurrences':{'matched_count':len(occurrence_pairs),'count_differences':count_differences,
                           'unmatched_reference':missing,'unmatched_output':extra,
                           'position_outliers_over_1mm':[v for v in occurrence_pairs if v['position_distance_mm']>1],
                           'scope':'Exact text, same page, one-to-one greedy nearest origins within 5mm; split/merged spans can appear unmatched.'},
            'scope':'Median metrics cover exact unique spans only. Occurrence audit separately includes duplicates. Signatures and geometry excluded.'}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--reference-dir',type=Path,required=True)
    cli.add_argument('--output-dir',type=Path,required=True)
    cli.add_argument('--report',type=Path,required=True)
    args=cli.parse_args()
    rows=[compare(args.reference_dir/(pdf.stem.removesuffix('_SHA')+'_0.pdf'),pdf)
          for pdf in sorted(args.output_dir.glob('*_SHA.pdf'))]
    if not rows:cli.error('No output PDFs found')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in rows:print(Path(row['output']).name,row['unique_exact_matches'],row['median_absolute_delta'])


if __name__=='__main__':main()
