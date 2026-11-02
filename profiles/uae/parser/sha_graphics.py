"""Bounded UAE text records, explicit line styles and embedded BMP instances."""
import base64
import math
import re
import struct


def polyline_primitives(data):
    """0x84 point arrays; the two observed flags are open and closed paths."""
    from sha_contours import records
    result=[]
    for offset,kind,record in records(data):
        if kind!=0x84 or len(record)<62:continue
        count=struct.unpack_from('<I',record,24)[0]
        if not 2<=count<=20000 or len(record)!=30+count*16:continue
        if record[28:30] not in (b'\x01\x02',b'\x02\x01'):continue
        coords=struct.unpack_from(f'<{count*2}d',record,30)
        if not all(math.isfinite(v) and abs(v)<2 for v in coords):continue
        points=list(zip(coords[::2],coords[1::2]))
        if record[28:30]==b'\x02\x01' and points[0]!=points[-1]:continue
        result.append({'offset':offset,'ref':struct.unpack_from('<I',record,6)[0],
                       'style_ref':struct.unpack_from('<I',record,20)[0],'points':points})
    return result


def circle_primitives(data):
    from sha_contours import records
    circles=[]
    for offset,kind,record in records(data):
        if kind!=0x59 or len(record)!=49:continue
        x,y,r=struct.unpack_from('<3d',record,24)
        if not all(math.isfinite(v) for v in (x,y,r)) or not (0<x<.43 and 0<y<.31 and 0<r<.05):continue
        circles.append({'offset':offset,'ref':struct.unpack_from('<I',record,6)[0],
                        'style_ref':struct.unpack_from('<I',record,20)[0],
                        'x':x*1000,'y':-y*1000,'radius':r*1000})
    return circles


def native_texts(data, sheet_id):
    from sha_contours import records as cluster_records
    records=[]
    for o,kind,record in cluster_records(data):
        if kind!=0x4d:continue
        if o+30>len(data):continue
        size=struct.unpack_from('<I',data,o+2)[0]
        end=o+6+size
        if not 50<=size<=len(data)-o-6:continue
        ref,owner,layer=struct.unpack_from('<III',data,o+6)
        if sheet_id is not None and owner!=sheet_id:continue
        candidates=[]
        for pos in range(o+28,min(o+44,end-2),2):
            count=struct.unpack_from('<H',data,pos)[0]
            stop=pos+2+count*2
            if not count or stop+32>end:continue
            try:text=data[pos+2:stop].decode('utf-16le')
            except UnicodeError:continue
            if not all(c.isprintable() or c in '\r\n\t' for c in text):continue
            if not text.strip():continue
            candidates.append((len(text),text,stop))
        if not candidates:continue
        _,text,stop=max(candidates)
        if '<?xml' in text:continue
        transforms=[]
        # Observed optional text-run properties are complete 8-byte entries
        # (character count, run kind, format reference), never partial words.
        for pos in range(stop,end-31,8):
            x,y,dx,dy=struct.unpack_from('<4d',data,pos)
            norm=math.hypot(dx,dy)
            if (all(math.isfinite(v) for v in (x,y,dx,dy)) and 0<x<.43 and 0<y<.31
                    and .0001<=norm<=1.001 and (abs(norm-1)<1e-6 or min(abs(dx),abs(dy))/norm<.0001)):
                priority=0 if abs(norm-1)<1e-6 or abs(norm-.001)<1e-9 else 1
                transforms.append((priority,pos,x,y,dx/norm,dy/norm))
        if transforms:
                _,pos,x,y,dx,dy=min(transforms)
                width=struct.unpack_from('<d',data,pos+40)[0]*1000 if pos+56<=end else 0
                width=width if math.isfinite(width) and .5<width<420 else 0
                box_height=struct.unpack_from('<d',data,pos+48)[0]*1000 if pos+56<=end else 0
                border=(pos+56<=end and data[pos+38]==0x10 and width>0
                        and math.isfinite(box_height) and .5<box_height<50)
                records.append({'text':text.rstrip('\x00'),'x':x,'y':y,'direction_x':dx,'direction_y':dy,
                                'alignment_flags':data[pos+35] if pos+36<=end else None,
                                'text_box_width_mm':width,
                                'text_box_border':border,'text_box_height_mm':box_height if border else 0,
                                'format_ref':struct.unpack_from('<I',data,o+20)[0],
                                'graphic_ref':ref,'layer_ref':layer,'offset':o,'end':end,'transform_offset':pos})
    return records


def text_formats(data):
    fonts={};links={}
    for m in re.finditer(b'\x2c\x00',data):
        o=m.start()
        if o+78>len(data):continue
        size=struct.unpack_from('<I',data,o+2)[0]
        ref=struct.unpack_from('<I',data,o+20)[0]
        height=struct.unpack_from('<d',data,o+48)[0]*1000
        count=struct.unpack_from('<H',data,o+74)[0]
        if not (76+count*2<=size+6<=len(data)-o and 0<ref<10000 and .2<height<15 and 0<count<80):continue
        family=data[o+76:o+76+count*2].decode('utf-16le',errors='replace')
        if family not in ('Arial','Times New Roman','Courier New','Tahoma','Calibri'):continue
        bold=bool(data[o+38]&1)
        # Width is a uint16 in thousandths for all observed height modes.
        # The adjacent +60 field can be nonzero in frame fonts, so reading a
        # uint32 here incorrectly consumes half of that independent field.
        width=struct.unpack_from('<H',data,o+58)[0]
        width_scale=width/1000 if 100<=width<=5000 else 1.0
        color=struct.unpack_from('<I',data,o+40)[0]
        if color==0xffffff or color>0xffffff:color=0
        fonts[ref]={'family':family,'height':height,'height_mode':data[o+47],'weight':'bold' if bold else 'normal','width_scale':width_scale,
                    'color':f'#{color&255:02x}{color>>8&255:02x}{color>>16&255:02x}'}
    for m in re.finditer(b'\x2d\x00\x5a\x00\x00\x00',data):
        o=m.start()
        if o+96>len(data):continue
        ref=struct.unpack_from('<I',data,o+20)[0];font=struct.unpack_from('<I',data,o+44)[0]
        if font in fonts:
            links[ref]={**fonts[font],'paragraph_alignment':struct.unpack_from('<I',data,o+40)[0]}
    return links


def line_styles(data):
    result={}
    for m in re.finditer(b'\x2e\x00\x36\x00\x00\x00',data):
        o=m.start()
        if o+60>len(data):continue
        ref=struct.unpack_from('<I',data,o+20)[0]
        width=struct.unpack_from('<d',data,o+40)[0]*1000
        color=struct.unpack_from('<I',data,o+56)[0]
        if 0<ref<10000 and math.isfinite(width) and .009<=width<=3 and color<=0xffffff:
            result[ref]={'width':width,'color':f'#{color&255:02x}{color>>8&255:02x}{color>>16&255:02x}'}
    # Some frame lines reference a composite 0x30 shape style, not 0x2e.
    # In this corpus flags 25 declare an explicit outline width; flags 1
    # leave it zero and use the document's default line style (id 1).
    from sha_contours import records
    for _,kind,record in records(data):
        if kind!=0x30 or len(record)!=96:continue
        ref=struct.unpack_from('<I',record,20)[0]
        flags=struct.unpack_from('<I',record,28)[0]
        color=struct.unpack_from('<I',record,44)[0]
        width=struct.unpack_from('<d',record,48)[0]*1000
        if flags==1 and width==0 and 1 in result:width=result[1]['width']
        if flags in (1,25) and math.isfinite(width) and .009<=width<=3 and color<=0xffffff:
            result[ref]={'width':width,'color':f'#{color&255:02x}{color>>8&255:02x}{color>>16&255:02x}'}
    return result


def embedded_images(data,streams):
    images=[]
    for name,bitmap in streams.items():
        match=re.fullmatch(r'JSite(\d+)/CONTENTS',name)
        if not match or not bitmap.startswith(b'BM'):continue
        ref=int(match[1])
        for occurrence in re.finditer(re.escape(struct.pack('<I',ref)),data):
            o=occurrence.start()
            if o<72:continue
            height=struct.unpack_from('<d',data,o-72)[0]
            x,y,width=struct.unpack_from('<3d',data,o-40)
            if not all(math.isfinite(v) and 0<v<.43 for v in (x,y,width,height)):continue
            images.append({'resource':name,'x':x*1000,'y':-y*1000-height*1000,
                           'width':width*1000,'height':height*1000,
                           'href':'data:image/bmp;base64,'+base64.b64encode(bitmap).decode('ascii')})
    return images
