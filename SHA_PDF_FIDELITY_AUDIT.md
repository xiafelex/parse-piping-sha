# SHA-PDF 图纸还原一致性审计

> 文档语言约定：本文件从本次更新起以中文记录。下方早期英文段落是已验证
> 的历史证据，保留原文以避免在翻译过程中误改二进制字段、引用号或计算条件；
> 后续维护会逐段转换为中文。

## 当前阶段结论

### 还原原则

- **图形还原唯一来源是 SHA**：Sheet、PSM、StyleCluster、JSite 和动态属性。
- **PDF 仅作视觉验收参考**：用于发现差异，绝不从 PDF 提取坐标、文字、矢量或
  像素来修改结果。
- **焊缝菱形、编号和引线不参与本审计**：它们是后续额外写入的业务图元。

### 28 页阶段性覆盖表

| 图纸 | PDF/SHA 物理页 | 当前基础图元结论 | 说明 |
| --- | ---: | --- | --- |
| N400P3A-CHW-N491163-01 | 1/1 | 已复核 | 主管线、图框、BOM、中文标题、尺寸及仪表一致。 |
| N400P3A-CHW-N434591-01 | 2/2 | 已复核 | 两页的双线、保温、跨页引用、仪表和标题框一致。 |
| N400P3A-LS-N492164-01 | 3/3 | 已复核 | 三个物理 Sheet 的分支、构件框、尺寸和标题字段一致。 |
| 100P3A-LN-276994-01 | 4/4 | 已复核 | 分图页、支撑标记、仪表、材料表和标题框一致。 |
| N400P3A-AMSS2-N444201-01 | 5/5 | 已复核 | 基础几何、文字、仪表及图框一致；复杂节点密线待进一步解码。 |
| N400P3A-UA-N495128-01 | 6/6 | 已复核 | 主管线、分支、尺寸、仪表、BOM 和标题框一致。 |
| N400P3A-RHO1-N434201-01 | 7/7 | 已复核 | 七页的连接、流向、标注、BOM 和标题框一致。 |

### 唯一未闭合问题

复杂法兰/阀门节点附近存在比 PDF 视觉上更密的 SHA 细线。已确认这些线来自
`18/32` 两点记录和 type-5 复合记录；它们与真实 UCI 构件轮廓共用对象组，不能
仅按 PSM status、局部对象组或 PDF 视觉安全删除。当前已把 SHA 内部的
`0x13/0xAC -> graphic ref -> local object group` 关系写入 SVG/trace，等待进一步
解码 PSM 父子/可见性语义后再做显示层修复。

## 定向纠偏台账

首轮逐页视觉复核已覆盖全部 `28/28` 个物理 Sheet；当前没有“尚未分析”的
PDF 页面。下表统计的是**尚未完成二进制语义解码**的定向问题，而不是漏审页面。

| 编号 | 影响范围 | SHA 证据 | 当前状态 | 下一步 |
| --- | --- | --- | --- | --- |
| T1 | 所有 28 页，视觉差异最明显于 AMSS2 `Sheet34246`、UA 密集节点页 | `18/32` 两点记录、type-5 复合线、`0x13/0xAC` 对象组关系 | 未闭合 | 解码 PSM 父子/可见性语义，不能按 PDF 或 group id 删线。 |
| T2 | AMSS2 `Sheet6` | 普通线父对象 `0x03C3` 的四条边与文本 `0x03C7` | 已澄清 | `0x03C7 = "6"` 的 Sheet 锚点落在框内；保留编号框，不加隐藏规则。 |
| T3 | LS `Sheet8093` 标题栏 | StyleCluster `0x1FB5` 与 Sheet/PSM 锚点存在不同对齐语义 | 未闭合 | 解码文字对齐/justification，不可直接按 PDF 平移。 |
| T4 | 多页复合构件 | composite child type `0`、`11`、`16` 尚无可靠几何语义 | 未闭合 | 建立子类型解码或 PSM 父对象映射；目前不将其误画为线。 |

### T1 当前定位结果

全量 trace 已显示 `56` 个页面级局部对象簇；每个簇通常同时含 UCI 构件线和
无 UCI 细节线，因此对象组本身不能定义“保留/删除”。例如 RHO1 `Sheet6` 组
`0x054B` 有 `243` 条线，其中 `121` 条带 UCI；AMSS2 `Sheet34246` 已解析到两组
`0x8621`、`0x8623`。接下来的 T1 分析将以**对象组 -> 复合对象头 -> PSM 父节点**
为顺序进行，并且每得到一个规则都要在其它 SHA 页面验证后才写入渲染器。

### T2 结论：普通线框 `0x03C3` 是有效编号框

AMSS2 `Sheet6` 中，父对象 `0x03C3` 的四条普通线子图元 `0x03C2`、`0x03C5`、
`0x03C6`、`0x03C8` 构成页面坐标约 `(2503,7834)--(2607,7908)` 的闭合方框。
文本 `0x03C7 = "6"` 的 Sheet 锚点为 `(2530,7847)`，严格位于该框内。虽然其 PSM
字框为 `(2467,7630)--(2516,7717)`，与几何框有样式/布局偏移，但不能据此把方框
视为空白或隐藏对象。该框应保留；T2 不产生渲染器删线修复。

## 历史英文证据

## Scope

This audit compares the PDF only as a visual acceptance reference.  All
reconstruction evidence and fixes must come from SHA streams.  Injected weld
diamonds, weld labels, and their leaders are excluded from fidelity checks.

## Page-Level Checklist

For every ISO page, verify these SHA-derived layers against the PDF:

1. Border, grid, revision block, title block, logos, and template images.
2. Main pipe double-lines, dashed insulation, flow arrows, weld dots, and
   component symbols including flanges, reducers, elbows, valves, and circles.
3. Dimensions, extension lines, arrowheads, boxed callouts, and leaders.
4. Text content, Chinese glyphs, font aspect ratio, anchor, box, and alignment.
5. Material table, support/instrument lists, notes, and page-specific title data.

## Current Evidence

| ISO | PDF pages | SHA render pages | First audit status | Notes |
| --- | ---: | ---: | --- | --- |
| N400P3A-CHW-N491163-01 | 1 | 1 | Base layer reviewed | Border, title, BOM, pipe geometry, dimensions, insulation, support text, and Chinese title content are present. Weld overlays excluded. |
| N400P3A-CHW-N434591-01 | 2 | 2 | Pending | Multi-page template and component review required. |
| N400P3A-LS-N492164-01 | 3 | 3 physical ISO sheets rendered | In progress | Physical mapping verified as `Sheet6 -> PDF 1`, `Sheet5368 -> PDF 2`, and `Sheet8093 -> PDF 3`. Legacy `logical_page()` title-digit extraction is not authoritative. |
| 100P3A-LN-276994-01 | 4 | 3 logical ISO sheets rendered | Pending | Page-number mapping must be audited before visual verdict. |
| N400P3A-AMSS2-N444201-01 | 5 | 5 | Pending | Existing detailed SHA decoding work must be revalidated against this PDF set. |
| N400P3A-UA-N495128-01 | 6 | 6 | Pending | Multi-page template and component review required. |
| N400P3A-RHO1-N434201-01 | 7 | 1 logical ISO sheet rendered | Pending | Page-number mapping must be audited before visual verdict. |

## Physical-Sheet Coverage

The current SHA-only audit render set contains `28` physical Sheet streams,
matching the `28` PDF pages across the seven supplied drawings. Physical Sheet
selection is mandatory: title-block text contains references to neighbouring
ISO sheets and is not a reliable page-number parser.

| ISO | PDF pages | Physical SHA Sheets rendered | Coverage |
| --- | ---: | ---: | --- |
| N400P3A-CHW-N491163-01 | 1 | 1 | Complete |
| N400P3A-CHW-N434591-01 | 2 | 2 | Complete |
| N400P3A-LS-N492164-01 | 3 | 3 | Complete |
| 100P3A-LN-276994-01 | 4 | 4 | Complete |
| N400P3A-AMSS2-N444201-01 | 5 | 5 | Complete |
| N400P3A-UA-N495128-01 | 6 | 6 | Complete |
| N400P3A-RHO1-N434201-01 | 7 | 7 | Complete |

## Decoder Risk Areas

- Composite Shape2D records: component outlines, title-frame rectangles, arcs,
  and instrument symbols.
- PSM-to-text association: font size, aspect ratio, baseline, text box, and
  leader/callout alignment.
- Template JSite images and their anchors.
- Logical PDF page numbering versus populated SHA Sheet streams.

## Confirmed Decoder Fixes

### 2026-07-26: Filter non-symbol 59/2B PSM ellipses

`59/2B` Shape2D signatures are not exclusively visible ellipse geometry.  In
the LS sample, `Sheet6` record `graphic_ref=0x14A1` had a PSM envelope of
`3973 x 5208` page units. Rendering it as an ellipse produced a page-sized
circle that is absent from the PDF.  The renderer now rejects 59/2B PSM
ellipses whose width or height exceeds `1000` page units.  Small instrument,
connection, and weld ellipses remain eligible.

Evidence: `N400P3A-LS-N492164-01`, physical `Sheet6`, compared with PDF page
1.  The re-render removes the false circle while retaining the real ISO
geometry.

### 2026-07-26: Require micro-UCI connection dots to touch decoded geometry

Small UCI/PSM envelopes without a direct vector reference are not always
visible weld or connection dots. In the LS sample, physical `Sheet8093` had
UCI `{000138AA-0000-0000-E626-42B5AE688804}` with a `31 x 30` PSM envelope at
page coordinate `(2562.5, 5097)`. Its nearest SHA-decoded line segment was
about `1610` page units away, and the rendered isolated dot was absent from
PDF page 3. The renderer now emits a micro-UCI dot only when its SHA ellipse
anchor (or PSM-envelope centre) is within `80` page units of a decoded Sheet
line or composite segment.

Evidence: `N400P3A-LS-N492164-01`, physical `Sheet8093`, compared with PDF
page 3. The connection-dot count changed from `15` to `14`; the isolated
left-side dot disappeared while real pipe-adjacent dots and the `FT N413201`
instrument circle remained.

### 2026-07-26: Decode rotated text from PSM projection, not envelope height

PSM gives an axis-aligned envelope even when a Sheet text record has a rotated
baseline. The previous renderer used the envelope height as the SVG font size,
which made diagonal annotations substantially too large. For a text direction
angle `a`, the renderer now solves the two projection equations
`W = L cos(a) + H sin(a)` and `B = L sin(a) + H cos(a)` for local text length
`L` and glyph height `H`; near 45 degrees, where that system is singular, it
keeps the conservative fallback until a StyleCluster metric is decoded.

Evidence: `N400P3A-CHW-N491163-01`, physical `Sheet6`, `GRID LINE` style
`0x0586`, angle `30` degrees, PSM envelope `420 x 292`. The corrected SHA-only
render uses glyph height `85.8` and text length `435.5`, replacing the false
`292`-unit font size. Its visual size matches the PDF reference while retaining
the original SHA anchor and angle.

### 2026-07-26: Initial page acceptance after rotated-text correction

Visual acceptance checks of `N400P3A-CHW-N491163-01/Sheet6` and
`100P3A-LN-276994-01/Sheet6` show the SHA-derived base layer contains the
pipe double-lines, insulation dashes, flow arrows, dimensions, component
outlines, text/callout frames, material table, title frame, Chinese title
content, template images, and north marker at the corresponding page
locations. Weld diamonds, weld labels and their leaders were excluded from
this check. Remaining review work is to repeat this element-level check for
the other 26 physical SHA Sheets and to log any variance before calling a page
accepted.

`N400P3A-AMSS2-N444201-01/Sheet6` was also checked as a dense-component
acceptance sample. Its pipe double-lines, insulation dashes, flow arrows,
flange/reducer composite outlines, `PI N444205` circular instrument, dimension
chain, callout frames, material table, and title frame are present at their
corresponding locations. Weld overlays remain excluded. No additional
SHA-derived rendering rule was justified by this page.

### 2026-07-26: Restrict frame-centering to component marker codes

The renderer formerly centred any ASCII label whose Sheet transform anchor
happened to fall in a decoded rectangle. This moved `INSUL:`, `CI30`, and
`CI50` to unrelated rectangle centres and expanded their glyphs to the frame
height. In `N400P3A-CHW-N434591-01`, physical `Sheet6`, the insulation texts
all use style `0x054B`; their PSM glyph heights are `86-87` page units while
the nearby rectangles are `154-155` page units high. Frame-centering is now
limited to verified ISO component marker syntax (`F/G/B/S/T` codes). Other
labels retain their direct PSM extent.

Result: the insulation text no longer overlaps or uses a false `126-127` unit
font. The exact association between the small `CIxx` codes and adjacent blank
composite rectangles was subsequently proven from the Sheet-local graphic
references. In this Sheet, `CI50` graphic `0x621` follows rectangle parent
`0x61B` by six local references; `CI30` `0x716` follows rectangle `0x711` by
five. Each parent has exactly four axis-aligned boundary segments. The renderer
now places `CIxx` in such a preceding local rectangle (within eight references)
while retaining the PSM glyph height and width. This places both codes into
their SHA-derived frames without the earlier oversizing.

### 2026-07-26: Classify frame content before applying frame centring

Follow-up on `N400P3A-CHW-N434591-01`, physical `Sheet6044`, showed that
reference strings such as `PS-N400-68279` and `PANDA3-005-1190` are genuine
long-frame contents. Treating all non-marker text as free PSM labels moved
them below their frames. The renderer now centres three SHA-proven frame-content
families: ISO marker codes (`F/G/B/S/T`), `PS-N<digits>-<digits>`, and
`PANDA<digits>-<digits>-<digits>`. `CIxx` continues to use its separate
preceding-graphic relation, while free labels such as `INSUL:` and `First
Dimension` retain their direct PSM placement.

### 2026-07-26: Reject PSM container envelopes for template Unicode labels

`N400P3A-RHO1-N434201-01`, physical `Sheet37157`, exposed a title-block
failure: the Chinese project title selected a nearby PSM envelope of height
`2458` page units and was rendered as a giant black overlay. The decoded
`SimHei-Z` StyleCluster metric is `67.2` page units, proving that envelope is a
container rather than a glyph envelope. Template Unicode labels now accept a
nearby PSM bbox only when its height is within `0.45x..4x` the StyleCluster
metric and its width is plausible for the string; otherwise they use the SHA
text anchor and StyleCluster font size. The three Chinese title labels now
render at `84`, `83`, and `67.2` page units respectively.

This page also confirms that physical stream identity is not PDF page identity:
`Sheet37157` displays `5 of 7` in its title block, while its `SEE SHT ... SHT
4` text is merely a neighbouring-sheet reference. Use the title-block page
field, not `SEE SHT` text or stream numeric suffix, when matching physical
Sheets to PDF pages.

After the Unicode correction, `Sheet37157` was matched to PDF page 5 by its
title-block `5 of 7` field. The base layer check confirms pipe geometry,
insulation dash, HV instrument circle, component frames, dimensions, material
table, and title block. The correction removes only the false giant Unicode
overlay and retains the normal Chinese title and company labels.

`N400P3A-RHO1-N434201-01/Sheet5614` was matched to PDF page 2 by title block
`2 of 7`. Its dense base layer was reviewed: pipe/component geometry, PT
instrument circle, insulation/trace note boxes, free note lines, `PS-N...`
reference frames, dimensions, material table, and title block all correspond.
This validates the frame-content classification on a page with multiple note
and reference-box families.

### 2026-07-26: Reject page-scale PSM envelopes for short Sheet text tokens

`100P3A-LN-276994-01`, physical `Sheet1046`, maps to PDF page 2 by the
title-block field `2 of 4`. Its first SHA-only render contained two false
overlays: text candidates `{f` and `1` were paired with PSM envelopes of
`4486 x 2099` and `4973 x 2371` page units. The decoded Sheet text scan can
see binary/header bytes that resemble short ASCII strings; these are not
visible labels, and the enormous envelopes identify their PSM references as
page/container objects rather than glyphs.

The renderer now accepts only the established printable ISO text character
set and rejects a one- or two-character candidate when its PSM glyph height
exceeds 800 page units. This is a SHA-internal consistency check, not a PDF
coordinate inference. After re-rendering, the pipe runs, elbows, dimensions,
flow arrows, `S3/S4/S5` support callouts, `PS-100-00742` frame, material table,
Chinese title block, and `2 of 4` page field remain while the two false
overlays are absent.

### 2026-07-26: Normalize type-5 composite endpoints and reject short-text containers

`100P3A-LN-276994-01`, physical `Sheet5632`, maps to PDF page 3 by its
title-block field `3 of 4`. Type-5 children inside a SHA composite record are
stored as uint16 coordinates at double *page* resolution. They were previously
returned as `value / 2` and then multiplied by `SHEET_UNIT` during SVG output,
which inflated flange/reducer/component strokes by 16800. They are now
normalized as `value / (2 * SHEET_UNIT)` before joining ordinary normalized
Sheet segments.

This page also demonstrated a short but legitimate text value (`6068`) whose
PSM ref was a 3033-by-1751 component/container envelope. The rotated-text
projection produced a false 6245-unit glyph. The existing short-token guard
therefore applies to every text up to ten characters when the PSM height is
over 800 page units. It removes the false overlay while preserving the type-5
component detail, pipe double-lines, dimensions, arrows, callout boxes,
material table and title block. A tentative filter of all 18/32 records whose
parent was the stream id was explicitly rejected: on this Sheet those records
include real pipe double-lines and dimensions.

`100P3A-LN-276994-01/Sheet6709` maps to PDF page 4 (`4 of 4`). After the
above corrections, its SHA-only result was reviewed against the PDF: the main
pipe double-lines and three branches, reducer/flange detail, PSV/PCV circular
instruments, insulation and flow symbols, dimensions, `SEE ISO/SHT` notes,
`S11/S19/S20` and `PS-...` frames, material list, north marker, and title block
are present in the corresponding locations. Weld overlays were excluded. No
new PDF-derived coordinate adjustment or SHA rule was required for this page.

### 2026-07-26: Regression check of the N434591 two-page dense template

The current renderer was re-run from the original SHA for
`N400P3A-CHW-N434591-01`. Physical `Sheet6` is title-block page `1 of 2` and
`Sheet6044` is `2 of 2`; both were compared to their matching PDF raster
references. The checks confirm that the type-5 normalized component layer did
not move the visible sheet geometry: pipe double-lines, insulation dashes,
flow arrows, dimensions, small component frames, `CI30/CI50` insulation
labels, `PS-N...` and `PANDA...` reference frames, instrument symbols,
connection notes, BOM columns and title-block content remain at their matching
locations. Dense flange/valve nodes contain many SHA composite strokes, as in
the corresponding PDF line groups. Weld diamonds and related labels remain
outside this acceptance scope. No additional SHA rule was justified.

### 2026-07-26: Seven-sheet RHO1 physical-page mapping and regression sweep

The original `N400P3A-RHO1-N434201-01` SHA was rendered anew with the current
parser. The title-block fields establish the authoritative PDF mapping:
`Sheet6 -> 1/7`, `Sheet5614 -> 2/7`, `Sheet7953 -> 3/7`,
`Sheet34639 -> 4/7`, `Sheet37157 -> 5/7`, `Sheet38316 -> 6/7`, and
`Sheet40376 -> 7/7`. This ordering differs from both physical stream suffixes
and `SEE SHT` notes.

All seven generated SVGs contain no text object above 800 page units, so the
short-text PSM-container and UTF-16 title guards remain effective. PDF
comparisons for pages 3, 4, 6 and 7 confirm their SHA-derived pipe
double-lines, insulation/trace labels, component and reference frames,
instruments, dimensions, flow arrows, connection notes, material lists and
title blocks. Page 2 and page 5 retain their previously documented checks.
Dense flange/valve regions are rendered from the SHA composite layer and match
the corresponding PDF line groups; weld overlays remain excluded.

The remaining page 1 check was completed on the same current build. Its
Chinese title-block labels, dual insulation/trace boxes, instrument circle,
grid labels, pipe double-lines, reference frames and connection annotations
match the PDF. The RHO1 seven-sheet set now has a current renderer/PDF
comparison for every physical page.

### 2026-07-26: UA six-sheet page mapping and cross-template regression

For `N400P3A-UA-N495128-01`, the title-block fields map the physical streams
as `Sheet6 -> 1/6`, `Sheet4779 -> 2/6`, `Sheet5818 -> 3/6`,
`Sheet6758 -> 4/6`, `Sheet8178 -> 5/6`, and `Sheet34136 -> 6/6`.
All six were regenerated directly from the original SHA and none contains an
SVG text glyph larger than 800 page units. PDF comparisons on pages 1 and 4
confirm the independent template's pipe double-lines, dimensions, arrows,
grid/connection notes, flanges, instrument circles, `PS-N...` callouts,
material table and title block align with the SHA-only output. The remaining
pages are retained in the same current-render set for detailed page review;
this entry does not treat the batch render as a substitute for that review.

### 2026-07-26: AMSS2 five-sheet page mapping and composite-layer regression

The current SHA-only rebuild of `N400P3A-AMSS2-N444201-01` maps physical
streams to title-block pages as `Sheet6 -> 1/5`, `Sheet5563 -> 2/5`,
`Sheet7763 -> 3/5`, `Sheet34246 -> 4/5`, and `Sheet36113 -> 5/5`.
No generated page contains an SVG text glyph larger than 800 page units.
Direct PDF comparisons of pages 2 and 4 confirm pipe double-lines, bends,
flange/valve composite line groups, flow arrows, dimensions, long `PS-N...`
frames, component marker boxes, instrument circles, support notes, BOM and
title block. This is the high-density regression sample for the normalized
type-5 composite endpoint rule; no new exception was required. The remaining
three pages stay in the rendered review set for page-specific acceptance.

Pages 2 and 3 of the same UA set were subsequently reviewed against their PDF
references. Long-run double lines, end connections, dimension chains,
component frames, pressure-instrument circle, bend geometry, arrows and
title-block fields correspond to the SHA-only results. UA pages 1 through 4
now have direct PDF checks; pages 5 and 6 remain for page-specific review.

PDF checks for UA pages 5 and 6 are now also complete. Their top dense branch
groups, long vertical/diagonal runs, multi-branch short-pipe geometry,
connection notes, component tags, dimensions, arrows, material lists and title
fields all match the SHA-only output. The UA six-sheet set therefore has a
current page-by-page visual review; weld overlays remain excluded.

### 2026-07-26: N491163 single-sheet regression check

`N400P3A-CHW-N491163-01/Sheet6` is a `1 of 1` physical page and was rendered
again from the original SHA. Its PDF comparison confirms the corrected rotated
`GRID LINE` labels retain their SHA angle and local glyph proportions, while
the pipe double-lines, insulation dash, platform marker, component/reference
frames, dimensions, connection notes, material list and title block remain
aligned. The current result contains no oversized text or page-scale false
geometry. Weld overlays are excluded from this validation.

### 2026-07-26: LS three-sheet full-page review

`N400P3A-LS-N492164-01` was rebuilt from its original SHA using the current
renderer. Title-block mapping is `Sheet6 -> 1/3`, `Sheet5368 -> 2/3`, and
`Sheet8093 -> 3/3`; all three SVGs have no text object above 800 page units.
Each matching PDF page was reviewed. Pipe double-lines, grid/connection notes,
platform symbols, insulation and trace frames, `PS-N...` reference callouts,
dimensions, flow arrows, material table, title frame and dense end components
are present at their corresponding locations. In the connection-point sample
on page 3, emitted micro-dots remain attached to decoded pipe/component
geometry and no isolated false dot is present. No new SHA parsing exception was
required; weld overlays were excluded.

### 2026-07-26: Restore proven Sheet line widths from StyleCluster

The base renderer had applied an SVG stroke width of `8` to every decoded
vector. This flattened the ISO's visible line hierarchy: ordinary pipe,
dimension and leader primitives use different `style_ref` values even when
they share the same binary two-point record layout. The `StyleCluster` stream
contains a repeatable line-weight record headed by `0x002E, 0x0036`; its
uint32 at byte `20` is the same style id used by the Sheet primitive and its
float64 at byte `40` is a normalized page line width. The renderer now converts
that ratio to page units (`ratio * 16800`) and assigns it only to matching
ordinary Sheet child primitives. Composite and template strokes remain on the
existing default until their own style relationship is decoded.

Evidence from `N400P3A-CHW-N491163-01/Sheet6`: styles `0x00EF`, `0x0118`,
`0x0119`, `0x011A`, `0x011B`, and `0x011C` resolve to widths `0.00030`,
`0.00035`, `0.00035`, `0.00095`, `0.00030`, and `0.00035` respectively.
The SHA-only rerender assigns `5.04`, `5.88`, and `15.96` page-unit strokes to
the corresponding direct Sheet records. The later second-record-family mapping
also resolves child ids shared with the composite layer; see the follow-up
below. Comparison to PDF page 1 shows the thin dimension/leader and heavier
drawing hierarchy is restored without moving geometry or introducing
PDF-derived style values. Weld overlays were excluded.

Follow-up on `N400P3A-AMSS2-N444201-01/Sheet5563` established that later
physical Sheets use a second direct-line family headed by `0x0018, 0x0032`.
Here the child primitive id is at byte `6`, the style reference is at byte
`20`, and the four float64 endpoints begin at byte `24`. Its style ids use the
same StyleCluster line-weight table. The current page has `1594` valid records
and all `1594` map to a source line width; its rerender restores those widths
for `1876` direct segments, `215` medium-weight segments, and `75` heavy
segments. Composite children that share those source child ids inherit their
same SHA style evidence; no style is guessed from the PDF. PDF page 2 was used
only to verify the resulting visual hierarchy; no PDF field or coordinate was
read by the decoder.

The current regression rebuild covers all `28` physical Sheets from the seven
supplied SHA files. The output directory also contains seven `Sheet221` files,
which are shared template streams and not physical ISO pages. Across the 28
physical page manifests, all `51,174` emitted vector segments now have a
SHA-derived StyleCluster line-width value; the SVG scan reports no text glyph
of `800` page units or larger and no inferred marker-frame fallback. This is a
structural coverage check, not final visual acceptance: composite linetypes,
colours, fill rules and unclassified primitive families still require
page-by-page SHA evidence before claiming exact PDF fidelity.

### 2026-07-26: Trace visible micro-UCI connection points in the manifest

The first UCI coverage scan incorrectly classified some rendered black dots as
missing because the manifest counted only line and text UCI associations. In
`N400P3A-CHW-N491163-01/Sheet6`, PCF confirms UCI
`{000138AA-0000-0000-435B-A9278868C104}` and
`{000138AA-0000-0000-3F5B-A9278868C104}` are `WELD` records. Their SHA dynamic
graphic refs `0x1555` and `0x1558` resolve to micro PSM envelopes and their
ellipse anchors are `(7176.96, 6983.76)` and `(7176.96, 5228.16)` page units.
Both anchors pass the existing 80-unit SHA-only topology check against decoded
pipe geometry and are rendered as connection dots.

The trace manifest now writes each accepted connection point with UCI, graphic
ref, PSM envelope, source anchor and mapping basis. This does not alter the
visible drawing; it prevents weld dots and other micro connection points from
being reported as missing components during fidelity audits. PCF is used here
only to classify this evidence sample, not to create the dot position.

With accepted micro points included, the 28-page UCI coverage scan leaves only
12 UCI values without a direct rendered line/text/point association. PCF type
classification, used solely as a semantic cross-check, shows seven are `WELD`
records (`LN/Sheet5632`, `LS/Sheet8093`, and UA pages), four are ordinary
pipe/flange records carrying insulation attributes (`AMSS2/Sheet34246`,
`RHO1/Sheet37157`, and UA pages), and one is the shared UA pipe support
`PS-N400-66977`. Thus this list does not establish twelve missing visible
symbols. It identifies the remaining PSM-to-parent hierarchy work needed to
associate already decoded visible component/support graphics with their UCI.
Do not synthesize a replacement glyph from a PCF coordinate; retain the SHA
geometry and report its UCI relation as unresolved until that hierarchy link is
proven.

### 2026-07-26: LN physical-page 1 visual regression

`100P3A-LN-276994-01/Sheet6` was compared visually with its matching PDF
reference using the original SHA-only render. The main pipe double-lines,
elbows, end connections, dimension strings, flow arrows, support/insulation
references, component-code frames, BOM, north marker and title frame align in
their respective regions. The apparent empty small rectangles are direct SHA
line primitives, not the renderer's inferred-marker fallback: the generated
SVG contains zero `inferred-marker-frame` elements on this page. Their nearby
component labels remain independent SHA text records located through their PSM
extents. No PDF geometry, text, or coordinate was used in this conclusion, and
the weld overlay was excluded from the review.

### 2026-07-26: Inherit the physical A1 viewbox for a headerless Sheet

The initial SHA-only render of `100P3A-LN-276994-01/Sheet1046` used the full
`16800 x 11880` Shape2D workspace because that local stream has no width,
height, or visible-y header values. Its direct primitives still use the same
normalized physical-page coordinates as the sibling sheets, so the ISO was
visibly shrunken into the upper-left corner when compared with PDF page 2.

`Sheet6` in the same SHA declares the shared source viewbox as
`(x=0, y=1886.563, width=14128.883, height=9978.981)`. The renderer now uses
that sibling SHA declaration only when a selected physical Sheet lacks a
declaration of its own. Re-rendering `Sheet1046` restores the complete A1
border, title block and drawing scale; the pipe, text, and all other decoded
geometry retain their original SHA coordinates. An inventory across the seven
supplied SHA files identifies this as the only headerless physical Sheet.
The PDF was used to identify and verify the scale defect, never as a geometry
or coordinate source.

The SHA weld-callout writer now uses the same inherited viewbox rule for its
SHA-only boundary and collision calculations. This does not affect the base
ISO comparison or use PDF data; it prevents a headerless Sheet from receiving
an incorrectly shrunken placement region in a later optional annotation pass.

### 2026-07-26: Withdraw the unverified mixed PSMspacemap parser

The exploratory parser for `PSMspacemap/0x00000000` was removed from
`analyze_psm_hierarchy.py`. Although it could consume the stream, a local
cross-check against a known `graphic_ref -> space_ref` record showed that its
ordinary/compact layout heuristic could shift subsequent node boundaries. Full
byte consumption alone is not evidence of a correct hierarchy. The report now
retains only the fully validated `PSMspacemap/0x00008000` `<4H> + <IH>` node
table; a UA regression report still produces 1406 validated nodes. Local
`graphic_ref -> space_ref` evidence remains diagnostic-only until a
cross-sample framing rule is proven.

### 2026-07-28: Composite metadata and conservative PSM identity recovery

A ten-SHA SHA-only audit now separates composite metadata from visible
geometry. Type-0 is a non-drawing composite range header; type-2, type-11,
and type-16 primarily reference already decoded `18/32` line children rather
than separate component outlines. A type-2-backed raw `18/32` segment shorter
than four page units is now admitted only when that exact child reference is
independently present in a structurally bounded type-2 composite record. This
recovers local node and arrow details without relaxing the global binary-noise
filter.

The validated `PSMspacemap/0x8000` relation code `201` can also establish a
strict UCI identity chain through a type-0 child to its same-parent type-5
visible outline. The renderer records that provenance in SVG/trace metadata
only; it does not alter geometry or visibility. Relation codes `190`, `183`,
and `184` remain unresolved. PDF was used only to review the resulting
SHA-only output.

### 2026-07-28: Additional validated PSM map and directory boundaries

`PSMspacemap/0xA000` was fully consumed by the same node framing in four of
the ten reviewed SHA files. It is now reported as an additional validated
node table, while its relation semantics remain inventory-only. `PSMroots`
and `PSMclustertable` were separately inspected and are name/stream
registries, not component-geometry indexes; they do not resolve the pending
`190/183/184` references. The mixed `0x0000` map remains inventory-only.

### 2026-07-28: PSM space-map boundary decoding without semantic overreach

The ten-SHA audit identified two independently repeatable structures in
`PSMspacemap/0x00000000`: a zero-terminated type-2/type-3 prefix and a final
type-3 root block holding one or two `190`/`201` references. Nine samples
contain five prefix records; AMSS1 contains a shortened three-record variant.
The intervening type-1-dominated bytes cannot safely use the `0x8000` node
layout, because that would convert ordinary local fields into implausible
relation codes. The renderer does not use any of these records for geometry.

The short maps `0x2000`, `0x4000`, and `0x6000` were also separated from the
ordinary node-table parser. They are exact-length uint16-list records: four
uint16 header values followed by the number of uint16 values stated in the
fourth header field. This recovers their boundaries and raw values only. No
PDF text, path, coordinate, or image was used to derive either rule.

The formerly unnamed `0x6000` header now has a cross-sample meaning: its
second uint16 field exactly equals the count of `0x0089` attribute-reference
records in `Unclustered Dynamic Attributes` for all ten files. Its third field
is that count plus 145, except AMSS1 where it is plus 175. Because the payload
list is always empty, this is a dynamic-attribute count/capacity control map,
not a Sheet221 local-id range or a visible-object selector. The reserve policy
itself remains unresolved.

`PSMsegmenttable` has also gained a concrete routing interpretation. It is
`stab + uint32 payload count + payload bytes`, and byte index `i` exactly
aligns with `PSMspacemap/0x(i*0x2000)` when that map exists. Any trailing
unallocated slots are zero. The observed values 1 and 9 remain opaque segment
tags; no PDF or geometry was used to assign them visibility or primitive type.

`PSMcluster0` now has an independently bounded named-record family. Records
begin with `0x0081`; their stored length exactly equals a 30-byte fixed header
plus a NUL-terminated UTF-16LE name. This recovers named ISO layer-like entries
including `PIPE`, `FITTINGS`, `WELDS`, `DIMLINES`, `MATLIST`, `ISOTEXT`,
`SKETCHES`, `NOZZLES`, and `Level n`, together with internal object refs. The
remaining metadata fields and actual primitive membership stay inventory-only;
the new parser does not hide, move, or create any SVG geometry.

Some named-record object references also resolve directly to nodes in complete
`0x8000`, `0xA000`, or `0xC000` tables. The report now keeps this named-layer
to PSM-node trace link. It is not promoted to an SVG layer assignment because
the final node-child-to-Sheet primitive membership relation is still absent.
In linked named-layer nodes, relation 190 repeatedly targets the verified
`0x0089` dynamic-attribute family, while 183/184/201 remain unresolved. This
is reported as hierarchy evidence only, not used for SVG visibility or layer
assignment.

Across the ten selected SHA files, named-record count is also an exact
subsequent-page check: `175 + 92 * (Sheet* directory entries after Sheet221)`.
`Sheet6` is the first page and is not part of this repeated group; SCC yields
819 and DMW 1,095 records. This confirms a fixed base plus repeated
later-page name groups without using a PDF.

The subsequent object-reference check now identifies the exact page mapping:
group `i` corresponds to directory-order physical Sheet `i` after Sheet221.
Every group repeats the same 92 names and has minimum object reference equal
to that Sheet header's local-id start minus two. A further SHA-only scan of
65,845 renderable `18/32` lines plus 23 zero-length point records finds every
uint32 at record byte `+14` in the
matching page's 92-object group, with no unknown or cross-page value. The
report now emits the direct `18/32 -> named layer` trace (`PIPE`, `FITTINGS`,
`DIMLINES`, `ISOTEXT`, `FRAME`, and peers); it remains provenance only and is
not a visibility rule.

### 2026-07-28: PSMroots directory decoding and compact-map variants

`PSMroots` is now fully framed as `rootb`, an eight-byte header, then repeated
`uint32 UTF-16 character count + UTF-16LE name + uint32 root reference`.
Across all ten SHA samples it yields the same five entries: `TopVFSet ->
0x10BB`, `_SupportOnlyList -> 0x4000`, `Dynamic Attributes Set Table ->
0x2000`, `StyleLibrarian -> 0x0001`, and `DocStore -> 0x0000`. The header byte
is `9` although five entries follow, so it is retained as an uninterpreted
header value rather than called a count.

This makes the root identities of three short maps evidence-based: `0x0000`
is `DocStore`, `0x2000` is the Dynamic Attributes Set Table, and `0x4000` is
the SupportOnlyList. All reviewed `0x2000` values numerically fall in
Sheet221's local-id interval, but they have no direct overlap with decoded
Sheet221 primitive references and no uint32 raw Sheet221 hits. This is an
internal Dynamic Attributes Set Table index sequence, not a Sheet221 object
scope. Every reviewed `0x4000` and `0x6000` list is empty;
`0x6000` has no matching root-directory name. Two RHO1 files omit `0x8000`
entirely while retaining the same root directory and short maps, proving that
the normal hierarchy map is optional for this export variant. These findings
come only from SHA stream bytes; PDF was not used as data.

### 2026-07-28: PSMclustertable complete stream-directory framing

The separate `PSMclustertable` stream is no longer treated as a raw string
scan. Its `clst` layout is now structurally validated as two uint32 header
values followed by the declared number of records. Each record stores a
uint32 UTF-16 byte length, `marker:uint8`, `directory_index:uint16`,
`child_count:uint32`, that many uint32 child directory indexes, and a
NUL-terminated printable UTF-16LE stream name. In all ten samples the marker
is `1` and directory index equals record order. Sheet rows have zero children;
the larger initial records carry a small list of child directory indexes.

The new parser consumed every byte in all ten selected SHA files; declared and
parsed entry counts agree, and every listed name resolves to an existing OLE
stream. The directory reliably inventories `PSMcluster0`, `StyleCluster`, the
dynamic-attribute streams, and all registered `Sheet*` streams. `PSMcluster0`
references itself and zero or more early Sheet directory entries; that is
stream containment only, not a link from a PSM object to visible geometry.

The root-directory follow-up also found that some `TopVFSet`, `StyleLibrarian`,
and `DocStore` references resolve to type-3 nodes in a complete `0x8000`,
`0xA000`, or `0xC000` table. UCI records are only one dynamic-attribute
subtype: a second verified field is `0x0089 + uint32 size + uint32 reference`,
following `_ISO` and `Element Tag` payloads. Twelve of the 15 observed
`190/201` child targets hit that general attribute field: nine compact `_ISO`
size-30 records and three `Element Tag` records of sizes 149/234/240. None hit
the UCI-only graphic-reference set, `PSMcluster0`, or a node id in a complete
map. They therefore route mainly to dynamic drawing/property references, not
direct components or Sheet primitives. The remaining three are not format failures:
`0xC001` is base `0xC000` plus offset 1 for an existing map, while two `0x8002`
targets are base `0x8000` plus offset 2. This completes the observed target
form classification, but the base-offset selector meanings remain unresolved.

The first contiguous run in the mixed `0x0000` middle uses another verified
relation-edge layout: `<record type, child count, repeated child count, parent
ref>` plus `<reserved=0, relation, child ref>` entries. Type-2 records carry
one edge and type-3 records can batch many edges. Their parents continue
references emitted by the preceding high-level prefix, confirming a PSM
hierarchy role. The regular samples each begin with 17 records and 191--209
such edges before a zero-child special case switches layout; parsing stops at
that transition. These records remain excluded from SVG geometry.

The later control block has now been bounded as a type-3 zero-relation list:
`<3, 0, N, 0>` plus `N` `<0, 0, child ref>` triples. Each of the nine regular
samples has seven such lists. Together with the ordinary relation containers
and one fixed zero-target variant, the sequence consumes the full middle
region exactly through the independent tail-root boundary. This is a format
framing result only: zero-relation child refs are not yet mapped to PSM
objects, Sheet records, or SVG elements.

### 2026-07-26: LN four-sheet visual review complete

The four physical pages of `100P3A-LN-276994-01` were reviewed against their
matching PDF pages after the `Sheet1046` viewbox correction. Across Sheets 6,
1046, 5632 and 6709, the SHA-only output retains pipe double-lines, end
connections, reducers/flanges and valve composites, flow arrows, dimensions,
component and support frames, insulation/trace notes, `SEE SHT` links, grid
lines, the PSV/PCV instrument circles, material list, north marker and title
block. Page 2 uses the inherited sibling viewbox; the remaining physical
Sheets use their own header declaration. Weld overlays were excluded. No
additional PDF-derived geometry or text correction was introduced.

### 2026-07-26: Recover boxed reference text from page-scale PSM containers

Text coverage comparison used PDF only to flag `PS-N400-69159` on
`N400P3A-LS-N492164-01/Sheet5368`. The text exists in that Sheet's SHA record
with anchor `(0.4550, 0.2283)`, but its `graphic_ref 0x1652` resolves to the
page-scale PSM envelope `(3290, 0, 9610, 5632)`. The old renderer correctly
rejected that envelope as a container and consequently omitted the valid
label.

The renderer now checks a narrow SHA-only relation before rejecting an
oversized PSM envelope: an ISO component marker or `PS-N...`/`PANDA...`
reference must have its raw Sheet anchor inside a directly decoded closed
rectangle. When that is true, the closed rectangle replaces the PSM container
as the label boundary. `PS-N400-69159` now renders in its source frame
`(7617, 3822, 8316, 3896)`. A full 28-page rerender found only four affected
labels: this LS reference, `S8` on N434591 Sheet6044, and
`PS-N400-67530`/`S21` on RHO1 Sheet5614. Their manifests explicitly record
`sha-closed-frame-replaces-psm-container`. PDF text was not copied into SVG.

### 2026-07-26: Recover non-boxed BOM/template text from PSM containers

The RHO1 physical page `Sheet5614` showed two material-table strings in the
PDF acceptance image that were present as raw SHA text but had page-scale PSM
containers: `VALVES / IN-LINE ITEMS` (`graphic_ref 0x16E3`) and `PTN434206`
(`0x16F8`). Both use SHA style `0x164A`; ordinary, correctly bounded records
of that same style establish the local anchor offset and glyph metrics. The
renderer therefore reconstructs their small text bounds from the SHA text
anchor and same-style SHA samples, marking the trace rows as
`sha-style-fallback-replaces-psm-container`.

This fallback is deliberately restricted to non-short text in the upper/right
material-template region. It excludes title-block identifiers, page fields and
short values that can share the same oversized PSM symptom. The full 28-page
physical-sheet rerender affects 13 material headings/descriptions across nine
pages, including the two RHO1 strings; all 28 generated physical SVGs parse
as XML. Their PNGs were regenerated and RHO1 page 2 was visually checked
against its PDF solely as an acceptance reference. No PDF text, position, or
geometry entered the reconstruction.

### 2026-07-26: Decode printable UTF-16 length prefixes before text references

On RHO1 `Sheet5614`, several material-list rows were present in raw SHA but
absent from the SVG, including `90Deg Elb LR...`, two `NM Flat Gk...` rows and
`SW Gk...`. The cause was a binary text-boundary error: Shape2D places a
uint16 UTF-16 character count immediately before these strings. A count in the
printable range (for example `0x0021`) resembles a UTF-16 `!`, so a generic
printable scan consumed it as a false first character. That shifted the
decoded `graphic_ref` and `style_ref` two bytes earlier and made their PSM
lookup invalid.

The parser now recognizes the prefix only when its uint16 value exactly equals
the following printable run's character count minus one, then resolves
references from the corrected text boundary. On the observed raw RHO record,
this restores `graphic_ref 0x16BF` and `style_ref 0x164A` instead of the
invalid `0x16BF0000`/`0x164A0000`. A source-wide scan found 189 such strings
across the 28 physical pages of the seven supplied SHA files. All 28 base SVG
and PNG pages were regenerated and their SVG XML parses successfully. PDF was
used only to expose and visually verify the missing descriptions; the
boundary rule and recovered values come solely from SHA bytes.

### 2026-07-26: Post-prefix visual regression, N491163 page 1 and N434591 page 2

After the UTF-16 length-prefix correction, the SHA-only render of
`N400P3A-CHW-N491163-01/Sheet6` was reviewed against PDF page 1. The pipe
double-lines, elbows/flange composite, dashed centre line, flow arrows,
dimension chains, support/component frames, insulation labels, material list,
north marker, revision/title block, Chinese labels and page border are present
in their matching regions. This review excludes the separately injected weld
callouts.

`N400P3A-CHW-N434591-01/Sheet6044` was also checked with a PDF/SHA structural
overlay after the same correction. Main pipe geometry, split-page `SEE SHT`
links, dimension lines, component/support frames, material lists and title
block have a visible PDF counterpart. The SVG includes many `<rect>` elements
for UCI provenance, but they are inside the hidden `sha-uci-regions` group and
are not visible ISO geometry. The apparent red/cyan fine fringes in the
overlay are PDF raster antialiasing and browser font/line rasterisation, not
independent SHA coordinate corrections. PDF remains an acceptance reference;
no PDF-derived values were used in either render.

### 2026-07-26: Do not infer a missing symbol from a coincidental dynamic-ref byte match

The first unresolved-UCI sweep flagged LS `Sheet8093` dynamic ref `0x1BA3`
with a local `433 x 254` PSM envelope. PCF classifies its UCI as an `ELBOW`,
but PDF and the SHA-only SVG already show the corresponding 90-degree elbow.
Raw Sheet inspection shows the only `0x1BA3` byte occurrence lies in a
type-5 composite child's uint16 coordinate field, not in an object-reference
field. The dynamic attribute's uint32 value can therefore collide with a
coordinate value in the Sheet byte stream.

Consequently, byte containment alone is insufficient evidence that a dynamic
UCI ref belongs to a visible Sheet primitive or that its PSM envelope denotes a
missing symbol. Candidate audits must require a decoded record boundary or a
validated hierarchy relation before treating the ref as an unrendered object.
This finding does not modify visible geometry; it removes a false missing-
component lead without using PDF coordinates.

### 2026-07-26: LS page 2/3 and AMSS2 page 1 post-prefix visual review

The SHA-only base renders of `N400P3A-LS-N492164-01/Sheet5368` and `Sheet8093`
were reviewed against PDF pages 2 and 3. Their split-page links, long pipe
runs, elbows/reducers, component and support frames, insulation notes,
instrument bubble, dimensions, material tables, north marker and title frame
are present in matching regions. The `PS-N400-69159` reference on LS page 2
also remains recovered by the already documented closed-frame rule.

`N400P3A-AMSS2-N444201-01/Sheet6` was checked against PDF page 1 after the
same text-boundary correction. The pipe double-lines, flange/reducer/tee
composites, instrument circle, flow arrows, dimensions, boxed component codes,
material list, Chinese title-block content and border all have their source
counterparts. This check excludes weld overlays. No PDF coordinate or text was
used to change the SVG; observed hairline/letter raster differences are not
independent SHA-element discrepancies.

### 2026-07-26: AMSS2 pages 2-5 complete post-prefix review

The remaining physical streams of `N400P3A-AMSS2-N444201-01` -- `Sheet5563`,
`Sheet7763`, `Sheet34246`, and `Sheet36113` -- were reviewed against PDF pages
2 through 5. The split-page `SEE SHT` references, long pipe runs, elbows,
reducers/flanges, complex junctions, instrument bubbles, flow arrows, slope
and elevation dimensions, boxed references, material lists, title-block
content and page borders all have matching base SHA output. This completes a
post-length-prefix visual pass over all five AMSS2 physical pages; the optional
weld overlay remains excluded. No additional PDF-to-SHA correction was
introduced.

### 2026-07-26: Type-0 composite-child experiment retained as unresolved

Composite records contain child tags `0`, `5`, `6`, `11`, and `16`. The base
renderer intentionally decodes only tag `5`, whose endpoint semantics have
been validated. A SHA-only experimental render of N491163 page 1 also treated
tag `0` as a line. It added only 17 short strokes (630 changed PNG pixels) and
did not resolve a PDF-visible missing component or outline; the page remained
otherwise unchanged. Current bytes establish that tag `0` has coordinate-like
fields, but do not prove it is always a visible straight segment rather than a
different primitive/control child. It therefore remains unrendered pending a
cross-sample record-boundary and PDF-shape correlation. This avoids a global
false-geometry regression.

### 2026-07-27: Type-0 is an overlapping composite detail, not a line family

A second SHA-only inventory covered all seven supplied drawings. It found
`1,530` composite children tagged `type=0`; `1,233` occur in a parent that
also contains type-5 children, and `1,228` overlap at least one sibling type-5
bounding box. Type-0 children have no direct `PSMcluster0` graphic mapping in
the overwhelming majority of cases. The pattern is therefore consistent with
an auxiliary/fill/control detail of a composite symbol, not a standalone
two-endpoint vector family.

No renderer change is made from this evidence alone. Rendering these values as
straight lines would duplicate already decoded flange/reducer/valve outlines
and reintroduce false strokes. PDF pages were used only to confirm that the
existing type-5 output has no corresponding systematic missing-outline defect;
the counts, parent membership, and overlap relationship all come from SHA
bytes. Type-0 remains an inventory-only primitive pending a validated subtype
decoder.

### 2026-07-27: Separate template text family from ISO body text

Local side-by-side inspection of RHO page 6 and AMSS2 page 2 exposed a real
template typography error: the renderer had inherited a global `monospace`
family for revision entries, BOM rows, title-block values, line numbers and
page fields. The SHA records identify the separation without using PDF data.
For example, RHO `Sheet38316` uses style `0x95C4` for the right-side title
fields and `0x9608` for the BOM while `0x9609` is used by the ISO body; AMSS2
`Sheet5563` shows the same three-cluster pattern as `0x15D3`, `0x1617`, and
`0x1618`.

The renderer now applies the observed sans-serif template family only to
direct Sheet text whose SHA anchor is in the physical right-side template
panel (`x >= 0.55 * SHEET_UNIT`). The fixed template groups already use the
same family. Main drawing annotations remain in the original fixed-pitch
family. All 28 physical pages were regenerated from SHA and parsed as valid
SVG. PDF was used to identify and validate the font-family discrepancy; the
panel boundary, style clustering, anchors, and replacement family come from
the SHA template/text records.

### 2026-07-27: Bind the ISO body to the explicit StyleCluster font family

The body text group previously declared only the CSS generic family
`monospace`. This is not a SHA font binding: it lets every SVG consumer choose
its own fixed-pitch substitute, which changes the aspect ratio of dimension
values, `SEE SHT` notes, component labels, and boxed callouts even though the
SHA anchor and PSM envelope are unchanged. `StyleCluster` in both RHO1 and
AMSS2 contains explicit `Courier New` records alongside the separate `Arial`
template records and `SimHei-Z` Chinese records. For example, RHO1 contains
`Courier New:0.005::-1` at offsets `0x1ED0` and `0x1F36`, while its template
family records contain `Arial:...` entries from `0x2002` onward.

The base ISO body now declares `font-family="Courier New, Courier, monospace"`.
The right-side template rule continues to override that with `Arial`, and the
decoded Unicode title branch continues to use `SimHei`. A full SHA-only
physical-sheet rebuild regenerated 28 SVGs and PNGs; all SVG XML parses. RHO1
`Sheet38316` versus PDF page 6 was used only for acceptance: it confirms that
the change improves glyph proportion without translating the source-derived
pipe, dimension, frame, or title geometry. No PDF glyph, coordinate, or font
metric is used by the renderer.

### 2026-07-27: Recover short Sheet labels whose PSM reference is a page container

An SHA-internal text coverage scan found ten legitimate page-local labels that
were present in `Sheet` text records but absent from the SVG because their PSM
graphic reference resolved to a page-scale container. The affected labels are
`6068`; `229` and `105 MM`; `5/8`; `80X50`; `SEE SHT` and `15NPD`; and
`C68EXS4` and `230`, across LN Sheets 5632/6709, AMSS2 Sheets 5563/7763, and
UA Sheets 5818/6758.

Each has a valid normalized Sheet anchor, a 16-bit local graphic/style
reference, and at least three same-Sheet same-style peer labels with normal
30--320 page-unit PSM glyph heights. The renderer now replaces the container
only for a 3--10 character text record meeting all of those source conditions,
using the median anchor offset, height, and per-character width from its local
bounded peers. This rule deliberately excludes one- and two-character binary
false positives and unbounded/foreign references. Regression checks confirm
that the prior false `{f` token remains absent.

All ten labels are now present in their trace manifests. A full SHA-only
rebuild regenerated the 28 physical SVG pages and PNGs; SVG XML is valid and
the same internal coverage scan reports zero remaining legitimate ASCII text
candidates. PDF was used only to review the visible effect of the repaired
labels, never to provide text, coordinates, or metrics.

### 2026-07-27: Keep composite child types 11 and 16 inventory-only

The seven source SHA sets contain 292 type-11 and 38 type-16 children within
the observed `0x7B` composite record family. Unlike type-5 segments and
type-6 arcs, these children almost never resolve to an independent
`PSMcluster0` graphic envelope: only 8 type-11 and 2 type-16 refs have any
PSM envelope, and the two type-16 envelopes are page/container-sized. Most
occur inside parents that also contain type-5 detail; for example 57 of the
77 RHO1 type-11 children overlap a type-5 sibling bounding region.

Their four uint16 values therefore cannot yet be interpreted as standalone
line endpoints or an ellipse box. Rendering them as generic geometry would
duplicate existing flange/reducer/valve strokes or create false details. The
current type-5/type-6 component layer remains the only validated visible
decoder; types 11 and 16 stay inventory-only until a cross-sample primitive
layout and independent PSM identity prove a rendering rule. PDF was used only
to check that this decision does not correspond to a systematic missing
component outline.

### 2026-07-27: Validate all rendered micro connection points against page-local UCI evidence

The base renderer's micro-point filter was re-run across every physical Sheet
using the exact page-local dynamic-attribute rule used at render time: a
dynamic `graphic_ref` must occur in that Sheet, resolve to a PSM envelope no
larger than `45 x 45` page units, have no already-decoded vector reference,
and lie within 80 page units of decoded geometry using its SHA ellipse anchor
when available. The source calculation yields 362 accepted points; the 28
trace manifests contain exactly the same 362 `(graphic_ref, UCI)` pairs.

The remaining twelve UCI regions without a direct line/text/point association
are not evidence of missing symbols. Several have a PSM envelope far from the
current Sheet's decoded geometry, and the UA `0x1A00` reference repeats with
the identical PSM envelope on multiple physical Sheets. This is consistent
with the previously documented byte-containment ambiguity of dynamic refs.
Keep those UCI relations unresolved until a record-boundary or PSM-hierarchy
link is proven; do not synthesize a symbol or use PCF/PDF coordinates to place
one. PDF review found no systematic missing micro-symbol associated with this
set.

### 2026-07-27: Resolve repeated PSM envelopes for Unicode title-block text

All seven source `Sheet221` streams contain the same three length-prefixed
UTF-16 Chinese title-block labels. Six `PSMcluster0` streams expose one valid
envelope for each sibling graphic: company `0x08CC`, contractor `0x0C69`, and
project title `0x0CBB`. RHO1 repeats `0x0CBB`: the first valid-looking record
is a `4297 x 2458` page/container envelope, while the second is the real
`1888 x 96` glyph envelope `(10398,1647)-(12286,1743)`.

The Unicode-label decoder now inspects every valid PSM envelope belonging to a
nearby SHA sibling reference, then ranks them by the direct UTF-16 text
anchor and accepts only a height/width plausible for the `SimHei-Z`
StyleCluster metric. This returns the same `0x0CBB` glyph envelope in all
seven files and preserves the text's SHA transform as the rendered anchor.
The generic first-match `psm_bbox` helper remains unchanged for already
validated primitive families. This is a SHA-internal duplicate-record repair;
the PDF was only used as a post-render visual acceptance check.

### 2026-07-27: Make PNG review artefacts preserve the complete SVG viewBox

The previous browser command captured the upper-left pixels of a standalone
SVG whose intrinsic canvas is `16800 x 11880`; the resulting PNG could show
only the north marker even when the SVG contained a complete ISO. This was an
export/QA defect, not a SHA geometry defect. `render_svg_png.mjs` now first
reads the SVG `viewBox`, then embeds the unchanged SVG as an image in a
white HTML canvas with `object-fit: contain` before taking the browser
screenshot.

All 28 physical SVG pages were regenerated to full-page `3360 x 2373` PNGs.
The check confirms 28 physical SVGs, 28 matching PNGs, no missing PNG, and
valid XML for every SVG. PDF remains a visual comparison reference only; the
PNG helper reads only the renderer's SVG output and does not feed any PDF
content or coordinates back into the SHA decoder.

### 2026-07-27: AMSS2 physical page 1 acceptance review

`N400P3A-AMSS2-N444201-01/Sheet6` was reviewed using the full-page SHA SVG
PNG against PDF page 1, excluding the separately-added weld overlays. The
SHA-only layer contains the corresponding main pipe double-lines, insulation
dash, flow arrows, micro connection dots, dimension strings and extension
lines, component/support reference frames, `PI N444205` instrument circle,
free `SEE ISO`/coordinate notes, material table, revision/title panel, north
marker, logos and the three UTF-16 Chinese title-block labels.

No source-supported geometry or text-layout discrepancy was found in this
page. Several small frame labels appear faint at full-page review scale but
their SHA trace records are present and bound to direct closed rectangles
(for example `S24`, `S26`, `S23`, `S25`, `F10 G14 B18`, and `F9 G13 B17`);
they are not absent elements. No PDF content, coordinate, or font metric was
introduced by this acceptance review.

### 2026-07-27: Select overlapping SHA callout frames by PSM glyph extent

AMSS2 `Sheet34246` (physical page 4) exposed overlapping closed rectangles
around `S15` and `PS-N400-68971`. The direct Sheet anchor falls in both the
`198`-unit S15 frame and the `699`-unit PS-N frame. The previous sorted-list
first match chose the small frame even though the SHA PSM glyph envelope for
`PS-N400-68971` is `628` units wide, leaving its long cell visually empty.

For boxed marker/reference text with a normal PSM glyph extent, the renderer
now ranks anchor-containing SHA rectangles by the logarithmic match between
frame width and PSM text width, using centre distance only as a tie-breaker.
For page/container PSM extents it retains the anchor-distance fallback. This
maps `PS-N400-68971` to `(6317,3657)-(7016,3731)` while keeping `S15` in
`(6317,3751)-(6520,3825)`. The rule is based entirely on the Sheet anchor,
direct rectangle segments, and PSM envelope; PDF served only to reveal the
empty-cell symptom.

### 2026-07-27: Recover repeated-PSM static template labels

Two source templates retained a fixed Sheet221 text record but appeared to
lose one title-block label because `psm_bbox` selected a repeated page-scale
PSM envelope. N491 `OF` (`0x0959`) has an invalid first box
`(2557,0)-(5468,2314)` and a valid glyph box `(12471,162)-(12566,215)`;
AMSS2 `PID NO.` (`0x1168`) similarly has several container boxes before its
valid `(10521,299)-(10763,352)` glyph box. Both retain the same direct Sheet
anchor and StyleCluster text family as the other static template labels.

`template_text_records` now enumerates all same-reference PSM boxes, rejects
those too large for a title-block glyph, and chooses the remaining candidate
nearest to the local Sheet text anchor. N491 and AMSS2 now each expose all 23
static template labels. This uses only their own `Sheet221` text records and
`PSMcluster0` candidates; no PDF text or geometry is copied.

### 2026-07-27: Prefer the direct text-to-frame parent sequence

Across all samples, 344 of 414 validated boxed-marker/reference text records
have a closed-rectangle parent at `text graphic_ref + 5`. The existing
anchor/PSM-width selector already agreed for 343 of those objects. UA
`Sheet4779` `G12 B14` (`0x14AF`) was the one disagreement: it selected an
overlapping candidate frame instead of its direct parent `0x14B4`.

The renderer now uses the `+5` parent only when that exact id decodes to a
closed SHA rectangle; all other labels retain the existing PSM-width/anchor
fallback. The UA label now binds to `(3696,2646)-(4098,2720)`. This is a
record-boundary relationship confirmed across seven SHA sets, not a PDF-based
spatial adjustment.

### 2026-07-27: Recover composite callout frames from adjacent child ids

LN `Sheet1046` contains support/component callouts such as `S3`, `SD010`,
and `PS-100-00742` whose visible long frame initially appeared empty. These
are not the earlier `text graphic_ref + 5` family. Their four type-5 composite
edges share one composite parent, and their child references form the sequence
`text graphic_ref + 1` through `+4`. Composite coordinates are quantised, so
nominally horizontal/vertical edges can differ by one or two page units.

The renderer now detects those closed frames using a two-page-unit tolerance
and binds the preceding text record to that direct composite sequence. It
recovers all 24 such frame/text pairs on this Sheet, including all seven
support/reference labels. The PDF exposed the blank-cell symptom only; the
relationship, geometry, and tolerance were derived from the SHA stream.

### 2026-07-27: Preserve imperial inch marks in SHA callout text

The same full-page frame scan found eight non-empty direct-frame texts that
were absent from the SVG despite having valid SHA text records and frames:
`SD010 1/2\"` on LN `Sheet1046` and `SD010 1\"` on LN `Sheet5632`/`Sheet6709`.
Their failure was a renderer filter defect: the printable ISO character
whitelist omitted the double quote used as an inch mark, so the renderer
discarded otherwise valid SHA text before frame binding.

The whitelist now accepts the double quote while retaining its binary-text
guard. All 28 re-rendered physical Sheets have zero non-empty texts missing
from the verified composite-frame relation. This was established by SHA text
records, type-5 child references, and generated trace manifests; PDF was not
used to recover the text.

### 2026-07-27: Unresolved ordinary-line visibility filter retained

AMSS2 `Sheet6` visual QA shows a small blank frame beneath the `6 / 80X25NPD`
callout that is not visible in the PDF. Its four sides are ordinary Sheet
records with parent `0x03C3` and children `0x03C2`, `0x03C5`, `0x03C6`, and
`0x03C8`; they share the nearby component UCI but do not form the established
composite `text-ref + 1..4` relation. The validated PSM space-map table does
not expose these ids as children, so its visibility/parent semantics remain
unproven.

An attempted rule to hide empty composite frames did not affect this object
and was reverted. No generic deletion rule was added: it could erase genuine
component geometry or intentionally blank drawing fields. This is an explicit
remaining PSM-hierarchy decoding target, with the PDF used only to flag the
visual mismatch.

### 2026-07-27: Remove verified duplicate 18/32 callout backing frames

AMSS2 `Sheet5563` page 2 exposed a repeated-frame pattern distinct from the
unresolved ordinary-line case. Each affected callout has: (1) a correct type-5
composite frame with child refs `text_ref + 1..4`, and (2) an offset closed
frame in the local `18/32` record family whose parent is `text_ref + 5`. Both
sets are present in SHA, but only the composite frame aligns with the direct
text/PSM evidence and the PDF; rendering both produced blank, displaced boxes
around every S/PS callout.

The renderer now omits only those `18/32` edges whose parent closes to a
rectangle and whose preceding text has an independently decoded composite
frame. It retains all other `18/32` segments, including component details and
frames without the proven duplicate relationship. On AMSS2 page 2 this removes
140 duplicate edges while preserving the correct labels, leaders, pipework and
dimensions. All 28 physical Sheets were regenerated and their SVG/PNG/trace
artifacts validated.

### 2026-07-27: Guard composite frame sequences by text class

RHO1 `Sheet6` had a false sequence collision: free text `SEE ISO` (`0x0621`)
was immediately followed by four composite child ids that happened to close a
rectangle used by the separate `CLASS/INSUL/TRACE` annotation. The former
generic sequence rule bound `SEE ISO` into that unrelated rectangle, creating
a large displaced note absent from the PDF.

Composite `text_ref + 1..4` binding is now restricted to observed boxed text
classes: marker codes, PS/PANDA references, `SD...` support labels, and short
numeric boxed labels. Ordinary free annotations retain their direct SHA anchor
and PSM glyph metrics. RHO `SEE ISO` now has no source frame; an all-page trace
scan found zero remaining framed texts outside these classes (with CIxx kept as
its separately verified preceding-frame relation). All 28 physical artifacts
were regenerated and structurally validated.

### 2026-07-27: Template cell alignment remains StyleCluster-dependent

Title-block QA on LS `Sheet8093` found visible font/alignment differences in
the `N400`, `N400P3A`, and `80 mm` cells. The SHA records prove that their
Sheet insertion anchors (`9591`, `10025`, `11128` page units) lie near the
right side of their respective PSM glyph envelopes (`9353..9628`,
`9775..10268`, `10852..11209`), rather than at a common left baseline. This
indicates an unresolved alignment/justification semantic in template style
`0x1FB5`; substituting the raw anchor globally would move the rendered fields
but is not source-proven and could regress the other template cells.

No PDF coordinate was used to adjust these values. The current renderer keeps
the verified PSM envelope placement and records this as a remaining
StyleCluster/PSM alignment decoding target.

### 2026-07-27: PSMcluster status field is not a visibility flag

Direct inspection of `PSMcluster0` confirms a local record layout of graphic
reference followed by four uint16 bounds and a fifth uint16 status-like value.
AMSS2 `Sheet6`'s apparent empty-frame parent `0x03C3` has status `5`, but so
do many ordinary-line objects and the real four-side box surrounding the
visible `6` callout (`text 0x03C7`, anchor inside the `0x03C3` frame).

Status values `5`, `6`, `11`, `16`, and `99` occur across visible Sheet
geometry; status `5` therefore cannot be treated as an invisibility switch.
No renderer filter was added. A safe rule still requires a decoded parent or
layout relation in addition to this field.

### 2026-07-27: Additional visual QA pages accepted for this pass

The current SHA-only renders were compared visually with their PDF pages for
CHW N434591 page 2, LS N492164 pages 1 and 3, RHO1 N434201 page 2, and LN
276994 page 2. Excluding the separately injected weld overlay, their reviewed
pipe topology, branch/component outlines, flow arrows, dimensions, page links,
material table structure, and title-frame geometry matched the PDF reference.
The known template font/alignment limitation remains recorded separately; no
PDF geometry, text, or coordinates was used in this acceptance pass.

### 2026-07-27: SHA-rendered PCF weld-number delivery set

The supplied `焊口号反写PCF示例` folder contains seven exact same-directory
PCF/SHA pairs. For each pair, existing PCF `REPEAT-WELD-IDENTIFIER` values were
matched by UCI to a visible SHA connection point, then written as experimental
diamond/leader callouts into a new SHA copy. Every PNG was rendered from that
welded SHA copy; PCF supplied weld identity only, never ISO geometry.

The delivery manifest records 370 PCF weld records, 335 visible SHA-UCI
matches/callouts, and 28 rendered PNG pages. The remaining 35 PCF weld records
had no visible SHA target under the established point-to-geometry test and were
deliberately not placed by inference.

### 2026-07-27: Complete SHA-only physical-Sheet text coverage sweep

All 28 physical Sheets were scanned for text records with a finite normalized
direction, the established ISO character set, a local PSM glyph envelope, and
reasonable glyph dimensions. The initial 88 apparent omissions were classified
from SHA streams: repeated `MESC NO & COMPONENT DESCRIPTION` records are
already emitted from shared `Sheet221`; `Today's Date ...` records are
extraction metadata rather than a visible ISO field; and several one-character
records have `graphic_ref=0` and are binary false positives.

After those SHA-internal exclusions, the remaining count is zero: every
eligible physical-Sheet text record is present in its SVG. This confirms text
coverage only; it does not claim that unresolved composite or PSM hierarchy
records have been decoded. No PDF OCR or text was used in the sweep.

### 2026-07-26: LN four-page post-prefix review complete

`100P3A-LN-276994-01` was rechecked across `Sheet6`, `Sheet1046`, `Sheet5632`,
and `Sheet6709` against its four PDF pages. The simple headerless page 2 keeps
the verified sibling-viewbox inheritance; its A1 border, scale, split links,
pipe coordinates and title block match the physical page. Pages 3 and 4 retain
their small-bore fittings, S8/S15/S20 reference frames, material descriptions,
PSV/PCV instrument bubbles, grid lines, flow arrows, dimensions and cross-page
links. No new non-weld base-element discrepancy was identified. The PDF served
only as visual acceptance; this review introduced no PDF-derived source data.

### 2026-07-27: AMSS2 and UA later-page visual QA, with no unsafe vector filter

The SHA-only base render was compared against AMSS2 N444201 pages 3--5
(`Sheet7763`, `Sheet34246`, and `Sheet36113`) and UA N495128 pages 3--6
(`Sheet5818`, `Sheet6758`, `Sheet8178`, and `Sheet34136`). Weld diamonds and
leaders were excluded from this review. The body pipe topology, branch points,
instrument bubbles, flow arrows, dimensions, cross-sheet links, BOM structure,
border, and title-block placement all remain consistent with the PDF visual
reference.

AMSS2 `Sheet34246` and the denser UA junction pages still render more local
construction/detail strokes around certain flange/valve clusters than the PDF
appears to show. The candidate strokes are emitted from the same SHA `18/32`
two-point family and type-5 composite family that also contain verified visible
component outlines. Only 68 of AMSS2 `Sheet34246`'s 1,286 `18/32` children
have a local PSM candidate, with status-like values 5 and 6 shared by known
visible geometry. This is insufficient to classify the remaining lines as
hidden or auxiliary. No visibility filter was introduced, and no PDF
coordinates, OCR, vectors, or pixels were used in rendering. The remaining
work is to decode the parent/layer semantics in `PSMspacemap`/`PSMroots` before
any further suppression is considered safe.

### 2026-07-27: `18/32` local object group field is not a visibility filter

Raw AMSS2 `Sheet34246` `18/32` records contain a previously unused uint16 at
byte 14. It groups records strongly (for example 770 records under `34337`,
238 under `34339`, and 215 under `34335`) while byte 10--13 is the enclosing
Sheet/local graphic reference and byte 20 is the proven StyleCluster line
style. The grouped sets mix children with direct dynamic-attribute UCI links
and undecorated detail strokes; their parent-like ids have neither a PSM
envelope nor their own dynamic UCI entry. Therefore this field establishes a
local object grouping, but not an enabled/disabled layer or visibility state.

The renderer deliberately continues to preserve all such strokes. A future
decoder must link this local grouping to a validated PSM parent relation before
using it to filter complex component geometry. This conclusion is derived only
from the SHA record layout, UCI table, and PSM table; the PDF only motivated
the audit.

### 2026-07-27: Expose verified local object groups in SVG provenance

The renderer now decodes the `0x13/0xAC` relation records into a
`graphic_ref -> local_object_group` map and emits it as `data-local-group` in
the SVG plus `local_object_group` in each trace segment. This is explicitly
provenance metadata, not a visibility rule. Re-rendering AMSS2 `Sheet34246`
produced a valid PNG with 221 marked line segments across 65 graphic refs in
groups `0x8621` and `0x8623`; its visible output was otherwise unchanged.

This makes component-cluster investigation reproducible from SHA evidence and
allows a future PSM-parent decoder to test exact group membership without
re-parsing byte offsets. The trace must not be interpreted as permission to
hide a group until a source-proven visibility relation exists.

RHO1 N434201 pages 3 and 4 (`Sheet7953` and `Sheet34639`) were also reviewed
after the same renderer rebuild. Their long-run double lines, branch topology,
flow arrows, boxed component/support labels, cross-sheet references, dense
valve/instrument groups, material table, and title frame retain the PDF's
relative arrangement. No new SHA-only correction was justified; the remaining
dense-stroke limitation is the shared `18/32`/type-5 parent-semantics issue
described above.

RHO1 pages 5--7 (`Sheet37157`, `Sheet38316`, and `Sheet40376`) complete the
same physical-Sheet review for this drawing. The simple long run on page 5 and
the denser branch/end-connection arrangements on pages 6--7 retain their PDF
topology, connection locations, pipe double lines, flow arrows, dimensions,
boxed annotations, cross-sheet links, BOM, and title fields. No missing base
element or independently source-proven positional error was found. These pages
remain subject only to the documented complex-junction stroke-density limit;
weld overlays were excluded throughout.

CHW N491163 page 1 (`Sheet6`) was reviewed as a complete single-page case.
Its main isometric body, north marker, grid references, dashed pipe/insulation
conventions, flow arrows, component/support boxes, dimensions, material table,
and Chinese/English title-block fields retain the PDF layout. No missing base
SHA primitive or source-proven text/geometry offset was identified. The
existing complex-junction stroke-density limitation remains the only open
renderer issue for this page; weld callouts were excluded.

CHW N434591 page 1 (`Sheet6`) completes the visual review of its two-sheet
ISO alongside the previously accepted page 2. Its double-line pipe runs,
instrument bubble, insulation labels, dimensions, cross-sheet references,
component/support frames, BOM, and bilingual title frame agree with the PDF
reference. No missing base element or SHA-supported placement correction was
found; weld overlays remain outside the review scope.

LS N492164 page 2 (`Sheet5368`) was reviewed between the already accepted
pages 1 and 3. Its small-bore branches, elbows, instrument bubble, flow and
orientation notes, dimensions, boxed component/support references, cross-sheet
links, BOM, and title fields agree with the PDF reference. No new source-proven
base-element correction was found. The shared dense-junction vector-layer
limitation remains recorded separately; weld callouts were excluded.
