#!/usr/bin/env python3
"""Create component-first structural frames for IDF and DXF before pipe matching."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def direction(edge):
    vector = [edge['b'][index] - edge['a'][index] for index in range(3)]
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else [0, 0, 0]


def idf_frame(idf, topology):
    pipes = {pipe['id']: pipe for pipe in idf['pipes']}
    all_points = [point for pipe in pipes.values() for point in (pipe['a'], pipe['b'])]
    origin = [min(point[axis] for point in all_points) for axis in range(3)]
    def project(point):
        x, y, z = [point[axis] - origin[axis] for axis in range(3)]
        return [(x - y) * .5, (x + y) * .288675 - z * .57735]
    frames = []
    for connector in topology['connector_hyperedges']:
        members = [pipes[pipe_id] for pipe_id in connector['incident_pipes']]
        if connector['degree'] == 3 and connector['record_codes'] == [41]:
            frame_type = 'junction_3'
        elif connector['degree'] == 2:
            first, second = (direction(member) for member in members)
            dot = abs(sum(left * right for left, right in zip(first, second)))
            frame_type = 'turn_2' if dot < .995 else 'inline_2'
        elif connector['degree'] == 1:
            frame_type = 'terminal_1'
        else:
            frame_type = f'connector_{connector["degree"]}'
        bores = sorted({member['bore'] for member in members})
        frames.append({'id': connector['id'], 'kind': frame_type, 'degree': connector['degree'],
                       'record_codes': connector['record_codes'], 'incident_pipes': connector['incident_pipes'],
                       'bore_change': len(bores) > 1, 'bores': bores,
                       'outlet_pipes': connector.get('outlet_pipes', []),
                       'centre3': connector.get('centre3'),
                       'centre': project(connector['centre3']) if connector.get('centre3') else None})
    return {'side': 'idf', 'frames': frames,
            'pipe_geometry': [{'id': pipe['id'], 'a': pipe['a'], 'b': pipe['b'],
                               'a2': project(pipe['a']), 'b2': project(pipe['b']), 'bore': pipe['bore']}
                              for pipe in pipes.values()],
            'pipe_frame_incidence': [{'pipe': pipe_id, 'frames': [frame['id'] for frame in frames if pipe_id in frame['incident_pipes']]}
                                     for pipe_id in pipes]}


def dxf_frame(global_graph, topology):
    components = {component['id']: component for component in global_graph['components']}
    frames = []
    for hyperedge in topology['through_component_hyperedges']:
        component = components[hyperedge['component_id']]
        frames.append({'id': hyperedge['component_id'], 'kind': component['kind'], 'degree': hyperedge['degree'],
                       'page': component['page'], 'centre': component['centre'],
                       'incident_pipes': [member['pipe'] for member in hyperedge['incident_pipes']]})
    pages = {pipe['id']: pipe['page'] for pipe in topology['pipes']}
    return {'side': 'dxf', 'frames': frames,
            'pipe_frame_incidence': [{'pipe': pipe['id'], 'page': pages[pipe['id']],
                                      'frames': [frame['id'] for frame in frames if pipe['id'] in frame['incident_pipes']]}
                                     for pipe in topology['pipes']]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('idf_topology', type=Path)
    parser.add_argument('idf_pipe_topology', type=Path)
    parser.add_argument('global_dxf_graph', type=Path)
    parser.add_argument('dxf_pipe_topology', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    idf = json.loads(args.idf_topology.read_text())
    idf_topology = json.loads(args.idf_pipe_topology.read_text())
    global_dxf = json.loads(args.global_dxf_graph.read_text())
    dxf_topology = json.loads(args.dxf_pipe_topology.read_text())
    result = {'algorithm': 'COMPONENT_FIRST_FRAME_GRAPH_V1', 'line_key': global_dxf['line_key'],
              'policy': 'build and align structural frames before assigning any individual pipe',
              'idf': idf_frame(idf, idf_topology), 'dxf': dxf_frame(global_dxf, dxf_topology)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'line_key': result['line_key'], 'idf_frames': len(result['idf']['frames']),
                      'dxf_frames': len(result['dxf']['frames']),
                      'idf_junctions': sum(frame['kind'] == 'junction_3' for frame in result['idf']['frames']),
                      'dxf_branch_tee': sum(frame['kind'] in {'branch', 'tee'} for frame in result['dxf']['frames'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
