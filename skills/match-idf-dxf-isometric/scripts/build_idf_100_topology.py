#!/usr/bin/env python3
"""Extract an IDF geometry graph while preserving stable 100 IDs and branches."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def parse(path: Path):
    edges=[]; serial=0
    for line, text in enumerate(path.read_text(errors='replace').splitlines(), 1):
        fields=text.split()
        if len(fields)<8 or not re.fullmatch(r'-?\d+',fields[0]): continue
        try:
            code=int(fields[0]);a=tuple(map(float,fields[1:4]));b=tuple(map(float,fields[4:7]));bore=float(fields[7])
        except ValueError: continue
        if code==100 and not any(',' in value for value in fields[8:]): continue
        if code==100: serial+=1
        edges.append({'line':line,'code':code,'a':a,'b':b,'bore':bore,'id':f'I{serial:03d}' if code==100 else None})
    return edges


class UnionFind:
    def __init__(self, values):
        self.parent = {value: value for value in values}
    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value
    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[b] = a


def main():
    ap=argparse.ArgumentParser();ap.add_argument('idf',type=Path);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    edges=parse(args.idf);nodes=defaultdict(list)
    for edge in edges:
        nodes[edge['a']].append((edge,'a'))
        if edge['b']!=edge['a']:nodes[edge['b']].append((edge,'b'))
    # Ordinary component/weld records (notably 150) make a coordinate's raw
    # incidence look like degree three even on a simple chain.  In these IDFs
    # record 41 is the observed branch/outlet connector.  55 must *not* be
    # used: it occurs on ordinary two-ended inline geometry and previously
    # created false branch anchors.  Retain raw degree as evidence rather than
    # using it as the classification itself.
    branch=[]
    for point,incident in nodes.items():
        non_self=[entry for entry in incident if entry[0]['a']!=entry[0]['b']]
        if any(edge['code'] == 41 for edge,_side in non_self):
            branch.append({'point':point,
                           'role':'junction' if len(non_self) >= 3 else 'outlet_leg',
                           'degree':len(non_self),'incident_100':[edge['id'] for edge,_side in non_self if edge['id']],
                           'incident_codes':[edge['code'] for edge,_side in non_self]})
    pipes=[]
    for edge in edges:
        if edge['code']!=100:continue
        pipes.append({**edge,'endpoint_signature':[
            {'degree':len([x for x in nodes[p] if x[0]['a']!=x[0]['b']]),
                           'codes':sorted(x[0]['code'] for x in nodes[p] if x[0] is not edge)} for p in (edge['a'],edge['b'])]})
    # Preserve the original coordinate graph as labelled records.  This is
    # deliberately not a contraction: codes such as 35/36/55/105/110/130 may
    # describe different inline bodies in different IDFs.  The global matcher
    # may compare their local sequence later, but must not silently treat all
    # of them as interchangeable nodes.
    design = [edge for edge in edges
              if 35 <= edge['code'] <= 150 and edge['a'] != edge['b']
              and edge['a'] != (0.0, 0.0, 0.0) and edge['b'] != (0.0, 0.0, 0.0)]
    raw_points = sorted({point for edge in design for point in (edge['a'], edge['b'])})
    raw_point_ids = {point: f'Q{index:03d}' for index, point in enumerate(raw_points, 1)}
    raw_graph = {
        'nodes': [{'id': raw_point_ids[point], 'point': point} for point in raw_points],
        'edges': [{'id': edge['id'] or f'R{index:03d}', 'record_code': edge['code'], 'line': edge['line'],
                   'a': raw_point_ids[edge['a']], 'b': raw_point_ids[edge['b']], 'bore': edge['bore']}
                  for index, edge in enumerate(design, 1)],
        'policy': 'labelled source records; no generic non-100 contraction',
    }
    # Contract only the empirically verified branch connector (41).  Generic
    # non-100 records such as 35/36/150 can be title/block geometry; blindly
    # contracting them collapses unrelated routes into one artificial node.
    # They remain endpoint context until their IDF semantics are separately
    # verified.
    uf = UnionFind(nodes)
    for edge in edges:
        if edge['code'] == 41 and edge['a'] != edge['b']:
            uf.union(edge['a'], edge['b'])
    groups = defaultdict(list)
    for point in nodes:
        groups[uf.find(point)].append(point)
    group_id = {root: f'N{index:03d}' for index, root in enumerate(groups, 1)}
    graph_nodes = defaultdict(list)
    graph_edges = []
    for pipe in pipes:
        left, right = group_id[uf.find(pipe['a'])], group_id[uf.find(pipe['b'])]
        graph_edges.append({'id': pipe['id'], 'a': left, 'b': right, 'line': pipe['line'], 'bore': pipe['bore']})
        graph_nodes[left].append(pipe['id']); graph_nodes[right].append(pipe['id'])
    contracted_nodes = [
        {'id': node, 'degree': len(incident), 'incident_100': sorted(set(incident)),
         'point_count': len(groups[root])}
        for root, node in group_id.items() for incident in [graph_nodes[node]]
    ]
    # A 150 record is a one-point support location: its second coordinate is
    # conventionally zero.  Preserve it independently of the 100 graph so a
    # matcher can use support locations as visible, non-destructive anchors.
    supports = [{'id': f'S{index:03d}', 'line': edge['line'], 'point': edge['a'], 'bore': edge['bore']}
                for index, edge in enumerate((edge for edge in edges if edge['code'] == 150), 1)]
    branch_connectors = [{'id': f'B{index:03d}', 'line': edge['line'], 'a': edge['a'], 'b': edge['b'],
                          'bore': edge['bore']}
                         for index, edge in enumerate((edge for edge in edges if edge['code'] == 41 and edge['a'] != edge['b']), 1)]
    result={'idf':args.idf.name,'idf_100_count':len(pipes),'geometry_edge_count':len(edges),'branch_nodes':branch,
            'supports_150':supports,'branch_connectors_41':branch_connectors,
            'pipes':pipes,'contracted_pipe_graph':{'nodes':contracted_nodes,'edges':graph_edges}}
    result['raw_geometry_graph'] = raw_graph
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'idf_100_count':len(pipes),'branch_node_count':len(branch),'branches':branch},ensure_ascii=False))
if __name__=='__main__':main()
