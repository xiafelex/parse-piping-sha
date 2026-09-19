#!/usr/bin/env python3
"""Render Chengda SHA pages with PCF-derived typed weld callouts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

PROJECT_DIR = Path(__file__).resolve().parent
PARSER_DIR = PROJECT_DIR / "parser"
if str(PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(PARSER_DIR))

from analyze_iso_split import read_sha_streams
from chengda_weld_mapping import add_construction_virtual_targets, build_weld_map
from chengda_management_callouts import (
    forward_owned_uci_regions,
    overlay_management_callouts,
    plan_management_layout,
    resolve_management_targets,
    write_management_audit,
)
from chengda_management_table import (
    ManagementInput,
    group_management_by_sheet,
    read_management_inputs,
)
from chengda_construction_welds import (
    ConstructionWeldInput,
    apply_construction_welds,
    construction_records_for_sheet,
    group_construction_by_sheet,
    read_construction_weld_inputs,
)
from chengda_weld_mapping import atomic_write_json
from inject_sha_weld_callouts import sheet_streams
from convert_chengda_sha_to_pdf import publish_delivery_pdf, sha256_file, validate_pdf


@dataclass(frozen=True)
class WeldPageJob:
    sheet_id: str
    sha_path: Path
    pcf_path: Path
    page: int
    sheet_stream: str


@dataclass(frozen=True)
class PageResult:
    sheet_id: str
    page: int
    pdf_path: Path
    weld_map_path: Path
    verification_path: Path
    trace_path: Path
    callout_count: int
    management_audit_path: Path
    management_callout_count: int


def discover_weld_jobs(sha_dir: Path, pcf_dir: Path) -> list[WeldPageJob]:
    sha_dir = Path(sha_dir).resolve()
    pcf_dir = Path(pcf_dir).resolve()
    pcfs = {path.stem.casefold(): path for path in pcf_dir.rglob("*.pcf") if path.is_file()}
    jobs: list[WeldPageJob] = []
    missing: list[str] = []
    for sha_path in sorted(sha_dir.rglob("*.sha"), key=lambda path: str(path).casefold()):
        pcf_path = pcfs.get(sha_path.stem.casefold())
        if pcf_path is None:
            missing.append(sha_path.stem)
            continue
        targets = sheet_streams(read_sha_streams(sha_path), None)
        for target in targets:
            jobs.append(
                WeldPageJob(
                    sheet_id=sha_path.stem,
                    sha_path=sha_path,
                    pcf_path=pcf_path,
                    page=int(target.page),
                    sheet_stream=str(target.stream_name),
                )
            )
    if missing:
        raise ValueError(f"{len(missing)}个SHA缺少同名单页PCF：{missing[:10]}")
    return jobs


def run_checked(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part.strip())
        raise RuntimeError(f"命令失败（{completed.returncode}）：{' '.join(command)}\n{detail}")
    return completed.stdout


def rendered_weld_labels(svg_path: Path) -> list[str]:
    import xml.etree.ElementTree as ET

    root = ET.parse(svg_path).getroot()
    return [
        "".join(element.itertext()).strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
        and str(element.attrib.get("data-mapping", "")).startswith("sha-injected-weld")
    ]


def _verify_qa(qa: dict[str, object], expected_count: int) -> None:
    required_equal = {
        "callout_count": expected_count,
        "typed_marker_structurally_verified": expected_count,
        "on_original_geometry": expected_count,
        "frame_overflow_count": 0,
        "drawing_region_violation_count": 0,
        "leader_source_crossing_count": 0,
        "leader_leader_crossing_callout_count": 0,
        "callout_conflict_count": 0,
        "virtual_pipe_centerline_violation_count": 0,
        "long_pipe_distribution_violation_count": 0,
    }
    failures = {
        key: (qa.get(key), expected)
        for key, expected in required_equal.items()
        if int(qa.get(key, -1)) != expected
    }
    if failures:
        raise ValueError(f"焊口标注验证未通过：{failures}")


def convert_weld_job(
    job: WeldPageJob,
    output_dir: Path,
    audit_dir: Path,
    force: bool,
    management_records: list[ManagementInput] | None = None,
    construction_records: list[ConstructionWeldInput] | None = None,
    project_profile: str = "000200fw",
) -> PageResult:
    output_dir = Path(output_dir).resolve()
    audit_dir = Path(audit_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    final_pdf = output_dir / f"{job.sheet_id}_{job.page}.pdf"
    if final_pdf.exists() and not force:
        raise FileExistsError(f"输出已存在；如需覆盖请增加--force：{final_pdf}")
    sha_hash = sha256_file(job.sha_path)
    pcf_hash = sha256_file(job.pcf_path)

    with tempfile.TemporaryDirectory(prefix="._chengda_weld_", dir=output_dir) as raw:
        work = Path(raw)
        weld_map = work / "weld-map.json"
        mapping = build_weld_map(
            job.pcf_path,
            job.sha_path,
            weld_map,
            include_unnumbered_topology=(project_profile == "100200"),
        )
        weld_payload = json.loads(weld_map.read_text(encoding="utf-8"))
        if construction_records is not None:
            if project_profile == "100200":
                weld_payload = add_construction_virtual_targets(
                    weld_payload,
                    construction_records,
                    job.pcf_path,
                    job.sha_path,
                )
            weld_payload, _ = apply_construction_welds(weld_payload, construction_records)
            atomic_write_json(weld_map, weld_payload)
        expected_rows = [
            row
            for row in weld_payload["welds"]
            if row.get("weld_number")
            and int(row.get("page", 999999)) == job.page
            and row.get("graphic_ref") is not None
        ]
        render_sha = job.sha_path
        verification = work / "verification.json"
        if expected_rows:
            annotated_sha = work / f"{job.sheet_id}-annotated.sha"
            injection_command = [
                sys.executable,
                str(PARSER_DIR / "inject_sha_weld_callouts.py"),
                str(job.sha_path),
                "--output", str(annotated_sha),
                "--pages", str(job.page),
                "--weld-map", str(weld_map),
                "--distance-threshold", "80.0",
                "--diamond-radius", "95",
            ]
            run_checked(injection_command, PARSER_DIR)
            run_checked(
                [
                    sys.executable,
                    str(PARSER_DIR / "verify_sha_weld_callouts.py"),
                    str(job.sha_path),
                    str(annotated_sha),
                    str(weld_map),
                    "--output", str(verification),
                ],
                PARSER_DIR,
            )
            qa = json.loads(verification.read_text(encoding="utf-8"))
            _verify_qa(qa, len(expected_rows))
            render_sha = annotated_sha
        else:
            verification.write_text(
                json.dumps({"callout_count": 0, "callouts": []}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        svg = work / f"{job.sheet_id}_{job.page}.svg"
        trace = work / f"{job.sheet_id}_{job.page}.trace.json"
        prepared_pdf = work / f"{job.sheet_id}_{job.page}.pdf"
        run_checked(
            [
                sys.executable,
                str(PARSER_DIR / "sha_to_svg_prototype.py"),
                str(render_sha),
                "--page", str(job.page),
                "--sheet-stream", job.sheet_stream,
                "--output", str(svg),
                "--manifest", str(trace),
                "--project-profile", project_profile,
            ],
            PARSER_DIR,
        )
        management_payload = json.loads(trace.read_text(encoding="utf-8"))
        management_payload["uci_regions"] = forward_owned_uci_regions(
            render_sha,
            job.sheet_stream,
        )
        management_targets, management_unresolved = resolve_management_targets(
            list(management_records or []),
            management_payload,
            pcf_path=job.pcf_path,
            weld_map=weld_payload,
            project_profile=project_profile,
        )
        management_placements = plan_management_layout(
            render_sha,
            page=job.page,
            sheet_stream=job.sheet_stream,
            targets=management_targets,
        )
        overlay_management_callouts(svg, management_targets, management_placements)
        temporary_management_audit = work / "management-callouts.json"
        write_management_audit(
            temporary_management_audit,
            management_targets,
            management_unresolved,
        )
        expected_labels = sorted(str(row["weld_number"]) for row in expected_rows)
        actual_labels = sorted(rendered_weld_labels(svg))
        if actual_labels != expected_labels:
            raise ValueError(f"SVG焊口标签不完整：预期{expected_labels}，实际{actual_labels}")
        run_checked(["node", str(PARSER_DIR / "render_svg_pdf.mjs"), str(svg), str(prepared_pdf)], PARSER_DIR)
        validate_pdf(prepared_pdf)
        if sha256_file(job.sha_path) != sha_hash or sha256_file(job.pcf_path) != pcf_hash:
            raise RuntimeError("源SHA或反写PCF在转换过程中发生变化")
        publish_delivery_pdf(prepared_pdf, final_pdf)
        validate_pdf(final_pdf)

        page_audit = audit_dir / "pages" / f"{job.sheet_id}_{job.page}"
        page_audit.mkdir(parents=True, exist_ok=True)
        final_map = page_audit / "weld-map.json"
        final_verification = page_audit / "verification.json"
        final_trace = page_audit / "trace.json"
        final_management_audit = page_audit / "management-callouts.json"
        shutil.copy2(weld_map, final_map)
        shutil.copy2(verification, final_verification)
        shutil.copy2(trace, final_trace)
        shutil.copy2(temporary_management_audit, final_management_audit)

    return PageResult(
        sheet_id=job.sheet_id,
        page=job.page,
        pdf_path=final_pdf,
        weld_map_path=final_map,
        verification_path=final_verification,
        trace_path=final_trace,
        callout_count=len(expected_rows),
        management_audit_path=final_management_audit,
        management_callout_count=len(management_targets),
    )


def parse_args() -> argparse.Namespace:
    base = PROJECT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("000200fw", "100200"),
        default="000200fw",
        help="000200FW=纯EP3D焊口对照；100200=施工单位焊口对照",
    )
    parser.add_argument("--sha-dir", type=Path, default=base / "input" / "sha")
    parser.add_argument("--pcf-dir", type=Path, default=base / "input" / "pcf")
    parser.add_argument("--output-dir", type=Path, default=base / "output" / "pdf")
    parser.add_argument("--audit-dir", type=Path, default=base / "output" / "audit")
    parser.add_argument("--support-table", type=Path, default=base / "input" / "support.csv")
    parser.add_argument("--valve-table", type=Path, default=base / "input" / "valve.csv")
    parser.add_argument("--flange-table", type=Path, default=base / "input" / "flange.csv")
    parser.add_argument("--weld-table", type=Path, help="施工焊口号来源Excel；省略则沿用PCF编号")
    parser.add_argument("--weld-number-column", default="施工焊口号")
    parser.add_argument("--no-management", action="store_true", help="不添加支架、阀门和法兰管理编号")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--include", help="仅处理逗号分隔的单页图号")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def _batch_row(result: PageResult) -> dict[str, object]:
    mapping = json.loads(result.weld_map_path.read_text(encoding="utf-8"))
    summary = mapping.get("summary", {})
    return {
        "sheet_id": result.sheet_id,
        "page": result.page,
        "status": "success",
        "pdf": str(result.pdf_path),
        "callout_count": result.callout_count,
        "management_callout_count": result.management_callout_count,
        "numbered_weld_count": int(summary.get("numbered_weld_count", 0)),
        "unmapped_numbered_count": int(summary.get("unmapped_numbered_count", 0)),
        "ambiguous_count": int(summary.get("ambiguous_count", 0)),
    }


def _numbered_issue_label(kind: object, number: object) -> str:
    kind_name = {
        "weld": "焊口",
        "support": "支架",
        "valve": "阀门",
        "flange": "法兰",
    }.get(str(kind).strip().casefold(), str(kind).strip() or "编号")
    value = str(number).strip() or "（编号缺失）"
    return f"{kind_name} {value}"


def write_manual_review_log(
    audit_dir: Path,
    rows: list[dict[str, object]],
) -> Path:
    """Write the small human-facing exception log for a completed SHA batch."""

    audit_dir = Path(audit_dir).resolve()
    unmatched_by_sheet: dict[str, list[str]] = {}
    unique_remaining_by_sheet: dict[str, list[str]] = {}
    failed: list[str] = []

    for row in sorted(
        rows, key=lambda item: (str(item.get("sheet_id", "")).casefold(), int(item.get("page", 0)))
    ):
        sheet_id = str(row.get("sheet_id", "")).strip()
        page = int(row.get("page", 0))
        if row.get("status") != "success":
            error = " ".join(str(row.get("error", "")).split())
            failed.append(f"{sheet_id}（第{page}页）：{error or '转换失败'}")
            continue

        page_dir = audit_dir / "pages" / f"{sheet_id}_{page}"
        weld_path = page_dir / "weld-map.json"
        if weld_path.is_file():
            weld_data = json.loads(weld_path.read_text(encoding="utf-8"))
            for weld in weld_data.get("welds", []):
                status = str(weld.get("confidence_status", "")).casefold()
                weld_number = str(weld.get("weld_number", "")).strip()
                if weld_number and status in {"unmapped", "ambiguous"}:
                    label = _numbered_issue_label("weld", weld_number)
                    unmatched_by_sheet.setdefault(sheet_id, []).append(label)
            # Construction-table rows which cannot be tied to a unique PCF
            # or SHA target are intentionally not inserted into ``welds``.
            # They must nevertheless remain visible in the concise manual
            # review log instead of disappearing from the delivery audit.
            for weld in weld_data.get("construction_unresolved", []):
                label = _numbered_issue_label("weld", weld.get("weld_number"))
                unmatched_by_sheet.setdefault(sheet_id, []).append(label)

        management_path = page_dir / "management-callouts.json"
        if management_path.is_file():
            management = json.loads(management_path.read_text(encoding="utf-8"))
            for item in management.get("unresolved", []):
                label = _numbered_issue_label(item.get("kind"), item.get("number"))
                unmatched_by_sheet.setdefault(sheet_id, []).append(label)
            for item in management.get("callouts", []):
                if item.get("mapping_method") == "unique-pcf-flange-interface-weld":
                    label = _numbered_issue_label(item.get("kind"), item.get("number"))
                    unique_remaining_by_sheet.setdefault(sheet_id, []).append(label)

    def issue_lines(grouped: dict[str, list[str]]) -> list[str]:
        lines: list[str] = []
        for sheet_id in sorted(grouped, key=str.casefold):
            labels = list(dict.fromkeys(grouped[sheet_id]))
            lines.append(f"- {sheet_id}：{'；'.join(labels)}")
        return lines or ["- 无"]

    success_count = sum(row.get("status") == "success" for row in rows)
    lines = [
        "成达印尼项目 SHA 转换人工审核日志",
        f"生成时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
        f"转换页数：{len(rows)}；成功：{success_count}；失败：{len(rows) - success_count}",
        "",
        "一、未匹配或匹配存在歧义的编号",
        *issue_lines(unmatched_by_sheet),
        "",
        "二、通过两侧唯一剩余规则匹配的编号（请人工确认）",
        *issue_lines(unique_remaining_by_sheet),
        "",
        "三、转换失败的图纸",
        *(failed or ["- 无"]),
        "",
    ]
    log_path = audit_dir / "SHA转换人工审核日志.txt"
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def write_unmatched_number_xlsx(
    audit_dir: Path,
    rows: list[dict[str, object]],
) -> Path:
    """Write one concise sheet containing only numbers absent from PDFs."""

    audit_dir = Path(audit_dir).resolve()
    issues: list[dict[str, object]] = []
    kind_names = {
        "weld": "焊口",
        "support": "支架",
        "valve": "阀门",
        "flange": "法兰",
    }

    def coordinate_text(value: object) -> str:
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        return str(value or "").strip()

    for batch_row in sorted(
        rows,
        key=lambda item: (
            str(item.get("sheet_id", "")).casefold(),
            int(item.get("page", 0)),
        ),
    ):
        sheet_id = str(batch_row.get("sheet_id", "")).strip()
        page = int(batch_row.get("page", 0))
        if batch_row.get("status") != "success":
            issues.append(
                {
                    "sheet_id": sheet_id,
                    "page": page,
                    "kind": "转换失败",
                    "number": "",
                    "reason": " ".join(str(batch_row.get("error", "")).split()),
                    "coordinate": "",
                    "source_row": "",
                }
            )
            continue

        page_dir = audit_dir / "pages" / f"{sheet_id}_{page}"
        weld_path = page_dir / "weld-map.json"
        if weld_path.is_file():
            weld_data = json.loads(weld_path.read_text(encoding="utf-8"))
            for weld in weld_data.get("welds", []):
                number = str(weld.get("weld_number", "")).strip()
                status = str(weld.get("confidence_status", "")).casefold()
                if not number or status not in {"unmapped", "ambiguous"}:
                    continue
                issues.append(
                    {
                        "sheet_id": sheet_id,
                        "page": page,
                        "kind": "焊口",
                        "number": number,
                        "reason": (
                            "PCF焊口未找到唯一SHA位置"
                            if status == "unmapped"
                            else "存在多个SHA候选位置"
                        ),
                        "coordinate": coordinate_text(weld.get("pcf_endpoint")),
                        "source_row": weld.get("construction_excel_row", ""),
                    }
                )
            for weld in weld_data.get("construction_unresolved", []):
                issues.append(
                    {
                        "sheet_id": sheet_id,
                        "page": page,
                        "kind": "焊口",
                        "number": str(weld.get("weld_number", "")).strip(),
                        "reason": str(weld.get("reason", "")).strip(),
                        "coordinate": coordinate_text(weld.get("coordinate")),
                        "source_row": weld.get("excel_row", ""),
                    }
                )

        management_path = page_dir / "management-callouts.json"
        if management_path.is_file():
            management = json.loads(management_path.read_text(encoding="utf-8"))
            for item in management.get("unresolved", []):
                source_rows = item.get("source_rows", [])
                issues.append(
                    {
                        "sheet_id": sheet_id,
                        "page": page,
                        "kind": kind_names.get(
                            str(item.get("kind", "")).casefold(),
                            str(item.get("kind", "")).strip() or "编号",
                        ),
                        "number": str(item.get("number", "")).strip(),
                        "reason": str(item.get("reason", "")).strip(),
                        "coordinate": coordinate_text(item.get("coordinate")),
                        "source_row": ", ".join(str(value) for value in source_rows),
                    }
                )

    deduplicated: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()
    for issue in issues:
        key = (
            issue["sheet_id"],
            issue["page"],
            issue["kind"],
            issue["number"],
            issue["reason"],
            issue["coordinate"],
        )
        if key not in seen:
            seen.add(key)
            deduplicated.append(issue)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "未标注编号"
    headers = ["单页图号", "页码", "编号类型", "编号", "未标注原因", "参考坐标", "来源表行号"]
    sheet.append(headers)
    for issue in deduplicated:
        sheet.append(
            [
                issue["sheet_id"],
                issue["page"],
                issue["kind"],
                issue["number"],
                issue["reason"],
                issue["coordinate"],
                issue["source_row"],
            ]
        )
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:G{max(sheet.max_row, 1)}"
    widths = {"A": 38, "B": 8, "C": 12, "D": 16, "E": 44, "F": 34, "G": 16}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    output = audit_dir / "未标注编号人工补标清单.xlsx"
    workbook.save(output)
    return output


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        raise ValueError("--workers必须大于等于1")
    if args.profile == "000200fw" and args.weld_table is not None:
        raise ValueError("000200FW固化配置只读取PCF中的EP3D焊口号，不允许--weld-table")
    if args.profile == "100200" and args.weld_table is None:
        raise ValueError("100200施工单位焊口对照配置必须提供--weld-table")
    jobs = discover_weld_jobs(args.sha_dir, args.pcf_dir)
    if args.include:
        included = {part.strip().casefold() for part in args.include.split(",") if part.strip()}
        jobs = [job for job in jobs if job.sheet_id.casefold() in included]
    if args.plan_only:
        print(json.dumps({"job_count": len(jobs), "jobs": [job.sheet_id for job in jobs]}, ensure_ascii=False, indent=2))
        return 0

    management_by_sheet: dict[tuple[str, int], list[ManagementInput]] = {}
    if not args.no_management:
        management_by_sheet = group_management_by_sheet(
            read_management_inputs(
                support_table=args.support_table,
                valve_table=args.valve_table,
                flange_table=args.flange_table,
                project_profile=args.profile,
            )
        )

    construction_by_sheet: dict[str, list[ConstructionWeldInput]] | None = None
    skipped_construction = []
    if args.weld_table is not None:
        construction_records, skipped_construction = read_construction_weld_inputs(
            args.weld_table, args.weld_number_column
        )
        construction_by_sheet = group_construction_by_sheet(construction_records)

    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                convert_weld_job,
                job,
                args.output_dir,
                args.audit_dir,
                args.force,
                management_by_sheet.get((job.sheet_id.casefold(), job.page), []),
                (
                    construction_records_for_sheet(construction_by_sheet, job.sheet_id)
                    if construction_by_sheet is not None
                    else None
                ),
                args.profile,
            ): job
            for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
                row = _batch_row(result)
                print(
                    f"已生成: {result.pdf_path}，焊口标注{result.callout_count}个，"
                    f"管理编号{result.management_callout_count}个"
                )
            except Exception as exc:
                row = {
                    "sheet_id": job.sheet_id,
                    "page": job.page,
                    "status": "failed",
                    "error": str(exc),
                }
                print(f"失败: {job.sheet_id}_{job.page}: {exc}", file=sys.stderr)
            rows.append(row)
    rows.sort(key=lambda row: (str(row["sheet_id"]).casefold(), int(row["page"])))
    summary = {
        "job_count": len(jobs),
        "success_pdf_count": sum(row["status"] == "success" for row in rows),
        "failed_pdf_count": sum(row["status"] == "failed" for row in rows),
        "callout_count": sum(int(row.get("callout_count", 0)) for row in rows),
        "management_callout_count": sum(int(row.get("management_callout_count", 0)) for row in rows),
        "numbered_weld_count": sum(int(row.get("numbered_weld_count", 0)) for row in rows),
        "unmapped_numbered_count": sum(int(row.get("unmapped_numbered_count", 0)) for row in rows),
        "ambiguous_count": sum(int(row.get("ambiguous_count", 0)) for row in rows),
        "ignored_empty_original_weld_count": len(skipped_construction),
        "rows": rows,
    }
    audit_dir = Path(args.audit_dir).resolve()
    audit_dir.mkdir(parents=True, exist_ok=True)
    summary_path = audit_dir / "PDF标注汇总.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.weld_table is not None:
        skipped_path = audit_dir / "焊口表忽略记录.json"
        skipped_path.write_text(
            json.dumps(
                [
                    {
                        "excel_row": row.excel_row,
                        "sheet_id": row.sheet_id,
                        "weld_number": row.weld_number,
                        "reason": row.reason,
                    }
                    for row in skipped_construction
                ],
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    review_log_path = write_manual_review_log(audit_dir, rows)
    unmatched_xlsx_path = write_unmatched_number_xlsx(audit_dir, rows)
    print(f"人工审核日志: {review_log_path}")
    print(f"人工补标清单: {unmatched_xlsx_path}")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, ensure_ascii=False, indent=2))
    return 1 if summary["failed_pdf_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
