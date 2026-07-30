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
- A weld UCI is not synonymous with one black-dot primitive. The PCF-validated `000138AA` weld family links through SHA direct graphic refs to circle/circle-companion records, lines, composites, and combinations of those families. Treat the UCI as the weld's SHA graphic group; choose a weld anchor only through a direct member/geometry rule, never by the nearest visible dot alone.
- PCF cross-checking shows that the first GUID field is a useful but non-exclusive UCI object-family hint in this corpus: `000138AA` is `WELD` in all 9,090 matched PCF rows; `0001388C` is mainly `PIPE`/`PIPE-FIXED` (4,934 of 4,937); `000138B6` is mainly instrument families (264 of 266); `000138A9` is `BOLT` (2,883 of 2,883) and `000138A8` is `GASKET` (2,381 of 2,381). In contrast, `00013885` and `000138B7` span multiple fittings, valves, and miscellaneous components. Use this only as PCF-derived family evidence, never as a complete component specification, a SHA rendering rule, or a substitute for an exact UCI match.
- Keep unavailable external resources explicit. Do not invent missing border/template geometry from a PDF.
- Distinguish drawing coordinates from plant/model coordinates. SHA normally supplies 2D plotted positions, not the PCF's 3D engineering points.
- If an entire ISO body, BOM, and title block appear horizontally or vertically scaled together, investigate the Sheet viewport first. Do not compensate by translating individual text groups or by calibrating against PDF measurements.
- If table text remains locally misaligned after the Sheet viewport is correct, treat it as a font metrics/style problem. Trace its PSM style reference into `StyleCluster`; do not use PDF text locations as replacement coordinates.
- For an instrument bubble, use the ellipse primitive's own SHA anchor for the circle centre and PSM only for its radius. If PSM text envelopes are contained in that ellipse envelope, move them by the same source-space anchor delta so the `PI`/tag text remains inside the bubble. Keep this rule scoped to the matched primitive group; do not apply it to ordinary BOM text or weld dots.
- Free ISO annotations, BOM text, and title-block values can use a text transform that is the true baseline while PSM stores a displaced glyph envelope. When repeated records of the same SHA text style show this pattern, render at the Sheet text anchor and use PSM only for font height/width. In the examined sample this was verified for styles `0x0585`, `0x0586`, and non-rotated `0x0E74`. Scope the rule to verified styles and exclude closed callout frames, rotated text, the north `N`, and instrument-bubble text.
- Dimension values using verified style `0x0897` follow the same rule: their Sheet text transforms define the intended alignment columns, while PSM supplies glyph height and width. In the examined sample, using the PSM left edge displaced `178`/`154` and `86`/`17` left of their vertical dimension columns.
- For a text object inside a decoded closed callout frame, never force its `textLength` to the frame width. Use the PSM width/height ratio, fit it vertically inside the SHA frame, and centre it in that frame. The frame defines placement; the PSM envelope defines glyph proportions. When an anchor is inside overlapping frames, choose a normal-PSM-text frame by logarithmic width agreement with its PSM glyph extent, then anchor-to-centre distance; use anchor distance only for a page/container PSM extent. This separates long `PS-N...` labels from adjacent short `Sxx` cells without PDF-derived placement.
- A process-note row can be a two-line SHA cell rather than a multi-note group: `INSUL:`/`CLASS:`/`TRACE:` has either a directly adjacent `graphic_ref + 1` code record such as `HI50`, or an observed reverse `graphic_ref - 2` code record such as `HI60`. When both direct Sheet anchors lie inside the same small closed `18/32` rectangle, bind both text records to that frame and render at their Sheet baselines using their PSM glyph size/width. This is a local reference-plus-anchor relation; do not infer it from visual proximity alone or apply it when the candidate code is not a plausible token.
- Before the overlap fallback, check whether `text graphic_ref + 5` is itself a directly decoded closed-frame parent. This observed Sheet-local sequence links 344 validated marker/reference texts across the sample set and is stronger than a spatial candidate. Use it only when that exact parent closes to a rectangle; otherwise retain the PSM-width/anchor rule.
- Some component/support callouts use a separate direct composite relation: four type-5 frame edges share a composite parent and have child references `text graphic_ref + 1` through `+4`. Recover the frame from that sequence before using spatial matching. Treat edges as horizontal/vertical within two page units because uint16 composite coordinates can quantise an otherwise straight edge by one unit. This was verified for `S3`, `SD010`, and `PS-100-00742` on LN `Sheet1046`; do not apply it unless all four sequential child references close a real rectangle.
- Preserve printable engineering symbols when filtering raw Sheet text. In particular, ASCII `\"` is the inch mark in labels such as `SD010 1/2\"` and `SD010 1\"`; excluding it silently creates an empty SHA frame even though both text and its direct composite frame exist. Retain a constrained printable whitelist so binary false positives are still excluded.
- Do not suppress a visible-looking blank frame merely because it has no nearby text. AMSS2 `Sheet6` has an extra four-side ordinary Sheet frame (parent `0x03C3`, children `0x03C2/0x03C5/0x03C6/0x03C8`) whose PDF visibility differs, but it is neither a proven composite text frame nor represented in the decoded PSM space-map child table. Keep such records until PSM visibility/parent semantics are decoded; a generic hide rule can remove real component geometry.
- The local `18/32` line family can contain an offset backing frame duplicated by the visible type-5 composite callout frame. Suppress an `18/32` rectangle only when its parent is `text_ref + 5` and that same text has an independently closed type-5 `text_ref + 1..4` frame. This verified duplicate relation removes displaced empty S/PS boxes while retaining all other `18/32` component details and unproven rectangles. Never suppress the full record family globally.
- Process-note frames can instead be visible only in the `18/32` family. When one decoded closed `18/32` rectangle contains at least two direct Sheet anchors whose text begins `CLASS`, `INSUL`, or `TRACE`, use each matching Sheet anchor as its baseline and retain its PSM envelope only for glyph height and width. If nested candidates exist, choose the smallest containing rectangle. This is a SHA-only group relation observed for 295 notes in the first 38-page batch; never apply it to a single note anchor or to ordinary free text.
- Dense flange/valve junctions can mix verified visible component outlines with additional `18/32` two-point and type-5 composite strokes. In AMSS2 `Sheet34246`, only 68 of 1,286 `18/32` children have a local PSM candidate and their status-like values 5/6 are also used by visible geometry. Do not filter this family from a PDF density comparison or status value alone; decode `PSMspacemap`/`PSMroots` parent semantics first.
- A valid later-physical-Sheet `18/32` line record stores a 32-bit `page_layer_ref` at byte `14`, in addition to child id at byte `6`, graphic/sheet reference at byte `10`, style id at byte `20`, and coordinates at byte `24`. For every later physical Sheet in the ten-SHA audit, every such reference resolves exactly to one object reference in that Sheet's 92-record `PSMcluster0` named-layer group. Emit the resolved name (`PIPE`, `FITTINGS`, `DIMLINES`, `ISOTEXT`, `FRAME`, etc.) as provenance/trace metadata. This is a direct layer-membership relation for the decoded `18/32` records, but it does not establish visibility, and it must not be applied to `Sheet6` or another unvalidated record family.
- A zero-length `18/32` record with identical start/end coordinates remains a valid page-layer member, not a drawable line. The ten-SHA audit contains 23 such point records. Preserve them in hierarchy/count reporting so `field_3` can be reconciled, but do not emit a zero-length SVG line unless a separate point-symbol decoder establishes its rendering.
- The later-physical-Sheet `0x004d` UTF-16 text family is fully bounded by `uint32 record_length@2 = 60 + 2 * uint16 character_count@28`; text begins at `+30`, followed by the normalized text transform. Its `page_layer_ref@14` follows the same direct per-page group relation: all 4,548 validated records in the ten-SHA audit resolve to `ISOTEXT`, `DIMTEXT`, `DIMLINES`, `FRAME`, or `MATLIST`. Use this to emit text-layer provenance, not to hide labels or infer a font/style not yet decoded.
- A second bounded `0x004D` text layout uses `record_length = 68 + 2 * character_count@28`, with eight bytes at `+30` and UTF-16 text beginning at `+38`; it preserves the same child, secondary, page-layer, style, and normalized transform fields as the normal layout. The full 459-SHA corpus has 1,383 such extended records alongside 123,964 normal records. All 1,383 have `uint16@30 = 1`, `uint16@36 = character_count@28`, and `uint32@32` exactly equal to the resolved `font_style_ref` of their `style_ref@20`. Emit these as explicit font-style redundancy validation plus raw bytes, not as an alignment, visibility, or geometry rule.
- In that same bounded `0x004d` text family, the actual `style_ref` is at byte `+20`; byte `+14` is demonstrably the page-layer object and must not be used as a style. The renderer corrects this only for records satisfying the full length and character-count equation, while retaining `page_layer_ref` separately in the trace. `secondary_ref@10` has two proven target categories in later pages: it is either the current numeric Sheet id or a structurally bounded `0x007b` composite object ref at byte `+6`. Across the ten-SHA audit all 4,548 records fell into those categories (4,193 current-Sheet, 355 composite). Use it as SHA grouping provenance, not a visual-style rule.
- A bounded `0x007b` composite record has `record_length@2 = 32 + 14 * child_count@22`, a composite object reference at `+6`, and current numeric Sheet reference at `+10`. All 5,491 later-page records in the ten-SHA audit satisfy that Sheet relation. Do not use `+2` as a parent/object reference; it is the record length. Type-5/type-6 child drawing remains unchanged, while the actual `+6` parent enables direct grouping of coordinate-note text and composite children.
- Valid later-physical-Sheet `0x0059/0x002b` circle records carry `primitive_ref@6`, `graphic_ref@10`, `page_layer_ref@14`, `style_ref@20`, and normalized centre coordinates at `+24`. All 159 audited records resolve to their own page's named-layer group: 144 `WELDS`, 13 `ISOTEXT`, and 2 `PIPE`. This supplies direct SHA provenance for weld dots and circular symbols, but classify the visible symbol from its primitive/PSM evidence rather than assuming every circle is a weld.
- The `0x0059/0x002b graphic_ref@10` link also resolves directly to a dynamic-attribute UCI for 146/159 audited records: all 144 `WELDS` and both `PIPE` records, while the 13 `ISOTEXT` records do not. For a WELDS-layer ellipse, emit the direct chain `ellipse primitive -> graphic_ref -> dynamic UCI` with its SHA centre; this is sufficient to bind an existing weld dot to a model entity, but it does not invent a weld number or a PCF relationship.
- A full 386-SHA / 1,032-physical-page direct-identity audit confirms that this is not limited to weld dots: exact `graphic_ref -> dynamic UCI` matches cover 592,285/960,386 bounded `18/32` lines, 5,373/5,588 `0x0059/0x002B` circles, 1,405/1,624 `0x0061` arcs, and 5,332/5,374 `0x0013/0x0063` circle companions. Attach UCI only for an exact ref match. The remaining primitives retain their independently decoded geometry/layer provenance; never borrow a UCI from a nearby line, envelope, or composite parent.
- The bounded `0x0013/0x0063` 99-byte record is the exact circle-geometry companion for a WELDS ellipse, not the 172-byte line-layer relation. It stores centre/radius/start/end angles at `+35`, a duplicate centre anchor at `+76`, and one relation-209/flag-5 ellipse child ref at `+97`. All 5,374 corpus records resolve through that explicit child to the same-graphic/layer/centre `0x0059/0x002b` ellipse. Primitive-plus-one is common (5,002 records) but not a binding requirement. The 43-byte `0x0059/0x002b` ellipse already stores its native centre/radius at `+24/+32/+40`; the paired radius is an exact cross-check, not a reason to infer from PSM.
- The fixed 172-byte `0x0013/0x00AC` family is also non-drawing metadata: all 47,901 corpus records contain three segment copies and three `18/32 child_ref`s, and every one of their 143,703 members exactly matches the corresponding Sheet line with endpoints reversed (zero forward or missing matches). Emit this as a reverse-line alias/range group only; render the original `18/32` line once. Its member flags have observed `1,0,0` ordering but are not a line-style or component classification.
- The fixed 65-byte `0x0061` record is a direct circular pipe arc: payload length `59`, then common refs at `+6/+10/+14/+20`, followed by five doubles at `+24` for centre x/y, radius, absolute start angle, and absolute end angle. Its end-angle interpretation is SHA-validated because all 26 audit arcs have both calculated endpoints within 0.04 page units of PIPE-layer line endpoints; treating the final value as a sweep matches only 11/26. Render the minor signed angle path with a style-derived width. All 26 audited arcs are `PIPE` layer; 23 directly resolve to a dynamic UCI and 3 retain geometry/layer provenance only. Do not scan this odd-sized family only at two-byte offsets, or legitimate records will be lost.
- The valid `0x13/0xAC` form is fixed at 172 bytes, not merely a four-byte marker. It stores `primitive_ref@6`, `graphic_ref@10`, `page_layer_ref@14`, and an unaligned direction-preserving line endpoint pair at `+35`. Derive a normalized extent only for range lookup; do not reject reverse lines. Across all 386 SHA, all 43,394 bounded records have an `18/32` line with the same graphic and page layer whose endpoints match exactly in either direction. Emit it as redundant line-geometry plus layer provenance; it supplies no additional visible stroke or visibility rule. Reject raw marker hits with invalid length/coordinates as nested-payload false positives.
- Its complete payload is three `0x67 + <4d>` endpoint segments at `+35/+68/+101`, a shared `<2d>` anchor at `+133`, and three child entries from `+154`. All 43,394 corpus records have relation code `203`, flags `[1,0,0]`, and three `18/32` child refs whose endpoints match the three stored segments in either direction. Treat it as a fixed three-stroke geometry companion with an anchor, never as a fourth visible stroke.
- Treat the type-5 `text_ref + 1..4` sequence as a frame relation only for verified boxed classes: ISO marker codes, `PS-N...`/`PANDA...` references, `SD...` support labels, and short numeric boxed labels. A free annotation can coincidentally precede four child ids that form an unrelated rectangle (RHO1 `SEE ISO` was a verified false match). Keep free annotations at their direct Sheet anchor plus PSM glyph metrics; CIxx remains the separately observed preceding-frame case.
- Template field anchors are not universally left baselines. LS `Sheet8093` style `0x1FB5` places the `N400`, `N400P3A`, and `80 mm` anchors near the right side of their PSM glyph envelopes. Until StyleCluster alignment semantics are decoded, retain the PSM glyph placement for this family; do not globally substitute the Sheet x anchor based on a PDF visual offset.
- Static `Sheet221` title-block labels can also have repeated `PSMcluster0` refs. For each direct template text anchor, enumerate the same-ref PSM candidates, reject page/container extents above the title-block glyph range, then select the remaining box nearest to the local Sheet anchor. This recovered N491 `OF` (`0x0959`) and AMSS2 `PID NO.` (`0x1168`) without using another drawing or a PDF as a metric source.
- For a physical-Sheet text coverage audit, require a finite normalized direction, ISO character set, nonzero graphic reference, and local plausible PSM glyph envelope. Exclude `Sheet221` labels that are intentionally emitted through the shared template layer and extraction-date metadata. Compare the remaining exact text against the generated SVG; do not treat raw byte substrings or `graphic_ref=0` one-character records as visible labels.
- A `PS-N...`/`PANDA...` reference or component marker can point to a page-scale PSM parent envelope rather than its own glyph extent. Before rejecting such an envelope, test whether the raw Sheet anchor lies inside a directly decoded closed rectangle. If so, use that SHA rectangle as the text boundary and record `sha-closed-frame-replaces-psm-container`; do not recover text from PDF. This must remain limited to the observed boxed-reference patterns and a real decoded frame.
- A short but legitimate page-local Sheet label can also point to a PSM container. Recover it only when every SHA-only guard holds: text length is 3--10, the Sheet anchor is normalized, graphic/style references are local 16-bit values, and at least three same-Sheet same-style peers have ordinary 30--320 page-unit PSM glyph heights. Use those peers' median anchor offset, glyph height, and per-character width. Do not apply this to one/two-character values, unbounded references, or an arbitrary style fallback: those patterns include binary false positives such as `{f` and `1`.
- For ordinary Sheet two-point primitives, do not flatten all strokes to one SVG width. A verified `StyleCluster` record headed by `0x002E,0x0036` stores the matching line-style id at byte `20` and its normalized width ratio at byte `40`; when that id equals the Sheet record's `style_ref`, render `ratio * 16800` page units. Keep template and composite strokes at a documented fallback unless their separate style linkage is proven.
- `StyleCluster` text font resolution is now direct: a bounded `0x002C` record has `style_ref@20`, font-size ratio `double@48`, UTF-16 font-name count `@74`, and exact `record_length = 70 + 2*count`. A fixed 90-byte `0x002D` record maps rendered Sheet `style_ref@20` to that font-style ref `@44`. In the ten-SHA audit this resolves all 6,350 bounded `0x004D` text records with no missing style: for example `0x00F4 -> 0x00E3 -> Courier New / 0.005`, and `0x00FB -> 0x00FA -> Arial / 0.0028`. Use this chain for font family and nominal size; retain PSM glyph envelopes for exact rendered width/height until font metrics are fully matched.
- Later physical Sheet streams can use a second two-point family headed by `0x0018,0x0032`: its child primitive id is at byte `6`, style id at byte `20`, and coordinates begin at byte `24`. It uses the same proven StyleCluster line-width table. Decode this family before treating a multi-page ISO's direct vector layer as unavailable.
- Preserve a raw `18/32` segment below the normal four-page-unit floor only when its child ref is independently present in a structurally bounded composite type-2 record. This exact SHA-only link recovers small node/arrow/component details while retaining the global floor against binary false positives. Never admit a short 18/32 segment merely because it is near a PDF-visible detail.
- Do not promote composite child tag `0` to a generic line primitive. A full 459-SHA audit exhausts all 47,856 type-0 children: 42,160 are exact bounding headers for a contiguous sibling range (41,694 ordinary geometry groups plus 466 text-group records that also contain a geometry-range header), and 5,696 directly pair a composite parent with `0x004D secondary_ref` and a type-0 child with that group's text `child_ref`. Thus type 0 is a non-drawing range/group companion, including text-range companions; drawing its four bounds as a line duplicates or invents component strokes.
- Do not render composite child tags `2`, `10`, `11`, `16`, or `21` as additional rectangles or lines. In the expanded 459-SHA audit, `type-2` aliases 101,509 `18/32` lines plus four `0x0059/0x002B` circles; in every case the composite object ref `+6` equals the linked primitive's graphic ref. Other aliases also close against an existing primitive with the same composite parent: `type-10` is 8 lines; `type-11` is 4,617 lines plus 688 `0x0061` PIPE arcs; `type-16` is 1,175 lines plus 176 arcs; `type-21` is 82 lines plus 12 arcs. All are zero-unmatched. Treat them as auxiliary spatial/segment metadata for already decoded geometry; they do not introduce an additional visible primitive.
- Although types `2`, `11`, and `16` now have a proven alias/reference target, their own four uint16 coordinate fields are still not an independent endpoint or bounding-box format. Preserve those fields as auxiliary metadata only; render the linked `18/32` line or `0x0061` arc once, never a second generic stroke.
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

- `Unclustered Dynamic Attributes`: UCI and internal graphic references.  A
  bounded `PipeLine Info` component subtype has four independently sized
  `0x1080` key/value blocks: `PipeLine Reference`, `Fly Text`, `Unique
  Component Identifier`, and `Element Tag`, followed by `0x0089`.  Treat the
  four keys as one component attribute group, not as four visible text
  objects.  The `0x1080` block size is validated; the `0x0089` footer-size
  field is sometimes a record-length match and sometimes an opaque capacity
  value, so do not use it alone as an object boundary.  The terminal
  `PipeLine Info` occurrence can run into unrelated document attributes and
  remains inventory-only without a local bound.  In the ten-sample audit,
  every bounded GUID value and its record-tail `graphic_ref` exactly matches
  the existing `dynamic_graphics()` UCI mapping; emit that validation before
  using the attributes as model-identity metadata.  The fixed companion
  `0x0003 + uint16(18) + "UCI Index" NUL + uint32` follows the UCI property.
  Nonzero values occur only when that UCI has multiple graphic records, but
  the index is not a universal sequence: among 838 duplicate-UCI groups in the
  386-SHA audit, only 216 are exactly `0..n-1` and many are all zero. Retain a
  nonzero value only as an auxiliary duplicate-UCI marker, never as a spatial
  point, physical asset identifier, or stable one-per-instance code.
  Across the same samples, nonempty `Fly Text` values have zero exact matches
  to decoded physical-Sheet `0x004d` text, while `Element Tag` values commonly
  do match.  Treat `Fly Text` as component-description metadata; only use an
  `Element Tag` match as a candidate text-identity link, never as its position.
- A compact `_ISO` subtype is independently bounded as `_ISO NUL + 0x0089 +
  uint32 size/reference + eight zero bytes + uint32 candidate graphic_ref +
  0xFFFF`. In every audited SHA, all but one record use size 30 and one uses
  size 36; more than 99% of candidate graphic refs resolve to a PSMcluster0
  envelope. It is dynamic-object-to-PSM routing metadata, not a direct Sheet
  primitive, visible text, or a license to infer geometry from its ref alone.
- The only other `0x1080` keys in every ten-sample export are one each of
  `Draw`, `Schematic`, `FileName`, and `MuSuStr`. They are document/export
  configuration (including a source specification path), not repeated
  component attributes or drawable ISO annotations.
- `PSMcluster0`: rendered spatial envelopes, page hierarchy, levels, and object metadata.
- The remaining object-space hierarchy target is the mixed `PSMspacemap/0x00000000` layout and its relation to `PSMsegmenttable`. `PSMroots` and `PSMclustertable` are verified name/stream registries, not component-geometry indexes.
- In the examined ten-SHA set, `PSMspacemap/0x00000000` has a zero-terminated prefix of type-2/type-3 records using the ordinary `<4H> + <IH>` relation layout, a complete middle sequence of separately bounded type-2/type-3 relation containers and zero-relation lists, and a distinct tail type-3 root block with one or two `190`/`201` children. A sliding scan can find type-1-like 14-byte patterns inside those payloads, but they are not independently framed records. Do not promote them to a layer hierarchy or use any part of this map to render geometry without a direct Sheet relation.
- `0x0000` now has three independently bounded layouts across all 386 audited N400 files: 171 direct counted-node tables, 152 complete mixed-relation layouts, and 63 prefixed counted-node tables. The mixed count includes six exports with a bounded split type-7/type-8 prefix continuation; its regular relation stream starts after that continuation, not at the first terminator. Two former exceptions are a counted-table double-child tail combining `182 -> 559` template routing and `190` dynamic-attribute routing, not a new node format. The prefixed form's fourth uint16 header field is exactly the number of uint16 values between the header and the ordinary node table; the first header field then exactly declares the node count. Always prefer a complete mixed parse when it succeeds. The prefix values are raw namespace indexes only, never primitive refs, coordinates, or geometry.
- The prefixed uint16 table now has a bounded negative classification: across 63 files, all 26,219 values are unique, at or below header field three, and have zero matches to currently decoded visible Sheet references, UCI graphics, `0x0089` dynamic attributes, named layers, validated space-map nodes, or text styles. Emit `reserved-or-unbound-local-id-inventory` with these counts. It is not a drawable object list, but there is not yet write-operation evidence to call it an allocator free list.
- In counted `0x0000` node tables, use the ordinary `<uint32 ref, uint16 relation>` payload only unless a node family is independently proven to use the alternative `<reserved:uint16, relation:uint16, child_ref:uint16>` form. The alternative is proven for type `1`, `node_id in {2,3}` with `type >= 2`, and `type >= 170`; its header's fourth uint16 is `parent_ref`. Emit an edge only for `reserved=0` and relation `181/182/183/184/190/201`; this is routing evidence, never geometry.
- With the parser's actual layout priority (171 counted plus 63 prefixed-counted files; do not double-count the 152 complete mixed files), those triple-form families yield 184,593 type-1 relations, 30,019 low-type local-table relations, and 39,804 type-170+ relations. Retain `234` type-1 and `226` type-170 nonzero `0,0,local_ref` rows as `zero_relation_extension_refs`, not edges. `node_id=3,type=0` is a distinct all-zero-relation anchor list; retain its 1,407 nonzero local refs as `zero_relation_list_refs`. None of these local refs is a component, coordinate, or SVG primitive without a direct validated Sheet relation.
- The nonzero zero-relation extensions have bounded target meaning: type-1 targets PSM named `Level 5` in 233 files and `Level 42` in one special file; type-170 targets PSM named `Default` in all 226 occurrences, each with one zero-target companion. They are hierarchy/default-state references. In the 234 counted-layout files, the type-0 anchor list has six fixed template/frame refs in 231 files; three files add one ref that exactly equals a later physical Sheet local-id start (`Sheet5222`, `Sheet4779`, or `Sheet6053`). Treat that seventh ref as a content-space root, never a pipe split point or drawable component.
- Across the 171 counted plus 63 prefixed-counted files, target namespaces give the relation codes bounded routing meanings: `181` targets JSite resources in 19,197/20,381 rows; `182` targets Sheet local roots in 19,899/22,719; `183` targets named PSM layer/hierarchy objects in 39,589/42,426; `190` reaches bounded `0x0089` dynamic attributes in 14,524/127,366 and dynamic graphic refs in 5,847; `201` reaches dynamic graphic refs in 2,621/42,918. `184` remains mixed. The primary page-template routes are `parent=238: 181 -> 559` (19,197) and `182 -> 398` (19,662). These are hierarchy routes only, never render instructions or piping topology.
- `PSMspacemap/0x00008000` has 69 ordinary full-node tables, is absent in 315 compact exports, and has two fully decoded prefixed-index variants. AMSS1 has eight prefix values and 1,501 ordinary nodes. SWS4 has 93 prefix values and 2,271 ordinary nodes separated by four all-zero padding runs of lengths `12/6/12/6`; skip those runs only at a record boundary, never as `id=0,type=0` nodes. Both consume their streams exactly. Prefix values remain non-drawable local inventory.
- `PSMsegmenttable` is a validated spacemap-presence/index table: across all 386 files, every nonzero payload slot has its corresponding `PSMspacemap/(slot * 0x2000)` stream and every zero slot lacks it. Use it to enumerate present map segments only. The observed nonzero byte values `1` and `9` have no directly proven component, layer, or visibility subtype meaning.
- The root-named `0x2000` Dynamic Attributes Set Table is a bounded internal index sequence, not a direct dynamic-attribute record list: its values have zero intersection with every bounded `0x0089` attribute reference across 386 files. They have unstable numeric-only intersections with Sheet refs, PSM envelopes, named objects, and dynamic graphics; without a stored relation edge, never promote those equal integers into a binding or use them to place geometry/text/UCI.
- The 23 one-child tail roots occur only in mixed or ambiguous samples, not in the counted-only layout. Fourteen `relation=190` targets are exact dynamic `0x0089` attribute references. Nine `relation=201` targets are not validated node ids: eight are `0x8000/0x8001/0x8002` within an existing `PSMspacemap/0x00008000` segment, and one `0x1FFF` lies in `0x00000000`. Emit the latter only as `spacemap-segment-address` plus raw offset; their primitive, layer, page, and business semantics remain unresolved.
- The `0x0000` middle begins with a second relation layout: `<record_type, child_count, repeated_count, parent_ref>` followed by `child_count` `<reserved=0, relation, child_ref>` entries. Its type-2 one-edge records and type-3 batch records continue references from the high-level prefix. In nine regular samples, a fixed 20-byte zero-target variant occurs exactly once and is followed by the same ordinary containers; this recovers 890--1,532 containers and 1,149--1,838 edges before the later zero-child control block. AMSS1 is a separate leading variant. These are PSM hierarchy edges only, not Sheet primitives or render instructions.
- The later zero-child control block is now bounded too: `type=3, child_count=0, repeated_count=N, parent_ref=0`, followed by `N` `<reserved=0, relation=0, child_ref>` triples. Combining ordinary containers, the one zero-target variant, and seven zero-relation lists consumes the complete middle region of all nine regular samples up to the independent tail-root block. The zero-relation child-ref semantics remain unresolved and must not be turned into geometry.
- In the examined ten-SHA set, the first six nonzero refs from the seven zero-relation lists are fixed `0x0CAA`, `0x0F05`, `0x0F1E`, `0x110F`, `0x112C`, and `0x1163`. Each resolves to the same `PSMcluster0` page/template envelope across samples: right-panel divider, panel side/top/bottom borders, and page-level frame. Treat them as shared template-space anchors, not piping components. The seventh ref varies per drawing but exactly equals a later physical `Sheet*` header's local-id start (for example `0x1797 -> Sheet6035`); it is a cross-Sheet content-space root reference, not visible geometry.
- The hierarchy report classifies each `0x0000` relation edge target by proven namespace membership. Across the ten examined SHA files, relation `182` targets a physical Sheet header local-id start in `861/957` cases, while `190` has the strongest PSM/dynamic-object overlap (`8,035/11,505` PSM envelopes and `765/11,505` dynamic refs). These are evidence-backed routing tendencies, not a license to rename every relation code: `181`, `183`, `184`, `190`, `201` still have mixed targets and must retain their numeric code in output.
- In the regular `0x0000` export variant, the page/template portion is now bounded further: repeated `181` records route `parent_ref=238` to shared JSite `559`; `182` repeatedly routes that same parent to the `Sheet6` root; and `183` target sets substantially overlap the known base-175 or later per-page-92 named-layer groups. Sparse `184` rows link the shared `Level 01`/`Level 27`, subsequent-page container, `Default`, and `01` layer objects. These are page/template routes only. AMSS1 omits this repeated segment, so never require it as a universal SHA invariant.
- AMSS1 uses the same full relation sequence beginning at byte `138`, which consumes exactly to its tail-root block (1,886 containers and 2,193 relation edges). Its preceding bytes `92..138` are a split high-level-prefix continuation: four padding bytes, node 7 (`4703/184`), node 8 (`7/183`, `9/182`), and a second zero terminator. Preserve this layout as an AMSS1-specific storage variant; it is not a generic relation-container layout.
- The short `PSMspacemap/0x00002000`, `0x00004000`, and `0x00006000` streams use a distinct exact-length form: `tseg`, four uint16 header fields, then the number of uint16 values declared by the fourth field. The decoded `PSMroots` directory names `0x2000` `Dynamic Attributes Set Table` and `0x4000` `_SupportOnlyList`; do not replace those names with inferred geometry semantics. `0x2000` carries a non-empty list in the examined set; `0x4000` and `0x6000` are zero-length list variants. Their headers and values are structurally decoded but their object/layer semantics are not.
- Every examined `0x2000` list value numerically falls inside the shared `Sheet221` local-id interval, never `Sheet6` or a later physical Sheet. This is only range overlap: across the ten samples, the values have zero direct overlap with decoded Sheet221 primitive refs and almost no raw Sheet221 byte occurrences (no uint32 hits). They are the non-repeating internal index sequence of the root-named `Dynamic Attributes Set Table`, not Sheet221 objects or global graphic ids.
- Across all 386 SHA, `0x2000` values are unique within each file but have no universal ordering contract: only 231 files are strictly ascending, while the remaining sequences contain 1,448 descending transitions and 3,474 positive jumps larger than one. Do not treat their stored order as an allocator/free-list order, draw order, coordinate order, or hierarchy traversal.
- `PSMroots` has a reproducible UTF-16 directory layout: `rootb`, an eight-byte header, then `uint32 character_count`, UTF-16LE name, and `uint32 root_ref` entries. The observed header byte is `9` while five entries follow, so retain it as `header_count_byte`, not an entry count. In the ten-SHA audit the entries were `TopVFSet -> 0x10BB`, `_SupportOnlyList -> 0x4000`, `Dynamic Attributes Set Table -> 0x2000`, `StyleLibrarian -> 0x0001`, and `DocStore -> 0x0000`. Only the latter three map refs are names/roots; a matching `PSMspacemap` stream exists for `0x0000`, `0x2000`, and `0x4000`.
- `TopVFSet -> 0x10BB` is now located directly in `PSMcluster0`: all 386 SHA have exactly one bounded `0x0067 + uint32(20) + 5*uint32` record whose `object_ref@6` is `0x10BB`. Its remaining raw values are invariant `(0,0,3,0)`. This proves a top-level PSM-set object link, not a page, component, layer, or drawing rule; preserve `role_raw=3` without naming it.
- `PSMroots` references can use more than one namespace. Cross-reference them to `PSMclustertable` before treating a value as an object id: in all 386 files, `DocStore -> 0` resolves to directory entry `PSMcluster0`, and `StyleLibrarian -> 1` resolves to `StyleCluster`. In contrast, `TopVFSet -> 0x10BB` resolves to its bounded `0x0067` object record, while `0x2000` and `0x4000` resolve to `PSMspacemap` streams. These are registry routes, never visible geometry or a drawing instruction.
- Composite `0x7B` children have a second, now validated reference route. In the full 386-SHA / 1,032-physical-Sheet corpus, every one of the `449,504` non-range children resolves in the same Sheet to an `18/32` line (`445,922`), `61` arc (`782`), or `59/2B` circle/ellipse (`2,800`); every target graphic reference exactly equals its composite parent reference. This makes the composite a grouping/backlink around the rendered primitive. Keep raw child type and local bounds untouched: `type 6` resolves to lines, arcs, and ellipses, so it is not a geometry or piping-component class. Type `0` is a local range child (`43,468` occurrences).
- The `0x7B type=0` local range role is now quantified. Of `43,468` ranges, `38,381` exactly equal the union envelope of their direct non-range children; direct member counts are `3` in `35,590` cases, `1` in `2,785`, and `4` in `6`. The remaining `5,087` contain no direct non-range child and are logical/layout ranges. No type-0 range contains another type-0 range in this corpus. Use it for component/UCI local extent or grouping only, never as a line to draw.
- The fixed `StyleCluster 0x2A` records are library defaults rather than physical drawing styles: every SHA has the same four style refs (`3`, `5`, `12`, `16`), and none occurs in any decoded physical `18/32`, `4D`, `59/2B`, `61`, or `13/63` primitive across all 1,032 physical Sheets. Their catalog backlinks are only `Normal` and `ANSI`; retain the `opaque_color_or_flags` field raw and do not treat it as RGB. Every `0x7C` local-resource group member is now classified as a local line, arc, ellipse, polygon, or text-template resource; this is a complete local symbol-library composition graph, still with no verified physical-Sheet placement route.
- Full-corpus audit of nonstandard six-byte payloads in counted `PSMspacemap/0x0000` nodes found only bounded zero-relation controls, local zero-relation references, and layout anchors outside relation codes `181/182/183/184/190/201`. No further stable, Sheet-reference-backed visible primitive family exists there. The remaining unknown PSM meanings are hierarchy/template/control semantics, not omitted lines, circles, text, flanges, or piping components.
- A contentless `JSite` is now decoded as a template route, not an empty object. In all 386 SHA, `JSite559/JProperties` is bounded `OLES` property code `3` with UTF-16 value `"221"`; every one of its 646 `0x3D` placements occurs on a non-`Sheet221` physical Sheet while that document contains `Sheet221`. The renderer/parser may therefore resolve it as `contentless_jsite_template_sheet_stream = Sheet221`, then layer that template's frame/title/revision primitives with the physical Sheet's content. It is not a bitmap or a piping component.
- For `JSite690` and `JSite1402`, use the bounded `Sheet221 0x3D` wrapper fields (`jsite_resource_id`, `placement_origin`, `placement_size`) to place the embedded DIB images. Do not locate them with an arbitrary raw resource-id byte search. The verified wrappers give the two title-template image placements directly and retain their primitive reference for traceability.
- `dynamic_attribute_pipeline_info.physical_sheet_text_value_matches` reports both occurrence and unique-value counts. In the full corpus, all 28,703 `PipeLine Reference` occurrences match physical `0x4D` text only because the one drawing line-number repeats in every component property row; this is not a component-label binding. `Fly Text` has zero direct 4D text matches, while 22,014 of 23,985 `Element Tag` occurrences match a visible text value. A string match remains identity evidence only: text coordinate, style, leader, and layer must come from the independent Sheet records.
- `dynamic_attribute_pipeline_info.element_tag_unique_text_candidates` now emits a candidate only when an Element Tag has exactly one same-SHA physical `0x4D` text record: 20,850 corpus property rows qualify. It includes the Sheet stream, text child/secondary refs, layer/style refs, anchor, and direction. The 1,164 multi-candidate rows and 1,971 unmatched rows remain explicitly unresolved; even a unique text candidate does not prove leader attachment or UCI ownership.
- A bounded `PipeLine Info` dynamic-attribute record is either a component instance or an empty schema stub. Across the 459-SHA corpus, 32,007 records have nonempty `PipeLine Reference`, `Fly Text`, and `Unique Component Identifier` and are `component-instance` rows; 17,441 have all three empty and are `empty-property-schema-stub` rows. A component instance may still have an empty or absent Element Tag. Never count schema stubs as UCI-less components or use their graphic refs as component geometry.
- `dynamic_attribute_pipeline_info.direct_sheet_graphic_bindings` is the stronger UCI route: graphic refs are taken only from bounded `PipeLine Info + 0x0089` records, then joined by exact equality to same-SHA Sheet graphics. This resolves all 28,703 valid UCI graphic records in the corpus. The legacy next-marker scan adds 330 false ASCII `SP1_` refs from trailing metadata and must not be used for binding. Retain every matched family (`18/32`, `59/2B`, `61`, `4D secondary`, `7B`) because composite and base primitives intentionally share one graphic ref; this is membership evidence, not an exclusive component-class label.
- Apply the bounded UCI map in every user-facing path, not just in reports: `sha_to_svg_prototype.py`, `inject_sha_weld_callouts.py`, `number_pcf_welds.py`, and `app_server.py` now all use it. Do not reintroduce the legacy `dynamic_graphics()` next-marker output into rendering, weld placement, or PCF/SHA direct-link counts.
- `0x4D tail_flags_u32` is zero except for one observed `0x01000000` Sheet221 record per SHA: the horizontal Chinese company-name template text using style 217. The parser exposes `observed_non_ascii_template_text_flag` for traceability only. It is not a general Unicode/font/encoding rule.
- Every decoded physical `0x4D` text style ref resolves through the current SHA's StyleCluster font chain (108,530 corpus records). `attach_4d_text_style_resources` adds `font_name`, `font_style_ref`, and `font_size_ratio`; unique Element Tag text candidates expose the font name/ratio too. Resolve this per file: numeric style refs are not cross-SHA font constants.
- Physical `0x004D` text uses `child_ref@6`, not its shared `secondary_ref@10`, to locate its PSM glyph envelope. In the 459-SHA `/管道` audit, 116,053 of 123,964 text records (93.62%) had exactly one envelope in a conservatively bounded contiguous PSM run; no matched text had competing envelopes. Convert both normalized anchor axes using the Shape2D sheet unit: `page_x = x * 16800`, `page_y = y * 16800` (not `y * 11880`). The PSM box is the final glyph extent; `font_size_ratio` is only a requested style metric and must not be substituted for final rendered height.
- `PSMclustertable` is a second, separate `clst` stream directory: two uint32 header values followed by its declared number of entries. Each entry is `uint32 UTF-16 byte length + marker:uint8 + directory_index:uint16 + child_count:uint32 + child_count uint32 directory indexes + NUL-terminated printable UTF-16LE stream name`. In the ten-SHA audit the marker is `1` and `directory_index` equals entry order. The parser fully consumes each stream and every listed name exists as an actual OLE stream. `PSMcluster0` has a small child directory list (itself and zero or more early `Sheet*` entries); ordinary Sheet entries have no children. This is stream-level containment, not ISO-page visibility, component identity, or geometry.
- `PSMsegmenttable` is `stab + uint32 payload_count + payload bytes`. Payload index `i` is the segment tag for `PSMspacemap/0x(i*0x2000)` when that stream exists; trailing unallocated slots are `0`. This alignment holds across the ten-SHA audit, including `0xA000` and `0xC000` when present. Tags `1` and `9` are not yet decoded as type, visibility, or geometry semantics, so preserve them as raw segment tags only.
- `PSMcluster0` contains a separately bounded `0x0081` named-record family: `uint16 0x0081 + uint32 record_length + 5 uint32 fields + UTF-16LE name + NUL`, with `record_length = 30 + 2*(name_char_count+1)`. It recovers ISO layer-like names such as `PIPE`, `FITTINGS`, `WELDS`, `DIMLINES`, `MATLIST`, `ISOTEXT`, `SKETCHES`, `NOZZLES`, and `Level n`, plus a per-record object reference and entry id. The third middle field (`field_3`) is now decoded as the declared member-record count for this page-layer object: across all 3,404 page-layer records in the ten-SHA audit it exactly equals the count of decoded Sheet records assigned through `page_layer_ref@14`, including zero-length 18/32 point records and empty 4d objects. `field_1` and `field_2` are always zero in every audited named record; treat them as observed reserved/unused fields, not layer data.
- The terminal uint16 in the bounded `PSMcluster0 <I5H>` envelope is an internal PSM subtype tag, not a Shape2D primitive or page-layer id. Full-corpus counterevidence: tag `5` occurs on lines, composites, and circles across `DIMLINES`, `FITTINGS`, `ISOTEXT`, and `PIPE`; tag `6` also spans lines, composites, circles, and arcs across `DIMLINES`, `FITTINGS`, `PIPE`, and `WELDS`. Keep it for traceability only; never use it as a flange/valve/weld class, layer selector, visibility flag, or SVG draw rule.
- Some `0x0081` named-record `object_ref` values exactly resolve to a node id in a fully parsed `0x8000`, `0xA000`, or `0xC000` table. Record this direct `named-layer -> PSM node` link in the trace. It is more common in complex multi-map SHA variants and absent in compact ones, but still does not prove node-child-to-Sheet primitive membership; do not assign SVG layer names from it automatically.
- For named records with a node link, relation `190` repeatedly targets the verified `0x0089` dynamic-attribute reference family. Relation `183` is bounded container-to-named-layer membership and relation `184` is polymorphic routing that must retain parent context. In the full-prefix `0x0000` layout, every relation `201` is now a geometry-companion route: it resolves to either a three-line `0x0013/0x00AC` group or a one-circle `0x0013/0x0063` group. Do not generalize that `201` meaning to other PSM layouts without the same structural proof.
- In the standard/prefixed-counted `0x0000` layouts, relation `201` has a different validated scope: after filtering to dynamic UCI graphic ids, all 5,863 distinct targets across the 386-file corpus directly equal a reference in a decoded same-SHA Sheet record. Matches include line, circle/circle-companion, arc, `0x007B` composite, and the shared `secondary_ref` of three-line `0x004D` coordinate-text groups (E/N/EL). Emit this as `dynamic-graphic-to-Sheet-record-route`, not as a component class, a UCI-to-single-geometry identity, or a global meaning for relation 201.
- In the ten-SHA audit, `PSMcluster0` named-record count exactly follows `175 + 92 * subsequent_sheet_count_after_Sheet221`, where the count is the number of `Sheet*` directory entries after `Sheet221` in `PSMclustertable`. `Sheet6` is the first ISO page and is not part of this repeated group. Use this as a SHA-only multi-page integrity check: 175 is an observed fixed base and 92 is an observed per-subsequent-page group size. Group `i` maps in directory order to subsequent Sheet `i`; its member refs are now directly used only to label validated `18/32` line records on that same Sheet.
- The fixed first 175 named records are also the direct `page_layer_ref@14` target group for `Sheet6`: all 23,592 decoded Sheet6 records across the ten-SHA audit resolve to it. In every sample, 164/175 `field_3` values exactly reconcile to Sheet6 member records. The stable remaining 11 are template/default objects (`Default`, numbered low layers, `Border`, `DwgTemplate`) with no Sheet6 member and must not be forced into the visible ISO layer. Emit this as `sheet6_base_named_layer_group` separately from the subsequent-page 92-record groups.
- Combining `Sheet6` with the bounded shared-template records in `Sheet221` (`0x0018`, `0x004d`, `0x003d`, `0x0084`, all retaining `page_layer_ref@14`) reconciles 174/175 fixed base records in every audited SHA. The sole nonmatching `Default object_ref=0x0008` has no Sheet primitive and occurs only in PSM/document metadata contexts; mark it as a non-drawing default/container object. Emit this combined view as `shared_base_named_layer_group`; do not extract geometry from its unresolved template/resource members.
- `Sheet221` has one stable `0x0084` record (104-byte payload, layer `60`) in all 386 audited N400 SHA files. It always contains the same closed five-point normalized template-page path from `+30`; retain it as a page/container path, never as a process component. The two stable `0x003D` records (234-byte payload, `Border` layer) have direct `JSite` bitmap resource ids at `+162` (`1402` and `690` respectively). Their normalized placement origin is `<2d>@42` and size is `<2d>@82`; the resulting aspect ratios exactly match the embedded BMP-DIB payloads (`1402: 537x212`, `690: 866x498`). A SHA-only exporter can therefore extract/embed them at the wrapper box. `+20` is opaque metadata, not a decoded style.
- The same bounded 234-byte `0x003D` wrapper also occurs on physical Sheets. Across all 386 N400 SHA, there are 646 physical-Sheet wrappers and every one references the contentless external `JSite559` at `+162`; each Sheet6 begins with the canonical wrapper `primitive_ref=398, sheet_ref=6, page_layer_ref=8, JSite=559`. Its `<2d>@42` origin and `<2d>@82` size provide a near-page-size external-container box. Emit this as a page-level external-OLE placement relation, not a pipe component, UCI, or drawable bitmap. The repeated placement frame and identity matrix are decoded above; only raw scale/trailing origin semantics and unseen non-identity transforms remain open.
- In the 323 files whose `PSMspacemap/0x00000000` has the standard counted-node form, the zero-relation anchor list always resolves its six stable refs directly to `Sheet221`: five `0x0018/0x0032` template boundary/divider lines (`3242,3845,3870,4367,4396`) and the `0x0084` page path (`4451`). 169 files have only those six anchors; 154 also contain one valid physical-Sheet root. Emit these as `sheet221-template-primitive` or `sheet-content-root`, never as pipe components or UCI targets.
- Long `Sheet221 0x004D` records are not a single text layout. The bounded normal form has visible UTF-16 text at `+30`, while a Revision subtype carries a UTF-16 XML `intstgxml` binding after a short opaque prefix and a Sheet text transform in its record tail. Emit the binding's `stream`, `select`, `alt`, XML offset, raw prefix hex/uint16 values, and transform offset. Across all 386 N400 SHA, all 9,650 decoded bindings target stream `Revision`; their `select` paths use the six leaf fields `MajorRev_ForRevise`, `RevisedBy`, `RevisedDate`, `CheckedBy`, `ApprovedBy`, and `RevisionDescription` against a particular history row (`[1+n]` or `last()-n`). Resolve those two selector forms against `TaggedTxtData/Revision`; when the requested history row or field is absent, use the binding's explicit `alt` text (normally empty) and mark it as an alt resolution rather than an error. The eight-byte prefix is a stable layout discriminator, but its business meaning is not proven. Template note transforms can carry a scale such as `0.001` rather than a unit direction, so retain their raw values. Report XML as a Revision-field binding and resolve its value through the Revision stream; never render XML literal. Both forms retain direct `child_ref@6`, `sheet_ref@10`, `page_layer_ref@14`, and `style_ref@20` provenance.
- In the fully bounded middle sequence of `PSMspacemap/0x00000000`, relations are routing evidence, not primitive types. Across the ten reviewed SHA files, relation `190` targets `_ISO` dynamic-attribute records 1,398 times and direct UCI graphics 765 times; relation `201` directly targets UCI graphics 113 times. Relation `182` is instead dominated by physical Sheet header roots, with only 22 dynamic-attribute exceptions (`_ISO` or `Element Tag`). Preserve these counts and original relations in the hierarchy report, but never draw a component from the relation code alone.
- In the mixed `0x0000` hierarchy, distinguish the `Sheet123` stream id from the Sheet header's local-root id: they are separate namespaces and can differ by a small file-local offset. The parser now reports `relation_code × parent_namespace × target_namespace`, including physical-sheet stream/local-root, named-layer, JSite resource, and run-bounded PSM envelope. This proves local routing contexts such as `unclassified PSM container -> named layer` without falsely naming the PSM container itself as a Sheet or piping component. Relation codes `181/182/183/184/190/201` remain polymorphic across contexts.
- `PSMcluster0` has three additional page-level object families. `0x0042` is a self-sized page-layer container (`record_length = 72 + 4 * member_count@26`, member refs from `+30`); its member set exactly matches the page's `0x0081` named-layer set. Its `member_anchor_ref@22` is always one of that vector's named layers (NOZZLES 726, DwgTemplate 459, Default 10 across 1,195 pages), so emit it as an in-container layer anchor only, never as a proven active/visible layer. The variable vector is followed by a fixed 42-byte control tail: its second uint32 is a direct `page_default_object_ref`, and all `1,195/1,195` values resolve to the same page's `0x0088 Default` object; that object's `page_parent_ref@26` is a named `0x0081 Default` member of the container. This closes both direct and indirect forms of `0x0042 -> 0x0088 Default -> 0x0081 Default -> Sheet` association. The remaining tail fields stay raw. It is not a Sheet reference field, visible-layer switch, or component/geometry link. `0x0057` is a bounded page/template-linked display-layer control with a Sheet stream backlink at `+22`: compact `148/176` forms contain `Default`/`DwgTemplate`, while `1756` (one in every SHA, always Sheet221) and rare `1916` forms contain a self-delimiting table of `<UTF-16 layer name, uint16 entry_id>` entries. The table has 82/91 entries respectively and exactly equals the Sheet's `0x0042 -> 0x0081` named-layer set except stable reserved name `05`; every table value equals that same Sheet layer's `0x0081 entry_id` across all 469 long profiles. It is a layer-directory ordering/identity field, not a visibility, freeze, print, color, or geometry state. Across 459 SHA / 1,195 physical Sheets, `0x0042` and `0x0088` occur exactly once per physical Sheet; all 1,195 validated `0x0057` records backlink to a valid Sheet. These are routing/profile objects, not drawable piping geometry.
- `PSMcluster0 0x0089` application-property records are a separate namespace from `Unclustered Dynamic Attributes` `0x0089` footers. All 2,304 corpus records have a bounded outer length (`record_length = 20 + property_payload_length@24`) and fall into `_PastedGraphic`, `MSTN_GLOBALS`, `_ISO`, or `PipeLine Info` application metadata. Every one of the 919 `MSTN_GLOBALS` records has a bounded `FileName` token followed by a `.dgn` source-template path; emit it as application/template provenance only, never as a Sheet coordinate or component property. `_PastedGraphic` has a deliberately narrow resource bridge: only 459/1,377 records have `object_ref - 1` equal to an existing JSite id (all JSite690 with embedded CONTENTS); the other 918 are unbound application objects and must not be inferred as images. The two `PipeLine Info` records expose the schema labels `PipeLine Reference`, `Fly Text`, `Unique Component Identifier`, `UCI Index`, and `Element Tag`; these labels are not the corresponding per-component property values. The 8 nonzero `parent_object_ref@18` rows all resolve to a bounded PSM envelope and a same-SHA Sheet graphic: six composite-plus-line groups and two extended `0x004D` text records. This attaches application metadata to a graphical PSM object only; it never replaces the separately bounded dynamic-UCI-to-Sheet chain or authorizes geometry changes.
- `PSMcluster0 0x0075` is the one-per-SHA top-level document catalog (`record_length=113`). It has a bounded three-entry UTF-16 byte-length table: `SiteObjects -> 2`, `PreferenceSet -> 3`, and `Sheets -> 4`. Treat these values as catalog entry ids, not local Sheet references or graphics. This clarifies why low ids can occur in PSM routing without identifying a pipe component.
- `PSMcluster0 0x0002` is the one-per-SHA fixed PreferenceSet index (`record_length=768`, object `256`). It has 25 eight-byte entries; the first two uint16 values are big-endian and the final uint32 is little-endian. The index content has three observed variants, while its boundary and count are stable in all 459 SHA. It contains application preference names, so retain entry fields raw and exclude it from ISO geometry, UCI, Sheet-local-reference, and rendering paths.
- `PSMcluster0 0x006C` is a one-per-SHA document-default bundle. Its fixed payload nests `0x0088 Default object 9 / parent 8` and a following `0x0037 companion style object 10`; all 459 have the same bounded layout and raw style fields. Object 10 is therefore an embedded default-style payload, not an independent pipe component, Sheet primitive, or relation target to render.
- `PSMcluster0 0x0073` is a one-per-SHA fixed 515-byte global `Background` site-object container. A bounded, 8-byte-aligned child `0x0076` occurs inside it at relative offsets 64..144. Its `+58` UTF-16 field is always `Sketch`; its following UTF-16 field is an optional source-document identifier. Across all 459 SHA, 450 identifiers are empty and 9 exactly match the SHA filename with the final revision suffix removed; child length is exactly `307 + 2 * identifier_character_count` (307/345/349/353/355 observed). This is Background/Sketch document metadata, never ISO pipe geometry, a component id, or an SVG draw instruction; retain the remaining numeric payload raw.
- `PSMcluster0 0x0064` is a one-per-SHA fixed all-zero control slot: `0x0064 + uint32(101) + 101 zero bytes`, total 107 bytes. All 459 SHA contain exactly one; only its stream offset varies with preceding optional document data (589/591/593). It has no object reference, text, geometry, component identity, or rendering role, so preserve it only as a document-integrity/control placeholder.
- The document-level `0x0065 + uint32(114)` site contains a bounded `Section1` output-page directory in every SHA (relative name offset 89/91/93). After `Section1`, fixed bytes `02 01 00 00 00`, and a uint32 page count, it stores counted `<uint16 UTF-16 length, Sheet1..SheetN, byte 1>` entries followed by `Backgrounds`. The final `s` of `Backgrounds` overlaps the following `0x0073 Background` marker, explaining the apparent record overlap. Across all 459 SHA, the directory count exactly equals the number of `0x0042` page containers excluding shared template Sheet221: `Sheet1` maps to Sheet6 and subsequent ordinal names map in container order to subsequent physical Sheet streams. These Section labels are ordinal output-page directory names, not OLE stream names, coordinates, components, or drawing instructions; outer 0x0065 numeric fields remain raw.
- In the 161 complete mixed-layout files from the 459-SHA `/管道` corpus, endpoint classification reduces the large `190/201` target pools without inventing semantics. `190` targets: 101,602 run-bounded PSM envelopes, 23,252 decoded Sheet `graphic_ref`s, 22,889 bounded `0x0089` dynamic-attribute refs, and 8,005 tseg node ids. `201` targets: 21,804 decoded Sheet `graphic_ref`s, 6,266 PSM envelopes, and 4,183 tseg node ids. These are exact same-SHA namespace matches, but an edge still becomes a component identity link only under the separately validated UCI/Sheet chain.
- Most remaining `190/201` targets are not a separate PSM object family: they are exact Sheet-local primitive refs (`18/32 child_ref`, `0x004D child_ref`, `0x0059/0x0061 primitive_ref`, `0x007B` composite child ref, or `0x003D` JSite-placement primitive ref). The final five exceptions were two `201 -> 0x003D` JSite559 template-placement refs on CW sheets and the same local-reference class. The complete mixed-layout audit now has no unclassified `190/201` target. A local primitive ref proves routing to an already decoded Sheet object, not an additional stroke, component type, or UCI identity.
- A stricter visible-component chain is now proven for `UCI graphic_ref -> validated 0x8000 node -> relation 201 -> exact Sheet composite type-0 child -> same-composite type-5/type-6 siblings inside the type-0 bounds`. The original 38 reviewed rows all landed on type-0. A full 386-SHA audit finds 297 additional visible-child/UCI pairs on 23 physical Sheets, and every one is type-0 propagation: relation 201 directly hit an ordinary visible child zero times. Type-0 remains non-drawing, but is a verified UCI-to-composite binding entry; propagate its UCI only to those bounded same-parent visible siblings, never to adjacent geometry or an outgoing leader.
- The 92-record page groups can now be mapped to a specific physical Sheet in order: group `i` maps to the `i`th `Sheet*` after `Sheet221`; every observed group has the same name sequence and `min(object_ref) = Sheet header local-id start - 2`. Emit this `physical Sheet -> named-layer group` mapping. It identifies per-page hierarchy objects, not yet individual SVG primitive membership.
- For every examined empty `0x6000` list, header field 1 exactly equals the count of `0x0089` records in `Unclustered Dynamic Attributes`. A full 386-SHA recomputation gives header field 2 minus field 1 reserves of 145 (382 files), 147 (1), 168 (2), and 175 (1); rare values occur in SWS4/AMSS1 export variants. Thus `0x6000` is a dynamic-attribute count/capacity control record, not a Sheet221 local-id range, visible-object list, or geometry filter. The configuration source for this reserve remains unresolved.
- A SHA can legitimately omit `PSMspacemap/0x00008000`. Two RHO1 samples retain `0x0000/0x2000/0x4000/0x6000` and the same `PSMroots` directory without it. Mark this as an export variant and continue decoding the present streams; never treat its absence as corruption or manufacture a `0x8000` hierarchy.
- UCI-bearing records are only one subtype of `Unclustered Dynamic Attributes`. A second verified attribute-reference field is `0x0089 + uint32 record_size + uint32 reference`; it follows `_ISO` and `Element Tag` payloads. The compact `_ISO` subtype has sizes 30/36, while `Element Tag` records can be larger. Of 15 root-node `190`/`201` child edges, 12 hit this general field: nine `_ISO` size-30 records and three `Element Tag` records (149/234/240). None hit the UCI-only `dynamic_graphics()` set, `PSMcluster0`, or a validated space-map node id. The remaining three are explicit space-map-base selectors: `0xC001 -> PSMspacemap/0xC000` and `0x8002 -> PSMspacemap/0x8000`; retain offsets `1`/`2` without naming their selector semantics. This fully classifies the observed target form, but not their visible-object meaning.
- Across all 386 SHA, bounded `PipeLine Info` component groups have the three mandatory keys `PipeLine Reference`, `Fly Text`, and `Unique Component Identifier` (44,363 records); 44,348 also carry `Element Tag`. The 15 valid three-key exceptions all occur in `N400P3A-HOSO-N419601-01-0.sha` and each has its own bounded `0x0089` footer, so this is a legal no-Element-Tag export variant, not truncation. Preserve it without inventing an empty tag.
- A corpus-wide bounded `0x1080` scan found no keys beyond the four component keys and document settings `Draw`, `Schematic`, `FileName`, and `MuSuStr`. Each SHA carries exactly one of the latter at fixed offsets 58/73/137/250 with values `Show`, `Hide`, the PlaceBorderLabel DGN path, and `INTH`. Emit these as document/export configuration header metadata only, never component attributes, visible text, or geometry.
- `PSMspacemap/0x0000A000` and optional `0x0000C000` can use the same fully consumed `<4H> + <IH>` node-table framing as `0x8000`. In the ten original N400 SHA that contain these maps, every `relation=190` target is an exact bounded `0x0089` dynamic-attribute reference: `7,048/7,048` in `0xA000`, and `1,159/1,159` in `0xC000`. Classify them as dynamic-attribute routing extensions, not component or geometry maps. Keep `182/183/184/201` as namespace-only evidence until a direct Sheet primitive boundary is proven.
- For fully parsed extension maps, every relation target is also a `PSMspacemap` segment address: derive `base = target & 0xFFFFE000`, preserve `offset = target & 0x1FFF`, and require the base stream to exist. It is not a node ID. Typical A000 routing is `183/201 -> A000`, `190 -> 6000` (the verified dynamic-attribute area), with 182/184 using `0000` or the current extension segment. This explains why `201` normally does not match a node table, but it still does not identify a Sheet primitive or visible component.
- A decoded PSM envelope is not a fully decoded PSM object. In the examined sample, 229 of 230 dynamic UCI graphic references yielded an envelope from `PSMcluster0`, but `PSMspacemap/*`, `PSMroots`, and `PSMclustertable` still contain the unresolved hierarchy, local transforms, and parent-child routing. Do not claim their role has been fully recovered solely from bbox coverage.
- A validated `PSMspacemap/0x00008000` relation code `201` can provide a direct component identity chain only when its child ref exactly resolves to a Sheet type-0 child and that type-0's same-parent type-5/type-6 children are already decoded. Attach the UCI to those visible SVG children with `mapping_basis=direct_psm_relation_201`; do not change geometry or infer visibility. Codes `190`, `183`, and `184` remain candidate-only even when their refs land in a Sheet local-id interval.
- Observed `Sheet` composite records beginning with `0x7B` hold child primitives at double page resolution. `primitive_type=5` stores a direct two-point segment; `primitive_type=6` stores an arc envelope. Decode both before concluding a component outline is unavailable.
- An earlier vector layer may be reused only when it is documented as SHA-derived and every imported line's `data-graphic` still occurs in the current SHA dynamic-attribute table. Strip pre-existing `data-layer`/`data-uci` attributes before adding current provenance; duplicate XML attributes invalidate the entire SVG. Preserve the imported line's original coordinates and attach the current UCI. This is a traceable compatibility layer for not-yet-decoded component primitive families, not a PDF-derived replacement.
- `StyleCluster`: style library references; observed content includes font families, size ratios, and line types.
- `JSitesList` is a bounded `OLEM + uint32 count + count uint32 resource ids` stream. In the ten reviewed SHA files it lists `559`, `690`, and `1402`; `690` and `1402` have self-identifying 32-bit BMP payloads of `866x498` and `537x212` pixels respectively. `559` has no `CONTENTS` stream, but its 18-byte `JProperties` is now bounded as `OLES + uint16(0) + property_code:uint16(3) + utf16_code_unit_count:uint16(4) + "221\\0"`; all 386 N400 SHA use that exact form. Treat `221` only as an external-site candidate identifier, not a proven Sheet221 link or drawable resource. A raw resource-id byte occurrence in a Sheet is not a JSite binding. `AppObject` is metadata, not geometry: its bounded UTF-16 provider path is normally `r:\rad2d\bin\igrSmartLabel.dll` (one absolute-path variant uses the same DLL). `DocVersion3` is a NUL-delimited Shape2DServer version-history sequence of module/version/mode/timestamp quadruples. Do not use any of these records for SVG coordinates or primitive creation.
- The JSite OLE wrappers corroborate that classification: 690/1402 each contain the same 106-byte `\x01CompObj` declaring `Picture (Device Independent Bitmap)` / `StaticDib` and a fixed 20-byte `\x01Ole` header, while 559 has only `JProperties` (`OLES`) and no embedded content. Treat 690/1402 as embedded template bitmaps and 559 as a contentless external OLE placeholder.
- `TaggedTxtData/*`: title, revision, signature, configuration, and other bound fields.
- Every reviewed `TaggedTxtData/*` stream is well-formed UTF-8 XML. `Sheet221` Revision bindings explicitly reference only `MajorRev_ForRevise`, `RevisedBy`, `RevisedDate`, `CheckedBy`, `ApprovedBy`, and `RevisionDescription`; resolve only these through the Revision XML when their binding is present. `TitleArea`, `SignatureArea`, `Configuration`, and other XML are field/value metadata unless a bounded Sheet binding is separately proven. XML supplies values, never SVG coordinates, font metrics, leaders, or geometry.
- `JSite*/CONTENTS`: embedded bitmaps/OLE resources when present. `JProperties` without `CONTENTS` is insufficient to reconstruct the resource. It may still participate in PSM levels or object hierarchy; report it as unresolved rather than asserting that it is a specific missing border.
- `Dynamic Attributes Metadata` is a fixed 28-byte registration/version header across the 386-SHA corpus (`signature=0x6C90F544`, format version 1, flags `0x40000000`, zero tail). `JTaggedTxtStgList` is a fixed 70-byte registration stream listing `TaggedTxtStorages` and `TaggedTxtData`. Both are stream metadata only, not UCI/component/Sheet/SVG sources.
- `Sheet12`, `Sheet39`, `Sheet65`, `Sheet91`, and `Sheet117` are each present in all 386 audited SHA files as the exact same eight bytes `44f5906c00000000` (`0x6C90F544 + uint32(0)`). Classify them as `fixed-empty-sheet-registration-stub`: they contain no Sheet header, primitives, text, coordinates, or ISO-page content. Exclude them from physical-page counts, ISO split detection, capacity inference, and SVG reconstruction. If a future file differs byte-for-byte, leave it unvalidated rather than applying this rule.
- Standard OLE `\x05SummaryInformation` and `\x05DocumentSummaryInformation` are document provenance only. Across the 386-file corpus they identify template `PANDA3_IFC.Sha`, title/keywords `D Wide Border`, category `Border`, `Shape2DServer Application`, a fixed border-area comment, codepage 1252, plus per-file author, last-saved-by, revision, and timestamps. Emit these fields for auditability, but never use them as Shape2D content, Sheet/page evidence, UCI, component identity, font, coordinate, or render input.
- `DocVersion2` is also invariant across the corpus: 129 bytes, SHA-256 `d0e8a9ace5eeece8161a3e6998c92b9cd424f12af0657f011ac1720b98f30019`. Classify it only as a legacy document-version/compatibility profile; do not parse its repeated integers as PSM records, references, coordinates, components, or draw instructions without an independent boundary.
- In the fully bounded mixed `PSMspacemap/0x00000000` relation sequence, `relation=183` is validated only as `PSM hierarchy container -> named-layer membership`: its targets repeatedly resolve to `0x0081` names such as `PIPE`, `FITTINGS`, `WELDS`, `DIMLINES`, `MATLIST`, `FRAME`, `Border`, and `Level n`, while its parents are not named-layer objects. Emit the bounded target names as hierarchy evidence, not as page visibility, a Sheet primitive list, component topology, or render instructions.
- `relation=184` is polymorphic, not a single semantic: depending on its parent it routes to named layers (`01`, `Level n`, `Default`), JSite 559, rare physical Sheet roots, or segment addresses. Emit `relation_184_parent_contexts`; do not classify it as a component, layer, page, or render relation without its verified parent context.
- `StyleCluster 0x002E` is a bounded line-style registration family (only 54/58-byte forms across 386 SHA). Its `style_ref@+20` matches Sheet line styles in 3,548 of 7,819 records and text styles in zero records. Its stable structural slots are `category_raw@18` (only 1/2), `flags_raw@32`, `auxiliary_u32_raw@34`, `line_width_ratio@40`, and terminal uint32. Preserve the first three as raw fields: their business meanings are not validated as line type, weight, fill, or font. In particular `auxiliary_u32_raw` is not a proven object reference.
- The expanded 459-SHA primitive-use cross-check keeps `0x002E category_raw` non-semantic but adds a useful boundary: every direct use in decoded physical Sheet lines/circles/arcs is category `1`; the three category-`2` registrations (styles `13`, `14`, `42`) have zero use in those families and in decoded `0x004D` text. This is only a scoped negative result, not proof that category 2 means fill, hidden, unused globally, or any component class.
- The bounded 234-byte `0x003D` OLE/JSite placement wrapper has a fully reconciled placement frame in all 1,418 corpus records: origin `<2d>@42`, raw scale `@66`, size `<2d>@82`, repeated origin `<2d>@122`, repeated width `@138`, inverse aspect ratio `@154 = height / width`, and identity affine matrix `<4d>@170 = (1,0,0,1)`. The raw scale repeats at `@218`. This validates the placement frame but not the business meaning of the scale, trailing origin at `@202/@210`, or any unseen non-identity transform.
- For embedded BMP-DIB JSite resources, `0x003D @66` is now a decoded **physical bitmap scale**: source size in metres is `pixel_width / x_pixels_per_metre`, `pixel_height / y_pixels_per_metre`; its product with `@66` matches the placed `<2d>@82` size within 0.01% in all 772 embedded-bitmap wrappers. Parse DIB `x/y_pixels_per_metre` from offsets 38/42 in the BMP file. This does not apply to contentless JSite559 or establish a pipe-component transform.
- StyleCluster local resources are not direct ISO-page component instances in this corpus. Across all 386 SHA, the 20,730 bounded local polygon (`0x0084`), arc (`0x0061`), circle (`0x0059`), line (`0x0018`), font-template (`0x0070`), and composition (`0x007C`) object refs have zero matches in every currently decoded physical-Sheet reference slot: line/text/arc/circle primitive refs, graphic refs, page-layer/style refs, composite refs, and composite child refs. Preserve them as reusable library definitions only. This does not rule out a future, separately proven instance mechanism.
- In the 54-byte `StyleCluster 0x002E` form, the final uint32 is a file-local RGB24 value when it is one of `0x00000000`, `0x00FFFFFF`, `0x000000FF`, or `0x00FF0000`; direct SHA layer/style evidence maps them to black/white/blue/red examples. The 58-byte form is different: every tail exactly resolves to a bounded `0x002F` dash-pattern `style_ref`, with fixed links `11 -> 9`, `18 -> 15`, and `231 -> 230` across all 386 files. Thus this tail is a direct pattern-style reference, not RGB. Across all 386 files and all currently decoded primitive families, only style 231 is used: exactly once by the fixed `Sheet221` layer `01` divider. The zero uses of 11/18 do not rule out an as-yet-unclassified primitive family.
- `StyleCluster 0x0012` is a bounded font-directory/fallback resource family (224/225/512/736/737-byte records) containing the same font families as `0x002C`: Arial, STANDARD, SimHei, Courier New, and CHAR_FAST_FONT. It is not a direct Sheet font-style/coordinate/geometry record.
- `StyleCluster 0x0018` (50-byte payload, 23 records/file) is a local two-point line resource: four doubles at `@24/@32/@40/@48` are start-x/start-y/end-x/end-y. All 23 are children of the nine `0x007C` symbol groups, including a five-line rectangle and a fourteen-line local outline. It is direct **local** geometry, never a physical Sheet coordinate or a pipe component. `0x0070` is a font-bearing local text-template resource: `record_length = 84 + 2*font_name_char_count@86`, UTF-16 font name at `@90`, local anchor doubles at `@18/@26`, scale `@34`, transform/rotation raw value `@42`, and font-size ratio `@74`. All 1,930 corpus records are `Arial`; no text content or physical Sheet instance is stored. Two records are children of group 8299.
- `StyleCluster 0x0084` is a bounded local vector-template record with either an 88-byte payload (four points) or 104-byte payload (five points): `object_ref@6`, an opaque local style/flags value `@24`, flags `@28`, then `<double x, double y>` pairs from `@30`; the final point equals the first, so it is a closed normalized polygon, not an ISO page coordinate sequence. Do not equate `@24` with a font/line-style ref: its values collide across StyleCluster namespaces. `0x007C` is a bounded resource-member group: `record_length = 16 + 4*child_count`, `object_ref@6`, `child_count@18`, refs from `@22`. It includes four all-polygon two-member groups and mixed groups, including a seven-member group containing a local circle, two triangles, two 0x0018 local lines and two 0x0070 resources. In all 386 N400 SHA there are 9 such groups and 10 polygons, byte-identical per object. Retain this reusable-symbol relationship, but do not assign a business name or draw it on a Sheet until an instance/transform link is proven.
- `StyleCluster 0x0061,0x003B` is a fixed 59-byte local arc resource: `object_ref@6`, zero padding `@10..23`, then `center_x`, `center_y`, `radius`, `start_angle`, and `end_angle` as five doubles from `@24`. It is structurally an arc but in StyleCluster-local symbol space; do not mix these coordinates with physical Sheet `0x0061` arcs. The 9 resource groups expose its reuse in composite symbol templates.
- `StyleCluster 0x0059,0x002B` also has a local **circle** variant: `object_ref@6`, zero reference/layer/style slots `@10..23`, center doubles `@24/@32`, radius `@40`, and terminal byte `@48`. The same center/radius/flag positions are independently verified in physical Sheet circle records; only the coordinate space differs. Group 8299 contains this local circle, two triangles, two 0x0018 lines, and two 0x0070 text templates.
- `StyleCluster 0x001B` is a bounded 202-byte named internal-style resource. Its `object_ref@6` has a separate UTF-16 catalog backlink, all unambiguous in this corpus: `Reference`, `border`, `Office Automation`, or in the five S3D variants `S3D_INTERNALSTYLE_LOCK/UNLOCK...`. Preserve `category_raw@26`, `subtype_raw@30`, and the five scalar slots at `@96/@108/@116/@124/@132` as metadata only. It is not an ISO component, Sheet coordinate, or directly drawable primitive.
- `StyleCluster 0x002A` is a fixed 46-byte style-directory record. Its two direct catalog mappings are `object_ref 8195 / style 3 -> Normal` and `8208 / style 5 -> ANSI`; styles 12 and 16 have no catalog name. All 386 files have the same refs `3,5,12,16`, and none appears in any currently decoded line/text/circle/arc primitive. This makes the family a default/directory candidate rather than a direct rendering source; still keep the uint32 at `@34` as opaque color/flags and the double at `@44` as a raw terminal ratio, not RGB, line-width, or SVG formula.
- Tag-0 StyleCluster records are bounded object-library containers, not missing geometry: `record_length@2`, `object_ref@6` (validated allocation range `8192+`), raw category `@18`, nested payload beginning `@30`. Child byte ranges may overlap recursively, so do not scan their interiors as independent primitive headers. Their category values remain storage metadata, not component or layer labels.
- Use `render_stylecluster_resource_atlas.py <sha> --output <svg>` to inspect only decoded local symbol-library geometry. The local `0x0059` formula is now bounded as centre/radius at `+24/+32/+40`; the atlas must still never overlay local coordinates on an ISO Sheet without a proven instance transform. The checked-in `artifacts/stylecluster-resource-atlas.svg`/`.png` is a SHA-only example, not a PDF-derived diagram.
- `StyleCluster` also has a bounded 30-byte-header named line-style catalog entry: six uint32 fields, one uint16, a uint32 character count, then exactly that many UTF-16 code units. The raw `0x0043` bytes are simply the letter `C`, not a record marker. Across all 386 SHA, all 3,131 `LW...C...` entries' `object_ref` directly matches a bounded `0x002E` line-style object, while all 763 `Dash` entries directly match a fixed-66-byte `0x002F` dash-pattern record. `0x002F` has raw `scale_ratio@40`, `segment_count@54`, and double ratios at `@56/@64`; preserve them in the report but do not emit SVG dash arrays until their sign/scale semantics are proven. The report field `dash_pattern_validated_primitive_usage` now scans all decoded line/circle/arc families: it finds only `230 -> 231` on one fixed `Sheet221/01` divider per file, not a general pipe-dash instance. The business meaning of tokens such as `LW3.5`, `C5`, and `P1001` remains unproven, so do not use the name alone to choose SVG width, color, or any component class.
- Across all eligible `0x8000` maps, the 112 strict `UCI node -> relation=201` targets have zero exact intersections with decoded `0x0061` pipe-arc primitive refs and `0x0059/0x002B` ellipse primitive refs. Keep relation 201 UCI propagation limited to its validated line/composite-child chain; do not attach it to arcs, instrument bubbles, or ellipses by analogy.
- `Sheet221` is a shared template but not one universal byte image: the 386-file corpus has three versions (376 files at 15144 bytes/hash `0343...`, 8 at 15144/hash `431f...`, and 2 SWS4 files at 15384/hash `1554...`). Emit the full template SHA-256 and length; template rules must remain bounded and profile-aware.
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

The 12 directly decoded visible `Sheet221` template texts, their transforms,
and style references are identical across all three observed template
profiles. Render those fixed frame texts from SHA records across profiles;
Revision values still require their explicit XML bindings.

When this workflow is handed to a colleague, send the skill together with the
algorithm files `sha_to_svg_prototype.py`, `analyze_iso_split.py`, `analyze_composite_primitives.py`,
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
`PSMspacemap/0x00000000` as validated merely because it reaches EOF. Its
current valid framing is only the separately bounded zero-terminated
type-2/type-3 prefix, the complete middle sequence of checked relation
containers/zero-relation lists (including the fixed zero-target variant), and
the final type-3 root block. This decodes record boundaries, not all relation
semantics or a generic node table; never reinterpret the middle as `0x8000`
nodes or infer Sheet geometry from consumption alone.

For the fully bounded regular `0x0000` relation sequence, preserve every
target as `base = target & 0xFFFFE000` plus the `0x1FFF` offset. In the ten
complex reviewed SHA files every target base names an existing `PSMspacemap`
stream: 183/184 route to `0x0000`; 190 to `0x0000` or `0x6000`; 201 to
`0x0000`; 181/182 can cross to `0x8000/A000/C000` or `0x6000`. Numeric
overlap with a node, UCI, named layer, or Sheet root is only a candidate until
an independent record boundary proves that specific namespace.

The seven bounded zero-relation lists at the end of that regular sequence are
template/layout anchors. In the reviewed complex SHA their nonzero targets
are ordered `3242/3845/3870/4367/4396/4451/final physical Sheet root`: the
first five have thin PSM envelopes and form shared frame/material-panel edges;
4451 is a page-layout container; the final value varies but equals the final
physical Sheet local root. Emit their classifications as layout provenance and
never render them as pipe, fitting, weld, or standalone SVG geometry.

The short maps `0x2000`, `0x4000`, and `0x6000` are structurally complete as
`tseg` plus four uint16 header words and a uint16 payload list. `0x6000` has
an empty list but its second header field is the exact count of `0x0089`
dynamic-attribute records; its third field holds an unexplained reserve above
that count. Do not link its header values to graphic ids.

`PSMcluster0` additionally has observed contiguous `<I5H>` envelope-record
runs. Accept a run only after at least three consecutive plausible 14-byte
records with a local reference, valid page bounds, and a small final tag; this
avoids treating an arbitrary byte occurrence as a graphic reference. The
final `uint16` is a verified opaque PSM record-subtype tag, not yet a component
or layer name: every run-bounded `0x004d` text `child_ref` in the ten-SHA
audit carries tag 0; tag 11 covers verified template-frame edges and all
reviewed `0x0061` pipe arcs; tag 5 includes many page/complex containers; and
tag 6 is strongly correlated with ellipse records. Other decoded families
overlap several tags, so do not promote these correlations into primitive
semantics or rendering rules.

In the ten complex SHA, rare tag-17/ref-3067 and tag-9/ref-3256 envelopes are
fixed `Sheet221` `18/32` template divider lines on named layer `01`; their
PSM bounds are unchanged across samples. Classify them as static template
provenance, not drawing-pipe or component records.

For run-bounded `18/32` records with a direct `page_layer_ref@14`, the ten
complex SHA audit found all 33,778 tag-2 envelopes on the `PIPE` layer and no
tag-2 example on `FITTINGS`, `DIMLINES`, or `ISOTEXT`. Preserve this only as
a one-way observed layer association: `PIPE` itself also uses tags 5/6/11/16/21,
so tag 2 does not define every pipe or any business property.

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
