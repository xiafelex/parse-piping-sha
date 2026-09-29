#!/usr/bin/env python3
"""只读诊断 200850 0A 三部分 errorpcf 的组件与清理前后差异。"""

from __future__ import annotations

import importlib.util
import math
import re
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from _bootstrap import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR


PROJECT = DATA_DIR
ROOT = PROJECT / "PCF_2026.09.02_2_重复归属清理" / "200850 0A"
ORIGINAL = PROJECT / "PCF_2026.09.02_2" / "200850 0A"
BASE_SCRIPT = PROJECT / "repair_pcf_2026_09_02_2_200300_0B_three_parts.py"

LOG_LINES = {
    "20-42-0850-26-MUA-8621-01.pcf": [70],
    "20-42-0850-26-MUA-8621-03.pcf": [514, 823],
    "20-42-0850-26-MUA-8729-03.pcf": [537],
    "20-42-0850-26-SLL-8384-10.pcf": [56, 274],
    "20-42-0850-26-VFG-8631-03.pcf": [155, 171],
    "20-42-0850-26-VFG-8731-03.pcf": [142, 275],
    "20-42-0850-26-WW-8883-03.pcf": [643],
}

QUERY_POINTS = {
    "MUA8621_gap_a": (101100.383, 51670.722, 6584.150),
    "MUA8621_gap_b": (101100.383, 48372.666, 6584.150),
    "SLL8384_cap_main": (128450.000, 29439.642, 6391.400),
    "SLL8384_olet_branch": (128534.591, 29355.051, 6466.400),
    "SLL8965_pipe_a": (105230.950, 36300.000, 4613.450),
    "SLL8965_pipe_b": (105119.050, 36300.000, 4613.450),
    "WR8804_branch_a": (143224.775, 44000.000, 7345.525),
    "WR8804_branch_b": (145250.000, 30950.000, 6349.954),
}


def load_base():
    spec = importlib.util.spec_from_file_location("pcf_base_200300", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {BASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


B = load_base()


def discover() -> list[tuple[str, str, Path]]:
    rows = []
    for part in ("1", "2", "3"):
        error_root = ROOT / part / "errorpcf"
        for category in sorted(p for p in error_root.iterdir() if p.is_dir() and p.name != "修复文件夹"):
            for path in sorted(category.glob("*.pcf")):
                rows.append((part, category.name, path))
    return rows


def read(path: Path):
    raw = path.read_bytes()
    try:
        raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raw.decode("gb18030", "strict")
    lines = raw.splitlines(keepends=True)
    blocks, materials_index = B.parse_blocks(lines)
    return raw, lines, blocks, materials_index


def vals(lines, block, key: bytes) -> list[bytes]:
    return B.values(lines, block, key)


def pts(lines, block) -> list[tuple[float, float, float]]:
    result = []
    for key in (b"END-POINT", b"BRANCH1-POINT", b"BRANCH2-POINT", b"BRANCH3-POINT", b"CO-ORDS"):
        for value in vals(lines, block, key):
            result.append(B.point(value))
    return result


def endpoints(lines, block) -> list[tuple[float, float, float]]:
    result = []
    for key in (b"END-POINT", b"BRANCH1-POINT", b"BRANCH2-POINT", b"BRANCH3-POINT"):
        result.extend(B.point(value) for value in vals(lines, block, key))
    if block.kind == "TEE-STUB":
        result.extend(B.point(value) for value in vals(lines, block, b"CENTRE-POINT"))
    if block.kind in {"OLET", "CAP"} and vals(lines, block, b"TAP-CONNECTION"):
        result.extend(B.point(value) for value in vals(lines, block, b"CO-ORDS"))
    return result


def block_text(lines, block) -> bytes:
    return b"".join(lines[block.start:block.end])


def normalize_block(lines, block) -> bytes:
    data = block_text(lines, block)
    data = re.sub(br"(?im)^\s*COMPONENT-IDENTIFIER\s+\S+\s*$", b"", data)
    data = re.sub(br"(?im)^\s*MASTER-COMPONENT-IDENTIFIER\s+\S+\s*$", b"", data)
    return re.sub(br"\s+", b" ", data).strip().upper()


def summary(lines, block) -> str:
    points = endpoints(lines, block)
    return (
        f"line={block.line} kind={block.kind} cid={block.cid or '-'} master={block.master or '-'} "
        f"uci={block.uci or '-'} skey={block.skey or '-'} mid={block.material_id or '-'} "
        f"exclude={block.excluded} points={points}"
    )


def component_at_line(blocks, line: int):
    matches = [block for block in blocks if block.start + 1 <= line <= block.end]
    return matches[0] if len(matches) == 1 else None


def diff_original(clean_path: Path, clean_lines, clean_blocks) -> None:
    original_path = ORIGINAL / clean_path.name
    if not original_path.is_file():
        print("  原始页缺失")
        return
    _, old_lines, old_blocks, _ = read(original_path)
    print("  原始页几何分量", [len(group) for group in topology_components(old_lines, old_blocks)])
    clean_by_uci = defaultdict(list)
    old_by_uci = defaultdict(list)
    for block in clean_blocks:
        if block.uci:
            clean_by_uci[block.uci].append(block)
    for block in old_blocks:
        if block.uci:
            old_by_uci[block.uci].append(block)
    changed = []
    for uci in sorted(set(old_by_uci) | set(clean_by_uci)):
        old_sigs = Counter(normalize_block(old_lines, block) for block in old_by_uci[uci])
        new_sigs = Counter(normalize_block(clean_lines, block) for block in clean_by_uci[uci])
        if old_sigs != new_sigs:
            changed.append((uci, old_by_uci[uci], clean_by_uci[uci]))
    print(f"  清理前后变化UCI组={len(changed)}")
    for uci, old_group, new_group in changed:
        print(f"    UCI {uci}: old={len(old_group)} new={len(new_group)}")
        for block in old_group:
            print("      old", summary(old_lines, block))
        for block in new_group:
            print("      new", summary(clean_lines, block))


def point_on_segment(p, a, b, tolerance=1e-3) -> bool:
    ab = tuple(b[i] - a[i] for i in range(3))
    ap = tuple(p[i] - a[i] for i in range(3))
    length2 = sum(v * v for v in ab)
    if length2 == 0:
        return math.dist(p, a) <= tolerance
    t = sum(ap[i] * ab[i] for i in range(3)) / length2
    q = tuple(a[i] + t * ab[i] for i in range(3))
    return -1e-9 <= t <= 1 + 1e-9 and math.dist(p, q) <= tolerance


def topology_components(lines, blocks):
    ignored = {"SUPPORT", "BOLT", "FLOW-ARROW", "END-POSITION-CLOSED"}
    usable = [b for b in blocks if b.kind not in ignored and endpoints(lines, b)]
    graph = {id(block): set() for block in usable}
    for i, left in enumerate(usable):
        lp = endpoints(lines, left)
        for right in usable[i + 1:]:
            rp = endpoints(lines, right)
            hit = bool(set(lp) & set(rp))
            if not hit:
                for p in lp:
                    for q1, q2 in zip(rp, rp[1:]):
                        if point_on_segment(p, q1, q2):
                            hit = True
                            break
                    if hit:
                        break
            if not hit:
                for p in rp:
                    for q1, q2 in zip(lp, lp[1:]):
                        if point_on_segment(p, q1, q2):
                            hit = True
                            break
                    if hit:
                        break
            if hit:
                graph[id(left)].add(id(right))
                graph[id(right)].add(id(left))
    by_id = {id(block): block for block in usable}
    unseen = set(graph)
    groups = []
    while unseen:
        first = unseen.pop()
        queue = deque([first])
        ids = {first}
        while queue:
            cur = queue.popleft()
            for nxt in graph[cur]:
                if nxt in unseen:
                    unseen.remove(nxt)
                    ids.add(nxt)
                    queue.append(nxt)
        groups.append([by_id[item] for item in ids])
    return sorted(groups, key=lambda group: (-len(group), min(block.line for block in group)))


def main() -> None:
    rows = discover()
    print("文件数", len(rows), "分类", dict(Counter(category for _, category, _ in rows)))
    for part, category, path in rows:
        raw, lines, blocks, _ = read(path)
        print(f"\n===== 第{part}部分 {category} / {path.name} lines={len(lines)} blocks={len(blocks)} =====")
        for line in LOG_LINES.get(path.name, []):
            block = component_at_line(blocks, line)
            print("  日志", line, summary(lines, block) if block else "未命中")
        if "不连通" in category or "错位" in category:
            groups = topology_components(lines, blocks)
            print("  几何分量", [len(group) for group in groups])
            for index, group in enumerate(groups, 1):
                print(f"    分量{index}: lines={min(b.line for b in group)}..{max(b.line for b in group)}")
                for block in sorted(group, key=lambda b: b.line)[:8]:
                    print("      ", summary(lines, block))
            diff_original(path, lines, blocks)

    print("\n===== 全单元点位命中（清理后根目录） =====")
    files = sorted(path for part in ("1", "2", "3") for path in (ROOT / part).glob("*.pcf"))
    parsed = []
    for path in files:
        _, lines, blocks, _ = read(path)
        parsed.append((path, lines, blocks))
    for label, query in QUERY_POINTS.items():
        print(label, query)
        for path, lines, blocks in parsed:
            for block in blocks:
                if block.kind not in {"PIPE", "PIPE-FIXED", "BEND", "ELBOW", "TEE", "OLET", "TEE-STUB"}:
                    continue
                block_points = endpoints(lines, block)
                hit = query in block_points
                if not hit and block.kind in {"PIPE", "PIPE-FIXED"} and len(block_points) == 2:
                    hit = point_on_segment(query, block_points[0], block_points[1])
                if hit:
                    print("  ", path.name, summary(lines, block))


if __name__ == "__main__":
    main()
