"""Decode POD framing, properties, ports and graphics; retain all unknown bytes."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import _bootstrap
import olefile
from pod_binary import parse_tree, lossless_tree, rebuild_tree
from pod_decode import decode
from pod_render import render
from sha_native import inspect
from cross_format import compare


def save(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def csv_table(path,columns,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(columns);writer.writerows(rows)


def process(path, output):
    output.mkdir(parents=True,exist_ok=True)
    with olefile.OleFileIO(path) as ole:
        if ole.listdir()!=[['PODFile']]:raise ValueError('unsupported additional POD streams')
        data=ole.openstream('PODFile').read()
    root,_=parse_tree(data)
    tree=lossless_tree(root,data)
    rebuilt=rebuild_tree(tree)
    if rebuilt!=data:raise ValueError('PODFile round-trip mismatch')
    model=decode(data)
    source=path.name.removesuffix('-pod.pod')
    pcf=path.with_name(source+'-pcf.pcf')
    sha=path.with_name(source+'.sha')
    sha_info=inspect(sha) if sha.exists() else None
    correlations=compare(model,pcf if pcf.exists() else None,sha_info)
    svg,preview=render(model)
    (output/'pod-preview.svg').write_text(svg,encoding='utf-8')
    save(output/'pod-object-tree.lossless.json',tree)
    save(output/'pod-decoded.json',model)
    csv_table(output/'构件清单.csv',['POD偏移','内部GUID','UCI','类型','材料GUID','端口数','图形组数'],
              [[c['offset'],c['object_guid'],c['uci'],c['type_name'],c['material_guid'],len(c['ports']),len(c['drawing_group_offsets'])] for c in model['components']])
    csv_table(output/'端口坐标.csv',['UCI','端口','X_mm','Y_mm','Z_mm','纸面X_0.001mm','纸面Y_0.001mm','口径','口径单位代码','对端GUID','对端端口','POD偏移'],
              [[c['uci'],p['name'],*p['xyz'],*p['drawing_xy'],p['bore'],p['bore_unit_code'],p['peer_object_guid'],p['peer_port'],p['offset']] for c in model['components'] for p in c['ports']])
    csv_table(output/'构件属性.csv',['UCI','POD偏移','属性ID','属性名','名称依据','数值类型码','值','是否有值'],
              [[c['uci'],p['offset'],p['property_id'],p['name'],p['name_evidence'],p['variant_code'],p['value'],p['present']] for c in model['components'] for p in c['properties']])
    save(output/'cross-format-audit.json',correlations)
    if sha_info:save(output/'sha-evidence.json',sha_info)
    restored=output/'roundtrip.pod'
    if restored.resolve()==path.resolve():raise ValueError('output must not overwrite source')
    # olefile.write_stream zeroes the last sector's slack, changing these PODs.
    # Patch only logical stream bytes through the existing FAT, retaining all
    # unrelated OLE directory/slack bytes from the original container template.
    container=bytearray(path.read_bytes())
    with olefile.OleFileIO(path) as ole:
        entry=ole.direntries[ole._find('PODFile')]
        if entry.size < ole.minisectorcutoff:raise ValueError('mini stream writer not validated')
        sector=entry.isectStart;cursor=0;visited=set()
        while cursor<len(rebuilt):
            if sector in visited:raise ValueError('cyclic FAT')
            visited.add(sector)
            start=(sector+1)*ole.sectorsize
            count=min(ole.sectorsize,len(rebuilt)-cursor)
            if start+count>len(container):raise ValueError('FAT outside container')
            container[start:start+count]=rebuilt[cursor:cursor+count]
            cursor+=count
            if cursor<len(rebuilt):sector=int(ole.fat[sector])
    restored.write_bytes(container)
    with olefile.OleFileIO(restored) as ole:
        if ole.openstream('PODFile').read()!=rebuilt:raise ValueError('rebuilt stream unreadable')
    original_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    roundtrip_hash=hashlib.sha256(restored.read_bytes()).hexdigest()
    summary={'file':str(path.resolve()),'source_sha256':original_hash,'roundtrip_sha256':roundtrip_hash,'byte_identical_roundtrip':original_hash==roundtrip_hash,
             'roundtrip_scope':'unknown payloads preserved; this is not complete semantic decoding', 'stream_bytes':len(data),
             'object_count':model['object_count'],'components':len(model['components']),'ports':sum(len(c['ports']) for c in model['components']),
             'connections':len(model['connections']),'property_values':len(model['property_values']),'declarations':len(model['declarations']),
             'primitive_counts':model['primitive_counts'],'decode_errors':model['decode_errors'],'coverage':model['coverage'],'preview':preview,
             'pcf_missing_uci':correlations.get('pcf',{}).get('missing_in_pod'),
             'sha_line_matches':correlations.get('sha',{}).get('exact_geometry_matches'),
             'sha_arc_matches':correlations.get('sha',{}).get('exact_arc_matches')}
    if not summary['byte_identical_roundtrip']:raise ValueError('OLE roundtrip mismatch')
    save(output/'summary.json',summary)
    return summary


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('input',type=Path)
    cli.add_argument('--out',type=Path,required=True)
    args=cli.parse_args()
    files=[args.input] if args.input.is_file() else sorted(args.input.glob('*-pod.pod'))
    if not files:cli.error('No POD files')
    summaries=[]
    for path in files:
        s=process(path,args.out/path.stem);summaries.append(s)
        print(f"{path.name}: {s['object_count']} objects, {s['components']} components, {s['ports']} ports, SHA line matches={s['sha_line_matches']}, roundtrip={s['byte_identical_roundtrip']}")
    save(args.out/'batch-summary.json',summaries)
    return int(any(s['decode_errors'] for s in summaries))


if __name__=='__main__':raise SystemExit(main())
