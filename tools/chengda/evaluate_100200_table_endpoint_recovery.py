#!/usr/bin/env python3
"""Evaluate strict topology recovery for table welds absent from PCF WELDs."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

PROJECT_DIR = DATA_DIR
PARSER_DIR = CODE_PARSER_DIR
sys.path.insert(0, str(PARSER_DIR))

from number_pcf_welds import (  # noqa: E402
    WeldBlock,
    parse_weld_blocks,
    sha_implicit_pipe_end_weld_points,
    sha_weld_points,
)


def synthetic_weld(row: dict[str, object]) -> WeldBlock:
    coordinate = tuple(float(value) for value in row["coordinate"])
    excel_row = int(row["excel_row"])
    return WeldBlock(
        start=-1,
        end=-1,
        lines=[],
        uci=f"{{CONSTRUCTION-TABLE-{excel_row}}}",
        component_identifier="",
        master_component_identifier="",
        skey="",
        category="",
        endpoint=(coordinate[0], coordinate[1], coordinate[2], 0.0),
        description="",
        repeat_weld_identifier=str(row["weld_number"]),
        augmentation_source="",
        augmentation_mode="",
        owner_uci="",
        owner_fraction=None,
        neighbor_uci_a="",
        neighbor_uci_b="",
    )


def main() -> int:
    unit = PROJECT_DIR / "SHA文件转换" / "100200"
    diagnosis = json.loads(
        (unit / "100200焊口未匹配诊断.json").read_text(encoding="utf-8")
    )
    by_sheet: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in diagnosis["no_pcf_weld_rows"]:
        by_sheet[str(row["sheet_id"])].append(row)

    results: list[dict[str, object]] = []
    for index, (sheet_id, rows) in enumerate(sorted(by_sheet.items()), start=1):
        pcf = unit / "PCF" / f"{sheet_id}.pcf"
        sha = unit / "SHA" / f"{sheet_id}.sha"
        source_lines, pcf_welds = parse_weld_blocks(pcf)
        native = sha_weld_points(sha, 80.0)
        # Existing zero-number WELDs may be valid orientation evidence.  Their
        # diagnostic IDs only bypass the production number filter.
        topology_welds = [
            replace(weld, repeat_weld_identifier="DIAG")
            if weld.repeat_weld_identifier.strip() in {"", "0"}
            else weld
            for weld in pcf_welds
        ]
        synthetic = [synthetic_weld(row) for row in rows]
        inferred = sha_implicit_pipe_end_weld_points(
            sha, source_lines, topology_welds + synthetic, native
        )
        for row, weld in zip(rows, synthetic):
            point = inferred.get(weld.uci)
            results.append(
                {
                    **row,
                    "strict_endpoint_recoverable": point is not None,
                    "recovered_point": point,
                }
            )
        if index % 250 == 0:
            print(f"checked {index}/{len(by_sheet)} sheets", flush=True)

    summary = Counter(
        (
            str(row["classification"]),
            "recoverable" if row["strict_endpoint_recoverable"] else "unresolved",
        )
        for row in results
    )
    payload = {
        "summary": {
            f"{classification}:{status}": count
            for (classification, status), count in sorted(summary.items())
        },
        "rows": results,
    }
    output = unit / "100200表格非PCF焊点端点拓扑恢复评估.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
