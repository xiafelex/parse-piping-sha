# SHA ISO AI Handoff

This package reconstructs piping ISO SVG output from a Shape2D/PDMS/Smart 3D
`.sha` file. It is deliberately SHA-only: a PDF may be used for visual QA but
must never provide geometry, text, coordinates, or placement values.

## Send These Files

- `sha_to_svg_prototype.py`
- `analyze_iso_split.py`
- `analyze_sha_pages.py`
- `analyze_psm_hierarchy.py`
- `render_psm_hierarchy_overlay.py`
- `run_sha_iso_render.py`
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

For every logical ISO page in a multi-sheet SHA:

```bash
python3 run_sha_iso_render.py /path/to/drawing.sha --all-pages --out-dir output/sha_svg --png
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

To inspect those candidate envelope matches without treating them as decoded
components, use the separate diagnostic output:

```bash
python3 render_psm_hierarchy_overlay.py /path/to/drawing.sha --page 1 \
  --output output/psm-type2-candidates.svg --types 2 --png
```

The default focuses on type-2 nodes with ids from `0x500` upward. It overlays
the matched `PSMcluster0` envelope on a SHA-only page reconstruction and labels
only sufficiently large envelopes so that thin strokes remain visible. This
does not classify a node as text, a flange, a weld, or any other primitive.

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
- The renderer recognizes only observed Shape2D primitive layouts. Do not
  generalize a record signature without validating it against the SHA and its
  PSM envelope.
- UCI is a strong model-object instance key. It is not automatically a
  physical one-item-one-code identifier.
