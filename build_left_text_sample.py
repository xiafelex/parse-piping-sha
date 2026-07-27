#!/usr/bin/env python3
"""Select paired SHA/PCF drawings for a bounded left-text correction trial."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from analyze_left_text_offsets import SHEET_UNIT, is_free_text_anchor_candidate


TARGETS = ("INSUL", "CLASS", "TRACE")


def paired_pcf(sha_path: Path, drawing: str) -> Path:
    return sha_path.with_name(re.sub(r"-0$", "-pcf", drawing) + ".pcf")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path("output/left_text_ten_sample/selection.json"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    choices = []
    for drawing in manifest["drawings"]:
        sha_path = Path(drawing["sha"])
        pcf_path = paired_pcf(sha_path, drawing["drawing"])
        if not pcf_path.exists():
            continue
        counts: Counter[str] = Counter()
        candidates: Counter[str] = Counter()
        pages = []
        for page in drawing["pages"]:
            trace = json.loads(Path(page["trace"]).read_text(encoding="utf-8"))
            view_x, _, view_width, _ = trace["coordinate_system"]["visible_sheet_viewbox"]
            cutoff = view_x + view_width * 0.55
            page_counts: Counter[str] = Counter()
            page_candidates: Counter[str] = Counter()
            for record in trace["text"]:
                if record["sheet_anchor_normalized"][0] * SHEET_UNIT >= cutoff:
                    continue
                target = next((name for name in TARGETS if record["text"].upper().startswith(name)), None)
                if target is None:
                    continue
                counts[target] += 1
                page_counts[target] += 1
                if is_free_text_anchor_candidate(record, cutoff):
                    candidates[target] += 1
                    page_candidates[target] += 1
            if page_counts:
                pages.append(
                    {
                        "sheet_stream": page["sheet_stream"],
                        "logical_page": page["logical_page"],
                        "target_counts": dict(page_counts),
                        "eligible_anchor_trial_counts": dict(page_candidates),
                    }
                )
        if not counts:
            continue
        # Coverage takes precedence over raw count so the small trial includes
        # all three text families where available.
        score = sum(counts.values()) + 50 * len(counts)
        choices.append(
            (
                score,
                {
                    "drawing": drawing["drawing"],
                    "sha": str(sha_path),
                    "pcf": str(pcf_path),
                    "pdf": drawing["pdf"],
                    "physical_sheet_count": drawing["physical_sheet_count"],
                    "target_counts": dict(counts),
                    "eligible_anchor_trial_counts": dict(candidates),
                    "pages_with_targets": pages,
                },
            )
        )
    selected = [item for _, item in sorted(choices, key=lambda row: (-row[0], row[1]["drawing"]))[: args.limit]]
    result = {
        "scope": "Ten-pair left ISO text correction trial. PCF confirms pairing only; SHA remains the geometry source.",
        "targets": list(TARGETS),
        "selected_drawings": selected,
        "total_target_counts": dict(sum((Counter(item["target_counts"]) for item in selected), Counter())),
        "total_eligible_anchor_trial_counts": dict(
            sum((Counter(item["eligible_anchor_trial_counts"]) for item in selected), Counter())
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected": len(selected), **result["total_target_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
