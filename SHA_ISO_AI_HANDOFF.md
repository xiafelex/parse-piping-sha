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

This validates `PSMspacemap/0x00008000` ordinary full node tables and the two
observed prefixed-index forms. The latter retain four uint16 header values, a
bounded uint16 prefix, then ordinary nodes; SWS4 also has record-boundary zero
padding. It reports local child references and relation-code distribution.
This does not claim those local references have already been linked to Sheet
primitives.

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
  `PSMroots` and `PSMclustertable` have now been identified as name/stream
  registries rather than component-geometry indexes. The remaining hierarchy
  target is the mixed `PSMspacemap/0x00000000` layout and its connection to
  `PSMsegmenttable` for complete hierarchy and local-transform recovery.
- `PSMspacemap/0x00000000` is structurally framed as a type-2/type-3
  zero-terminated prefix, a complete middle sequence of relation containers
  and zero-relation lists, and a final one/two-child type-3 root block. Never
  feed that middle region to the `0x8000` node parser or promote incidental
  type-1-like byte patterns inside its payloads to independent records.
- Three `0x0000` layouts are now bounded across 386 N400 SHA files: 171 direct
  counted-node tables, 152 complete mixed-relation layouts, and 63 prefixed
  counted-node tables. The two former exceptions are counted-table double-child
  tails combining `182 -> 559` template routing with `190` property routing.
  In the prefixed form, header
  field four is the exact uint16 reference-list count before the ordinary node
  table declared by header field one. Its list entries are namespace inventory
  only. Always prefer the complete mixed parse when it succeeds. The 23 one-child tails occur
  only in mixed or ambiguous samples. Their 14 `190` targets are bounded
  `0x0089` dynamic attributes; the 9 `201` targets are segment addresses, not
  node ids. Report the latter only as `spacemap-segment-address` plus offset.
- In the 171 + 63 counted layouts only, three-byte-word child payloads are now
  structurally distinguished from ordinary `<uint32 ref, uint16 relation>`
  rows. Type `1`, `node_id=2/3,type>=2`, and type `170+` use
  `<reserved, relation, child_ref>` with header field four as parent ref.
  Emit only zero-reserved `181/182/183/184/190/201` rows as routing edges;
  retain `0,0,local_ref` as unbound extension/anchor inventory. This improves
  hierarchy diagnostics but does not add any SVG geometry or component label.
- Counted-layout target classification now confirms the routing limits across
  all 234 files: `181` is chiefly JSite resources, `182` chiefly Sheet roots,
  `183` chiefly named PSM layer/hierarchy objects, while `190`/some `201`
  reach dynamic attributes or graphics. The repeated `parent=238` routes
  `181 -> 559` and `182 -> 398` are page/template hierarchy. Do not infer
  piping connectivity, an ISO split, or a visible primitive from these codes.
- `PSMroots` now has a validated UTF-16 root-directory layout. Its observed
  entries bind `DocStore -> 0x0000`, `Dynamic Attributes Set Table -> 0x2000`,
  and `_SupportOnlyList -> 0x4000`; `TopVFSet -> 0x10BB` and
  `StyleLibrarian -> 0x0001` do not match a `PSMspacemap` stream. The root
  header's byte value is not an entry count. These are registry names, not
  visible primitive classes.
- Root references are not a single object-id namespace. `PSMclustertable`
  resolves `DocStore -> 0` to directory entry `PSMcluster0`, and
  `StyleLibrarian -> 1` to `StyleCluster`; `TopVFSet -> 0x10BB` instead has
  its own bounded `0x0067` object record. Resolve this registry route before
  attempting any primitive, page, UCI, or layer interpretation.
- `0x7B` composite children now have a validated same-Sheet graphic backlink:
  every non-range child reference resolves to an `18/32` line, `61` arc, or
  `59/2B` ellipse whose graphic ref equals the composite parent ref. Corpus
  evidence is 449,504 exact links in 1,032 physical sheets. This is a grouping
  relationship, not an instruction to redraw the geometry and not a component
  class: raw type `6` links to line, arc, and ellipse records.
- `0x7B type=0` is a local composite-range child, not visible geometry. In
  38,381 of 43,468 occurrences its bounds exactly equal the envelope of the
  direct non-range children; the remaining ranges have no direct visible child.
  It is suitable for local extent/grouping analysis but must not be rendered.
- `StyleCluster 0x2A` is a fixed internal/default style family, not a physical
  drawing style: refs 3/5/12/16 occur in every SHA and have zero direct use in
  all decoded physical primitive families. Its opaque color/flag field must
  remain raw. `0x7C` local resource groups now resolve every member into the
  local line, arc, ellipse, polygon, or text-template resource family, but no
  direct placement into a physical Sheet has been proven.
- In counted `PSMspacemap/0x0000`, the payload entries outside the six known
  relation codes are all bounded zero-relation controls or layout anchors.
  There is no additional stable Sheet-reference-backed visible primitive family
  hidden in this PSM stream; remaining undecoded values are control/template
  semantics rather than missing piping geometry.
- `JSite559` is a contentless template route, not an empty OLE object. Its
  OLES property value is `"221"`, which resolves to the present `Sheet221`
  stream; 646 physical-Sheet `0x3D` placements use this resource across the
  corpus. Layer the referenced Sheet221 template with the physical Sheet
  rather than attempting bitmap rendering for this resource.
- `PSMclustertable` is also fully framed as a stream directory: `clst`, two
  uint32 header values, then the declared number of entries with
  `marker:uint8`, `directory_index:uint16`, `child_count:uint32`, child
  directory indexes, and a UTF-16 name. Across ten samples the marker is 1
  and the directory index equals entry order. It consumes exactly to stream
  end and every listed name exists as an OLE stream. Its child list is stream
  containment only; do not use it as geometry or ISO-page visibility.
- `PSMspacemap/0x00002000`, `0x00004000`, and `0x00006000` are exact-length
  uint16-list streams. `0x2000` is specifically the root-directory Dynamic
  Attributes Set Table. Its values only numerically overlap Sheet221's local-id
  interval: they have no direct decoded-Sheet221-ref overlap and no uint32 raw
  Sheet221 occurrences, so they are internal table indexes rather than Sheet
  objects. `0x4000` is the empty `_SupportOnlyList` in the ten samples.
  `0x6000` is also empty and unnamed, but its header field 1 exactly equals
  the total `0x0089` dynamic-attribute record count in all ten samples. Its
  next field reserves 145 entries (175 in AMSS1). It is a dynamic-attribute
  count/capacity control record, not a geometry, layer, or component id.
- The `PipeLine Info` subtype inside `Unclustered Dynamic Attributes` is now
  bounded at the property-block level. Its four `0x1080` blocks are one
  component property group: `PipeLine Reference`, `Fly Text`, `Unique
  Component Identifier`, and `Element Tag`. Their declared block sizes
  validate key/value extraction, but they are model metadata, not direct SVG
  text primitives. Keep the `0x0089` footer as a property-space reference:
  its uint32 length sometimes matches the local record and sometimes behaves
  as a capacity/cross-record field. A final unbounded `PipeLine Info` tail is
  intentionally left unresolved rather than consuming later document
  attributes. Across the ten audited files, every bounded GUID value and its
  record-tail `graphic_ref` matches `dynamic_graphics()` exactly; the report
  emits this check rather than assuming it from byte proximity.
  Its fixed 18-byte `UCI Index` companion has a uint32 value after the UCI
  block. Nonzero values only occur for repeated-UCI graphic instances in the
  audited data. Preserve it as an instance index; it is neither a coordinate
  nor a direct drawing instruction.
  A SHA-only text audit finds no exact physical-Sheet `0x004d` text match for
  nonempty `Fly Text` values, whereas `Element Tag` values commonly match.
  Do not render `Fly Text`; use an `Element Tag` string match only as a
  candidate identity relation and still obtain layout from the Sheet/PSM data.
- The compact `_ISO` subtype is also structurally bounded: key NUL, `0x0089`,
  size/reference, eight zero bytes, candidate `graphic_ref`, and `0xFFFF`.
  In all ten audited files one record has size 36 and the rest size 30; over
  99% of candidate refs have PSMcluster0 envelopes. Use it only as dynamic
  object-to-PSM routing evidence, never as a direct Sheet geometry mapping.
- `Draw`, `Schematic`, `FileName`, and `MuSuStr` are the only other `0x1080`
  keys in the ten-file audit, each appearing once per SHA. Keep them as
  document/export configuration metadata, never as ISO annotations or
  component records.
- Root-node `190/201` children now have complete observed-form coverage: 12
  of 15 are `0x0089` dynamic-attribute references, while the other three are
  small offsets from an existing map base (`0xC001 -> 0xC000`,
  `0x8002 -> 0x8000`). Preserve the base and offset; their selector semantics
  and visible-object meaning are still unresolved.
- The two middle `0x6000` header values happen to fall in Sheet221's numeric
  range in these samples, but that is not their semantic namespace: field 1
  is the exact dynamic-attribute record count. Do not use this coincidence to
  map the record to Sheet221 objects.
- `PSMspacemap/0x00008000` is optional, not a mandatory SHA invariant: two
  reviewed RHO1 exports omit it while retaining the root directory and short
  maps. The hierarchy report records this as a compact export variant and
  continues with the remaining streams.
- `PSMspacemap/0x00000000` has two independently bounded export layouts. In
  addition to the zero-terminated relation-container route, 63 corpus SHA
  files use a complete prefixed node table: four uint16 header values, a
  `2..778`-item uint16 prefix, then `422..1,706` ordinary nodes and optional
  six-byte terminal zero padding. Parse its node boundaries, but retain local
  refs and relation codes as internal PSM routing rather than Sheet geometry.
  Within this layout specifically, all 21,657 relation-190 edges resolve to
  `0x0089` dynamic attributes, while 5,360 of 5,486 relation-181 edges resolve
  to physical Sheet roots. These are scoped routing facts, not global relation
  code definitions and not a source of extra SVG geometry. Relation 182 is a
  JSite-resource placement route (5,485 of 6,116 direct resource hits) and
  relation 183 is a template-frame/layout-anchor route. All 444 relation-184
  edges converge on internal terminal `2402`, so they are a shared internal
  terminal-anchor route, not visible geometry. In this layout, all 5,169
  relation-201 targets resolve to geometry companions: 4,974 fixed three-line
  `0x13/0xAC` groups and 195 `0x13/0x63` circles. This does not assign UCI or
  make those companion records additional visible primitives; it is not a
  global relation-code definition.
  visible primitives.
- `PSMsegmenttable` payload slots align to map segments: byte `i` labels the
  `PSMspacemap` base `i * 0x2000`; zero-valued trailing slots have no map.
  This is a verified routing/index relation, not a decoded meaning for tags
  `1` and `9`. Do not use those tag values to hide or classify geometry.
- `PSMcluster0` has a validated named-record family beginning `0x0081`. It
  yields layer-like names (`PIPE`, `FITTINGS`, `WELDS`, `DIMLINES`, `MATLIST`,
  `ISOTEXT`, `SKETCHES`, `NOZZLES`, and `Level n`) and their internal object
  refs. The name-record length equation is validated. On later physical Sheets,
  valid `18/32` lines resolve their uint32 at byte `+14` directly to exactly one
  name in that page's 92-record group. Emit that name as provenance, never as a
  visibility filter. The third middle field (`field_3`) is a verified declared
  page-layer member-record count: all 3,404 layer objects in the ten-SHA audit
  exactly match decoded Sheet-record membership. The first two middle fields
  are always zero in the audited exports, so retain them as reserved/unused
  rather than assigning layer meaning; other primitive families remain open.
- Some named-record object refs directly resolve to nodes in complete `0x8000`,
  `0xA000`, or `0xC000` maps. The hierarchy report emits that link, but it is
  not proof of node-child-to-Sheet primitive membership and must not
  automatically assign an SVG layer.
- In linked named-layer nodes, relation 190 repeatedly targets the verified
  `0x0089` dynamic-attribute family. Relation 183 is bounded hierarchy-to-
  named-layer membership; 184 remains parent-context-dependent. In the
  validated full-prefix layout, every relation 201 is a geometry-companion
  route to either a three-line `0x0013/0x00AC` group or a circle
  `0x0013/0x0063` group; do not generalize that result to other layouts.
  report emits these category counts as hierarchy evidence only.
- The optional `0xA000` and `0xC000` maps have now been fully framed as normal
  node tables in ten original N400 SHA files. Every relation-190 edge is an
  exact `0x0089` dynamic-attribute target (`7,048/7,048` for `0xA000`,
  `1,159/1,159` for `0xC000`). Treat them as attribute-routing extensions;
  their other relation codes still do not prove a visible Sheet primitive.
- Extension-map targets are segment addresses rather than node IDs: use
  `base = target & 0xFFFFE000`, retain the `0x1FFF` offset, and require that
  `PSMspacemap/base` exists. This is a structural routing fact only; it must
  not be converted into component geometry or a Sheet association.
- The named-record count gives a SHA-only subsequent-page check in the reviewed
  set: `175 + 92 * number_of_Sheet_entries_after_Sheet221`. `Sheet6` is the
  first page and is not in this repeated group. The parser reports the expected
  count and match result. Treat 175 and 92 as observed export constants only.
- The page groups are now specifically bound in directory order: group `i`
  maps to physical Sheet `i` after Sheet221. Every reviewed group repeats the
  same 92 names and has `min(object_ref) = Sheet local-id start - 2`. Across
  65,845 renderable `18/32` lines plus 23 zero-length point records from the
  ten reviewed SHA files, every `+14`
  layer reference matched its own page group with no cross-page or unknown
  value. The report emits this per-page mapping and direct `18/32 -> layer`
  trace.
- The fixed first 175 named records are the corresponding base group for
  `Sheet6`: all decoded Sheet6 records resolve their `+14` layer ref there.
  Each reviewed export reconciles 164 of 175 `field_3` counts; the stable
  remaining 11 are template/default records. `Sheet221` partially resolves
  those template records (`01`, `04`, and `09` counts match exactly), but its
  remaining resource/text formats are not yet a source for geometry.
- Combining the Sheet6 records with bounded Sheet221 template records
  (`0x18`, `0x4d`, `0x3d`, `0x84`) reconciles 174 of 175 fixed base `field_3`
  counts in every reviewed SHA. The only remainder, `Default 0x0008`, occurs
  in PSM/document metadata but no Sheet primitive, so retain it as a
  non-drawing container/default object.
- Across all 386 audited N400 SHA files, `Sheet221` always contains one
  104-byte `0x0084` page-scale template container on layer `60`; its closed
  five-point normalized path from `+30` is byte-for-byte stable. It may be
  retained as template geometry but never classified as a pipe component.
  The reviewed set also has two 234-byte `0x003D` bitmap-placement wrappers
  on `Border`. Do not treat an `0x0084 +20` header field as a style. The long
- Each `0x003D` resource id is also directly resolvable in the SHA: `JSite1402`
  is an embedded 32-bit `537x212` BMP-DIB and `JSite690` an embedded 32-bit
  `866x498` BMP-DIB. Retain wrapper placement and extract the `CONTENTS`
  payload when a SHA-only output needs the original bitmap frame/logo content.
- For the 323 files with the standard counted `PSMspacemap/0x00000000` form,
  zero-relation anchors directly resolve to six Sheet221 template primitives:
  five fixed boundary/divider lines and the `0x0084` page path. A further 154
  of those files add one physical-Sheet root anchor. Treat these as template
  and page-root structure only, never as pipe components or UCI bindings.
  `0x004D` template family includes both visible text and Revision XML field
  bindings; resolve the latter against the Revision stream and never render
  the XML literal. The parser preserves the XML `stream/select/alt`, its raw
  prefix, and its tail transform location for traceability.
- The fully bounded `PSMspacemap/0x00000000` relation sequence is a hierarchy
  router, not a Sheet primitive list. In the ten-SHA audit, relation `190`
  hit `_ISO` dynamic attributes 1,398 times and direct UCI graphics 765 times;
  relation `201` hit direct UCI graphics 113 times. Relation `182` is instead
  dominated by physical Sheet header roots, with only 22 `_ISO`/`Element Tag`
  dynamic-attribute exceptions. Keep the raw relation and target category in
  diagnostics; do not create SVG geometry from a relation code alone.
- A stricter 0x8000 route is proven for 38 direct rows: the UCI graphic ref
  resolves to a validated node whose `relation=201` child is an exact Sheet
  composite `type-0` child. Every one of those type-0 children has same-parent
  visible type-5/type-6 siblings inside its type-0 bounds (112 visible child
  refs total). `type-0` stays non-drawing, but is a verified UCI-to-composite
  binding entry; assign that UCI only to contained same-composite siblings,
  excluding outgoing leaders.
- Do not upgrade a `relation=190` local-id interval candidate into geometry.
  In the ten-SHA audit, all 96 such candidates had zero matches to decoded
  ordinary lines, `18/32` lines, `0x61` arcs, `0x59/0x2b` ellipses, or
  composite parent refs in their interval-selected Sheet. Treat them as
  unresolved object/container-space targets until a direct record-boundary
  relation is proven.
- Text style resolution is now SHA-direct. A `StyleCluster 0x002C` record
  carries `style_ref@20`, font-size ratio `double@48`, and a UTF-16 font name
  starting at `+76`; its exact payload is `70 + 2*name_count`. A fixed
  `0x002D` record maps a rendered Sheet text style at `+20` to the font-style
  ref at `+44`. This resolves all 6,350 bounded physical-Sheet `0x004D` text
  records in the ten-SHA audit. Use the chain for family/nominal size and PSM
  envelopes for rendered glyph extents.
- A bounded StyleCluster `LW...C...` catalog entry has a 30-byte header and
  exactly `name_count` UTF-16 code units. Its `object_ref` directly matches a
  bounded `0x002E` line-style object in all 3,131 corpus entries. The raw
  `0x0043` bytes are the letter `C`, not a record marker. Emit the name to
  line-style link, but do not treat tokens such as `LW3.5`, `C5`, or `P1001`
  as SVG width/color semantics without separate proof.
- The same catalog layout has a `Dash` entry: all 763 corpus instances resolve
  by `object_ref` to a fixed-66-byte `0x002F` dash-pattern record (style refs
  9, 15, or 230). Across all 386 files, its only use in currently decoded
  primitives is `230 -> 231`, once in the fixed `Sheet221/01` divider; styles
  9/15 have no such use. This proves scope and the name-to-pattern route, not
  a decoded SVG dash-array payload; do not invent dash spacing from its
  remaining bytes.
- The bounded `StyleCluster 0x002E` line-style records now expose their full
  stable framing: `category_raw@18` (only values 1/2), `style_ref@20`,
  `flags_raw@32`, `auxiliary_u32_raw@34`, width ratio `double@40`, and a
  terminal uint32. The three raw slots are deliberately not named as cap,
  fill, engineering line type, or an object reference: only the 58-byte form's terminal reference
  is proved to route to a `0x002F` dash-pattern style.
- The 234-byte `0x003D` JSite/OLE placement wrapper has a SHA-internally
  reconciled frame in all 1,418 corpus records: origin at `@42/@50` repeats at
  `@122/@130`, width `@82` repeats at `@138`, `@154 = height / width`,
  `<4d>@170` is `(1,0,0,1)`, and the raw scale at `@66` repeats at `@218`.
  Use this for the placement box only; the scale's business meaning, the
  trailing `@202/@210` pair, and unseen non-identity transforms remain open.
- For the embedded BMP-DIB wrappers specifically, the scale is now decoded:
  BMP width/height divided by its DIB `x/y_pixels_per_metre` yields a native
  metre size, and multiplying it by `0x003D@66` matches `@82/@90` in all 772
  bitmap placements within 0.01% exporter rounding. Apply this only when the
  JSite has self-identifying BMP contents; JSite559 remains contentless.
- The fixed 46-byte `StyleCluster 0x002A` family always has style refs
  `3,5,12,16`; `3` and `5` are catalogued as `Normal` and `ANSI`. None occurs
  in any currently decoded line, text, circle, or arc primitive across all
  386 files, so treat it as a default/directory candidate rather than a direct
  rendering rule. Its remaining numeric fields are still raw.
- `StyleCluster 0x0084` is a bounded local closed-polygon template, not a
  physical Sheet vector: the 88/104-byte variants carry four/five point pairs
  from `+30`, with the last point closing the path. `0x007C` is a bounded
  resource group (`16 + 4*child_count`): some groups contain only polygons,
  while others mix local arcs, local circle resources, local 0x0018 lines,
  and font-bearing local 0x0070 text templates.
  The fixed 59-byte `StyleCluster 0x0061,0x003B` record contains local arc
  center/radius/start/end-angle doubles at `+24`. Keep this graph for future
  symbol-library recovery, but do not label or place any member on an ISO
  until a direct physical instance transform is decoded.
- A `StyleCluster 0x0059,0x002B` local variant is a local circle: the zero
  Sheet-reference slots are followed by local center x/y, radius, and a flag
  in the same field positions as physical Sheet circles. Do not feed its local
  coordinates into ISO page placement without a physical instance transform.
- `StyleCluster 0x0070` is not a generic constant: it has a bounded UTF-16
  font definition (`name_count@86`, name `@90`) plus local anchor/scale/raw
  transform values. All N400 corpus instances name `Arial`, but contain no
  text content and no physical Sheet instance; use it only as local template
  metadata.
- Treat three additional streams as metadata only: `JSitesList` is an exact
  `OLEM + count + uint32 ids` resource list (`559/690/1402` in the reviewed
  set). `690`/`1402` are 32-bit BMPs of `866x498`/`537x212`; `559` has no
  embedded contents. A raw Sheet byte hit is not a resource binding.
  `AppObject` identifies the `igrSmartLabel.dll` provider; and
  `DocVersion3` is a NUL-delimited Shape2DServer version-history quadruple
  sequence. They are useful provenance, but never SVG geometry sources.
- All reviewed `TaggedTxtData/*` streams are UTF-8 XML. Across the 386-SHA
  corpus, Sheet221 has 9,650 direct Revision bindings. They use only these
  leaf fields: `MajorRev_ForRevise`, `RevisedBy`, `RevisedDate`, `CheckedBy`,
  `ApprovedBy`, and `RevisionDescription`, selecting a particular revision
  history row through `[1+n]` or `last()-n`. Both selector forms now resolve
  directly against `TaggedTxtData/Revision`: 2,714 bindings resolve to their
  selected XML value and 6,936 target an unpopulated history row, where the
  explicit XML `alt=""` correctly resolves the visual field to blank. This
  is field-value recovery only; Sheet221 record coordinates and styles remain
  the source for placement and rendering.
- Local `StyleCluster` geometry/template records must not be promoted to page
  components by numeric object reference alone. A full 386-SHA cross-check
  found zero direct matches for all 20,730 local polygon/arc/circle/line/text
  template/composition refs in validated physical-Sheet reference slots. Keep
  them as library definitions unless a separate bounded instance record is
  discovered.
- `JSite559` has no embedded `CONTENTS`, but its fixed 18-byte `JProperties`
  now decodes as one UTF-16 property (`code=3`, value `221`). Preserve this
  as an external-site candidate identifier only: it is not sufficient proof
  of a Sheet221 binding or a drawable bitmap replacement.
- Physical Sheets also use the bounded 234-byte `0x003D` OLE-placement
  wrapper. All 646 such wrappers in the corpus target JSite559; every Sheet6
  has its canonical placement as the first record. Retain this page-level
  placement trace, but do not render it as a piping component or bitmap.
- Exact `graphic_ref` joins provide broad, SHA-only UCI coverage across all
  physical pages: 61.67% of bounded `18/32` lines, 96.15% of circles, 86.51%
  of arcs, and 99.22% of circle-geometry companions. Emit UCI only for that
  exact join; unmatched geometry must remain unlabelled rather than borrowing
  a nearby component identity.
- The strict PSM `relation=201` fallback is fully constrained: all 297
  additional UCI-visible-child pairs in the corpus come from a relation-201
  target that is composite type-0, propagated only to its bounded same-parent
  visible siblings. There are zero direct relation-201 hits on ordinary
  visible children. Do not widen this mapping to adjacent lines or leaders.
- `0x0013/0x00AC` is now a fully bounded line-geometry/layer companion: valid
  records are 172 bytes and contain a direction-preserving four-double endpoint
  pair at unaligned offset `+35`. All 43,394 valid corpus records match an
  `18/32` line with the same graphic and page layer, in either endpoint order.
  Use this only as redundant provenance, not
  as an extra stroke or a visibility instruction. Its audited fixed header is
  `204/1/3` at `+20/+24/+28`, followed by `0x0102` and `0x67` at `+32/+34`;
  these are record-format bytes, not the referenced line's style. Its
  `primitive_ref` is from a separate object namespace and must not be replaced
  by the enclosed line child id.
  The full record has three `0x67 + <4d>` line segments, one anchor, and three
  relation-203 child refs; each child ref resolves to its corresponding 18/32
  segment in either endpoint order. It is a three-stroke companion group, not
  a fourth rendered line.
  Resolve a value only through such a bounded Sheet binding. Other XML streams
  are metadata unless their own source binding is proven; they never supply
  SVG coordinates or geometry.
- Later physical Sheets now have a direct primitive-to-page-layer route, not
  merely a named-layer inventory. Valid `18/32` lines, bounded `0x004d` text,
  and `0x0059/0x002b` ellipse-like records all carry `page_layer_ref@14` that
  resolves to that Sheet's 92-object group; `0x004d style_ref` is instead at
  `+20`. The `0x0013/0x00ac` relation repeats the exact `18/32` layer link.
- A bounded `0x004d` text `secondary_ref@10` is either the current Sheet id or
  a bounded `0x007b` composite object ref at `+6`. The latter groups coordinate
  note rows such as `E/N/EL`. In `0x007b`, `+2` is always record length, never
  the parent reference; use `record_length = 32 + 14 * child_count`, parent at
  `+6`, and Sheet id at `+10`.
- The `0x0059/0x002b` WELDS ellipses directly resolve `graphic_ref@10` to UCI.
  Their paired `0x0013/0x0063` companion has the same graphic/layer/centre and
  supplies an exact radius cross-check at `+35`, plus an explicit relation-209
  ellipse child ref; use that child, not a primitive-plus-one assumption, for
  pairing. The 43-byte ellipse itself stores native radius at `+40`. The odd-length `0x0061` family
  is a PIPE circular arc with centre/radius/absolute start and end angles at
  `+24`; scan all byte offsets and render its validated minor signed arc.
- The weld-callout injector currently writes line/text primitives directly into
  `Sheet*` streams without a decoded PSM or full hierarchy backfill. Treat the
  resulting SHA as an experimental branch until a vendor engine confirms it.
- The renderer recognizes only observed Shape2D primitive layouts. Do not
  generalize a record signature without validating it against the SHA and its
  PSM envelope.
- UCI is a strong model-object instance key. It is not automatically a
  physical one-item-one-code identifier.
