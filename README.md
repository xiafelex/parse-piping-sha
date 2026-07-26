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

Use `--all-pages` to render every logical ISO Sheet in a multi-page SHA.

Add `--png` when Node.js and Playwright are installed. See
[`SHA_ISO_AI_HANDOFF.md`](SHA_ISO_AI_HANDOFF.md) for the full workflow and the
prompt to give another AI. [`SKILL.md`](SKILL.md) is the reusable Codex skill.
See [`APP_DEVELOPMENT_PLAN.md`](APP_DEVELOPMENT_PLAN.md) for the proposed
desktop product architecture, phases, data model, and acceptance criteria.

## First-stage local workspace

The repository now includes a local-first import and analysis workspace. It
creates a project directory, copies PCF/SHA source files as immutable originals,
records SHA-256 hashes, renders every SHA ISO page to SVG plus a trace JSON, and
shows PCF/SHA UCI coverage and candidate same-line split interfaces.

```bash
python3 app_server.py
```

Open `http://127.0.0.1:8765`, create a project, import `.pcf` and `.sha` files,
then select **运行分析**. Generated artifacts are written under `app_data/` and
can be relocated with `--data-dir`. PDFs are deliberately excluded from import
because they remain visual QA evidence only, never reconstruction input.

For a desktop window, install Electron once and start the thin local shell:

```bash
cd apps/desktop
npm install
npm start
```

The Electron shell starts the same `app_server.py` engine and does not send
source files to a remote service.

## Tools

- `sha_to_svg_prototype.py`: SHA-only SVG renderer plus JSON trace manifest.
- `analyze_iso_split.py`: UCI/graphic/page split analysis.
- `analyze_sha_pages.py`: page and same-line split inspection.
- `analyze_psm_hierarchy.py`: validated PSM hierarchy evidence extractor.
- `render_psm_hierarchy_overlay.py`: SHA-only visual diagnostic for candidate PSM node envelopes.
- `run_sha_iso_render.py`: convenience wrapper for SVG, trace JSON, and PNG.

## Status

The renderer decodes observed line, composite, arc, ellipse, text, template,
and selected PSM layout records. `PSMspacemap/0x00008000` has a validated
hierarchy-node parser; relation-code semantics and several other PSM streams
remain intentionally unresolved pending cross-sample verification.

## PSM Diagnostic Overlay

To inspect what the validated hierarchy records cover on a page, generate a
separate, explicitly non-semantic overlay:

```bash
python3 render_psm_hierarchy_overlay.py /path/to/drawing.sha --page 1 \
  --output output/psm-type2-candidates.svg --types 2 --png
```

The default shows only type-2 nodes with ids at least `0x500`, which keeps the
visual legible. Add `--types 2,3` for the broader aggregation-layer view. The
boxes are candidate matches only: a numeric node id is matched to a plausible
record-bounded `PSMcluster0` envelope. They do not yet identify a box as text, flange, weld,
or any other semantic drawing element.
