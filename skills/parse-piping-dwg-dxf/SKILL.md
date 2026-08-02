---
name: parse-piping-dwg-dxf
description: Analyse piping-isometric DWG/DXF vector drawings to classify welds, flow arrows, supports, elbows, straight-pipe runs, tees, branch outlets, flanges, reducers, and valves; produce vector-anchored whole-page and local review PNGs; and match the resulting DXF topology to IDF 100 straight records. Use for piping ISO DWG/DXF conversion, component/weld/support detection, straight-pipe segmentation, or IDF-to-DXF topology reconciliation.
---

# Piping DWG/DXF Vector Analysis

Use the DXF vector entities as the drawing-position authority. Convert a binary DWG to a separate DXF copy first; preserve the source DWG. Use IDF only as an independent topology and attribute source—never project IDF 3D coordinates directly onto sheet coordinates.

Read [flat-941-profile.md](references/flat-941-profile.md) for the calibrated 0.6-wide profile before analysing that drawing family. For a different export profile, inventory entities and create an unpromoted review queue; do not copy its numeric tolerances blindly.

## Required workflow

1. Render the unmodified DXF page, including its full border; crop only the drawing area for local review.
2. Inventory entity types, DXF handles, widths, blocks, extents, and layers. Prefer named blocks when they survive conversion.
3. Extract the raw pipe skeleton and retain each source handle and exact endpoint.
4. Classify geometry in the mandatory order below. A later class may not override an earlier owned body.
5. Build a typed graph, split it at physical boundaries, and assemble final pipe runs.
6. Render a whole-page overlay and numbered 3×3 local evidence panels. Record every classification with source filename, handles, rule, anchors, confidence, and evidence path.
7. Only after DXF review, parse IDF `100` records and perform topology matching.

## Mandatory classifier order

1. `RAW_PIPE` — project-profile pipe vectors only; do not call them final straight pipe.
2. `WELD` and `FLOW_ARROW` — vector contact with the pipe axis/endpoint is mandatory.
3. `ELBOW` — weld → continuous pipe-vector group → weld.
4. `COMPONENT` — closed body plus weld/contact topology: branch outlet, flange, reducer, or valve.
5. `SUPPORT` — physical paired support strokes at a pipe cross-section/end.
6. `PIPE_ROLE` — split at confirmed boundaries; contract arrows only inside an already split run.
7. `TEE` — recognise a three-leg weld-star only from endpoint roles.
8. `IDF_MATCH` — compare normalised typed graphs; never force a match from length alone.

## Promoted vector rules

### Raw pipe and final pipe roles

- In the calibrated flat profile, a two-point 0.6-wide `POLYLINE` is raw pipe only.
- Split at every weld, support, elbow boundary, branch, flange, reducer, valve, or terminal. A confirmed arrow is transparent and cannot be a pipe endpoint.
- Emit a final role, not generic “pipe”: `ARROW_PIPE`, `SUPPORT_PIPE`, `SUPPORT_WELD_PIPE`, `WELD_PIPE`, `SUPPORT_EMPTY_PIPE`, or `WELD_EMPTY_PIPE`. Exclude `ELBOW_PIPE` from straight-pipe output.
- Preserve real DXF end positions in overlays: use flat caps and do not invent rounded gaps or shifted circles.

### Weld and arrow ownership

- Accept a weld only when its closed body vector-contacts the pipe boundary. The calibrated profile contains compact closed weld bodies, including a six-sided outline with a local crossing-fill package.
- Classify the six-sided crossing-fill weld before flange/reducer/valve tests. Its owned closed outline is never a component candidate.
- Accept a flow arrow only when its open wedge bridges two collinear pipe sides. Do not treat a flow arrow as a physical break.

### Elbow and tee

- Accept an elbow only when its full continuous bend group is bounded by two confirmed welds. Include the short boundary vectors in the elbow body; stop at a weld, support, or component.
- Accept a tee only when exactly three `WELD_EMPTY_PIPE` runs share one empty endpoint and their three opposite endpoints are welds. Do not promote an ordinary two-leg bend or an untyped junction.

### Support

- For an in-path support cross-section, require two short pipe-parallel strokes on opposite pipe walls, aligned with the exact join of adjoining pipe vectors. Text, leaders, dimensions, and symbol centres are not support anchors.
- For a terminal support cross-section, allow an unsplit raw pipe endpoint only if two short zero-width strokes are pipe-parallel, opposite, and symmetric around that endpoint. Treat it as the same hard graph cut. A one-sided tick is insufficient.
- Never bridge a confirmed support: it separates two physical pipe runs.

### Components

- `BRANCH_OUTLET`: compact closed 8-vertex body with two distinct symmetric body-edge midpoints coincident with two distinct weld-axis centres. Require the associated branch topology; do not infer it from vertex count alone.
- `WELDED_FLAT_FLANGE`: plate-like closed body with one edge midpoint coincident with a weld-axis centre.
- `WELDED_LONG_NECK_FLANGE`: one connected group comprising the plate and its physically contacting trapezoid neck. Select the plate by boundary contact with the neck, never centroid proximity.
- `REDUCER`: complete two-interface taper with unequal interface widths and an outer parallel-side pair. Exclude it from flange and valve queues first.
- `VALVE`: classify only after both adjacent flange groups and their welds have been removed from the candidate graph.
- Do not use a body centre or loose spatial proximity as a component anchor.

## Rendering and review contract

- Always deliver the original DXF render and a vector overlay for the same view.
- Use small plain IDs and source handles in local panels. Highlight the complete component body, never a guessed centre point.
- For uncertain classes, generate 3×3 local panels and ask for panel IDs. Keep confirmed and rejected samples in separate audit rows; do not treat screenshots as geometry input.
- Recommended colours: cyan elbow; pink weld; yellow arrow pipe; orange support-support; brown support-weld; deep orange weld-weld; green support-empty; blue weld-empty; teal tee; violet branch; amber flange; gold reducer; grey unresolved.

## IDF 100 matching stage

1. Parse each valid IDF `100` as a straight-pipe edge and retain adjacent typed component records as nodes.
2. Build the DXF graph from the classified output; contract only ordinary CAD decomposition and confirmed arrows.
3. Normalise each graph under allowed isometric rotation/mirror while preserving degree, branch order, component order, turns, bore transitions, and relative length ratios.
4. Match by endpoint/component signature, then branch degree/order, turn sequence, bore transition, and finally relative length. Treat cut-pipe tables as downstream validation, not matching truth.
5. Emit candidate pairs, competing candidates, score margin, and confidence. Keep structural conflicts unresolved.
6. For every non-trivial pair, render: full numbered IDF graph, full numbered DXF overlay, and a paired local topology crop.

## Rule update discipline

For each human verdict, record the ordered prerequisites, measured vector features, topology effect, rejected alternative, validation counts, and recall limit. Change the smallest general rule that explains both positive and negative samples; replay prior labelled examples and then forward-test unseen pages. Store source-specific handles only as regression evidence, never as a production rule.
