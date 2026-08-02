# Single-page matching contract

## Minimum records

### IDF 100 / 120

Store the stable review ID, record type, original line span, raw fields, coordinates if present, bore, cut length if present, and predecessor/successor typed records. Assign IDs in source order before scoring.

### DXF typed graph

Store source filename, all source handles, vector anchor/endpoint coordinates, semantic class, endpoint roles, incident component classes, bore if available, and evidence image path. Use source+handle as identity; a handle alone is not globally unique.

## Score order

Use lexicographic topology evidence before scalar length:

1. Exact endpoint/component signature.
2. Degree and branch-order preservation.
3. Turn and elbow sequence.
4. Bore transition.
5. Relative-length ratio within the same matched neighbourhood.

Do not use title-block position, annotation text position, materials table, cut-pipe table, or IDF-to-DXF absolute-coordinate projection as a score feature.

## CHAIN_100_V1 audit fields

For the first, branch-free algorithm, retain `idf_chain_order`, `dxf_chain_order`, `forward_score`, `reverse_score`, `orientation_margin`, and per-pair score components `{connector, turn_context, bore, role, relative_length}`. A hand-authored mapping file may be used only as a review annotation; label it `human_seed` and never report it as algorithm output.

## Review images

The full IDF image must show every `I###` and `W###`. The full DXF overlay must show its matched IDs at vector anchors, without covering the original geometry. A local pair must show the same candidate relation on both sides and state the confidence plus the decisive topology evidence.

## Extension boundary

Multiple DXF sheets, continuation marks, same-line ISO splits, and a single IDF whose graph spans more than one page are out of scope for this version. First partition and prove page membership; then apply this single-page contract independently to each page.

## Initial algorithm validation

`CHAIN_100_V1` is intentionally conservative. The first strict single-page test (`DR200008`) had 3 IDF `100` and 3 DXF final-pipe runs and produced a unique forward orientation. Two subsequent strict single-page tests (`VT200001`, `VT200002`) had fewer IDF `100` than DXF final-pipe runs because DXF support cuts produced additional `SUPPORT_*` runs. They are recorded as `not_chain_eligible`, not failed or force-matched. This establishes the next question: whether a support-bounded DXF group is one IDF `100` or multiple; do not decide from count alone.
