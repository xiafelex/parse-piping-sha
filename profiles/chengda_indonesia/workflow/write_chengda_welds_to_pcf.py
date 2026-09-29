#!/usr/bin/env python3
"""Write Chengda weld-table identifiers and S/F data to a PCF copy."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR

from openpyxl import Workbook


PROJECT_DIR = DATA_DIR
WORKSPACE_DIR = SHARED_DIR
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

import write_weld_numbers_from_excel as legacy
from chengda_weld_table import WeldInput, read_weld_inputs
from pcf_weld_augmentation import augment_pcf_tree, validate_augmented_tree


@dataclass(frozen=True)
class WritebackSummary:
    total: int
    written: int
    unmatched: int
    ambiguous: int
    created_endpoint_welds: int
    split_pipe_welds: int
    output_files: tuple[Path, ...]


def legacy_record(record: WeldInput) -> legacy.ExcelWeld:
    return legacy.ExcelWeld(
        row_number=record.excel_row,
        unit="",
        pipeline=record.sheet_id,
        weld_number=record.weld_number,
        segment_number="",
        weld_point_type=record.weld_type,
        coordinate_text=",".join(str(value) for value in record.coordinate),
        coordinate=record.coordinate,
    )


def _status_group(status: str) -> str:
    if status in {"已写入", "编号已一致"}:
        return "written"
    if status in {"PCF候选不唯一", "Excel记录冲突", "现有编号冲突"}:
        return "ambiguous"
    return "unmatched"


def _write_json_audit(path: Path, records: list[WeldInput], results: list[legacy.AuditResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for record, result in zip(records, results):
        selected = result.selected.weld if result.selected is not None else None
        rows.append(
            {
                "excel_row": record.excel_row,
                "sheet_id": record.sheet_id,
                "weld_number": record.weld_number,
                "weld_type": record.weld_type,
                "coordinate": [str(value) for value in record.coordinate],
                "status": _status_group(result.status),
                "detail_status": result.status,
                "reason": result.reason,
                "pcf": selected.relative_path.as_posix() if selected is not None else "",
                "weld_uci": selected.uci if selected is not None else "",
            }
        )
    payload = {"total": len(rows), "counts": dict(Counter(row["status"] for row in rows)), "rows": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_excel_audit(path: Path, records: list[WeldInput], results: list[legacy.AuditResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "PCF反写明细"
    worksheet.append(
        ["Excel行", "单页图号", "焊点编号", "焊点类型", "焊点坐标", "结果", "详细状态", "原因", "PCF", "WELD UCI"]
    )
    for record, result in zip(records, results):
        selected = result.selected.weld if result.selected is not None else None
        worksheet.append(
            [
                record.excel_row,
                record.sheet_id,
                record.weld_number,
                record.weld_type,
                ",".join(str(value) for value in record.coordinate),
                _status_group(result.status),
                result.status,
                result.reason,
                selected.relative_path.as_posix() if selected is not None else "",
                selected.uci if selected is not None else "",
            ]
        )
    workbook.save(path)


def writeback_tree(
    excel_path: Path,
    input_dir: Path,
    output_dir: Path,
    audit_dir: Path,
    number_column: str = "焊点编号",
    tolerance_mm: Decimal = Decimal("0.03"),
    overwrite_output: bool = True,
) -> WritebackSummary:
    excel_path = Path(excel_path).resolve()
    input_dir = Path(input_dir).resolve()
    output_dir = Path(output_dir).resolve()
    audit_dir = Path(audit_dir).resolve()
    records = read_weld_inputs(excel_path, number_column)
    legacy_records = [legacy_record(record) for record in records]
    source_hashes = {path.relative_to(input_dir): legacy.sha256_bytes(path.read_bytes()) for path in legacy.pcf_files(input_dir)}

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output_dir.name}.augment-", dir=output_dir.parent) as raw:
        augmented = Path(raw)
        staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.result-", dir=output_dir.parent))
        shutil.copytree(input_dir, augmented, dirs_exist_ok=True)
        decisions, augmentation_stats = augment_pcf_tree(
            legacy_records,
            input_dir,
            augmented,
            tolerance_mm,
        )
        validate_augmented_tree(input_dir, augmented, decisions)
        shutil.copytree(augmented, staging, dirs_exist_ok=True)
        all_welds: list[legacy.PcfWeld] = []
        encodings: dict[Path, str] = {}
        for path in legacy.pcf_files(augmented):
            parsed, encoding = legacy.parse_pcf_file(path, augmented)
            all_welds.extend(parsed)
            encodings[path.relative_to(augmented)] = encoding
        results, modifications, _ = legacy.match_records(
            legacy_records,
            all_welds,
            tolerance_mm,
            overwrite_existing=False,
        )
        legacy.write_modified_pcfs(augmented, staging, modifications, encodings)
        legacy.validate_copy(augmented, staging, modifications)
        _write_json_audit(audit_dir / "PCF反写明细.json", records, results)
        _write_excel_audit(audit_dir / "PCF反写明细.xlsx", records, results)
        try:
            legacy.publish_output_directory(staging, output_dir, overwrite_output)
        finally:
            if staging.exists() and staging.parent == output_dir.parent:
                shutil.rmtree(staging)

    after_hashes = {path.relative_to(input_dir): legacy.sha256_bytes(path.read_bytes()) for path in legacy.pcf_files(input_dir)}
    if after_hashes != source_hashes:
        raise RuntimeError("源PCF在反写过程中发生变化")
    groups = Counter(_status_group(result.status) for result in results)
    return WritebackSummary(
        total=len(records),
        written=groups["written"],
        unmatched=groups["unmatched"],
        ambiguous=groups["ambiguous"],
        created_endpoint_welds=int(augmentation_stats["created_endpoint_welds"]),
        split_pipe_welds=int(augmentation_stats["created_pipe_split_welds"]),
        output_files=tuple(legacy.pcf_files(output_dir)),
    )


def parse_args() -> argparse.Namespace:
    base = PROJECT_DIR / "SHA文件转换"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel", type=Path, default=base / "焊口表-000200FW 0-壁厚材质处理-带特性.xlsx")
    parser.add_argument("--input-dir", type=Path, default=base / "000200FW 0-PCF")
    parser.add_argument("--output-dir", type=Path, default=base / "000200FW 0-PCF-焊口反写测试")
    parser.add_argument("--audit-dir", type=Path, default=base / "焊口反写与标注审计")
    parser.add_argument("--number-column", default="焊点编号")
    parser.add_argument("--tolerance-mm", type=Decimal, default=Decimal("0.03"))
    parser.add_argument("--overwrite-output", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = writeback_tree(
        args.excel,
        args.input_dir,
        args.output_dir,
        args.audit_dir,
        number_column=args.number_column,
        tolerance_mm=args.tolerance_mm,
        overwrite_output=args.overwrite_output,
    )
    payload = {
        "total": summary.total,
        "written": summary.written,
        "unmatched": summary.unmatched,
        "ambiguous": summary.ambiguous,
        "created_endpoint_welds": summary.created_endpoint_welds,
        "split_pipe_welds": summary.split_pipe_welds,
        "output_file_count": len(summary.output_files),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
