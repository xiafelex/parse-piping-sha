#!/usr/bin/env python3
"""Derive one reviewable page range from a global-cover geometry audit.

It deliberately cannot resolve the full cover: only a page whose *local*
range is geometrically separated from every different competing range is
emitted.  Downstream propagation may use that one page only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def row_for_page(candidate, page):
    return next((row for row in candidate["pages"] if row["page"] == page), None)


def number(value):
    return int(value[1:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("global_cover", type=Path)
    parser.add_argument("geometry_audit", type=Path)
    parser.add_argument("--page", required=True, type=int)
    parser.add_argument("--minimum-margin", type=float, default=.10)
    parser.add_argument("--allow-boundary-intersection", action="store_true",
                        help="when equal top ranges differ only at a boundary, emit their common interior only")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cover = json.loads(args.global_cover.read_text())
    audit = json.loads(args.geometry_audit.read_text())
    choices = {}
    for candidate in audit.get("candidates", []):
        row = row_for_page(candidate, args.page)
        geometry = row and row.get("geometry")
        if geometry is None:
            continue
        key = tuple(row["idf_range"])
        choices[key] = max(choices.get(key, float("-inf")), geometry["mean_cosine"])
    ranked = sorted(((score, key) for key, score in choices.items()), reverse=True)
    if len(ranked) < 2:
        raise SystemExit("page range needs at least two distinct candidate ranges")
    score, target_range = ranked[0]
    top_ranges = [key for value, key in ranked if abs(value - score) < 1e-6]
    if len(top_ranges) == 1:
        if score - ranked[1][0] < args.minimum_margin:
            raise SystemExit("page range is not geometrically separated from a different candidate range")
        status = "local_geometry_page_range_validated"
        competing_top_ranges = [list(target_range)]
        lower_range, lower_margin = list(ranked[1][1]), round(score - ranked[1][0], 5)
    elif args.allow_boundary_intersection:
        next_lower = next((value for value, _key in ranked if value < score - 1e-6), None)
        if next_lower is None or score - next_lower < args.minimum_margin:
            raise SystemExit("equal top ranges have no geometrically separated lower alternative")
        start = max(number(key[0]) for key in top_ranges)
        end = min(number(key[1]) for key in top_ranges)
        if start > end:
            raise SystemExit("equal top ranges do not share a contiguous interior")
        target_range = (f"I{start:03d}", f"I{end:03d}")
        status = "local_geometry_page_interior_validated"
        competing_top_ranges = [list(key) for key in top_ranges]
        lower_range = list(next(key for value, key in ranked if value == next_lower))
        lower_margin = round(score - next_lower, 5)
    else:
        raise SystemExit("top geometry ranges tie; use --allow-boundary-intersection only for their common interior")
    selected = None
    candidates = [cover["best"]] + cover.get("alternatives", [])
    for candidate in candidates:
        page_row = next((row for row in candidate["page_ranges"] if row["page"] == args.page), None)
        if page_row and (tuple(page_row["idf_range"]) == target_range or
                         (status == "local_geometry_page_interior_validated" and
                          number(page_row["idf_range"][0]) <= number(target_range[0]) and
                          number(page_row["idf_range"][1]) >= number(target_range[1]))):
            selected = candidate
            break
    if selected is None:
        raise SystemExit("geometry-selected range missing from source global cover")
    result = {"algorithm": "LOCAL_GEOMETRY_PAGE_RANGE_V1", "line_key": cover.get("line_key"),
              "status": status, "page": args.page,
              "idf_range": list(target_range), "mean_cosine": score,
              "competing_top_ranges": competing_top_ranges, "lower_geometry_range": lower_range,
              "lower_geometry_margin": lower_margin,
              "policy": "validates this page range only; all other page ranges remain unresolved",
              # Retain the exact schema needed by the existing page-only
              # propagator, but consumers must use the stated page only.
              "best": selected}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({key: result[key] for key in ("line_key", "page", "idf_range", "lower_geometry_margin")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
