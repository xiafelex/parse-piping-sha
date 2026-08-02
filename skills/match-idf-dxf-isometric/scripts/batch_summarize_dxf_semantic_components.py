#!/usr/bin/env python3
"""Refresh semantic-topology JSON for a DXF directory in one adapter process."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import ezdxf

from summarize_dxf_semantic_components import export_semantics, load_adapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dxf_root", type=Path)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--glob", default="*.dxf")
    args = parser.parse_args()
    adapter = load_adapter(args.adapter)
    paths = sorted(args.dxf_root.glob(args.glob))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for path in paths:
        if hasattr(adapter, "SOURCE"):
            adapter.SOURCE = path
        payload = export_semantics(ezdxf.readfile(path), adapter, path)
        target = args.output_dir / f"{path.stem}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        temporary.replace(target)
        results.append({"dxf": path.name, "pipe_count": payload["pipe_count"],
                        "component_counts": payload["component_counts"]})
    print(json.dumps({"page_count": len(results), "pages": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
