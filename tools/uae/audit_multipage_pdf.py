"""Compare every received design page with native SHA output, with inspectable limits."""
import argparse,json,sys,hashlib
from pathlib import Path
from collections import Counter
import fitz,numpy as np,openpyxl
from PIL import Image,ImageFilter
from openpyxl.styles import Font,PatternFill
from audit_pdf_text_fidelity import compare as compare_text

REGIONS={'管道图面':(6,5,301,245),'材料表':(302,5,415,201),'图框说明':(7,255,183,281),
         '标题栏':(183,255,415,297),'修订签字区域':(387,206,414,222),'专业签字区域':(255,247,278,252)}

def compare_words(reference,output):
    """Locate actual word boxes; stripped span origins include leading whitespace."""
    grouped=[]
    for path in (reference,output):
        words={}
        with fitz.open(path) as doc:
            for page in doc:
                for w in page.get_text('words'):
                    rect=fitz.Rect(w[:4])*page.rotation_matrix
                    words.setdefault((page.number,w[4]),[]).append([rect.x0*25.4/72,rect.y0*25.4/72])
        grouped.append(words)
    old,new=grouped;missing=[];extra=[];outliers=[];count=0
    import math
    for key in old.keys()|new.keys():
        a,b=old.get(key,[]),new.get(key,[]);useda=set();usedb=set()
        for delta,i,j in sorted((math.dist(x,y),i,j) for i,x in enumerate(a) for j,y in enumerate(b)):
            if delta>3:break
            if i in useda or j in usedb:continue
            useda.add(i);usedb.add(j);count+=1
            if delta>1:outliers.append({'page':key[0]+1,'word':key[1],'distance_mm':delta})
        missing.extend({'page':key[0]+1,'word':key[1],'xy_mm':x} for i,x in enumerate(a) if i not in useda)
        extra.extend({'page':key[0]+1,'word':key[1],'xy_mm':x} for i,x in enumerate(b) if i not in usedb)
    return {'reference':str(reference),'output':str(output),'matched_word_occurrences':count,
            'unmatched_reference':missing,'unmatched_output':extra,'position_outliers_over_1mm':outliers,
            'scope':'Exact words, same page, greedy one-to-one box positions within 3mm. Driver word splitting can differ. Not a semantic or glyph fidelity score.'}

def raster(page,dpi):
    pix=page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False,colorspace=fitz.csRGB)
    # Respect each original page's rotation and normalize its driver-rounded
    # MediaBox (e.g. 1191x842 pt) to the same A3 paper-space coordinate system.
    return Image.frombytes('RGB',(pix.width,pix.height),pix.samples).resize((round(420*dpi/25.4),round(297*dpi/25.4)))

def compare_page(a,b,dpi=200):
    scale=dpi/25.4;radius=max(1,round(.254*scale));rows={}
    for name,box in REGIONS.items():
        crop=tuple(round(v*scale) for v in box)
        old=np.min(np.asarray(a.crop(crop)),axis=2)<180;new=np.min(np.asarray(b.crop(crop)),axis=2)<180
        def dilate(mask):return np.asarray(Image.fromarray(mask.astype('uint8')*255).filter(ImageFilter.MaxFilter(2*radius+1)))>0
        missing=old&~dilate(new);extra=new&~dilate(old)
        rows[name]={'参考墨迹匹配比例':float(1-missing.sum()/old.sum()) if old.any() else None,
                    '输出墨迹有据比例':float(1-extra.sum()/new.sum()) if new.any() else None,
                    '参考墨迹像素':int(old.sum()),'输出墨迹像素':int(new.sum())}
    return rows

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--selection-json',required=True,type=Path);cli.add_argument('--pdf-dir',required=True,type=Path)
    cli.add_argument('--out-dir',required=True,type=Path);args=cli.parse_args()
    args.out_dir.mkdir(exist_ok=True,parents=True)
    selection=json.loads(args.selection_json.read_text(encoding='utf-8'))['selected']
    summary=[];texts=[];details=[];words=[]
    for case in selection:
        ref=case['图号'];reference=Path(case['样本文件夹'])/(ref+'_设计原图.pdf')
        candidates=list(args.pdf_dir.rglob(ref+'_SHA.pdf'))
        if len(candidates)!=1:raise ValueError(f'Expected one converted file: {ref}, got {candidates}')
        output=candidates[0]
        texts.append(compare_text(reference,output))
        words.append(compare_words(reference,output))
        with fitz.open(reference) as old,fitz.open(output) as new:
            if len(old)!=len(new):raise ValueError(f'{ref}: page count differs')
            for j in range(len(old)):
                if ref not in old[j].get_text().replace(' ','') or ref not in new[j].get_text().replace(' ',''):
                    raise ValueError(f'{ref} page {j+1}: drawing number missing')
                a,b=raster(old[j],200),raster(new[j],200)
                regions=compare_page(a,b)
                row={'图号':ref,'页码':j+1,'总页数':len(old),'页数与图号校验':'通过'}
                for name in ('管道图面','材料表','图框说明','标题栏'):
                    row[name+'参考墨迹匹配比例']=regions[name]['参考墨迹匹配比例']
                    row[name+'输出墨迹有据比例']=regions[name]['输出墨迹有据比例']
                summary.append(row);details.append({'图号':ref,'页码':j+1,'regions':regions})
                if j==case['设计图页码']-1:
                    side=Image.new('RGB',(a.width*2,a.height),'white');side.paste(a,(0,0));side.paste(b,(a.width,0))
                    side.resize((2520,891)).save(args.out_dir/(ref+'_原图左_转换右.png'))
                del a,b
        print(ref,'audited',len(old) if not old.is_closed else case['设计图总页数'],'pages',flush=True)
    book=openpyxl.Workbook();sheet=book.active;sheet.title='逐页图面核对'
    headers=list(summary[0]);sheet.append(headers)
    for r in summary:sheet.append([r[k] for k in headers])
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    for c in sheet[1]:c.font=Font(bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='234C64')
    for col in sheet.columns:
        sheet.column_dimensions[col[0].column_letter].width=31
        if '比例' in col[0].value:
            for c in list(col)[1:]:c.number_format='0.00%'
    notes=book.create_sheet('指标说明');notes.append(['说明'])
    for line in ['按200dpi渲染，原图旋转属性已应用，纸张尺寸统一归一到A3。',
                 '只比较深于180灰度的墨迹；方形邻域容差0.254mm，白底不计分。',
                 '两个方向分别衡量漏画和多画；附近错误墨迹可能掩盖差异，不评价颜色与工程语义。',
                 '比例不是整图还原率，也不能证明字体、符号拓扑或签字一致。',
                 '全量逐页文字出现位置差异详见文字核对.json；拆分/合并span可能形成假差异。']:
        notes.append([line])
    notes.column_dimensions['A'].width=110
    book.save(args.out_dir/'逐页核对结果.xlsx')
    (args.out_dir/'图面核对.json').write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.out_dir/'文字核对.json').write_text(json.dumps(texts,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.out_dir/'逐词位置核对.json').write_text(json.dumps(words,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Audited',len(summary),'pages')

if __name__=='__main__':main()
