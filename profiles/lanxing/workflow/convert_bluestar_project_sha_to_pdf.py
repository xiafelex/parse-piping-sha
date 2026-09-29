#!/usr/bin/env python3
"""Convert all Blue Star SHA drawings to annotated, single-page PDF files.

The source SHA and PCF files are read-only.  Weld numbers and S/F types come
from the already audited PCF copies written from the Blue Star Excel weld
table.  Every physical SHA Sheet becomes one A1-landscape PDF named
``<pipeline>_<page>.pdf`` below its unit directory.

The job is resumable.  A successful line is skipped only when source hashes,
conversion-code hashes, output names and output hashes still match its status
record.  Failures are isolated per line and do not discard completed PDFs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader


SCRIPT_DIR = Path(__file__).resolve().parents[4]
DEFAULT_PROJECT_ROOT = SCRIPT_DIR / "蓝星项目"
DEFAULT_PCF_ROOT = (
    DEFAULT_PROJECT_ROOT
    / "PCF"
    / "最终可导入完整版本_第2版_焊口号及类型反写"
)
DEFAULT_OUTPUT = DEFAULT_PROJECT_ROOT / "PDF转换"
DEFAULT_REPOSITORY = Path(__file__).resolve().parents[1] / "parser"
DEFAULT_UNITS = ("100", "701", "N400")
SHA_VERSION_RE = re.compile(r"^(?P<line>.+)-(?P<version>\d+)$")


@dataclass(frozen=True)
class Job:
    unit: str
    line_id: str
    sha_path: str
    pcf_path: str
    sha_version: int
    skipped_sha_versions: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--pcf-root", type=Path, default=DEFAULT_PCF_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repository", type=Path, default=DEFAULT_REPOSITORY)
    parser.add_argument("--units", nargs="+", default=list(DEFAULT_UNITS))
    parser.add_argument(
        "--line",
        action="append",
        default=[],
        help="只转换指定管线号；可重复使用。",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="并行管线数。建议先用1，确认稳定后使用2。",
    )
    parser.add_argument("--distance-threshold", type=float, default=80.0)
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略成功状态并覆盖本脚本此前发布的同名PDF。",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="只检查SHA/PCF配对、版本选择和物理页数，不生成PDF。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="仅处理排序后的前N条，用于小范围验证。",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="出现首条失败后不再提交新任务（已运行任务仍会结束）。",
    )
    parser.add_argument(
        "--keep-work-on-error",
        action="store_true",
        help="失败时保留该管线的临时技术文件，便于诊断。",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def line_and_version(sha_path: Path) -> tuple[str, int]:
    match = SHA_VERSION_RE.fullmatch(sha_path.stem)
    if not match:
        raise ValueError(f"SHA文件名不符合<管线号>-<版本>.sha：{sha_path}")
    return match.group("line"), int(match.group("version"))


def discover_jobs(
    project_root: Path,
    pcf_root: Path,
    units: list[str],
    selected_lines: set[str],
) -> tuple[list[Job], list[dict[str, Any]]]:
    jobs: list[Job] = []
    issues: list[dict[str, Any]] = []
    seen_output_keys: set[tuple[str, str]] = set()
    for unit in units:
        unit_root = project_root / unit
        unit_pcf_root = pcf_root / unit
        if not unit_root.is_dir():
            issues.append({"unit": unit, "error": f"单元目录不存在：{unit_root}"})
            continue
        if not unit_pcf_root.is_dir():
            issues.append({"unit": unit, "error": f"反写PCF目录不存在：{unit_pcf_root}"})
            continue
        candidates: dict[str, list[tuple[Path, int]]] = defaultdict(list)
        for sha_path in sorted(unit_root.rglob("*.sha")):
            try:
                line_id, version = line_and_version(sha_path)
            except ValueError as exc:
                issues.append({"unit": unit, "sha": str(sha_path), "error": str(exc)})
                continue
            if selected_lines and line_id not in selected_lines:
                continue
            candidates[line_id].append((sha_path, version))

        for line_id, versions in sorted(candidates.items()):
            pcf_path = unit_pcf_root / f"{line_id}-pcf.pcf"
            if not pcf_path.is_file():
                issues.append(
                    {
                        "unit": unit,
                        "line_id": line_id,
                        "error": f"缺少焊口号反写PCF：{pcf_path}",
                    }
                )
                continue
            # Prefer the SHA delivered beside a local PCF.  This resolves the
            # two known N400 -0/-1 revisions in favour of the current -1 issue.
            # If that evidence is unavailable, take the highest numeric suffix.
            versions.sort(
                key=lambda item: (
                    (item[0].parent / f"{line_id}-pcf.pcf").is_file(),
                    item[1],
                    item[0].stat().st_mtime_ns,
                    str(item[0]),
                ),
                reverse=True,
            )
            chosen, version = versions[0]
            key = (unit, line_id)
            if key in seen_output_keys:
                issues.append(
                    {
                        "unit": unit,
                        "line_id": line_id,
                        "error": "同一单元内管线号重复，无法安全命名输出",
                    }
                )
                continue
            seen_output_keys.add(key)
            jobs.append(
                Job(
                    unit=unit,
                    line_id=line_id,
                    sha_path=str(chosen.resolve()),
                    pcf_path=str(pcf_path.resolve()),
                    sha_version=version,
                    skipped_sha_versions=tuple(
                        str(path.resolve()) for path, _ in versions[1:]
                    ),
                )
            )
    if selected_lines:
        found = {job.line_id for job in jobs}
        for missing in sorted(selected_lines - found):
            issues.append({"line_id": missing, "error": "指定管线未找到可转换的SHA/PCF配对"})
    return sorted(jobs, key=lambda job: (job.unit, job.line_id)), issues


def conversion_code_hash(repository: Path) -> str:
    files = [
        Path(__file__).resolve(),
        repository / "sha_to_svg_prototype.py",
        repository / "inject_sha_weld_callouts.py",
        repository / "verify_sha_weld_callouts.py",
        repository / "number_pcf_welds.py",
        repository / "audit_pcf_sha_weld_topology.py",
        repository / "render_svg_pdf.mjs",
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def validate_pdf(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size < 1000:
        raise ValueError(f"PDF不存在或过小：{path}")
    reader = PdfReader(str(path))
    if len(reader.pages) != 1:
        raise ValueError(f"PDF不是单页：{path}，实际{len(reader.pages)}页")
    page = reader.pages[0]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    if width <= height:
        raise ValueError(f"PDF不是横向页面：{path}，{width}x{height}")
    return {
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "media_box_points": [round(width, 3), round(height, 3)],
    }


def run_logged(
    command: list[str],
    cwd: Path,
    log: list[str],
    *,
    attempts: int = 1,
    retry_delay_seconds: float = 1.0,
) -> None:
    if attempts <= 0:
        raise ValueError("子进程尝试次数必须为正数")
    environment = os.environ.copy()
    # Every JSON handoff in this pipeline is UTF-8 and project paths contain
    # Chinese text.  Make child Python behaviour independent of the launching
    # terminal's Windows ANSI code page.
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    completed: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.append(
            f"$ {' '.join(command)}"
            f"\n[attempt {attempt}/{attempts}]\n{completed.stdout}"
        )
        if completed.returncode == 0:
            return
        if attempt < attempts:
            time.sleep(retry_delay_seconds)
    assert completed is not None
    raise subprocess.CalledProcessError(
        completed.returncode, command, output=completed.stdout
    )


def rendered_weld_labels(svg_path: Path) -> list[str]:
    import xml.etree.ElementTree as ET

    root = ET.parse(svg_path).getroot()
    return [
        "".join(element.itertext()).strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
        and str(element.attrib.get("data-mapping", "")).startswith(
            "sha-injected-weld"
        )
    ]


def status_path(output_root: Path, job: Job) -> Path:
    return output_root / "_转换记录" / "状态" / job.unit / f"{job.line_id}.json"


def successful_status_is_current(
    output_root: Path,
    job: Job,
    source_sha256: str,
    source_pcf_sha256: str,
    code_hash: str,
) -> dict[str, Any] | None:
    path = status_path(output_root, job)
    if not path.is_file():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        status.get("status") != "success"
        or status.get("source_sha256") != source_sha256
        or status.get("source_pcf_sha256") != source_pcf_sha256
        or status.get("conversion_code_sha256") != code_hash
    ):
        return None
    outputs = status.get("outputs", [])
    if not outputs:
        return None
    for item in outputs:
        path = output_root / str(item.get("relative_path", ""))
        if (
            not path.is_file()
            or path.stat().st_size != int(item.get("size", -1))
            or sha256_file(path) != item.get("sha256")
        ):
            return None
    return status


def build_weld_map(
    pcf_path: Path,
    sha_path: Path,
    output_path: Path,
    distance_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    from audit_pcf_sha_weld_topology import audit_topology
    from number_pcf_welds import (
        long_pipe_drawing_overrides,
        number_welds,
        parse_pipe_components,
        parse_weld_blocks,
        sha_implicit_pipe_end_weld_points,
        sha_virtual_weld_points,
        sha_weld_points,
    )

    source_lines, welds = parse_weld_blocks(pcf_path)
    overrides = long_pipe_drawing_overrides(
        welds, parse_pipe_components(source_lines)
    )
    sha_points = sha_weld_points(sha_path, distance_threshold)
    sha_points.update(
        sha_virtual_weld_points(sha_path, welds, sha_points, overrides)
    )
    sha_points.update(
        sha_implicit_pipe_end_weld_points(
            sha_path, source_lines, welds, sha_points
        )
    )
    numbered = number_welds(
        welds, sha_points, prefix="", use_repeat_id=True
    )
    for row in numbered:
        repeat_id = str(row.get("repeat_weld_identifier", "")).strip()
        row["weld_number"] = repeat_id if repeat_id and repeat_id != "0" else ""
    actual_numbers = [
        str(row["weld_number"]) for row in numbered if row["weld_number"]
    ]
    duplicates = [
        value for value, count in Counter(actual_numbers).items() if count > 1
    ]
    if duplicates:
        raise ValueError(f"PCF焊口号重复：{duplicates}")
    too_long = [value for value in actual_numbers if len(value) > 5]
    if too_long:
        raise ValueError(f"焊口号超过5字符：{too_long}")
    mapped = [
        row
        for row in numbered
        if row.get("page") not in (None, 999999)
        and row.get("graphic_ref") is not None
    ]
    callout_targets = [row for row in mapped if row.get("weld_number")]
    unresolved_types = [
        row for row in callout_targets if row.get("weld_type") not in {"S", "F"}
    ]
    if unresolved_types:
        raise ValueError(f"存在无法判定S/F的已编号焊口：{len(unresolved_types)}")
    numbered_unmapped = [
        row
        for row in numbered
        if row.get("weld_number")
        and (
            row.get("page") in (None, 999999)
            or row.get("graphic_ref") is None
        )
    ]
    payload = {
        "pcf": str(pcf_path),
        "sha": str(sha_path),
        "number_source": (
            "audited Blue Star PCF copy written from Excel; "
            "REPEAT-WELD-IDENTIFIER 0/blank is not fabricated"
        ),
        "marker_rule": "F=square (diamond orientation); S=circle",
        "leader_rule": "one direct straight line",
        "weld_count": len(numbered),
        "mapped_weld_count": len(mapped),
        "callout_target_count": len(callout_targets),
        "unnumbered_count": sum(not row.get("weld_number") for row in numbered),
        "welds": numbered,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    topology = audit_topology(pcf_path, sha_path, output_path)
    if numbered_unmapped:
        raise ValueError(
            "存在已编号但没有严格SHA焊点证据的焊口："
            f"{[row['weld_number'] for row in numbered_unmapped]}"
        )
    if callout_targets and not bool(
        topology["summary"]["semantic_matching_ready"]
    ):
        raise ValueError(
            "未通过焊口语义匹配门禁："
            f"{topology['summary']['semantic_gate']}"
        )
    return numbered, callout_targets, topology


def verify_callouts(
    qa: dict[str, Any], callout_targets: list[dict[str, Any]]
) -> None:
    if int(qa["callout_count"]) != len(callout_targets):
        raise ValueError(
            f"注入数量不完整：目标{len(callout_targets)}，实际{qa['callout_count']}"
        )
    fields = (
        "leader_starts_at_source_dot",
        "leader_is_single_straight_line",
        "on_geometry",
        "marker_closed",
        "leader_ends_at_marker_edge",
        "label_inside_marker",
        "marker_shape_matches_weld_type",
        "marker_clear_of_source_geometry",
        "marker_clear_of_source_text",
        "leader_clear_of_source_geometry_and_text",
        "marker_inside_outer_frame",
        "marker_inside_drawing_region",
        "marker_has_drawing_clearance",
        "leader_clear_of_other_leaders",
        "leader_clear_of_other_markers",
        "marker_clear_of_other_markers",
        "on_expected_pipe_centerline",
    )
    failed = [
        item.get("label")
        for item in qa["callouts"]
        if not all(bool(item.get(field)) for field in fields)
    ]
    if failed:
        raise ValueError(f"存在未通过结构/布局验证的焊口标注：{failed}")


def process_job(
    job: Job,
    config: dict[str, Any],
) -> dict[str, Any]:
    started = datetime.now().astimezone()
    output_root = Path(config["output_root"])
    repository = Path(config["repository"])
    sha_path = Path(job.sha_path)
    pcf_path = Path(job.pcf_path)
    state_path = status_path(output_root, job)
    source_sha256 = sha256_file(sha_path)
    source_pcf_sha256 = sha256_file(pcf_path)
    code_hash = str(config["code_hash"])
    if not config["force"]:
        current = successful_status_is_current(
            output_root,
            job,
            source_sha256,
            source_pcf_sha256,
            code_hash,
        )
        if current is not None:
            return {
                "status": "skipped",
                "unit": job.unit,
                "line_id": job.line_id,
                "page_count": int(current["page_count"]),
                "callout_count": int(current["callout_count"]),
            }

    sys.path.insert(0, str(repository))
    from analyze_iso_split import read_sha_streams
    from inject_sha_weld_callouts import sheet_streams

    work_root = (
        output_root
        / "._work"
        / f"{job.unit}__{job.line_id}__{uuid.uuid4().hex[:8]}"
    )
    work_root.mkdir(parents=True, exist_ok=False)
    log: list[str] = []
    preserve_work = False
    try:
        weld_map = work_root / "weld-map.json"
        numbered, callout_targets, topology = build_weld_map(
            pcf_path,
            sha_path,
            weld_map,
            float(config["distance_threshold"]),
        )
        render_sha = sha_path
        qa: dict[str, Any] = {"callout_count": 0, "callouts": []}
        if callout_targets:
            annotated_sha = work_root / f"{job.line_id}-含焊口号.sha"
            run_logged(
                [
                    sys.executable,
                    str(repository / "inject_sha_weld_callouts.py"),
                    str(sha_path),
                    "--output",
                    str(annotated_sha),
                    "--weld-map",
                    str(weld_map),
                    "--distance-threshold",
                    str(config["distance_threshold"]),
                    "--diamond-radius",
                    "80",
                ],
                repository,
                log,
            )
            qa_path = work_root / "weld-callout-verification.json"
            run_logged(
                [
                    sys.executable,
                    str(repository / "verify_sha_weld_callouts.py"),
                    str(sha_path),
                    str(annotated_sha),
                    str(weld_map),
                    "--output",
                    str(qa_path),
                ],
                repository,
                log,
            )
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            verify_callouts(qa, callout_targets)
            render_sha = annotated_sha

        targets = sheet_streams(read_sha_streams(sha_path), None)
        pages = [int(target.page) for target in targets]
        if len(pages) != len(set(pages)):
            raise ValueError(f"物理Sheet页码重复，无法按管线号_页码命名：{pages}")
        if not targets:
            raise ValueError("SHA中没有可渲染的物理Sheet")

        prepared_outputs: list[tuple[Path, Path, dict[str, Any]]] = []
        for target in targets:
            svg_path = work_root / f"{job.line_id}_{target.page}.svg"
            trace_path = work_root / f"{job.line_id}_{target.page}.trace.json"
            run_logged(
                [
                    sys.executable,
                    str(repository / "sha_to_svg_prototype.py"),
                    str(render_sha),
                    "--page",
                    str(target.page),
                    "--sheet-stream",
                    target.stream_name,
                    "--output",
                    str(svg_path),
                    "--manifest",
                    str(trace_path),
                ],
                repository,
                log,
            )
            expected_labels = sorted(
                str(row["weld_number"])
                for row in callout_targets
                if int(row["page"]) == int(target.page)
            )
            actual_labels = sorted(rendered_weld_labels(svg_path))
            if actual_labels != expected_labels:
                raise ValueError(
                    f"第{target.page}页SVG焊口标签不完整："
                    f"预期{expected_labels}，实际{actual_labels}"
                )
            prepared_pdf = work_root / f"{job.line_id}_{target.page}.pdf"
            run_logged(
                [
                    "node",
                    str(repository / "render_svg_pdf.mjs"),
                    str(svg_path),
                    str(prepared_pdf),
                ],
                repository,
                log,
                attempts=3,
                retry_delay_seconds=2.0,
            )
            pdf_info = validate_pdf(prepared_pdf)
            final_pdf = (
                output_root
                / job.unit
                / f"{job.line_id}_{target.page}.pdf"
            )
            prepared_outputs.append(
                (
                    prepared_pdf,
                    final_pdf,
                    {
                        "page": int(target.page),
                        "sheet_stream": target.stream_name,
                        "relative_path": str(final_pdf.relative_to(output_root)),
                        "weld_labels": expected_labels,
                        "svg_weld_labels_verified": True,
                        **pdf_info,
                    },
                )
            )

        # Recheck immutable inputs immediately before publication.
        if sha256_file(sha_path) != source_sha256:
            raise ValueError("源SHA在转换期间发生变化")
        if sha256_file(pcf_path) != source_pcf_sha256:
            raise ValueError("焊口号反写PCF在转换期间发生变化")

        previous_outputs: set[str] = set()
        if state_path.is_file():
            try:
                previous = json.loads(state_path.read_text(encoding="utf-8"))
                previous_outputs = {
                    str(item["relative_path"])
                    for item in previous.get("outputs", [])
                    if item.get("relative_path")
                }
            except (OSError, json.JSONDecodeError, KeyError):
                previous_outputs = set()
        output_rows: list[dict[str, Any]] = []
        for prepared_pdf, final_pdf, row in prepared_outputs:
            final_pdf.parent.mkdir(parents=True, exist_ok=True)
            if final_pdf.exists() and not config["force"]:
                known = str(final_pdf.relative_to(output_root)) in previous_outputs
                if not known:
                    raise FileExistsError(
                        f"存在不受状态记录管理的同名PDF，未覆盖：{final_pdf}"
                    )
            os.replace(prepared_pdf, final_pdf)
            published = validate_pdf(final_pdf)
            row.update(published)
            output_rows.append(row)

        current_relative = {str(row["relative_path"]) for row in output_rows}
        for stale_relative in sorted(previous_outputs - current_relative):
            stale = output_root / stale_relative
            if stale.is_file() and stale.parent == output_root / job.unit:
                stale.unlink()

        finished = datetime.now().astimezone()
        status = {
            "status": "success",
            "unit": job.unit,
            "line_id": job.line_id,
            "selected_sha_version": job.sha_version,
            "source_sha": str(sha_path),
            "source_pcf": str(pcf_path),
            "source_sha256": source_sha256,
            "source_pcf_sha256": source_pcf_sha256,
            "conversion_code_sha256": code_hash,
            "skipped_sha_versions": list(job.skipped_sha_versions),
            "page_count": len(output_rows),
            "pcf_weld_count": len(numbered),
            "callout_count": len(callout_targets),
            "unnumbered_pcf_weld_count": sum(
                not row.get("weld_number") for row in numbered
            ),
            "topology_semantic_matching_ready": bool(
                topology["summary"]["semantic_matching_ready"]
            ),
            "qa_structurally_verified": int(
                qa.get("typed_marker_structurally_verified", 0)
            ),
            "outputs": output_rows,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "elapsed_seconds": round((finished - started).total_seconds(), 3),
        }
        atomic_json(state_path, status)
        prior_failure_log = (
            output_root
            / "_转换记录"
            / "失败日志"
            / job.unit
            / f"{job.line_id}.log"
        )
        if prior_failure_log.is_file():
            prior_failure_log.unlink()
        return {
            "status": "success",
            "unit": job.unit,
            "line_id": job.line_id,
            "page_count": len(output_rows),
            "callout_count": len(callout_targets),
        }
    except Exception as exc:
        failure_log = (
            output_root
            / "_转换记录"
            / "失败日志"
            / job.unit
            / f"{job.line_id}.log"
        )
        failure_log.parent.mkdir(parents=True, exist_ok=True)
        failure_log.write_text(
            "\n\n".join(log)
            + "\n\n"
            + "".join(traceback.format_exception(exc)),
            encoding="utf-8",
        )
        failure = {
            "status": "failed",
            "unit": job.unit,
            "line_id": job.line_id,
            "selected_sha_version": job.sha_version,
            "source_sha": str(sha_path),
            "source_pcf": str(pcf_path),
            "source_sha256": source_sha256,
            "source_pcf_sha256": source_pcf_sha256,
            "conversion_code_sha256": code_hash,
            "skipped_sha_versions": list(job.skipped_sha_versions),
            "error": f"{type(exc).__name__}: {exc}",
            "failure_log": str(failure_log.relative_to(output_root)),
            "started_at": started.isoformat(),
            "finished_at": datetime.now().astimezone().isoformat(),
        }
        atomic_json(state_path, failure)
        if config["keep_work_on_error"]:
            failure["work_directory"] = str(work_root)
            preserve_work = True
        return failure
    finally:
        if work_root.exists() and not preserve_work:
            shutil.rmtree(work_root)


def page_plan(jobs: list[Job], repository: Path) -> list[dict[str, Any]]:
    sys.path.insert(0, str(repository))
    from analyze_iso_split import read_sha_streams
    from inject_sha_weld_callouts import sheet_streams

    rows: list[dict[str, Any]] = []
    for job in jobs:
        targets = sheet_streams(read_sha_streams(Path(job.sha_path)), None)
        rows.append(
            {
                **asdict(job),
                "pages": [
                    {"page": int(target.page), "sheet_stream": target.stream_name}
                    for target in targets
                ],
                "output_names": [
                    f"{job.line_id}_{int(target.page)}.pdf"
                    for target in targets
                ],
            }
        )
    return rows


def main() -> int:
    args = parse_args()
    if args.workers <= 0:
        raise ValueError("--workers必须为正整数")
    if args.fail_fast and args.workers != 1:
        raise ValueError("--fail-fast仅支持--workers 1")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit必须为正整数")
    project_root = args.project_root.resolve()
    pcf_root = args.pcf_root.resolve()
    output_root = args.output_dir.resolve()
    repository = args.repository.resolve()
    required = [
        "sha_to_svg_prototype.py",
        "inject_sha_weld_callouts.py",
        "verify_sha_weld_callouts.py",
        "number_pcf_welds.py",
        "audit_pcf_sha_weld_topology.py",
        "render_svg_pdf.mjs",
    ]
    missing = [name for name in required if not (repository / name).is_file()]
    if missing:
        raise FileNotFoundError(f"转换代码缺少文件：{missing}")
    if output_root == project_root:
        raise ValueError("输出目录不能覆盖蓝星项目根目录")
    # The default output is intentionally inside the project root.  Only
    # forbid placing it inside one of the recursively scanned unit roots.
    if any(
        output_root == project_root / unit
        or project_root / unit in output_root.parents
        for unit in args.units
    ):
        raise ValueError("输出目录不能位于100、701、N400源单元目录内")

    jobs, issues = discover_jobs(
        project_root,
        pcf_root,
        list(args.units),
        set(args.line),
    )
    if args.limit is not None:
        jobs = jobs[: args.limit]
    if not jobs:
        raise ValueError("没有发现可转换的SHA/PCF配对")
    if issues and not args.plan_only:
        raise ValueError(
            "输入盘点存在问题，请先运行--plan-only查看："
            + json.dumps(issues[:5], ensure_ascii=False)
        )

    if args.plan_only:
        rows = page_plan(jobs, repository)
        payload = {
            "project_root": str(project_root),
            "pcf_root": str(pcf_root),
            "output_root": str(output_root),
            "job_count": len(rows),
            "page_count": sum(len(row["pages"]) for row in rows),
            "superseded_sha_count": sum(
                len(row["skipped_sha_versions"]) for row in rows
            ),
            "issues": issues,
            "jobs": rows,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if issues else 0

    output_root.mkdir(parents=True, exist_ok=True)
    code_hash = conversion_code_hash(repository)
    config = {
        "output_root": str(output_root),
        "repository": str(repository),
        "distance_threshold": args.distance_threshold,
        "code_hash": code_hash,
        "force": args.force,
        "keep_work_on_error": args.keep_work_on_error,
    }
    results: list[dict[str, Any]] = []
    if args.workers == 1:
        for index, job in enumerate(jobs, start=1):
            result = process_job(job, config)
            results.append(result)
            print(
                f"[{index}/{len(jobs)}] {job.unit}/{job.line_id}: "
                f"{result['status']}，{result.get('page_count', 0)}页，"
                f"{result.get('callout_count', 0)}个焊口号",
                flush=True,
            )
            if result["status"] == "failed" and args.fail_fast:
                break
    else:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=args.workers
        ) as executor:
            futures = {
                executor.submit(process_job, job, config): job for job in jobs
            }
            for index, future in enumerate(
                concurrent.futures.as_completed(futures), start=1
            ):
                job = futures[future]
                result = future.result()
                results.append(result)
                print(
                    f"[{index}/{len(jobs)}] {job.unit}/{job.line_id}: "
                    f"{result['status']}，{result.get('page_count', 0)}页，"
                    f"{result.get('callout_count', 0)}个焊口号",
                    flush=True,
                )

    summary = {
        "project_root": str(project_root),
        "pcf_root": str(pcf_root),
        "output_root": str(output_root),
        "conversion_code_sha256": code_hash,
        "units": list(args.units),
        "requested_job_count": len(jobs),
        "completed_result_count": len(results),
        "success_count": sum(row["status"] == "success" for row in results),
        "resume_skipped_count": sum(
            row["status"] == "skipped" for row in results
        ),
        "failed_count": sum(row["status"] == "failed" for row in results),
        "published_page_count": sum(
            int(row.get("page_count", 0))
            for row in results
            if row["status"] in {"success", "skipped"}
        ),
        "annotated_callout_count": sum(
            int(row.get("callout_count", 0))
            for row in results
            if row["status"] in {"success", "skipped"}
        ),
        "finished_at": datetime.now().astimezone().isoformat(),
        "failed_lines": [
            {
                "unit": row["unit"],
                "line_id": row["line_id"],
                "error": row.get("error"),
                "failure_log": row.get("failure_log"),
            }
            for row in results
            if row["status"] == "failed"
        ],
    }
    atomic_json(output_root / "转换汇总.json", summary)
    work_parent = output_root / "._work"
    if work_parent.is_dir() and not any(work_parent.rglob("*")):
        work_parent.rmdir()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2 if summary["failed_count"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
