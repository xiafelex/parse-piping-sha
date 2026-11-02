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
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

PROGRAM_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DATA_DIR
PARSER_DIR = CODE_PARSER_DIR
if str(PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(PARSER_DIR))

from analyze_iso_split import forward_owned_dynamic_graphics, read_sha_streams
from chengda_weld_mapping import add_construction_virtual_targets, build_weld_map
from chengda_management_callouts import (
    forward_owned_uci_regions,
    overlay_management_callouts,
    overlay_unified_weld_callouts,
    plan_unified_page_layout,
    resolve_management_targets,
    resolve_spool_targets,
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
    read_iso_review_weld_inputs,
    remap_weld_sheet_aliases,
)
from chengda_spool_table import SpoolInput, group_spools_by_sheet, read_spool_inputs
from chengda_weld_mapping import atomic_write_json
from chengda_template_owned_page import space_template_owned_pipe_splits
from chengda_sheet_ids import sheet_id_key
from chengda_audit_reasons import chinese_audit_reason
from inject_sha_weld_callouts import sheet_streams
from convert_chengda_sha_to_pdf import (
    publish_delivery_pdf,
    sha256_file,
    signature_dir_for_sha,
    validate_pdf,
)


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
    spool_callout_count: int


@dataclass(frozen=True)
class AuditPageResult:
    sheet_id: str
    page: int
    weld_map_path: Path
    trace_path: Path
    management_audit_path: Path
    callout_count: int
    management_callout_count: int
    spool_callout_count: int


def discover_weld_jobs(
    sha_dir: Path,
    pcf_dir: Path,
    included_sheet_ids: set[str] | None = None,
    missing_pcf_sheet_ids: list[str] | None = None,
) -> list[WeldPageJob]:
    sha_dir = Path(sha_dir).resolve()
    pcf_dir = Path(pcf_dir).resolve()
    pcfs: dict[str, set[Path]] = {}
    for path in pcf_dir.rglob("*.pcf"):
        if not path.is_file():
            continue
        pcfs.setdefault(sheet_id_key(path.stem), set()).add(path)
        # Exporters may remove forbidden filename separators rather than
        # encode them. The internal pipeline reference retains the identity.
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) == 2 and fields[0] == "PIPELINE-REFERENCE":
                pcfs.setdefault(sheet_id_key(fields[1]), set()).add(path)
    included_keys = (
        {sheet_id_key(value) for value in included_sheet_ids}
        if included_sheet_ids is not None else None
    )
    jobs: list[WeldPageJob] = []
    missing: list[str] = []
    for sha_path in sorted(sha_dir.rglob("*.sha"), key=lambda path: str(path).casefold()):
        if (
            included_keys is not None
            and sheet_id_key(sha_path.stem) not in included_keys
        ):
            continue
        candidates = pcfs.get(sheet_id_key(sha_path.stem), set())
        if not candidates:
            missing.append(sha_path.stem)
            continue
        if len(candidates) != 1:
            # Filename escapes can leave byte-identical PCF copies with the
            # same internal pipeline reference. Only those are interchangeable;
            # genuinely different revisions must still fail closed.
            if len({sha256_file(path) for path in candidates}) != 1:
                raise ValueError(f"SHA对应多个PCF，拒绝自动选择：{sha_path.name}：{sorted(map(str, candidates))}")
        pcf_path = min(candidates, key=lambda path: (
            sheet_id_key(path.stem) != sheet_id_key(sha_path.stem),
            str(path).casefold(),
        ))
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
        if missing_pcf_sheet_ids is None:
            raise ValueError(f"{len(missing)}个SHA缺少同名单页PCF：{missing[:10]}")
        missing_pcf_sheet_ids.extend(missing)
    return jobs


def run_checked(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part.strip())
        raise RuntimeError(f"命令失败（{completed.returncode}）：{' '.join(command)}\n{detail}")
    return completed.stdout


def run_weld_injection_with_safe_retry(
    command: list[str],
    skip_known_strict_failure: bool = False,
) -> str:
    """Run proven layout, compact verified layout, then audited best effort.

    The compact retry broadens candidate search while keeping the physical
    text envelopes shared with the verifier. It does not permit bent
    leaders, frame overflow, marker overlap, or crossings.
    """

    compact_command = [*command, "--relaxed-text-clearance"]
    if skip_known_strict_failure:
        run_checked([*compact_command, "--best-effort-layout"], PARSER_DIR)
        return "best-effort"
    if not skip_known_strict_failure:
        try:
            run_checked(command, PARSER_DIR)
            return "normal"
        except RuntimeError as exc:
            detail = str(exc)
            if not (
                "No locally valid straight-leader candidate" in detail
                or "No zero-crossing global layout" in detail
            ):
                raise
    try:
        run_checked(compact_command, PARSER_DIR)
        return "compact"
    except RuntimeError as compact_exc:
        compact_detail = str(compact_exc)
        if not (
            "No locally valid straight-leader candidate" in compact_detail
            or "No zero-crossing global layout" in compact_detail
        ):
            raise
        run_checked([*compact_command, "--best-effort-layout"], PARSER_DIR)
        return "best-effort"


def rendered_weld_labels(svg_path: Path) -> list[str]:
    import xml.etree.ElementTree as ET

    root = ET.parse(svg_path).getroot()
    return [
        "".join(element.itertext()).strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
        and str(element.attrib.get("data-mapping", "")).startswith("sha-injected-weld")
    ]


def _verify_qa(
    qa: dict[str, object],
    expected_count: int,
    allow_best_effort: bool = False,
) -> None:
    required_equal = {
        "callout_count": expected_count,
        "on_original_geometry": expected_count,
        "frame_overflow_count": 0,
        "drawing_region_violation_count": 0,
        "virtual_pipe_centerline_violation_count": 0,
        "long_pipe_distribution_violation_count": 0,
    }
    if not allow_best_effort:
        required_equal.update(
            {
                "typed_marker_structurally_verified": expected_count,
                "leader_source_crossing_count": 0,
                "leader_leader_crossing_callout_count": 0,
                "callout_conflict_count": 0,
            }
        )
    failures = {
        key: (qa.get(key), expected)
        for key, expected in required_equal.items()
        if int(qa.get(key, -1)) != expected
    }
    if failures:
        raise ValueError(f"焊口标注验证未通过：{failures}")
    if allow_best_effort:
        structural_fields = (
            "leader_starts_at_source_dot",
            "leader_is_single_straight_line",
            "marker_closed",
            "leader_ends_at_marker_edge",
            "marker_shape_matches_weld_type",
            "marker_inside_outer_frame",
            "marker_inside_drawing_region",
            "marker_has_drawing_clearance",
            "label_inside_marker",
            "on_expected_pipe_centerline",
        )
        invalid = [
            item.get("label")
            for item in qa.get("callouts", [])
            if not all(bool(item.get(field)) for field in structural_fields)
        ]
        if invalid:
            raise ValueError(f"尽力布局的结构验证未通过：{invalid}")


def prepare_weld_payload(
    job: WeldPageJob,
    weld_map: Path,
    construction_records: list[ConstructionWeldInput] | None,
    project_profile: str,
    manual_weld_records: list[object] | None,
) -> dict[str, object]:
    """Build the shared PCF/SHA weld mapping used by render and audit-only runs."""

    build_weld_map(
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
        if manual_weld_records:
            unresolved = list(weld_payload.get("construction_unresolved", []))
            unresolved.extend(
                {
                    "excel_row": row.excel_row,
                    "sheet_id": row.sheet_id,
                    "weld_number": row.weld_number,
                    "coordinate": [],
                    "reason": row.reason,
                }
                for row in manual_weld_records
            )
            weld_payload["construction_unresolved"] = unresolved
            summary = dict(weld_payload.get("summary", {}))
            summary["numbered_weld_count"] = int(
                summary.get("numbered_weld_count", 0)
            ) + len(manual_weld_records)
            summary["unmapped_numbered_count"] = int(
                summary.get("unmapped_numbered_count", 0)
            ) + len(manual_weld_records)
            weld_payload["summary"] = summary
        weld_payload = space_template_owned_pipe_splits(weld_payload, job.sha_path)
        atomic_write_json(weld_map, weld_payload)
    return weld_payload


def convert_weld_job(
    job: WeldPageJob,
    output_dir: Path,
    audit_dir: Path,
    force: bool,
    management_records: list[ManagementInput] | None = None,
    construction_records: list[ConstructionWeldInput] | None = None,
    project_profile: str = "000200fw",
    signature_dir: Path | None = None,
    manual_weld_records: list[object] | None = None,
    spool_records: list[SpoolInput] | None = None,
    repair_layout: bool = False,
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
        weld_payload = prepare_weld_payload(
            job,
            weld_map,
            construction_records,
            project_profile,
            manual_weld_records,
        )
        expected_rows = [
            row
            for row in weld_payload["welds"]
            if row.get("weld_number")
            and int(row.get("page", 999999)) == job.page
            and row.get("graphic_ref") is not None
        ]
        render_sha = job.sha_path
        verification = work / "verification.json"
        source_streams = read_sha_streams(job.sha_path)
        source_sheet_data = source_streams.get(job.sheet_stream, b"")
        template_data = source_streams.get("Sheet221", b"")
        dynamic = forward_owned_dynamic_graphics(
            source_streams.get("Unclustered Dynamic Attributes", b"")
        )
        dynamic_refs = {
            int(record["graphic_ref"])
            for records in dynamic.values()
            for record in records
        }
        sheet_owner_count = sum(
            ref.to_bytes(4, "little") in source_sheet_data for ref in dynamic_refs
        )
        template_owner_count = sum(
            ref.to_bytes(4, "little") in template_data for ref in dynamic_refs
        )
        # Binary insertion is retained for the proven conventional layout.
        # Template-owned one-page files receive the same callouts later in the
        # SVG joint-layout stage, because their Sheet6 is not the owning Shape2D
        # stream even when it happens to be larger than 1 KiB.
        use_binary_injection = (
            len(source_sheet_data) > 1024
            and sheet_owner_count >= template_owner_count
        )
        if expected_rows and use_binary_injection:
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
            layout_mode = run_weld_injection_with_safe_retry(
                injection_command,
                skip_known_strict_failure=repair_layout,
            )
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
            qa["layout_mode"] = layout_mode
            verification.write_text(
                json.dumps(qa, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _verify_qa(
                qa,
                len(expected_rows),
                allow_best_effort=layout_mode == "best-effort",
            )
            render_sha = annotated_sha
        else:
            verification.write_text(
                json.dumps(
                    {
                        "callout_count": 0,
                        "callouts": [],
                        "layout_mode": (
                            "svg-direct-compact-sheet"
                            if expected_rows and not use_binary_injection
                            else "strict"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )

        svg = work / f"{job.sheet_id}_{job.page}.svg"
        trace = work / f"{job.sheet_id}_{job.page}.trace.json"
        prepared_pdf = work / f"{job.sheet_id}_{job.page}.pdf"
        render_command = [
                sys.executable,
                str(PARSER_DIR / "sha_to_svg_prototype.py"),
                str(render_sha),
                "--page", str(job.page),
                "--sheet-stream", job.sheet_stream,
                "--output", str(svg),
                "--manifest", str(trace),
                "--project-profile", project_profile,
                "--pcf", str(job.pcf_path),
            ]
        resolved_signatures = signature_dir_for_sha(job.sha_path, signature_dir)
        if resolved_signatures is not None:
            render_command.extend(["--signature-dir", str(resolved_signatures)])
        run_checked(render_command, PARSER_DIR)
        management_payload = json.loads(trace.read_text(encoding="utf-8"))
        # Preserve the renderer's same-record association as geometry-only
        # evidence.  Component identity below uses the corrected forward-owned
        # regions, while a missing material-excluded neighbour can still be
        # represented by this legacy envelope.
        management_payload["legacy_uci_regions"] = list(
            management_payload.get("uci_regions", [])
        )
        management_payload["uci_regions"] = forward_owned_uci_regions(
            # UCI ownership is source metadata. Synthetic weld text added to
            # the temporary SHA must never participate in component identity.
            job.sha_path,
            job.sheet_stream,
        )
        management_targets, management_unresolved = resolve_management_targets(
            list(management_records or []),
            management_payload,
            pcf_path=job.pcf_path,
            weld_map=weld_payload,
            project_profile=project_profile,
        )
        spool_targets, spool_unresolved = resolve_spool_targets(
            list(spool_records or []),
            weld_payload,
            job.sha_path,
            job.sheet_stream,
            job.page,
            pcf_path=job.pcf_path,
        )
        all_management_targets = management_targets + spool_targets
        all_management_unresolved = management_unresolved + spool_unresolved
        management_layout_audit: dict[str, object] = {}
        weld_placements, management_placements = plan_unified_page_layout(
            job.sha_path,
            page=job.page,
            sheet_stream=job.sheet_stream,
            weld_rows=expected_rows,
            management_targets=all_management_targets,
            layout_audit=management_layout_audit,
        )
        verification_payload = json.loads(verification.read_text(encoding="utf-8"))
        verification_payload["preliminary_weld_layout_mode"] = verification_payload.get(
            "layout_mode", "strict"
        )
        verification_payload["layout_mode"] = management_layout_audit.get(
            "layout_mode", "strict"
        )
        verification_payload["layout_scope"] = management_layout_audit.get(
            "layout_scope", "weld-management-spool-joint"
        )
        verification_payload["unified_layout_counts"] = {
            "weld": management_layout_audit.get("weld_count", 0),
            "management": management_layout_audit.get("management_count", 0),
            "spool": management_layout_audit.get("spool_count", 0),
        }
        verification.write_text(
            json.dumps(verification_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        overlay_unified_weld_callouts(svg, expected_rows, weld_placements)
        overlay_management_callouts(svg, all_management_targets, management_placements)
        temporary_management_audit = work / "management-callouts.json"
        write_management_audit(
            temporary_management_audit,
            all_management_targets,
            all_management_unresolved,
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
        spool_callout_count=len(spool_targets),
    )


def audit_weld_job(
    job: WeldPageJob,
    audit_dir: Path,
    management_records: list[ManagementInput] | None = None,
    construction_records: list[ConstructionWeldInput] | None = None,
    project_profile: str = "000200fw",
    manual_weld_records: list[object] | None = None,
    spool_records: list[SpoolInput] | None = None,
) -> AuditPageResult:
    """Recompute number matching without injecting callouts or producing a PDF."""

    audit_dir = Path(audit_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="._chengda_audit_") as raw:
        work = Path(raw)
        weld_map = work / "weld-map.json"
        weld_payload = prepare_weld_payload(
            job,
            weld_map,
            construction_records,
            project_profile,
            manual_weld_records,
        )
        expected_rows = [
            row
            for row in weld_payload["welds"]
            if row.get("weld_number")
            and int(row.get("page", 999999)) == job.page
            and row.get("graphic_ref") is not None
        ]

        # The manifest is semantic input to management-number matching.  The
        # temporary SVG is intentionally discarded and never converted to PDF.
        svg = work / "manifest-source.svg"
        trace = work / "trace.json"
        run_checked(
            [
                sys.executable,
                str(PARSER_DIR / "sha_to_svg_prototype.py"),
                str(job.sha_path),
                "--page", str(job.page),
                "--sheet-stream", job.sheet_stream,
                "--output", str(svg),
                "--manifest", str(trace),
                "--project-profile", project_profile,
                "--pcf", str(job.pcf_path),
            ],
            PARSER_DIR,
        )
        management_payload = json.loads(trace.read_text(encoding="utf-8"))
        management_payload["legacy_uci_regions"] = list(
            management_payload.get("uci_regions", [])
        )
        management_payload["uci_regions"] = forward_owned_uci_regions(
            job.sha_path,
            job.sheet_stream,
        )
        management_targets, management_unresolved = resolve_management_targets(
            list(management_records or []),
            management_payload,
            pcf_path=job.pcf_path,
            weld_map=weld_payload,
            project_profile=project_profile,
        )
        spool_targets, spool_unresolved = resolve_spool_targets(
            list(spool_records or []),
            weld_payload,
            job.sha_path,
            job.sheet_stream,
            job.page,
            pcf_path=job.pcf_path,
        )
        management_audit = work / "management-callouts.json"
        write_management_audit(
            management_audit,
            management_targets + spool_targets,
            management_unresolved + spool_unresolved,
        )

        page_audit = audit_dir / "pages" / f"{job.sheet_id}_{job.page}"
        page_audit.mkdir(parents=True, exist_ok=True)
        final_map = page_audit / "weld-map.json"
        final_trace = page_audit / "trace.json"
        final_management_audit = page_audit / "management-callouts.json"
        final_verification = page_audit / "verification.json"
        shutil.copy2(weld_map, final_map)
        shutil.copy2(trace, final_trace)
        shutil.copy2(management_audit, final_management_audit)
        final_verification.write_text(
            json.dumps(
                {
                    "audit_only": True,
                    "layout_mode": "not-evaluated",
                    "callout_count": len(expected_rows),
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

    return AuditPageResult(
        sheet_id=job.sheet_id,
        page=job.page,
        weld_map_path=final_map,
        trace_path=final_trace,
        management_audit_path=final_management_audit,
        callout_count=len(expected_rows),
        management_callout_count=len(management_targets),
        spool_callout_count=len(spool_targets),
    )


def parse_args() -> argparse.Namespace:
    base = PROJECT_DIR / "SHA文件转换"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("000200fw", "100200"),
        default="000200fw",
        help="000200FW=纯EP3D焊口对照；100200=施工单位焊口对照",
    )
    parser.add_argument("--sha-dir", type=Path, default=base / "000200FW")
    parser.add_argument("--pcf-dir", type=Path, default=base / "000200FW 0-PCF-焊口反写测试")
    parser.add_argument("--output-dir", type=Path, default=base / "000200FW_PDF-焊口标注测试")
    parser.add_argument("--audit-dir", type=Path, default=base / "焊口反写与标注审计")
    parser.add_argument(
        "--signature-dir",
        type=Path,
        help="单元级签名资源目录；省略时自动查找SHA上级目录中的“签名资源”",
    )
    parser.add_argument(
        "--support-table", type=Path, action="append",
        help="支架表CSV；可重复传入多张分版表",
    )
    parser.add_argument(
        "--valve-table", type=Path, action="append",
        help="阀门表CSV；可重复传入多张分版表",
    )
    parser.add_argument(
        "--flange-table", type=Path, action="append",
        help="法兰表CSV；可重复传入多张分版表",
    )
    parser.add_argument("--weld-table", type=Path, help="施工焊口号来源Excel；省略则沿用PCF编号")
    parser.add_argument("--weld-number-column", default="施工焊口号")
    parser.add_argument('--weld-sheet-alias', action='append', default=[],
                        help='仅合并焊口表图号，格式原图号=目标图号；可重复指定，不改源表')
    parser.add_argument(
        "--spool-number-column",
        help="从焊口表读取预制管段号；每条管线每个管段仅标注一次",
    )
    parser.add_argument(
        "--weld-table-mode",
        choices=("construction", "iso-review"),
        default="construction",
        help="construction=100200施工号规则；iso-review=ISO审核列混合编号规则",
    )
    parser.add_argument(
        "--missing-position-is-ep3d-missing",
        action="store_true",
        help="焊点位置为空的审核行一律不落图，并列为EP3D缺失焊口待人工补标",
    )
    parser.add_argument("--iso-review-remark-policy", choices=("default", "402001", "301200", "100600", "100203", "201600", "404043", "304100-WS", "ws-iso-review", "ws-iso-review-302100"), default="default")
    parser.add_argument("--no-management", action="store_true", help="不添加支架、阀门和法兰管理编号")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--include", help="仅处理逗号分隔的单页图号")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume-log", type=Path,
                        help="同一输入资料续跑：复用指定运行日志中已完成且PDF与页面审计完整的页面")
    parser.add_argument(
        "--repair-layout",
        action="store_true",
        help="仅重跑已知布局失败页：跳过重复的长时间原始严格布局搜索",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="仅重新计算SHA/PCF/表格编号匹配并生成人工补标清单；不注入标注、不计算布局、不生成PDF",
    )
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
        "spool_callout_count": result.spool_callout_count,
        "numbered_weld_count": int(summary.get("numbered_weld_count", 0)),
        "unmapped_numbered_count": int(summary.get("unmapped_numbered_count", 0)),
        "ambiguous_count": int(summary.get("ambiguous_count", 0)),
    }


def completed_log_row(job: WeldPageJob, output_dir: Path, audit_dir: Path,
                      completed_pdfs: set[str], started_at: float) -> dict[str, object] | None:
    """Recover a completed page from this run; incomplete audits are rerun."""
    pdf = (Path(output_dir) / f"{job.sheet_id}_{job.page}.pdf").resolve()
    if str(pdf).casefold() not in completed_pdfs or not pdf.is_file():
        return None
    if pdf.stat().st_mtime < started_at:
        return None
    if any(p.stat().st_mtime > started_at for p in (job.sha_path, job.pcf_path)):
        return None
    page_dir = Path(audit_dir).resolve() / 'pages' / f'{job.sheet_id}_{job.page}'
    paths = [page_dir / name for name in ('weld-map.json', 'verification.json',
                                         'trace.json', 'management-callouts.json')]
    if not all(p.is_file() and p.stat().st_mtime >= started_at for p in paths):
        return None
    try:
        validate_pdf(pdf)
        verification = json.loads(paths[1].read_text(encoding='utf-8'))
        json.loads(paths[2].read_text(encoding='utf-8'))
        management = json.loads(paths[3].read_text(encoding='utf-8'))
        callouts = management['callouts']
        spools = sum(str(item.get('kind', '')).casefold() == 'spool' for item in callouts)
        result = PageResult(job.sheet_id, job.page, pdf, paths[0], paths[1], paths[2],
                            int(verification.get('unified_layout_counts', {}).get(
                                'weld', verification.get('callout_count', 0))),
                            paths[3], len(callouts) - spools, spools)
        return _batch_row(result)
    except (OSError, ValueError, KeyError, RuntimeError):
        return None


def _audit_batch_row(result: AuditPageResult) -> dict[str, object]:
    mapping = json.loads(result.weld_map_path.read_text(encoding="utf-8"))
    summary = mapping.get("summary", {})
    return {
        "sheet_id": result.sheet_id,
        "page": result.page,
        "status": "success",
        "pdf": "",
        "audit_only": True,
        "callout_count": result.callout_count,
        "management_callout_count": result.management_callout_count,
        "spool_callout_count": result.spool_callout_count,
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
        "spool": "管段",
    }.get(str(kind).strip().casefold(), str(kind).strip() or "编号")
    value = str(number).strip() or "（编号缺失）"
    return f"{kind_name} {value}"


REVIEW_MAPPING_METHODS = {
    "unique-pcf-flange-interface-weld",
    "unique-remaining-adjacent-flange-pair",
    "pcf-fitting-opposite-port-legacy-neighbour",
    "pcf-sha-fpfl-chain-order-reconciliation",
    "pcf-sha-fpfl-anchored-subpath-reconciliation",
    "pcf-sha-fpfl-terminal-seed-reconciliation",
    "pcf-visible-component-opposite-port",
    "pcf-hidden-component-visible-neighbour-opposite-port",
    "support-partial-uci-cluster",
    "support-coordinate-unique-weld-root",
}


def _weld_requires_review(item: dict[str, object]) -> bool:
    return bool(item.get("manual_review_required")) or (
        "construction-table-material-chain-terminal-pipe"
        in str(item.get("mapping_method", ""))
    )


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
                if weld_number and status == "mapped" and _weld_requires_review(weld):
                    label = _numbered_issue_label("weld", weld_number)
                    unique_remaining_by_sheet.setdefault(sheet_id, []).append(label)
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
                if item.get("mapping_method") in REVIEW_MAPPING_METHODS:
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
    missing_pcf_sheet_ids: list[str] | None = None,
    layout_evaluated: bool = True,
    extra_issues: list[dict[str, object]] | None = None,
) -> Path:
    """Write one concise sheet containing only numbers absent from PDFs."""

    audit_dir = Path(audit_dir).resolve()
    issues: list[dict[str, object]] = list(extra_issues or [])
    review_matches: list[dict[str, object]] = []
    best_effort_pages: list[dict[str, object]] = []
    kind_names = {
        "weld": "焊口",
        "support": "支架",
        "valve": "阀门",
        "flange": "法兰",
        "spool": "管段",
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
        verification_path = page_dir / "verification.json"
        if verification_path.is_file():
            verification = json.loads(verification_path.read_text(encoding="utf-8"))
            if str(verification.get("layout_mode", "")).casefold() == "best-effort":
                best_effort_pages.append(
                    {
                        "sheet_id": sheet_id,
                        "page": page,
                        "layout_mode": "best-effort",
                        "callout_count": int(verification.get("callout_count", 0) or 0),
                        "source_crossings": int(
                            verification.get("leader_source_crossing_count", 0) or 0
                        ),
                        "leader_crossings": int(
                            verification.get("leader_leader_crossing_callout_count", 0) or 0
                        ),
                        "conflicts": int(
                            verification.get("callout_conflict_count", 0) or 0
                        ),
                    }
                )
        weld_path = page_dir / "weld-map.json"
        if weld_path.is_file():
            weld_data = json.loads(weld_path.read_text(encoding="utf-8"))
            for weld in weld_data.get("welds", []):
                number = str(weld.get("weld_number", "")).strip()
                status = str(weld.get("confidence_status", "")).casefold()
                if not number or status not in {"unmapped", "ambiguous"}:
                    if number and status == "mapped" and _weld_requires_review(weld):
                        review_matches.append(
                            {
                                "sheet_id": sheet_id,
                                "page": page,
                                "kind": "焊口",
                                "number": number,
                                "method": str(weld.get("mapping_method", "")),
                                "ucis": str(weld.get("material_chain_shared_uci", "")),
                                "note": str(
                                    weld.get(
                                        "manual_review_reason",
                                        "材料及长度比例自动推断，请人工确认标注位置",
                                    )
                                ),
                            }
                        )
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
            for item in management.get("callouts", []):
                method = str(item.get("mapping_method", "")).strip()
                if method not in REVIEW_MAPPING_METHODS:
                    continue
                review_matches.append(
                    {
                        "sheet_id": sheet_id,
                        "page": page,
                        "kind": kind_names.get(
                            str(item.get("kind", "")).casefold(),
                            str(item.get("kind", "")).strip() or "编号",
                        ),
                        "number": str(item.get("number", "")).strip(),
                        "method": method,
                        "ucis": ";".join(str(value) for value in item.get("ucis", [])),
                        "note": "拓扑唯一自动匹配，请人工确认标注位置",
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
                chinese_audit_reason(str(issue["reason"])),
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

    review_sheet = workbook.create_sheet("自动匹配需审核")
    review_headers = ["单页图号", "页码", "编号类型", "编号", "自动匹配方法", "UCI", "审核说明"]
    review_sheet.append(review_headers)
    review_seen: set[tuple[object, ...]] = set()
    for item in review_matches:
        key = (
            item["sheet_id"], item["page"], item["kind"], item["number"], item["method"]
        )
        if key in review_seen:
            continue
        review_seen.add(key)
        review_sheet.append(
            [
                item["sheet_id"], item["page"], item["kind"], item["number"],
                item["method"], item["ucis"], item["note"],
            ]
        )
    for cell in review_sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    review_sheet.freeze_panes = "A2"
    review_sheet.auto_filter.ref = f"A1:G{max(review_sheet.max_row, 1)}"
    for column, width in widths.items():
        review_sheet.column_dimensions[column].width = width
    for row in review_sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)

    layout_sheet = workbook.create_sheet("宽松布局需审核")
    layout_headers = [
        "单页图号", "页码", "布局模式", "标注总数", "引线与图形交叉数",
        "引线互相交叉标注数", "标注冲突数", "审核说明",
    ]
    layout_sheet.append(layout_headers)
    if not layout_evaluated:
        layout_sheet.append(
            [
                "", "", "not-evaluated", "", "", "", "",
                "本次为仅匹配审计，未执行标注布局；本Sheet不用于判断宽松布局问题",
            ]
        )
    for item in sorted(
        best_effort_pages,
        key=lambda value: (str(value["sheet_id"]).casefold(), int(value["page"])),
    ):
        layout_sheet.append(
            [
                item["sheet_id"], item["page"], item["layout_mode"],
                item["callout_count"], item["source_crossings"],
                item["leader_crossings"], item["conflicts"],
                "严格及紧凑布局均无解，需人工检查是否手工落图",
            ]
        )
    for cell in layout_sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    layout_sheet.freeze_panes = "A2"
    layout_sheet.auto_filter.ref = f"A1:H{max(layout_sheet.max_row, 1)}"
    for column, width in {
        "A": 38, "B": 8, "C": 14, "D": 12, "E": 20, "F": 24,
        "G": 16, "H": 54,
    }.items():
        layout_sheet.column_dimensions[column].width = width
    for row in layout_sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)

    missing_pcf_sheet = workbook.create_sheet("SHA缺少PCF")
    missing_pcf_sheet.append(["单页图号", "处理状态", "说明"])
    for sheet_id in sorted(set(missing_pcf_sheet_ids or []), key=str.casefold):
        missing_pcf_sheet.append(
            [sheet_id, "未转换", "存在SHA，但未找到同名单页PCF；未执行编号标注转换"]
        )
    for cell in missing_pcf_sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    missing_pcf_sheet.freeze_panes = "A2"
    missing_pcf_sheet.auto_filter.ref = f"A1:C{max(missing_pcf_sheet.max_row, 1)}"
    for column, width in {"A": 38, "B": 12, "C": 58}.items():
        missing_pcf_sheet.column_dimensions[column].width = width
    for row in missing_pcf_sheet.iter_rows(min_row=2):
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
    if args.spool_number_column and args.weld_table is None:
        raise ValueError("管段号标注必须同时提供--weld-table")
    included = None
    if args.include:
        included = {part.strip().casefold() for part in args.include.split(",") if part.strip()}
    missing_pcf_sheet_ids: list[str] = []
    jobs = discover_weld_jobs(
        args.sha_dir,
        args.pcf_dir,
        included,
        missing_pcf_sheet_ids,
    )
    if args.plan_only:
        print(
            json.dumps(
                {
                    "job_count": len(jobs),
                    "jobs": [job.sheet_id for job in jobs],
                    "missing_pcf_count": len(missing_pcf_sheet_ids),
                    "missing_pcf_sheet_ids": missing_pcf_sheet_ids,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    management_by_sheet: dict[tuple[str, int], list[ManagementInput]] = {}
    if not args.no_management:
        default_table_dir = PROJECT_DIR / "SHA文件转换"
        management_by_sheet = group_management_by_sheet(
            read_management_inputs(
                support_table=(
                    args.support_table or [default_table_dir / "支架表.csv"]
                ),
                valve_table=(
                    args.valve_table or [default_table_dir / "阀门表.csv"]
                ),
                flange_table=(
                    args.flange_table or [default_table_dir / "法兰表.csv"]
                ),
                project_profile=args.profile,
            )
        )

    construction_by_sheet: dict[str, list[ConstructionWeldInput]] | None = None
    spool_by_sheet: dict[tuple[str, int], list[SpoolInput]] = {}
    manual_weld_by_sheet: dict[str, list[object]] = {}
    skipped_construction = []
    table_issues_without_jobs: list[dict[str, object]] = []
    if args.weld_table is not None:
        if args.weld_table_mode == "iso-review":
            construction_records, skipped_construction = read_iso_review_weld_inputs(
                args.weld_table,
                missing_position_is_ep3d_missing=args.missing_position_is_ep3d_missing,
                remark_policy=args.iso_review_remark_policy,
            )
        else:
            construction_records, skipped_construction = read_construction_weld_inputs(
                args.weld_table, args.weld_number_column
            )
        construction_records = remap_weld_sheet_aliases(construction_records, args.weld_sheet_alias)
        skipped_construction = remap_weld_sheet_aliases(skipped_construction, args.weld_sheet_alias)
        construction_by_sheet = group_construction_by_sheet(construction_records)
        # Full-unit audit must also retain table targets that have no page job.
        # They otherwise disappear because page workers cannot report them.
        if not args.include:
            job_sheet_keys = {sheet_id_key(job.sheet_id) for job in jobs}
            missing_pcf_keys = {sheet_id_key(value) for value in missing_pcf_sheet_ids}
            for record in [*construction_records, *[item for item in skipped_construction if item.manual_review_required]]:
                key = sheet_id_key(record.sheet_id)
                if key in job_sheet_keys:
                    continue
                reason = ("目标图号缺少PCF，无法生成编号标注" if key in missing_pcf_keys
                          else "审核目标图号无对应SHA文件，需人工核对图号及补标")
                source_reason = getattr(record, "reason", "")
                if source_reason:
                    reason = f"{source_reason}；{reason}"
                table_issues_without_jobs.append({
                    "sheet_id": record.sheet_id, "page": 1, "kind": "焊口",
                    "number": record.weld_number, "reason": reason,
                    "coordinate": ", ".join(str(value) for value in getattr(record, "coordinate", ())),
                    "source_row": record.excel_row,
                })
        selected_numbers = {
            (sheet_id_key(row.sheet_id), row.weld_number.casefold())
            for row in construction_records
        }
        for row in skipped_construction:
            if row.manual_review_required and (
                sheet_id_key(row.sheet_id), row.weld_number.casefold()
            ) not in selected_numbers:
                manual_weld_by_sheet.setdefault(sheet_id_key(row.sheet_id), []).append(row)
        if args.spool_number_column:
            spool_by_sheet = group_spools_by_sheet(
                read_spool_inputs(args.weld_table, args.spool_number_column)
            )

    rows: list[dict[str, object]] = []
    pending_jobs = jobs
    if args.resume_log is not None:
        if args.audit_only:
            raise ValueError('--resume-log仅适用于同一输入资料的PDF转换续跑')
        log_path = Path(args.resume_log).resolve()
        metadata = log_path.stat()
        started_at = getattr(metadata, 'st_birthtime', metadata.st_ctime)
        previous_log_lines = log_path.read_text(encoding='utf-8-sig').splitlines()
        for line in previous_log_lines:
            if line.startswith('续跑原始启动时间: '):
                started_at = min(started_at, float(line.split(': ', 1)[1]))
        inputs = [args.weld_table, *(args.support_table or []),
                  *(args.valve_table or []), *(args.flange_table or [])]
        if args.signature_dir and Path(args.signature_dir).is_dir():
            inputs.extend(p for p in Path(args.signature_dir).rglob('*') if p.is_file())
        if any(Path(p).stat().st_mtime > started_at for p in inputs if p is not None):
            raise ValueError('输入表格或签名资源在原任务启动后有变更，不能复用该日志续跑')
        completed_pdfs = {line[len('已生成: '):].split('，焊口标注', 1)[0].strip().casefold()
                          for line in previous_log_lines
                          if line.startswith('已生成: ')}
        print(f'续跑原始启动时间: {started_at}')
        pending_jobs = []
        for job in jobs:
            row = completed_log_row(job, args.output_dir, args.audit_dir, completed_pdfs, started_at)
            if row is None:
                pending_jobs.append(job)
            else:
                rows.append(row)
                # Retain the same completion line so this resumed log can be
                # used again without losing earlier completed pages.
                print(f"已生成: {row['pdf']}，焊口标注{row['callout_count']}个，"
                      f"管理编号{row['management_callout_count']}个，管段号{row['spool_callout_count']}个（复用）")
        print(f'续跑：复用{len(rows)}页，剩余{len(pending_jobs)}页，并发{args.workers}')
    # Unified page layout is CPU-bound Python work.  A thread pool leaves the
    # expensive compatibility search behind the GIL and effectively serial;
    # page jobs are independent, so processes provide real --workers
    # parallelism without changing any per-page layout or rendering rule.
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        if args.audit_only:
            futures = {
                executor.submit(
                    audit_weld_job,
                    job,
                    args.audit_dir,
                    management_by_sheet.get((sheet_id_key(job.sheet_id), job.page), []),
                    (
                        construction_records_for_sheet(construction_by_sheet, job.sheet_id)
                        if construction_by_sheet is not None
                        else None
                    ),
                    args.profile,
                    manual_weld_by_sheet.get(sheet_id_key(job.sheet_id), []),
                    spool_by_sheet.get((sheet_id_key(job.sheet_id), job.page), []),
                ): job
                for job in pending_jobs
            }
        else:
            futures = {
                executor.submit(
                    convert_weld_job,
                    job,
                    args.output_dir,
                    args.audit_dir,
                    args.force,
                    management_by_sheet.get((sheet_id_key(job.sheet_id), job.page), []),
                    (
                        construction_records_for_sheet(construction_by_sheet, job.sheet_id)
                        if construction_by_sheet is not None
                        else None
                    ),
                    args.profile,
                    args.signature_dir,
                    manual_weld_by_sheet.get(sheet_id_key(job.sheet_id), []),
                    spool_by_sheet.get((sheet_id_key(job.sheet_id), job.page), []),
                    args.repair_layout,
                ): job
                for job in pending_jobs
            }
        for future in as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
                if args.audit_only:
                    row = _audit_batch_row(result)
                    print(
                        f"已审计: {result.sheet_id}_{result.page}，"
                        f"可定位焊口{result.callout_count}个，"
                        f"可定位管理编号{result.management_callout_count}个，"
                        f"可定位管段号{result.spool_callout_count}个"
                    )
                else:
                    row = _batch_row(result)
                    print(
                        f"已生成: {result.pdf_path}，焊口标注{result.callout_count}个，"
                        f"管理编号{result.management_callout_count}个，"
                        f"管段号{result.spool_callout_count}个"
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
        "mode": "audit-only" if args.audit_only else "convert",
        "job_count": len(jobs),
        "success_page_count": sum(row["status"] == "success" for row in rows),
        "success_pdf_count": (
            0 if args.audit_only else sum(row["status"] == "success" for row in rows)
        ),
        "failed_pdf_count": sum(row["status"] == "failed" for row in rows),
        "callout_count": sum(int(row.get("callout_count", 0)) for row in rows),
        "management_callout_count": sum(int(row.get("management_callout_count", 0)) for row in rows),
        "spool_callout_count": sum(int(row.get("spool_callout_count", 0)) for row in rows),
        "numbered_weld_count": sum(int(row.get("numbered_weld_count", 0)) for row in rows),
        "unmapped_numbered_count": sum(int(row.get("unmapped_numbered_count", 0)) for row in rows),
        "ambiguous_count": sum(int(row.get("ambiguous_count", 0)) for row in rows),
        "ignored_empty_original_weld_count": len(skipped_construction),
        "missing_pcf_sha_count": len(missing_pcf_sheet_ids),
        "rows": rows,
    }
    audit_dir = Path(args.audit_dir).resolve()
    audit_dir.mkdir(parents=True, exist_ok=True)
    summary_path = audit_dir / (
        "编号匹配审计汇总.json" if args.audit_only else "PDF标注汇总.json"
    )
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
                        "manual_review_required": row.manual_review_required,
                    }
                    for row in skipped_construction
                ],
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    review_log_path = write_manual_review_log(audit_dir, rows)
    unmatched_xlsx_path = write_unmatched_number_xlsx(
        audit_dir,
        rows,
        missing_pcf_sheet_ids,
        layout_evaluated=not args.audit_only,
        extra_issues=table_issues_without_jobs,
    )
    print(f"人工审核日志: {review_log_path}")
    print(f"人工补标清单: {unmatched_xlsx_path}")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, ensure_ascii=False, indent=2))
    return 1 if summary["failed_pdf_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
