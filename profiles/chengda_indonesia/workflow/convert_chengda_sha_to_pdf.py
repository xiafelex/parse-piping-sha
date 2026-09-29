#!/usr/bin/env python3
"""Convert Chengda Indonesia SHA drawings with the project-specific parser."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from pypdf import PdfReader, PdfWriter


PROGRAM_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DATA_DIR
DEFAULT_INPUT_DIR = PROJECT_DIR / "SHA文件转换"
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT_DIR
PARSER_DIR = CODE_PARSER_DIR


@dataclass(frozen=True)
class PageJob:
    sha_path: Path
    relative_parent: Path
    page: int
    sheet_stream: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--signature-dir",
        type=Path,
        help="单元级签名资源目录；省略时自动查找SHA上级目录中的“签名资源”",
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的受控PDF")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_jobs(input_dir: Path) -> tuple[list[Path], list[PageJob]]:
    sys.path.insert(0, str(PARSER_DIR))
    from analyze_iso_split import read_sha_streams
    from inject_sha_weld_callouts import sheet_streams

    sha_files = sorted(
        (path for path in input_dir.rglob("*.sha") if path.is_file()),
        key=lambda path: str(path.relative_to(input_dir)).casefold(),
    )
    jobs: list[PageJob] = []
    for sha_path in sha_files:
        targets = sheet_streams(read_sha_streams(sha_path), None)
        if not targets:
            raise ValueError(f"SHA中没有可渲染的物理Sheet：{sha_path}")
        pages = [int(target.page) for target in targets]
        if len(pages) != len(set(pages)):
            raise ValueError(f"SHA物理页码重复：{sha_path}，{pages}")
        relative_parent = sha_path.parent.relative_to(input_dir)
        jobs.extend(
            PageJob(
                sha_path=sha_path,
                relative_parent=relative_parent,
                page=int(target.page),
                sheet_stream=str(target.stream_name),
            )
            for target in targets
        )
    return sha_files, jobs


def run_checked(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stdout, result.stderr) if part.strip())
        raise RuntimeError(f"命令失败（{result.returncode}）：{' '.join(command)}\n{detail}")


def validate_pdf(path: Path) -> tuple[float, float]:
    payload = path.read_bytes()
    if not payload.startswith(b"%PDF-"):
        raise ValueError(f"输出缺少PDF文件头：{path}")
    if payload.count(b"%%EOF") != 1:
        raise ValueError(f"输出PDF结束标记数量异常：{path}")
    document = PdfReader(str(path), strict=True)
    if document.is_encrypted:
        raise ValueError(f"输出PDF不应加密：{path}")
    if len(document.pages) != 1:
        raise ValueError(f"输出不是单页PDF：{path}")
    box = document.pages[0].mediabox
    width, height = float(box.width), float(box.height)
    if width <= height:
        raise ValueError(f"输出不是横向页面：{path}，{width}×{height} pt")
    return width, height


def normalize_delivery_pdf(source: Path, target: Path) -> None:
    """Rewrite a browser PDF as a conservative, cross-reader delivery PDF."""

    reader = PdfReader(str(source), strict=True)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    with target.open("wb") as handle:
        writer.write(handle)
        handle.flush()
        os.fsync(handle.fileno())
    validate_pdf(target)


def publish_delivery_pdf(source: Path, target: Path) -> None:
    """Atomically publish a PDF without retaining a sandbox-only Windows ACL."""

    os.replace(source, target)
    if os.name == "nt":
        result = subprocess.run(
            ["icacls", str(target), "/inheritance:e"],
            capture_output=True,
        )
        if result.returncode != 0:
            detail = b"\n".join(
                part for part in (result.stdout, result.stderr) if part.strip()
            ).decode(errors="replace")
            raise RuntimeError(f"恢复输出PDF继承权限失败：{target}\n{detail}")


def signature_dir_for_sha(sha_path: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return Path(explicit).resolve()
    for parent in sha_path.parents:
        candidate = parent / "签名资源"
        if candidate.is_dir():
            return candidate.resolve()
    return None


def convert_job(
    job: PageJob,
    output_dir: Path,
    force: bool,
    signature_dir: Path | None = None,
) -> Path:
    final_dir = output_dir / job.relative_parent
    final_dir.mkdir(parents=True, exist_ok=True)
    final_pdf = final_dir / f"{job.sha_path.stem}_{job.page}.pdf"
    if final_pdf.exists() and not force:
        raise FileExistsError(f"输出已存在；如需覆盖请增加--force：{final_pdf}")

    source_hash = sha256_file(job.sha_path)
    with tempfile.TemporaryDirectory(prefix="._chengda_sha_", dir=final_dir) as temporary:
        work = Path(temporary)
        svg = work / f"{job.sha_path.stem}_{job.page}.svg"
        manifest = work / f"{job.sha_path.stem}_{job.page}.trace.json"
        prepared_pdf = work / f"{job.sha_path.stem}_{job.page}.pdf"
        render_command = [
                sys.executable,
                str(PARSER_DIR / "sha_to_svg_prototype.py"),
                str(job.sha_path),
                "--page",
                str(job.page),
                "--sheet-stream",
                job.sheet_stream,
                "--output",
                str(svg),
                "--manifest",
                str(manifest),
            ]
        resolved_signatures = signature_dir_for_sha(job.sha_path, signature_dir)
        if resolved_signatures is not None:
            render_command.extend(["--signature-dir", str(resolved_signatures)])
        run_checked(render_command, PARSER_DIR)
        run_checked(
            ["node", str(PARSER_DIR / "render_svg_pdf.mjs"), str(svg), str(prepared_pdf)],
            PARSER_DIR,
        )
        validate_pdf(prepared_pdf)
        if sha256_file(job.sha_path) != source_hash:
            raise RuntimeError(f"源SHA在转换过程中发生变化：{job.sha_path}")
        publish_delivery_pdf(prepared_pdf, final_pdf)
    validate_pdf(final_pdf)
    return final_pdf


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"输入目录不存在：{input_dir}")
    if not PARSER_DIR.is_dir():
        raise FileNotFoundError(f"成达专用解析器不存在：{PARSER_DIR}")

    sha_files, jobs = discover_jobs(input_dir)
    print(f"SHA文件: {len(sha_files)}")
    print(f"物理页: {len(jobs)}")
    for job in jobs:
        print(f"{job.sha_path.stem} -> 第{job.page}页 ({job.sheet_stream})")
    if args.plan_only:
        return 0

    outputs: list[Path] = []
    for job in jobs:
        output = convert_job(job, output_dir, args.force, args.signature_dir)
        outputs.append(output)
        print(f"已生成: {output}")
    print(f"转换完成: {len(outputs)}页")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
