"""Drawing-content decoder for single-page SHA containers owned by the template.

This is a storage-layout branch, not a unit-number override. The conventional
Sheet6 renderer is untouched; shared primitives still use its binary decoders.
"""
from __future__ import annotations

import base64
import html
import json
import math
import re
import struct
from pathlib import Path
from chengda_paths import ASSET_DIR, PROJECT_DIR
from functools import lru_cache


@lru_cache(maxsize=8)
def font_advances(path):
    from fontTools.ttLib import TTFont
    with TTFont(path) as font:
        units = font['head'].unitsPerEm
        return {chr(code): font['hmtx'][glyph][0] / units
                for code, glyph in font.getBestCmap().items()}


@lru_cache(maxsize=8)
def font_ink_heights(path):
    from fontTools.ttLib import TTFont
    from fontTools.pens.boundsPen import BoundsPen
    with TTFont(path) as font:
        units = font['head'].unitsPerEm
        glyphs = font.getGlyphSet()
        heights = {}
        for code, glyph in font.getBestCmap().items():
            pen = BoundsPen(glyphs)
            glyphs[glyph].draw(pen)
            if pen.bounds is not None:
                heights[chr(code)] = (pen.bounds[1]/units, pen.bounds[3]/units)
        return heights


def centred_text_baseline(text, font_path, size, x, y, segments):
    """Centre visible glyphs between the actual enclosing horizontal rules."""
    rules = [y1*16800 for x1, y1, x2, y2, *_ in segments
             if abs(y1-y2) < 1e-7 and min(x1, x2)*16800 <= x <= max(x1, x2)*16800]
    below, above = [v for v in rules if v < y], [v for v in rules if v > y]
    if not below or not above:
        return None
    bottom, top = max(below), min(above)
    if not 20 < top-bottom < 500:
        return None
    heights = font_ink_heights(str(font_path))
    bounds = [heights[c] for c in text if c in heights]
    if not bounds:
        return None
    ink_middle = (min(v[0] for v in bounds)+max(v[1] for v in bounds))/2*size
    return (bottom+top)/2-ink_middle


def format_font_definition(styles, format_ref):
    # A text format (0x30) references its font definition (0x2c).
    # Font definitions store horizontal scale in thousandths after height.
    for match in re.finditer(re.escape(struct.pack('<I', format_ref)), styles):
        p = match.start()
        if p < 2 or p+28 > len(styles) or styles[p-2:p] not in (b'\x01\x00', b'\x02\x00') or styles[p+14:p+16] not in (b'\x00\x00', b'\x12\x00'):
            continue
        font_ref = struct.unpack_from('<I', styles, p+24)[0]
        for other in re.finditer(re.escape(struct.pack('<I', font_ref)), styles):
            q = other.start()
            if q >= 20 and styles[q-20:q-18] == b'\x2c\x00' and q+40 <= len(styles):
                scale = struct.unpack_from('<H', styles, q+38)[0] / 1000
                height = struct.unpack_from('<d', styles, q+28)[0]
                if .0001 <= height <= .02:
                    return dict(height=height, width_scale=scale if .1 <= scale <= 3 else 1.0)
    return None


def format_width_scale(styles, format_ref):
    definition = format_font_definition(styles, format_ref)
    return definition['width_scale'] if definition else 1.0


def bound_title_geometry(record, segments):
    """Resolve number-cell typography and centre the actual visible glyphs."""
    if record.get('stream') == 'TitleArea' and record.get('field') in ('DrawingNumber', 'Sheet'):
        folder = ASSET_DIR/'template_owned_fonts'
        profile = json.loads((folder/'profile.json').read_text(encoding='utf-8'))
        info = profile['fonts']['feature']
        advances = font_advances(str(folder/info['file']))
        size = float(record['font_size'])
        nominal_width = sum(advances.get(c, .6) for c in str(record['text']))*size
        baseline = centred_text_baseline(str(record['text']), folder/info['file'], size,
                                         float(record['x']), float(record['y']), segments)
        return dict(family='TemplateOwned-feature', x=float(record['x'])-nominal_width/2,
                    y=float(record['y']) if baseline is None else baseline,
                    letter_spacing=float(info.get('letter_spacing_em', 0))*size)
    return None


def revision_text_geometry(record, styles, api):
    definition = format_font_definition(styles, int(record.get('format_ref', 0)))
    if definition is None:
        return None
    font_size = definition['height']*16800*1.4
    x, y = float(record['x'])*16800, float(record['y'])*16800
    # Compact revision labels carry their top insertion, including the text
    # format's height; their parent triangle does not determine glyph size.
    profile = json.loads((ASSET_DIR/'template_owned_fonts'/'profile.json').read_text(encoding='utf-8'))
    ascent = profile['fonts']['plot']['ascent']
    return x, y-ascent*font_size, font_size, 'TemplateOwned-plot'


def is_template_owned_page(streams, selected_name, template_name):
    from analyze_iso_split import forward_owned_dynamic_graphics
    if selected_name == template_name or not template_name:
        return False
    physical = [name for name, data in streams.items()
                if re.fullmatch(r"Sheet\d+", name) and name != template_name and len(data) > 8]
    if physical != [selected_name]:
        return False
    dynamic = forward_owned_dynamic_graphics(streams.get("Unclustered Dynamic Attributes", b""))
    refs = {int(row["graphic_ref"]) for rows in dynamic.values() for row in rows}
    count = lambda data: sum(struct.pack("<I", ref) in data for ref in refs)
    return bool(refs) and count(streams[template_name]) > max(2, count(streams[selected_name]))


def mapping_sheet_streams(streams, wanted_pages):
    """Supply owned geometry to matching while retaining physical page identity.

    Ordinary SHA targets are returned unchanged. In the one-page template-owned
    variant the pipe groups, UCI containers and weld anchors are in the template;
    Sheet6 remains the logical output page and annotation destination. This
    adapter is for matching only, not rendering or modifying SHA containers.
    """
    from inject_sha_weld_callouts import SheetTarget, sheet_streams
    from sha_to_svg_prototype import discover_template_stream
    targets = sheet_streams(streams, wanted_pages)
    template_name, template_data = discover_template_stream(streams)
    return [SheetTarget(target.stream_name, target.page, template_data)
            if is_template_owned_page(streams, target.stream_name, template_name)
            else target for target in targets]


def space_template_owned_pipe_splits(payload, sha_path, minimum_gap_mm=3.5):
    """Keep projected splits readable beside native endpoint welds.

    This changes only drawing coordinates for this storage variant. The proven
    pipe axis supplies direction; native endpoints and other welds supply bounds
    so a split cannot jump to another pipe, pass a peer or replace an endpoint.
    """
    from analyze_iso_split import read_sha_streams
    from inject_sha_weld_callouts import sheet_streams
    from sha_to_svg_prototype import discover_template_stream
    streams = read_sha_streams(sha_path)
    physical = sheet_streams(streams, None)
    template_name, _ = discover_template_stream(streams)
    if len(physical) != 1 or not is_template_owned_page(streams, physical[0].stream_name, template_name):
        return payload
    minimum_gap = float(minimum_gap_mm)*16800/1000
    allowed = {
        'construction-table-collinear-pipe-run',
        'construction-table-material-chain-two-sided-bracket',
        'construction-table-material-chain-terminal-pipe',
        'construction-table-material-chain-cumulative-pipe',
    }
    rows = [dict(row) for row in payload.get('welds', [])]
    mapped = [row for row in rows if row.get('weld_number') and row.get('graphic_ref') is not None]
    changed = 0
    for row in mapped:
        if row.get('construction_virtual_rule') not in allowed:
            continue
        axis = row.get('pipe_axis')
        if not isinstance(axis, list) or len(axis) != 2:
            continue
        start, end = tuple(map(float, axis[0])), tuple(map(float, axis[1]))
        length = math.dist(start, end)
        if length <= 2*minimum_gap:
            continue
        direction = ((end[0]-start[0])/length, (end[1]-start[1])/length)
        original = (float(row['sha_x']), float(row['sha_y']))
        station = sum((original[i]-start[i])*direction[i] for i in (0, 1))
        if not 0 < station < length:
            continue
        endpoints = [peer for peer in mapped if peer is not row
                     and peer.get('page') == row.get('page')
                     and not peer.get('construction_virtual_target')
                     and min(math.dist((float(peer['sha_x']), float(peer['sha_y'])), tip)
                             for tip in (start, end)) <= 3]
        close = [peer for peer in endpoints
                 if math.dist(original, (float(peer['sha_x']), float(peer['sha_y']))) < minimum_gap]
        if len(close) != 1:
            continue
        endpoint = close[0]
        endpoint_xy = (float(endpoint['sha_x']), float(endpoint['sha_y']))
        endpoint_at_start = math.dist(endpoint_xy, start) < math.dist(endpoint_xy, end)
        requested = minimum_gap if endpoint_at_start else length-minimum_gap
        proposed = tuple(start[i]+requested*direction[i] for i in (0, 1))
        # Enforce order and keep a gap to every already mapped peer on this
        # axis. Crowded runs retain the engineering point for manual review.
        blocked = False
        for peer in mapped:
            if peer is row or peer is endpoint or peer.get('page') != row.get('page'):
                continue
            xy = (float(peer['sha_x']), float(peer['sha_y']))
            peer_station = sum((xy[i]-start[i])*direction[i] for i in (0, 1))
            if not 0 <= peer_station <= length:
                continue
            projected = tuple(start[i]+peer_station*direction[i] for i in (0, 1))
            if math.dist(xy, projected) > 3:
                continue
            if (peer_station-station)*(peer_station-requested) <= 0 or math.dist(xy, proposed) < minimum_gap:
                blocked = True
                break
        if blocked:
            continue
        row['sha_x'], row['sha_y'] = round(proposed[0], 3), round(proposed[1], 3)
        row['visual_endpoint_clearance'] = dict(
            original_sha_point=list(original), minimum_gap_mm=float(minimum_gap_mm),
            minimum_gap_page_units=minimum_gap,
            actual_gap_page_units=round(math.dist(proposed, endpoint_xy), 3),
            endpoint_weld_number=endpoint['weld_number'],
            rule='template-owned-verified-pipe-axis-minimum-endpoint-gap',
        )
        row['manual_review_required'] = True
        reason = '分段焊口按最小视觉间距沿已确认直管调整，工程坐标不变'
        row['manual_review_reason'] = ';'.join(filter(None, (row.get('manual_review_reason', ''), reason)))
        changed += 1
    if not changed:
        return payload
    output = dict(payload, welds=rows)
    output['template_owned_endpoint_spacing'] = dict(adjusted_count=changed, minimum_gap_mm=float(minimum_gap_mm))
    return output


def plot_text_records(data, psm, style_data, api):
    metrics = api.style_format_metrics(style_data)
    compass_circles = [c for c in api.template_circle_primitives(data) + api.standalone_arcs(data)
                       if 50 < float(c['radius']) < 300 and float(c['center_y']) > 16800*.30]
    records = list(api.text_records(data))
    # Compact template texts also carry scaled direction vectors. The normal
    # sheet scanner's unit-vector condition omits degree-bearing compass text.
    known = {int(r['text_start']) for r in records}
    for match in re.finditer(b'\x4d\x00', data):
        start = match.start()
        if start + 30 > len(data):
            continue
        size = struct.unpack_from('<I', data, start + 2)[0]
        count = struct.unpack_from('<H', data, start + 28)[0]
        pos, end = start + 30, start + 30 + count * 2
        if pos in known or not 1 <= count <= 240 or end + 32 > min(len(data), start + 2 + size):
            continue
        try:
            text = data[pos:end].decode('utf-16le')
        except UnicodeDecodeError:
            continue
        if not text or not all(c.isprintable() for c in text):
            continue
        x, y, dx, dy = struct.unpack_from('<dddd', data, end)
        if not (0 <= x <= 1 and 0 <= y <= 1 and .0005 <= math.hypot(dx, dy) <= 1.2):
            continue
        records.append(dict(text=text, graphic_ref=struct.unpack_from('<I', data, start+6)[0],
                            style_ref=struct.unpack_from('<I', data, start+14)[0],
                            format_ref=struct.unpack_from('<I', data, start+20)[0],
                            text_start=pos, x=x, y=y, direction_x=dx, direction_y=dy))
    selected = []
    for record in records:
        text = str(record['text']).strip()
        fmt = int(record.get('format_ref', 0))
        metric = metrics.get(fmt)
        magnitude = math.hypot(record['direction_x'], record['direction_y'])
        if (not fmt or not text or text == 'a;' or text.startswith('<?xml')
            or not all(c.isprintable() for c in text) or int(record['style_ref']) > 0xffff
            or not (0 <= record['x'] <= 1 and 0 <= record['y'] <= 1)
            or not (.0005 <= magnitude <= .02 or .8 <= magnitude <= 1.2)):
            continue
        bbox = api.psm_bbox(psm, int(record['graphic_ref']))
        # Direct plotted texts use an object-local style, unlike the fixed
        # title block's compound-style words. Missing family declarations are
        # accepted only with a local glyph envelope at the insertion point.
        local = bbox is not None and 10 < bbox[3]-bbox[1] < 150 and abs(bbox[0]-record['x']*16800) < 150
        if magnitude < .8 and not any(
            math.hypot(record['x']*16800-c['center_x'], record['y']*16800-c['center_y']) < 1.8*c['radius']
            for c in compass_circles
        ):
            continue
        if not (metric and metric[0] == 'Arial-Z') and not (
            metric is None and local and record['x'] < .30 and record['y'] < .04
        ):
            continue
        row = dict(record, text=text, bbox=bbox, metric=metric)
        selected.append(row)
    return selected


def render_plot_content(data, psm, styles, records, api):
    """Emit original insertion points, plotter glyph metrics, arcs and dots."""
    resource_dir = ASSET_DIR / 'template_owned_fonts'
    profile_path = resource_dir / 'profile.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8')) if profile_path.exists() else {}
    elements = []
    for role, entry in profile.get('fonts', {}).items():
        encoded = base64.b64encode((resource_dir / entry['file']).read_bytes()).decode('ascii')
        elements.append(f'<style>@font-face{{font-family:TemplateOwned-{role};src:url(data:font/ttf;base64,{encoded})}}</style>')
    # PSM supplies a glyph envelope, whereas Shape2D height is cap-height.
    # The conversion to font-em is calibrated once from the reference font,
    # stored as font metadata, and never from individual label offsets.
    em_scale = float(profile.get('plot_height_to_em', 1.4))
    segments = api.template_line_segments(data)
    for row in records:
        text, box, metric = row['text'], row['bbox'], row['metric']
        x, y = float(row['x'])*16800, float(row['y'])*16800
        dx, dy = float(row['direction_x']), float(row['direction_y'])
        role = 'plot' if metric else 'feature'
        font_size = metric[1]*16800*em_scale if metric else (box[3]-box[1]) / float(profile.get('feature_bbox_to_em', 1.13))
        if not metric and not box:
            continue
        angle = math.degrees(math.atan2(dy, dx))
        horizontal_scale = 1.0
        length_attr = ''
        if metric and box is not None and row['y'] < .04:
            # Direct title values share a baseline format with the plot body,
            # but retain a compound horizontal scale and local glyph envelope.
            # PSM width includes the source character spacing.
            horizontal_scale = format_width_scale(styles, int(row['format_ref']))
            if horizontal_scale != 1.0:
                length_attr = f' textLength="{(box[2]-box[0])/horizontal_scale:.4f}" lengthAdjust="spacingAndGlyphs"'
                # Compound title values use the after-edge insertion; ordinary
                # plot annotations use a baseline. Resolve the former through
                # the actual plot font descent, not a page-specific offset.
                descent = profile.get('fonts', {}).get(role, {}).get('descent', -.2119140625)
                y -= float(descent)*font_size
                info = profile.get('fonts', {}).get(role, {})
                if info.get('file'):
                    baseline = centred_text_baseline(text, resource_dir/info['file'], font_size,
                                                     x, float(row['y'])*16800, segments)
                    if baseline is not None:
                        y = baseline
        if math.hypot(dx, dy) < .02 and box is not None:
            # Scaled template transforms denote the local text origin, not
            # the plot-text baseline. PSM preserves its transformed line box;
            # the embedded font ascender/descender resolves the baseline.
            font_info = profile.get('fonts', {}).get(role, {})
            ascent = float(font_info.get('ascent', .9052734375))
            descent = float(font_info.get('descent', -.2119140625))
            horizontal_scale = format_width_scale(styles, int(row['format_ref']))
            advances = font_advances(str(resource_dir / font_info['file'])) if font_info.get('file') else {}
            width = sum(advances.get(char, .6) for char in text)*font_size*horizontal_scale
            cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            corners = [(xx*cosine-yy*sine, xx*sine+yy*cosine)
                       for xx in (0, width) for yy in (descent*font_size, ascent*font_size)]
            x, y = box[0]-min(p[0] for p in corners), box[1]-min(p[1] for p in corners)
        transform = f' transform="rotate({-angle:.5f} {x:.4f} {11880-y:.4f})"' if abs(angle) > .01 else ''
        if horizontal_scale != 1.0:
            transform = (f' transform="translate({x:.4f} {11880-y:.4f}) rotate({-angle:.5f}) '
                         f'scale({horizontal_scale:.6f} 1) translate({-x:.4f} {y-11880:.4f})"')
        family = f'TemplateOwned-{role}' if role in profile.get('fonts', {}) else 'Arial'
        elements.append(f'<text x="{x:.4f}" y="{11880-y:.4f}" font-family="{family}" '
                        f'font-size="{font_size:.4f}" fill="#000000" stroke="none"{transform} '
                        f'{length_attr} '
                        f'data-layer="sha-template-owned-text" data-graphic="0x{int(row["graphic_ref"]):08X}" '
                        f'data-format="0x{int(row["format_ref"]):04X}">{html.escape(text)}</text>')
    widths = api.line_style_widths(styles)
    arcs = api.standalone_arcs(data)
    for arc in arcs:
        x1,y1,x2,y2,large,sweep = api.standalone_arc_geometry(arc)
        radius = arc['radius']
        width = widths.get(int(arc['style_ref']), 4.2)
        elements.append(f'<path d="M {x1:.4f} {11880-y1:.4f} A {radius:.4f} {radius:.4f} 0 {large} {sweep} {x2:.4f} {11880-y2:.4f}" '
                        f'fill="none" stroke="#000000" stroke-width="{width:.4f}" '
                        f'data-layer="sha-template-owned-arc" data-child="0x{int(arc["primitive_ref"]):08X}"/>')
    dots = [c for c in api.template_circle_primitives(data) if c['radius'] <= 30]
    for dot in dots:
        elements.append(f'<circle cx="{dot["center_x"]:.4f}" cy="{11880-dot["center_y"]:.4f}" r="{dot["radius"]:.4f}" '
                        f'fill="#000000" stroke="none" data-layer="sha-template-owned-weld-dot"/>')
    return elements, dict(text_count=len(records), arc_count=len(arcs), weld_dot_count=len(dots))
