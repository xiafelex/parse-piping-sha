"""UAE filled contours, including circular segments in embedded vector marks."""
import math
import struct


def records(data):
    """Walk a complete serialized cluster; never scan arbitrary payload bytes."""
    pos=8
    while pos<len(data):
        if pos+6>len(data):raise ValueError('Truncated cluster record header')
        kind,size=struct.unpack_from('<HI',data,pos)
        end=pos+6+size
        if end>len(data):raise ValueError('Truncated cluster record')
        yield pos,kind,data[pos:end]
        pos=end


def contours(data):
    result=[]
    for offset,kind,record in records(data):
        if kind!=0x13 or len(record)<34:continue
        count=struct.unpack_from('<I',record,24)[0]
        if not 1<=count<=100:continue
        pos=28;paths=[]
        try:
            for _ in range(count):
                segments=struct.unpack_from('<I',record,pos)[0];pos+=4
                if not 1<=segments<=10000 or record[pos:pos+2] not in (b'\x02\x00',b'\x02\x01',b'\x04\x01'):raise ValueError('Unknown contour header')
                pos+=2;points=[]
                for _ in range(segments):
                    opcode=record[pos];pos+=1
                    if opcode==0x67:
                        x1,y1,x2,y2=struct.unpack_from('<4d',record,pos);pos+=32
                        edge=[(x1,y1),(x2,y2)]
                    elif opcode==0x68:
                        n=struct.unpack_from('<I',record,pos)[0];pos+=4
                        if not 3<=n<=20000 or record[pos:pos+2] not in (b'\x02\x01',b'\x03\x01'):raise ValueError('Unknown polygon header')
                        pos+=2;values=struct.unpack_from(f'<{n*2}d',record,pos);pos+=n*16
                        edge=list(zip(values[::2],values[1::2]))
                    elif opcode==0x73:
                        cx,cy,r,rotation,ratio=struct.unpack_from('<5d',record,pos);pos+=40
                        direction=record[pos];pos+=1
                        if not (0<r<1 and abs(rotation)<1e-8 and abs(ratio-1)<1e-8 and direction in (0,1)):raise ValueError('Unverified circle')
                        edge=[(cx+r*math.cos(2*math.pi*i/128),cy+r*math.sin(2*math.pi*i/128)) for i in range(129)]
                    elif opcode==0x74:
                        start,end,cx,cy,r,rotation,ratio=struct.unpack_from('<7d',record,pos);pos+=56
                        direction=record[pos];pos+=1
                        if not (0<r<1 and abs(rotation)<1e-8 and abs(ratio-1)<1e-8 and direction in (0,1)):raise ValueError('Unverified ellipse')
                        sweep=(end-start)%(2*math.pi) if direction else -((start-end)%(2*math.pi))
                        n=max(2,math.ceil(abs(sweep)/.025))
                        edge=[(cx+r*math.cos(start+sweep*i/n),cy+r*math.sin(start+sweep*i/n)) for i in range(n+1)]
                    else:raise ValueError(f'Unknown contour segment {opcode}')
                    if not all(math.isfinite(v) and abs(v)<2 for pt in edge for v in pt):raise ValueError('Invalid coordinates')
                    if points and math.dist(points[-1],edge[0])>1e-6:raise ValueError('Disconnected segments')
                    points.extend(edge if not points else edge[1:])
                paths.append(points)
            trailer=bytes.fromhex('ea8ca039593e2946')*2+b'\x01'
            if record[pos:pos+17]!=trailer:
                x,y=struct.unpack_from('<2d',record,pos)
                if not all(math.isfinite(v) and abs(v)<2 for v in (x,y)) or record[pos+16]!=1:
                    raise ValueError('Unknown contour trailer')
            references=struct.unpack_from('<I',record,pos+17)[0]
            if pos+21+references*8!=len(record):raise ValueError('Invalid contour reference trailer')
            result.append({'offset':offset,'ref':struct.unpack_from('<I',record,6)[0],
                           'style_ref':struct.unpack_from('<I',record,20)[0],
                           'paths':paths,'remaining_bytes':record[pos+17:].hex()})
        except (ValueError,struct.error,IndexError):
            continue
    return result


def filled_contours(data,style_data):
    colors={}
    for _,kind,record in records(style_data):
        if kind==0x30 and len(record)>=48:
            ref=struct.unpack_from('<I',record,20)[0]
            color=struct.unpack_from('<I',record,44)[0]
            if color<=0xffffff:colors[ref]=f'#{color&255:02x}{color>>8&255:02x}{color>>16&255:02x}'
    return [{**shape,'color':colors[shape['style_ref']]} for shape in contours(data) if shape['style_ref'] in colors]


def embedded_instances(data,streams):
    """A JSite holds several symbols; the instance tail selects one owner subtree.

    119/127-byte records share the transform at +43 and group/resource as the
    final two uint32s. The longer form carries optional properties before them.
    Rendering the entire resource incorrectly repeats unrelated symbols.
    """
    from collections import defaultdict
    cache={}
    for offset,kind,record in records(data):
        if kind!=0xce or len(record) not in (119,127):continue
        group,resource=struct.unpack_from('<II',record,len(record)-8)
        cluster=streams.get(f'JSite{resource}/PSMcluster0')
        if cluster is None:continue
        a,b,c,d,x,y,scale=struct.unpack_from('<7d',record,43)
        if not all(math.isfinite(v) for v in (a,b,c,d,x,y,scale)) or not 0<scale<100:continue
        key=(resource,group)
        if key not in cache:
            source=list(records(cluster));children=defaultdict(list);ids=set()
            for _,_,r in source:
                if len(r)<14:continue
                ref,owner=struct.unpack_from('<II',r,6);ids.add(ref);children[owner].append(ref)
            if group not in ids:raise ValueError(f'JSite{resource}: missing referenced symbol group {group}')
            allowed=set();todo=[group]
            while todo:
                ref=todo.pop()
                if ref in allowed:continue
                allowed.add(ref);todo.extend(children[ref])
            cache[key]=cluster[:8]+b''.join(r for _,_,r in source if len(r)>=14 and struct.unpack_from('<I',r,6)[0] in allowed)
        yield {'offset':offset,'resource':resource,'group':group,'cluster':cache[key],
               'matrix':(a,b,c,d,x,y,scale),'style':streams.get(f'JSite{resource}/StyleCluster',b'')}


def embedded_contours(data,streams):
    instances=[]
    for instance in embedded_instances(data,streams):
        offset=instance['offset'];ref=instance['resource'];cluster=instance['cluster']
        a,b,c,d,x,y,scale=instance['matrix']
        shapes=contours(cluster)
        for shape in shapes:
            paths=[[(1000*(x+scale*(a*u+c*v)), -1000*(y+scale*(b*u+d*v))) for u,v in path] for path in shape['paths']]
            instances.append({'resource':f'JSite{ref}/PSMcluster0','instance_offset':offset,'ref':shape['ref'],'paths':paths})
    return instances


def embedded_strokes(data,streams):
    """Decode primitive outlines alongside an embedded symbol's fills.

    Symbol geometry uses the instance scale; pen widths remain paper widths.
    White symbol pens are plotted black, as verified for this corpus's logo.
    """
    from sha_graphics import line_styles,polyline_primitives
    result=[]
    for instance in embedded_instances(data,streams):
        resource=instance['resource'];cluster=instance['cluster'];style_data=instance['style']
        a,b,c,d,x,y,scale=instance['matrix']
        styles=line_styles(style_data)
        shapes=[]
        for pos,kind,record in records(cluster):
            points=[]
            if kind==0x18 and len(record)==56:
                values=struct.unpack_from('<4d',record,24)
                points=list(zip(values[::2],values[1::2]))
            elif kind==0x59 and len(record)==49:
                cx,cy,r=struct.unpack_from('<3d',record,24)
                if not 0<r<1:continue
                points=[(cx+r*math.cos(math.tau*i/256),cy+r*math.sin(math.tau*i/256)) for i in range(257)]
            elif kind==0x61 and len(record)==65:
                cx,cy,r,start,end=struct.unpack_from('<5d',record,24)
                direction=record[64]
                if not (0<r<1 and 0<=start<=math.tau+1e-6 and 0<=end<=math.tau+1e-6 and direction in (0,1)):continue
                sweep=(end-start)%math.tau if direction else -((start-end)%math.tau)
                n=max(2,math.ceil(abs(sweep)/.025))
                points=[(cx+r*math.cos(start+sweep*i/n),cy+r*math.sin(start+sweep*i/n)) for i in range(n+1)]
            if points:
                shapes.append({'ref':struct.unpack_from('<I',record,6)[0],
                               'style_ref':struct.unpack_from('<I',record,20)[0],'points':points})
        shapes.extend(polyline_primitives(cluster))
        for shape in shapes:
            if shape['style_ref'] not in styles:continue
            if not all(math.isfinite(v) and abs(v)<2 for pt in shape['points'] for v in pt):continue
            style=styles[shape['style_ref']]
            points=[(1000*(x+scale*(a*u+c*v)),-1000*(y+scale*(b*u+d*v))) for u,v in shape['points']]
            result.append({'resource':resource,'ref':shape['ref'],'points':points,
                           'width':style['width'],'color':'#000000' if style['color']=='#ffffff' else style['color']})
    return result
