#!/usr/bin/env python3
"""Summarize SHA-only render traces and rank pages for PDF visual QA.

The report intentionally derives every metric from SHA render trace JSON. PDF
files are not opened by this program. A reviewer can use the resulting ranking
with the separately generated PDF thumbnails to classify visual differences.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def trace_metrics(path: Path) -> dict[str, int]:
    trace = json.loads(path.read_text(encoding="utf-8"))
    return {
        "segments": len(trace.get("segments", [])),
        "text": len(trace.get("text", [])),
        "template_segments": int(trace.get("sha_template_geometry", {}).get("line_count", 0)),
        "template_images": len(trace.get("sha_template_images", [])),
        "template_text": int(trace.get("sha_template_text_count", 0)),
        "template_revision": int(trace.get("sha_template_revision_count", 0)),
        "connection_points": int(trace.get("sha_connection_point_count", 0)),
        "marker_boxes": int(trace.get("inferred_marker_box_count", 0)),
        "composite_arcs": sum(1 for segment in trace.get("segments", []) if segment.get("source") == "sha-composite-arc"),
    }


def median_absolute_deviation(values: list[int]) -> tuple[float, float]:
    centre = statistics.median(values)
    return centre, statistics.median(abs(value - centre) for value in values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="audit_manifest.json from audit_sha_pdf_corpus.py")
    parser.add_argument("--output", type=Path, help="Default: corpus_quality_summary.json beside manifest")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pages: list[dict[str, Any]] = []
    issues: Counter[str] = Counter()
    for drawing in manifest.get("drawings", []):
        if drawing.get("page_count_matches_pdf") is False:
            issues["SHA physical Sheet count differs from PDF page count"] += 1
        for page in drawing.get("pages", []):
            trace_path = Path(page["trace"])
            if not trace_path.is_file():
                issues["missing SHA trace artifact"] += 1
                continue
            metrics = trace_metrics(trace_path)
            pages.append({
                "drawing": drawing["drawing"],
                "sheet_stream": page["sheet_stream"],
                "qa_page_index": page["qa_page_index"],
                "logical_page": page.get("logical_page"),
                "page_order_basis": drawing["page_order_basis"],
                "metrics": metrics,
                "sha_svg": page["sha_svg"],
                "pdf_png": page.get("pdf_png"),
            })

    segment_centre, segment_mad = median_absolute_deviation([page["metrics"]["segments"] for page in pages])
    text_centre, text_mad = median_absolute_deviation([page["metrics"]["text"] for page in pages])
    dense_threshold = segment_centre + 4 * max(segment_mad, 1)
    sparse_text_threshold = max(0, text_centre - 4 * max(text_mad, 1))

    ranked: list[dict[str, Any]] = []
    for page in pages:
        metrics = page["metrics"]
        flags: list[str] = []
        if page["page_order_basis"] != "unique SHA title-block logical page numbers":
            flags.append("page order fallback")
        if metrics["template_segments"] < 30:
            flags.append("template geometry unusually sparse")
        if metrics["template_images"] < 1:
            flags.append("template image missing")
        if metrics["segments"] > dense_threshold:
            flags.append("unusually dense vector layer")
        if metrics["text"] < sparse_text_threshold:
            flags.append("unusually sparse text layer")
        for flag in flags:
            issues[flag] += 1
        ranked.append({**page, "flags": flags, "priority": len(flags)})

    ranked.sort(key=lambda page: (-int(page["priority"]), -int(page["metrics"]["segments"]), page["drawing"], page["qa_page_index"]))
    report = {
        "scope": "SHA trace metrics only; PDF is not opened or parsed",
        "summary": {
            "pages_with_trace": len(pages),
            "segment_median": segment_centre,
            "segment_mad": segment_mad,
            "dense_vector_threshold": dense_threshold,
            "text_median": text_centre,
            "text_mad": text_mad,
            "sparse_text_threshold": sparse_text_threshold,
            "candidate_issue_frequency": dict(issues.most_common()),
        },
        "pages_ranked_for_visual_qa": ranked,
    }
    output = args.output or args.manifest.with_name("corpus_quality_summary.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {output}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
