---
name: parse-piping-sha
description: Analyze piping/isometric `.sha` files produced by Shape2D, PDMS, or Smart 3D. Use when inventorying ISO pages, extracting UCI-to-graphic relationships, identifying same-line ISO splits, recovering title/revision/BOM data, or producing a traceable SHA-only SVG reconstruction. Use a supplied PDF only for visual QA, never as a geometry, coordinate, or text source for reconstruction.
---

# Piping SHA Parsing

Treat `.sha` as an OLE/Shape2D drawing container, not a checksum file. Preserve the source and write all derived files separately.

## Workflow

1. Run `scripts/inventory_sha.py <file.sha>` to inventory streams, logical ISO pages, UCI records, JSite resources, and tagged XML.
2. Identify drawing pages from non-empty `Sheet*` streams. Do not assume `Sheet6` is the only page.
3. Read each drawing Sheet's header before creating the SVG `viewBox`. A Shape2D Sheet can define a visible ISO viewport inside its larger square workspace, including a non-zero y origin; apply that complete source rectangle uniformly to Sheet vectors, PSM bounds, text, and symbols.
4. Build the identity chain: `UCI -> dynamic attribute graphic_ref -> Sheet -> PSM spatial record -> Shape2D primitive`.
5. Decode geometry in layers: line records, alternate line records, arcs, ellipses/point symbols, text, then composite objects.
6. Use raw Shape2D anchors for symbol centres when present. Use PSM bounding boxes for rendered extent only; do not assume their centres are the true point anchor.
7. Read `TaggedTxtData/*` for title, revision, signature, and configuration values. Read `JSite*/CONTENTS` only when present; label a site without `CONTENTS` as an unresolved external resource.
8. Decode `StyleCluster` before claiming font, font size, line type, or line weight fidelity. A style reference alone is not a decoded style.
9. Output SVG plus a JSON manifest recording each element's SHA source stream, source viewport, references, anchor/bbox, UCI candidates, and mapping confidence.

## Evidence Rules

- Use PDF only to identify visual discrepancies after a SHA-only render. Do not extract PDF text, coordinates, paths, or image pixels to position generated elements.
- Treat UCI as the strongest model-object instance key. It is not automatically a physical asset, procurement item, spool, or strict "one item one code" identifier.
- Mark a geometry-to-UCI mapping `direct` only when SHA object references match. Mark PSM-overlap associations as `candidate`.
- Keep unavailable external resources explicit. Do not invent missing border/template geometry from a PDF.
- Distinguish drawing coordinates from plant/model coordinates. SHA normally supplies 2D plotted positions, not the PCF's 3D engineering points.
- If an entire ISO body, BOM, and title block appear horizontally or vertically scaled together, investigate the Sheet viewport first. Do not compensate by translating individual text groups or by calibrating against PDF measurements.
- If table text remains locally misaligned after the Sheet viewport is correct, treat it as a font metrics/style problem. Trace its PSM style reference into `StyleCluster`; do not use PDF text locations as replacement coordinates.
- For an instrument bubble, use the ellipse primitive's own SHA anchor for the circle centre and PSM only for its radius. If PSM text envelopes are contained in that ellipse envelope, move them by the same source-space anchor delta so the `PI`/tag text remains inside the bubble. Keep this rule scoped to the matched primitive group; do not apply it to ordinary BOM text or weld dots.
- Free ISO annotations, BOM text, and title-block values can use a text transform that is the true baseline while PSM stores a displaced glyph envelope. When repeated records of the same SHA text style show this pattern, render at the Sheet text anchor and use PSM only for font height/width. In the examined sample this was verified for styles `0x0585`, `0x0586`, and non-rotated `0x0E74`. Scope the rule to verified styles and exclude closed callout frames, rotated text, the north `N`, and instrument-bubble text.
- Dimension values using verified style `0x0897` follow the same rule: their Sheet text transforms define the intended alignment columns, while PSM supplies glyph height and width. In the examined sample, using the PSM left edge displaced `178`/`154` and `86`/`17` left of their vertical dimension columns.
- For a text object inside a decoded closed callout frame, never force its `textLength` to the frame width. Use the PSM width/height ratio, fit it vertically inside the SHA frame, and centre it in that frame. The frame defines placement; the PSM envelope defines glyph proportions.
- Unicode title text can carry a non-ASCII font binding outside the ordinary ASCII text scan. Trace its local style id into `StyleCluster`, but also resolve its graphic reference into `PSMcluster0`: the PSM envelope is the authoritative rendered height and width when present. If its Sheet x anchor consistently differs from the PSM left edge in the same direction as verified text styles, use the Sheet anchor for x while retaining PSM y/height/width. Record this mixed mapping in the trace manifest.
- Do not reject UTF-16 labels merely because they contain full-width punctuation or trailing control characters. Strip controls and accept normal CJK punctuation. Shape2D may insert a 24-byte object header between the UTF-16 payload and its `x, y, direction` transform; scan the following aligned fields for the first plausible normalized transform. For each resulting label, choose a nearby sibling graphic only when its PSM envelope agrees with the text anchor. In the examined title block this recovered `德希尼布化学工程（天津）有限公司` -> `0x0C69` and `蓝星安迪苏南京有限公司` -> `0x08CC`.
- Small ASCII labels can use the same sibling relationship: retain the Sheet baseline anchor and use the adjacent PSM envelope for font height and forced text width. In the examined logo row, `T.EN Chemical Engineering (Tianjin) Co,.LTD` maps to `0x0BB0` and `Bluestar Adisseo Nanjing Co,.LTD` maps to `0x057F`. Never replace these with a visual/PDF-derived point size.
- A template text record and its rendered PSM envelope can be sibling graphics rather than share the same `graphic_ref`. Search the local Sheet object neighbourhood and validate against the text anchor and PSM dimensions before associating them. In the examined title block, `PIPING ISOMETRIC` uses the Sheet text anchor from record `0x1252` and the sibling PSM envelope `0x0E77`; render at the anchor with that PSM height and width. Record this relationship as a SHA sibling mapping, not a PDF calibration or a guessed font tier.
- Template labels without a PSM envelope still retain a direct Sheet anchor. Use their local style references and `StyleCluster` font-ratio tiers rather than prototype constants where the relationship is verified. Keep company-name text conservative until its own style-object mapping is decoded; its logo-row baseline has a different layout rule. Mark all such mappings as style-derived until the full hierarchy is decoded.
- For static template labels with both a Sheet anchor and PSM envelope, use the Sheet anchor for x and the PSM envelope for y/height/width. The PSM left edge can be a local-layout coordinate and visibly shifts revision/title-block labels left. Bound revision XML values without a PSM envelope should use a verified `StyleCluster` size tier rather than a fixed prototype size.

## Known Shape2D Observations

- `Unclustered Dynamic Attributes`: UCI and internal graphic references.
- `PSMcluster0`: rendered spatial envelopes, page hierarchy, levels, and object metadata.
- `PSMspacemap/*`, `PSMroots`, `PSMsegmenttable`: unresolved object-space hierarchy; prioritize these for exact parent-child mapping.
- A decoded PSM envelope is not a fully decoded PSM object. In the examined sample, 229 of 230 dynamic UCI graphic references yielded an envelope from `PSMcluster0`, but `PSMspacemap/*`, `PSMroots`, and `PSMclustertable` still contain the unresolved hierarchy, local transforms, and parent-child routing. Do not claim their role has been fully recovered solely from bbox coverage.
- Observed `Sheet` composite records beginning with `0x7B` hold child primitives at double page resolution. `primitive_type=5` stores a direct two-point segment; `primitive_type=6` stores an arc envelope. Decode both before concluding a component outline is unavailable.
- An earlier vector layer may be reused only when it is documented as SHA-derived and every imported line's `data-graphic` still occurs in the current SHA dynamic-attribute table. Strip pre-existing `data-layer`/`data-uci` attributes before adding current provenance; duplicate XML attributes invalidate the entire SVG. Preserve the imported line's original coordinates and attach the current UCI. This is a traceable compatibility layer for not-yet-decoded component primitive families, not a PDF-derived replacement.
- `StyleCluster`: style library references; observed content includes font families, size ratios, and line types.
- `TaggedTxtData/*`: title, revision, signature, configuration, and other bound fields.
- `JSite*/CONTENTS`: embedded bitmaps/OLE resources when present. `JProperties` without `CONTENTS` is insufficient to reconstruct the resource. It may still participate in PSM levels or object hierarchy; report it as unresolved rather than asserting that it is a specific missing border.
- A shared JSite id may occur in every page header. Treat that as evidence of a shared dependency, not as proof that its geometry is embedded in each Sheet.
- In one A1 sample, drawing Sheets advertised a visible viewport of approximately `(x=0, y=1886.73, width=14129.72, height=9978.98)` inside a nominal `16800 x 11880` workspace. The matching width, height, and y-maximum ratios were about `0.841`, `0.594`, and `0.706`; `y = (0.706 - 0.594) * 16800`. This preserves A1's aspect ratio and avoids clipping the title block. Nearby header dimensions can describe other internal extents; validate the complete rectangle across drawing Sheets. This is sample evidence, not a universal constant.
- Observed primitive signatures are evidence from samples, not a complete format specification. Validate each against anchors and PSM bounds before using it generally.

## Deliverables

Provide a concise report with page count, UCI count, resource status, decoded versus unresolved layers, and confidence limits. For SVG work, include a trace manifest. When comparing against PDF, list discrepancies and then trace fixes only to SHA fields.

## Handoff To Another AI

When this workflow is handed to a colleague, send the skill together with the
algorithm files `sha_to_svg_prototype.py`, `analyze_iso_split.py`,
`analyze_sha_pages.py`, `analyze_psm_hierarchy.py`, `render_psm_hierarchy_overlay.py`, and `run_sha_iso_render.py`. Include
`SHA_ISO_AI_HANDOFF.md` as the execution and evidence-rule contract.

The standard command is:

```bash
python3 run_sha_iso_render.py /path/to/drawing.sha --page 1 --out-dir output/sha_svg --png
```

The wrapper emits an SVG and a trace JSON; PNG output is optional. It accepts
an optional component layer only when that SVG is documented as SHA-derived
from the exact same source SHA. Never accept a PDF as an algorithm input.

Ask the next AI to first inspect the trace JSON and report decoded versus
unresolved layers. Every visual correction must cite a SHA stream, object
reference, text anchor, PSM envelope, or primitive record. If it cannot do
that, it must report the element as unresolved rather than calibrating it from
the PDF.

For PSM hierarchy work, run:

```bash
python3 analyze_psm_hierarchy.py /path/to/drawing.sha --output output/psm-hierarchy.json
```

It fully validates the observed node framing in `PSMspacemap/0x00008000`:
`tseg` header, `<4H>` node header, followed by `child_count` `<IH>` child
entries. Other `PSMspacemap` streams can change record layout and are
inventory-only until separately validated. The local child refs and relation
codes are evidence only. Their semantic meaning and their link back to
`PSMcluster0`/Sheet primitives must be established before using them for
rendering.

To inspect the current evidence visually without assigning unsupported
semantics, run:

```bash
python3 render_psm_hierarchy_overlay.py /path/to/drawing.sha --page 1 \
  --output output/psm-type2-candidates.svg --types 2 --png
```

This produces a separate SHA-only diagnostic SVG: type-2 `0x8000` node ids
at least `0x500` are matched numerically to record-bounded `PSMcluster0` envelopes
and overlaid on the SHA-only render. It is an investigation aid, not a
primitive decoder. Use `--types 2,3` only for the denser aggregate view.

Current evidence from the examined SHA: the validated `0x8000` table has 1,994
nodes, with 432 node IDs also resolving to a `PSMcluster0` envelope. Type-2
nodes commonly carry one or two local child refs; type-3 nodes dominate the
table and are likely an aggregation layer. The local refs do not overlap node
IDs, so do not label relation codes `182`, `183`, `184`, `190`, or `201` as
specific drawing primitive types without a cross-sample proof.

The current report also reads the observed unaligned Sheet identity header
`<I H I I I>` at byte 4 and reports local child refs that land inside the
header's local id span. In the examined file, `Sheet34246` and `Sheet36113`
show repeatable `cluster_ref + 1`, `+3`, and `+6` targets under relations
`183`, `182`, and `184`. This proves a Sheet-local namespace association only;
it does not identify those relations as line/text/flange primitives.

The current `PSMsegmenttable` is structurally complete in the examined file:
`stab` + a uint32 count + exactly that many payload bytes. Its six payload
values are decoded as bytes but their semantics are unknown. `PSMcluster0`
tag values are likewise only statistical record tags: the same tag can occur
on dynamic UCI-linked graphics and non-dynamic template/layout graphics, so do
not call a tag a component class.

`PSMcluster0` graphic refs can be grouped into intervals starting at a Sheet
header's `cluster_ref` and ending at the next Sheet `cluster_ref`. This is a
verified storage-namespace association in the examined file, useful for
narrowing which Sheet stream to decode next. It is not proof that the records
appear on that rendered ISO page or belong to one UCI.

`PSMcluster0` additionally has observed contiguous `<I5H>` envelope-record
runs in the examined file. Accept a run only after at least three consecutive
plausible 14-byte records; this avoids treating an arbitrary byte occurrence
as a graphic reference. The final `uint16` is still an unresolved tag, and
these records alone do not decode PSM primitive semantics.
