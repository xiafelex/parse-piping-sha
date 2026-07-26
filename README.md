# parse-piping-sha

SHA-only tooling for inspecting and reconstructing piping isometric drawings
produced by Shape2D, PDMS, or Smart 3D.

The project preserves a strict evidence rule: PDF files may be used for visual
quality assurance, but never as a source of coordinates, geometry, text, or
placement in reconstructed output.

## Quick Start

```bash
python3 -m pip install -r requirements.txt
python3 run_sha_iso_render.py /path/to/drawing.sha --page 1 --out-dir output/sha_svg
```

Add `--png` when Node.js and Playwright are installed. See
[`SHA_ISO_AI_HANDOFF.md`](SHA_ISO_AI_HANDOFF.md) for the full workflow and the
prompt to give another AI. [`SKILL.md`](SKILL.md) is the reusable Codex skill.

## Tools

- `sha_to_svg_prototype.py`: SHA-only SVG renderer plus JSON trace manifest.
- `analyze_iso_split.py`: UCI/graphic/page split analysis.
- `analyze_sha_pages.py`: page and same-line split inspection.
- `analyze_psm_hierarchy.py`: validated PSM hierarchy evidence extractor.
- `run_sha_iso_render.py`: convenience wrapper for SVG, trace JSON, and PNG.

## Status

The renderer decodes observed line, composite, arc, ellipse, text, template,
and selected PSM layout records. `PSMspacemap/0x00008000` has a validated
hierarchy-node parser; relation-code semantics and several other PSM streams
remain intentionally unresolved pending cross-sample verification.
