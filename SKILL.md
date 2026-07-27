---
name: parse-piping-sha
description: Analyze piping/isometric `.sha` files produced by Shape2D, PDMS, or Smart 3D. Use when inventorying ISO pages, extracting UCI-to-graphic relationships, identifying same-line ISO splits, recovering title/revision/BOM data, or producing a traceable SHA-only SVG reconstruction. Use a supplied PDF only for visual QA, never as a geometry, coordinate, or text source for reconstruction.
---

# Piping SHA Parsing

Treat `.sha` as an OLE/Shape2D drawing container, not a checksum file. Preserve the source and write all derived files separately.

## Workflow

1. Run `scripts/inventory_sha.py <file.sha>` to inventory streams, logical ISO pages, UCI records, JSite resources, and tagged XML.
2. Identify drawing pages from non-empty `Sheet*` streams. Do not assume `Sheet6` is the only page.
3. Read each drawing Sheet's header before creating the SVG `viewBox`. A Shape2D Sheet can define a visible ISO viewport inside its larger square workspace, including a non-zero y origin; apply that complete source rectangle uniformly to Sheet vectors, PSM bounds, text, and symbols. If a physical Sheet has no such declaration, inherit the declared viewbox from a sibling physical Sheet in the same SHA (prefer `Sheet6`) only after confirming that its primitives use the same normalized page coordinate system; do not fall back to the full square workspace by default.
4. Build the identity chain: `UCI -> dynamic attribute graphic_ref -> Sheet -> PSM spatial record -> Shape2D primitive`.
5. Decode geometry in layers: line records, alternate line records, arcs, ellipses/point symbols, text, then composite objects.
6. Use raw Shape2D anchors for symbol centres when present. Use PSM bounding boxes for rendered extent only; do not assume their centres are the true point anchor.
7. Read `TaggedTxtData/*` for title, revision, signature, and configuration values. Read `JSite*/CONTENTS` only when present; label a site without `CONTENTS` as an unresolved external resource.
8. Decode `StyleCluster` before claiming font, font size, line type, or line weight fidelity. A style reference alone is not a decoded style.
9. Output SVG plus a JSON manifest recording each element's SHA source stream, source viewport, references, anchor/bbox, UCI candidates, and mapping confidence.
10. For experimental SHA writeback, inject only SHA-derived primitives. When adding weld-style diamonds, derive the target dots from SHA UCI/PSM evidence, write to a copy of the SHA, and verify first with the local SHA-only renderer before claiming vendor-engine compatibility.

## Evidence Rules

- Use PDF only to identify visual discrepancies after a SHA-only render. Do not extract PDF text, coordinates, paths, or image pixels to position generated elements.
- Treat UCI as the strongest model-object instance key. It is not automatically a physical asset, procurement item, spool, or strict "one item one code" identifier.
- Mark a geometry-to-UCI mapping `direct` only when SHA object references match. Mark PSM-overlap associations as `candidate`.
- A PCF can be used to classify an unresolved UCI as weld, pipe, flange, or support during audit, but never to supply an SVG coordinate or replacement symbol. If its SHA PSM-to-parent link is not proven, retain the visible SHA geometry and mark that UCI relation unresolved.
- Keep unavailable external resources explicit. Do not invent missing border/template geometry from a PDF.
- Distinguish drawing coordinates from plant/model coordinates. SHA normally supplies 2D plotted positions, not the PCF's 3D engineering points.
- If an entire ISO body, BOM, and title block appear horizontally or vertically scaled together, investigate the Sheet viewport first. Do not compensate by translating individual text groups or by calibrating against PDF measurements.
- If table text remains locally misaligned after the Sheet viewport is correct, treat it as a font metrics/style problem. Trace its PSM style reference into `StyleCluster`; do not use PDF text locations as replacement coordinates.
- For an instrument bubble, use the ellipse primitive's own SHA anchor for the circle centre and PSM only for its radius. If PSM text envelopes are contained in that ellipse envelope, move them by the same source-space anchor delta so the `PI`/tag text remains inside the bubble. Keep this rule scoped to the matched primitive group; do not apply it to ordinary BOM text or weld dots.
- Free ISO annotations, BOM text, and title-block values can use a text transform that is the true baseline while PSM stores a displaced glyph envelope. When repeated records of the same SHA text style show this pattern, render at the Sheet text anchor and use PSM only for font height/width. In the examined sample this was verified for styles `0x0585`, `0x0586`, and non-rotated `0x0E74`. Scope the rule to verified styles and exclude closed callout frames, rotated text, the north `N`, and instrument-bubble text.
- Dimension values using verified style `0x0897` follow the same rule: their Sheet text transforms define the intended alignment columns, while PSM supplies glyph height and width. In the examined sample, using the PSM left edge displaced `178`/`154` and `86`/`17` left of their vertical dimension columns.
- For a text object inside a decoded closed callout frame, never force its `textLength` to the frame width. Use the PSM width/height ratio, fit it vertically inside the SHA frame, and centre it in that frame. The frame defines placement; the PSM envelope defines glyph proportions. When an anchor is inside overlapping frames, choose a normal-PSM-text frame by logarithmic width agreement with its PSM glyph extent, then anchor-to-centre distance; use anchor distance only for a page/container PSM extent. This separates long `PS-N...` labels from adjacent short `Sxx` cells without PDF-derived placement.
- Before the overlap fallback, check whether `text graphic_ref + 5` is itself a directly decoded closed-frame parent. This observed Sheet-local sequence links 344 validated marker/reference texts across the sample set and is stronger than a spatial candidate. Use it only when that exact parent closes to a rectangle; otherwise retain the PSM-width/anchor rule.
- Some component/support callouts use a separate direct composite relation: four type-5 frame edges share a composite parent and have child references `text graphic_ref + 1` through `+4`. Recover the frame from that sequence before using spatial matching. Treat edges as horizontal/vertical within two page units because uint16 composite coordinates can quantise an otherwise straight edge by one unit. This was verified for `S3`, `SD010`, and `PS-100-00742` on LN `Sheet1046`; do not apply it unless all four sequential child references close a real rectangle.
- Preserve printable engineering symbols when filtering raw Sheet text. In particular, ASCII `\"` is the inch mark in labels such as `SD010 1/2\"` and `SD010 1\"`; excluding it silently creates an empty SHA frame even though both text and its direct composite frame exist. Retain a constrained printable whitelist so binary false positives are still excluded.
- Do not suppress a visible-looking blank frame merely because it has no nearby text. AMSS2 `Sheet6` has an extra four-side ordinary Sheet frame (parent `0x03C3`, children `0x03C2/0x03C5/0x03C6/0x03C8`) whose PDF visibility differs, but it is neither a proven composite text frame nor represented in the decoded PSM space-map child table. Keep such records until PSM visibility/parent semantics are decoded; a generic hide rule can remove real component geometry.
- The local `18/32` line family can contain an offset backing frame duplicated by the visible type-5 composite callout frame. Suppress an `18/32` rectangle only when its parent is `text_ref + 5` and that same text has an independently closed type-5 `text_ref + 1..4` frame. This verified duplicate relation removes displaced empty S/PS boxes while retaining all other `18/32` component details and unproven rectangles. Never suppress the full record family globally.
- Dense flange/valve junctions can mix verified visible component outlines with additional `18/32` two-point and type-5 composite strokes. In AMSS2 `Sheet34246`, only 68 of 1,286 `18/32` children have a local PSM candidate and their status-like values 5/6 are also used by visible geometry. Do not filter this family from a PDF density comparison or status value alone; decode `PSMspacemap`/`PSMroots` parent semantics first.
- Raw `18/32` records have a local grouping uint16 at byte 14, in addition to the child id at byte 6, graphic/sheet reference at byte 10, style id at byte 20, and coordinates at byte 24. It can group hundreds of records, but AMSS2 `Sheet34246` groups mix direct-UCI component children with undecorated detail strokes and have no independent PSM/UCI parent mapping. Treat it as an object-grouping clue only, never as a visibility or layer filter.
- Preserve validated `0x13/0xAC` relations as provenance: their 32-bit graphic ref at byte 10 maps to a 32-bit local group at byte 14. Emit the group in SVG `data-local-group` and trace `local_object_group` metadata, but do not alter the vector output from it. AMSS2 `Sheet34246` validates this relation for dense-component groups `0x8621` and `0x8623`.
- Treat the type-5 `text_ref + 1..4` sequence as a frame relation only for verified boxed classes: ISO marker codes, `PS-N...`/`PANDA...` references, `SD...` support labels, and short numeric boxed labels. A free annotation can coincidentally precede four child ids that form an unrelated rectangle (RHO1 `SEE ISO` was a verified false match). Keep free annotations at their direct Sheet anchor plus PSM glyph metrics; CIxx remains the separately observed preceding-frame case.
- Template field anchors are not universally left baselines. LS `Sheet8093` style `0x1FB5` places the `N400`, `N400P3A`, and `80 mm` anchors near the right side of their PSM glyph envelopes. Until StyleCluster alignment semantics are decoded, retain the PSM glyph placement for this family; do not globally substitute the Sheet x anchor based on a PDF visual offset.
- Static `Sheet221` title-block labels can also have repeated `PSMcluster0` refs. For each direct template text anchor, enumerate the same-ref PSM candidates, reject page/container extents above the title-block glyph range, then select the remaining box nearest to the local Sheet anchor. This recovered N491 `OF` (`0x0959`) and AMSS2 `PID NO.` (`0x1168`) without using another drawing or a PDF as a metric source.
- For a physical-Sheet text coverage audit, require a finite normalized direction, ISO character set, nonzero graphic reference, and local plausible PSM glyph envelope. Exclude `Sheet221` labels that are intentionally emitted through the shared template layer and extraction-date metadata. Compare the remaining exact text against the generated SVG; do not treat raw byte substrings or `graphic_ref=0` one-character records as visible labels.
- A `PS-N...`/`PANDA...` reference or component marker can point to a page-scale PSM parent envelope rather than its own glyph extent. Before rejecting such an envelope, test whether the raw Sheet anchor lies inside a directly decoded closed rectangle. If so, use that SHA rectangle as the text boundary and record `sha-closed-frame-replaces-psm-container`; do not recover text from PDF. This must remain limited to the observed boxed-reference patterns and a real decoded frame.
- A short but legitimate page-local Sheet label can also point to a PSM container. Recover it only when every SHA-only guard holds: text length is 3--10, the Sheet anchor is normalized, graphic/style references are local 16-bit values, and at least three same-Sheet same-style peers have ordinary 30--320 page-unit PSM glyph heights. Use those peers' median anchor offset, glyph height, and per-character width. Do not apply this to one/two-character values, unbounded references, or an arbitrary style fallback: those patterns include binary false positives such as `{f` and `1`.
- For ordinary Sheet two-point primitives, do not flatten all strokes to one SVG width. A verified `StyleCluster` record headed by `0x002E,0x0036` stores the matching line-style id at byte `20` and its normalized width ratio at byte `40`; when that id equals the Sheet record's `style_ref`, render `ratio * 16800` page units. Keep template and composite strokes at a documented fallback unless their separate style linkage is proven.
- Later physical Sheet streams can use a second two-point family headed by `0x0018,0x0032`: its child primitive id is at byte `6`, style id at byte `20`, and coordinates begin at byte `24`. It uses the same proven StyleCluster line-width table. Decode this family before treating a multi-page ISO's direct vector layer as unavailable.
- Do not promote composite child tag `0` to a generic line primitive. Across the seven verified SHA sets, `1,228/1,530` tag-0 children overlap a type-5 sibling inside the same composite parent and normally have no direct PSM graphic mapping. Treat it as auxiliary composite detail until a subtype decoder proves a visible geometry rule; drawing its four bounds as a line duplicates or invents component strokes.
- Keep composite child types `11` and `16` inventory-only unless a subtype decoder is proven. In the examined sets they rarely have an independent `PSMcluster0` identity and commonly overlap a type-5 sibling inside the same parent; their four uint16 fields are not established as line endpoints or ellipse bounds. Do not render them as generic strokes merely because they occur near a visible flange, reducer, or valve.
- For a micro connection-point audit, reproduce the renderer's complete page-local filter, not a global `PSMcluster0` scan: dynamic ref present in the selected Sheet, PSM envelope at most `45 x 45`, no decoded vector ref, and anchor-to-geometry distance at most 80 page units. Compare exact `(graphic_ref, UCI)` pairs with the trace manifest. A dynamic-ref byte occurrence can recur across physical Sheets and is not evidence of an unrendered point.
- Unicode title text can carry a non-ASCII font binding outside the ordinary ASCII text scan. Trace its local style id into `StyleCluster`, but also resolve its graphic reference into `PSMcluster0`: the PSM envelope is the authoritative rendered height and width when present. If its Sheet x anchor consistently differs from the PSM left edge in the same direction as verified text styles, use the Sheet anchor for x while retaining PSM y/height/width. Record this mixed mapping in the trace manifest.
- Do not reject UTF-16 labels merely because they contain full-width punctuation or trailing control characters. Strip controls and accept normal CJK punctuation. Shape2D may insert a 24-byte object header between the UTF-16 payload and its `x, y, direction` transform; scan the following aligned fields for the first plausible normalized transform. For each resulting label, choose a nearby sibling graphic only when its PSM envelope agrees with the text anchor. In the examined title block this recovered `德希尼布化学工程（天津）有限公司` -> `0x0C69` and `蓝星安迪苏南京有限公司` -> `0x08CC`.
- Small ASCII labels can use the same sibling relationship: retain the Sheet baseline anchor and use the adjacent PSM envelope for font height and forced text width. In the examined logo row, `T.EN Chemical Engineering (Tianjin) Co,.LTD` maps to `0x0BB0` and `Bluestar Adisseo Nanjing Co,.LTD` maps to `0x057F`. Never replace these with a visual/PDF-derived point size.
- A template text record and its rendered PSM envelope can be sibling graphics rather than share the same `graphic_ref`. Search the local Sheet object neighbourhood and validate against the text anchor and PSM dimensions before associating them. In the examined title block, `PIPING ISOMETRIC` uses the Sheet text anchor from record `0x1252` and the sibling PSM envelope `0x0E77`; render at the anchor with that PSM height and width. Record this relationship as a SHA sibling mapping, not a PDF calibration or a guessed font tier.
- Keep the physical right-side template panel separate from the main ISO annotation font family. Across the reviewed sheets, direct text with a SHA anchor at `x >= 0.55 * SHEET_UNIT` forms the BOM/title/revision family (for example RHO `0x95C4/0x9608` versus body `0x9609`, and AMSS2 `0x15D3/0x1617` versus body `0x1618`). Render that panel in the SHA-proven sans-serif template family while preserving the fixed-pitch main drawing annotations. This is a source-coordinate/style partition, not a PDF-derived crop or position.
- Do not leave the ISO-body fixed-pitch family as CSS-generic `monospace`. The examined `StyleCluster` explicitly contains `Courier New` records, separately from `Arial` template and `SimHei-Z` Chinese records. Emit `font-family="Courier New, Courier, monospace"` for the body so an SVG consumer does not silently substitute a different fixed-pitch font and distort SHA-derived text width/height. Keep the right template panel's `Arial` override and Unicode title branch separate.
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

For experimental writeback work, report separately:

- which `Sheet*` streams were modified;
- whether the OLE stream sizes changed;
- whether the modified SHA re-opened cleanly;
- whether the SHA-only renderer confirmed the injected geometry.

## Handoff To Another AI

When this workflow is handed to a colleague, send the skill together with the
algorithm files `sha_to_svg_prototype.py`, `analyze_iso_split.py`,
`analyze_sha_pages.py`, `analyze_psm_hierarchy.py`, `render_psm_hierarchy_overlay.py`, `run_sha_iso_render.py`, and `inject_sha_weld_callouts.py`. Include
`SHA_ISO_AI_HANDOFF.md` as the execution and evidence-rule contract.

The standard command is:

```bash
python3 run_sha_iso_render.py /path/to/drawing.sha --page 1 --out-dir output/sha_svg --png
```

For a multi-page SHA, first inventory all populated `Sheet*` streams and then
render all recognized logical pages with:

```bash
python3 run_sha_iso_render.py /path/to/drawing.sha --all-pages --out-dir output/sha_svg --png
```

The wrapper emits an SVG and a trace JSON; PNG output is optional. It accepts
an optional component layer only when that SVG is documented as SHA-derived
from the exact same source SHA. Never accept a PDF as an algorithm input.

For PCF-derived weld numbering, first write the number into a PCF copy and
retain the exact SHA target identity in a weld map:

```bash
python3 number_pcf_welds.py /path/to/line.pcf /path/to/drawing.sha \
  --output-pcf output/line-numbered.pcf \
  --output-map output/line-weld-map.json
```

Each weld-map row must retain `page`, `graphic_ref`, `uci`, and the SHA 2D
point.  Do not use UCI alone during writeback: one UCI can be visible in more
than one Sheet context.  The unique writeback key is
`(page, graphic_ref, uci)`.

For experimental weld-callout writeback, run:

```bash
python3 inject_sha_weld_callouts.py /path/to/drawing.sha \
  --output output/annotated.sha \
  --weld-map output/line-weld-map.json
```

This script currently writes only Sheet-level line/text primitives. It is
evidence-backed at the SHA-only renderer level, not yet a guarantee that a
vendor Shape2D or PDMS engine will accept the modified hierarchy.

Never accept a weld-callout result merely because its injected coordinates are
self-consistent.  Independently re-read the original and annotated SHA and
validate every generated callout:

```bash
python3 verify_sha_weld_callouts.py /path/to/original.sha \
  output/annotated.sha output/line-weld-map.json \
  --output output/weld-callout-qa.json
```

The report must show every leader starting at its source Sheet ellipse anchor,
every diamond closing, and every selected point within the declared tolerance
of original decoded pipe geometry.  If any callout fails, remap that UCI to a
different same-UCI candidate or omit it as unresolved; do not publish a
visually plausible but unverified callout.

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

Do not associate a dynamic UCI `graphic_ref` with a Sheet primitive merely
because its four little-endian bytes occur anywhere in the Sheet stream. A
uint32 ref can coincide with adjacent uint16 coordinates in a type-5 composite
child. Require either a known decoded record boundary/reference field or a
validated PSM hierarchy link. In the verified LS `Sheet8093` case, dynamic ref
`0x1BA3` is an elbow's PSM identity but its only Sheet-byte occurrence is the
coordinate value `0x1BA3`; the elbow is already rendered by its composite
outline. Treat bare byte containment as an audit candidate only, never proof
of an omitted visible symbol.

Do not treat a byte-consuming mixed-layout parser for
`PSMspacemap/0x00000000` as validated merely because it reaches EOF. A tested
ordinary/compact-node heuristic can shift later node boundaries despite full
consumption. Keep this stream inventory-only, except for individually checked
local `graphic_ref -> space_ref` records, until a cross-sample framing rule is
proved.

The short maps `0x2000`, `0x4000`, and `0x6000` are structurally complete as
`tseg` plus two uint16 header words and a uint16 payload list. Their values are
now extracted, but the lists' index/segment meaning is unresolved; do not link
them to graphic ids without a cross-sample mapping.

`PSMcluster0` additionally has observed contiguous `<I5H>` envelope-record
runs in the examined file. Accept a run only after at least three consecutive
plausible 14-byte records; this avoids treating an arbitrary byte occurrence
as a graphic reference. The final `uint16` is still an unresolved tag, and
these records alone do not decode PSM primitive semantics.

## PDF Fidelity Guardrail

When producing PNGs for visual QA, do not screenshot the top-left viewport of
a standalone SVG with a large intrinsic `width`/`height`: that can crop the
ISO to its north marker even though the SHA-derived SVG is complete. Read the
SVG `viewBox`, render the unchanged SVG inside a white HTML image canvas with
`object-fit: contain`, and screenshot that canvas at the required review
resolution. Verify physical SVG count equals PNG count before comparing any
page to its PDF. This is an output-validation rule only; PDF pixels must never
enter the decoder or alter SHA-derived geometry.

Use a PDF only as a visual acceptance reference, never as a source of geometry
or coordinates. A `59/2B` Sheet signature plus a PSM envelope is not by itself
proof of visible ellipse geometry: it can be a layout/container record. Render
it as an ellipse only when its envelope is local to a symbol. In the current
cross-sample renderer, reject records with either PSM envelope dimension above
1000 page units; this removes demonstrated page-sized false circles while
preserving small instrument and connection symbols. Record the SHA Sheet name,
graphic ref, PSM dimensions, PDF observation, and re-render result for every
such filtering rule.

Tiny UCI/PSM envelopes without a direct vector reference are also not
automatically visible connection or weld dots. Before emitting one, take its
SHA ellipse anchor when available, otherwise its PSM-envelope centre, and
require it to be within 80 page units of a decoded Sheet line or composite
segment. This is a SHA-only topology check: it prevents layout/hidden micro
records from becoming isolated dots while retaining pipe-adjacent weld dots.
Document the rejected UCI, Sheet stream, envelope, nearest-line distance, and
the visual acceptance result.

For every accepted micro point, write its UCI, graphic ref, PSM envelope,
ellipse anchor (or envelope-centre fallback), and topology mapping basis into
the SVG trace manifest. Do not audit UCI coverage from line/text associations
alone: an accepted SHA ellipse-anchor dot is already a rendered base-layer
object even when no line or text carries that UCI.

For rotated Sheet text, do not treat the PSM axis-aligned envelope height as
the font size. With baseline angle `a`, envelope width `W`, and envelope height
`B`, solve `W = L*cos(a) + H*sin(a)` and `B = L*sin(a) + H*cos(a)` for local
text length `L` and glyph height `H`; render at the SHA text anchor and angle.
When `a` is near 45 degrees the system is singular, retain a documented
conservative fallback until the relevant StyleCluster metric is proven. Record
the style id, direction, PSM envelope, recovered metrics, and visual result.

Do not centre arbitrary ASCII text in a nearby decoded rectangle merely because
the Sheet text transform anchor lies inside it. Apply rectangle-centering only
to verified component-marker syntax (`F/G/B/S/T` codes). Free labels such as
`INSUL:` and insulation codes must keep their direct PSM extent unless a
separate SHA composite-record relation proves the box/text association.

One verified exception is an insulation code matching `CI\d+`: a code can be
associated with a closed axis-aligned rectangle whose Sheet parent graphic ref
precedes the text graphic by one to eight local refs. Verify that the parent
has exactly the four boundary segments; use the rectangle centre only for the
code position, and retain the code's PSM glyph height and text length. In the
validated sample, `0x621 -> 0x61B` and `0x716 -> 0x711` establish this rule.

Frame-centering is also verified for reference text matching
`PS-N<digits>-<digits>` or `PANDA<digits>-<digits>-<digits>`, in addition to
the ISO component marker syntax. Keep `INSUL:`, `First Dimension`, coordinate
notes, and other free labels at their PSM extent. This classification is
required because a text transform anchor may coincide with a leader/frame
without making its glyphs frame content.

For UTF-16 template/Chinese labels, a nearby PSM bbox may be a title-block
container. Use the StyleCluster font metric as a plausibility gate: accept a
candidate PSM height only within roughly `0.45x..4x` that metric and reject a
width that is implausible for the text length; otherwise render from the SHA
text anchor with the StyleCluster size. Never infer physical PDF page order
from `SEE SHT` text or a `SheetNNNN` suffix. The authoritative page mapping is
the actual title-block `sheet / total` field rendered from the same physical
Sheet.

For corpus QA, enumerate every populated physical `Sheet` rather than only
the deduplicated logical page numbers. Read the current-page field at title
block paper x about `0.753` and the total-page field at x about `0.773`; do
not select either one by byte-record order. Before visual review, assert that
the SHA physical-Sheet count equals its sibling PDF page count, while keeping
the PDF outside the reconstruction path. `audit_sha_pdf_corpus.py`,
`summarize_sha_render_corpus.py`, `measure_sha_pdf_visual_difference.py`, and
`build_sha_pdf_visual_qa.py` implement this evidence-only flow.

Right-edge title blocks can contain both a plain drawing identifier and the
final `*identifier*` label. When both are direct vertical Sheet text records
in the right title area, render only the starred final label. If its PSM
reference has multiple candidates, select the box nearest the direct SHA text
anchor; this is a scoped text rule, not a generic PSM primitive rule. Apply
the same anchor-based candidate selection to documented fixed Sheet221 sibling
labels such as `PIPING ISOMETRIC` and the company-name fields.

The same sibling graphic reference can occur more than once in `PSMcluster0`.
For a UTF-16 label that already has a direct SHA transform, enumerate every
valid envelope for that reference rather than accepting the first byte match;
rank candidates by agreement with the text anchor and retain only the one
whose dimensions pass the StyleCluster metric gate. In the RHO1 template,
`0x0CBB` first resolves to a page/container record and later to its real
`1888 x 96` Chinese project-title extent. Keep this multiple-candidate logic
scoped to source-anchored text: it does not prove a new generic PSM hierarchy
or justify changing primitive geometry.

For a non-boxed BOM/template label whose PSM envelope is page-scale, do not
generically replace the envelope with a style estimate. A SHA-only fallback is
permitted only when all of these are true: the raw Sheet anchor is in the
upper/right material-template region (at least 50% of page width and 15% of
page height from the visible origin), the text has at least eight characters,
and repeated same-style SHA records establish glyph height, character width,
and anchor offset. Render from that anchor and record
`sha-style-fallback-replaces-psm-container` in the manifest. This rule is for
material descriptions/headings, not title-block identifiers, page fields, or
short numeric values; never use PDF coordinates to widen it.

Sheet text objects are recovered from a binary stream, so reject records that
contain characters outside the observed printable ISO text set. In addition,
a one- or two-character candidate paired with a PSM envelope taller than 800
page units is a page/container false positive, not a visible rotated label.
This guard must be applied before the rotated-text projection calculation;
record the candidate text, Sheet, PSM dimensions, and re-render outcome.

Before resolving a printable UTF-16 Sheet text run's graphic/style references,
check its first uint16 value. If it equals the decoded printable character
count minus one, it is a Shape2D character-count prefix that the printable
scan has swallowed (a count such as `0x0021` otherwise appears as `!`). Shift
the text start by two bytes and resolve references relative to that corrected
start. Do not strip arbitrary leading printable characters: this exact count
relation is the proof. The transform remains after the end of the run. This
fix is required for material descriptions and other strings whose corrected
`graphic_ref`/`style_ref` would otherwise be shifted by two bytes.

Type-5 child primitives in a composite Sheet record use uint16 coordinates at
twice page resolution. Convert each endpoint to the renderer's normalized
Sheet coordinate as `value / (2 * SHEET_UNIT)`; do not multiply a page-unit
value by `SHEET_UNIT` again during SVG output. Cross-check on a component-rich
page, because type-5 carries real flange/reducer/valve detail. Do not reject an
18/32 record solely because its parent ref equals the numeric Sheet stream id:
that relation can carry visible pipe double-lines and dimensions.

Composite child tags `0`, `6`, `11`, and `16` are not validated as generic
straight segments. A SHA-only test that rendered tag `0` alongside tag `5` on
N491163 added only sparse short strokes and did not explain a PDF-visible
omission. Keep those tags inventory-only until a cross-sample record-boundary
and visible-shape relation proves their primitive semantics; do not promote
them merely because their four uint16 fields resemble endpoints.
