#!/usr/bin/env python3
"""Decode IDF option-switch 42 and audit it against the source DXF north arrow.

The ASCII IDF header serializes 140 ISOGEN option switches as ten rows of
fourteen integers.  Switch 42 is row 3, column 14 (one based).  It controls
the isometric viewpoint by prescribing the screen bearing of the ``N`` arrow.

The DXF itself is never transformed.  This utility only declares or rejects
the bearing that an IDF E/N/Z reconstruction may be aligned to.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


# Alias/ISOGEN Option Switch 42, View Point Control.  Values 3--6 select a
# corner; 7--10 are the corresponding boxed north-symbol variants.
VIEWPOINTS = {
    3: ("top_left", False, (-1.0, 1.0)),
    4: ("bottom_right", False, (1.0, -1.0)),
    5: ("top_right", False, (1.0, 1.0)),
    6: ("bottom_left", False, (-1.0, -1.0)),
    7: ("top_left", True, (-1.0, 1.0)),
    8: ("bottom_right", True, (1.0, -1.0)),
    9: ("top_right", True, (1.0, 1.0)),
    10: ("bottom_left", True, (-1.0, -1.0)),
}


def read_switches(path: Path) -> list[int]:
    rows = []
    for text in path.read_text(errors="replace").splitlines()[:10]:
        fields = text.split()
        if len(fields) != 14:
            raise ValueError(f"IDF option-switch row has {len(fields)} fields, expected 14: {text!r}")
        rows.extend(int(field) for field in fields)
    if len(rows) != 140:
        raise ValueError(f"IDF option-switch header has {len(rows)} values, expected 140")
    return rows


def bearing(vector: tuple[float, float]) -> float:
    return math.degrees(math.atan2(vector[1], vector[0])) % 360.0


def circular_delta(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("idf", type=Path)
    parser.add_argument("--north-audit", type=Path,
                        help="output of extract_north_reference_v1.py; optional for header-only decoding")
    parser.add_argument("--max-bearing-delta-deg", type=float, default=30.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    switches = read_switches(args.idf)
    value = switches[41]  # switch number 42, zero-based array index 41
    result = {
        "algorithm": "IDF_OPTION42_VIEWPOINT_AUDIT_V1",
        "idf": str(args.idf),
        "header_layout": "10 rows x 14 values; option 42 = row 3, column 14 (one based)",
        "option_switch_42": value,
        "status": "unsupported_option42_value",
    }
    if value in VIEWPOINTS:
        corner, boxed, vector = VIEWPOINTS[value]
        expected_bearing = bearing(vector)
        result.update({
            "north_arrow_corner": corner,
            "north_arrow_boxed": boxed,
            "expected_source_vector": [round(component / math.sqrt(2.0), 6) for component in vector],
            "expected_bearing_deg": round(expected_bearing, 3),
            "status": "viewpoint_declared_only",
        })

    if args.north_audit:
        north = json.loads(args.north_audit.read_text())
        result["north_audit"] = str(args.north_audit)
        if north.get("status") == "candidate_requires_visual_confirmation" and north.get("vector_candidate"):
            vector = tuple(north["vector_candidate"])
            observed_bearing = bearing(vector)
            result["observed_source_bearing_deg"] = round(observed_bearing, 3)
            if value in VIEWPOINTS:
                delta = circular_delta(result["expected_bearing_deg"], observed_bearing)
                result.update({
                    "bearing_delta_deg": round(delta, 3),
                    "status": "consistent" if delta <= args.max_bearing_delta_deg else "viewpoint_north_mismatch",
                })
            else:
                result["status"] = "unsupported_option42_value"
        else:
            result["status"] = "dxf_north_unresolved"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({key: result.get(key) for key in (
        "option_switch_42", "north_arrow_corner", "north_arrow_boxed", "status", "bearing_delta_deg"
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
