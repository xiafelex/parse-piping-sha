# SHA ISO AI Handoff

This package reconstructs piping ISO SVG output from a Shape2D/PDMS/Smart 3D
`.sha` file. It is deliberately SHA-only: a PDF may be used for visual QA but
must never provide geometry, text, coordinates, or placement values.

## Send These Files

- `sha_to_svg_prototype.py`
- `analyze_iso_split.py`
- `analyze_sha_pages.py`
- `analyze_psm_hierarchy.py`
- `run_sha_iso_render.py`
- `number_pcf_welds.py`
- `inject_sha_weld_callouts.py`
- `SHA_ISO_AI_HANDOFF.md`
- `parse-piping-sha/SKILL.md` from the installed Codex skill directory

The runtime needs Python 3.11+ and `olefile`:

```bash
python3 -m pip install olefile
```

PNG preview is optional and needs Node.js plus Playwright:

```bash
npm install playwright
npx playwright install chromium
```

## Run

```bash
python3 run_sha_iso_render.py /path/to/drawing.sha --page 1 --out-dir output/sha_svg --png
```

It creates:

- `*.svg`: SHA-derived vector reconstruction.
- `*.trace.json`: source stream, UCI candidates, PSM envelopes, anchors, and mapping method for audit.
- `*.svg.png`: optional preview.

For a documented SHA-derived component vector layer from the *same* SHA:

```bash
python3 run_sha_iso_render.py /path/to/drawing.sha --page 1 \
  --component-layer /path/to/same-sha-uci-component.svg --png
```

Do not provide a PDF or a component SVG from another line/drawing to this
argument.

To render every populated logical ISO page in a multi-sheet SHA:

```bash
python3 run_sha_iso_render.py /path/to/drawing.sha --all-pages --out-dir output/sha_svg --png
```

When a delivery folder contains both PCF and SHA files, use the paired-folder
entry point rather than a PCF renderer:

```bash
python3 render_sha_matched_pcf_folder.py /path/to/folder --out-dir output/sha_matched_iso
```

It requires an exact sibling pairing of `<drawing>-pcf.pcf` and
`<drawing>-0.sha`. The PCF validates the business-file relationship only; all
ISO pages, coordinates, symbols, text, dimensions, and templates are read from
the SHA. Missing SHA files are reported as errors and are never rendered from
PCF geometry.

## Experimental SHA Writeback

The repository now includes an experimental SHA writeback path for weld-style
diamond callouts. It works by:

1. finding micro UCI dot symbols that are already present in the SHA;
2. filtering them by visible page geometry only;
3. computing a nearby offset diamond, text, and leader;
4. appending new Sheet primitives and writing the expanded stream back into the
   OLE/CFB container.

Example:

```bash
python3 inject_sha_weld_callouts.py /path/to/drawing.sha \
  --output output/annotated.sha
```

Or for specific logical pages only:

```bash
python3 inject_sha_weld_callouts.py /path/to/drawing.sha \
  --pages 1,3 --output output/annotated.sha
```

For a PCF-driven weld workflow, first number the `WELD` blocks in the PCF and
emit a UCI-based weld map:

```bash
python3 number_pcf_welds.py /path/to/line.pcf /path/to/drawing.sha \
  --output-pcf output/line-numbered.pcf \
  --output-map output/line-weld-map.json
```

Then feed that weld map back into the SHA writeback step:

```bash
python3 inject_sha_weld_callouts.py /path/to/drawing.sha \
  --weld-map output/line-weld-map.json \
  --output output/annotated.sha
```

This path keeps the weld numbering source in the PCF while using SHA-only page
geometry to place the visible callouts.

This is still evidence-limited:

- It is verified against the local SHA-only renderer in this repo.
- It does not use PDF geometry, OCR, or image pixels.
- Vendor-engine acceptance is still experimental because the full Shape2D
  hierarchy and parent-child semantics are not completely decoded.
- The current implementation expands existing `Sheet*` streams and updates the
  OLE FAT directly; keep the original SHA untouched and write to a copy.

## Inspect Unresolved PSM

```bash
python3 analyze_psm_hierarchy.py /path/to/drawing.sha \
  --output output/sha_svg/psm-hierarchy.json
```

This currently validates the complete `PSMspacemap/0x00008000` node table and
reports its local child references and relation-code distribution. Other maps
are inventory-only because their layouts change within the stream. It does not
claim those local references have already been linked to Sheet primitives.

In the examined sample the validated table has 1,994 nodes, 432 of whose IDs
also resolve to a `PSMcluster0` envelope. This supports a hierarchy link but
does not yet define the semantics of relation codes or local child references.

## Prompt For Another AI

```text
Use the attached parse-piping-sha SKILL.md and the SHA ISO scripts.
Input is a Shape2D .sha file. Reconstruct from SHA only; PDF is visual QA only.
Run the wrapper, inspect the SVG and trace JSON, then report:
1. decoded layers and unresolved layers;
2. UCI -> graphic -> PSM -> primitive traceability;
3. any visual discrepancy and the SHA record to investigate;
4. never use PDF coordinates, paths, OCR text, or pixels to change output.
```

## Current Limits

- `PSMcluster0` envelopes are partially decoded. `PSMspacemap/*`,
  `PSMroots`, `PSMclustertable`, and `PSMsegmenttable` remain the next target
  for complete hierarchy and local-transform recovery.
- The weld-callout injector currently writes line/text primitives directly into
  `Sheet*` streams without a decoded PSM or full hierarchy backfill. Treat the
  resulting SHA as an experimental branch until a vendor engine confirms it.
- The renderer recognizes only observed Shape2D primitive layouts. Do not
  generalize a record signature without validating it against the SHA and its
  PSM envelope.
- UCI is a strong model-object instance key. It is not automatically a
  physical one-item-one-code identifier.
