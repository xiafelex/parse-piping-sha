"""Isolated configurable SHA/PCF template demo, never uses PDF as geometry.

Default verified single-sheet example: 4-213-52-0212-HCX-PAA.
Weld identity is real UCI/PCF ID; display number, S/F and spool tags are virtual.
Preserves SHA routing/dimensions. Does not claim to recreate a fabrication layout.
"""
from pathlib import Path
from collections import defaultdict, Counter
from copy import deepcopy
import argparse
import csv
import html
import json
import math
import re
import struct
import sys
import subprocess
import textwrap
import xml.etree.ElementTree as ET
import fitz
from pypdf import PdfReader
from analyze_iso_split import read_sha_streams, forward_owned_dynamic_graphics
from sha_native import preview, lines
from sha_contours import records
from compare_three_format_fields import pcf_read
from benchmark_pod_incremental_value import sha_weld_baseline

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)

def attr(block, key, default=''):
    return block['attributes'].get(key, [default])[0]

def owner_roles(data, streams, blocks):
    children = defaultdict(list)
    for off, kind, record in records(data):
        if len(record) >= 14:
            ref, parent = struct.unpack_from('<II', record, 6)
            children[parent].append(ref)
    bindings = forward_owned_dynamic_graphics(streams.get('Unclustered Dynamic Attributes', b''))
    roles = defaultdict(set)
    for block in blocks:
        for binding in bindings.get(attr(block, 'UCI'), []):
            todo = [binding['graphic_ref']]
            seen = set()
            while todo:
                ref = todo.pop()
                if ref in seen:
                    continue
                seen.add(ref)
                roles[ref].add(block['type'])
                todo.extend(children.get(ref, []))
    # Styling ownership is not automatically a centerline classification.
    return {r['offset']: sorted(roles.get(r['child_ref'], set())) for r in lines(data)}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', default='4-213-52-0212-HCX-PAA')
    ap.add_argument('--rules', type=Path, default=HERE/'custom_style_rules.json')
    args = ap.parse_args()
    cfg = json.loads(args.rules.read_text(encoding='utf-8'))
    if not cfg['virtual_data']:
        raise ValueError('This demonstration only supports explicitly virtual numbering.')
    project = ROOT/'阿联酋项目'
    sha = next(project.glob('30201*/**/'+args.base+'.sha'))
    pcf = sha.with_name(args.base+'-pcf.pcf')
    streams = read_sha_streams(sha)
    if {s for s in streams if re.fullmatch(r'Sheet\d+', s)} != {'Sheet6', 'Sheet39817'}:
        raise ValueError('This template demo supports one drawing sheet; refusing to drop other sheets.')
    output = project/'SHA自定义出图风格标注'
    work = output/'验证过程'
    work.mkdir(parents=True, exist_ok=True)
    blocks, materials, units = pcf_read(pcf)
    if units.get('UNITS-CO-ORDS') != 'MM' or units.get('UNITS-BORE') != 'INCH':
        raise ValueError('This demo requires MM coordinates and INCH bores.')
    baseline, _ = sha_weld_baseline(pcf, sha)
    if any(len(r['sha_circle_candidates']) != 1 for r in baseline):
        raise ValueError('Unresolved or ambiguous weld anchors; no fabricated coordinates allowed.')
    svg, coverage = preview(streams['Sheet6'], streams)
    source = ET.fromstring(svg)
    group = next(c for c in source if c.tag.endswith('}g'))
    roles = owner_roles(streams['Sheet6'], streams, blocks)
    rule_counts = Counter()
    primitives = []
    for el in list(group):
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 'text':
            el.set('font-family', 'Arial')
            el.set('fill', '#111111')
        elif tag != 'image':
            if el.get('stroke', 'none') != 'none':
                el.set('stroke', '#111111')
            if el.get('fill', 'none') != 'none':
                el.set('fill', '#111111')
            if el.get('stroke-width'):
                semantic = roles.get(int(el.get('data-offset', '-1')), [])
                known = [v for v in semantic if v in cfg['line_width_mm'] and v != 'OTHER']
                role = known[0] if len(known) == 1 else 'OTHER'
                el.set('stroke-width', str(cfg['line_width_mm'][role]))
                rule_counts[role] += 1
                if role != 'OTHER':
                    el.set('data-pcf-role', role)
                primitives.append({'sha_offset':el.get('data-offset'), 'roles':semantic, 'applied':role})
        if tag == 'circle':
            x,y = float(el.get('cx')),float(el.get('cy'))
            if any(math.hypot(x-r['sha_circle_candidates'][0]['x_mm'], y+r['sha_circle_candidates'][0]['y_mm']) < .02 for r in baseline):
                group.remove(el)
    s = cfg['drawing_scale']
    cx, cy, cw, ch = cfg['source_clip_mm']
    ox, oy = cfg['drawing_origin_mm']
    def point(x,y):
        return ox+(x-cx)*s, oy+(y-cy)*s
    parts = [f'<svg xmlns="{NS}" width="420mm" height="297mm" data-page-width-mm="420" data-page-height-mm="297" viewBox="0 0 420 297">',
             '<rect width="420" height="297" fill="white"/>',
             f'<defs><clipPath id="drawing"><rect x="{cx}" y="{cy}" width="{cw}" height="{ch}"/></clipPath></defs>',
             f'<g transform="translate({ox-cx*s} {oy-cy*s}) scale({s})"><g clip-path="url(#drawing)">{ET.tostring(group,encoding="unicode")}</g></g>']
    def line(x,y,a,b,width=.18):
        parts.append(f'<path d="M{x},{y} L{a},{b}" fill="none" stroke="#111" stroke-width="{width}"/>')
    def rect(x,y,w,h,width=.18,fill='none'):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" stroke="#111" stroke-width="{width}" fill="{fill}"/>')
    def text(x,y,t,size=2,anchor='start',bold=False):
        parts.append(f'<text x="{x}" y="{y}" font-family="Arial" font-size="{size}" text-anchor="{anchor}" font-weight="{"bold" if bold else "normal"}" fill="#111">{html.escape(str(t))}</text>')
    rect(5,5,410,287,.45)
    rect(8,8,404,282,.22)
    line(288,8,288,290,.25)
    line(8,244,288,244,.25)
    # Grid title & table style adapted from the supplied fabrication-style PDF.
    text(350,13,'BILL OF MATERIALS - PCF',2.8,'middle',True)
    text(350,17,'REF = PCF MATERIAL ID; quantities from supplied components',1.55,'middle')
    line(288,20,412,20)
    text(290,23,'REF',1.7); text(300,23,'DESCRIPTION / ITEM CODE',1.7); text(409,23,'QTY',1.7,'end')
    grouped = defaultdict(list)
    for block in blocks:
        mid = attr(block,'MATERIAL-IDENTIFIER')
        if mid in materials and block['type'] != 'WELD' and attr(block,'MATERIAL-LIST').upper() != 'EXCLUDE':
            grouped[mid].append(block)
    y = 26
    for mid, members in sorted(grouped.items(), key=lambda kv:int(kv[0])):
        material = materials[mid]
        description = material.get('DESCRIPTION','')
        descrows = textwrap.wrap(description, 102)
        if all(b['type']=='PIPE' for b in members):
            qty = f"{sum(float(attr(b,'CUT-PIECE-LENGTH','0')) for b in members)/1000:.3f}m"
        elif all(b['type']=='BOLT' for b in members):
            qty = str(sum(int(attr(b,'BOLT-QUANTITY','1')) for b in members))
        else:
            qty = str(len(members))
        text(291,y,mid,1.7);text(410,y,qty,1.7,'end')
        for row in descrows:
            text(299,y,row,1.45);y += 1.9
        text(299,y,material.get('ITEM-CODE',''),1.5);y+=2.7
    if y > 109:
        raise ValueError('BOM overflows the template; enlarge/reflow before exporting.')
    line(288,112,412,112,.25)
    text(318,116,'JOINT SUMMARY - DEMO',2.2,'middle',True)
    text(380,116,'CUT LENGTH - PCF',2.2,'middle',True)
    line(350,112,350,209);line(288,119,412,119)
    text(291,123,'TAG',1.8);text(308,123,'S/F',1.8);text(320,123,'SIZE',1.8);text(334,123,'PCF ID',1.8)
    text(353,123,'DEMO ID',1.8);text(374,123,'mm',1.8);text(390,123,'PCF ID',1.8)
    blocks_by_id={attr(b,'COMPONENT-IDENTIFIER'):b for b in blocks if attr(b,'COMPONENT-IDENTIFIER')}
    # Extract obstacles solely from the SHA-derived SVG, never the reference PDF.
    occupied=[]
    obstacle_segments=[]
    with fitz.open(stream=svg.encode(),filetype='svg') as doc:
        pdfbytes=doc.convert_to_pdf()
    with fitz.open(stream=pdfbytes,filetype='pdf') as doc:
        page=doc[0]
        for block in page.get_text('dict')['blocks']:
            if 'lines' not in block:continue
            for row in block['lines']:
                for span in row['spans']:
                    x0,y0,x1,y1=span['bbox']
                    xa,ya=point(x0/page.rect.width*420,y0/page.rect.height*297-297)
                    xb,yb=point(x1/page.rect.width*420,y1/page.rect.height*297-297)
                    if xb<287 and yb<240:occupied.append(fitz.Rect(xa,ya,xb,yb))
        def mapped(pt):
            return point(pt.x/page.rect.width*420,pt.y/page.rect.height*297-297)
        def segment(a,b):
            x,y=mapped(a);xx,yy=mapped(b)
            if min(x,xx)>=8 and max(x,xx)<=287 and min(y,yy)>=8 and max(y,yy)<=228:
                if math.hypot(xx-x,yy-y)>.08:obstacle_segments.append([x,y,xx,yy])
        for path in page.get_drawings():
            for item in path['items']:
                if item[0]=='l':segment(item[1],item[2])
                elif item[0]=='re':
                    r=item[1]
                    for a,b in ((r.tl,r.tr),(r.tr,r.br),(r.br,r.bl),(r.bl,r.tl)):segment(a,b)
                elif item[0]=='c':
                    a,b,c,d=item[1:];last=a
                    for j in range(1,13):
                        t=j/12;u=1-t
                        pt=a*(u**3)+b*(3*u*u*t)+c*(3*u*t*t)+d*(t**3)
                        segment(last,pt);last=pt
    ordered=sorted(baseline,key=lambda r:int(r['pcf_id']))
    input_layout={'points':[point(r['sha_circle_candidates'][0]['x_mm'],-r['sha_circle_candidates'][0]['y_mm']) for r in ordered],
                  'segments':obstacle_segments,'boxes':[list(b) for b in occupied], 'radius':cfg['weld']['label_radius_mm']}
    request=work/'布局输入.json';request.write_text(json.dumps(input_layout),encoding='utf-8')
    result=work/'布局碰撞核查.json'
    subprocess.run([sys.executable,str(HERE/'solve_custom_callouts.py'),str(request),str(result)],check=True)
    layout=json.loads(result.read_text(encoding='utf-8'))
    weld_rows=[]
    for i,row in enumerate(sorted(baseline,key=lambda r:int(r['pcf_id'])),1):
        p=row['sha_circle_candidates'][0]
        assert p['sheet']=='Sheet6'
        x,y=point(p['x_mm'],-p['y_mm'])
        assert 8<x<288 and 8<y<244
        sf='F' if i%cfg['weld']['virtual_field_every']==0 else 'S'
        tag=f'{sf}-{i:02d}'
        if sf=='S':
            parts.append(f'<circle cx="{x}" cy="{y}" r="0.65" fill="black"/>')
        else:
            line(x-1,y-1,x+1,y+1,.35);line(x-1,y+1,x+1,y-1,.35)
        radius=cfg['weld']['label_radius_mm']
        lx,ly,endx,endy=layout['placements'][i-1]
        line(x,y,endx,endy,.12)
        parts.append(f'<circle cx="{lx}" cy="{ly}" r="{radius}" fill="white" stroke="#111" stroke-width="0.18"/>')
        text(lx,ly+.6,tag,cfg['weld']['label_font_mm'],'middle')
        b=blocks_by_id[row['pcf_id']]
        size=attr(b,'WELD-ATTRIBUTE2','?')
        yy=127+(i-1)*4.6
        text(291,yy,tag,1.9);text(309,yy,sf,1.9);text(320,yy,size+' in',1.9);text(339,yy,row['pcf_id'],1.9,'middle')
        weld_rows.append({'virtual_tag':tag,'virtual_sf':sf,'pcf_id':row['pcf_id'],'uci':row['uci'], 'sheet':p['sheet'],'sha_circle_ref':p['ref'],'sha_x_mm':p['x_mm'],'sha_y_mm':p['y_mm'],'output_x_mm':x,'output_y_mm':y,'label_x_mm':lx,'label_y_mm':ly,'virtual':True})
    pipes=[b for b in blocks if b['type']=='PIPE']
    for i,b in enumerate(pipes,1):
        yy=127+(i-1)*5
        text(354,yy,f'D-C{i:02d}',1.9);text(374,yy,attr(b,'CUT-PIECE-LENGTH','?'),1.9);text(397,yy,attr(b,'COMPONENT-IDENTIFIER'),1.9,'middle')
    text(353,170,'Lengths: PCF CUT-PIECE-LENGTH.',1.75)
    text(353,174,'D-Cxx: virtual display IDs.',1.75)
    text(353,178,'No added fabrication allowance.',1.75)
    text(353,182,'End preparation: not inferred.',1.75)
    text(291,193,'S/F and weld tags are virtual; UCI / anchors are real.',1.75)
    text(291,198,'Spool boxes below are style examples, not topology.',1.65)
    line(288,200,412,200)
    text(350,206,'DEMO / NOT FOR FABRICATION',2.4,'middle',True)
    # Explicitly isolated legend examples instead of inventing fabricated spool membership.
    line(8,230,288,230)
    text(12,235,'SYMBOL RULES',2.1,bold=True)
    parts.append('<circle cx="55" cy="234" r="0.65" fill="black"/>')
    text(59,235,'SHOP (S)',1.9)
    line(88,233,90,235,.35);line(88,235,90,233,.35);text(93,235,'FIELD (F)',1.9)
    rect(129,232,41,6,.16,'white');text(149.5,236,'DEMO-SP01',2,'middle')
    text(175,236,'Spool box style only; no fabrication grouping assigned.',1.8)
    text(12,241,cfg['notice'],1.8,bold=True)
    # Reuse genuine decoded SHA frame content, not image crops of the reference PDFs.
    frame_svg,_=preview(streams['Sheet39817'],streams)
    frame_group=next(c for c in ET.fromstring(frame_svg) if c.tag.endswith('}g'))
    original_groups=ET.tostring(frame_group,encoding='unicode')+ET.tostring(next(c for c in source if c.tag.endswith('}g')),encoding='unicode')
    for name,clip,dest in [('notes',(6,-42,296,44),(8,244,280,46)),('title',(302,-70,112,72),(288,210,124,80))]:
        a,b,w,h=clip;dx,dy,dw,dh=dest
        parts.append(f'<defs><clipPath id="{name}"><rect x="{a}" y="{b}" width="{w}" height="{h}"/></clipPath></defs>')
        parts.append(f'<g transform="translate({dx} {dy}) scale({dw/w} {dh/h}) translate({-a} {-b})"><g clip-path="url(#{name})">{original_groups}</g></g>')
    # Table grid like the supplied marked-up isometric.
    for x in (306,316,332):line(x,119,x,190,.12)
    for x in (370,387):line(x,119,x,166,.12)
    line(288,190,350,190,.12);line(350,166,412,166,.12)
    parts.append('</svg>')
    custom=''.join(parts)
    svgpath=work/(args.base+'_自定义模板.svg')
    svgpath.write_text(custom,encoding='utf-8')
    pdfpath=output/(args.base+'_SHA自定义风格_V2_避碰布局.pdf')
    # Same browser renderer used by the existing UAE PDF delivery workflow.
    subprocess.run(['node',str(HERE/'render_svg_pdf.mjs'),str(svgpath),str(pdfpath)],check=True,cwd=HERE)
    reader=PdfReader(pdfpath,strict=True)
    assert len(reader.pages)==1 and not reader.is_encrypted
    assert abs(float(reader.pages[0].mediabox.width)-420*72/25.4)<2
    with fitz.open(pdfpath) as pdf:
        assert 'VIRTUAL' in pdf[0].get_text()
        for row in weld_rows:assert row['virtual_tag'] in pdf[0].get_text()
        pdf[0].get_pixmap(matrix=fitz.Matrix(1.2,1.2)).save(work/(args.base+'_自定义模板预览.png'))
    with (work/'焊口映射_虚拟值.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(weld_rows[0]));writer.writeheader();writer.writerows(weld_rows)
    manifest={'sha':str(sha),'pcf':str(pcf),'reference_pdf_used_for_geometry':False,'pod_used':False,'virtual_fields':['weld_tag','S/F','cut_display_id','spool_legend'], 'welds':len(weld_rows),'line_style_counts':dict(rule_counts),'sha_decode':coverage,'layout_audit':layout,'primitive_style_rules':primitives,'rules':cfg,'limitations':['SHA decode remains partial','unchanged SHA routing; no new topology layout','global collision-free solution under measured SHA obstacles; manual review still required','spool legend is not a division plan','single-sheet sample only']}
    (work/'自定义出图验证记录.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pdf':str(pdfpath),'welds':len(weld_rows),'materials':len(grouped),'style_counts':dict(rule_counts)},ensure_ascii=False))

if __name__=='__main__':
    main()
