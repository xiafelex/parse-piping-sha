#!/usr/bin/env python3
"""Evaluate strict PCF-topology recovery for 100200 WELDs missing SHA dots."""

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

from number_pcf_welds import (
    parse_weld_blocks,
    sha_implicit_pipe_end_weld_points,
    sha_weld_points,
)


def main() -> int:
    unit = PROJECT_DIR / "SHA文件转换" / "100200"
    diagnosis_path = unit / "100200焊口未匹配诊断.json"
    data = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    by_sheet: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in data["pcf_weld_without_sha_rows"]:
        by_sheet[str(row["sheet_id"])].append(row)

    results: list[dict[str, object]] = []
    for index, (sheet_id, rows) in enumerate(sorted(by_sheet.items()), start=1):
        pcf = unit / "PCF" / f"{sheet_id}.pcf"
        sha = unit / "SHA" / f"{sheet_id}.sha"
        source_lines, welds = parse_weld_blocks(pcf)
        native = sha_weld_points(sha, 80.0)
        numbered_for_diagnosis = [
            replace(weld, repeat_weld_identifier=(weld.repeat_weld_identifier or "DIAG"))
            if weld.repeat_weld_identifier.strip() not in {"", "0"}
            else replace(weld, repeat_weld_identifier="DIAG")
            for weld in welds
        ]
        inferred = sha_implicit_pipe_end_weld_points(
            sha, source_lines, numbered_for_diagnosis, native
        )
        for row in rows:
            uci = str(row["uci"])
            point = inferred.get(uci)
            results.append(
                {
                    **row,
                    "strict_topology_recoverable": point is not None,
                    "recovered_point": point,
                }
            )
        if index % 50 == 0:
            print(f"checked {index}/{len(by_sheet)} sheets", flush=True)

    counts = Counter(
        "recoverable" if row["strict_topology_recoverable"] else "unresolved"
        for row in results
    )
    output = unit / "100200缺失SHA焊点拓扑恢复评估.json"
    payload = {"summary": dict(counts), "rows": results}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
