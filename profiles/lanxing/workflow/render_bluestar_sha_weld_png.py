#!/usr/bin/env python3
"""Batch-render Blue Star SHA drawings to PNG with PCF weld numbers.

This driver follows the xiafelex/parse-piping-sha workflow:

1. map original PCF WELD UCI records to SHA micro-dot evidence;
2. map PCF-AUGMENTED welds to the existing SHA owner-component centreline;
3. retain the exact PCF REPEAT-WELD-IDENTIFIER value as the label;
4. inject leader/typed-marker/text primitives into a copied SHA (F=square, S=circle);
5. independently verify every injected callout;
6. render every physical SHA Sheet to SVG and a no-crop PNG.

Source PCF/SHA/PDF files are read-only. Derived artifacts are atomically
published to the fixed Blue Star conversion directory, with hashes recorded
in a JSON manifest; later runs replace that directory instead of creating
progressively longer process-folder names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from PIL import Image


SCRIPT_DIR = Path(__file__).resolve().parents[4]
DEFAULT_INPUT = SCRIPT_DIR / "蓝星项目" / "焊口号反写PCF示例"
DEFAULT_REPOSITORY = Path(__file__).resolve().parents[1] / "parser"
DEFAULT_OUTPUT = SCRIPT_DIR / "蓝星项目" / "焊口号反写PCF示例-SHA转换"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--repository", type=Path, default=DEFAULT_REPOSITORY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--png-width", type=int, default=3360)
    parser.add_argument("--distance-threshold", type=float, default=80.0)
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help="允许原子替换自定义的已存在输出目录；固定默认目录自动覆盖。",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def try_inject(
    repository: Path,
    source_sha: Path,
    output_sha: Path,
    weld_map: Path,
    distance_threshold: float,
    identifier: str,
    relaxed_text_clearance: bool = False,
) -> int | None:
    """Inject once; the writer expands each marker to its page text metrics."""
    for diamond_radius in (80,):
        command = [
                sys.executable,
                str(repository / "inject_sha_weld_callouts.py"),
                str(source_sha),
                "--output",
                str(output_sha),
                "--weld-map",
                str(weld_map),
                "--distance-threshold",
                str(distance_threshold),
                "--diamond-radius",
                str(diamond_radius),
            ]
        if relaxed_text_clearance:
            command.append("--relaxed-text-clearance")
        injection = subprocess.run(
            command,
            cwd=repository,
            check=False,
        )
        if injection.returncode == 0:
            return diamond_radius
        if output_sha.exists():
            output_sha.unlink()
        print(
            f"{identifier}: 自适应标注框无完整避让方案",
            file=sys.stderr,
        )
    return None


def drawing_id(path: Path) -> str:
    if path.suffix.lower() == ".pcf" and path.stem.endswith("-pcf"):
        return path.stem[:-4]
    raise ValueError(f"PCF文件名不符合*-pcf.pcf约定：{path}")


def validate_png(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        width, height = image.size
        if width < 1000 or height < 500:
            raise ValueError(f"PNG尺寸异常：{path} -> {width}x{height}")
        preview = image.convert("L")
        preview.thumbnail((300, 300))
        if preview.getextrema()[0] >= 250:
            raise ValueError(f"PNG疑似空白：{path}")
    return width, height


def rendered_weld_labels(svg_path: Path) -> list[str]:
    """Return labels that reached the final SVG weld-label rendering branch."""

    root = ET.parse(svg_path).getroot()
    return [
        "".join(element.itertext()).strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
        and str(element.attrib.get("data-mapping", "")).startswith(
            "sha-injected-weld"
        )
    ]


def publish_directory(source: Path, target: Path) -> None:
    """Publish by verified copy when managed Windows blocks directory rename."""
    shutil.copytree(source, target)
    source_files = {
        path.relative_to(source): path
        for path in source.rglob("*")
        if path.is_file()
    }
    target_files = {
        path.relative_to(target): path
        for path in target.rglob("*")
        if path.is_file()
    }
    if source_files.keys() != target_files.keys():
        raise ValueError("正式输出与临时输出文件清单不一致")
    for relative_path, source_path in source_files.items():
        target_path = target_files[relative_path]
        if source_path.stat().st_size != target_path.stat().st_size:
            raise ValueError(f"正式输出文件大小校验失败：{relative_path}")
        if sha256_file(source_path) != sha256_file(target_path):
            raise ValueError(f"正式输出SHA-256校验失败：{relative_path}")
    shutil.rmtree(source)


def main() -> int:
    args = parse_args()
    input_root = args.input_dir.resolve()
    repository = args.repository.resolve()
    output_root = args.output_dir.resolve()
    if not input_root.is_dir():
        raise FileNotFoundError(f"示例目录不存在：{input_root}")
    required_repo_files = [
        "audit_pcf_sha_weld_topology.py",
        "number_pcf_welds.py",
        "inject_sha_weld_callouts.py",
        "verify_sha_weld_callouts.py",
        "run_sha_iso_render.py",
        "render_svg_png.mjs",
    ]
    missing_repo = [name for name in required_repo_files if not (repository / name).is_file()]
    if missing_repo:
        raise FileNotFoundError(f"解析仓库缺少文件：{', '.join(missing_repo)}")
    if args.png_width <= 0:
        raise ValueError("PNG宽度必须为正数")
    default_output = DEFAULT_OUTPUT.resolve()
    if output_root.exists() and output_root != default_output and not args.overwrite_output:
        raise FileExistsError(f"输出目录已存在，为避免覆盖已停止：{output_root}")

    sys.path.insert(0, str(repository))
    from analyze_iso_split import read_sha_streams
    from audit_pcf_sha_weld_topology import audit_topology
    from inject_sha_weld_callouts import sheet_streams
    from number_pcf_welds import (
        number_welds,
        long_pipe_drawing_overrides,
        parse_pipe_components,
        parse_weld_blocks,
        sha_implicit_pipe_end_weld_points,
        sha_virtual_weld_points,
        sha_weld_points,
    )

    pcfs = sorted(input_root.rglob("*-pcf.pcf"))
    if not pcfs:
        raise ValueError(f"没有找到*-pcf.pcf：{input_root}")
    pairs: list[tuple[Path, Path, str]] = []
    for pcf_path in pcfs:
        identifier = drawing_id(pcf_path)
        sha_path = pcf_path.with_name(f"{identifier}-0.sha")
        if not sha_path.is_file():
            raise FileNotFoundError(f"缺少与PCF同目录的SHA：{sha_path}")
        pairs.append((pcf_path, sha_path, identifier))

    temp_root = output_root.parent / f".{output_root.name}.tmp-{uuid.uuid4().hex[:8]}"
    if temp_root.exists():
        raise FileExistsError(f"临时输出目录意外存在：{temp_root}")
    manifest_rows: list[dict[str, object]] = []
    total_png = 0
    total_callouts = 0
    total_unnumbered = 0
    total_numbered_unmapped = 0
    try:
        temp_root.mkdir(parents=True)
        for pair_index, (pcf_path, sha_path, identifier) in enumerate(pairs, start=1):
            relative_parent = pcf_path.parent.relative_to(input_root)
            line_output = temp_root / relative_parent
            technical = line_output / "技术文件"
            svg_dir = technical / "SVG及追踪"
            technical.mkdir(parents=True)
            svg_dir.mkdir(parents=True)

            pcf_hash_before = sha256_file(pcf_path)
            sha_hash_before = sha256_file(sha_path)
            source_lines, welds = parse_weld_blocks(pcf_path)
            drawing_overrides = long_pipe_drawing_overrides(
                welds, parse_pipe_components(source_lines)
            )
            sha_points = sha_weld_points(sha_path, args.distance_threshold)
            sha_points.update(
                sha_virtual_weld_points(
                    sha_path, welds, sha_points, drawing_overrides
                )
            )
            sha_points.update(
                sha_implicit_pipe_end_weld_points(
                    sha_path, source_lines, welds, sha_points
                )
            )
            numbered = number_welds(
                welds,
                sha_points,
                prefix="",
                use_repeat_id=True,
            )
            # The repository's helper normally assigns a fallback number when
            # REPEAT-WELD-IDENTIFIER is 0.  For this project, preserve PCF as
            # source of truth and do not invent a number for those records.
            for row in numbered:
                repeat_id = str(row.get("repeat_weld_identifier", "")).strip()
                row["weld_number"] = repeat_id if repeat_id and repeat_id != "0" else ""

            actual_numbers = [str(row["weld_number"]) for row in numbered if row["weld_number"]]
            duplicate_numbers = [
                number for number, count in Counter(actual_numbers).items() if count > 1
            ]
            if duplicate_numbers:
                raise ValueError(f"{identifier}焊口号重复：{duplicate_numbers}")
            too_long = [number for number in actual_numbers if len(number) > 5]
            if too_long:
                raise ValueError(f"{identifier}存在超过5字符的焊口号：{too_long}")

            mapped = [
                row
                for row in numbered
                if row.get("page") not in (None, 999999) and row.get("graphic_ref") is not None
            ]
            callout_targets = [row for row in mapped if row.get("weld_number")]
            unresolved_types = [
                row for row in callout_targets if row.get("weld_type") not in {"S", "F"}
            ]
            if unresolved_types:
                raise ValueError(
                    f"{identifier}存在无法判定S/F的已编号焊口：{len(unresolved_types)}"
                )
            unnumbered = [row for row in numbered if not row.get("weld_number")]
            numbered_unmapped = [
                row
                for row in numbered
                if row.get("weld_number")
                and (row.get("page") in (None, 999999) or row.get("graphic_ref") is None)
            ]
            weld_map_path = technical / f"{identifier}-weld-map.json"
            weld_map_payload = {
                "pcf": str(pcf_path),
                "sha": str(sha_path),
                "number_source": "PCF REPEAT-WELD-IDENTIFIER; 0/blank is not fabricated",
                "marker_rule": "F=square (diamond orientation); S=circle (16-segment approximation)",
                "leader_rule": "one direct straight line record from the native or PCF-derived weld anchor to the marker edge",
                "point_evidence_rule": (
                    "native welds use the physical Sheet Shape2D ellipse "
                    "referenced by the immediately preceding serialized "
                    "dynamic record; PCF-AUGMENTED welds use owner-UCI "
                    "centreline interpolation with neighbor-UCI orientation"
                ),
                "weld_count": len(numbered),
                "mapped_weld_count": len(mapped),
                "callout_target_count": len(callout_targets),
                "unnumbered_count": len(unnumbered),
                "welds": numbered,
            }
            weld_map_path.write_text(
                json.dumps(weld_map_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            topology_audit_path = (
                technical / f"{identifier}-焊口拓扑审计.json"
            )
            topology_audit = audit_topology(
                pcf_path, sha_path, weld_map_path
            )
            topology_audit_path.write_text(
                json.dumps(
                    topology_audit, ensure_ascii=False, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            topology_summary = topology_audit["summary"]
            if numbered_unmapped:
                raise ValueError(
                    f"{identifier}存在已编号但没有严格SHA焊点证据的焊口："
                    f"{[row['weld_number'] for row in numbered_unmapped]}"
                )
            if not bool(
                topology_summary["semantic_matching_ready"]
            ):
                raise ValueError(
                    f"{identifier}未通过焊口语义匹配门禁："
                    f"{topology_summary['semantic_gate']}"
                )

            annotated_sha = technical / f"{identifier}-含焊口号.sha"
            selected_diamond_radius: int | list[int] | None = None
            injection_mode = "single-pass"
            # 80 is only the minimum radius. The injector enlarges each S/F
            # marker from the current page's real glyph width and height.
            selected_diamond_radius = try_inject(
                repository,
                sha_path,
                annotated_sha,
                weld_map_path,
                args.distance_threshold,
                identifier,
            )
            if selected_diamond_radius is None:
                raise ValueError(
                    f"{identifier}在自适应标注尺寸下无全页零冲突布局；"
                    "未启用文字重叠或分批交叉回退"
                )

            qa_path = technical / f"{identifier}-焊口标注验证.json"
            run(
                [
                    sys.executable,
                    str(repository / "verify_sha_weld_callouts.py"),
                    str(sha_path),
                    str(annotated_sha),
                    str(weld_map_path),
                    "--output",
                    str(qa_path),
                ],
                repository,
            )
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            if int(qa["callout_count"]) != len(callout_targets):
                raise ValueError(
                    f"{identifier}注入数量不完整：目标{len(callout_targets)}，实际{qa['callout_count']}"
                )
            core_verified = sum(
                all(
                    bool(item[field])
                    for field in (
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
                    )
                )
                for item in qa["callouts"]
            )
            geometry_clear = sum(
                bool(item["marker_clear_of_source_geometry"]) for item in qa["callouts"]
            )
            text_clear = sum(
                bool(item["marker_clear_of_source_text"]) for item in qa["callouts"]
            )
            if core_verified != int(qa["callout_count"]):
                raise ValueError(
                    f"{identifier}存在未通过核心结构验证的焊口标注："
                    f"{core_verified}/{qa['callout_count']}"
                )
            if geometry_clear != int(qa["callout_count"]):
                raise ValueError(
                    f"{identifier}存在压住原始管线几何的焊口标注："
                    f"{geometry_clear}/{qa['callout_count']}"
                )
            if int(qa["typed_marker_structurally_verified"]) != int(qa["callout_count"]):
                raise ValueError(
                    f"{identifier}存在越界、引线交叉或原图相交的焊口标注："
                    f"frame={qa['frame_overflow_count']}，"
                    f"drawing={qa['drawing_region_violation_count']}，"
                    f"source={qa['leader_source_crossing_count']}，"
                    f"leader={qa['leader_leader_crossing_callout_count']}，"
                    f"callout={qa['callout_conflict_count']}"
                )

            run(
                [
                    sys.executable,
                    str(repository / "run_sha_iso_render.py"),
                    str(annotated_sha),
                    "--all-sheets",
                    "--out-dir",
                    str(svg_dir),
                ],
                repository,
            )
            physical_pages = sheet_streams(read_sha_streams(sha_path), None)
            png_rows: list[dict[str, object]] = []
            for target in physical_pages:
                svg_path = svg_dir / f"{annotated_sha.stem}-{target.stream_name}-sha.svg"
                trace_path = svg_dir / f"{annotated_sha.stem}-{target.stream_name}-sha.trace.json"
                if not svg_path.is_file() or not trace_path.is_file():
                    raise FileNotFoundError(f"缺少SHA渲染结果：{svg_path}")
                expected_labels = sorted(
                    str(row["weld_number"])
                    for row in callout_targets
                    if int(row["page"]) == target.page
                )
                binary_labels = sorted(
                    str(item["label"])
                    for item in qa["callouts"]
                    if int(item["page"]) == target.page
                )
                if binary_labels != expected_labels:
                    raise ValueError(
                        f"{identifier}第{target.page}页SHA焊口标签验证失败："
                        f"预期{expected_labels}，实际{binary_labels}"
                    )
                svg_labels = sorted(rendered_weld_labels(svg_path))
                if svg_labels != expected_labels:
                    raise ValueError(
                        f"{identifier}第{target.page}页SVG焊口标签渲染不完整："
                        f"预期{expected_labels}，实际{svg_labels}"
                    )
                png_path = line_output / f"{identifier}-第{target.page}页-含焊口号.png"
                run(
                    [
                        "node",
                        str(repository / "render_svg_png.mjs"),
                        str(svg_path),
                        str(png_path),
                        str(args.png_width),
                    ],
                    repository,
                )
                width, height = validate_png(png_path)
                png_rows.append(
                    {
                        "page": target.page,
                        "sheet_stream": target.stream_name,
                        "png": str(png_path.relative_to(temp_root)),
                        "width": width,
                        "height": height,
                        "weld_labels": expected_labels,
                        "svg_weld_labels_verified": True,
                    }
                )
                total_png += 1

            if sha256_file(pcf_path) != pcf_hash_before:
                raise ValueError(f"源PCF在处理期间发生变化：{pcf_path}")
            if sha256_file(sha_path) != sha_hash_before:
                raise ValueError(f"源SHA在处理期间发生变化：{sha_path}")
            total_callouts += len(callout_targets)
            total_unnumbered += len(unnumbered)
            total_numbered_unmapped += len(numbered_unmapped)
            manifest_rows.append(
                {
                    "drawing_id": identifier,
                    "source_pcf": str(pcf_path),
                    "source_sha": str(sha_path),
                    "source_pcf_sha256": pcf_hash_before,
                    "source_sha_sha256": sha_hash_before,
                    "pcf_welds": len(numbered),
                    "sha_mapped_welds": len(mapped),
                    "rendered_callouts": len(callout_targets),
                    "rendered_f_welds": sum(row.get("weld_type") == "F" for row in callout_targets),
                    "rendered_s_welds": sum(row.get("weld_type") == "S" for row in callout_targets),
                    "unnumbered_pcf_welds": len(unnumbered),
                    "numbered_unmapped_pcf_welds": len(numbered_unmapped),
                    "topology_audit": str(
                        topology_audit_path.relative_to(temp_root)
                    ),
                    "topology_semantic_matching_ready": bool(
                        topology_summary["semantic_matching_ready"]
                    ),
                    "topology_uci_no_dynamic_record_count": int(
                        topology_summary[
                            "uci_no_dynamic_record_count"
                        ]
                    ),
                    "topology_uci_multiple_dynamic_record_count": int(
                        topology_summary[
                            "uci_multiple_dynamic_record_count"
                        ]
                    ),
                    "topology_adjacent_pipe_centerline_mismatch_weld_count": int(
                        topology_summary[
                            "adjacent_pipe_centerline_mismatch_weld_count"
                        ]
                    ),
                    "verified_callouts": int(qa["typed_marker_structurally_verified"]),
                    "core_verified_callouts": core_verified,
                    "geometry_clear_callouts": geometry_clear,
                    "text_clear_callouts": text_clear,
                    "source_text_overlap_callouts": int(qa["callout_count"]) - text_clear,
                    "frame_overflow_count": int(qa["frame_overflow_count"]),
                    "drawing_region_violation_count": int(qa["drawing_region_violation_count"]),
                    "leader_source_crossing_count": int(qa["leader_source_crossing_count"]),
                    "leader_leader_crossing_callout_count": int(qa["leader_leader_crossing_callout_count"]),
                    "callout_conflict_count": int(qa["callout_conflict_count"]),
                    "diamond_radius": selected_diamond_radius,
                    "injection_mode": injection_mode,
                    "physical_pages": len(physical_pages),
                    "pngs": png_rows,
                }
            )
            print(
                f"[{pair_index}/{len(pairs)}] {identifier}: "
                f"{len(physical_pages)}页，{len(callout_targets)}个焊口号"
            )

        manifest = {
            "input_root": str(input_root),
            "repository": str(repository),
            "repository_commit": "8e475178c9bce26b94530af221511aaec87ed53b",
            "render_rule": (
                "SHA geometry only; PCF supplies weld number, S/F type "
                "and UCI identity; native welds use the ellipse reference "
                "serialized immediately before each UCI record; F=square, "
                "S=circle; one straight leader"
            ),
            "pair_count": len(pairs),
            "png_count": total_png,
            "rendered_callout_count": total_callouts,
            "unnumbered_pcf_weld_count": total_unnumbered,
            "numbered_unmapped_pcf_weld_count": total_numbered_unmapped,
            "drawings": manifest_rows,
        }
        (temp_root / "生成清单.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if output_root.exists():
            shutil.rmtree(output_root)
        publish_directory(temp_root, output_root)
    except Exception:
        if temp_root.exists() and temp_root.parent == output_root.parent:
            shutil.rmtree(temp_root)
        raise

    print(
        json.dumps(
            {
                "输出目录": str(output_root),
                "管线": len(pairs),
                "PNG": total_png,
                "已标注焊口号": total_callouts,
                "PCF中未编号焊口": total_unnumbered,
                "PCF中已编号但无严格SHA焊点证据": total_numbered_unmapped,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)
