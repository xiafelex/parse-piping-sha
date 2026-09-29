#!/usr/bin/env python3
"""Re-evaluate unresolved 201600 flange tags without rendering PDFs."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font


ROOT = DATA_DIR
PARSER = CODE_PARSER_DIR
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PARSER) not in sys.path:
    sys.path.insert(0, str(PARSER))

from chengda_management_callouts import (  # noqa: E402
    forward_owned_uci_regions,
    resolve_management_targets,
)
from chengda_management_table import (  # noqa: E402
    group_management_by_sheet,
    read_management_inputs,
)
from convert_chengda_weld_sha_to_pdf import discover_weld_jobs  # noqa: E402


UNIT = ROOT / "SHA文件转换" / "201600"
AUDIT = UNIT / "水印及编号标注审计"
BASELINE = AUDIT / "未标注编号人工补标清单.xlsx"
OUTPUT = UNIT / "201600_法兰标注规则预检.xlsx"


def baseline_rows() -> list[dict[str, object]]:
    workbook = load_workbook(BASELINE, read_only=True, data_only=True)
    sheet = workbook["未标注编号"]
    headers = [str(value or "") for value in next(sheet.iter_rows(values_only=True))]
    rows = []
    for values in sheet.iter_rows(min_row=2, values_only=True):
        row = dict(zip(headers, values))
        if row.get("编号类型") == "法兰":
            rows.append(row)
    workbook.close()
    return rows


def inspect_page(job, records, wanted_numbers: set[str]) -> list[dict[str, object]]:
    page_dir = AUDIT / "pages" / f"{job.sheet_id}_{job.page}"
    trace_path = page_dir / "trace.json"
    weld_path = page_dir / "weld-map.json"
    if not trace_path.exists():
        return [
            {
                "单页图号": job.sheet_id,
                "页码": job.page,
                "编号": number,
                "结果": "仍未恢复",
                "最新原因或方法": "缺少既有SHA解析trace",
                "置信度": "",
            }
            for number in sorted(wanted_numbers)
        ]

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace["legacy_uci_regions"] = list(trace.get("uci_regions", []))
    trace["uci_regions"] = forward_owned_uci_regions(job.sha_path, job.sheet_stream)
    weld_map = (
        json.loads(weld_path.read_text(encoding="utf-8"))
        if weld_path.exists()
        else None
    )
    mapped, unresolved = resolve_management_targets(
        records,
        trace,
        pcf_path=job.pcf_path,
        weld_map=weld_map,
        project_profile="100200",
    )
    targets = {
        target.number: target
        for target in mapped
        if target.kind == "flange" and target.number in wanted_numbers
    }
    missing = {
        str(row.get("number", "")): row
        for row in unresolved
        if row.get("kind") == "flange"
    }
    flange_records = {record.number: record for record in records if record.kind == "flange"}
    visible_ucis = {
        str(region.get("uci", "")).upper()
        for region in trace.get("uci_regions", [])
    }
    pcf_text = job.pcf_path.read_text(encoding="utf-8", errors="replace")
    starts = list(re.finditer(r"(?m)^(\S.*?)\s*$", pcf_text))
    pcf_types: dict[str, set[str]] = {}
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(pcf_text)
        block = pcf_text[start.start():end]
        uci = re.search(r"(?m)^\s+UCI\s+(\{[^}]+\})", block)
        if uci:
            pcf_types.setdefault(uci.group(1).upper(), set()).add(start.group(1).strip())
    uci_frequency = Counter(
        uci
        for record in flange_records.values()
        for uci in record.ucis
    )
    result = []
    for number in sorted(wanted_numbers):
        record = flange_records.get(number)
        ucis = tuple(record.ucis) if record is not None else ()
        diagnostics = {
            "UCI": ";".join(ucis),
            "可见UCI数": sum(uci in visible_ucis for uci in ucis),
            "UCI总数": len(ucis),
            "PCF元件类型": ";".join(
                f"{uci}:{'/'.join(sorted(pcf_types.get(uci, {'未知'})))}"
                for uci in ucis
            ),
            "同页法兰共享次数": max((uci_frequency[uci] for uci in ucis), default=0),
        }
        target = targets.get(number)
        if target is not None:
            result.append(
                {
                    "单页图号": job.sheet_id,
                    "页码": job.page,
                    "编号": number,
                    "结果": "已恢复（高置信）" if target.confidence == "high" else "已恢复（需审核）",
                    "最新原因或方法": target.mapping_method,
                    "置信度": target.confidence,
                    **diagnostics,
                }
            )
        else:
            result.append(
                {
                    "单页图号": job.sheet_id,
                    "页码": job.page,
                    "编号": number,
                    "结果": "仍未恢复",
                    "最新原因或方法": str(missing.get(number, {}).get("reason", "未返回匹配结果")),
                    "置信度": "",
                    **diagnostics,
                }
            )
    return result


def write_report(rows: list[dict[str, object]], original: dict[tuple[str, int, str], str]) -> None:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "统计"
    summary.append(["范围", "结果", "数量"])
    for scope, selected in (
        ("原400道paired UCI问题", [row for row in rows if original[(str(row['单页图号']), int(row['页码']), str(row['编号']))] == "paired UCI not visible on this SHA page"]),
        ("全部未标注法兰", rows),
    ):
        counts = Counter(str(row["结果"]) for row in selected)
        for result in ("已恢复（高置信）", "已恢复（需审核）", "仍未恢复"):
            summary.append([scope, result, counts[result]])
        summary.append([scope, "合计", len(selected)])

    details = workbook.create_sheet("逐项结果")
    headers = [
        "单页图号", "页码", "编号", "原未标注原因", "结果", "最新原因或方法", "置信度",
        "UCI", "可见UCI数", "UCI总数", "PCF元件类型", "同页法兰共享次数",
    ]
    details.append(headers)
    for row in sorted(rows, key=lambda item: (str(item["单页图号"]).casefold(), int(item["页码"]), str(item["编号"]))):
        key = (str(row["单页图号"]), int(row["页码"]), str(row["编号"]))
        details.append([
            row["单页图号"], row["页码"], row["编号"], original[key],
            row["结果"], row["最新原因或方法"], row["置信度"], row.get("UCI", ""),
            row.get("可见UCI数", 0), row.get("UCI总数", 0), row.get("PCF元件类型", ""),
            row.get("同页法兰共享次数", 0),
        ])

    remaining = workbook.create_sheet("仍需分析")
    remaining.append(headers)
    for row in rows:
        if row["结果"] != "仍未恢复":
            continue
        key = (str(row["单页图号"]), int(row["页码"]), str(row["编号"]))
        remaining.append([
            row["单页图号"], row["页码"], row["编号"], original[key],
            row["结果"], row["最新原因或方法"], row["置信度"], row.get("UCI", ""),
            row.get("可见UCI数", 0), row.get("UCI总数", 0), row.get("PCF元件类型", ""),
            row.get("同页法兰共享次数", 0),
        ])

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for column in sheet.columns:
            width = min(60, max(10, max(len(str(cell.value or "")) for cell in column) + 2))
            sheet.column_dimensions[column[0].column_letter].width = width
    workbook.save(OUTPUT)


def main() -> int:
    baseline = baseline_rows()
    wanted: dict[tuple[str, int], set[str]] = {}
    original: dict[tuple[str, int, str], str] = {}
    for row in baseline:
        sheet_id = str(row["单页图号"])
        page = int(row["页码"])
        number = str(row["编号"])
        wanted.setdefault((sheet_id.casefold(), page), set()).add(number)
        original[(sheet_id, page, number)] = str(row["未标注原因"])

    records = group_management_by_sheet(
        read_management_inputs(
            support_table=UNIT / "支架表.csv",
            valve_table=UNIT / "阀门表.csv",
            flange_table=UNIT / "法兰表.csv",
            project_profile="100200",
        )
    )
    missing_pcf_sheet_ids: list[str] = []
    jobs = discover_weld_jobs(
        UNIT / "201600 SHA",
        UNIT / "201600 0A PCF",
        missing_pcf_sheet_ids=missing_pcf_sheet_ids,
    )
    selected = [job for job in jobs if (job.sheet_id.casefold(), job.page) in wanted]
    results: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                inspect_page,
                job,
                records.get((job.sheet_id.casefold(), job.page), []),
                wanted[(job.sheet_id.casefold(), job.page)],
            ): job
            for job in selected
        }
        for future in as_completed(futures):
            results.extend(future.result())

    found = {(str(row["单页图号"]).casefold(), int(row["页码"]), str(row["编号"])) for row in results}
    for (sheet, page), numbers in wanted.items():
        for number in numbers:
            if (sheet, page, number) in found:
                continue
            original_key = next(key for key in original if key[0].casefold() == sheet and key[1:] == (page, number))
            results.append({
                "单页图号": original_key[0], "页码": page, "编号": number,
                "结果": "仍未恢复", "最新原因或方法": "未发现对应SHA/PCF转换任务", "置信度": "",
            })
    write_report(results, original)
    print(json.dumps({"output": str(OUTPUT), "counts": Counter(str(row["结果"]) for row in results)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
