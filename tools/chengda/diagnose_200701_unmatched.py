#!/usr/bin/env python3
"""Produce evidence-rich diagnostics for 200701 unmatched callouts."""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from openpyxl import load_workbook


PROJECT = DATA_DIR
UNIT = PROJECT / "SHA文件转换" / "200701"
AUDIT = UNIT / "水印及编号标注审计"
PCF_DIR = UNIT / "200701 0 PCF"
ISSUES = AUDIT / "未标注编号人工补标清单.xlsx"
WELD_TABLE = UNIT / "焊口表-200701 0-壁厚材质处理-带特性_ISO对照结果_焊口号数字排序-人工核对0921.xlsx"
for candidate in (PROJECT, CODE_PARSER_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from chengda_management_table import read_management_inputs


def text(value: object) -> str:
    return "" if value is None else str(value).strip()


def point(value: object) -> tuple[float, float, float] | None:
    parts = [part.strip() for part in text(value).replace("，", ",").split(",")]
    if len(parts) < 3:
        return None
    try:
        return tuple(float(value) for value in parts[:3])  # type: ignore[return-value]
    except ValueError:
        return None


def blocks(path: Path) -> list[dict[str, object]]:
    lines = path.read_text(errors="replace").splitlines()
    starts = [index for index, line in enumerate(lines) if line and not line[0].isspace()]
    result: list[dict[str, object]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        raw = "\n".join(lines[start:end])
        coordinates = [
            tuple(float(value) for value in match)
            for match in re.findall(
                r"(?:END-POINT|CENTRE-POINT|BRANCH\d*-POINT|CO-ORDS)\s+"
                r"([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)",
                raw,
            )
        ]
        uci = re.search(r"\bUCI\s+(\{[^}]+\})", raw)
        result.append(
            {
                "kind": lines[start].strip(),
                "uci": uci.group(1).upper() if uci else "",
                "coordinates": coordinates,
                "line": start + 1,
            }
        )
    return result


def segment_projection(
    coordinate: tuple[float, float, float],
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float]:
    delta = tuple(second[index] - first[index] for index in range(3))
    squared = sum(value * value for value in delta)
    if squared <= 1e-12:
        return math.dist(coordinate, first), 0.0
    offset = tuple(coordinate[index] - first[index] for index in range(3))
    raw_fraction = sum(offset[index] * delta[index] for index in range(3)) / squared
    fraction = max(0.0, min(1.0, raw_fraction))
    projected = tuple(first[index] + fraction * delta[index] for index in range(3))
    return math.dist(coordinate, projected), fraction


def workbook_rows(path: Path, sheet_name: str | None = None) -> list[dict[str, object]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[sheet_name] if sheet_name else workbook.active
    rows = sheet.iter_rows(values_only=True)
    headers = [text(value) for value in next(rows)]
    result = [
        {headers[index]: value for index, value in enumerate(row) if index < len(headers)}
        for row in rows
    ]
    workbook.close()
    return result


def main() -> None:
    issues = workbook_rows(ISSUES, "未标注编号")
    source_rows = workbook_rows(WELD_TABLE)
    source_by_excel_row = {index: row for index, row in enumerate(source_rows, start=2)}
    management_records = read_management_inputs(
        support_table=UNIT / "支架表.csv",
        valve_table=UNIT / "阀门表.csv",
        flange_table=UNIT / "法兰表.csv",
        project_profile="100200",
    )
    management_by_key = {
        (record.sheet_id.casefold(), record.page, record.kind, record.number): record
        for record in management_records
    }
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    details: list[dict[str, object]] = []
    for issue in issues:
        sheet_id = text(issue.get("单页图号"))
        reason = text(issue.get("未标注原因"))
        kind = text(issue.get("编号类型"))
        grouped[(kind, reason)].append(issue)
        pcf_path = PCF_DIR / f"{sheet_id}.pcf"
        pcf_blocks = blocks(pcf_path) if pcf_path.is_file() else []
        source_row_numbers = [
            int(value)
            for value in re.findall(r"\d+", text(issue.get("来源表行号")))
        ]
        sources = [source_by_excel_row[row] for row in source_row_numbers if row in source_by_excel_row]
        coordinate = point(issue.get("参考坐标"))
        nearest_pipes: list[dict[str, object]] = []
        nearest_ports: list[dict[str, object]] = []
        if coordinate is not None:
            for block in pcf_blocks:
                coords = block["coordinates"]
                if block["kind"] in {"PIPE", "PIPE-FIXED"} and len(coords) >= 2:
                    distance, fraction = segment_projection(coordinate, coords[0], coords[1])
                    nearest_pipes.append(
                        {
                            "distance": round(distance, 4),
                            "fraction": round(fraction, 6),
                            "kind": block["kind"],
                            "uci": block["uci"],
                            "line": block["line"],
                            "length": round(math.dist(coords[0], coords[1]), 4),
                        }
                    )
                for candidate in coords:
                    nearest_ports.append(
                        {
                            "distance": round(math.dist(coordinate, candidate), 4),
                            "kind": block["kind"],
                            "uci": block["uci"],
                            "line": block["line"],
                            "coordinate": candidate,
                        }
                    )
        management_record = management_by_key.get(
            (sheet_id.casefold(), int(issue.get("页码") or 1), {
                "支架": "support", "阀门": "valve", "法兰": "flange", "管段": "spool"
            }.get(kind, kind), text(issue.get("编号")))
        )
        requested_ucis = set(management_record.ucis if management_record else ()) or {
            match.upper()
            for source in sources
            for match in re.findall(r"\{[^}]+\}", " ".join(text(value) for value in source.values()))
        }
        pcf_uci_blocks = [
            block for block in pcf_blocks if block["uci"] in requested_ucis
        ]
        page_dir = AUDIT / "pages" / f"{sheet_id}_{int(issue.get('页码') or 1)}"
        trace_path = page_dir / "trace.json"
        weld_map_path = page_dir / "weld-map.json"
        trace = json.loads(trace_path.read_text(encoding="utf-8")) if trace_path.is_file() else {}
        weld_map = json.loads(weld_map_path.read_text(encoding="utf-8")) if weld_map_path.is_file() else {}
        visible_ucis = {str(row.get("uci", "")).upper() for row in trace.get("uci_regions", [])}
        topology: list[dict[str, object]] = []
        for component in pcf_uci_blocks:
            for port in component["coordinates"]:
                adjacent = [
                    {
                        "kind": block["kind"],
                        "uci": block["uci"],
                        "visible": block["uci"] in visible_ucis,
                        "line": block["line"],
                    }
                    for block in pcf_blocks
                    if block is not component
                    and block["uci"]
                    and any(math.dist(port, candidate) <= 5.0 for candidate in block["coordinates"])
                ]
                mapped_welds = [
                    {
                        "number": row.get("weld_number"),
                        "sha": [row.get("sha_x"), row.get("sha_y")],
                        "status": row.get("confidence_status"),
                        "method": row.get("mapping_method"),
                    }
                    for row in weld_map.get("welds", [])
                    if isinstance(row.get("pcf_endpoint"), list)
                    and len(row["pcf_endpoint"]) >= 3
                    and math.dist(port, tuple(float(value) for value in row["pcf_endpoint"][:3])) <= 5.0
                ]
                topology.append({"port": port, "adjacent": adjacent, "mapped_welds": mapped_welds})
        details.append(
            {
                "sheet_id": sheet_id,
                "page": issue.get("页码"),
                "kind": kind,
                "number": text(issue.get("编号")),
                "reason": reason,
                "source_rows": source_row_numbers,
                "source": sources,
                "coordinate": coordinate,
                "nearest_pipes": sorted(nearest_pipes, key=lambda row: row["distance"])[:5],
                "nearest_ports": sorted(nearest_ports, key=lambda row: row["distance"])[:8],
                "requested_ucis": sorted(requested_ucis),
                "pcf_uci_blocks": pcf_uci_blocks,
                "visible_requested_ucis": sorted(requested_ucis & visible_ucis),
                "management_topology": topology,
            }
        )
    output = {
        "summary": [
            {"kind": key[0], "reason": key[1], "count": len(rows)}
            for key, rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
        ],
        "details": details,
    }
    destination = AUDIT / "未标注原因诊断.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(destination)
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
