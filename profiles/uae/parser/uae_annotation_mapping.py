"""Associate supplied management rows with native SHA owner trees and PCF geometry."""
from pathlib import Path
from collections import defaultdict,Counter
import csv,math,re,struct,sys
from analyze_iso_split import read_sha_streams,forward_owned_dynamic_graphics
from sha_document import document_pages
from sha_contours import records
from sha_native import lines
from sha_graphics import circle_primitives
from benchmark_pod_incremental_value import sha_weld_baseline
sys.path.insert(0,str(Path(__file__).resolve().parents[4]/'阿联酋项目/项目文件解析/PCF工作量清单梳理'))
from build_pcf_register import parse


def read_tables(folder):
    result={}
    for kind,name in [('weld','焊口表.csv'),('support','支架表.csv'),('valve','阀门表.csv'),('flange','法兰表.csv')]:
        path=folder/name;raw=path.read_bytes()
        try:raw.decode('utf-8-sig');encoding='utf-8-sig'
        except UnicodeError:encoding='gb18030'
        rows=[]
        with path.open(encoding=encoding,newline='') as f:
            reader=csv.reader(f);original=next(reader);seen=Counter();headers=[]
            for h in original:
                seen[h]+=1;headers.append(h if seen[h]==1 else h+str(seen[h]))
            for n,values in enumerate(reader,2):
                if not any(v.strip() for v in values):continue
                if kind=='weld' and len(values)==len(headers)+1:
                    keys=headers+['单元号']
                elif len(values)==len(headers):keys=headers
                else:raise ValueError(f'{name} row {n}: unexpected {len(values)} fields')
                row={k:v.strip() for k,v in zip(keys,values)};row['_row']=n;rows.append(row)
        result[kind]=rows
    return result


def coord(value):
    if not value:return None
    try:
        p=tuple(float(v.strip()) for v in value.split(','))
        return p if len(p)==3 and all(math.isfinite(v) for v in p) else None
    except ValueError:return None


class Drawing:
    def __init__(self,pcf,sha):
        self.pcf=pcf;self.sha=sha;self.components=parse(pcf.read_bytes())['components']
        self.byuci={c['uci'].upper():c for c in self.components if c['uci']}
        self.streams=read_sha_streams(sha);self.pages=document_pages(self.streams)
        self.pageby={p['sheet']:p['number'] for p in self.pages}
        self.bind=forward_owned_dynamic_graphics(self.streams.get('Unclustered Dynamic Attributes',b''))
        self.geometry={};self.cached={}
        for p in self.pages:
            sheet=p['sheet'];data=self.streams[sheet];children=defaultdict(list);ids=set()
            for _,_,r in records(data):
                if len(r)<14:continue
                child,parent=struct.unpack_from('<II',r,6);ids.add(child);children[parent].append(child)
            self.geometry[sheet]={'ids':ids,'children':children,'lines':lines(data),'circles':circle_primitives(data)}
        baseline,_=sha_weld_baseline(pcf,sha)
        self.welds=[]
        for row in baseline:
            c=self.byuci.get(row['uci'].upper())
            if not c or c['omitted']:continue
            pts=[v[1][:3] for v in c['points']]
            for q in row['sha_circle_candidates']:
                self.welds.append({'component':c,'xyz':pts[0] if pts else None,'sheet':q['sheet'],
                                   'anchor':(q['x_mm'],297-q['y_mm']),'ref':q['ref']})

    def geometries(self,uci):
        uci=uci.upper()
        if uci in self.cached:return self.cached[uci]
        result=[]
        for sheet,g in self.geometry.items():
            for b in self.bind.get(uci,[]):
                root=b['graphic_ref']
                if root not in g['ids']:continue
                allowed=set();todo=[root]
                while todo:
                    child=todo.pop()
                    if child in allowed:continue
                    allowed.add(child);todo+=g['children'][child]
                ls=[l for l in g['lines'] if l['child_ref'] in allowed]
                points=[(l['xy_metres'][i]*1000,297-l['xy_metres'][i+1]*1000) for l in ls for i in (0,2)]
                points += [(c['x'],297+c['y']) for c in g['circles'] if c['ref'] in allowed]
                if points:
                    box=(min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points))
                    result.append({'sheet':sheet,'root':root,'points':points,'box':box,
                                   'segments':[[l['xy_metres'][0]*1000,297-l['xy_metres'][1]*1000,l['xy_metres'][2]*1000,297-l['xy_metres'][3]*1000] for l in ls],
                                   'anchor':((box[0]+box[2])/2,(box[1]+box[3])/2)})
        self.cached[uci]=result
        return result

    def endpoint_anchor(self,xyz,sheet):
        matches=[w['anchor'] for w in self.welds if w['xyz'] and w['sheet']==sheet and math.dist(xyz,w['xyz'])<=.15]
        if matches:return matches[0]
        return None

    def pipe_point(self,xyz,support=False):
        """Interpolate only inside a straight, UCI-owned PCF PIPE, oriented by a real weld."""
        result=[]
        for c in self.components:
            if c['type']!='PIPE' or c['omitted']:continue
            ends=[p[1][:3] for p in c['points'] if p[0]=='END-POINT']
            if len(ends)!=2:continue
            a,b=ends;v=tuple(y-x for x,y in zip(a,b));den=sum(q*q for q in v)
            if den<=0:continue
            t=sum((x-y)*z for x,y,z in zip(xyz,a,v))/den
            nearest=tuple(x+t*y for x,y in zip(a,v));error=math.dist(nearest,xyz)
            tolerance=max(.2,ends and c['points'][0][1][3]*25.4/2+50) if support else .2
            if error>tolerance or not -.0001<=t<=1.0001:continue
            for g in self.geometries(c['uci']):
                points=g['points']
                # The two extremes are actual line endpoints, including broken line gaps.
                u,z=max(((u,z) for u in points for z in points),key=lambda pair:math.dist(*pair))
                aa=self.endpoint_anchor(a,g['sheet']);bb=self.endpoint_anchor(b,g['sheet'])
                if aa is None and bb is None:continue
                evidence=aa or bb
                near=min((u,z),key=lambda p:math.dist(p,evidence))
                far=z if near==u else u
                if math.dist(near,evidence)>1.5:continue
                pa,pb=(near,far) if aa else (far,near)
                anchor=(pa[0]+t*(pb[0]-pa[0]),pa[1]+t*(pb[1]-pa[1]))
                result.append({'sheet':g['sheet'],'anchor':anchor,'root':g['root'],'method':'管内分段坐标＋同UCI管子图元＋端部焊点定向',
                               'pipe_uci':c['uci'],'fraction':t,'coordinate_error_mm':error})
        # Sharing a connection point is valid; accept only one distinct paper anchor.
        unique={ (v['sheet'],round(v['anchor'][0],2),round(v['anchor'][1],2)):v for v in result }
        return list(unique.values())

    def map_weld(self,row):
        xyz=coord(row['焊点位置'])
        if xyz is None:return [],'焊点坐标无效'
        direct=[w for w in self.welds if w['xyz'] and math.dist(xyz,w['xyz'])<=.15]
        unique={(w['sheet'],round(w['anchor'][0],2),round(w['anchor'][1],2)):w for w in direct}
        if len(unique)==1:
            w=next(iter(unique.values()));return [{'sheet':w['sheet'],'anchor':w['anchor'],'root':w['ref'],'method':'焊点工程坐标＋PCF UCI＋SHA焊点'}],''
        if len(unique)>1:return [],'坐标对应多个不同图页或焊点'
        interpolated=self.pipe_point(xyz)
        if len(interpolated)==1:return interpolated,''
        return [],'分段焊点定位不唯一' if interpolated else '未找到图面焊点或可定向管内位置'

    def map_management(self,kind,rows):
        ucis={u.upper() for r in rows for u in re.findall(r'\{[^}]+\}',r['UCI'])}
        geoms=[g for u in sorted(ucis) for g in self.geometries(u)]
        grouped=defaultdict(list)
        for g in geoms:grouped[g['sheet']].append(g)
        if len(grouped)!=1:
            if kind=='support' and not grouped:
                xyz=coord(rows[0]['支架坐标'])
                if xyz:
                    fallback=self.pipe_point(xyz,support=True)
                    if len(fallback)==1:
                        fallback[0]['method']='支架表工程坐标投影至同UCI主管线；管外偏移已记录'
                        return fallback,''
                    centers=[]
                    for c in self.components:
                        if c['omitted'] or c['type'] in ('SUPPORT','WELD'):continue
                        pts=[p[1][:3] for p in c['points'] if p[0]=='CENTRE-POINT']
                        if any(math.dist(xyz,p)<=.2 for p in pts):
                            for g in self.geometries(c['uci']):centers.append({**g,'method':'支架工程坐标与构件中心重合＋该构件UCI图元'})
                    if len(centers)==1:return centers,''
            return [],'UCI未对应唯一图页，坐标补充定位也不唯一'
        sheet,items=next(iter(grouped.items()));anchors=[g['anchor'] for g in items]
        if max((math.dist(a,b) for a in anchors for b in anchors),default=0)>12:
            return [],'同编号UCI的图面位置相距超过12mm'
        anchor=(sum(p[0] for p in anchors)/len(anchors),sum(p[1] for p in anchors)/len(anchors))
        return [{'sheet':sheet,'anchor':anchor,'root':items[0]['root'],'method':'管理表UCI＋SHA图元所属对象组','ucis':sorted(ucis)}],''

    def map_spools(self,ref,rows,weld_targets):
        """A supplied spool is a group of shop welds, not a fabricated pipe ID.

        Place one double-frame tag on each page containing its supplied welds.
        Prefer the midpoint of a constituent straight pipe between its actual
        table weld endpoints; otherwise use a supplied shop weld as the anchor.
        """
        groups=defaultdict(list);component_welds=defaultdict(list)
        for row in rows:
            if row.get('管段编号'):groups[row['管段编号']].append(row)
            point=coord(row['焊点位置'])
            if point:
                for key in ('元件编号','元件编号2'):
                    if row.get(key):component_welds[row[key]].append(point)
        mapped_by_number={t['number']:t for t in weld_targets if t['kind']=='weld'}
        targets=[];audit=[]
        for number,members in groups.items():
            welds=[mapped_by_number[r['焊点编号']] for r in members if r['焊点编号'] in mapped_by_number]
            pages={t['sheet'] for t in welds}
            if not pages:raise ValueError(f'{ref}: spool {number} has no mapped constituent weld')
            candidates=defaultdict(list);component_ids=set()
            for r in members:
                for side in (1,2):
                    key='元件编号' if side==1 else '元件编号2';cid=r.get(key,'')
                    if not cid:continue
                    component_ids.add(cid)
                    description=r.get('描述1' if side==1 else '描述2','').upper()
                    if not description.startswith('PIPE,'):continue
                    points=component_welds[cid]
                    if len(points)<2:continue
                    a,b=max(((a,b) for a in points for b in points),key=lambda pair:math.dist(*pair))
                    length=math.dist(a,b)
                    if length<=.2:continue
                    midpoint=tuple((x+y)/2 for x,y in zip(a,b))
                    positions=self.pipe_point(midpoint)
                    if len(positions)!=1:continue
                    pos=positions[0]
                    if pos['sheet'] in pages:
                        candidates[pos['sheet']].append({**pos,'score':length,'component_id':cid,
                            'method':'管段组成管子两端焊点中点＋PCF直管UCI图元'})
            for sheet in sorted(pages,key=lambda s:self.pageby[s]):
                if candidates[sheet]:pos=max(candidates[sheet],key=lambda v:v['score'])
                else:
                    page_welds=[t for t in welds if t['sheet']==sheet]
                    center=(sum(t['anchor'][0] for t in page_welds)/len(page_welds),sum(t['anchor'][1] for t in page_welds)/len(page_welds))
                    chosen=min(page_welds,key=lambda t:math.dist(t['anchor'],center))
                    pos={'sheet':sheet,'anchor':chosen['anchor'],'root':chosen['root'],
                         'method':'管段所属预制焊口图元；未推造管子中点'}
                targets.append({'kind':'spool','number':number,'weld_type':'','page':self.pageby[sheet],
                    **pos,'source_rows':[r['_row'] for r in members],
                    'source_welds':[r['焊点编号'] for r in members],'component_ids':sorted(component_ids)})
                audit.append({'图号':ref,'类别':'spool','编号':number,'来源行数':len(members),
                    '焊点类型':'','管段编号':number,'实际页码':self.pageby[sheet],'状态':'已定位','原因':'',
                    '定位方法':pos['method'],'图面坐标_mm':list(pos['anchor']),
                    '来源CSV行号':','.join(str(r['_row']) for r in members),
                    '所属焊口':','.join(r['焊点编号'] for r in members),'组成元件编号':';'.join(sorted(component_ids))})
        return targets,audit

    def map_tables(self,ref,tables):
        targets=[];audit=[]
        for kind,allrows in tables.items():
            rows=[r for r in allrows if r['管线号']==ref]
            labelkey={'weld':'焊点编号','support':'支架编号','valve':'阀门号','flange':'法兰编号'}[kind]
            groups=defaultdict(list)
            for r in rows:groups[r[labelkey]].append(r)
            for label,members in groups.items():
                if kind=='weld':
                    identities={(r['焊点位置'],r['焊点类型'],r.get('管段编号','')) for r in members}
                    if len(identities)>1:raise ValueError(f'{ref}: conflicting weld identity {label}')
                    positions,error=self.map_weld(members[0])
                else:positions,error=self.map_management(kind,members)
                for pos in positions:
                    targets.append({'kind':kind,'number':label,'weld_type':members[0].get('焊点类型',''),
                                    'spool_number':members[0].get('管段编号',''),
                                    'page':self.pageby[pos['sheet']],**pos,'source_rows':[r['_row'] for r in members]})
                audit.append({'图号':ref,'类别':kind,'编号':label,'来源行数':len(members),
                              '焊点类型':members[0].get('焊点类型',''),
                              '管段编号':members[0].get('管段编号',''),
                              '实际页码':self.pageby[positions[0]['sheet']] if positions else '',
                              '状态':'已定位' if positions else '未定位','原因':error,
                              '定位方法':positions[0]['method'] if positions else '',
                              '图面坐标_mm':list(positions[0]['anchor']) if positions else [],
                              '来源CSV行号':','.join(str(r['_row']) for r in members)})
        spools,spool_audit=self.map_spools(ref,[r for r in tables['weld'] if r['管线号']==ref],targets)
        return targets+spools,audit+spool_audit
