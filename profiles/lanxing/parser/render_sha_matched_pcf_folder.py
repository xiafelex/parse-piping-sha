#!/usr/bin/env python3
"""Render SHA-derived ISO pages for PCF/SHA pairs found below one folder.

The PCF is used only to locate and validate its matching drawing.  Geometry,
layout, title blocks, symbols, and every rendered page primitive come from the
matching SHA.  A PCF without an exact sibling ``<drawing>-0.sha`` is rejected;
this command deliberately has no PCF-only rendering fallback.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from analyze_iso_split import read_sha_streams


ROOT = Path(__file__).resolve().parent
SHEET_NAME = re.compile(r"Sheet\d+")


def drawing_id(path: Path) -> str:
    """Return the shared drawing ID for ``*-pcf.pcf`` or ``*-0.sha``."""
    if path.suffix.lower() == ".pcf" and path.stem.endswith("-pcf"):
        return path.stem[:-4]
    if path.suffix.lower() == ".sha" and path.stem.endswith("-0"):
        return path.stem[:-2]
    raise ValueError(f"Unsupported drawing filename: {path.name}")


def physical_sheets(sha_path: Path) -> list[str]:
    return sorted(
        name
        for name, stream in read_sha_streams(sha_path).items()
        if SHEET_NAME.fullmatch(name) and name != "Sheet221" and len(stream) > 1024
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Root folder containing paired PCF and SHA files.")
    parser.add_argument("--out-dir", type=Path, default=Path("output/sha_matched_iso"))
    args = parser.parse_args()

    folder = args.folder.resolve()
    if not folder.is_dir():
        parser.error(f"Folder not found: {folder}")
    pcfs = sorted(folder.rglob("*-pcf.pcf"))
    if not pcfs:
        parser.error(f"No *-pcf.pcf files found below {folder}")

    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    matches: list[dict[str, object]] = []
    failures: list[str] = []

    for pcf_path in pcfs:
        identifier = drawing_id(pcf_path)
        sha_path = pcf_path.with_name(f"{identifier}-0.sha")
        if not sha_path.is_file():
            failures.append(f"{pcf_path}: missing exact sibling {sha_path.name}")
            continue
        sheets = physical_sheets(sha_path)
        if not sheets:
            failures.append(f"{sha_path}: no populated physical Sheet streams")
            continue
        iso_output = output / identifier
        command = [
            sys.executable,
            str(ROOT / "run_sha_iso_render.py"),
            str(sha_path),
            "--all-sheets",
            "--png",
            "--out-dir",
            str(iso_output),
        ]
        subprocess.run(command, check=True)
        matches.append({
            "drawing_id": identifier,
            "pcf": str(pcf_path),
            "sha": str(sha_path),
            "physical_sheets": sheets,
            "page_count": len(sheets),
            "render_source": "SHA only; PCF is pairing validation only",
        })

    manifest = output / "sha_pcf_pair_manifest.json"
    manifest.write_text(json.dumps({"matches": matches, "failures": failures}, indent=2), encoding="utf-8")
    print(f"Pair manifest: {manifest}")
    print(f"Rendered SHA pairs: {len(matches)}")
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
