"""Render real supplied UAE weld/support/valve/flange numbers on SHA-derived pages."""
import argparse,json,math,subprocess,sys,html,hashlib
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
import xml.etree.ElementTree as ET
import fitz,openpyxl
from openpyxl.styles import Font,PatternFill,Alignment
from pypdf import PdfWriter
import _bootstrap
from convert_uae_sha_to_pdf import convert,compose,validate_pdf,publish_delivery_pdf
from uae_annotation_mapping import Drawing,read_tables

NS='http://www.w3.org/2000/svg'
COLORS={'weld':'#ff0000','support':'#0066ff','valve':'#0066ff','flange':'#0066ff','spool':'#ff0000'}
ANNOTATION_FONT='SimHei, Microsoft YaHei, Arial, sans-serif'
ANNOTATION_SCALE=1.5
CN={'weld':'焊口','support':'支架','valve':'阀门','flange':'法兰管理','spool':'管段'}

def page_obstacles(page):
    k=25.4/72;segments=[];boxes=[]
    def edge(a,b):
        values=[a.x*k,a.y*k,b.x*k,b.y*k]
        if min(values[::2])>301 or max(values[::2])<5 or min(values[1::2])>250 or max(values[1::2])<5:return
        if math.dist(values[:2],values[2:])>.03:segments.append(values)
    for d in page.get_drawings():
        for item in d['items']:
            if item[0]=='l':edge(item[1],item[2])
            elif item[0]=='re':
                r=item[1]
                for a,b in [(r.tl,r.tr),(r.tr,r.br),(r.br,r.bl),(r.bl,r.tl)]:edge(a,b)
            elif item[0]=='qu':
                q=item[1]
                for a,b in [(q.ul,q.ur),(q.ur,q.lr),(q.lr,q.ll),(q.ll,q.ul)]:edge(a,b)
            elif item[0]=='c':
                a,b,c,d=item[1:];last=a
                for j in range(1,9):
                    t=j/8;v=1-t
                    point=a*(v**3)+b*(3*v*v*t)+c*(3*v*t*t)+d*(t**3);edge(last,point);last=point
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            x,y,a,b=[v*k for v in line['bbox']]
            if x<301 and a>5 and y<250 and b>5:boxes.append([x-.2,y-.2,min(a+.2,301),b+.2])
    unique={tuple(round(v,3) for v in s):s for s in segments}
    return list(unique.values()),boxes

def format_target(t):
    t=dict(t)
    if t['kind']=='weld':t.update(width=6.*ANNOTATION_SCALE,height=6.*ANNOTATION_SCALE)
    elif t['kind']=='spool':
        em=sum(.34 if c in '-_.' else .56 if c.isdigit() else .62 for c in t['number'])
        t.update(width=(max(1.6*.7,1.6*em)+.9+.7)*ANNOTATION_SCALE,height=(1.6+.6+.7)*ANNOTATION_SCALE)
    else:t.update(width=(max(2.2*.7,2.2*.68*len(t['number']))+.9)*ANNOTATION_SCALE,height=2.8*ANNOTATION_SCALE)
    return t

def layout_job(path):
    target=path.with_name(path.stem+'_布局.json');log=path.with_suffix('.log')
    solver=_bootstrap.PARSER/'solve_real_callouts.py'
    adapter=_bootstrap.PARSER/'dense_layout_adapter.py'
    signature=hashlib.sha256(path.read_bytes()+solver.read_bytes()+(adapter.read_bytes() if adapter.exists() else b'')).hexdigest()
    if target.exists() and json.loads(target.read_text(encoding='utf-8')).get('input_algorithm_sha256')==signature:return target
    with log.open('w',encoding='utf-8') as stream:
        result=subprocess.run([sys.executable,'-u',str(_bootstrap.PARSER/'solve_real_callouts.py'),str(path),str(target)],stdout=stream,stderr=subprocess.STDOUT,timeout=900)
    if result.returncode:raise RuntimeError(f'{path.name}: {log.read_text(encoding="utf-8")[-1600:]}')
    payload=json.loads(target.read_text(encoding='utf-8'));payload['input_algorithm_sha256']=signature
    target.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    return target

def overlay(svg,placements):
    root=ET.fromstring(svg);group=ET.SubElement(root,f'{{{NS}}}g',{'id':'received-table-callouts'})
    for t in placements:
        x,y=t['anchor'];cx,cy=t['center'];ex,ey=t['leader_end'];w,h=t['width'],t['height'];color=COLORS[t['kind']]
        weld=t['kind']=='weld'
        ET.SubElement(group,f'{{{NS}}}line',{'x1':str(x),'y1':str(y-297),'x2':str(ex),'y2':str(ey-297),'stroke':color,'stroke-width':'.25' if weld else '.2','data-layer':'number-leader'})
        if weld:ET.SubElement(group,f'{{{NS}}}circle',{'cx':str(cx),'cy':str(cy-297),'r':str(w/2),'fill':'none','stroke':color,'stroke-width':'.25','data-layer':'weld-marker'})
        else:ET.SubElement(group,f'{{{NS}}}rect',{'x':str(cx-w/2),'y':str(cy-h/2-297),'width':str(w),'height':str(h),'fill':'none','stroke':color,'stroke-width':'.2','data-layer':'management-marker'})
        if t['kind']=='spool':
            inset=.35*ANNOTATION_SCALE
            ET.SubElement(group,f'{{{NS}}}rect',{'x':str(cx-w/2+inset),'y':str(cy-h/2+inset-297),'width':str(w-2*inset),'height':str(h-2*inset),'fill':'none','stroke':color,'stroke-width':'.2','data-layer':'spool-marker-inner'})
        size=(1.6 if t['kind']=='spool' else 2.2)*ANNOTATION_SCALE*(min(1.,4/max(1,len(t['number']))) if weld else 1.)
        text=ET.SubElement(group,f'{{{NS}}}text',{'x':str(cx),'y':str(cy-297),'font-size':str(size),'font-family':ANNOTATION_FONT,'font-weight':'700','text-anchor':'middle','dominant-baseline':'central','fill':color})
        text.text=t['number']
    return ET.tostring(root,encoding='unicode')

def render_annotated(case,drawing,work,output,layouts):
    ref=case['图号'];jobs=[]
    for p in drawing.pages:
        svg,_=compose(drawing.streams,p['sheet']);layout=layouts[(ref,p['number'])]
        svg=overlay(svg,layout['placements']);path=work/ref/f'标注页_{p["number"]:02}.svg';path.parent.mkdir(exist_ok=True)
        path.write_text(svg,encoding='utf-8');jobs.append({'input':str(path.resolve()),'output':str(path.with_suffix('.pdf').resolve())})
    manifest=work/ref/'打印任务.json';manifest.write_text(json.dumps(jobs),encoding='utf-8')
    result=subprocess.run(['node',str(_bootstrap.PARSER/'render_svg_pdf.mjs'),'--manifest',str(manifest.resolve())],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180+30*len(jobs))
    if result.returncode:raise RuntimeError(result.stderr)
    writer=PdfWriter()
    for job in jobs:validate_pdf(Path(job['output']));writer.append(job['output'])
    writer.add_metadata({'/Title':ref+' - Received table annotations','/Subject':'Real supplied weld, spool and management numbers. Transparent markers and straight leaders.'})
    tmp=work/ref/'完整标注.pdf';writer.write(tmp);writer.close();validate_pdf(tmp,len(jobs))
    target=output/(ref+'_SHA_编号标注.pdf');publish_delivery_pdf(tmp,target);validate_pdf(target,len(jobs))
    # Verify label strings after PDF production, including page ownership.
    with fitz.open(target) as pdf:
        for p in drawing.pages:
            text=pdf[p['number']-1].get_text()
            for t in layouts[(ref,p['number'])]['placements']:
                if t['number'] not in text:raise ValueError(f'{ref} page {p["number"]}: missing printed label {t["number"]}')
    return target

def workbook(path,audit,layouts):
    wb=openpyxl.Workbook();wb.remove(wb.active)
    aggregate=[]
    for ref in sorted({r['图号'] for r in audit}):
        r={'图号':ref}
        for kind in CN:
            subset=[a for a in audit if a['图号']==ref and a['类别']==kind]
            r[CN[kind]+'编号数']=len({a['编号'] for a in subset});r[CN[kind]+'标注处数']=sum(a['状态']=='已定位' for a in subset)
        r['标注图页数']=sum(k[0]==ref for k in layouts);aggregate.append(r)
        r['新增引线交叉组数']=sum(len(v.get('routed_leader_crossings',[])) for k,v in layouts.items() if k[0]==ref)
        r['引线跨主体线段事件数']=sum(t['source_crossings'][0] for k,v in layouts.items() if k[0]==ref for t in v['placements'])
        r['引线与原文字框相交事件数']=sum(t['source_crossings'][1] for k,v in layouts.items() if k[0]==ref for t in v['placements'])
    detail=[]
    for r in audit:
        notes=[]
        for (ref,num),layout in layouts.items():
            if ref!=r['图号']:continue
            for i,t in enumerate(layout['placements']):
                if (t['kind'],t['number'])!=(r['类别'],r['编号']):continue
                for a,b in layout.get('routed_leader_crossings',[]):
                    if i in (a,b):notes.append('与'+layout['placements'][b if i==a else a]['number']+'引线交叉')
                if t['source_crossings'][0]:notes.append('跨主体线段事件'+str(t['source_crossings'][0])+'次')
                if t['source_crossings'][1]:notes.append('与原文字框相交'+str(t['source_crossings'][1])+'次')
        detail.append({**r,'类别':CN[r['类别']],'引线说明':'；'.join(notes)})
    for name,data in [('标注汇总',aggregate),('编号与图页',detail)]:
        sh=wb.create_sheet(name);headers=list(dict.fromkeys(k for row in data for k in row));sh.append(headers)
        for r in data:sh.append([json.dumps(r.get(h,''),ensure_ascii=False) if isinstance(r.get(h),(list,dict)) else r.get(h,'') for h in headers])
        sh.freeze_panes='A2';sh.auto_filter.ref=sh.dimensions
        for cell in sh[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='234C64')
        for col in sh.columns:sh.column_dimensions[col[0].column_letter].width=40 if '方法' in col[0].value else 27
    wb.save(path)

def main():
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('--input-dir',type=Path,required=True);cli.add_argument('--output-dir',type=Path,help='Defaults to the input selection directory');cli.add_argument('--workers',type=int,default=2);args=cli.parse_args()
    inp=args.input_dir.resolve();out=(args.output_dir or inp).resolve();out.mkdir(exist_ok=True,parents=True)
    native=out/'原图转换PDF';annotated=out/'编号标注PDF';work=out/'过程记录'
    for p in (native,annotated,work):p.mkdir(exist_ok=True)
    cases=json.loads((inp/'分析结果.json').read_text(encoding='utf-8'))['selected']
    snapshots=work/'输入表快照';snapshots.mkdir(exist_ok=True);table_hashes={}
    for name in ('焊口表.csv','支架表.csv','阀门表.csv','法兰表.csv'):
        payload=(inp/name).read_bytes();(snapshots/name).write_bytes(payload);table_hashes[name]=hashlib.sha256(payload).hexdigest()
    tables=read_tables(snapshots)
    drawings={};audit=[];jobs=[]
    for case in cases:
        ref=case['图号'];folder=Path(case['样本文件夹']);d=Drawing(folder/(ref+'.pcf'),folder/(ref+'.sha'));drawings[ref]=d
        mapped,rows=d.map_tables(ref,tables);audit+=rows
        if any(r['状态']!='已定位' for r in rows):raise ValueError(f'{ref}: unresolved table rows; refusing silent omission')
        source=convert(d.sha,d.sha.parent,native,True)
        print('SHA converted:',ref,source['page_count'],'pages',flush=True)
        with fitz.open(source['pdf']) as doc:
            for p in d.pages:
                targets=[format_target(t) for t in mapped if t['sheet']==p['sheet']]
                segments,boxes=page_obstacles(doc[p['number']-1])
                spec={'targets':targets,'segments':segments,'boxes':boxes,'region':[6,6,300,249]}
                primary=[]
                for component in d.components:
                    if component['omitted'] or component['type'] in ('SUPPORT','WELD'):continue
                    for g in d.geometries(component['uci']):
                        if g['sheet']==p['sheet']:primary.extend(g['segments'])
                spec['blocking_segments']=primary
                target=work/f'{ref}_页{p["number"]:02}.json'
                # Never reuse stale layouts if inputs or obstacles have changed.
                content=json.dumps(spec,ensure_ascii=False,indent=2)
                if target.exists() and target.read_text(encoding='utf-8')!=content:
                    cached=target.with_name(target.stem+'_布局.json')
                    if cached.exists():cached.rename(cached.with_name(cached.stem+'_旧_'+hashlib.sha256(cached.read_bytes()).hexdigest()[:8]+'.json'))
                target.write_text(content,encoding='utf-8');jobs.append((ref,p['number'],target))
    layouts={};failures=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(layout_job,path):(ref,num,path) for ref,num,path in jobs}
        for future in as_completed(futures):
            ref,num,path=futures[future]
            try:
                result=future.result();layout=json.loads(result.read_text(encoding='utf-8'));layouts[(ref,num)]=layout
                print('Layout:',ref,'page',num,'labels',len(layout['placements']),'pair conflicts',len(layout['pair_conflicts']),flush=True)
            except Exception as e:failures.append(str(e));print('LAYOUT ERROR',ref,num,str(e),flush=True)
    if failures:
        (out/'布局失败.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf-8')
        raise RuntimeError('Some pages need layout repair; see 布局失败.json')
    changed=[name for name,digest in table_hashes.items() if hashlib.sha256((inp/name).read_bytes()).hexdigest()!=digest]
    if changed:raise RuntimeError('输入表在计算中发生变化，需重新读取：'+','.join(changed))
    outputs=[];selection=fitz.open()
    for case in cases:
        ref=case['图号'];target=render_annotated(case,drawings[ref],work,annotated,layouts);outputs.append(str(target))
        with fitz.open(target) as doc:selection.insert_pdf(doc,from_page=case['设计图页码']-1,to_page=case['设计图页码']-1)
        print('Annotated PDF:',target.name,flush=True)
    selection.save(out/'五张选定图页_真实编号标注合册.pdf');selection.close()
    workbook(out/'真实编号标注清单.xlsx',audit,layouts)
    report={'files':outputs,'pages':len(jobs),'labels':dict(Counter(r['类别'] for r in audit)),
            'unmapped':0,'pair_conflicts':sum(len(v['pair_conflicts']) for v in layouts.values()),
            'pair_conflicts_scope':'最终单段直线引线与编号框之间的冲突检查；原图线条及文字相交另计',
            'leader_type':'straight','annotation_style':'chengda_indonesia',
            'font_scale':ANNOTATION_SCALE,'marker_background':'transparent','white_leader_halo':False,
            'routed_leader_crossings':sum(len(v.get('routed_leader_crossings',[])) for v in layouts.values()),
            'primary_line_crossing_events':sum(t['source_crossings'][0] for v in layouts.values() for t in v['placements']),
            'source_text_crossing_events':sum(t['source_crossings'][1] for v in layouts.values() for t in v['placements']),
            'pipe_segment_labels':sum(r['类别']=='spool' for r in audit),
            'unique_pipe_segments':len({(r['图号'],r['编号']) for r in audit if r['类别']=='spool'}),
            'pipe_segment_reason':'按新版焊口表的正式管段号标注，跨页管段各页重复显示同号',
            'csv_sha256':table_hashes,
            'root_clearance_mm':3.,'audit':audit}
    (out/'标注验证.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'标注说明.txt').write_text('按新版焊口表的真实焊口号及正式管段号标注；跨页管段在各页重复显示同号，不另编新号。\n成达印尼风格：焊口红色圆圈、管段红色双矩形框、支架阀门法兰管理蓝色矩形；全部采用单段直线引线，透明无填充、无白色衬底，字号及边框沿用1.5倍。\n管段优先标在组成管子两端焊点的中点；没有可核实直管中点时，关联该管段的预制焊口图元。所属焊口和两侧元件编号见清单。\n编号框避让原图文字和线条；新增标注间冲突与原图线段/文字框相交分别统计。根部3mm为局部符号引出区。\n输入CSV快照及SHA256保存在过程记录与验证JSON，避免后续覆盖源表后丢失版本依据。\n',encoding='utf-8-sig')
    print(json.dumps({k:v for k,v in report.items() if k!='audit'},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
