#!/usr/bin/env python3
"""Diagnose 100200 construction welds missing PCF or SHA counterparts."""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

PROJECT_DIR = DATA_DIR
PARSER_DIR = CODE_PARSER_DIR
sys.path.insert(0, str(PARSER_DIR))

from analyze_iso_split import forward_owned_dynamic_graphics, read_sha_streams
from chengda_construction_welds import read_construction_weld_inputs
from inject_sha_weld_callouts import point_segment_distance, sheet_streams
from number_pcf_welds import parse_pipe_components, parse_weld_blocks
from sha_to_svg_prototype import SHEET_UNIT, ellipse_anchors, line_segments, template_line_segments


def point_pipe_distance(point: tuple[float, float, float], pipe: object) -> tuple[float, float]:
    first = tuple(float(value) for value in pipe.endpoint_a[:3])
    second = tuple(float(value) for value in pipe.endpoint_b[:3])
    delta = tuple(second[index] - first[index] for index in range(3))
    denominator = sum(value * value for value in delta)
    if denominator <= 1e-12:
        return math.dist(point, first), 0.0
    fraction = sum((point[index] - first[index]) * delta[index] for index in range(3)) / denominator
    clamped = max(0.0, min(1.0, fraction))
    projected = tuple(first[index] + clamped * delta[index] for index in range(3))
    return math.dist(point, projected), clamped


def inspect_no_pcf_weld(
    pcf_path: Path,
    coordinate: tuple[float, float, float],
) -> dict[str, object]:
    lines, welds = parse_weld_blocks(pcf_path)
    pipes = parse_pipe_components(lines)
    weld_distances = sorted(
        [
            (
                math.dist(coordinate, tuple(float(value) for value in weld.endpoint[:3])),
                weld,
            )
            for weld in welds
            if weld.endpoint is not None
        ],
        key=lambda item: item[0],
    )
    pipe_distances = sorted(
        ((*point_pipe_distance(coordinate, pipe), pipe) for pipe in pipes),
        key=lambda item: (item[0], item[1]),
    )
    nearest_weld_distance = weld_distances[0][0] if weld_distances else None
    nearest_pipe_distance = pipe_distances[0][0] if pipe_distances else None
    nearest_fraction = pipe_distances[0][1] if pipe_distances else None
    nearest_pipe = pipe_distances[0][2] if pipe_distances else None
    tied_pipes = (
        [item for item in pipe_distances if item[0] <= max(0.10, float(nearest_pipe_distance) + 1e-6)]
        if nearest_pipe_distance is not None
        else []
    )
    if nearest_weld_distance is not None and nearest_weld_distance <= 5.0:
        classification = "near-pcf-weld-coordinate-offset"
    elif (
        nearest_pipe_distance is not None
        and nearest_pipe_distance <= 0.10
        and nearest_fraction is not None
        and 0.001 < nearest_fraction < 0.999
        and len(tied_pipes) == 1
    ):
        classification = "unique-interior-pipe-split"
    elif nearest_pipe_distance is not None and nearest_pipe_distance <= 0.10:
        classification = "on-pipe-axis-but-end-or-ambiguous"
    elif nearest_pipe_distance is not None and nearest_pipe_distance <= 5.0:
        classification = "near-pipe-axis-coordinate-offset"
    else:
        classification = "no-close-pcf-topology"
    return {
        "classification": classification,
        "nearest_weld_distance": nearest_weld_distance,
        "nearest_pipe_distance": nearest_pipe_distance,
        "nearest_pipe_fraction": nearest_fraction,
        "nearest_pipe_uci": getattr(nearest_pipe, "uci", ""),
        "nearest_pipe_component_identifier": getattr(nearest_pipe, "component_identifier", ""),
        "near_pipe_candidate_count": len(tied_pipes),
    }


def inspect_missing_sha(sha_path: Path, uci: str) -> dict[str, object]:
    streams = read_sha_streams(sha_path)
    dynamic = forward_owned_dynamic_graphics(
        streams.get("Unclustered Dynamic Attributes", b"")
    )
    records = dynamic.get(uci, [])
    sheets = sheet_streams(streams, None)
    record_details: list[dict[str, object]] = []
    for record in records:
        graphic_ref = int(record["graphic_ref"])
        occurrences = []
        for sheet in sheets:
            anchors = ellipse_anchors(sheet.data)
            exact_ref = struct.pack("<I", graphic_ref) in sheet.data
            anchor = anchors.get(graphic_ref)
            nearest_geometry = None
            if anchor is not None:
                segments = line_segments(sheet.data) + template_line_segments(sheet.data)
                if segments:
                    nearest_geometry = min(
                        point_segment_distance(
                            float(anchor[0]), float(anchor[1]),
                            x1 * SHEET_UNIT, y1 * SHEET_UNIT,
                            x2 * SHEET_UNIT, y2 * SHEET_UNIT,
                        )
                        for x1, y1, x2, y2, _, _ in segments
                    )
            if exact_ref or anchor is not None:
                occurrences.append(
                    {
                        "page": sheet.page,
                        "sheet_stream": sheet.stream_name,
                        "exact_ref": exact_ref,
                        "ellipse_anchor": list(anchor) if anchor is not None else None,
                        "distance_to_geometry": nearest_geometry,
                    }
                )
        record_details.append({"graphic_ref": graphic_ref, "occurrences": occurrences})
    if not records:
        classification = "weld-uci-absent-from-sha-dynamic-attributes"
    elif not any(item["occurrences"] for item in record_details):
        classification = "weld-uci-present-but-no-page-owned-graphic"
    elif not any(
        occurrence["ellipse_anchor"] is not None
        for item in record_details
        for occurrence in item["occurrences"]
    ):
        classification = "page-owned-weld-uci-without-ellipse"
    elif not any(
        occurrence["ellipse_anchor"] is not None
        and occurrence["distance_to_geometry"] is not None
        and float(occurrence["distance_to_geometry"]) <= 80.0
        for item in record_details
        for occurrence in item["occurrences"]
    ):
        classification = "ellipse-too-far-from-decoded-geometry"
    else:
        classification = "unexpected-native-point-filtering"
    return {
        "classification": classification,
        "dynamic_record_count": len(records),
        "records": record_details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    unit = PROJECT_DIR / "SHA文件转换" / "100200"
    parser.add_argument("--unit-dir", type=Path, default=unit)
    parser.add_argument("--output", type=Path, default=unit / "100200焊口未匹配诊断.json")
    args = parser.parse_args()

    audit_pages = args.unit_dir / "水印及编号标注审计" / "pages"
    pcf_dir = args.unit_dir / "PCF"
    sha_dir = args.unit_dir / "SHA"
    excel = args.unit_dir / "焊口表-壁厚材质处理-带特性-汇总.xlsx"
    records, _ = read_construction_weld_inputs(excel)
    by_excel_row = {record.excel_row: record for record in records}

    no_pcf_rows: list[dict[str, object]] = []
    missing_sha_rows: list[dict[str, object]] = []
    pcf_cache: dict[str, Path] = {path.stem.casefold(): path for path in pcf_dir.glob("*.pcf")}
    sha_cache: dict[str, Path] = {path.stem.casefold(): path for path in sha_dir.glob("*.sha")}
    sha_diagnostics: dict[tuple[str, str], dict[str, object]] = {}

    for map_path in sorted(audit_pages.glob("*/*weld-map.json")):
        data = json.loads(map_path.read_text(encoding="utf-8"))
        sheet_id = map_path.parent.name.rsplit("_", 1)[0]
        pcf_path = pcf_cache[sheet_id.casefold()]
        sha_path = sha_cache[sheet_id.casefold()]
        for item in data.get("construction_unresolved", []):
            record = by_excel_row.get(int(item["excel_row"]))
            coordinate = tuple(float(value) for value in item["coordinate"])
            diagnosis = inspect_no_pcf_weld(pcf_path, coordinate)
            no_pcf_rows.append(
                {
                    "sheet_id": sheet_id,
                    "weld_number": item.get("weld_number"),
                    "excel_row": item.get("excel_row"),
                    "coordinate": item.get("coordinate"),
                    "original_weld_number": getattr(record, "original_weld_number", ""),
                    "material_uci_a": getattr(record, "material_uci_a", ""),
                    "material_uci_b": getattr(record, "material_uci_b", ""),
                    **diagnosis,
                }
            )
        for item in data.get("welds", []):
            if not item.get("weld_number") or item.get("confidence_status") == "mapped":
                continue
            uci = str(item.get("source_uci") or item.get("uci") or "")
            cache_key = (sheet_id.casefold(), uci)
            if cache_key not in sha_diagnostics:
                sha_diagnostics[cache_key] = inspect_missing_sha(sha_path, uci)
            missing_sha_rows.append(
                {
                    "sheet_id": sheet_id,
                    "weld_number": item.get("weld_number"),
                    "uci": uci,
                    "pcf_endpoint": item.get("pcf_endpoint"),
                    "master_component_identifier": item.get("master_component_identifier"),
                    **sha_diagnostics[cache_key],
                }
            )

    no_pcf_counts = Counter(str(row["classification"]) for row in no_pcf_rows)
    missing_sha_counts = Counter(str(row["classification"]) for row in missing_sha_rows)
    payload = {
        "summary": {
            "no_pcf_weld_count": len(no_pcf_rows),
            "no_pcf_weld_classifications": dict(no_pcf_counts),
            "pcf_weld_without_sha_count": len(missing_sha_rows),
            "pcf_weld_without_sha_classifications": dict(missing_sha_counts),
        },
        "no_pcf_weld_rows": no_pcf_rows,
        "pcf_weld_without_sha_rows": missing_sha_rows,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
