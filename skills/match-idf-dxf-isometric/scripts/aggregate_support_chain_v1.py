#!/usr/bin/env python3
"""Conservatively contract DXF fragments split only by verified supports.

This is a validation utility, not a blanket rule that a support can be ignored.
It joins adjacent final-pipe fragments only at an exactly shared endpoint typed
as SUPPORT on both fragments.  A SUPPORT_EMPTY fragment is never contracted:
its free end is page/topology evidence, not CAD fragmentation.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path

import ezdxf


def distance(a, b):
    return math.dist(a, b)


def load_chain_module():
    path = Path(__file__).with_name('match_chain_100_v1.py')
    spec = importlib.util.spec_from_file_location('chain_100_v1', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def endpoint_is_support(row, point, tolerance=.15):
    for annotation in row.get('endpoint_annotations') or []:
        if annotation.get('support') and distance(point, annotation['point']) <= tolerance:
            return True
    return False


def ordered_items(dxf, records):
    """Use the existing conservative path construction, preserving raw rows."""
    chain = load_chain_module()
    doc = ezdxf.readfile(dxf)
    raw = json.loads(records.read_text())
    by_id = {row['id']: row for row in raw}
    ordered = chain.dxf_chain(dxf, records)
    for item in ordered:
        item['source_row'] = by_id[item['record_id']]
    return ordered


def shared_support(a, b):
    """Return the common support point, or None when contraction is forbidden."""
    if a['kind'] == 'support_empty_pipe' or b['kind'] == 'support_empty_pipe':
        return None
    for p in (a['a'], a['b']):
        for q in (b['a'], b['b']):
            if distance(p, q) <= .15 and endpoint_is_support(a['source_row'], p) and endpoint_is_support(b['source_row'], q):
                return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    return None


def aggregate(ordered):
    groups = []
    current = [ordered[0]]
    cuts = []
    for left, right in zip(ordered, ordered[1:]):
        anchor = shared_support(left, right)
        if anchor is not None:
            current.append(right)
            cuts.append({'left': left['record_id'], 'right': right['record_id'], 'anchor': anchor,
                         'reason': 'shared_endpoint_is_support_on_both_fragments'})
        else:
            groups.append(current)
            current = [right]
    groups.append(current)
    result = []
    for number, members in enumerate(groups, 1):
        result.append({
            'group_id': f'G{number:03d}',
            'members': [x['record_id'] for x in members],
            'handles': [h for x in members for h in x['handles']],
            'member_kinds': [x['kind'] for x in members],
            'length': sum(x['length'] for x in members),
            'a': members[0]['a'], 'b': members[-1]['b'],
            'contains_arrow': any(x['kind'] == 'arrow_pipe' for x in members),
        })
    return result, cuts


def evaluate(idfs, groups):
    if len(idfs) != len(groups):
        raise ValueError(f'not_aggregate_eligible: IDF 100={len(idfs)}, support-contracted groups={len(groups)}')
    idf_total = sum(x['length'] for x in idfs)
    dxf_total = sum(x['length'] for x in groups)
    def score(reverse=False):
        source = list(reversed(groups)) if reverse else groups
        rows = []
        total = 0.0
        for slot, (idf, group) in enumerate(zip(idfs, source)):
            arrow = 4 if idf['context']['flow'] and group['contains_arrow'] else 1 if not idf['context']['flow'] and not group['contains_arrow'] else 0
            terminal = 2 if idf['context']['terminal'] and slot == len(idfs)-1 else 0
            relative_length = max(0, 3 - 12 * abs(idf['length']/idf_total - group['length']/dxf_total))
            parts = {'role': arrow, 'terminal': terminal, 'relative_length': relative_length}
            value = sum(parts.values()); total += value
            rows.append({'idf_id': idf['id'], 'dxf_group': group['group_id'],
                         'members': group['members'], 'handles': group['handles'],
                         'score': round(value, 3), 'components': parts})
        return total, rows
    return score(False), score(True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('idf', type=Path); ap.add_argument('dxf', type=Path); ap.add_argument('dxf_records', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    chain = load_chain_module()
    try:
        idfs = chain.idf_pipes(args.idf)
        ordered = ordered_items(args.dxf, args.dxf_records)
        groups, contracted = aggregate(ordered)
        (forward, rows), (reverse, backwards) = evaluate(idfs, groups)
        winning = rows if forward >= reverse else backwards
        margin = abs(forward - reverse)
        result = {'algorithm': 'SUPPORT_CONTRACTION_CHAIN_V1', 'eligible': True,
                  'idf_100_count': len(idfs), 'dxf_fragment_count': len(ordered), 'dxf_group_count': len(groups),
                  'contractions': contracted, 'groups': groups, 'idf': idfs,
                  'forward_score': round(forward, 3), 'reverse_score': round(reverse, 3),
                  'orientation_margin': round(margin, 3),
                  'confidence': 'high' if margin >= 3 else 'medium' if margin >= 1 else 'unresolved',
                  'matches': winning}
    except ValueError as error:
        result = {'algorithm': 'SUPPORT_CONTRACTION_CHAIN_V1', 'eligible': False, 'reason': str(error)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({key: result.get(key) for key in ('algorithm','eligible','idf_100_count','dxf_fragment_count','dxf_group_count','forward_score','reverse_score','orientation_margin','reason')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
