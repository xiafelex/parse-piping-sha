"""Export source-scoped design spool / cut-piece membership, preserving ambiguity."""
import csv
import json
from collections import defaultdict, Counter
from pathlib import Path
import olefile
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from pod_decode import decode
from export_pod_relationships import properties, value, summary_properties
from compare_three_format_fields import pcf_read

ROOT = Path(__file__).resolve().parents[4]
PROJECT = ROOT / '阿联酋项目'
OUT = PROJECT / '设计预制段与下料段明细'


def key(v):
    return '' if v is None else str(v)


def main():
    OUT.mkdir(exist_ok=True)
    tables = defaultdict(list)
    files = sorted(p for d in PROJECT.glob('30201*') for p in d.rglob('*-pod.pod'))
    for index, path in enumerate(files):
        base = path.name.removesuffix('-pod.pod')
        source = str(path.relative_to(ROOT))
        with olefile.OleFileIO(path) as ole:
            model = decode(ole.openstream('PODFile').read())
        summaries, cuts = [], []
        for s in model['summaries']:
            names = [x['value'] for x in s['header_strings']]
            p = summary_properties(s)
            obj = {'raw': s, 'p': p, 'seq': key(p.get('SEQUENCE-NUMBER')),
                   'id': key(p.get('IDENTIFIER')), 'key': model['document_guid']+':'+str(s['offset'])}
            if len(names)>1 and names[1]=='Spool-Summary':summaries.append(obj)
            if len(names)>1 and names[1]=='Cut-Piece-Summary':cuts.append(obj)
        spools = defaultdict(list)
        for s in summaries:spools[s['seq']].append(s)
        cuts_by_seq = defaultdict(list)
        for c in cuts:cuts_by_seq[c['seq']].append(c)
        pcf = path.with_name(base+'-pcf.pcf')
        blocks, materials, units = pcf_read(pcf)
        pcb = defaultdict(list)
        for b in blocks:
            for uci in b['attributes'].get('UCI', []):pcb[uci].append(b)
        components = {c['object_guid']:c for c in model['components']}
        info = {}
        member_by_spool, member_by_cut = defaultdict(list), defaultdict(list)
        for c in model['components']:
            p = properties(c)
            seq, cseq = key(value(p,'SPOOL-SEQUENCE-NUMBER')), key(value(p,'CUT-PIECE-SEQUENCE-NUMBER'))
            evidence = p.get('SPOOL-SEQUENCE-NUMBER',{}).get('name_evidence','')
            certainty = '明确属性' if evidence=='explicit POD schema id' else '名称候选' if seq else '无分段属性'
            candidates = cuts_by_seq.get(cseq, []) if cseq else []
            if seq:
                filtered = [s for s in candidates if key(s['p'].get('SPOOL-SEQUENCE-NUMBER'))==seq]
            else:filtered = candidates
            cut_status = '序号及归属一致' if seq and filtered else '仅下料序号关联' if filtered else '未关联'
            if candidates and seq and not filtered:cut_status='序号存在但预制段冲突'
            if not seq and len(filtered)==1 and p.get('CUT-PIECE-SEQUENCE-NUMBER',{}).get('name_evidence')=='explicit POD schema id':
                seq=key(filtered[0]['p'].get('SPOOL-SEQUENCE-NUMBER'))
                certainty='由唯一切管汇总推导'
            mids={mid for b in pcb[c['uci']] for mid in b['attributes'].get('MATERIAL-IDENTIFIER',[])}
            codes=sorted({materials[m].get('ITEM-CODE','') for m in mids if m in materials}-{''})
            descriptions=sorted({materials[m].get('DESCRIPTION','') for m in mids if m in materials}-{''})
            common={'图号':base,'源POD':source,'构件GUID':c['object_guid'],'UCI':c['uci'],
                    '构件类型':c['type_name'],'设计预制段序号':seq,'归属依据':certainty,
                    '原Spool字段名称依据':evidence,'下料序号':cseq,
                    '材料表零件号':value(p,'REPEAT-PART-NUMBER'),
                    'PCF构件编号':';'.join(v for b in pcb[c['uci']] for v in b['attributes'].get('COMPONENT-IDENTIFIER',[])),
                    '材料代码':';'.join(codes),'材料描述':value(p,'ITEM-DESCRIPTION') or ';'.join(descriptions),
                    '下料长度_构件原值':value(p,'CUT-PIECE-LENGTH'),
                    'PCF坐标单位':units.get('UNITS-CO-ORDS',''),
                    '口径_原值':';'.join(sorted({str(t['bore']) for t in c['ports']})),
                    'PCF口径单位':units.get('UNITS-BORE',''),
                    '设计焊接分类':value(p,'WELD-ATTRIBUTE3'),
                    '焊口编号原值':value(p,'REPEAT-WELD-IDENTIFIER'),
                    'POD构件偏移':c['offset']}
            info[c['object_guid']]=common
            assigned=spools.get(seq,[]) if seq else []
            if len(assigned)==1:
                row={**common,'预制段关联键':assigned[0]['key'],'预制段原始标识':assigned[0]['id']}
                tables['预制段组成件'].append(row);member_by_spool[assigned[0]['key']].append(row)
            else:
                tables['未关联预制段构件'].append({**common,'原因':'无分段属性' if not seq else '序号无汇总或汇总不唯一'})
            for cut in filtered:
                row={**common,'下料段关联键':cut['key'],'下料段原始标识':cut['id'],
                     '归属汇总个数':len(filtered),'关联状态':cut_status,
                     '对象角色':'管对象' if c['type_name']=='Pipe' else '辅助对象_不计作管材料'}
                tables['下料段组成对象'].append(row);member_by_cut[cut['key']].append(row)
            if cseq and not filtered:
                tables['关联异常'].append({'图号':base,'源POD':source,'构件GUID':c['object_guid'],
                    '下料序号':cseq,'设计预制段序号':seq,'问题':cut_status})
        for cut in cuts:
            p=cut['p'];seq=key(p.get('SPOOL-SEQUENCE-NUMBER')); parents=spools.get(seq,[])
            members=member_by_cut[cut['key']];pipes=[r for r in members if r['构件类型']=='Pipe']
            tables['下料段总表'].append({'图号':base,'源POD':source,'下料段关联键':cut['key'],
                '下料段原始标识':cut['id'],'下料序号':cut['seq'],'设计预制段序号':seq,
                '预制段原始标识':parents[0]['id'] if len(parents)==1 else '',
                '预制段关联键':parents[0]['key'] if len(parents)==1 else '',
                '预制段关联状态':'已关联汇总' if len(parents)==1 else '序号0_无预制段汇总' if seq=='0' else '无唯一预制段汇总',
                '端1加工':p.get('END-PREP1'),'端2加工':p.get('END-PREP2'),
                '汇总LENGTH原值':p.get('LENGTH'),
                'LENGTH除1000候选毫米值_未全量确认':p.get('LENGTH')/1000 if isinstance(p.get('LENGTH'),(int,float)) else None,
                '关联管对象数':len(pipes),'关联辅助对象数':len(members)-len(pipes),
                '关联管对象长度原值列表':json.dumps([r['下料长度_构件原值'] for r in pipes]),
                '材料代码':';'.join(sorted({r['材料代码'] for r in pipes}-{''})),
                '关联状态':'单管对象' if len(pipes)==1 else '多个管对象_未强制合并' if pipes else '无管对象',
                '汇总偏移':cut['raw']['offset']})
            for row in pipes:
                c=components[row['构件GUID']]
                for port in c['ports']:
                    if port['name'].startswith('Centre_'):continue
                    peer=components.get(port['peer_object_guid']); beyond=[]
                    if peer and peer['type_name'].endswith('-Weld'):
                        beyond=[components[q['peer_object_guid']] for q in peer['ports']
                            if q['peer_object_guid'] in components and q['peer_object_guid']!=c['object_guid']]
                    tables['下料段端部连接'].append({'图号':base,'下料段关联键':cut['key'],
                        '下料段原始标识':cut['id'],'管对象GUID':c['object_guid'],'管UCI':c['uci'],
                        '端口':port['name'],'端口工程坐标':json.dumps(port['xyz']),
                        '端口图面坐标':json.dumps(port['drawing_xy']),'端口加工代码':port['end_preparation'],
                        '直接连接类型':peer['type_name'] if peer else '',
                        '直接连接UCI':peer['uci'] if peer else '',
                        '跨焊口相邻构件类型':';'.join(x['type_name'] for x in beyond),
                        '跨焊口相邻构件UCI':';'.join(x['uci'] for x in beyond),
                        '说明':'相邻件不自动计入该下料管段；用于后续装配关联'})
        for spool in summaries:
            members=member_by_spool[spool['key']]
            owncuts=[c for c in cuts if key(c['p'].get('SPOOL-SEQUENCE-NUMBER'))==spool['seq']]
            tables['设计预制段总表'].append({'图号':base,'源POD':source,'预制段关联键':spool['key'],
                '预制段原始标识':spool['id'],'设计预制段序号':spool['seq'],
                '下料段数量':len(owncuts),'下料段标识列表':';'.join(c['id'] for c in owncuts),
                '明确及推导组成对象数':sum(r['归属依据']!='名称候选' for r in members),
                '名称候选组成对象数':sum(r['归属依据']=='名称候选' for r in members),
                '明确及推导类型构成':json.dumps(Counter(r['构件类型'] for r in members if r['归属依据']!='名称候选'),ensure_ascii=False),
                '候选类型构成':json.dumps(Counter(r['构件类型'] for r in members if r['归属依据']=='名称候选'),ensure_ascii=False),
                '中心线长度原值':spool['p'].get('CENTRELINE-LENGTH'),
                '重量原值':spool['p'].get('WEIGHT'),'焊接口径累计原值':spool['p'].get('WELD-DIAMETER'),
                '重心XYZ原值':json.dumps([spool['p'].get('CENTRE-OF-GRAVITY-'+axis) for axis in 'XYZ']),
                '汇总偏移':spool['raw']['offset'],'说明':'设计分段；不自动等同施工SP号，汇总单位未统一换算'})
        if (index+1)%20==0:print(f'processed {index+1}/{len(files)}',flush=True)
    guide=[['概念','说明'],['设计预制段 Spool','设计出图阶段的装配分组；通常由管、管件及预制焊口组成，不直接等同施工最终分段。'],
        ['下料段 Cut piece','一段待切割/加工的管材；可以有开孔或弯制。两端相邻法兰、弯头不是下料管材本身。'],
        ['加工组合 Work piece','围绕下料管材及其焊接零件、焊口的加工组合；本次未声称完整还原该层。'],
        ['序号与编号','SEQUENCE-NUMBER为关联序号；IDENTIFIER为原始标识。输出关联键为本工具追溯键，不是设计编号。'],
        ['范围','全部交付POD，按源文件隔离。记录未跨图/跨版本去重。'],
        ['候选','名称候选来自继承槽位，不能作为已确认施工归属；多管对象保留逐对象明细，不盲目求和或选一个。'],
        ['长度','构件下料长度保留原值；汇总LENGTH及/1000候选值分列，候选换算未全量认证。'],
        ['归属','按明确序号关联，或由唯一切管汇总推导；没有分段信息的对象另表列出，不沿拓扑盲目扩张。'],
        ['连接','端部表中的相邻焊口/管件属于连接对象，不能全算作下料段组成材料。'],
        ['用途','解析核对表，不能直接作为已确认施工分段或最终切管指令。']]
    wb=openpyxl.Workbook();ws=wb.active;ws.title='阅读说明'
    for row in guide:ws.append(row)
    order=['设计预制段总表','预制段组成件','下料段总表','下料段组成对象','下料段端部连接','关联异常','未关联预制段构件']
    for name in order:
        rows=tables[name]
        if not rows:continue
        columns=list(dict.fromkeys(k for r in rows for k in r))
        with (OUT/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(rows)
        ws=wb.create_sheet(name);ws.append(columns)
        for r in rows:ws.append([r.get(k) for k in columns])
    for ws in wb:
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for c in ws[1]:c.font=Font(color='FFFFFF',bold=True);c.fill=PatternFill('solid',fgColor='24476A')
        for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=26
    wb['阅读说明'].column_dimensions['B'].width=110
    wb.save(OUT/'阿联酋_设计预制段与下料段详细信息.xlsx')
    result={'files':len(files),'rows':{k:len(v) for k,v in tables.items()},
        'cut_status':dict(Counter(r['关联状态'] for r in tables['下料段总表'])),
        'membership_evidence':dict(Counter(r['归属依据'] for r in tables['预制段组成件'])),
        'cut_parent_status':dict(Counter(r['预制段关联状态'] for r in tables['下料段总表']))}
    (OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
