#!/usr/bin/env python3
"""Render a bounded SHA-only left-text anchor trial from a selected sample list."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sha_to_svg_prototype import render


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path, help="selection.json from build_left_text_sample.py")
    parser.add_argument("--out-dir", type=Path, default=Path("output/left_text_ten_sample/trial"))
    args = parser.parse_args()

    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    prefixes = tuple(value.upper() for value in selection["targets"])
    pages = []
    for drawing in selection["selected_drawings"]:
        drawing_dir = args.out_dir / drawing["drawing"]
        drawing_dir.mkdir(parents=True, exist_ok=True)
        for page in drawing["pages_with_targets"]:
            sheet_stream = page["sheet_stream"]
            svg = drawing_dir / f"{sheet_stream}.svg"
            trace = drawing_dir / f"{sheet_stream}.trace.json"
            render(
                Path(drawing["sha"]),
                svg,
                wanted_page=1,
                debug_boxes=False,
                manifest_path=trace,
                component_layer=None,
                sheet_stream=sheet_stream,
                anchor_left_free_text=True,
                anchor_left_free_text_prefixes=prefixes,
            )
            rendered = json.loads(trace.read_text(encoding="utf-8"))
            changed = [
                record
                for record in rendered["text"]
                if record["position_mapping"] == "sha-left-free-anchor-plus-psm-size"
            ]
            pages.append(
                {
                    "drawing": drawing["drawing"],
                    "sha": drawing["sha"],
                    "pcf": drawing["pcf"],
                    "sheet_stream": sheet_stream,
                    "svg": str(svg.resolve()),
                    "trace": str(trace.resolve()),
                    "changed_text_count": len(changed),
                    "changed_texts": [record["text"] for record in changed],
                }
            )
    result = {
        "scope": "SHA-only trial. Only INSUL/CLASS/TRACE prefixes are eligible; PCF is pairing evidence only.",
        "prefixes": prefixes,
        "pages": pages,
        "changed_text_total": sum(page["changed_text_count"] for page in pages),
    }
    report = args.out_dir / "trial_manifest.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pages": len(pages), "changed_text_total": result["changed_text_total"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
