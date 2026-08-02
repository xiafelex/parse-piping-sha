#!/usr/bin/env python3
"""Build a page-local DXF topology graph for one multi-page ISO line.

DXF coordinates are sheet-local.  This program deliberately never tries to
translate page 001 onto page 002.  Instead it keeps each page's classified
pipe/component graph intact and adds explicit page-continuation *ports* and
only evidence-backed cross-page bridge candidates.  The resulting graph is
the DXF side of global IDF↔DXF matching; every pipe retains its source page
and source handle for later per-page rendering.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path


def line_page(name: str):
    match = re.search(r"941([A-Z0-9]+)S9412C.*PD0704-(\d+)-", name)
    if not match:
        raise ValueError(f"cannot obtain line/page from {name}")
    return match.group(1), int(match.group(2))


def point_distance(left, right):
    return math.dist(left, right)


def point_segment_distance(point, left, right):
    dx, dy = right[0] - left[0], right[1] - left[1]
    denom = dx * dx + dy * dy
    ratio = 0 if not denom else max(0, min(1, ((point[0] - left[0]) * dx + (point[1] - left[1]) * dy) / denom))
    return math.dist(point, (left[0] + ratio * dx, left[1] + ratio * dy))


def component_segments(component):
    paths = [component.get("outline", []), *component.get("welds", []), *component.get("subpaths", [])]
    result = [pair for path in paths for pair in zip(path, path[1:])]
    result.extend(component.get("strokes", []))
    return result


def load_jsonl(path: Path):
    result = {}
    if not path.exists():
        return result
    for raw in path.read_text().splitlines():
        if raw.strip():
            row = json.loads(raw)
            result[row["source"]] = row
    return result


def handle_id(pipe):
    return "+".join(pipe.get("handles", [])) or "unhandled"


def derived_endpoints(pipe):
    """Use classified endpoints, or the outer pair of a transparent arrow group."""
    endpoints = pipe.get("endpoints", [])
    if len(endpoints) == 2:
        return endpoints, "classified"
    raw = [point for segment in pipe.get("source_vector_segments", [])
           for point in segment.get("endpoints", [])]
    if len(raw) < 2:
        return [], "unavailable"
    left, right = max(((left, right) for index, left in enumerate(raw)
                       for right in raw[index + 1:]), key=lambda pair: point_distance(*pair))
    return [left, right], "outer_source_vectors"


def endpoint_nodes(source, index, pipe):
    endpoints, endpoint_source = derived_endpoints(pipe)
    annotations = pipe.get("endpoint_annotations", [])
    result = []
    for end_index, point in enumerate(endpoints):
        annotation = annotations[end_index] if end_index < len(annotations) else {}
        result.append({
            "id": f"{source}:P{index:03d}:E{end_index}",
            "point": point,
            "role": {
                "support": bool(annotation.get("support")),
                "weld": bool(annotation.get("weld")),
                "empty": not bool(annotation.get("support")) and not bool(annotation.get("weld")),
            },
            "endpoint_source": endpoint_source,
        })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--line", required=True, help="line key, e.g. CWR200001")
    parser.add_argument("--topology-dir", type=Path, required=True)
    parser.add_argument("--continuations", type=Path, required=True)
    parser.add_argument("--terminal-candidates", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint-tolerance", type=float, default=.15)
    args = parser.parse_args()

    pages = []
    for path in sorted(args.topology_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            # A concurrent batch refresh writes atomically in current tools;
            # tolerate a stale pre-atomic/auxiliary JSON rather than treating
            # it as a drawing page.
            continue
        if not payload.get("dxf"):
            continue
        try:
            key, page = line_page(payload["dxf"])
        except ValueError:
            continue
        if key == args.line:
            pages.append((page, payload["dxf"], payload))
    if not pages:
        raise SystemExit(f"no semantic topology for {args.line}")
    pages.sort()

    continuations = load_jsonl(args.continuations)
    terminals = []
    if args.terminal_candidates and args.terminal_candidates.exists():
        terminals = json.loads(args.terminal_candidates.read_text())

    pipe_nodes, component_nodes, endpoint_nodes_out, graph_edges = [], [], [], []
    local_endpoints = defaultdict(list)
    page_summary = []
    source_to_page = {source: page for page, source, _data in pages}
    for page, source, data in pages:
        page_pipes = 0
        for index, pipe in enumerate(data.get("pipes", [])):
            pipe_id = f"{source}:P{index:03d}"
            node = {
                "id": pipe_id, "page": page, "source": source,
                "kind": pipe["kind"], "handles": pipe.get("handles", []),
                "has_vector_endpoints": len(derived_endpoints(pipe)[0]) == 2,
                "endpoint_source": derived_endpoints(pipe)[1],
                "endpoint_annotations": pipe.get("endpoint_annotations", []),
                "endpoints": derived_endpoints(pipe)[0],
            }
            pipe_nodes.append(node); page_pipes += 1
            ends = endpoint_nodes(source, index, pipe)
            endpoint_nodes_out.extend(ends)
            for endpoint in ends:
                graph_edges.append({"kind": "pipe_endpoint", "from": pipe_id, "to": endpoint["id"]})
                local_endpoints[source].append((pipe_id, endpoint))
        for index, component in enumerate(data.get("components", [])):
            component_nodes.append({
                "id": f"{source}:C{index:03d}", "page": page, "source": source,
                "kind": component["kind"], "handles": component.get("handles", []),
                "centre": component.get("centre"),
            })
        page_summary.append({"page": page, "source": source, "pipe_count": page_pipes,
                             "component_count": len(data.get("components", []))})

    # These are raw, sheet-local endpoint coincidences only.  They are useful
    # for support segmentation and never cross from one page coordinate system
    # to another.
    for source, endpoints in local_endpoints.items():
        for left_index, (left_pipe, left) in enumerate(endpoints):
            for right_pipe, right in endpoints[left_index + 1:]:
                if left_pipe == right_pipe or point_distance(left["point"], right["point"]) > args.endpoint_tolerance:
                    continue
                graph_edges.append({
                    "kind": "in_page_exact_endpoint", "from": left["id"], "to": right["id"],
                    "distance": round(point_distance(left["point"], right["point"]), 5),
                    "shared_role": {"left": left["role"], "right": right["role"]},
                })

    # Contact is still calculated only inside its source page.  It captures
    # endpoint-to-confirmed-component topology (including elbow boundary
    # welds) while avoiding any inference from dimensions or annotation text.
    components_by_source = defaultdict(list)
    for component in component_nodes:
        # Recover the source component body in order to keep its vector paths.
        source_data = next(data for _page, source, data in pages if source == component["source"])
        index = int(component["id"].rsplit("C", 1)[1])
        components_by_source[component["source"]].append((component, source_data["components"][index]))
    for source, endpoints in local_endpoints.items():
        for pipe_id, endpoint in endpoints:
            for component, body in components_by_source[source]:
                segments = component_segments(body)
                if not segments:
                    continue
                distance = min(point_segment_distance(endpoint["point"], left, right) for left, right in segments)
                if distance <= 1.1:
                    graph_edges.append({"kind": "endpoint_component_contact", "from": endpoint["id"],
                                        "to": component["id"], "distance": round(distance, 5),
                                        "component_kind": component["kind"]})

    continuation_ports, continuation_edges = [], []
    for page, source, _data in pages:
        for link_index, link in enumerate(continuations.get(source, {}).get("links", [])):
            target_page = link.get("page")
            if target_page not in {entry[0] for entry in pages}:
                continue
            port = {"id": f"{source}:X{link_index:02d}", "source": source, "page": page,
                    "mode": link.get("mode"), "target_page": target_page,
                    "text_point": link.get("point"), "evidence": link.get("evidence")}
            continuation_ports.append(port)
            continuation_edges.append({"kind": "page_continuation", "from": port["id"],
                                       "to_page": target_page, "mode": port["mode"],
                                       "evidence": port["evidence"]})

    # Terminal candidates were generated from existing semantic evidence.  A
    # bridge is intentionally labelled candidate, never a merged pipe: a page
    # may show the same physical segment twice near a continuation boundary.
    bridges = []
    source_handles = defaultdict(list)
    for node in pipe_nodes:
        for handle in node["handles"]:
            source_handles[(node["source"], handle)].append(node["id"])
    for row in terminals:
        if row.get("line") != args.line:
            continue
        left_source, right_source = row["left_source"], row["right_source"]
        left_ids = [node for handle in row["left"].get("handles", []) for node in source_handles[(left_source, handle)]]
        right_ids = [node for handle in row["right"].get("handles", []) for node in source_handles[(right_source, handle)]]
        bridges.append({
            "kind": "cross_page_terminal_candidate", "from_page": row["from_page"], "to_page": row["to_page"],
            "left_pipe_ids": sorted(set(left_ids)), "right_pipe_ids": sorted(set(right_ids)),
            "semantic_kind": row["left"].get("kind"), "grade": row.get("grade"),
            "policy": "candidate only; preserve both source fragments until global topology confirms duplicate or continuation",
        })

    result = {
        "algorithm": "MULTIPAGE_DXF_GLOBAL_GRAPH_V1",
        "line_key": args.line,
        "coordinate_policy": "sheet-local coordinates only; no cross-page affine overlay",
        "page_summary": page_summary,
        "pipes": pipe_nodes,
        "components": component_nodes,
        "endpoint_nodes": endpoint_nodes_out,
        "in_page_edges": [edge for edge in graph_edges if edge["kind"] != "pipe_endpoint"],
        "continuation_ports": continuation_ports,
        "continuation_edges": continuation_edges,
        "cross_page_bridge_candidates": bridges,
        "limitations": [
            "continuation text alone does not identify a physical pipe endpoint",
            "a bridge candidate is not deduplicated or mapped to an IDF 100 until global topology evidence agrees",
            "typed arrows without retained vector endpoints are transparent semantic evidence, not graph endpoints",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"line_key": args.line, "pages": len(pages), "pipes": len(pipe_nodes),
                      "components": len(component_nodes), "in_page_endpoint_edges": len(result["in_page_edges"]),
                      "continuation_ports": len(continuation_ports), "cross_page_bridge_candidates": len(bridges)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
