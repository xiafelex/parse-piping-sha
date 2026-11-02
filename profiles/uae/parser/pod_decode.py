"""UAE POD semantic decoder. Exact framing + observed, bounded record schemas.

Every semantic item retains its byte offset. Unknown records are reported and
preserved by pod_binary.lossless_tree, rather than silently discarded.
"""
from __future__ import annotations
import math
import re
import struct
from collections import Counter
from pod_binary import Reader, parse_tree, length_strings


def finish(reader):
    if reader.pos != len(reader.data):
        raise ValueError(f'{len(reader.data) - reader.pos} unconsumed bytes')


def variant(reader):
    code = reader.u16()
    if code == 8:
        value = reader.string()
    elif code == 5:
        value = reader.f64()
        if not math.isfinite(value):
            raise ValueError('non-finite double')
    elif code == 3:
        value = reader.i32()
    elif code == 0:
        value = reader.string()
        if value:
            raise ValueError('nonempty VT_EMPTY storage')
        value = None
    else:
        raise ValueError(f'unsupported variant {code}')
    present = reader.u8()
    if present not in (0, 1):
        raise ValueError('invalid value presence flag')
    return {'variant_code': code, 'value': value, 'present': bool(present)}


def declaration(node, data):
    q = Reader(node.head(data))
    d = {'offset': node.start, 'slot': q.u32(), 'property_id': q.u32(), 'schema_flag': q.u8(), 'name': q.string(), 'label': q.string()}
    if not re.fullmatch(r'[A-Z][A-Z0-9_-]*', d['name']):
        raise ValueError('not a property declaration')
    d.update(category_code=q.u32(), value_kind=q.u32(), flags=[q.u8(), q.u8(), q.u8()], constraint=q.string(), unknown_flag=q.u8())
    if q.pos < len(q.data):
        d['default_or_value'] = variant(q)
    finish(q)
    return d


def property_value(node, data, declarations, slots=None):
    q = Reader(node.head(data))
    v = {'offset': node.start, 'slot': q.u32(), 'property_id': q.u32(), 'unknown_flag': q.u8()}
    v.update(variant(q))
    finish(q)
    match = declarations.get(v['property_id'])
    if match:
        v['name'], v['name_evidence'] = match['name'], 'explicit POD schema id'
    elif slots and v['slot'] in slots:
        v['name'], v['name_evidence'] = slots[v['slot']]['name'], 'candidate: inherited component schema slot'
    else:
        v['name'], v['name_evidence'] = f"PROPERTY_{v['property_id']}", 'unresolved'
    return v


def point(node, data):
    q = Reader(node.head(data))
    result = {'offset': node.start, 'name': q.string(), 'centre_flag': q.u8(), 'bore': q.f64(), 'bore_unit_code': q.u32(), 'end_preparation': q.string(), 'peer_port': q.string(), 'peer_object_guid': q.string(), 'drawing_xy': [q.i32(), q.i32()], 'drawing_flag': q.u8()}
    finish(q)
    if len(node.children) != 1 or len(node.children[0].children) != 3:
        raise ValueError('unexpected xyz tuple')
    result['xyz'], result['coordinate_modes'], result['coordinate_extras'] = [], [], []
    for axis in node.children[0].children:
        q = Reader(axis.head(data))
        result['coordinate_modes'].append(q.u32())
        result['xyz'].append(q.f64())
        result['coordinate_extras'].append(q.f64())
        finish(q)
    return result


def primitive(node, code, data):
    q = Reader(node.head(data))
    p = {'offset': node.start, 'end': node.end, 'primitive_code': code, 'tag': node.tag}
    if code == 3:
        p.update(kind='line', xy=[q.i32() for _ in range(4)], style_id=q.u32())
    elif code in (6, 7):
        style, fill, count = q.u32(), q.i32(), q.u32()
        if count > len(q.data) // 8:
            raise ValueError('invalid polygon point count')
        p.update(kind='polygon', style_id=style, fill_code=fill, filled_candidate=(code == 7), points=[[q.i32(), q.i32()] for _ in range(count)])
    elif code == 1:
        p.update(kind='circle', center=[q.i32(), q.i32()], radius=q.i32(), style_id=q.u32(), fill_code=q.i32())
    elif code == 8:
        text = q.string()
        p.update(kind='text', text=text, xy=[q.i32(), q.i32()], character_size=[q.i32(), q.i32()], font_id=q.u32(), color_code=q.i32(), field6=q.i32(), text_style=q.i32(), field8=q.i32(), angle_millidegrees=q.i32(), field10=q.i32(), field11=q.i32())
    elif code == 0:
        p.update(kind='arc', center=[q.i32(), q.i32()], radii=[q.i32(), q.i32()], rotation_millidegrees=q.i32(), start_millidegrees=q.i32(), sweep_millidegrees=q.i32(), style_id=q.u32(), interpretation='signed start+sweep, 0.001 degrees; all four sample arcs independently agree with SHA')
    else:
        raise ValueError(f'unknown graphic primitive {code}')
    finish(q)
    return p


def decode(data: bytes):
    root, nodes = parse_tree(data)
    by_offset = {n.start: n for n in nodes}
    if root.tag != 5 or len(root.children) != 3:
        raise ValueError('unsupported POD root schema')
    q = Reader(root.head(data))
    version = q.string()
    if version != '6.0.0.92':
        raise ValueError(f'POD serialization version not validated: {version}')
    q.u32(); q.u16(); document_guid = q.string(); finish(q)
    pipelines = root.children[1].children
    if len(pipelines) != 1 or len(pipelines[0].children) != 15:
        raise ValueError('currently validated only for one pipeline and 15 sections')
    pipeline = pipelines[0]
    q = Reader(pipeline.head(data))
    line_name = q.string()
    line_header_unknown = q.data[q.pos:].hex()
    schema, decoded_declarations, property_nodes, ambiguous_ids = {}, [], set(), set()
    for n in nodes:
        if n.tag != 3 or n.children:
            continue
        try:
            d = declaration(n, data)
        except (ValueError, UnicodeError):
            continue
        previous = schema.get(d['property_id'])
        if previous and previous['name'] != d['name']:
            ambiguous_ids.add(d['property_id'])
        schema[d['property_id']] = d
        decoded_declarations.append(d)
        property_nodes.add(n.start)
    for key in ambiguous_ids:
        schema.pop(key, None)
    slots = {declaration(n, data)['slot']: declaration(n, data) for n in pipeline.children[8].children}
    values, unknown = [], []
    for n in nodes:
        if n.tag != 3 or n.children or n.start in property_nodes:
            continue
        try:
            # Slot names only used within the direct component property collection.
            parent = by_offset.get(n.parent)
            owner = by_offset.get(parent.parent) if parent else None
            is_component_property = owner in pipeline.children[11].children and owner.tag == 3 and len(owner.children) > 2 and parent is owner.children[2]
            v = property_value(n, data, schema, slots if is_component_property else None)
            values.append(v)
            property_nodes.add(n.start)
        except (ValueError, UnicodeError):
            continue
    value_map = {v['offset']: v for v in values}
    components, connections = [], []
    for n in pipeline.children[11].children:
        q = Reader(n.head(data))
        if n.tag == 2:
            link = {'offset': n.start, 'object_guid': q.string(), 'from_guid': q.string(), 'from_port': q.string(), 'to_guid': q.string()}
            link['to_port'] = q.string() if q.pos < len(q.data) else None
            finish(q)
            connections.append(link)
            continue
        comp = {'offset': n.start, 'end': n.end, 'object_guid': q.string(), 'uci': q.string(), 'component_kind_code': q.u32(), 'material_guid': q.string()}
        comp['header_unknown_hex'] = q.data[q.pos:].hex()
        comp['ports'], comp['properties'] = [], []
        for pn in n.children[0].children:
            try:
                comp['ports'].append(point(pn, data))
            except (ValueError, UnicodeError) as exc:
                unknown.append({'offset': pn.start, 'context': 'port', 'reason': str(exc)})
        comp['properties'] = [value_map[c.start] for c in n.children[2].children if c.start in value_map]
        comp['unresolved_property_offsets'] = [c.start for c in n.children[2].children if c.start not in value_map]
        comp['type_name'] = Reader(n.children[2].head(data)).string()
        comp['drawing_group_offsets'] = []
        components.append(comp)
    groups, primitive_counts = [], Counter()
    for n in nodes:
        h = n.head(data)
        if n.tag != 2 or len(h) != 16 or not n.children or struct.unpack_from('<I', h, 8)[0] != len(n.children):
            continue
        layer, local_id, count, _ = struct.unpack('<4I', h)
        if any(c.children for c in n.children):
            continue
        group = {'offset': n.start, 'layer_id': layer, 'local_id': local_id, 'declared_primitives': count, 'primitives': []}
        owner = next((c for c in components if c['offset'] < n.start < c['end']), None)
        if owner:
            group['owner_guid'], group['uci'] = owner['object_guid'], owner['uci']
            owner['drawing_group_offsets'].append(n.start)
        for c in n.children:
            code = struct.unpack_from('<I', data, c.start - 4)[0]
            primitive_counts[code] += 1
            try:
                group['primitives'].append(primitive(c, code, data))
            except (ValueError, UnicodeError) as exc:
                unknown.append({'offset': c.start, 'context': 'graphic', 'primitive_code': code, 'reason': str(exc)})
        # Trailing 32-bit value varies, and must not be mistaken for next type.
        group['trailing_hex'] = data[n.children[-1].end:n.end - 4].hex()
        groups.append(group)
    layers, fonts = {}, {}
    for n in pipeline.children[3].children:
        q = Reader(n.head(data)); key = q.u32(); layers[key] = q.string(); finish(q)
    for n in pipeline.children[2].children:
        q = Reader(n.head(data)); key = q.u32(); file_name=q.string();flag=q.u32();family=q.string();variant_name=q.string();finish(q)
        fonts[key] = {'file': file_name, 'flag': flag, 'family': family, 'variant': variant_name}
    def object_records(collection):
        records=[]
        for n in collection.children:
            strings=length_strings(n.head(data),n.start+8)
            records.append({'offset':n.start,'end':n.end,'tag':n.tag,'header_strings':strings,
                            'property_declarations':[d for d in decoded_declarations if n.start<d['offset']<n.end],
                            'property_values':[v for v in values if n.start<v['offset']<n.end],
                            'note':'header strings keep source order; unlabelled references are not assigned invented roles'})
        return records
    materials=object_records(pipeline.children[10])
    summaries=object_records(pipeline.children[12])
    # Expose all exact direct payload spans; undeclared fields stay traceable.
    structure = [{'offset': n.start, 'end': n.end, 'parent': n.parent, 'tag': n.tag, 'children': [c.start for c in n.children], 'direct_spans': list(n.spans())} for n in nodes]
    return {'version': version, 'document_guid': document_guid, 'pipeline': line_name, 'pipeline_header_unknown_hex': line_header_unknown,
            'object_count': len(nodes), 'objects': structure, 'declarations': decoded_declarations, 'property_values': values,
            'components': components, 'connections': connections, 'materials': materials, 'summaries': summaries, 'graphics': groups, 'layers': layers, 'fonts': fonts, 'primitive_counts': dict(primitive_counts), 'decode_errors': unknown,
            'coverage': {'framing_complete': True, 'complete_semantic_decode': False, 'complete_visual_reconstruction': False,
                         'remaining': ['object tag/version semantics', 'unused style/palette fields', 'material/summary relationship semantics', 'candidate inherited property names', 'text justification and noncircular arc generalization', 'SHA template and usergraphics are separate outputs, not assumed present in POD']}}
