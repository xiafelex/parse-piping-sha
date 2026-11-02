#!/usr/bin/env python3
"""Parse the UAE project's isogen-neutral1 fixed-position report, preserving blanks.

Usage: python parse_uae_isogen_neutral.py INPUT_DIRECTORY --out OUTPUT_DIRECTORY
Use --xlsx for an Excel workbook (requires openpyxl). CSV/JSON need only stdlib.
This is a sample-verified project layout, NOT a universal Isogen layout. Exact
field payload widths and wholly blank reserved columns need the report config.
No input files are changed. Nonzero exit means an error or an unverified file;
partial diagnostic outputs are still written, with an explicit status per row.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


SCHEMA_ID = "uae-isogen-neutral1-observed-v1"
SOURCE = "https://hexagonppm.fluidtopics.net/r/en-US/Isogen-Isometric-Drawing-Options-Reference/Version-15/554787"
# Inclusive character slice boundaries, including padding, not guaranteed max
# business-field widths. Final support field is open-ended; never truncate it.
FIELDS = [
    ("record_type", "记录类别", 1, 6, "已确认", "属性31；COMP管件/阀门、PIPE管子、GASK垫片、BOLT螺栓、SUPP支架、INST仪表、ENGR特殊件"),
    ("project_code", "项目侧编码_待确认", 7, 15, "业务含义待确认", "属性31；样本608200；不能等同图框PROJECT NO. 30201_50150_LT_HE"),
    ("area_code", "区域或工作包编码_待确认", 16, 21, "业务含义待确认", "属性31；样本H4-P；仅可确认原始编码，区域/工作包为候选解释"),
    ("iso_suffix", "图号末段编码", 22, 25, "结构已确认/业务含义待确认", "属性31；PAA/PNC，与PIPELINE-REFERENCE末段一致；不擅自解释分类规则"),
    ("report_iso_id", "报表图号片段", 26, 46, "已确认", "属性31；例如213-0212-HCX-PAA；不是完整图号"),
    ("line_id", "工艺管线号", 47, 71, "已确认", "属性31；例如213-0212-HCX，对应PCF LINE-ID"),
    ("drawing_code", "图号中段编码", 72, 76, "结构已确认/业务含义待确认", "属性31；52，与完整图号第三段一致；不能据此定义专业代码"),
    ("unknown_code", "固定编码_待确认", 77, 90, "业务含义待确认", "属性31；999；没有依据判定为页码、区域或占位值"),
    ("revision", "版本号", 91, 116, "已确认", "PCF ATTRIBUTE51；样本0；与图纸版本栏一致"),
    ("commodity_code", "构件短码", 117, 122, "已确认", "属性32；FLG/PIP/RTEE/90E等；不是完整ITEM-CODE，也不是SKEY；允许空值"),
    ("piping_spec", "管道等级", 123, 138, "已确认", "属性33；对应构件PIPING-SPEC"),
    ("size_1", "主口径或螺栓直径", 139, 146, "已确认", "属性33；管件如4.0IN，螺栓如22.225MM，保留原单位"),
    ("size_2", "次口径或螺栓长度", 147, 154, "已确认", "属性33；三通/异径件为次口径；BOLT为材料报表长度mm；其它记录可空"),
    ("quantity_or_length", "数量或管长", 155, 180, "已确认", "属性33；PIPE为报表/下料长度mm，其它为计数；不可跨类别直接求和"),
    ("end_preparation", "端部形式代码", 181, 230, "已确认", "属性33；BE/RFFE等原码；BOLT/GASK/SUPP可空；代码扩展含义需项目字典"),
    ("support_code", "支架型号", 231, None, "已确认", "属性33；例如SW-300-100-4\"-C.S.；非支架记录为空"),
]
KNOWN_TYPES = {"COMP", "PIPE", "GASK", "BOLT", "SUPP", "INST", "ENGR"}
NUMBER = r"\d+(?:\.\d+)?"
SIZE = NUMBER + r"(?:IN|MM)"


def parse_line(raw: str) -> dict[str, str]:
    """Do not strip leading spaces, split whitespace, or guess missing columns."""
    if not raw.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise ValueError("本布局仅接受可打印ASCII和普通空格；TAB/非ASCII/控制符需另建布局")
    values = {key: raw[start - 1:end].strip() for key, _, start, end, _, _ in FIELDS}
    errors = []
    for key, _, start, end, _, _ in FIELDS:
        slot = raw[start - 1:end]
        if slot and slot.strip() and slot.startswith(" "):
            errors.append(f"{key}未从固定起始列{start}开始")
    if values["record_type"] not in KNOWN_TYPES:
        errors.append("未知记录类别")
    for key in ("project_code", "area_code", "iso_suffix", "report_iso_id", "line_id", "drawing_code", "unknown_code", "revision", "piping_spec"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", values[key]):
            errors.append(f"{key}为空或包含错位字符")
    if values["report_iso_id"] != values["line_id"] + "-" + values["iso_suffix"]:
        errors.append("图号片段与管线号/图号末段不一致")
    if not re.fullmatch(SIZE, values["size_1"]):
        errors.append("主规格格式错误")
    second = values["size_2"]
    if values["record_type"] == "BOLT":
        if not values["size_1"].endswith("MM") or not re.fullmatch(NUMBER, second):
            errors.append("BOLT需MM直径及数值长度")
    elif second and not re.fullmatch(SIZE, second):
        errors.append("次规格格式错误")
    if not re.fullmatch(NUMBER, values["quantity_or_length"]):
        errors.append("数量/长度格式错误或行已截断")
    for key in ("commodity_code", "end_preparation"):
        if values[key] and not re.fullmatch(r"[A-Za-z0-9_-]+", values[key]):
            errors.append(f"{key}格式错误")
    if values["record_type"] == "SUPP" and not values["support_code"]:
        errors.append("支架型号缺失")
    if values["record_type"] != "SUPP" and values["support_code"]:
        errors.append("非支架记录出现未识别尾部内容")
    if errors:
        raise ValueError("；".join(errors))
    return values


def pcf_evidence(path: Path) -> tuple[dict, dict[str, list[dict]], list[dict]]:
    """Build report candidates without selecting arbitrary UCI for equal rows.

    Project rules verified on four source files: skip CONTINUATION/EXCLUDE;
    merge PIPEs sharing a UCI only when their entire report rows agree.
    Non-pipe identical rows retain their multiplicity.
    """
    text = path.read_text(encoding="utf-8-sig")
    header, blocks, current = {}, [], None
    for line_no, line in enumerate(text.splitlines(), 1):
        if line == "MATERIALS":
            break
        if re.fullmatch(r"[A-Z][A-Z0-9-]*", line):
            current = {"type": line, "line": line_no, "attrs": {}, "flags": []}
            blocks.append(current)
        else:
            parts = line.strip().split(None, 1)
            if not parts:
                continue
            if current is None:
                header[parts[0]] = parts[1] if len(parts) > 1 else ""
            elif len(parts) == 1:
                current["flags"].append(parts[0])
            else:
                current["attrs"][parts[0]] = parts[1]
    if "ATTRIBUTE51" not in header or "PIPELINE-REFERENCE" not in header:
        raise ValueError("PCF缺少ATTRIBUTE51或PIPELINE-REFERENCE，不能重建本项目报表")
    groups, skipped, pipes = defaultdict(list), [], {}
    for block in blocks:
        a = block["attrs"]
        if "COMPONENT-ATTRIBUTE31" not in a:
            continue
        if "CONTINUATION" in block["flags"] or a.get("MATERIAL-LIST") == "EXCLUDE":
            skipped.append({"pcf_line": block["line"], "component_id": a.get("COMPONENT-IDENTIFIER"), "reason": "CONTINUATION/EXCLUDE"})
            continue
        segments = [(a["COMPONENT-ATTRIBUTE31"], 90), (header["ATTRIBUTE51"], 26), (a.get("COMPONENT-ATTRIBUTE32", ""), 6)]
        if any(len(value) > width for value, width in segments):
            raise ValueError(f"PCF行{block['line']}属性溢出已验证布局")
        row = ("".join(value.ljust(width) for value, width in segments) + a.get("COMPONENT-ATTRIBUTE33", "")).rstrip()
        values = parse_line(row)
        ref = {"component_id": a.get("COMPONENT-IDENTIFIER", ""), "uci": a.get("UCI", ""), "pcf_line": block["line"], "type": block["type"], "bolt_length": a.get("BOLT-LENGTH", ""), "cut_piece_length": a.get("CUT-PIECE-LENGTH", "")}
        warnings = []
        if a.get("PIPING-SPEC") and values["piping_spec"] != a["PIPING-SPEC"]:
            raise ValueError(f"PCF行{block['line']}属性33等级与PIPING-SPEC不一致")
        if block["type"] == "BOLT" and ref["bolt_length"] and float(ref["bolt_length"]) != float(values["size_2"]):
            warnings.append("报表螺栓长度不同于PCF BOLT-LENGTH，分别保留")
        uci = ref["uci"]
        if block["type"] == "PIPE" and uci and uci in pipes:
            old_row, old_candidate = pipes[uci]
            if old_row != row:
                raise ValueError(f"同一PIPE UCI的报表属性冲突：{uci}")
            old_candidate["components"].append(ref)
            skipped.append({"pcf_line": block["line"], "component_id": ref["component_id"], "reason": "相同UCI下料管段合并"})
            continue
        candidate = {"components": [ref], "warnings": warnings}
        groups[row].append(candidate)
        if block["type"] == "PIPE" and uci:
            pipes[uci] = (row, candidate)
    return header, dict(groups), skipped


def audit_file(path: Path) -> tuple[list[dict], dict]:
    data = path.read_bytes()
    lines = data.decode("utf-8-sig").splitlines()
    suffix = "-isogen-neutral1.txt"
    pcf = path.with_name(path.name[:-len(suffix)] + "-pcf.pcf")
    report = {"file": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest(), "pcf": str(pcf.resolve()), "errors": [], "unverified": [], "skipped_pcf_records": []}
    groups, header = {}, {}
    if pcf.exists():
        try:
            header, groups, report["skipped_pcf_records"] = pcf_evidence(pcf)
            report["pcf_sha256"] = hashlib.sha256(pcf.read_bytes()).hexdigest()
            if not groups:
                report["errors"].append("PCF没有可用于重建本布局的报表属性，不能核验TXT")
        except (ValueError, UnicodeError) as exc:
            report["errors"].append(f"PCF验证失败：{exc}")
    else:
        report["unverified"].append("缺少同名PCF，只有字段格式验证，不能保证与原始报表属性一致")
    rows, actual = [], Counter()
    for number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        actual[raw.rstrip()] += 1
        row = {"source_file": path.name, "source_line": number, "raw_line": raw, "status": "未验证", "error": "", "pcf_candidates": []}
        try:
            values = parse_line(raw)
            row.update(values)
            row["quantity_unit"] = "mm" if values["record_type"] == "PIPE" else "计数"
            row["size_2_meaning"] = "螺栓报表长度(mm)" if values["record_type"] == "BOLT" else "次口径" if values["size_2"] else ""
            candidates = groups.get(raw.rstrip(), [])
            row["pcf_candidates"] = candidates
            row["status"] = "PCF精确匹配" if candidates else "仅格式通过"
            if candidates:
                full_ref = header["PIPELINE-REFERENCE"]
                parts = full_ref.split("-")
                if len(parts) != 6 or values["drawing_code"] != parts[2] or values["report_iso_id"] != "-".join([parts[1], *parts[3:]]) or values["line_id"] != header.get("LINE-ID"):
                    raise ValueError("报表图号或LINE-ID与同名PCF头不一致")
            elif groups:
                raise ValueError("TXT原行无法与PCF重建行精确匹配，疑似错位/修改")
        except ValueError as exc:
            row["status"] = "错误"
            row["error"] = str(exc)
            report["errors"].append(f"TXT行{number}：{exc}")
        rows.append(row)
    if groups:
        expected = Counter({raw: len(candidates) for raw, candidates in groups.items()})
        report["missing_rows"] = list((expected - actual).elements())
        report["extra_rows"] = list((actual - expected).elements())
        if report["missing_rows"] or report["extra_rows"]:
            report["errors"].append("报表行及重复次数与PCF不一致（比较不依赖行序）")
    if not rows:
        report["errors"].append("没有有效数据行")
    report["row_count"] = len(rows)
    report["verified_rows"] = sum(row["status"] == "PCF精确匹配" for row in rows)
    report["status"] = "失败" if report["errors"] else "未验证" if report["unverified"] else "通过"
    return rows, report


def write_csv(path: Path, headers: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def export(out: Path, rows: list[dict], reports: list[dict], xlsx: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    headers = ["源文件", "源行号"] + [f[1] for f in FIELDS] + ["数量或长度单位", "第二规格含义", "核验状态", "错误说明", "PCF候选关联", "原始行"]
    table = [[row["source_file"], row["source_line"]] + [row.get(f[0], "") for f in FIELDS] + [row.get("quantity_unit", ""), row.get("size_2_meaning", ""), row["status"], row["error"], json.dumps(row["pcf_candidates"], ensure_ascii=False), row["raw_line"]] for row in rows]
    dict_headers = ["序号", "字段ID", "字段名称", "起始列(1基)", "切片结束列(含填充)", "确认程度", "含义与证据", "样本值", "空值行数"]
    dictionary = [[i, key, name, start, end or "行尾", confidence, note, " | ".join(sorted({r.get(key, "") for r in rows} - {""})), sum(not r.get(key) for r in rows)] for i, (key, name, start, end, confidence, note) in enumerate(FIELDS, 1)]
    file_headers = ["文件", "记录数", "精确匹配行数", "状态", "错误", "未验证原因"]
    checks = [[r["file"], r["row_count"], r["verified_rows"], r["status"], "；".join(r["errors"]), "；".join(r["unverified"])] for r in reports]
    write_csv(out / "TXT解析明细.csv", headers, table)
    write_csv(out / "字段字典.csv", dict_headers, dictionary)
    notes = [
        "本布局有16个可观察字段；普通行的次规格/支架编号为空，按空格split会丢列。",
        "区间包含右侧填充；例如91-116并非版本号业务长度26。原始行保留全部空白；全空区是否还有未启用字段需报表配置确认。",
        "608200、H4-P、999的业务名尚无证据；PAA/PNC和52的图号对应关系已确认，编码规则未确认。不要将候选名称视为正式业务定义。",
        "已检查PCF、POD可读属性、PDF文字、索引xlsm及项目内配置文件清单，未发现Neutral报表字段定义。需原始Neutral Report配置及属性31/32/33的生成规则确认未知项。",
        "ENGR在本批对应MISC-COMPONENT，SKEY=RP并有特殊件位号；不将ENGR泛化为所有项目的固定类型。",
        "PIPE数值为报表/下料长度mm；BOLT次规格为报表螺栓长度mm，保留与PCF BOLT-LENGTH的差异。",
        "BOLT数量为同条记录螺栓数量，其它非PIPE记录为件/项计数。相同行不去重；TXT没有UCI，多候选关联不能擅选唯一构件。",
        "PCF交叉核验：过滤CONTINUATION/EXCLUDE，只合并相同UCI且属性一致的PIPE；忽略行序，但严格比较每种原行的出现次数。这是当前样本验证规则。",
        "缺少PCF只算格式通过；TAB、未知类型、关键字段截断、非ASCII和不匹配原行会报告错误。未验证或错误时命令退出码为1。",
        "CSV保留空字段并正确引用含逗号/引号的内容；Excel所有文本单元格按文本保存，保留编码和原始数值精度。",
        "使用范围仅为本批项目布局；其它项目Neutral报表可自定义列，不可直接套用。官方格式参考：" + SOURCE,
    ]
    (out / "解析说明.txt").write_text("\n\n".join(notes), encoding="utf-8-sig")
    payload = {"schema": SCHEMA_ID, "fields": [dict(zip(("key", "name", "start", "end", "confidence", "evidence"), f)) for f in FIELDS], "notes": notes, "files": reports, "rows": rows}
    (out / "核验与原始证据.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if xlsx:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
        book = Workbook()
        book.remove(book.active)
        for name, cols, data in [("解析明细", headers, table), ("字段字典", dict_headers, dictionary), ("文件核验", file_headers, checks), ("使用说明", ["说明"], [[s] for s in notes])]:
            sheet = book.create_sheet(name)
            sheet.append(cols)
            for record in data:
                sheet.append(record)
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
                        cell.number_format = "@"
                    cell.alignment = Alignment(vertical="top")
            for cell in sheet[1]:
                cell.fill = PatternFill("solid", fgColor="24476A")
                cell.font = Font(color="FFFFFF", bold=True)
            sheet.freeze_panes = "C2" if name == "解析明细" else "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for i, col in enumerate(cols, 1):
                sheet.column_dimensions[get_column_letter(i)].width = min(55, max(15, len(col) * 2 + 2))
        book.save(out / "TXT精准解析.xlsx")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="TXT文件或目录；目录默认不递归")
    parser.add_argument("--out", type=Path, required=True, help="输出目录")
    parser.add_argument("--recursive", action="store_true", help="递归寻找*-isogen-neutral1.txt")
    parser.add_argument("--xlsx", action="store_true", help="另外输出Excel（需openpyxl）")
    args = parser.parse_args()
    if args.xlsx:
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            parser.error("--xlsx需要openpyxl；可去掉--xlsx仅输出CSV/JSON")
    suffix = "-isogen-neutral1.txt"
    files = ([args.input] if args.input.is_file() and args.input.name.endswith(suffix) else sorted(args.input.rglob('*' + suffix) if args.recursive else args.input.glob('*' + suffix)))
    if not files:
        parser.error("未找到*-isogen-neutral1.txt输入")
    rows, reports = [], []
    for path in files:
        try:
            parsed, report = audit_file(path)
        except (UnicodeError, OSError) as exc:
            parsed, report = [], {"file": str(path), "row_count": 0, "verified_rows": 0, "status": "失败", "errors": [str(exc)], "unverified": []}
        rows.extend(parsed)
        reports.append(report)
        print(f"{path.name}: {report['status']}, {report['row_count']}行, {report['verified_rows']}行精确匹配")
    export(args.out, rows, reports, args.xlsx)
    print(f"输出: {args.out.resolve()}")
    return int(any(r["status"] != "通过" for r in reports))


if __name__ == "__main__":
    raise SystemExit(main())
