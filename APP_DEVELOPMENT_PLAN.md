# PCF + SHA Piping ISO App Development Plan

## 中文执行摘要

本项目建议建设为一个本地优先的桌面 App。用户导入 PCF 与 SHA 后，
系统同时建立“工程拓扑模型”和“二维出图模型”，并保留两者之间可追溯的
映射关系。PDF 只用于人工或自动视觉验收，不作为反向提取数据源。

产品由五个相互连接的工作台组成：

1. **分图工作台**：识别同一管线内部的跨页接口，显示工程坐标与图纸位置，
   支持将分图点移动到另一个合法连接点，并只重算受影响页面。
2. **模板工作台**：解析和维护图框、标题栏、材料表、版本栏、中英文字段、
   字体、线型、比例和项目绑定规则，使 EP3D 出图可按原项目模板复现。
3. **焊缝工作台**：在已有连接处或直管中间增加焊缝，自动拆分直管、保持
   拓扑与长度守恒，并生成焊缝符号、编号、引线及 PCF 派生文件。
4. **元件规则工作台**：按项目沉淀 UCI、Sheet、PSM、StyleCluster 与矢量
   图元的组合规律，形成可版本化、可解释、可回归测试的元件规则库。
5. **联合出图工作台**：以 PCF 提供的工程语义和三维坐标为基础，以 SHA
   提供的页面、模板、样式与二维图元知识为依据，生成可追溯 SVG/PDF/PNG。

首个可用版本不追求立即替代完整 PDMS/EP3D 出图引擎，而是先交付导入、
五页浏览、对象追踪、分图识别和诊断报告。随后依次增加分图编辑、模板复现、
焊缝编辑、项目规则学习和联合排版能力。四人团队预计约 10 周形成分析型
MVP，完整可生产出图引擎预计 9 至 12 个月；单人持续开发预计 15 至 20 个月。

所有源文件保持只读，修改保存为项目覆盖层和新的派生文件。任何对象映射都
必须标明 `direct`、`derived`、`candidate` 或 `unresolved`，避免把推断结果
当成源文件事实。

## 1. Product Goal

Build a local-first desktop application that imports one or more PCF and SHA
files, links their engineering and drawing objects, lets users inspect and
modify drawing-generation decisions, and exports traceable SVG, PDF, PNG,
JSON, and derived PCF artifacts.

The application should eventually support five connected workflows:

1. Detect and modify same-line ISO split points.
2. Reproduce project-specific EP3D/Shape2D drawing templates.
3. Add weld nodes to PCF topology, including welds inserted into straight pipe.
4. Learn and maintain project-specific SHA component drawing rules.
5. Generate ISO drawings from the combined PCF engineering model and SHA
   template/style knowledge.

The source PCF and SHA files must remain immutable. User changes are stored as
project overrides and exported as new derived files.

## 2. Recommended Product Shape

### 2.1 Application type

Use a cross-platform desktop application rather than a browser-only service.

Reasons:

- PCF, SHA, PDF, and project templates are normally local engineering files.
- Projects can contain confidential plant and supplier information.
- SHA parsing and SVG generation already exist as local Python tools.
- Large binary files and multi-page vector previews are easier to process
  without uploading them to a remote server.
- A later optional collaboration server can reuse the same project schema and
  engine API.

### 2.2 Recommended technology

| Layer | Recommended technology | Responsibility |
|---|---|---|
| Desktop shell | Electron | File selection, local process lifecycle, packaging |
| User interface | React + TypeScript | Project workspace, inspectors, editors, SVG canvas |
| Parsing/rendering engine | Python 3.11+ | Existing PCF/SHA parsers, linker, layout and export |
| Local API | FastAPI or JSON-RPC over stdio | Stable boundary between UI and Python engine |
| Project database | SQLite | Normalized objects, overrides, jobs and provenance |
| Binary/artifact storage | Project directory | Original files, SVG, PNG, PDF, manifests |
| Rule profiles | Versioned JSON/YAML | Project templates, styles and component rules |

Electron is recommended for the first production version because launching and
packaging the existing Python engine is simpler than embedding it in a Rust
desktop shell. Tauri can be reconsidered after the engine API and packaging
requirements stabilize.

## 3. User Workflow

### 3.1 Create a project

1. Create a local project workspace.
2. Import one or more PCF files.
3. Import matching SHA files.
4. Optionally import PDF files for visual QA only.
5. Choose or create a project rule profile.
6. Run analysis.

The application should detect likely PCF/SHA pairs from pipeline references,
sheet titles, UCI values, and file names. The user confirms uncertain matches.

### 3.2 Review analysis

The project dashboard reports:

- PCF pipeline and component counts.
- SHA logical page count and Sheet streams.
- UCI and internal graphic counts.
- PSM stream status.
- PCF-to-SHA link coverage.
- Same-line split interfaces.
- Missing styles, external resources, and unresolved graphics.
- Direct, derived, candidate, and unresolved mapping counts.

### 3.3 Inspect a drawing

The user opens a multi-page ISO viewer and can:

- Zoom and pan the SHA-derived SVG.
- Switch between ISO pages.
- Click a pipe, fitting, weld, annotation, dimension, or title-block item.
- See the PCF block, UCI, SHA graphic reference, Sheet record, PSM envelope,
  style record, coordinates, and mapping confidence.
- Highlight the same object in the PCF topology tree and all pages.
- Compare the generated preview with an optional PDF QA layer without using
  PDF coordinates as reconstruction inputs.

### 3.4 Modify

The application supports project overrides:

- Move a same-line split from one connection/component to another.
- Insert a weld into a straight pipe at a distance or engineering coordinate.
- Change a weld identifier, field/shop type, and displayed weld mark.
- Select a template and title-block field mapping.
- Change a component-symbol rule for the current project profile.
- Adjust supported annotation placement policies.

Overrides never rewrite the imported SHA. They update the normalized project
model and trigger regeneration.

### 3.5 Export

The application exports:

- Derived PCF with added welds or normalized metadata.
- SVG with source/provenance attributes.
- PDF and PNG previews.
- Trace JSON for every rendered element.
- Split-point and weld reports.
- A reusable project rule profile.
- A validation report listing unresolved or candidate mappings.

## 4. Core Architecture

```text
Imported PCF ──> PCF parser ───────┐
                                   │
Imported SHA ──> SHA/PSM parser ───┼─> Normalized project graph
                                   │              │
Optional PDF ─> QA comparator ─────┘              │
                                                  ├─> Split engine
Project profile ─> Rule/style/template library ───┼─> Weld engine
                                                  ├─> Layout engine
                                                  └─> SVG/PDF/PNG/PCF exporters
```

### 4.1 Engine modules

| Module | Main responsibility |
|---|---|
| `ingest` | Hash, preserve and register imported source files |
| `pcf_parser` | Parse pipelines, components, points, attributes and materials |
| `sha_container` | Read OLE streams and resource inventory |
| `sha_sheet` | Parse pages, viewports, primitives, text and composites |
| `sha_psm` | Parse PSM envelopes, hierarchies, registries and namespaces |
| `sha_style` | Resolve fonts, text metrics, line styles and symbol styles |
| `linker` | Build PCF UCI to SHA graphic to Sheet/PSM/primitive links |
| `topology` | Build the engineering connectivity graph |
| `split_engine` | Detect, validate, move and regenerate ISO boundaries |
| `weld_engine` | Insert and validate weld nodes and split straight pipe |
| `template_engine` | Load title blocks, BOM layouts and project bindings |
| `rule_engine` | Apply project-specific component and annotation rules |
| `layout_engine` | Convert the engineering graph into page-space objects |
| `renderer` | Emit traceable SVG and prepare PDF/PNG export |
| `validator` | Check topology, page continuity, provenance and render coverage |

### 4.2 Repository target structure

```text
parse-piping-sha/
  apps/
    desktop/                  # Electron application
    ui/                       # React/TypeScript workspace
  engine/
    api/                      # FastAPI or JSON-RPC service
    domain/                   # Normalized project model
    parsers/
      pcf/
      sha/
    linking/
    split/
    weld/
    template/
    rules/
    layout/
    render/
    validation/
  packages/
    schema/                   # JSON Schema and TypeScript types
  profiles/
    base/
    projects/
  fixtures/
    sanitized/
  tests/
    unit/
    integration/
    golden/
  docs/
```

The current scripts should be moved behind engine interfaces only after
regression fixtures capture their current outputs.

## 5. Normalized Data Model

The data model is the most important architectural boundary. PCF engineering
coordinates and SHA drawing coordinates must never share an ambiguous field.

### 5.1 Main entities

| Entity | Required fields |
|---|---|
| `Project` | id, name, profile version, created time |
| `SourceFile` | id, path, SHA-256, type, immutable copy, import version |
| `Pipeline` | pipeline reference, specification, units |
| `Component` | id, PCF type, UCI, attributes, material/item code |
| `EngineeringPoint` | east, north, elevation, branch role |
| `Connection` | endpoint A, endpoint B, component relation |
| `Weld` | id, engineering point, pipe distance, type, mark, source |
| `IsoPage` | logical page, total pages, Sheet stream, viewport |
| `GraphicObject` | graphic ref, UCI candidates, page, PSM record |
| `Primitive2D` | line/arc/ellipse/text/composite, drawing coordinates |
| `TextObject` | content, anchor, direction, style, binding |
| `Style` | font, height, width ratio, line type, line weight |
| `Template` | frame, title block, BOM regions, field bindings |
| `SplitBoundary` | upstream/downstream graph edges, page pair, override |
| `ProjectRule` | matcher, symbol/layout action, evidence, version |
| `RenderElement` | geometry/text, provenance chain, confidence |

### 5.2 Coordinate types

Use explicit types:

- `EngineeringPoint3D`: PCF plant coordinates.
- `DrawingPoint2D`: Shape2D normalized coordinates.
- `PagePoint`: nominal `16800 x 11880` page units.
- `SvgPoint`: SVG viewBox coordinates.

Every transformation must be named, versioned, and recorded in the trace.

### 5.3 Provenance and confidence

Every relationship and rendered element carries:

- `direct`: exact source reference.
- `derived`: deterministic transformation from direct source fields.
- `candidate`: spatial or heuristic association.
- `unresolved`: stored but not interpreted.

The UI must display these states and prevent candidate evidence from silently
becoming a destructive PCF or project-rule change.

## 6. Functional Modules

### 6.1 Import and pairing

MVP requirements:

- Drag/drop and file-picker import.
- Immutable source copies and hashes.
- PCF/SHA file-type validation.
- Multi-page SHA inventory.
- Automatic pipeline-reference pairing.
- Duplicate and version detection.
- Import error report with stream/block offset.

### 6.2 Multi-page ISO viewer

- SVG canvas with page thumbnails.
- Layer controls: pipe geometry, components, welds, dimensions, annotations,
  template, BOM, PSM envelopes and debug IDs.
- Element inspector with full provenance chain.
- Search by UCI, component id, weld id, item code or graphic ref.
- Highlight cross-page and split-boundary objects.
- Export the current page or all pages.

### 6.3 Split-point editor

The split model should operate on topology edges, not on annotation text.

Initial algorithm:

1. Detect page-to-page `SHT` links from SHA.
2. Find UCI/graphic objects shared by or adjacent to both pages.
3. Map them to PCF components and shared engineering endpoints.
4. Produce ranked split-boundary candidates.
5. Require confirmation when no direct UCI/endpoint chain exists.

Editing a split:

1. User selects the current boundary.
2. User chooses another valid connection/component edge.
3. Engine validates page continuity and component ownership.
4. Layout engine regenerates affected pages.
5. Validator confirms paired continuation marks and no orphan components.

The first version stores split overrides in the project database. Writing
native proprietary SHA split records is a later research item, not an MVP
requirement.

### 6.4 Weld insertion editor

Support three insertion methods:

- Select an existing PCF connection.
- Select a straight pipe and enter distance from an endpoint.
- Enter an engineering coordinate that lies on a straight pipe segment.

For an inserted straight-pipe weld, the engine must:

1. Validate that the point lies on the pipe within tolerance.
2. Split the pipe into two valid PCF pipe components.
3. Preserve material, bore, specification and relevant attributes.
4. Add a weld entity and unique component/UCI identifiers.
5. Update topology, material quantities and downstream split candidates.
6. Generate the weld dot, item number and weld annotation using project rules.
7. Export a new PCF and a change manifest.

### 6.5 Template editor

Template capture includes:

- Sheet viewport and paper size.
- Border and internal title-block grid.
- Static and bound text.
- Revision table.
- BOM column definitions and row rules.
- Logo/image resources.
- Font, text width, line type and line weight mappings.
- North arrow, notes and project-specific symbols.

The template editor should initially expose structured fields and a live
preview. Free-form CAD editing is outside the first release.

### 6.6 Project rule library

Rules are selected by project/profile and can match:

- PCF component type and attributes.
- Bore/specification/material.
- UCI/dynamic attribute patterns.
- SHA object/style/PSM evidence.
- Connectivity and orientation.

A rule produces:

- Symbol primitive family.
- Anchor/connection policy.
- Annotation template.
- Weld/split behavior.
- BOM classification.
- Confidence and source examples.

Rules require versioning, fixtures and an approval state:
`experimental`, `validated`, or `production`.

## 7. API Contract

The UI should never import parser implementation modules directly. A stable
local API allows the engine and app to evolve independently.

Initial endpoints or equivalent JSON-RPC methods:

| Operation | Purpose |
|---|---|
| `project.create` | Create project workspace |
| `source.import` | Import and hash PCF/SHA/PDF |
| `analysis.run` | Parse, link and validate sources |
| `analysis.status` | Job progress and diagnostics |
| `pages.list` | Logical ISO page inventory |
| `page.render` | Generate SVG and trace |
| `element.get` | Provenance and linked engineering object |
| `split.list` | Detected split boundaries |
| `split.override` | Store and validate changed boundary |
| `weld.insert` | Add weld override and split pipe |
| `template.get/update` | Template/profile configuration |
| `project.render` | Regenerate affected or all pages |
| `export.create` | Produce PCF/SVG/PDF/PNG/report package |

Long operations run as cancellable jobs with progress events. Job outputs are
content-addressed so unchanged inputs do not need to be parsed again.

## 8. Delivery Phases

### Phase 0: Baseline and test corpus

Estimated effort: 2 weeks.

Deliverables:

- Sanitized fixtures covering single-page and five-page SHA files.
- Matching PCF, optional PDF QA, and expected page/element counts.
- Golden SVG/trace outputs for the current parser.
- Formal source immutability and evidence rules.
- Initial JSON Schema for the normalized graph.

Exit criteria:

- Existing command-line outputs are reproducible in CI.
- Each fixture has expected page, UCI, graphic, PSM and split counts.
- No test derives reconstruction coordinates from PDF.

### Phase 1: Import and analysis desktop MVP

Estimated effort: 4 weeks.

Deliverables:

- Desktop shell and project workspace.
- PCF/SHA import and pairing.
- Background analysis jobs.
- Project summary and diagnostics.
- Five-page SVG viewer and trace inspector.
- Export of existing SHA-derived SVG/PNG/JSON.

Exit criteria:

- A user can create a project, import the current five-page sample and inspect
  all five pages without using the command line.
- Counts match the CLI baseline.
- Clicking a direct UCI-linked object shows its source chain.

### Phase 2: Stable domain graph and linker

Estimated effort: 4 weeks.

Deliverables:

- Normalized PCF topology model.
- Normalized SHA drawing model.
- UCI/graphic/Sheet/PSM/primitive link table.
- Confidence-aware diagnostics.
- Search and cross-page highlighting.

Exit criteria:

- Every PCF component and every dynamic SHA graphic is accounted for.
- Unlinked objects are explicit and exportable.
- Direct and candidate links are never conflated.

### Phase 3: Split-point analysis and editing

Estimated effort: 5 weeks.

Deliverables:

- Same-line split detection.
- PCF engineering-coordinate mapping.
- Cross-page boundary viewer.
- Split override editor.
- Affected-page regeneration and validation.

Exit criteria:

- The current five-page sample exposes four paired interfaces.
- Moving a split to another valid flange/connection changes the correct page
  ownership and continuation marks.
- Regenerated pages contain no missing or duplicated components.

### Phase 4: Template/profile system

Estimated effort: 5 weeks.

Deliverables:

- Versioned template schema.
- EP3D-to-original template field mapping.
- Title block, revision table and BOM generator.
- Style and Unicode font mapping.
- Project profile import/export.

Exit criteria:

- The selected project profile reproduces the known frame, title block, BOM
  columns, revision fields, Chinese text and page numbering from SHA sources.
- Template changes do not require modifying parser code.

### Phase 5: Weld insertion and PCF export

Estimated effort: 5 weeks.

Deliverables:

- Straight-pipe weld insertion.
- Existing-connection weld insertion.
- Weld identity and mark editor.
- Topology/material validation.
- Derived PCF and change-manifest export.
- Weld symbol and annotation rendering.

Exit criteria:

- A weld can be inserted at a valid distance on a straight pipe.
- The pipe is split correctly, quantities remain consistent, and identifiers
  are unique.
- Re-importing the derived PCF reconstructs the same topology and weld.

### Phase 6: Project component-rule learning

Estimated effort: 6 weeks and ongoing.

Deliverables:

- Rule authoring and approval UI.
- Sample-to-rule comparison reports.
- Project-specific component symbol library.
- Regression fixtures per rule/profile.
- Exportable rule package for other PDF/SVG rendering software.

Exit criteria:

- A validated rule reproduces the same symbol family and connection anchors
  across multiple drawings from the same project.
- Rules with conflicting evidence remain experimental and do not run in
  production mode.

### Phase 7: Combined PCF + SHA drawing engine

Estimated effort: 8 weeks.

Deliverables:

- PCF topology-to-layout pipeline.
- Automatic page allocation and split selection.
- Component, weld, dimension and annotation placement.
- Template/BOM rendering.
- SVG, PDF, PNG and trace export.

Exit criteria:

- A PCF plus project profile generates a complete multi-page ISO package.
- Every visible element has a source or rule provenance record.
- Golden tests pass for geometry, topology, page continuity and key visual
  regions.

### Phase 8: Production hardening

Estimated effort: 4 weeks.

Deliverables:

- Signed installers and update mechanism.
- Crash recovery and project backups.
- Performance profiling and caching.
- Security review and dependency inventory.
- User documentation and diagnostic bundle export.

## 9. Testing Strategy

### 9.1 Unit tests

- PCF block and coordinate parsing.
- OLE stream inventory.
- Sheet viewport and primitive records.
- PSM record framing and full-stream consumption.
- Style and text bindings.
- Topology operations and pipe splitting.
- Coordinate transformations.

### 9.2 Integration tests

- PCF/SHA pairing.
- UCI-to-graphic-to-page links.
- Multi-page continuation chains.
- Split override and page regeneration.
- Weld insertion and PCF re-import.
- Template/profile application.

### 9.3 Golden tests

Store expected:

- Page counts and Sheet identities.
- UCI and graphic counts.
- PSM node/record counts.
- SVG element manifests.
- Selected SVG crops rendered to images.
- Exported PCF topology summaries.

PDF can be used to identify visual regressions, but fixes must be traced to
PCF, SHA, profile, or rule inputs. PDF paths and OCR coordinates are never
accepted as renderer geometry.

### 9.4 Property checks

- Source files remain byte-identical.
- Every connection has valid endpoint ownership.
- Every inserted weld lies on its parent pipe.
- Material length before and after pipe splitting remains within tolerance.
- Every split interface has two consistent continuation marks.
- Every rendered element has provenance and confidence.

## 10. Security and Data Integrity

- Process files locally by default.
- Never execute embedded OLE content.
- Treat text and XML as untrusted input.
- Apply file-size, record-count and recursion limits.
- Store source hashes and read-only copies.
- Use atomic writes for project state and exports.
- Keep automatic backups before topology or profile changes.
- Produce an explicit change manifest for every derived PCF.

## 11. Team and Schedule Assumptions

For a small team of one desktop/frontend engineer, two Python/geometry
engineers, and one QA/domain engineer, a usable analysis MVP is approximately
10 weeks and a production-capable combined drawing engine is approximately
9–12 months.

For one engineer working sequentially, expect roughly 15–20 months because
format research, UI implementation, topology editing, rendering and fixture
validation compete for the same time.

Research on proprietary Shape2D semantics continues in parallel. Unknown PSM
semantics should not block the MVP when the required behavior already has a
direct Sheet/UCI/PCF evidence path, but unknowns must remain visible in the
trace and validation report.

## 12. Immediate Next Sprint

The first two-week sprint should deliver:

1. Freeze the current five-page sample as a regression fixture or create a
   sanitized equivalent.
2. Define the normalized project JSON Schema.
3. Refactor the existing scripts behind a single `analyze-project` engine
   command without changing their behavior.
4. Create an Electron/React project shell.
5. Implement project creation and PCF/SHA import.
6. Run analysis as a background process and display page/UCI/PSM counts.
7. Embed the generated SVG for all logical pages.
8. Show the trace JSON for a selected element.

Sprint acceptance:

- Importing the current PCF and SHA produces five selectable pages.
- The app reports 196 unique UCI values and 229 page graphic references for
  the current sample, with all page graphics resolving to PSM envelopes.
- The four same-line page interfaces are listed.
- Original files are unchanged and source hashes are visible.
- The same analysis can be repeated with identical outputs.

## 13. Decisions Required Before Phase 1 Ends

These decisions should be made after the first working desktop prototype:

- Whether production installations may download a Python runtime or require a
  fully bundled offline installer.
- Whether user projects may use a shared collaboration server.
- Which PCF dialects and vendor extensions are in the supported contract.
- Whether derived PCF export must preserve original formatting byte-for-byte
  outside modified blocks.
- Which EP3D template format must be imported/exported.
- Which weld numbering and field/shop classification standards are required.
- Which project provides the second independent SHA corpus for rule
  validation.
