"""POD-only vector inspection preview. No SHA/PDF geometry is borrowed."""
import html
import math


def render(model):
    # POD units observed as 1/1000 paper mm, SHA stores paper metres.
    scale = 1000
    shapes = []
    omitted = []
    for group in model['graphics']:
        for p in group['primitives']:
            kind = p['kind']
            attr = f'data-pod-offset="{p["offset"]}" data-layer="{group["layer_id"]}"'
            if group.get('uci'):
                attr += f' data-uci="{html.escape(group["uci"], quote=True)}"'
            if kind == 'line':
                x,y,a,b = [n/scale for n in p['xy']]
                shapes.append(f'<line {attr} x1="{x}" y1="{-y}" x2="{a}" y2="{-b}"/>')
            elif kind == 'polygon':
                points = ' '.join(f'{x/scale},{-y/scale}' for x,y in p['points'])
                fill = '#111' if p['filled_candidate'] else 'none'
                shapes.append(f'<polygon {attr} points="{points}" fill="{fill}"/>')
            elif kind == 'circle':
                x,y = p['center']; radius=p['radius']/scale
                fill = '#111' if p['fill_code']==1 else 'none'
                shapes.append(f'<circle {attr} cx="{x/scale}" cy="{-y/scale}" r="{radius}" fill="{fill}"/>')
            elif kind == 'text':
                x,y=[v/scale for v in p['xy']]; size=p['character_size'][1]/scale
                font=model['fonts'].get(p['font_id'], {})
                family=html.escape(font.get('family','Arial'), quote=True)
                weight='bold' if font.get('variant')=='Bold' else 'normal'
                angle=-p['angle_millidegrees']/1000
                shapes.append(f'<text {attr} x="{x}" y="{-y}" transform="rotate({angle} {x} {-y})" font-family="{family}" font-weight="{weight}" font-size="{size}" stroke="none" fill="#111">{html.escape(p["text"])}</text>')
            elif kind == 'arc' and p['radii'][0] == p['radii'][1] and p['rotation_millidegrees'] == 0:
                x,y=[v/scale for v in p['center']];r=p['radii'][0]/scale
                a=math.radians(p['start_millidegrees']/1000)
                sweep=p['sweep_millidegrees']/1000;b=a+math.radians(sweep)
                sx,sy=x+r*math.cos(a),-(y+r*math.sin(a))
                ex,ey=x+r*math.cos(b),-(y+r*math.sin(b))
                shapes.append(f'<path {attr} d="M {sx} {sy} A {r} {r} 0 {int(abs(sweep)>180)} {int(sweep<0)} {ex} {ey}"/>')
            else:
                omitted.append({'offset': p['offset'], 'kind': kind})
    # A3 page extent is project layout, not a decoded POD header claim.
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -297 420 297" width="1680" height="1188">\n'
    svg += '<title>POD decoded-vector preview; excludes SHA template and usergraphics; text alignment provisional</title>\n'
    svg += '<rect x="0" y="-297" width="420" height="297" fill="white"/>\n'
    svg += '<g stroke="#111" stroke-width="0.15" fill="none">\n' + '\n'.join(shapes) + '\n</g></svg>'
    return svg, {'rendered_primitives': len(shapes), 'omitted_primitives': omitted, 'complete': False, 'limitations': ['noncircular arcs not validated', 'text alignment and some stroke/fill styles provisional', 'page extent set to project A3', 'separate SHA frame/usergraphics not included']}
