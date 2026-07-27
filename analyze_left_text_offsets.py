#!/usr/bin/env python3
"""Inventory SHA-only text-placement evidence for the left ISO drawing area.

The paired PDF is intentionally not read.  This tool classifies the renderer's
existing SHA trace so a visual QA pass can decide which recurring offsets are
safe to promote into a rendering rule.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SHEET_UNIT = 16800


def text_category(text: str) -> str:
    if re.fullmatch(r"(?:\d+(?:\.\d+)?|\d+X\d+NPD|\d+NPD)", text):
        return "尺寸/管径数值"
    if re.fullmatch(r"(?:[FGBST]\d+(?:-[A-Z0-9]+)?)(?:\s+[FGBST]\d+(?:-[A-Z0-9]+)?)*|T\d+-\d+", text):
        return "构件方框代码"
    if re.fullmatch(r"(?:E|N|EL)\s*[+\-]?\d+(?:\.\d+)?", text):
        return "坐标"
    if text.startswith(("SEE ISO", "CONN TO", "CONT TO", "NOT FOUND")):
        return "跨页/连接注释"
    if "DIMENSION" in text.upper() or "SLOPE" in text.upper():
        return "尺寸说明"
    if text.startswith(("STEM", "ORIENT", "FLOW", "GRID")):
        return "方向/工艺注释"
    return "其他工程注释"


def text_family(text: str) -> str:
    """Retain layout-relevant punctuation while grouping changing identifiers."""
    return re.sub(r"\d+", "#", text)


def median_mad(values: list[float]) -> tuple[float, float]:
    centre = statistics.median(values)
    return centre, statistics.median(abs(value - centre) for value in values)


def is_free_text_anchor_candidate(record: dict[str, Any], left_cutoff: float) -> bool:
    """Match the renderer's deliberately narrow free-annotation experiment."""
    text = record["text"]
    anchor_x, anchor_y = record["sheet_anchor_normalized"]
    anchor_x *= SHEET_UNIT
    left, bottom, right, top = record["psm_bbox_page_units"]
    height, width = top - bottom, right - left
    direction_x, direction_y = record["direction"]
    if record["position_mapping"] != "sha-psm-envelope" or anchor_x >= left_cutoff:
        return False
    if record["ellipse_anchor_adjustment_page_units"] is not None or record["source_frame_page_units"] is not None:
        return False
    if abs(direction_y) > 0.1 or abs(direction_x - 1) > 0.1 or record["inferred_marker_box"]:
        return False
    if re.fullmatch(r"(?:PS-N\d+-\d+|PANDA\d+-\d+-\d+)", text):
        return False
    if re.fullmatch(r'SD\d+(?:\s+\d+(?:/\d+)?\")?', text) or re.fullmatch(r"\d{1,3}", text):
        return False
    if re.fullmatch(r"CI\d+", text) or text == "N":
        return False
    if height > 320 or width > height * max(2, len(text)) * 2.2:
        return False
    return -260 <= left - anchor_x <= -40 and -280 <= bottom - anchor_y * SHEET_UNIT <= -50


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="audit_manifest.json generated from SHA-only rendering")
    parser.add_argument("--json-out", type=Path, default=Path("output/left_text_offset_summary.json"))
    parser.add_argument("--markdown-out", type=Path, default=Path("output/LEFT_TEXT_OFFSET_AUDIT_CN.md"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    mapping_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    category_mapping_counts: Counter[tuple[str, str]] = Counter()
    candidate_groups: dict[tuple[str, str, str], list[tuple[float, float]]] = defaultdict(list)
    unresolved: Counter[str] = Counter()
    total = 0

    for drawing in manifest["drawings"]:
        for page in drawing["pages"]:
            trace = json.loads(Path(page["trace"]).read_text(encoding="utf-8"))
            view_x, _, view_width, _ = trace["coordinate_system"]["visible_sheet_viewbox"]
            left_cutoff = view_x + view_width * 0.55
            for record in trace["text"]:
                anchor_x, anchor_y = record["sheet_anchor_normalized"]
                anchor_x *= SHEET_UNIT
                anchor_y *= SHEET_UNIT
                if anchor_x >= left_cutoff:
                    continue

                total += 1
                mapping = record["position_mapping"]
                kind = text_category(record["text"])
                mapping_counts[mapping] += 1
                category_counts[kind] += 1
                category_mapping_counts[(kind, mapping)] += 1
                left, bottom, right, top = record["psm_bbox_page_units"]
                height, width = top - bottom, right - left

                if mapping != "sha-psm-envelope":
                    continue
                if height > 320 or width > height * max(2, len(record["text"])) * 2.2:
                    unresolved["页面级/复合 PSM 容器，不能用统一文字偏移处理"] += 1
                    continue
                dx, dy = left - anchor_x, bottom - anchor_y
                if is_free_text_anchor_candidate(record, left_cutoff):
                    key = (record["style_ref"], kind, text_family(record["text"]))
                    candidate_groups[key].append((dx, dy))
                else:
                    unresolved["受构件框、椭圆、旋转或文字类型保护，不套用自由文字偏移"] += 1

    stable: list[dict[str, Any]] = []
    candidate_total = 0
    for (style, kind, family), offsets in candidate_groups.items():
        dx, dx_mad = median_mad([item[0] for item in offsets])
        dy, dy_mad = median_mad([item[1] for item in offsets])
        count = len(offsets)
        candidate_total += count
        if count >= 20 and dx_mad <= 50 and dy_mad <= 55:
            stable.append(
                {
                    "style_ref": style,
                    "category": kind,
                    "text_family": family,
                    "count": count,
                    "median_offset_page_units": [round(dx, 1), round(dy, 1)],
                    "mad_page_units": [round(dx_mad, 1), round(dy_mad, 1)],
                }
            )
        else:
            unresolved["候选偏移存在，但样本量或离散度不足以自动推广"] += count
    stable.sort(key=lambda item: (-item["count"], item["style_ref"], item["text_family"]))

    result = {
        "scope": "Left ISO drawing region only; no PDF input was read.",
        "left_text_total": total,
        "mapping_counts": dict(mapping_counts.most_common()),
        "category_counts": dict(category_counts.most_common()),
        "category_mapping_counts": [
            {"category": kind, "mapping": mapping, "count": count}
            for (kind, mapping), count in category_mapping_counts.most_common()
        ],
        "direct_psm_anchor_band_candidates": candidate_total,
        "stable_candidate_groups": stable,
        "stable_candidate_texts": sum(item["count"] for item in stable),
        "unresolved_counts": dict(unresolved.most_common()),
        "interpretation": (
            "A stable offset is SHA evidence for a candidate coordinate-space relation, not proof that the Sheet anchor "
            "is the final paper position. Promote it only after PDF visual QA, while keeping PDF out of all geometry inputs."
        ),
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 左侧单线图文字偏移审计（SHA-only）",
        "",
        "PDF 不参与本统计、不提供坐标或文字；只允许在后续人工目视验收时使用。",
        "",
        "## 总览",
        "",
        f"- 左侧文字总数：`{total:,}`。",
        f"- 可在原始 PSM 字框与 Sheet 锚点间观察到候选稳定偏移的文字：`{candidate_total:,}`。",
        f"- 达到样本量和离散度阈值、可进入 PDF 目视验证的稳定文字族：`{len(stable)}` 组 / `{sum(item['count'] for item in stable):,}` 个文字。",
        "- 稳定偏移只表示 SHA 中两种坐标关系反复出现，不代表可对所有文字直接套用。",
        "",
        "## 当前定位方式",
        "",
    ]
    lines.extend(f"- `{mapping}`：`{count:,}`" for mapping, count in mapping_counts.most_common())
    lines.extend(["", "## 文字类别", ""])
    lines.extend(f"- {kind}：`{count:,}`" for kind, count in category_counts.most_common())
    lines.extend(["", "## 可验证的稳定候选（前 30 组）", ""])
    for item in stable[:30]:
        lines.append(
            f"- `{item['style_ref']}` / {item['category']} / `{item['text_family']}`："
            f"`{item['count']}` 个，PSM 相对 Sheet 锚点中位差 `({item['median_offset_page_units'][0]}, "
            f"{item['median_offset_page_units'][1]})`，MAD `({item['mad_page_units'][0]}, {item['mad_page_units'][1]})`。"
        )
    lines.extend(["", "## 暂不自动修正", ""])
    lines.extend(f"- {reason}：`{count:,}`" for reason, count in unresolved.most_common())
    args.markdown_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"left_text_total": total, "stable_groups": len(stable)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
