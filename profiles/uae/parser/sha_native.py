"""UAE SHA evidence reader: actual uint32 graphic IDs and metre coordinates.

This supplements the isolated legacy renderer; no POD/PCF input is used to
identify SHA line records. It deliberately does not claim all SHA types decoded.
"""
import math
import re
import struct
import html
from analyze_iso_split import read_sha_streams
from analyze_sha_pages import text_objects


def lines(data):
    from sha_contours import records
    result = []
    # Keep the public diagnostic API for one isolated line record. Whole
    # clusters use declared boundaries and never search inside other payloads.
    source=([(0,0x18,data)] if len(data)==56 else []) if data.startswith(bytes.fromhex('180032000000')) else records(data)
    for off,kind,record in source:
        if kind!=0x18 or len(record)!=56:continue
        if off + 56 > len(data):
            continue
        _, _, _, child, owner, style, zero, flags = struct.unpack_from('<HHHIIIHI', data, off)
        xy = struct.unpack_from('<4d', data, off + 24)
        # 0x10 occurs on the four WBS frame edges. Their coordinates and
        # visibility are independently confirmed by the reference drawings.
        if zero not in (0, 0x10) or not all(math.isfinite(x) and abs(x) < 10 for x in xy):
            continue
        result.append({'offset': off, 'child_ref': child, 'owner_ref': owner, 'style_ref': style, 'flags': flags, 'record_flags': zero, 'xy_metres': xy})
    return result


def metadata(streams):
    result = {}
    for name, data in streams.items():
        if name.endswith('AppObject'):
            result[name] = [m.group().decode('utf-16le') for m in re.finditer(rb'(?:[ -~]\x00){5,}', data)]
        elif name.endswith('DocVersion3'):
            result[name] = [m.group().decode('ascii') for m in re.finditer(rb'[ -~]{5,}', data)]
        elif name.startswith('TaggedTxtData/'):
            try: result[name] = data.decode('utf-8').rstrip('\x00')
            except UnicodeError: pass
    return result


def inspect(path):
    from sha_to_svg_prototype import standalone_arcs
    streams = read_sha_streams(path)
    sheets = {}
    for name, data in streams.items():
        if not re.fullmatch(r'Sheet\d+', name):
            continue
        texts = text_objects(data)
        sheets[name] = {'lines': lines(data), 'text_candidates': texts, 'arcs': standalone_arcs(data), 'byte_length': len(data)}
    return {'metadata': metadata(streams), 'sheets': sheets, 'stream_sizes': {k: len(v) for k, v in streams.items()},
            'uci_strings': sorted({m.group().decode('ascii') for b in streams.values() for m in re.finditer(rb'\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}', b)}),
            'complete_decode': False, 'coordinate_units': 'metres (verified against POD integer drawing points / 1e6)'}


def preview(data, streams=None):
    """Consistent native metre coordinates. No legacy PSM/font-size heuristics."""
    from sha_to_svg_prototype import text_records, standalone_arcs
    from sha_bindings import bound_text
    from sha_graphics import native_texts, line_styles, text_formats, embedded_images, circle_primitives, polyline_primitives
    from sha_contours import embedded_contours, filled_contours, embedded_strokes, embedded_instances
    styles=line_styles((streams or {}).get('StyleCluster',b''))
    formats=text_formats((streams or {}).get('StyleCluster',b''))
    is_frame=streams is not None and data==streams.get('Sheet39817')
    parts=[]
    native_lines=lines(data)
    observed_styles={line['style_ref'] for line in native_lines}
    rejected_styles=0
    for line in native_lines:
        x,y,a,b=[v*1000 for v in line['xy_metres']]
        style=styles.get(line['flags'],{'width':.15,'color':'#111'})
        parts.append(f'<line x1="{x}" y1="{-y}" x2="{a}" y2="{-b}" stroke="{style["color"]}" stroke-width="{style["width"]}" data-offset="{line["offset"]}"/>')
    arc_count=0
    polylines=polyline_primitives(data)
    for polyline in polylines:
        style=styles.get(polyline['style_ref'],{'width':.15,'color':'#111'})
        points=' '.join(f'{x*1000},{-y*1000}' for x,y in polyline['points'])
        parts.append(f'<polyline points="{points}" stroke="{style["color"]}" stroke-width="{style["width"]}" fill="none" data-offset="{polyline["offset"]}"/>')
    circles=circle_primitives(data)
    for circle in circles:
        style=styles.get(circle['style_ref'],{'width':.15,'color':'#111'})
        parts.append(f'<circle cx="{circle["x"]}" cy="{circle["y"]}" r="{circle["radius"]}" stroke="{style["color"]}" stroke-width="{style["width"]}" fill="none"/>')
    for arc in standalone_arcs(data):
        cx,cy,r=[arc[k]/16.8 for k in ('center_x','center_y','radius')]
        start,end=arc['start_angle'],arc['end_angle']
        sweep=(end-start)%(2*math.pi) if arc['direction']==1 else -((start-end)%(2*math.pi))
        x1,y1=cx+r*math.cos(start),-cy-r*math.sin(start)
        x2,y2=cx+r*math.cos(end),-cy-r*math.sin(end)
        style=styles.get(arc['style_ref'],{'width':.15,'color':'#111'})
        parts.append(f'<path d="M {x1} {y1} A {r} {r} 0 {int(abs(sweep)>math.pi)} {int(sweep<0)} {x2} {y2}" stroke="{style["color"]}" stroke-width="{style["width"]}"/>')
        arc_count+=1
    text_count=0; seen=set()
    for t in ([] if streams else text_records(data)):
        if any(marker in t['text'] for marker in ('<?xml', '<body>', '<intstgxml', '</body>')):continue
        x,y=t['x'],t['y'];dx,dy=t['direction_x'],t['direction_y']
        if not (0<=x<=0.43 and 0<=y<=0.31 and 0.8<=math.hypot(dx,dy)<=1.2 and t['text']):continue
        # Conservative preview only: printable binary fragments can inherit an
        # adjacent valid transform. Require independent style-reference evidence.
        # A genuine text-only style may be omitted; candidates remain in evidence.
        if t['style_ref'] not in observed_styles:
            rejected_styles+=1
            continue
        key=(t['text'],round(x,9),round(y,9),round(dx,9),round(dy,9))
        if key in seen:continue
        seen.add(key)
        angle=-math.degrees(math.atan2(dy,dx));x*=1000;y*=-1000
        parts.append(f'<text x="{x}" y="{y}" transform="rotate({angle} {x} {y})" font-size="1.6" font-family="Arial" stroke="none" fill="#111">{html.escape(t["text"])}</text>')
        text_count+=1
    frame_texts=native_texts(data,None) if streams else []
    for t in frame_texts:
        fmt=formats.get(t['format_ref'],{'height':1.6,'family':'Arial','weight':'normal'})
        size=fmt['height'];x,y=t['x']*1000,-t['y']*1000
        # Mode 0x0d declares cap height rather than CSS em size. Ratios below
        # are calibrated for the current Windows font substitutions.
        if fmt.get('height_mode')==0x0d:
            size*=1.54 if fmt['family']=='Times New Roman' else 1.42
        elif fmt.get('height_mode')==0x0b:
            size*=.9
        alignment=t['alignment_flags']
        anchor='middle' if alignment is not None and alignment&0x10 else 'start'
        baseline=size*(0 if alignment==0 else .715 if alignment in (7,23) else .34 if alignment in (3,5) else .8)
        angle=-math.degrees(math.atan2(t['direction_y'],t['direction_x']))
        if t.get('text_box_border'):
            # Alignment 0 anchors the baseline; alignment 1 anchors the top.
            # The latter occurs on the two reinforcement-pad note boxes.
            height=t['text_box_height_mm']
            top=y if alignment==1 else y-height*.8
            parts.append(f'<rect x="{x}" y="{top}" width="{t["text_box_width_mm"]}" height="{height}" transform="rotate({angle} {x} {y})" stroke="#000" stroke-width="0.35" fill="none"/>')
        # In the observed Shape2D paragraphs each tab advances one space.
        # SVG normalizes raw tab characters away at line starts.
        rows=t['text'].replace('\t',' ').replace('\r\n','\n').replace('\r','\n').split('\n')
        rows=[row for row in rows if row.strip()]
        if len(rows)>1 and fmt.get('height_mode')==0x0b:
            baseline=size*.87
        line_advance=fmt['height'] if fmt.get('height_mode')==0x0b else size*1.1
        if len(rows)>1 and fmt.get('paragraph_alignment')==0x100 and t['text_box_width_mm']:
            x+=t['text_box_width_mm']/2
            anchor='middle'
        # Fit text to its declared SHA text-box width, using
        # standard-font metrics (font substitution remains approximate).
        lengths=None
        if rows and t['text_box_width_mm']:
            import fitz
            font=fitz.Font(fontname=('tibo' if fmt['weight']=='bold' else 'tiro') if fmt['family']=='Times New Roman' else ('hebo' if fmt['weight']=='bold' else 'helv'))
            lengths=[font.text_length(row,fontsize=size) for row in rows]
            drawn_width=max(lengths)*fmt.get('width_scale',1.0)
            factor=min(1,t['text_box_width_mm']/drawn_width) if drawn_width else 1
            # Only constrain overflow. Forcing every otherwise fitting line
            # to standard-font widths defeats the selected Windows font's
            # own metrics and kerning, widening the project title.
            lengths=[v*factor for v in lengths] if factor<1 else None
        spans=''
        for i,row in enumerate(rows):
            length_attr=f' textLength="{lengths[i]}" lengthAdjust="spacingAndGlyphs"' if lengths and lengths[i]>0 else ''
            spans+=f'<tspan x="{x}" dy="{0 if i==0 else line_advance}"{length_attr}>{html.escape(row)}</tspan>'
        width_scale=fmt.get('width_scale',1.0)
        transform=f'rotate({angle} {x} {y}) translate({x} {y}) scale({width_scale} 1) translate({-x} {-y})'
        parts.append(f'<text xml:space="preserve" style="white-space:pre" x="{x}" y="{y+baseline}" text-anchor="{anchor}" transform="{transform}" font-size="{size}" font-family="{fmt["family"]}" font-weight="{fmt["weight"]}" fill="{fmt.get("color","#111")}" stroke="none">{spans}</text>')
        text_count+=1
    pictures=embedded_images(data,streams or {})
    for pic in pictures:
        parts.append(f'<image x="{pic["x"]}" y="{pic["y"]}" width="{pic["width"]}" height="{pic["height"]}" href="{pic["href"]}"/>')
    vectors=embedded_contours(data,streams) if streams else []
    fills=filled_contours(data,streams['StyleCluster']) if streams and 'StyleCluster' in streams else []
    for shape in fills:
        path=' '.join('M '+' L '.join(f'{x*1000},{-y*1000}' for x,y in points)+' Z' for points in shape['paths'])
        parts.append(f'<path d="{path}" fill="{shape["color"]}" fill-rule="evenodd" stroke="none" data-contour-ref="{shape["ref"]}"/>')
    for vector in vectors:
        path=' '.join('M '+' L '.join(f'{x},{y}' for x,y in points)+' Z' for points in vector['paths'])
        parts.append(f'<path d="{path}" fill="#000" fill-rule="evenodd" stroke="none" data-embedded-ref="{vector["ref"]}"/>')
    vector_strokes=embedded_strokes(data,streams) if streams else []
    for shape in vector_strokes:
        points=' '.join(f'{x},{y}' for x,y in shape['points'])
        parts.append(f'<polyline points="{points}" fill="none" stroke="{shape["color"]}" stroke-width="{shape["width"]}" stroke-linejoin="round" stroke-linecap="round" data-embedded-stroke-ref="{shape["ref"]}"/>')
    embedded_text_count=0
    for instance in embedded_instances(data,streams or {}):
        local_formats=text_formats(instance['style'])
        a,b,c,d,x,y,scale=instance['matrix']
        matrix=f'matrix({scale*a} {-scale*b} {-scale*c} {scale*d} {1000*x} {-1000*y})'
        for t in native_texts(instance['cluster'],None):
            fmt=local_formats.get(t['format_ref'])
            if fmt is None:continue
            size=fmt['height']
            if fmt.get('height_mode')==0x0d:size*=1.54 if fmt['family']=='Times New Roman' else 1.42
            elif fmt.get('height_mode')==0x0b:size*=.9
            tx,ty=t['x']*1000,-t['y']*1000
            baseline=0 if t['alignment_flags']==0 else size*.8
            angle=-math.degrees(math.atan2(t['direction_y'],t['direction_x']))
            parts.append(f'<g transform="{matrix}"><text x="{tx}" y="{ty+baseline}" transform="rotate({angle} {tx} {ty})" font-size="{size}" font-family="{fmt["family"]}" font-weight="{fmt["weight"]}" fill="{fmt["color"]}" stroke="none" data-embedded-text-ref="{t["graphic_ref"]}">{html.escape(t["text"])}</text></g>')
            embedded_text_count+=1
    bindings=bound_text(data,streams or {})
    for t in bindings:
        x,y=t['x']*1000,-t['y']*1000
        angle=-math.degrees(math.atan2(t['direction_y'],t['direction_x']))
        if t.get('latest_revision') and t.get('format_ref') in formats:
            fmt=formats[t['format_ref']]
            size=fmt['height']
            if fmt.get('height_mode')==0x0d:size*=1.54 if fmt['family']=='Times New Roman' else 1.42
            baseline=0 if t.get('alignment_flags')==0 else size*.8
            parts.append(f'<text x="{x}" y="{y+baseline}" transform="rotate({angle} {x} {y})" font-size="{size}" font-family="{fmt["family"]}" font-weight="{fmt["weight"]}" fill="{fmt["color"]}" stroke="none">{html.escape(t["text"])}</text>')
            continue
        # Native binding positions sit near the text top. Baseline offset and
        # font remain provisional; content and position come only from SHA.
        y+=2.0
        anchor='start'
        if formats.get(t.get('format_ref'),{}).get('paragraph_alignment')==0x100 and t.get('text_box_width_mm'):
            x+=t['text_box_width_mm']/2
            anchor='middle'
        parts.append(f'<text x="{x}" y="{y}" transform="rotate({angle} {x} {y})" text-anchor="{anchor}" font-size="2.25" font-family="Times New Roman" font-weight="bold" stroke="none" fill="#111">{html.escape(t["text"])}</text>')
    svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -297 420 297" width="1680" height="1188"><title>SHA native line/text preview; partial geometry, provisional fonts; no POD or PDF used</title><rect x="0" y="-297" width="420" height="297" fill="white"/><g stroke="#111" stroke-width="0.15" fill="none">'+'\n'.join(parts)+'</g></svg>'
    return svg,{'line_count':len(native_lines),'polyline_count':len(polylines),'circle_count':len(circles),'filled_contour_count':len(fills),'embedded_vector_contour_count':len(vectors),'embedded_stroke_count':len(vector_strokes),'embedded_text_count':embedded_text_count,'text_count':text_count,'native_text_records':len(frame_texts),'frame_text_records':len(frame_texts) if is_frame else 0,'embedded_bitmap_instances':len(pictures),'arc_count':arc_count,'bound_text_count':len(bindings),'unverified_style_candidates_omitted':rejected_styles,'complete':False,'limitations':['font substitution, paragraph and bound-text alignment provisional','not all graphic record types and text decorations are decoded','signature resources have not been resolved']}
