#!/usr/bin/env python3
"""Recover ISO page and same-line split structure from a SHA file only.

This is intentionally SHA-only: it reports paper-space marker locations, but never
claims they are engineering E/N/EL coordinates.  PCF is needed to map a split marker
back to the plant model coordinate system.
"""

from __future__ import annotations

import argparse
import re
import struct
from collections import defaultdict
from pathlib import Path

import olefile


def text_objects(data: bytes) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    for match in re.finditer(rb"(?:(?:[\x20-\x7e]\x00){1,})", data):
        if match.end() + 16 > len(data):
            continue
        x, y = struct.unpack_from("<dd", data, match.end())
        if not (-1 < x < 2 and -1 < y < 2):
            continue
        objects.append(
            {
                "text": match.group().decode("utf-16le"),
                "offset": match.start(),
                "x": x,
                "y": y,
            }
        )
    return objects


def logical_page(objects: list[dict[str, object]]) -> tuple[int, int] | None:
    """Read the title-block `SHEET n OF total` digits by their stable paper position."""

    digits = [
        obj
        for obj in objects
        if str(obj["text"]).isdigit()
        and 0.745 < float(obj["x"]) < 0.780
        and 0.0 < float(obj["y"]) < 0.015
    ]
    totals = [
        obj
        for obj in objects
        if str(obj["text"]).isdigit()
        and 0.765 < float(obj["x"]) < 0.790
        and 0.0 < float(obj["y"]) < 0.015
    ]
    if not digits or not totals:
        return None
    return int(str(digits[0]["text"])), int(str(totals[0]["text"]))


def inspect(sha_path: Path) -> str:
    with olefile.OleFileIO(sha_path) as ole:
        pages: list[dict[str, object]] = []
        for parts in ole.listdir(streams=True, storages=False):
            name = "/".join(parts)
            if not re.fullmatch(r"Sheet\d+", name):
                continue
            data = ole.openstream(parts).read()
            if len(data) <= 1024:
                continue
            objects = text_objects(data)
            page_number = logical_page(objects)
            if page_number is None:
                continue
            pipeline = next(
                (
                    str(obj["text"]).strip("*")
                    for obj in objects
                    if str(obj["text"]).startswith("*")
                    and "-" in str(obj["text"])
                ),
                "",
            )
            labels = [
                obj
                for obj in objects
                if "SHT" in str(obj["text"]).upper()
                and (not pipeline or pipeline in str(obj["text"]))
            ]
            pages.append(
                {
                    "stream": name,
                    "number": page_number[0],
                    "total": page_number[1],
                    "pipeline": pipeline,
                    "labels": labels,
                }
            )

    pages.sort(key=lambda page: int(page["number"]))
    page_numbers = {int(page["number"]) for page in pages}
    edges: dict[tuple[int, int], list[tuple[dict[str, object], dict[str, object]]]] = defaultdict(list)
    lines = [f"SHA: {sha_path.name}"]
    if not pages:
        return "\n".join(lines + ["No ISO page streams were recognized."]) + "\n"

    lines.append(f"ISO pages: {len(pages)}")
    for page in pages:
        lines.append(
            f"  Page {page['number']} of {page['total']}: {page['stream']} "
            f"pipeline={page['pipeline'] or '(not found)'}"
        )
        for label in page["labels"]:
            target = re.search(r"\bSHT\s+(\d+)\b", str(label["text"]), re.I)
            if not target:
                continue
            target_page = int(target.group(1))
            lines.append(
                f"    -> SHT {target_page}: {label['text']!r} "
                f"paper=({float(label['x']):.4f}, {float(label['y']):.4f})"
            )
            if target_page in page_numbers and target_page != int(page["number"]):
                edge = tuple(sorted((int(page["number"]), target_page)))
                edges[edge].append((page, label))

    lines.append(f"Same-line split interfaces: {len(edges)}")
    for left, right in sorted(edges):
        lines.append(f"  Sheet {left} <-> Sheet {right}")
        for page, label in edges[(left, right)]:
            lines.append(
                f"    Sheet {page['number']} marker at "
                f"paper=({float(label['x']):.4f}, {float(label['y']):.4f})"
            )

    lines.extend(
        [
            "Engineering split coordinates: not recoverable from SHA alone.",
            "The paper coordinates above are drawing-layout positions, not E/N/EL values.",
            "Provide PCF to map the paired SHA graphic objects to the shared engineering endpoint.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect(args.sha)
    if args.output:
        args.output.write_text(result)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
