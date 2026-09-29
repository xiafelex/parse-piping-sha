#!/usr/bin/env python3
"""Copy Blue Star PDF/PCF/SHA files into unit/page-count/pipeline folders.

The PCF is always copied from the weld-number backfilled PCF directory.  The
matching PDF and SHA are selected from the same original package directory as
the unique original PCF for that unit and PIPELINE-REFERENCE.  No source file
is modified, and an existing output directory is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from pypdf import PdfReader


SCRIPT_DIR = Path(__file__).resolve().parents[4]
PROJECT_ROOT = SCRIPT_DIR / "蓝星项目"
DEFAULT_PCF_ROOT = PROJECT_ROOT / "PCF" / "最终可导入完整版本_第2版_焊口号反写"
DEFAULT_OUTPUT = PROJECT_ROOT / "按单元_PDF页数_管线整理_焊口号版"
DEFAULT_AUDIT = PROJECT_ROOT / "按单元_PDF页数_管线整理_焊口号版_整理清单.xlsx"
UNITS = ("100", "701", "N400")

PIPELINE_RE = re.compile(
    rb"^PIPELINE-REFERENCE[ \t]+([^\r\n]+)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class OriginalSet:
    unit: str
    pipeline: str
    original_pcf: Path
    pdf: Path
    sha: Path
    pdf_pages: int


@dataclass(frozen=True)
class CopyItem:
    unit: str
    pipeline: str
    pdf_pages: int
    weld_number_pcf: Path
    original_pcf: Path
    pdf: Path
    sha: Path
    destination_relative: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将蓝星项目整理为 单元/“N页”/管线号/PDF+PCF+SHA。"
    )
    parser.add_argument(
        "--pcf-root",
        type=Path,
        default=DEFAULT_PCF_ROOT,
        help="本次写入焊口号后的PCF目录",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="包含100、701、N400原始资料的蓝星项目目录",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="新建的整理副本目录",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=DEFAULT_AUDIT,
        help="整理清单Excel（位于输出目录外）",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pipeline_reference(path: Path) -> str:
    match = PIPELINE_RE.search(path.read_bytes())
    if not match:
        raise ValueError(f"PCF缺少PIPELINE-REFERENCE：{path}")
    return match.group(1).strip().decode("ascii", errors="strict")


def pcf_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pcf"
    )


def matching_asset_files(parent: Path, pipeline: str, suffix: str) -> list[Path]:
    pattern = re.compile(rf"^{re.escape(pipeline)}-\d+$", re.IGNORECASE)
    return sorted(
        path
        for path in parent.iterdir()
        if path.is_file()
        and path.suffix.lower() == suffix.lower()
        and pattern.fullmatch(path.stem)
    )


def discover_original_sets(project_root: Path) -> dict[tuple[str, str], OriginalSet]:
    original_pcfs: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for unit in UNITS:
        unit_root = project_root / unit
        if not unit_root.is_dir():
            raise FileNotFoundError(f"缺少原始单元目录：{unit_root}")
        for path in pcf_files(unit_root):
            original_pcfs[(unit, pipeline_reference(path))].append(path)

    result: dict[tuple[str, str], OriginalSet] = {}
    for key, candidates in original_pcfs.items():
        unit, pipeline = key
        if len(candidates) != 1:
            raise ValueError(
                f"原始PCF不是唯一：单元={unit}，管线={pipeline}，"
                f"数量={len(candidates)}，文件={[str(path) for path in candidates]}"
            )
        original_pcf = candidates[0]
        pdfs = matching_asset_files(original_pcf.parent, pipeline, ".pdf")
        shas = matching_asset_files(original_pcf.parent, pipeline, ".sha")
        if len(pdfs) != 1 or len(shas) != 1:
            raise ValueError(
                f"同批次PDF/SHA不是唯一：单元={unit}，管线={pipeline}，"
                f"PDF={[path.name for path in pdfs]}，SHA={[path.name for path in shas]}，"
                f"目录={original_pcf.parent}"
            )
        try:
            pages = len(PdfReader(pdfs[0]).pages)
        except Exception as exc:
            raise ValueError(f"无法读取PDF页数：{pdfs[0]}：{exc}") from exc
        if pages < 1:
            raise ValueError(f"PDF页数小于1：{pdfs[0]}")
        result[key] = OriginalSet(
            unit=unit,
            pipeline=pipeline,
            original_pcf=original_pcf,
            pdf=pdfs[0],
            sha=shas[0],
            pdf_pages=pages,
        )
    return result


def build_copy_plan(
    pcf_root: Path,
    original_sets: dict[tuple[str, str], OriginalSet],
) -> list[CopyItem]:
    plan: list[CopyItem] = []
    seen: set[tuple[str, str]] = set()
    for pcf in pcf_files(pcf_root):
        relative = pcf.relative_to(pcf_root)
        if len(relative.parts) < 2:
            raise ValueError(f"焊口号版PCF不在单元目录内：{pcf}")
        unit = relative.parts[0]
        pipeline = pipeline_reference(pcf)
        key = (unit, pipeline)
        if key in seen:
            raise ValueError(f"焊口号版PCF管线重复：单元={unit}，管线={pipeline}")
        seen.add(key)
        original = original_sets.get(key)
        if original is None:
            raise ValueError(f"找不到原始PCF/PDF/SHA组合：单元={unit}，管线={pipeline}")
        destination = Path(unit) / f"{original.pdf_pages}页" / pipeline
        plan.append(
            CopyItem(
                unit=unit,
                pipeline=pipeline,
                pdf_pages=original.pdf_pages,
                weld_number_pcf=pcf,
                original_pcf=original.original_pcf,
                pdf=original.pdf,
                sha=original.sha,
                destination_relative=destination,
            )
        )
    return sorted(plan, key=lambda item: (item.unit, item.pdf_pages, item.pipeline))


def assert_distinct_names(item: CopyItem) -> None:
    names = [item.pdf.name.casefold(), item.weld_number_pcf.name.casefold(), item.sha.name.casefold()]
    if len(set(names)) != 3:
        raise ValueError(f"目标文件名冲突：单元={item.unit}，管线={item.pipeline}，文件={names}")


def copy_plan(plan: list[CopyItem], temp_output: Path) -> None:
    for item in plan:
        assert_distinct_names(item)
        destination = temp_output / item.destination_relative
        destination.mkdir(parents=True, exist_ok=False)
        shutil.copy2(item.pdf, destination / item.pdf.name)
        shutil.copy2(item.weld_number_pcf, destination / item.weld_number_pcf.name)
        shutil.copy2(item.sha, destination / item.sha.name)


def style_sheet(worksheet, widths: dict[int, float]) -> None:
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
        cell.fill = fill
    for index, width in widths.items():
        worksheet.column_dimensions[get_column_letter(index)].width = width


def write_audit(path: Path, output_root: Path, pcf_root: Path, plan: list[CopyItem]) -> None:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "汇总"
    summary.append(["项目", "值"])
    summary.append(["输出目录", str(output_root)])
    summary.append(["管线数", len(plan)])
    summary.append(["PDF数", len(plan)])
    summary.append(["PCF数", len(plan)])
    summary.append(["SHA数", len(plan)])
    summary.append(["PCF来源", str(pcf_root)])
    summary.append([])
    summary.append(["单元", "PDF页数", "管线数"])
    distribution = Counter((item.unit, item.pdf_pages) for item in plan)
    for (unit, pages), count in sorted(distribution.items(), key=lambda row: (row[0][0], row[0][1])):
        summary.append([unit, pages, count])
    summary.column_dimensions["A"].width = 28
    summary.column_dimensions["B"].width = 90
    summary.column_dimensions["C"].width = 14
    summary["A1"].font = Font(bold=True)
    summary["B1"].font = Font(bold=True)

    detail = workbook.create_sheet("整理明细")
    detail.append(
        [
            "单元",
            "PDF页数",
            "管线号",
            "目标相对目录",
            "PDF文件名",
            "PCF文件名",
            "SHA文件名",
            "PDF来源",
            "焊口号PCF来源",
            "原始PCF定位依据",
            "SHA来源",
            "PDF字节数",
            "PCF字节数",
            "SHA字节数",
            "PDF SHA256",
            "PCF SHA256",
            "SHA文件 SHA256",
        ]
    )
    for item in plan:
        detail.append(
            [
                item.unit,
                item.pdf_pages,
                item.pipeline,
                item.destination_relative.as_posix(),
                item.pdf.name,
                item.weld_number_pcf.name,
                item.sha.name,
                str(item.pdf),
                str(item.weld_number_pcf),
                str(item.original_pcf),
                str(item.sha),
                item.pdf.stat().st_size,
                item.weld_number_pcf.stat().st_size,
                item.sha.stat().st_size,
                sha256_file(item.pdf),
                sha256_file(item.weld_number_pcf),
                sha256_file(item.sha),
            ]
        )
    style_sheet(
        detail,
        {
            1: 10,
            2: 12,
            3: 36,
            4: 65,
            5: 42,
            6: 42,
            7: 42,
            8: 90,
            9: 90,
            10: 90,
            11: 90,
            15: 68,
            16: 68,
            17: 68,
        },
    )
    workbook.save(path)


def validate_output(temp_output: Path, plan: list[CopyItem]) -> None:
    pipeline_directories = [
        directory
        for directory in temp_output.glob("*/*/*")
        if directory.is_dir()
    ]
    if len(pipeline_directories) != len(plan):
        raise ValueError(
            f"输出管线目录数不正确：实际{len(pipeline_directories)}，预期{len(plan)}"
        )

    for item in plan:
        destination = temp_output / item.destination_relative
        files = sorted(path for path in destination.iterdir() if path.is_file())
        if len(files) != 3:
            raise ValueError(f"管线目录不是3个文件：{destination}，实际{len(files)}")
        expected = {
            item.pdf.name: item.pdf,
            item.weld_number_pcf.name: item.weld_number_pcf,
            item.sha.name: item.sha,
        }
        if {path.name for path in files} != set(expected):
            raise ValueError(f"管线目录文件名不正确：{destination}")
        for name, source in expected.items():
            copied = destination / name
            if copied.stat().st_size != source.stat().st_size:
                raise ValueError(f"复制后字节数不一致：{copied}")
            if sha256_file(copied) != sha256_file(source):
                raise ValueError(f"复制后SHA256不一致：{copied}")
        copied_pages = len(PdfReader(destination / item.pdf.name).pages)
        if copied_pages != item.pdf_pages:
            raise ValueError(f"复制后PDF页数变化：{destination / item.pdf.name}")
        if destination.parent.name != f"{item.pdf_pages}页":
            raise ValueError(f"PDF页数目录命名错误：{destination}")


def source_fingerprints(plan: list[CopyItem]) -> dict[Path, tuple[int, str]]:
    sources = {
        source
        for item in plan
        for source in (item.pdf, item.weld_number_pcf, item.original_pcf, item.sha)
    }
    return {path: (path.stat().st_size, sha256_file(path)) for path in sources}


def validate_sources_unchanged(fingerprints: dict[Path, tuple[int, str]]) -> None:
    for path, (size, digest) in fingerprints.items():
        if not path.is_file() or path.stat().st_size != size or sha256_file(path) != digest:
            raise ValueError(f"源文件在整理期间发生变化：{path}")


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    pcf_root = args.pcf_root.resolve()
    output_root = args.output_dir.resolve()
    audit_path = args.audit.resolve()

    if not project_root.is_dir():
        raise FileNotFoundError(f"蓝星项目目录不存在：{project_root}")
    if not pcf_root.is_dir():
        raise FileNotFoundError(f"焊口号版PCF目录不存在：{pcf_root}")
    if output_root.exists():
        raise FileExistsError(f"输出目录已存在，为避免覆盖已停止：{output_root}")
    if audit_path.exists():
        raise FileExistsError(f"整理清单已存在，为避免覆盖已停止：{audit_path}")

    original_sets = discover_original_sets(project_root)
    plan = build_copy_plan(pcf_root, original_sets)
    if not plan:
        raise ValueError("没有可整理的焊口号版PCF")
    fingerprints = source_fingerprints(plan)

    temp_output = output_root.parent / f".{output_root.name}.tmp-{uuid.uuid4().hex[:8]}"
    temp_audit = audit_path.parent / f".{audit_path.name}.tmp-{uuid.uuid4().hex[:8]}.xlsx"
    try:
        temp_output.mkdir(parents=False, exist_ok=False)
        copy_plan(plan, temp_output)
        write_audit(temp_audit, output_root, pcf_root, plan)
        validate_output(temp_output, plan)
        validate_sources_unchanged(fingerprints)
        temp_output.rename(output_root)
        temp_audit.rename(audit_path)
    except Exception:
        if temp_output.exists() and temp_output.parent.resolve() == output_root.parent.resolve():
            if temp_output.name.startswith(f".{output_root.name}.tmp-"):
                shutil.rmtree(temp_output)
        if temp_audit.exists() and temp_audit.parent.resolve() == audit_path.parent.resolve():
            if temp_audit.name.startswith(f".{audit_path.name}.tmp-"):
                temp_audit.unlink()
        raise

    distribution = Counter(item.pdf_pages for item in plan)
    report = {
        "管线数": len(plan),
        "PDF数": len(plan),
        "PCF数": len(plan),
        "SHA数": len(plan),
        "PDF页数分布": {f"{pages}页": count for pages, count in sorted(distribution.items())},
        "输出目录": str(output_root),
        "整理清单": str(audit_path),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)
