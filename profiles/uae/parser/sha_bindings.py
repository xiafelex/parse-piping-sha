"""Resolve the observed UAE template XML bindings from SHA streams only."""
import math
import re
import struct
import xml.etree.ElementTree as ET

XML_PATTERN = rb'<\x00\?\x00x\x00m\x00l\x00.*?<\x00/\x00b\x00o\x00d\x00y\x00>\x00'


def bound_text(data, streams):
    records = []
    from sha_contours import records as cluster_records
    try:
        text_records=[(offset,record) for offset,kind,record in cluster_records(data) if kind==0x4d]
    except ValueError:
        text_records=[]  # Also accepts isolated XML fragments for inspection.
    for match in re.finditer(XML_PATTERN, data):
        try:
            body = ET.fromstring(match.group().decode('utf-16le'))
        except (ET.ParseError, UnicodeError):
            continue
        binding = body.find('intstgxml')
        if binding is None:
            continue
        expression = binding.get('select', '')
        source = streams.get('TaggedTxtData/' + binding.get('stream', ''))
        if not source:
            continue
        root = ET.fromstring(source.decode('utf-8'))
        selector = re.fullmatch(r'/Revision/RevisionRecord\[\(\(last\(\)>3\) and position\(\)=\(last\(\)-(\d+)\)\) or \(\(last\(\) <=3\) and position\(\)=(\d+)\)\]/(\w+)', expression)
        latest=re.fullmatch(r'/Revision/RevisionRecord\[last\(\)\]/(\w+)',expression)
        if latest:
            revisions=root.findall('RevisionRecord')
            field=latest[1]
            value=revisions[-1].findtext(field,'') if revisions else ''
        elif selector:
            revisions = root.findall('RevisionRecord')
            back, position, field = selector.groups()
            index = len(revisions)-1-int(back) if len(revisions)>3 else int(position)-1
            value = revisions[index].findtext(field, '') if 0<=index<len(revisions) else ''
        else:
            simple = re.fullmatch(r'/(\w+)/(\w+)', expression)
            if not simple or simple[1] != root.tag:
                continue
            field = simple[2]
            value = root.findtext(field, '')
        if not value:
            continue
        # Observed two layouts: direct transform, or 64-byte property run then transform.
        for offset in (0,64):
            if match.end()+offset+32 > len(data):
                continue
            x,y,dx,dy = struct.unpack_from('<4d',data,match.end()+offset)
            if (all(math.isfinite(v) for v in (x,y,dx,dy)) and
                    .001<x<.42 and .001<y<.297 and .999<math.hypot(dx,dy)<1.001):
                item={'text':value,'x':x,'y':y,'direction_x':dx,'direction_y':dy,
                      'field':field,'source_expression':expression,'offset':match.start(),'latest_revision':bool(latest)}
                for record_start,record in text_records:
                    if record_start<=match.start()<record_start+len(record):
                        item['format_ref']=struct.unpack_from('<I',record,20)[0]
                        tail=match.end()+offset+35-record_start
                        if tail<len(record):item['alignment_flags']=record[tail]
                        width_offset=match.end()+offset+40-record_start
                        if width_offset+16<=len(record):
                            width=struct.unpack_from('<d',record,width_offset)[0]*1000
                            if math.isfinite(width) and .5<width<420:item['text_box_width_mm']=width
                        break
                records.append(item)
                break
    return records
