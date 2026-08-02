#!/usr/bin/env python3
"""Refresh component-first page-range hypotheses for all unambiguous IDF lines.

This is orchestration only: each source algorithm keeps its own audit JSON.
Lines with zero or multiple IDF files are skipped, because resolving source
identity is itself a topology problem and must never be guessed by filename
ordering.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent


def run(script: str, arguments: list[str]) -> None:
    subprocess.run([sys.executable, str(HERE / script), *arguments], check=True,
                   stdout=subprocess.DEVNULL)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('inventory', type=Path)
    parser.add_argument('idf_root', type=Path)
    parser.add_argument('dxf_topology_dir', type=Path)
    parser.add_argument('continuations', type=Path)
    parser.add_argument('terminal_dir', type=Path)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--lines', nargs='*', help='optional line keys')
    parser.add_argument('--top', type=int, default=12)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text())
    wanted = set(args.lines or [])
    summary = []
    for line in inventory['lines']:
        key = line['line_key']
        if wanted and key not in wanted:
            continue
        idf_files = line.get('idf_files', [])
        if len(idf_files) != 1 or not line.get('dxf_pages'):
            summary.append({'line_key': key, 'status': 'skipped_ambiguous_or_missing_source',
                            'idf_files': idf_files, 'dxf_page_count': len(line.get('dxf_pages', []))})
            continue
        idf = args.idf_root / idf_files[0]
        if not idf.exists():
            summary.append({'line_key': key, 'status': 'skipped_idf_not_found'})
            continue
        terminal = args.terminal_dir / f'{key}.json'
        idf_topology = args.output_root / 'idf-topology' / f'{idf.stem}.json'
        idf_pipe = args.output_root / 'global-idf-pipe-topology' / f'{key}.json'
        dxf_global = args.output_root / 'global-dxf-graphs' / f'{key}.json'
        dxf_pipe = args.output_root / 'global-dxf-pipe-topology' / f'{key}.json'
        frame_graph = args.output_root / 'component-frame-graphs' / f'{key}.json'
        windows = args.output_root / 'component-frame-window-candidates' / f'{key}.json'
        cover = args.output_root / 'component-frame-global-cover' / f'{key}.json'
        for path in [idf_topology, idf_pipe, dxf_global, dxf_pipe, frame_graph, windows, cover]:
            path.parent.mkdir(parents=True, exist_ok=True)
        try:
            run('build_idf_100_topology.py', [str(idf), '--output', str(idf_topology)])
            run('build_idf_pipe_component_topology.py', [str(idf_topology), '--output', str(idf_pipe)])
            global_args = ['--line', key, '--topology-dir', str(args.dxf_topology_dir),
                           '--continuations', str(args.continuations), '--output', str(dxf_global)]
            if terminal.exists():
                global_args += ['--terminal-candidates', str(terminal)]
            run('build_multipage_dxf_global_graph.py', global_args)
            run('build_dxf_pipe_topology_graph.py', [str(dxf_global), '--output', str(dxf_pipe)])
            run('build_component_frame_graphs.py', [str(idf_topology), str(idf_pipe), str(dxf_global),
                                                    str(dxf_pipe), '--output', str(frame_graph)])
            run('score_dxf_page_idf_frame_windows_v1.py', [str(frame_graph), '--output', str(windows),
                                                            '--top', str(args.top)])
            run('solve_global_frame_window_cover_v1.py', [str(windows), str(frame_graph),
                                                          '--output', str(cover), '--per-page', str(args.top)])
            result = json.loads(cover.read_text())
            summary.append({'line_key': key, 'status': result['status'],
                            'idf_100_count': result['idf_100_count'],
                            'page_ranges': result['best']['page_ranges'],
                            'missing': result['best']['missing_indices'],
                            'duplicates': result['best']['duplicate_indices']})
        except subprocess.CalledProcessError as error:
            summary.append({'line_key': key, 'status': 'algorithm_error', 'returncode': error.returncode})
    output = args.output_root / 'component-frame-batch-summary.json'
    output.write_text(json.dumps({'algorithm': 'BATCH_COMPONENT_FRAME_MATCHING_V1',
                                  'policy': 'no CONT matching; skips ambiguous IDF source identity',
                                  'lines': summary}, ensure_ascii=False, indent=2))
    print(json.dumps({'output': str(output), 'lines': len(summary),
                      'unique_exact': sum(x['status'] == 'topology_global_unique_exact_cover_candidate' for x in summary)},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
