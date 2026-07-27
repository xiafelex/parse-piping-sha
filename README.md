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
The collaborative samples, SHA-derived artifacts, and Chinese research history
are in [`research/README.md`](research/README.md) and
[`docs/RESEARCH_JOURNEY_CN.md`](docs/RESEARCH_JOURNEY_CN.md).

## 2026-07 reconstruction handoff

The plain-language Chinese project review, including the SHA architecture,
PCF/IDF/SHA boundaries, staged render examples, current quality coverage, weld
writeback experiments, and the follow-up roadmap, is available at
[`docs/管道SHA图纸还原过程总结.pdf`](docs/管道SHA图纸还原过程总结.pdf).

The accompanying evidence ledger is
[`SHA_PDF_FIDELITY_AUDIT.md`](SHA_PDF_FIDELITY_AUDIT.md). It records the strict
rule that PDF is visual QA only, never a geometry or text source.

### PCF weld writeback experiment

The experimental weld workflow keeps PCF as the engineering-number source and
uses SHA only for verified paper-space placement:

1. `number_pcf_welds.py`: assigns or maps PCF weld identifiers to SHA UCI/dot evidence.
2. `plan_pcf_weld_lanes.py`: plans lane side and ordering from PCF straight-pipe topology.
3. `inject_sha_weld_callouts.py`: writes diamonds, labels, and leaders into a SHA copy.
4. `verify_sha_weld_callouts.py`: checks that every leader starts at its source SHA point and that every diamond closes.
5. `render_sha_matched_pcf_folder.py`: renders SHA-derived pages for matched PCF/SHA pairs.

`render_pcf_weld_iso.py` is intentionally a PCF-only diagnostic renderer. It is
not evidence of SHA-compatible output and must not be presented as the SHA
writeback result.

### Rebuilding the process PDF

`generate_sha_reconstruction_story.mjs` creates the Chinese review PDF from
the project-local `output/` examples. It needs Node.js plus Playwright and the
same generated image paths used in the original analysis workspace. The final
PDF is versioned in `docs/` so another machine can read the current result even
without the original samples.

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

## macOS application package

For a self-contained Apple Silicon (`arm64`) application, build the embedded
Python engine and the Electron package from a Mac with Python 3 and Node.js:

```bash
python3 -m pip install pyinstaller
cd apps/desktop
npm install
npm run dist:mac
```

The resulting `.app`, `.dmg`, and `.zip` are written to `apps/desktop/dist/`.
The app embeds the ISO parsing engine; it does not require Python after
installation. Because this internal build is not Apple-notarized, macOS may
require **Control-click -> Open** the first time. Imported projects and their
immutable source copies are stored in the app's Application Support directory.
If the engine cannot start, see `engine-startup.log` in that same directory.

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
