#!/usr/bin/env python3
"""Inspect PCF/SHA ISO page splits without changing either source file.

The SHA format is a Shape2D OLE container.  This script uses the observed relation
between its dynamic-attribute records, PSM graphic IDs, and Sheet streams to identify
which graphical subobjects are stored on which ISO page.
"""

from __future__ import annotations

import argparse
import re
import struct
from collections import defaultdict
from pathlib import Path

import olefile


TOP_LEVEL = re.compile(r"^[A-Z][A-Z-]+$")
POINT_LINE = re.compile(
    r"(?:END-POINT|CENTRE-POINT|BRANCH1-POINT)\s+"
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)"
)


def read_sha_streams(sha_path: Path) -> dict[str, bytes]:
    with olefile.OleFileIO(sha_path) as ole:
        return {
            "/".join(parts): ole.openstream(parts).read()
            for parts in ole.listdir(streams=True, storages=False)
        }


def pcf_components(pcf_path: Path) -> tuple[str, list[dict[str, object]]]:
    lines = pcf_path.read_text(errors="replace").splitlines()
    pipeline = ""
    blocks: list[tuple[int, int, str]] = []
    starts = [
        (index, line)
        for index, line in enumerate(lines)
        if TOP_LEVEL.fullmatch(line) and line != "MATERIALS"
    ]

    for index, line in enumerate(lines):
        if line.startswith("PIPELINE-REFERENCE"):
            pipeline = line.split(maxsplit=1)[1].strip()
            break

    for index, (start, kind) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        blocks.append((start, end, kind))

    components: list[dict[str, object]] = []
    for start, end, kind in blocks:
        block = "\n".join(lines[start:end])
        identifier = re.search(r"COMPONENT-IDENTIFIER\s+(\d+)", block)
        uci = re.search(r"UCI\s+(\{[^}]+\})", block)
        attr6 = re.search(r"COMPONENT-ATTRIBUTE6\s+(\S+)", block)
        if not identifier or not uci:
            continue
        points = [tuple(map(float, values)) for values in POINT_LINE.findall(block)]
        components.append(
            {
                "id": int(identifier.group(1)),
                "kind": kind,
                "uci": uci.group(1),
                "line_ref": attr6.group(1) if attr6 else "",
                "continuation": "CONTINUATION" in block,
                "points": points,
            }
        )
    return pipeline, components


def dynamic_graphics(data: bytes) -> dict[str, list[dict[str, int]]]:
    """Return UCI -> internal SHA graphic references.

    The final 20/8 bytes of these records are stable across the supplied samples.
    They are internal references, not PCF engineering coordinates.
    """

    # The five bytes immediately before this text are part of the serialized record.
    # Starting at the text itself would shift both internal references by five bytes.
    marker = b"PipeLine Info\x00"
    starts = [match.start() - 5 for match in re.finditer(re.escape(marker), data)]
    output: dict[str, list[dict[str, int]]] = defaultdict(list)

    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(data)
        record = data[start:end]
        # Dynamic records can contain arbitrary binary braces.  Only a standard
        # GUID is a PCF UCI and safe to decode as text.
        uci = re.search(rb"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}", record)
        if not uci or len(record) < 20:
            continue
        output[uci.group().decode()].append(
            {
                "record_offset": start,
                "space_ref": struct.unpack_from("<I", record, len(record) - 20)[0],
                "graphic_ref": struct.unpack_from("<I", record, len(record) - 8)[0],
            }
        )
    return output


def sheet_text_objects(data: bytes) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    for match in re.finditer(rb"(?:(?:[\x20-\x7e]\x00){4,})", data):
        text = match.group().decode("utf-16le")
        if "SHT" not in text.upper() and "SEE ISO" not in text.upper():
            continue
        if match.end() + 16 > len(data):
            continue
        x, y = struct.unpack_from("<dd", data, match.end())
        if not (-1 < x < 2 and -1 < y < 2):
            continue
        objects.append({"text": text, "offset": match.start(), "x": x, "y": y})
    return objects


def pages_for_graphic(graphic_ref: int, sheets: dict[str, bytes]) -> list[str]:
    needle = struct.pack("<I", graphic_ref)
    return [name for name, data in sheets.items() if needle in data]


def point_key(point: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(round(value, 3) for value in point)


def report(pcf_path: Path, sha_path: Path, component_id: int | None = None) -> str:
    streams = read_sha_streams(sha_path)
    sheets = {
        name: data
        for name, data in streams.items()
        if re.fullmatch(r"Sheet\d+", name) and len(data) > 1024
    }
    dynamic = dynamic_graphics(streams.get("Unclustered Dynamic Attributes", b""))
    pipeline, components = pcf_components(pcf_path)

    if component_id is not None:
        selected = next(
            (component for component in components if int(component["id"]) == component_id),
            None,
        )
        if selected is None:
            raise ValueError(f"PCF component #{component_id} was not found")
        refs = dynamic.get(str(selected["uci"]), [])
        lines = [
            f"Component #{selected['id']} {selected['kind']}",
            f"UCI: {selected['uci']}",
            f"PCF points: {selected['points']}",
            f"PCF continuation: {selected['continuation']}",
            "",
            "SHA graphic migration manifest:",
        ]
        psm = streams.get("PSMcluster0", b"")
        for ref in refs:
            graphic_ref = int(ref["graphic_ref"])
            needle = struct.pack("<I", graphic_ref)
            page_hits = {
                name: [match.start() for match in re.finditer(re.escape(needle), data)]
                for name, data in sheets.items()
            }
            page_hits = {name: offsets for name, offsets in page_hits.items() if offsets}
            psm_offsets = [match.start() for match in re.finditer(re.escape(needle), psm)]
            lines.append(
                f"  attr={ref['record_offset']} space=0x{int(ref['space_ref']):04X} "
                f"graphic=0x{graphic_ref:04X} pages={page_hits} psm={psm_offsets}"
            )
        return "\n".join(lines) + "\n"

    component_pages: dict[int, set[str]] = {}
    component_primary_page: dict[int, str] = {}
    component_graphics: dict[int, list[tuple[int, list[str]]]] = {}
    for component in components:
        refs = dynamic.get(str(component["uci"]), [])
        graphics = [
            (int(ref["graphic_ref"]), pages_for_graphic(int(ref["graphic_ref"]), sheets))
            for ref in refs
        ]
        component_graphics[int(component["id"])] = graphics
        component_pages[int(component["id"])] = {
            page for _, pages in graphics for page in pages
        }
        counts: dict[str, int] = defaultdict(int)
        for _, pages in graphics:
            for page in pages:
                counts[page] += 1
        if counts:
            component_primary_page[int(component["id"])] = max(
                counts, key=lambda page: (counts[page], page)
            )

    lines = [
        f"Pipeline: {pipeline}",
        f"SHA page streams: {', '.join(sorted(sheets)) or '(none found)'}",
        "",
        "Continuation/SHT labels:",
    ]
    for name in sorted(sheets):
        for obj in sheet_text_objects(sheets[name]):
            lines.append(
                f"  {name}: {obj['text']!r} at {obj['offset']} "
                f"paper=({obj['x']:.4f}, {obj['y']:.4f})"
            )

    lines.extend(["", "PCF component page membership:"])
    for component in sorted(components, key=lambda item: int(item["id"])):
        pages = sorted(component_pages[int(component["id"])])
        if not pages:
            continue
        marker = " external-continuation" if component["continuation"] else ""
        lines.append(
            f"  #{component['id']} {component['kind']} pages={pages}{marker}"
        )
        for graphic_ref, graphic_pages in component_graphics[int(component["id"])]:
            lines.append(f"    graphic 0x{graphic_ref:04X} -> {graphic_pages}")

    by_point: dict[tuple[float, float, float], list[dict[str, object]]] = defaultdict(list)
    for component in components:
        if component["continuation"]:
            continue
        for point in component["points"]:
            by_point[point_key(point)].append(component)

    lines.extend(["", "Candidate same-line page interfaces:"])
    candidates = 0
    for point, linked in sorted(by_point.items()):
        ids = {int(component["id"]) for component in linked}
        if len(ids) < 2:
            continue
        primary_pages = {
            component_primary_page[component_id]
            for component_id in ids
            if component_id in component_primary_page
        }
        if len(primary_pages) < 2:
            continue
        candidates += 1
        detail = ", ".join(
            f"#{component['id']} {component['kind']}="
            f"primary:{component_primary_page.get(int(component['id']), '-')} "
            f"all:{sorted(component_pages[int(component['id'])])}"
            for component in linked
        )
        lines.append(f"  {point}: {detail}")
    if not candidates:
        lines.append("  (no cross-page component interface found)")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcf", type=Path)
    parser.add_argument("sha", type=Path)
    parser.add_argument(
        "--component",
        type=int,
        help="Print the SHA object/index manifest for one PCF component identifier",
    )
    parser.add_argument("--output", type=Path, help="Write report to this path instead of stdout")
    args = parser.parse_args()
    result = report(args.pcf, args.sha, args.component)
    if args.output:
        args.output.write_text(result)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
